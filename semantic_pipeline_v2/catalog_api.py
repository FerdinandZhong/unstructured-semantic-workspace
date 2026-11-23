"""E11: human corrections of Output A links and Output B concepts, and the database-built graph.

A correction is an appended row with actor and revision. The pipeline records are never changed; `apply_*` overlays them on read."""

import json

from .semantic_store import _jsonb, _lit

PREDICATES = {"exactMatch", "closeMatch", "broadMatch", "narrowMatch", "relatedMatch"}
STATUSES = {"proposed", "corrected", "rejected"}
RELATIONS = {"states_value_of", "is_about", "none"}
# kind -> {patch key: check}
PATCHES = {
    "concept": {"label": "text", "definition": "text", "status": STATUSES},
    "concept_link": {"predicate": PREDICATES, "status": STATUSES},
    "evidence_link": {"relation": RELATIONS, "statedValue": "text?", "unit": "text?", "period": "text?",
                      "definitionId": "text?", "status": STATUSES},
}
_DDL = """CREATE TABLE IF NOT EXISTS v2_catalog_correction (
    revision bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, dataset text NOT NULL, kind text NOT NULL,
    key text NOT NULL, patch jsonb NOT NULL, actor text NOT NULL, at timestamptz NOT NULL DEFAULT now());"""


def validate_patch(kind, patch):
    allowed = PATCHES[kind]
    if not isinstance(patch, dict) or not patch or not set(patch) <= set(allowed):
        raise ValueError("Require a patch with " + ", ".join(sorted(allowed)))
    for key, value in patch.items():
        rule = allowed[key]
        if isinstance(rule, set):
            ok = value in rule
        elif rule == "text?":
            ok = value is None or (isinstance(value, str) and len(value) <= 200 and "\x00" not in value)
        else:
            ok = isinstance(value, str) and value.strip() != "" and len(value) <= 2000 and "\x00" not in value
        if not ok:
            raise ValueError(f"Invalid {key}")
    return patch


def correction_key(kind, body):
    """Stable key of the thing corrected: concept id, concept id + definition, or chunk + original definition."""
    if kind == "concept":
        return body["conceptId"]
    first = body["conceptId"] if kind == "concept_link" else body["chunkId"]
    if not isinstance(first, str) or not isinstance(body["definitionId"], (str, type(None))):
        raise ValueError("Invalid correction target")
    return json.dumps([first, body["definitionId"] or ""])


class CatalogCorrections:
    def __init__(self, database):
        self.database = database
        database.sql(_DDL)

    def record(self, dataset, kind, key, patch, actor):
        out = self.database.sql(
            f"INSERT INTO v2_catalog_correction (dataset, kind, key, patch, actor) VALUES ({_lit(dataset)}, "
            f"{_lit(kind)}, {_lit(key)}, {_jsonb(patch)}, {_lit(actor)}) RETURNING revision;")
        return int(out.splitlines()[0])

    def record_many(self, dataset, kind, keys, patch, actor):
        """One patch on several keys: one INSERT, so one transaction. Returns the revisions."""
        if not keys:
            return []
        rows = ", ".join(f"({_lit(dataset)}, {_lit(kind)}, {_lit(key)}, {_jsonb(patch)}, {_lit(actor)})" for key in keys)
        out = self.database.sql(f"INSERT INTO v2_catalog_correction (dataset, kind, key, patch, actor) VALUES {rows} "
                                "RETURNING revision;")
        return [int(line) for line in out.splitlines() if line.strip()]

    def latest(self, dataset):
        """{(kind, key): [rows in revision order]}"""
        out = self.database.sql(
            "SELECT COALESCE(jsonb_agg(jsonb_build_object('revision', revision, 'kind', kind, 'key', key, "
            "'patch', patch, 'actor', actor, 'at', at) ORDER BY revision), '[]'::jsonb) "
            "FROM v2_catalog_correction WHERE dataset = :'dataset';", dataset=dataset)
        by = {}
        for row in json.loads(out):
            by.setdefault((row["kind"], row["key"]), []).append(row)
        return by


def _stamp(rows):
    last = rows[-1]
    return {"actor": last["actor"], "revision": last["revision"], "at": last["at"]}


def apply_concepts(concepts, corrections):
    out = []
    for c in concepts:
        c = dict(c)
        rows = corrections.get(("concept", c["id"]), [])
        for row in rows:
            c.update({k: v for k, v in row["patch"].items()})
        if rows:
            if not any("status" in r["patch"] for r in rows):
                c["status"] = "corrected"
            c["correction"] = _stamp(rows)
        links = []
        for k in c.get("links", []):
            k = dict(k)
            rows = corrections.get(("concept_link", json.dumps([c["id"], k["definition_id"] or ""])), [])
            for row in rows:
                k.update(row["patch"])
            if rows:
                if not any("status" in r["patch"] for r in rows):
                    k["status"] = "corrected"
                k["correction"] = _stamp(rows)
            links.append(k)
        c["links"] = links
        out.append(c)
    return out


