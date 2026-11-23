"""Build and inspect both local V2 workspaces from pinned prepared sources."""

from semantic_pipeline_v2.model_cache import load_yaml
import argparse
from contextlib import contextmanager
import csv
import fcntl
import json
import os
from pathlib import Path

import yaml

from .core import State, digest, normalize, v1_repository
from .passages import article_body, from_observations
from ._pilot_postgres import PilotDatabaseError, PilotPostgres


ROOT = Path(__file__).resolve().parents[1]
FINANCE = "demo_data/finance"
FINANCE_PROJECTIONS = ROOT / FINANCE / "local/finance_workbook"
FACTORY = "demo_data/manufacturing"
EXTRA_SOURCES = "demo_data/extra_sources.json"
EXPORT = "docs/design/v2-workspace/workspace-data.js"
STAGES = [
    {"id": "ingest", "title": "Ingest", "views": ["source"]},
    {"id": "knowledge", "title": "Knowledge", "views": ["ontology"]},
    {"id": "review", "title": "Review & publish", "views": []},
    {"id": "entity", "title": "Entity 360", "views": []},
]
JADE = "demo_data/preload/adverse1536_NEWS-CORP0006700_jade-logistics-pte-ltd.md"
FINANCE_HIGHLIGHTS = ["Customer snapshots", "Account snapshots", "Recorded risk", "Native-currency transactions"]
FACTORY_HIGHLIGHTS = ["Sales order quantity", "Product catalogue", "Supplier lead time", "Work order scrap"]
REVIEW_DECISIONS = ("keep_separate", "request_evidence")
MAX_REVIEW_NOTE = 480
CLI_REVIEW_NOTE = "Local rehearsal decision recorded through the CLI."


def scoped(dataset, kind, key):
    return f"{dataset}:{kind}:{key}"


def source(dataset, key, path, title, kind, description, *, expected=None, **metadata):
    payload = (ROOT / path).read_bytes()
    revision = digest(payload)
    if expected is not None and revision != expected:
        raise ValueError(f"Source checksum mismatch: {path}")
    return {
        "id": scoped(dataset, "source", key),
        "title": title, "kind": kind, "description": description,
        "citation": {"path": path, "sha256": revision},
        **metadata,
    }


def extra_sources(dataset):
    """Registered wider-corpus documents (`demo_data/extra_sources.json`), pinned by SHA-256.

    Tracked files are re-hashed on every load. A `local_only` file (git-ignored local data) is hashed only when
    present, so the export stays the same without it; readers skip it when absent. Planted-truth manifests are not
    read here: the registry holds only identity, label and scope.
    """
    out = []
    for item in json.loads((ROOT / EXTRA_SOURCES).read_text())[dataset]:
        path = ROOT / item["path"]
        if path.is_file() and digest(path.read_bytes()) != item["sha256"]:
            raise ValueError(f"Source checksum mismatch: {item['path']}")
        if not path.is_file() and not item.get("local_only"):
            raise ValueError(f"Source missing: {item['path']}")
        extra = {k: item[k] for k in ("label", "scope", "attribution") if k in item}
        out.append({"id": scoped(dataset, "source", item["key"]), "title": item["title"], "kind": item["kind"],
                    "description": item["description"], **extra,
                    "citation": {"path": item["path"], "sha256": item["sha256"]}})
    return out


def observe(dataset, key, record, title, prefix, description, *, line_count=1):
    citation = record["citation"]
    text = (ROOT / citation["path"]).read_bytes()
    if digest(text) != citation["sha256"]:
        raise ValueError(f"Evidence source changed: {citation['path']}")
    lines = text.decode().splitlines()
    starts = [index for index, line in enumerate(lines) if line.startswith(prefix)]
    if len(starts) != 1:
        raise ValueError(f"Expected one evidence location for {key}")
    start = starts[0]
    end = start + line_count
    if end > len(lines) or any(not line.strip() or line.startswith("#") for line in lines[start:end]):
        raise ValueError(f"Invalid evidence extent for {key}")
    quote = "\n".join(lines[start:end])
    return {
        "id": scoped(dataset, "observation", key), "title": title,
        "description": description, "sourceId": record["id"], "quote": quote,
        "citation": dict(citation, startLine=start + 1, endLine=end),
    }


