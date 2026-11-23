"""PostgreSQL schema for V2 ontology, graph and corrections.

Tables are append-only with revisions.  The correction key binds to the
definition ID plus that definition's own content hash, so a change to an
unrelated definition never orphans a correction.
"""

from semantic_pipeline_v2.model_cache import load_yaml
import json
from hashlib import sha256
from pathlib import Path

from .core import normalize


# ---------------------------------------------------------------------------
# SQL helpers for safe literal embedding in multi-row INSERT statements.
# PilotPostgres passes params via psql --set, which does not support newlines
# in multi-row VALUES.  For single-row ops the :'param' quoting style is used
# (matching mapping_corrections.py).  For bulk inserts we build SQL literals
# directly.
# ---------------------------------------------------------------------------

def _lit(s: str) -> str:
    """PostgreSQL single-quoted text literal (doubles single quotes)."""
    return "'" + s.replace("'", "''") + "'"


def _opt(value) -> str:
    """PostgreSQL text literal, or SQL NULL when value is None."""
    return "NULL" if value is None else _lit(str(value))


def _jsonb(obj) -> str:
    """PostgreSQL jsonb literal, or SQL NULL when obj is None."""
    if obj is None:
        return "NULL"
    return _lit(json.dumps(obj, sort_keys=True, ensure_ascii=True)) + "::jsonb"


def _helios(metadata: dict) -> dict:
    for item in metadata.get("custom_extensions") or []:
        if item.get("vendor_name") == "HELIOS":
            raw = item.get("data")
            return json.loads(raw) if isinstance(raw, str) else dict(raw or {})
    return {}