def apply_links(links, corrections):
    """Output A links with human edits; the original definition stays in `notes`."""
    out = []
    for k in links:
        original = (k.get("notes") or {}).get("originalDefinitionId") or k["definition_id"] or ""
        rows = corrections.get(("evidence_link", json.dumps([k["chunk_id"], original])), []) or \
            corrections.get(("evidence_link", json.dumps([k["chunk_id"], k["definition_id"] or ""])), [])
        if not rows:
            out.append(k)
            continue
        k = {**k, "notes": {**(k.get("notes") or {}), "originalDefinitionId": original}}
        changed = set()
        for row in rows:
            for key, value in row["patch"].items():
                name = {"statedValue": "stated_value", "definitionId": "definition_id"}.get(key, key)
                k[name] = value
                if name != "status":
                    changed.add(name)
        k["changed"] = sorted(changed)      # display: empty for an approval, the edited fields for a correction
        if not any("status" in r["patch"] for r in rows):
            k["status"] = "corrected"
        k["actor"], k["recorded_at"], k["revision"] = rows[-1]["actor"], rows[-1]["at"], rows[-1]["revision"]
        out.append(k)
    return out


def scope_concepts(concepts, chunk_source, visible):
    """A concept shows only evidence of readable sources. One whose every evidence chunk is withheld is dropped.
    Its copied examples are hidden when any evidence chunk is withheld."""
    out = []
    for c in concepts:
        chunks = c["evidence"]["chunks"]
        keep = [x for x in chunks if x in chunk_source and visible(chunk_source[x])]
        if chunks and not keep:
            continue
        ok = set(keep)
        links = [{**k, "support": {**k.get("support", {}), "chunks": [x for x in k.get("support", {}).get("chunks", []) if x in ok]}}
                 for k in c.get("links", [])]
        # `examples` are copied from the evidence chunks with no record of which one: hide them when any is withheld
        partial = len(keep) < len(chunks)
        scoped = {**c, "links": links, "evidence": {**c["evidence"], "chunks": keep}}
        if partial:
            # Examples and synonyms are copied from passages, with no record of which one. The meaning of a concept
            # found in documents only is written from all of them. A model-anchored meaning comes from the model.
            scoped["examples"], scoped["synonyms"] = [], []
            if c.get("origin") == "document":
                scoped["definition"] = "Meaning withheld: part of this concept's evidence is not readable for this account."
        out.append(scoped)
    return out


def graph(definitions, concepts, links):
    """Nodes and edges from rows. `links` and `concepts` are already scoped and corrected."""
    nodes, edges = {}, []

    def node(ident, kind, label, **extra):
        nodes.setdefault(ident, {"id": ident, "kind": kind, "label": label, **extra})

    for d in definitions:
        node(d["id"], "definition", d.get("label") or d["id"].rsplit("/", 1)[-1], definitionKind=d["kind"])
    for k in links:
        node(k["source_id"], "source", k["source_id"].rsplit(":", 1)[-1])
        node(k["chunk_id"], "chunk", k["span"]["text"][:60], sourceId=k["source_id"])
        edges.append({"source": k["source_id"], "target": k["chunk_id"], "relation": "contains"})
        if k["definition_id"] and k["status"] != "rejected":
            node(k["definition_id"], "definition", k["definition_id"].rsplit("/", 1)[-1])
            edges.append({"source": k["chunk_id"], "target": k["definition_id"], "relation": k["relation"],
                          "status": k["status"]})
        if k["entity_key"] != "unresolved":
            node("entity:" + k["entity_key"], "entity", k["entity_key"])
            edges.append({"source": k["chunk_id"], "target": "entity:" + k["entity_key"], "relation": "about"})
    for c in concepts:
        node("concept:" + c["id"], "concept", c["label"], origin=c["origin"], status=c["status"])
        for x in c["evidence"]["chunks"]:
            if x in nodes:
                edges.append({"source": "concept:" + c["id"], "target": x, "relation": "evidence"})
        for k in c["links"]:
            if k["status"] != "rejected" and k["definition_id"] in nodes:
                edges.append({"source": "concept:" + c["id"], "target": k["definition_id"],
                              "relation": k["predicate"], "status": k["status"]})
    return {"empty": not nodes, "nodes": list(nodes.values()), "edges": edges}