def read_model(dataset, path, key, title, highlights, expected=None):
    record = source(dataset, key, path, title, "structured-model",
                    "Prepared Ossie candidate with definitions only.", expected=expected)
    raw = (ROOT / path).read_bytes()
    normalized = normalize(raw, record["citation"]["sha256"])
    model = load_yaml(raw)
    knowledge = {kind: [] for kind in ("datasets", "relationships", "fields", "metrics")}
    groups = {"dataset": "datasets", "relationship": "relationships",
              "field": "fields", "metric": "metrics"}
    metrics_by_table = {}
    for item in normalized["records"]:
        _, kind, local = item["source_id"].split("/", 2)
        metadata = item["source_metadata"]
        binding = {
            "id": scoped(dataset, "definition", item["source_id"]),
            "name": local, "title": metadata.get("label") or metadata["name"],
            "description": item["definition"], "datatype": metadata.get("datatype"),
            "sourceId": record["id"],
        }
        for field in ("from", "to", "from_columns", "to_columns", "primary_key"):
            if field in metadata:
                binding[field] = metadata[field]
        if kind == "dataset":  # an `origin: extracted` table is filled from documents (Output C), not from the workbook
            ext = next((json.loads(e["data"]) for e in metadata.get("custom_extensions") or []
                        if e.get("vendor_name") == "HELIOS"), {})
            if ext.get("origin"):
                binding["origin"] = ext["origin"]
        knowledge[groups[kind]].append(binding)
        if kind == "metric":
            for table in item["input_tables"]:
                metrics_by_table.setdefault(table, []).append({
                    "name": local, "label": local.replace("_", " ").capitalize(),
                    "description": binding["description"],
                })
    for table in knowledge["datasets"]:
        if table["name"] in metrics_by_table:
            table["metrics"] = metrics_by_table[table["name"]]
    knowledge["modelSource"] = record["id"]
    knowledge["namespace"] = model["name"]
    catalog = {
        "namespace": model["name"],
        "tableCount": len(knowledge["datasets"]), "fieldCount": len(knowledge["fields"]),
        "relationshipCount": len(knowledge["relationships"]), "highlights": highlights,
    }
    return record, knowledge, catalog


def entity(dataset, key, title, kind, description, sources, observations):
    return {
        "id": scoped(dataset, "entity", key), "title": title, "kind": kind,
        "description": description, "sourceIds": [item["id"] for item in sources],
        "observationIds": [item["id"] for item in observations],
    }


def workbook_table_count(path, columns, source_revision):
    """Inspect a prepared CSV without disclosing its rows. No revision means no SourceBatch column to check."""
    if path.is_symlink() or not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8", newline="") as stream:
            rows = csv.reader(stream, strict=True)
            if next(rows, None) != columns:
                return None
            batch = columns.index("SourceBatch") if source_revision else None
            count = 0
            for row in rows:
                if len(row) != len(columns) or (batch is not None and row[batch] != source_revision):
                    return None
                count += 1
            return count
    except (OSError, UnicodeError, csv.Error):
        return None


def workbook_import(structured, source_revision, *, local, directory=None):
    """List only the table projections declared for the pinned demo workbook (or a CSV folder)."""
    directory = FINANCE_PROJECTIONS if directory is None else directory
    fields = {}
    for field in structured["fields"]:
        table, column = field["name"].split("/", 1)
        fields.setdefault(table, []).append(column)
    lookup = local and not directory.is_symlink() and not directory.parent.is_symlink()
    tables, from_documents = [], []
    for definition in structured["datasets"]:
        if definition.get("origin") == "extracted":   # filled from documents by Output C, not by the workbook
            from_documents.append({"id": definition["id"], "name": definition["name"]})
            continue
        name = definition["name"]
        filename = name + ".csv"
        count = (workbook_table_count(directory / filename, fields[name], source_revision)
                 if lookup else None)
        tables.append({
            "id": definition["id"], "name": name, "filename": filename,
            "status": "available" if count is not None else "unavailable", "rowCount": count,
        })
    available = sum(table["status"] == "available" for table in tables)
    return {
        "status": "available" if available == len(tables) else "unavailable",
        "synthetic": True, "tableCount": len(tables), "availableTableCount": available,
        "tables": tables, "fromDocuments": from_documents,
        **({"reason": "local data absent"} if available != len(tables) else {}),
    }