def _definition_hash(record: dict) -> str:
    """Hash only this definition's own content, independent of YAML revision.

    Two definitions from different releases that share source_id, text and
    expression will have the same hash, so a correction on one remains valid
    when the sibling definitions in the same YAML change.
    """
    content = {
        "source_id": record["source_id"],
        "definition": record["definition"],
        "expression": record["expression"],
        "source_metadata": record["source_metadata"],
    }
    return sha256(json.dumps(content, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_DDL = """
    CREATE TABLE IF NOT EXISTS v2_ontology_release (
        release_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        dataset       text   NOT NULL,
        source_sha256 text   NOT NULL,
        source_hex    text   NOT NULL,
        created_at    timestamptz NOT NULL DEFAULT now(),
        UNIQUE (dataset, source_sha256)
    );
    CREATE TABLE IF NOT EXISTS v2_concept (
        concept_id  text NOT NULL,
        dataset     text NOT NULL,
        release     text NOT NULL,
        label       text NOT NULL,
        definition  text,
        broader     text,
        synonyms    jsonb,
        status      text NOT NULL DEFAULT 'candidate',
        PRIMARY KEY (concept_id, dataset, release)
    );
    ALTER TABLE v2_concept ADD COLUMN IF NOT EXISTS synonyms jsonb;
    CREATE TABLE IF NOT EXISTS v2_definition (
        id               text NOT NULL,
        dataset          text NOT NULL,
        release          text NOT NULL,
        kind             text NOT NULL,
        source_id        text NOT NULL,
        label            text,
        definition       text NOT NULL,
        expression       jsonb,
        grain            text,
        unit             text,
        time_field       text,
        required_filters jsonb,
        concept_id       text,
        source_metadata  jsonb,
        definition_hash  text NOT NULL,
        PRIMARY KEY (id, release)
    );
    CREATE INDEX IF NOT EXISTS v2_definition_dataset_release
        ON v2_definition (dataset, release);
    CREATE TABLE IF NOT EXISTS v2_graph_node (
        node_id  text NOT NULL,
        kind     text NOT NULL,
        dataset  text NOT NULL,
        payload  jsonb,
        release  text NOT NULL,
        PRIMARY KEY (node_id, release)
    );
    CREATE TABLE IF NOT EXISTS v2_graph_edge (
        edge_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        request_id text   UNIQUE,
        src        text   NOT NULL,
        dst        text,
        relation   text   NOT NULL,
        evidence   jsonb,
        confidence real,
        method     text   NOT NULL,
        status     text   NOT NULL DEFAULT 'proposed',
        created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS v2_graph_edge_src
        ON v2_graph_edge (src, relation, edge_id DESC);
    CREATE TABLE IF NOT EXISTS v2_catalog_concept (
        concept_id      text NOT NULL,
        dataset         text NOT NULL,
        catalog_version text NOT NULL,
        label           text NOT NULL,
        origin          text NOT NULL,
        status          text NOT NULL DEFAULT 'proposed',
        record          jsonb NOT NULL,
        created_at      timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (concept_id, dataset, catalog_version)
    );
    CREATE TABLE IF NOT EXISTS v2_entity_link (
        link_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        dataset    text   NOT NULL,
        entity_id  text   NOT NULL,
        passage_id text   NOT NULL,
        method     text   NOT NULL,
        confidence real,
        status     text   NOT NULL DEFAULT 'proposed',
        created_at timestamptz NOT NULL DEFAULT now()
    );
"""


# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------

class StaleCorrection(ValueError):
    """Correction failed stale-write check; reload before editing."""


def _project_lint():
    """`demo_data/enrich_ossie.py::lint` (a script, not a package), loaded by path."""
    import importlib.util
    path = Path(__file__).resolve().parents[1] / "demo_data" / "enrich_ossie.py"
    spec = importlib.util.spec_from_file_location("_enrich_ossie_lint", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.lint


# ---------------------------------------------------------------------------
# SemanticStore
# ---------------------------------------------------------------------------

class SemanticStore:
    """Ontology and graph storage backed by the V2 pilot PostgreSQL."""

    def __init__(self, database):
        self.db = database
        database.sql(_DDL)
        self._migrate_corrections()

    # ------------------------------------------------------------------
    # One-time migration from v2_mapping_corrections to v2_graph_edge
    # ------------------------------------------------------------------

    def _migrate_corrections(self):
        has_table = self.db.sql("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_name = 'v2_mapping_corrections'
            );
        """)
        if has_table != "t":
            return
        count = self.db.sql("SELECT count(*) FROM v2_mapping_corrections;")
        if count == "0":
            return
        already_migrated = self.db.sql("""
            SELECT EXISTS (
                SELECT 1 FROM v2_graph_edge
                WHERE relation = 'migrated_correction' LIMIT 1
            );
        """)
        if already_migrated == "t":
            return
        self.db.sql("""
            INSERT INTO v2_graph_edge
                (request_id, src, dst, relation, evidence, confidence, method, status)
            SELECT
                request_id,
                dataset || ':correction:' || anchor,
                receipt->>'targetId',
                'migrated_correction',
                receipt,
                1.0,
                'human',
                'accepted'
            FROM v2_mapping_corrections
            ON CONFLICT (request_id) DO NOTHING;
        """)

    # ------------------------------------------------------------------
    # Import / export
    # ------------------------------------------------------------------

    def import_ossie(self, dataset: str, path, *, allow_unlinted: bool = False) -> dict:
        """Import an Ossie YAML file in one transaction.  Idempotent: keyed on the YAML sha256.

        Re-importing fills anything a release row lacks (repairs a half import). A model that fails the
        project lint is refused unless ``allow_unlinted`` is set (minimal fixture models only).
        """
        raw = Path(path).read_bytes()
        source_sha = sha256(raw).hexdigest()

        import yaml as _yaml
        doc = load_yaml(raw)
        if not allow_unlinted:
            errors = _project_lint()(doc)
            if errors:
                raise ValueError(f"model fails the project lint ({len(errors)} errors): " + "; ".join(errors[:5]))

        normalized = normalize(raw, source_sha)
        source_hex = raw.hex()

        # Bulk-insert definitions for this release.
        rows = []
        for record in normalized["records"]:
            parts = record["source_id"].split("/", 2)
            if len(parts) < 3:
                continue
            _, kind, _ = parts
            if kind not in ("field", "metric", "dataset", "relationship"):
                continue
            def_id = f"{dataset}:definition:{record['source_id']}"
            def_hash = _definition_hash(record)
            metadata = record.get("source_metadata") or {}
            label = metadata.get("label") or metadata.get("name") or ""
            helios = _helios(metadata) if kind in ("field", "metric") else {}
            rows.append(
                "({id}, {dataset}, {release}, {kind}, {source_id}, "
                "{label}, {definition}, {expression}, {metadata}, {def_hash}, "
                "{grain}, {unit}, {time_field}, {required_filters}, {concept_id})".format(
                    id=_lit(def_id),
                    dataset=_lit(dataset),
                    release=_lit(source_sha),
                    kind=_lit(kind),
                    source_id=_lit(record["source_id"]),
                    label=_lit(label),
                    definition=_lit(record["definition"]),
                    expression=_jsonb(record["expression"]),
                    metadata=_jsonb(metadata),
                    def_hash=_lit(def_hash),
                    grain=_opt(helios.get("grain")),
                    unit=_opt(helios.get("unit")),
                    time_field=_opt(helios.get("time_field")),
                    required_filters=_jsonb(helios.get("required_filters")),
                    concept_id=_opt(helios.get("concept")),
                )
            )

        # Concepts from the HELIOS top-level custom_extensions.
        concept_rows = []
        for ext in doc.get("custom_extensions", []):
            if ext.get("vendor_name") == "HELIOS":
                try:
                    d = json.loads(ext["data"])
                except (json.JSONDecodeError, TypeError, KeyError):
                    break
                for c in d.get("concepts", []):
                    if not isinstance(c, dict) or "id" not in c or "label" not in c:
                        continue
                    broader_val = _lit(c["broader"]) if c.get("broader") else "NULL"
                    definition_val = _lit(c["definition"]) if c.get("definition") else "''"
                    concept_rows.append(
                        f"({_lit(c['id'])}, {_lit(dataset)}, {_lit(source_sha)},"
                        f" {_lit(c['label'])}, {definition_val}, {broader_val},"
                        f" {_jsonb(list(c.get('synonyms') or []))})"
                    )
                break

        statements = [
            "BEGIN;",
            "LOCK TABLE v2_ontology_release IN SHARE ROW EXCLUSIVE MODE;",
            "INSERT INTO v2_ontology_release (dataset, source_sha256, source_hex) "
            "VALUES (:'dataset', :'sha', :'hex') ON CONFLICT (dataset, source_sha256) DO NOTHING;",
        ]
        if rows:
            statements.append(
                "INSERT INTO v2_definition "
                "(id, dataset, release, kind, source_id, label, definition, "
                "expression, source_metadata, definition_hash, grain, unit, time_field, "
                "required_filters, concept_id) VALUES\n"
                + ",\n".join(rows) + "\nON CONFLICT DO NOTHING;"
            )
        if concept_rows:
            statements.append(
                "INSERT INTO v2_concept (concept_id, dataset, release, label, definition, broader, synonyms) VALUES\n"
                + ",\n".join(concept_rows) + "\nON CONFLICT DO NOTHING;"
            )
        statements.append("COMMIT;")
        # One psql run with ON_ERROR_STOP: an error before COMMIT rolls the whole import back.
        self.db.sql("\n".join(statements), dataset=dataset, sha=source_sha, hex=source_hex)

        result = self.db.sql("""
            SELECT row_to_json(r)
            FROM v2_ontology_release r
            WHERE dataset = :'dataset' AND source_sha256 = :'sha';
        """, dataset=dataset, sha=source_sha)
        return json.loads(result)

    def write_catalog(self, dataset: str, version: str, concepts: list) -> int:
        """Add the Output B concepts of one catalog version. Add only: an existing row is never changed."""
        if not concepts:
            return 0
        rows = ",\n".join(
            f"({_lit(c['id'])}, {_lit(dataset)}, {_lit(version)}, {_lit(c['label'])}, {_lit(c['origin'])}, "
            f"{_lit(c.get('status', 'proposed'))}, {_jsonb(c)})" for c in concepts)
        self.db.sql(
            "BEGIN;\nINSERT INTO v2_catalog_concept (concept_id, dataset, catalog_version, label, origin, status, "
            "record) VALUES\n" + rows + "\nON CONFLICT DO NOTHING;\nCOMMIT;")
        return len(concepts)

    def latest_catalog_version(self, dataset: str):
        out = self.db.sql("SELECT catalog_version FROM v2_catalog_concept WHERE dataset = :'dataset' "
                          "ORDER BY created_at DESC, catalog_version DESC LIMIT 1;", dataset=dataset)
        return out or None

    def read_catalog(self, dataset: str, version: str) -> list:
        result = self.db.sql(
            "SELECT COALESCE(jsonb_agg(record ORDER BY concept_id), '[]'::jsonb) FROM v2_catalog_concept "
            "WHERE dataset = :'dataset' AND catalog_version = :'version';", dataset=dataset, version=version)
        return json.loads(result)

    def export_ossie(self, dataset: str) -> bytes:
        """Return the raw YAML bytes for the latest release of dataset."""
        result = self.db.sql("""
            SELECT source_hex FROM v2_ontology_release
            WHERE dataset = :'dataset'
            ORDER BY release_id DESC LIMIT 1;
        """, dataset=dataset)
        if not result:
            raise ValueError(f"No release found for dataset {dataset!r}")
        return bytes.fromhex(result)

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def list_definitions(self, dataset: str) -> list:
        """List all definitions for the latest release of dataset."""
        result = self.db.sql("""
            SELECT COALESCE(jsonb_agg(row_to_json(d)), '[]'::jsonb)
            FROM v2_definition d
            WHERE dataset = :'dataset'
              AND release = (
                  SELECT source_sha256 FROM v2_ontology_release
                  WHERE dataset = :'dataset'
                  ORDER BY release_id DESC LIMIT 1
              );
        """, dataset=dataset)
        return json.loads(result)

    # ------------------------------------------------------------------
    # Human-method graph edge corrections
    # ------------------------------------------------------------------

    def correction_anchor(self, dataset: str, definition_id: str, definition_hash: str) -> str:
        """Stable anchor keyed on definition content, not YAML revision.

        Changing an unrelated definition in the same YAML leaves this anchor
        unchanged, so the correction remains valid.
        """
        return sha256(
            json.dumps([dataset, definition_id, definition_hash], sort_keys=True).encode()
        ).hexdigest()

    def record_correction(
        self,
        dataset: str,
        definition_id: str,
        definition_hash: str,
        action: str,
        target_id,
        previous_revision: str,
        actor: str,
        request_id: str,
    ) -> str:
        """Record a human correction as a human-method v2_graph_edge row.

        Uses the same stale-write check as mapping_corrections.py:97-148:
        the INSERT is conditional on previousRevision matching the latest
        correctionRevision (or the anchor itself if no correction exists yet).

        Raises StaleCorrection if the check fails.
        Returns the new correctionRevision.
        """
        anchor = self.correction_anchor(dataset, definition_id, definition_hash)
        new_revision = sha256(
            json.dumps([anchor, request_id], sort_keys=True).encode()
        ).hexdigest()
        evidence = {
            "dataset": dataset,
            "definitionId": definition_id,
            "action": action,
            "targetId": target_id,
            "actor": actor,
            "previousRevision": previous_revision,
            "correctionRevision": new_revision,
        }
        # dst: use empty string as the "no target" sentinel; NULLIF converts it.
        dst_param = target_id if target_id else ""
        persisted = self.db.sql("""
            BEGIN;
            LOCK TABLE v2_graph_edge IN SHARE ROW EXCLUSIVE MODE;
            INSERT INTO v2_graph_edge
                (request_id, src, dst, relation, evidence, confidence, method, status)
            SELECT
                :'request_id',
                :'anchor',
                NULLIF(:'dst', ''),
                'manual_correction',
                :'evidence'::jsonb,
                1.0,
                'human',
                'accepted'
            WHERE :'previous' = COALESCE((
                SELECT evidence->>'correctionRevision'
                FROM v2_graph_edge
                WHERE src = :'anchor' AND relation = 'manual_correction'
                ORDER BY edge_id DESC LIMIT 1
            ), :'anchor')
            ON CONFLICT (request_id) DO NOTHING;
            SELECT evidence
            FROM v2_graph_edge
            WHERE request_id = :'request_id';
            COMMIT;
        """,
            request_id=request_id,
            anchor=anchor,
            dst=dst_param,
            evidence=json.dumps(evidence, sort_keys=True),
            previous=previous_revision,
        )
        if not persisted or json.loads(persisted) != evidence:
            raise StaleCorrection("Definition changed. Reload before editing.")
        return new_revision
