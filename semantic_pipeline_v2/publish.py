"""Real publish: the approved concept catalog and the mapping corrections as a versioned Ossie export.

A release is the dataset's base model plus, in the HELIOS concept extension, the catalog concepts (no `candidate`, nothing a
correction rejected) with definition links and evidence references, the saved mapping corrections, and a `release` block.
Releases are rows of `v2_release` in the private V2 PostgreSQL. Nothing else is written."""

import json
from hashlib import sha256

import yaml

from . import concept_catalog as cc
from .extracted_fields import mask_extracted
from .semantic_store import _jsonb, _lit

_DDL = """CREATE TABLE IF NOT EXISTS v2_release (
    dataset text NOT NULL, version int NOT NULL, content_sha256 text NOT NULL, model_sha256 text NOT NULL,
    catalog_version text, counts jsonb NOT NULL, source_ids jsonb NOT NULL, principal_id text NOT NULL,
    published_at timestamptz NOT NULL DEFAULT now(), document text NOT NULL, PRIMARY KEY (dataset, version));"""
DEFAULTS = {"examples": [], "unit": None, "grain": None, "type": None, "broad": False, "level": None, "parent": None,
            "time": None, "actor": None, "status": "proposed"}


class EmptyRelease(ValueError):
    """No catalog concept and no mapping correction to publish."""