def financial_services(dataset, *, local_projections=True):
    from .media_transcript import archived_transcript

    inventory = json.loads((ROOT / FINANCE / "sources.json").read_text())
    crosswalk_path = f"{FINANCE}/looker_crosswalk.json"
    crosswalk = json.loads((ROOT / crosswalk_path).read_text())
    structured_source, structured, catalog = read_model(
        dataset, f"{FINANCE}/finance.ossie.yaml", "finance-model",
        "Finance workbook model", FINANCE_HIGHLIGHTS,
    )
    if crosswalk["ossie_namespace"] != catalog["namespace"]:
        raise ValueError("Finance crosswalk uses a different model namespace")
    assets = {asset["id"]: asset for asset in inventory["assets"]}
    jade_asset = next(asset for asset in assets.values() if asset["path"] == JADE)
    media = source(dataset, "jade-adverse-media", JADE, "Jade Logistics adverse-media report",
                   "adverse-media", "Synthetic report for enhanced due diligence.",
                   expected=jade_asset["sha256"], scope="Jade Logistics")
    crosswalk_source = source(dataset, "looker-crosswalk", crosswalk_path, "Looker metadata crosswalk",
                              "mapping-reference", "Prepared candidate mappings and unmapped fields.")
    asset = assets["workbook"]
    workbook = {
        "id": scoped(dataset, "source", "workbook"), "title": "MockUp Data.xlsx",
        "kind": "workbook", "description": "Synthetic source imported as separate structured CSV tables.",
        "citation": {"manifest": f"{FINANCE}/sources.json", "sourceName": asset["path"],
                     "sha256": asset["sha256"]},
        "structuredImport": workbook_import(structured, asset["sha256"], local=local_projections),
    }
    readiness = {
        "xfa_render_required": "Populated XFA form fields need rendering or extraction before review.",
        "text_layer_present": "Text layer present. Content and entity identity still require review.",
        "inventory_only": "Original recording retained. Transcript and entity attribution are pending.",
        "local_source": "Synthetic Jade recording. The archived transcript requires matching original bytes.",
    }
    pending = []
    transcript = []
    for asset in assets.values():
        if asset["root"] == "project" or asset["id"] == "workbook":
            continue
        audio = asset["kind"].endswith("audio")
        record = {
            "id": scoped(dataset, "source", asset["id"]),
            "title": Path(asset["path"]).name,
            "kind": "audio" if audio else "pdf",
            "description": readiness[asset["status"]],
            "status": asset["status"], "scope": asset["scope"],
            "visibility": "presenter_metadata_only" if audio else "inventory_metadata_only",
            **({"pages": asset["pages"]} if "pages" in asset else {}),
        }
        if audio:
            if asset["id"] == "jade_call" and local_projections:
                transcript = archived_transcript(
                    ROOT / FINANCE / "jade_transcript.json", v1_repository(), asset,
                )
            record["transcriptStatus"] = "available" if asset["id"] == "jade_call" and transcript else "unavailable"
            if record["transcriptStatus"] == "available":
                record.update(
                    description="Synthetic Jade recording with an archived timestamped transcript.",
                    transcriptSegmentCount=len(transcript),
                )
        pending.append(record)
    reported = observe(dataset, "reported-payment-concern", media, "Reported payment concern",
                       "Regional Business Wire -- ", "An allegation attributed to the named media source.",
                       line_count=2)
    qualification = observe(dataset, "unverified-report", media, "Source qualification",
                            "Note for screening:", "The report calls itself unverified.", line_count=2)
    observations = [reported, qualification]
    definitions = {item["name"]: item["id"] for item in structured["fields"]}
    mappings = [
        {
            "id": scoped(dataset, "mapping", item["key"]), "title": item["key"].replace("_", " ").title(),
            "description": item["review_note"], "status": item["decision"],
            "sourceId": crosswalk_source["id"], "lookerField": item["looker_field"],
            "targetId": definitions[item["workbook_field"]] if item["workbook_field"] else None,
        }
        for item in crosswalk["mappings"]
    ]
    mappings.append({
        "id": scoped(dataset, "mapping", "reported-concern-vs-recorded-risk"),
        "title": "Reported concern and recorded risk",
        "description": "Keep the source allegation separate from the recorded risk rating. No workbook identity is established.",
        "status": "unmapped", "sourceId": media["id"],
        "observationId": reported["id"], "targetId": None,
    })
    jade = entity(dataset, "jade-logistics", "Jade Logistics", "Customer",
                  "Evidence-only demo entity. The supplied workbook has no reviewed identity link.",
                  [media], observations)
    jade["answer"] = {
        "question": "What does the supplied report establish about Jade Logistics?",
        "text": "The synthetic report alleges opaque cross-border payment flows and describes itself as unverified. The supplied evidence establishes no recorded risk rating.",
        "observationIds": [item["id"] for item in observations],
        "status": "evidence_only",
    }
    return {
        "id": dataset, "title": "Financial Services",
        "description": "Workbook definitions, Looker candidate mappings and scoped synthetic Jade Logistics evidence.",
        "catalog": catalog,
        "sources": [workbook, structured_source, media, crosswalk_source, *pending, *extra_sources(dataset)],
        "knowledge": {"structured": structured, "observations": [*observations, *transcript], "mappings": mappings},
        "entities": [jade],
    }


