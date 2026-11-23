"""E11 server side: /api/catalog, /api/catalog-correction and /api/graph read private PostgreSQL rows."""

import os
from pathlib import Path
from urllib.parse import urlencode

import pytest

from semantic_pipeline_v2.evidence_links import EvidenceLinks
from semantic_pipeline_v2.semantic_store import SemanticStore
from semantic_pipeline_v2.workspace import review_database
from test_workspace import running_server

DATASET = "financial-services"
OPEN, HIDDEN = f"{DATASET}:source:finance-note", f"{DATASET}:source:jade-adverse-media"
ROOT = Path(__file__).resolve().parents[1]


def pg_bin():
    value = os.environ.get("V2_PG_BIN")
    if not value:
        pytest.skip("set V2_PG_BIN for private PostgreSQL integration tests")
    return Path(value)


def link(chunk, source, definition, text, **extra):
    return {"chunk_id": chunk, "source_id": source, "source_revision": "b" * 64,
            "span": {"start": 0, "end": len(text), "page": None, "text": text}, "definition_id": definition,
            "relation": "states_value_of", "stated_value": "9", "unit": "days", "period": None,
            "entity_key": "cust-1", "interpretation": "x", "candidates": [definition], "scores": {},
            "method": "m", "model_revision": None, "status": "proposed", "actor": "pipeline", "notes": {}, **extra}


def concept(ident, label, origin, chunks, definition):
    return {"id": ident, "label": label, "definition": f"{label} definition", "synonyms": [], "examples": [],
            "origin": origin, "type": "attribute", "unit": None, "grain": None,
            "links": [{"definition_id": definition, "predicate": "closeMatch", "status": "proposed",
                       "reason": "r", "support": {"chunks": chunks}}],
            "evidence": {"catalog_version": "cat-1", "chunks": chunks}, "status": "proposed", "actor": "pipeline-b"}


def seed(database):
    store = SemanticStore(database)
    store.import_ossie(DATASET, ROOT / "demo_data/finance/finance.ossie.yaml")
    definition = store.list_definitions(DATASET)[0]["id"]
    EvidenceLinks(database).replace(DATASET, OPEN, [link("c-open", OPEN, definition, "Customer NRIC S1234567D owes 9 days")])
    EvidenceLinks(database).replace(DATASET, HIDDEN, [link("c-hid", HIDDEN, definition, "Adverse media")])
    store.write_catalog(DATASET, "cat-1", [
        concept("open", "Open concept", "document", ["c-open"], definition),
        concept("hidden", "Hidden concept", "document", ["c-hid"], definition),
        concept("mixed", "Mixed concept", "both", ["c-open", "c-hid"], definition)])
    return definition


def session(call, user):
    _, _, headers = call("POST", "/api/session", {"userId": user, "recover": True}, include_headers=True)
    return {"Cookie": "; ".join(v.split(";", 1)[0] for n, v in headers if n == "Set-Cookie")}