def _data(text):
    model = yaml.load(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
    return model, json.loads(cc._concept_extension(model)["data"])


def link_corrections(corrections, chunk_source, visible):
    """Evidence-link corrections (`v2_catalog_correction` rows of kind `evidence_link`) of readable sources, one row per
    link: the patches merged in revision order, the last actor and revision. `corrections` is `CatalogCorrections.latest`."""
    rows = []
    for (kind, key), items in sorted(corrections.items()):
        chunk, definition = json.loads(key) if kind == "evidence_link" else (None, None)
        if chunk in chunk_source and visible(chunk_source[chunk][0]):
            rows.append({"chunkId": chunk, "sourceId": chunk_source[chunk][0], "definitionId": definition or None,
                         "patch": {k: v for r in items for k, v in r["patch"].items()},
                         "actor": items[-1]["actor"], "revision": items[-1]["revision"]})
    return rows


def build_release(dataset, base_text, catalog_version, concepts, chunk_source, corrections, link_rows=()):
    """`concepts` are scoped and corrected catalog records; `chunk_source` {chunk id: (source id, revision)};
    `corrections` {anchor: mapping correction receipt} of readable sources, `link_rows` from `link_corrections`. Returns the release; `errors` blocks confirm."""
    shown = []
    for c in concepts:
        if c["status"] in ("candidate", "rejected") or c.get("origin") == "structured":
            continue
        refs = [{"chunkId": x, "sourceId": chunk_source[x][0], "revision": chunk_source[x][1]}
                for x in c["evidence"]["chunks"] if x in chunk_source]
        shown.append({**DEFAULTS, **{k: v for k, v in c.items() if k != "evidenceDetail"},
                      "evidence": {**c["evidence"], "refs": refs},
                      "links": [k for k in c["links"] if k.get("status") != "rejected"]})
    rows = [{"anchor": a, "sourceId": r["sourceId"], "observationId": r["observationId"], "action": r["action"],
             "targetId": r.get("targetId"), "actor": r["actor"], "revision": r["correctionRevision"],
             **({"note": r["note"]} if r.get("note") else {})}
            for a, r in sorted(corrections.items()) if r["action"] != "restore"]
    if not shown and not rows and not link_rows:
        raise EmptyRelease("Nothing to publish: no catalog concepts and no corrections")
    shown, rows, link_rows = mask_extracted(shown), mask_extracted(rows), mask_extracted(list(link_rows))
    sources = sorted({r["sourceId"] for c in shown for r in c["evidence"]["refs"]}
                     | {r["sourceId"] for r in rows} | {r["sourceId"] for r in link_rows})
    model_sha = sha256(base_text.encode()).hexdigest()
    counts = {"concepts": 0, "links": 0, "evidenceRefs": 0, "mappingCorrections": len(rows),
              "evidenceLinkCorrections": len(link_rows)}
    errors, text = [], None
    try:
        exported = cc.export_ossie(base_text, cc.exportable({"concepts": shown}))
        model, data = _data(exported)
        stamps = {c["id"]: c["correction"] for c in shown if c.get("correction")}
        for c in data["concepts"]:
            if c["id"] in stamps:
                c["correction"] = {"actor": stamps[c["id"]]["actor"], "revision": stamps[c["id"]]["revision"]}
        added = [c for c in data["concepts"] if c.get("origin") in ("document", "both") and c["id"] in {s["id"] for s in shown}]
        counts.update(concepts=len(added), links=sum(len(c["links"]) for c in added),
                      evidenceRefs=sum(len(c["evidence"]["refs"]) for c in added))
        data["mapping_corrections"] = rows
        data["evidence_link_corrections"] = link_rows
        data["release"] = {"format": 1, "dataset": dataset, "modelSha256": model_sha, "catalogVersion": catalog_version,
                           "counts": counts}
        cc._concept_extension(model)["data"] = json.dumps(data, sort_keys=True)
        text = cc._enrich().render(model)
        errors += cc._enrich().lint(model)
        if cc.SCHEMA.exists():
            import jsonschema
            try:
                jsonschema.Draft202012Validator(json.loads(cc.SCHEMA.read_text())).validate(model)
            except jsonschema.ValidationError as error:
                errors.append("schema: " + error.message[:200])
    except (cc.CatalogError, KeyError, ValueError, TypeError) as error:
        errors.append(f"export: {error}")
    return {"text": text, "sha256": sha256(text.encode()).hexdigest() if text else None, "errors": errors,
            "counts": counts, "modelSha256": model_sha, "catalogVersion": catalog_version, "sources": sources}


def diff(previous_text, release_text):
    """What a release adds, removes or changes against the previous one, by concept id and correction anchor."""
    def parts(text):
        if text is None:
            return {}, {}, {}
        _, data = _data(text)
        return {c["id"]: c for c in data["concepts"] if c.get("origin") in ("document", "both")}, \
            {r["anchor"]: r for r in data.get("mapping_corrections", [])}, \
            {f'{r["chunkId"]} | {r["definitionId"] or ""}': r for r in data.get("evidence_link_corrections", [])}

    def compare(old, new):
        return {"added": sorted(set(new) - set(old)), "removed": sorted(set(old) - set(new)),
                "changed": sorted(k for k in set(old) & set(new) if old[k] != new[k])}
    (old_c, old_m, old_l), (new_c, new_m, new_l) = parts(previous_text), parts(release_text)
    return {"concepts": compare(old_c, new_c), "corrections": compare(old_m, new_m), "evidenceLinks": compare(old_l, new_l)}


class Releases:
    def __init__(self, database):
        self.db = database
        database.sql(_DDL)

    def history(self, dataset):
        """Newest first, without the document."""
        out = self.db.sql(
            "SELECT COALESCE(jsonb_agg(jsonb_build_object('version', version, 'publishedAt', published_at, "
            "'principalId', principal_id, 'catalogVersion', catalog_version, 'modelSha256', model_sha256, "
            "'contentSha256', content_sha256, 'counts', counts, 'sourceIds', source_ids) ORDER BY version DESC), '[]'::jsonb) "
            "FROM v2_release WHERE dataset = :'dataset';", dataset=dataset)
        return json.loads(out)

    def document(self, dataset, version):
        out = self.db.sql("SELECT encode(convert_to(document, 'UTF8'), 'hex') FROM v2_release "
                          "WHERE dataset = :'dataset' AND version = :'version'::int;", dataset=dataset, version=str(version))
        return bytes.fromhex(out).decode() if out else None

    def add(self, dataset, release, principal_id):
        """(version, written). An unchanged state is not written twice: the newest release with this content is returned."""
        out = self.db.sql(
            "BEGIN;\nLOCK TABLE v2_release IN SHARE ROW EXCLUSIVE MODE;\n"
            "INSERT INTO v2_release (dataset, version, content_sha256, model_sha256, catalog_version, counts, source_ids, "
            "principal_id, document) SELECT :'dataset', COALESCE(max(version), 0) + 1, :'sha', :'model', "
            f"NULLIF(:'catalog', ''), {_jsonb(release['counts'])}, {_jsonb(release['sources'])}, :'principal', "
            "convert_from(decode(:'hex', 'hex'), 'UTF8') FROM v2_release WHERE dataset = :'dataset' "
            "HAVING COALESCE((SELECT content_sha256 FROM v2_release WHERE dataset = :'dataset' "
            "ORDER BY version DESC LIMIT 1), '') <> :'sha' RETURNING version;\nCOMMIT;",
            dataset=dataset, sha=release["sha256"], model=release["modelSha256"], catalog=release["catalogVersion"] or "",
            principal=principal_id, hex=release["text"].encode().hex())
        if out.strip().isdigit():
            return int(out), True
        return self.history(dataset)[0]["version"], False