DOCUMENT_OBSERVATIONS = {
    "MFG-001": [("minimum-purchase", "Minimum purchase commitment", "The customer commits to order",
                 "Contractual quantity, scoped customer, product and period.")],
    "MFG-002": [
        ("order-line", "Scheduled order line", "This schedule references",
         "Source line and quantity named by the schedule."),
        ("original-delivery", "Original delivery promise", "The synthetic promised delivery date",
         "Original delivery date in effect from 2024-05-29."),
    ],
    "MFG-003": [("amended-delivery", "Amended delivery promise", "Effective 2024-05-30,",
                 "Amends only the original delivery clause. This is distinct from the operational due date.")],
    "MFG-004": [("acceptance", "Written acceptance", "The customer will inspect",
                 "Requires documented receipt and written acceptance.")],
    "MFG-005": [("warranty", "Warranty period", "The synthetic warranty lasts",
                 "Starts only after documented acceptance.")],
    "MFG-006": [("supplier-lead-time", "Supplier lead-time promise", "The supplier promises receipt",
                 "The contractual clock starts at purchase-order acceptance.")],
    "MFG-007": [("second-customer-commitment", "Second customer commitment", "Customer Camping and Sports Store,",
                 "The second customer has a separately scoped quantity commitment.")],
}


def manufacturing(dataset, *, local_projections=True):
    pack = ROOT / FACTORY
    checks = json.loads((pack / "semantics/preparation.json").read_text())
    model_source, structured, catalog = read_model(
        dataset, f"{FACTORY}/semantics/manufacturing.ossie.yaml", "manufacturing-model",
        "AdventureWorks manufacturing model", FACTORY_HIGHLIGHTS, expected=checks["model_sha256"],
    )
    # Row counts come from the git-ignored AdventureWorks CSV projections when they are present.
    structured["structuredImport"] = workbook_import(structured, None, local=local_projections,
                                                     directory=pack / "data/csv")
    manifest = json.loads((pack / "documents/manifest.json").read_text())
    sources = [model_source]
    observations = []
    entity_sources = {}
    entity_observations = {}
    for item in manifest["documents"]:
        key = item["id"]
        path = f"{FACTORY}/{item['primary_path']}"
        title = (ROOT / path).read_text().splitlines()[0].removeprefix("# ")
        record = source(dataset, key, path, title, "synthetic-contract",
                        "Synthetic demo contract.", expected=item["sha256"],
                        scope=item["entity_scope"], revision=item["revision"],
                        effectiveFrom=item["effective_from"], supersedes=item["supersedes"])
        group = [observe(dataset, key, record, title, prefix, description)
                 for key, title, prefix, description in DOCUMENT_OBSERVATIONS[key]]
        sources.append(record)
        observations.extend(group)
        entity_sources.setdefault(item["entity_scope"], []).append(record)
        entity_observations.setdefault(item["entity_scope"], []).extend(group)
    by_observation = {item["id"].rsplit(":", 1)[1]: item for item in observations}
    review_specs = [
        ("delivery-vs-due-date", "amended-delivery", "Sales_SalesOrderHeader/DueDate",
         "Delivery promise and operational due date",
         "Contractual delivery differs from the operational due date. Require independent proof of receipt."),
        ("warranty-vs-acceptance", "warranty", None, "Warranty applicability",
         "The operational source has no verified warranty-duration field."),
        ("order-quantity", "order-line", "Sales_SalesOrderDetail/OrderQty", "Scheduled and ordered quantity",
         "Check the source order and line identity before comparing quantities."),
    ]
    definitions = {item["name"]: item["id"] for item in structured["fields"]}
    mappings = [
        {
            "id": scoped(dataset, "mapping", key), "title": title, "description": description,
            "status": "candidate" if field else "unmapped",
            "observationId": by_observation[observation]["id"],
            "sourceId": by_observation[observation]["sourceId"],
            "targetId": definitions[field] if field else None,
        }
        for key, observation, field, title, description in review_specs
    ]
    entities = [
        entity(dataset, key, title, kind, "Entity scope comes from the synthetic contract manifest.",
               entity_sources[scope], entity_observations[scope])
        for scope, key, title, kind in [
            ("customer:29641", "westside-plaza", "Westside Plaza", "Customer"),
            ("vendor:1518", "international-trek-center", "International Trek Center", "Supplier"),
            ("customer:29629", "camping-and-sports-store", "Camping and Sports Store", "Customer"),
        ]
    ]
    entities[0]["answer"] = {
        "question": "What does the amended schedule say about delivery for order line 36824?",
        "text": "The amendment moves order 51131’s promise to June 17, 2024. The original schedule identifies line 36824 and 16 units. Delivery remains unknown without receipt evidence.",
        "observationIds": [by_observation[key]["id"] for key in ("amended-delivery", "order-line")],
        "status": "evidence_only",
    }
    return {
        "id": dataset, "title": "Manufacturing",
        "description": "AdventureWorks definitions and scoped synthetic customer and supplier contracts.",
        "catalog": catalog, "sources": [*sources, *extra_sources(dataset)],
        "knowledge": {"structured": structured, "observations": observations, "mappings": mappings},
        "entities": entities,
    }