def test_catalog_correction_and_graph_routes(tmp_path):
    query = "?" + urlencode({"dataset": DATASET})
    with review_database(tmp_path / "state", pg_bin()) as database, running_server(
            database, evidence_dir=tmp_path / "evidence") as call:
        # empty database: explicit empty state, no fixture data
        assert call("GET", "/api/catalog" + query) == (200, {"version": None, "concepts": [], "empty": True})
        status, graph = call("GET", "/api/graph" + query)
        assert status == 200 and graph["empty"] is True and graph["nodes"] == [] and graph["edges"] == []
        definition = seed(database)
        status, read = call("GET", "/api/catalog" + query)
        assert status == 200 and read["version"] == "cat-1" and read["editable"] is True
        assert {c["id"] for c in read["concepts"]} == {"open", "hidden", "mixed"}
        open_concept = next(c for c in read["concepts"] if c["id"] == "open")
        assert open_concept["origin"] == "document" and open_concept["links"][0]["predicate"] == "closeMatch"
        assert open_concept["evidenceDetail"][0]["chunkId"] == "c-open"
        assert "S1234567D" not in str(read) and "S12****7D" in str(read)  # NRIC masked
        # graph from database rows
        status, graph = call("GET", "/api/graph" + query)
        kinds = {n["kind"] for n in graph["nodes"]}
        assert not graph["empty"] and {"definition", "concept", "source", "chunk", "entity"} <= kinds
        assert any(e["relation"] == "closeMatch" and e["target"] == definition for e in graph["edges"])
        assert any(e["relation"] == "states_value_of" and e["target"] == definition for e in graph["edges"])
        assert "S1234567D" not in str(graph)
        edit = {"dataset": DATASET, "kind": "concept", "conceptId": "open", "patch": {"label": "Renamed"}}
        assert call("POST", "/api/catalog-correction", {**edit, "conceptId": "nope"})[0] == 400
        assert call("POST", "/api/catalog-correction", {**edit, "patch": {"label": ""}})[0] == 400
        assert call("POST", "/api/catalog-correction", {**edit, "patch": {"bogus": 1}})[0] == 400
        status, first = call("POST", "/api/catalog-correction", edit)
        assert status == 200 and first["revision"]
        status, second = call("POST", "/api/catalog-correction", {
            "dataset": DATASET, "kind": "concept_link", "conceptId": "open", "definitionId": definition,
            "patch": {"predicate": "broadMatch"}})
        assert status == 200 and second["revision"] > first["revision"]
        status, third = call("POST", "/api/catalog-correction", {
            "dataset": DATASET, "kind": "evidence_link", "chunkId": "c-open", "definitionId": definition,
            "patch": {"relation": "is_about", "statedValue": "12"}})
        assert status == 200
        assert call("POST", "/api/catalog-correction", {  # unknown link
            "dataset": DATASET, "kind": "evidence_link", "chunkId": "zzz", "definitionId": definition,
            "patch": {"relation": "is_about"}})[0] == 400
        # reader and restricted reader: no controls, withheld evidence gone
        reader = session(call, "finance_reader")
        assert call("GET", "/api/catalog" + query, headers=reader)[1]["editable"] is False
        assert call("POST", "/api/catalog-correction", edit, reader)[0] == 403
        restricted = session(call, "finance_restricted")
        seen = call("GET", "/api/catalog" + query, headers=restricted)[1]
        assert {c["id"] for c in seen["concepts"]} == {"open", "mixed"}
        mixed = next(c for c in seen["concepts"] if c["id"] == "mixed")
        assert [e["chunkId"] for e in mixed["evidenceDetail"]] == ["c-open"]
        assert mixed["links"][0]["support"]["chunks"] == ["c-open"]
        graph = call("GET", "/api/graph" + query, headers=restricted)[1]
        assert "c-hid" not in str(graph) and HIDDEN not in str(graph) and "Hidden concept" not in str(graph)
        assert call("POST", "/api/catalog-correction", {
            "dataset": DATASET, "kind": "evidence_link", "chunkId": "c-hid", "definitionId": definition,
            "patch": {"relation": "is_about"}}, restricted)[0] == 403
    # corrections survive a restart; the pipeline record is untouched
    with review_database(tmp_path / "state", pg_bin()) as database, running_server(
            database, evidence_dir=tmp_path / "evidence") as call:
        concept_row = next(c for c in call("GET", "/api/catalog" + query)[1]["concepts"] if c["id"] == "open")
        assert concept_row["label"] == "Renamed" and concept_row["status"] == "corrected"
        assert concept_row["correction"]["actor"] == "admin" and concept_row["correction"]["revision"] == first["revision"]
        assert concept_row["links"][0]["predicate"] == "broadMatch" and concept_row["links"][0]["status"] == "corrected"
        assert SemanticStore(database).read_catalog(DATASET, "cat-1")[1]["label"] in ("Open concept", "Mixed concept")
        [saved] = [k for k in call("GET", "/api/evidence-links?" + urlencode({"dataset": DATASET, "sourceId": OPEN}))[1]["links"]]
        assert saved["relation"] == "is_about" and saved["stated_value"] == "12" and saved["status"] == "corrected"
        assert saved["actor"] == "admin" and EvidenceLinks(database).read(DATASET, OPEN)[0]["relation"] == "states_value_of"