DATASETS = {"financial-services": financial_services, "manufacturing": manufacturing}


def load_workspace(dataset, *, local_projections=True):
    workspace = DATASETS[dataset](dataset, local_projections=local_projections)
    observations = workspace["knowledge"]["observations"]
    passages = []
    for record in workspace["sources"]:
        examples = [item for item in observations if item["sourceId"] == record["id"]]
        record["examples"] = examples
        if local_projections and examples:
            if record["kind"] == "adverse-media":
                raw = (ROOT / record["citation"]["path"]).read_bytes()
                if digest(raw) != record["citation"]["sha256"]:
                    raise ValueError("Article source changed")
                passages.append(article_body(record, examples, raw))
            else:
                passages.extend(from_observations(examples))
    if local_projections:
        workspace["knowledge"]["passages"] = passages
    validate_links(workspace)
    return workspace


def validate_links(workspace):
    dataset = workspace["id"]
    sources = {item["id"] for item in workspace["sources"]}
    knowledge = workspace["knowledge"]
    observations = {item["id"]: item["sourceId"] for item in knowledge["observations"]}
    targets = {item["id"] for item in knowledge["structured"]["fields"]}
    groups = [workspace["sources"], knowledge["observations"], knowledge["mappings"],
              workspace["entities"]]
    groups.extend(knowledge["structured"][name]
                  for name in ("fields", "datasets", "metrics", "relationships"))
    for collection in groups:
        keys = {item["id"] for item in collection}
        if len(collection) != len(keys) or any(not key.startswith(dataset + ":") for key in keys):
            raise ValueError("Duplicate or cross-dataset identifiers are prohibited")
    if not set(observations.values()) <= sources:
        raise ValueError("Observation references an unavailable source")
    for mapping in knowledge["mappings"]:
        if (mapping["sourceId"] not in sources
                or mapping.get("observationId") not in (None, *observations)
                or mapping.get("targetId") not in (None, *targets)):
            raise ValueError("Unresolved mapping reference")
    for item in workspace["entities"]:
        if not set(item["sourceIds"]) <= sources or any(
            observations.get(key) not in item["sourceIds"] for key in item["observationIds"]
        ):
            raise ValueError("Entity references evidence outside its source scope")
        if item.get("answer") and not set(item["answer"]["observationIds"]) <= set(item["observationIds"]):
            raise ValueError("Answer references evidence outside its entity scope")


def stage_view(workspace, stage, selection=None):
    if selection is not None and stage not in ("source", "review", "entity"):
        raise ValueError("Selection requires source, review or entity stage")
    if stage == "source":
        entries = workspace["sources"]
    elif stage == "ontology":
        return workspace["knowledge"]["structured"]
    elif stage == "ingest":
        return {"sources": workspace["sources"], "catalog": workspace["catalog"]}
    elif stage == "knowledge":
        return workspace["knowledge"]
    elif stage == "review":
        entries = workspace["knowledge"]["mappings"]
    elif stage == "entity":
        entries = workspace["entities"]
    else:
        raise ValueError("Unknown workspace stage")
    if selection is None:
        return entries
    for entry in entries:
        if entry["id"] == selection:
            return entry
    raise ValueError("Selection is unavailable in this dataset and view")


def export_bytes():
    exported = {"version": 1, "stages": STAGES,
                "datasets": [load_workspace(key, local_projections=False) for key in DATASETS]}
    return ("// Generated by python -m semantic_pipeline_v2.workspace --write-export\n"
            "window.V2_WORKSPACE_DATA = " + json.dumps(exported, indent=2, ensure_ascii=True)
            + ";\n").encode()


def review_revision(workspace, selection):
    mapping = stage_view(workspace, "review", selection)
    source_revisions = {
        item["id"]: item["citation"]["sha256"] for item in workspace["sources"] if "citation" in item
    }
    return digest(json.dumps(
        {"sources": source_revisions, "mapping": mapping}, sort_keys=True
    ).encode())


@contextmanager
def review_database(state_directory, pg_bin):
    state_path = Path(state_directory)
    if state_path.is_symlink():
        raise ValueError("Dedicated review state cannot be a symlink")
    if state_path.is_dir() and not (state_path / "instance.json").exists() and any(state_path.iterdir()):
        raise ValueError("Refusing an unmarked, nonempty review state directory")
    state = State.create(state_path, "v2-workspace-review", [ROOT, ROOT.parent / "semantic-hub"])
    with (state.root / "review.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("The dedicated review state is already in use") from error
        with PilotPostgres.start(pg_bin, data_directory=state.root / "pgdata") as database:
            database.sql("""
                CREATE TABLE IF NOT EXISTS review_receipts (
                    mapping_id text NOT NULL, evidence_revision text NOT NULL, receipt jsonb NOT NULL,
                    PRIMARY KEY (mapping_id, evidence_revision)
                );
            """)
            yield database


def get_review(workspace, selection, database):
    revision = review_revision(workspace, selection)
    output = database.sql("""
        BEGIN READ ONLY;
        SELECT receipt FROM review_receipts
        WHERE mapping_id = :'mapping_id' AND evidence_revision = :'revision';
        COMMIT;
    """, mapping_id=selection, revision=revision)
    return json.loads(output) if output else None


def list_reviews(workspace, database):
    """Saved decisions whose evidence revision still matches the prepared mappings."""
    output = database.sql("""
        BEGIN READ ONLY;
        SELECT COALESCE(jsonb_agg(receipt ORDER BY mapping_id), '[]'::jsonb) FROM review_receipts
        WHERE receipt->>'dataset' = :'dataset';
        COMMIT;
    """, dataset=workspace["id"])
    current = {item["id"] for item in workspace["knowledge"]["mappings"]}
    return [item for item in json.loads(output) if item["mappingId"] in current
            and item["evidenceRevision"] == review_revision(workspace, item["mappingId"])]


class ReviewConflict(ValueError):
    """A different outcome already exists for the prepared evidence revision."""


def record_review(workspace, selection, decision, database, *, note, evidence_revision):
    """Persist an explicit local rehearsal outcome bound to the prepared evidence."""
    if not selection or decision not in REVIEW_DECISIONS:
        raise ValueError("A claim-conservative local review decision is required")
    if not note.strip() or len(note) > MAX_REVIEW_NOTE or "\x00" in note:
        raise ValueError(f"Review note must contain 1 to {MAX_REVIEW_NOTE} characters")
    fixture_revision = review_revision(workspace, selection)
    if evidence_revision != fixture_revision:
        raise ReviewConflict("Evidence changed. Reload the current review before recording a decision.")
    receipt = {
        "dataset": workspace["id"], "mappingId": selection, "decision": decision,
        "note": note,
        "evidenceRevision": fixture_revision, "mode": "local_rehearsal",
        "publication": "none",
    }
    persisted = database.sql("""
        BEGIN;
        INSERT INTO review_receipts VALUES (:'mapping_id', :'revision', :'receipt'::jsonb)
        ON CONFLICT DO NOTHING;
        SELECT receipt FROM review_receipts
        WHERE mapping_id = :'mapping_id' AND evidence_revision = :'revision';
        COMMIT;
    """, mapping_id=selection, revision=fixture_revision, receipt=json.dumps(receipt))
    if json.loads(persisted) != receipt:
        raise ReviewConflict("A different local decision already exists for this evidence revision")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=[*DATASETS, "banking"])
    parser.add_argument("--stage", choices=[item["id"] for item in STAGES] + ["source", "ontology"])
    parser.add_argument("--select", help="full scoped source, entity or mapping ID")
    parser.add_argument("--state-dir", type=Path, help="absolute V2 state root outside the repository")
    parser.add_argument("--pg-bin", type=Path, default=os.environ.get("V2_PG_BIN"))
    parser.add_argument("--record-decision", choices=REVIEW_DECISIONS, help="persist a local rehearsal decision")
    parser.add_argument("--note", help=f"review rationale, defaults to: {CLI_REVIEW_NOTE}")
    parser.add_argument("--evidence-revision", help="optional expected revision of selected review evidence")
    export_options = parser.add_mutually_exclusive_group()
    export_options.add_argument("--write-export", action="store_true")
    export_options.add_argument("--check-export", action="store_true")
    args = parser.parse_args()
    try:
        if args.write_export or args.check_export:
            if args.dataset or args.stage or args.select or args.state_dir or args.record_decision:
                parser.error("Export always includes both datasets")
            payload = export_bytes()
            path = ROOT / EXPORT
            if args.write_export:
                path.write_bytes(payload)
            elif path.read_bytes() != payload:
                raise ValueError("Workspace export is out of date")
            print(EXPORT)
            return
        if (args.stage or args.select) and not args.dataset:
            parser.error("Stage or selection requires a dataset")
        if args.select and args.stage not in ("source", "review", "entity"):
            parser.error("Selection requires source, review or entity stage")
        if any((args.record_decision, args.state_dir, args.note, args.evidence_revision)) and not (
            args.record_decision and args.state_dir and args.stage == "review" and args.select
        ):
            parser.error("Recording a decision requires review stage, mapping selection and state directory")
        if args.dataset:
            dataset = "financial-services" if args.dataset == "banking" else args.dataset
            workspace = load_workspace(dataset)
            if args.record_decision:
                expected = args.evidence_revision or review_revision(workspace, args.select)
                with review_database(args.state_dir, args.pg_bin) as database:
                    result = record_review(workspace, args.select, args.record_decision, database,
                                           note=args.note if args.note is not None else CLI_REVIEW_NOTE,
                                           evidence_revision=expected)
            else:
                result = stage_view(workspace, args.stage, args.select) if args.stage else workspace
        else:
            result = [{"id": model["id"], "title": model["title"], **model["catalog"]}
                      for model in map(load_workspace, DATASETS)]
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except (OSError, ValueError, PilotDatabaseError) as error:
        parser.exit(1, f"Workspace unavailable: {error}\n")


if __name__ == "__main__":
    main()
