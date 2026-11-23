"""Approve all (2026-11-22): one request approves a listed set of evidence links or concepts in one transaction, with
the same checks as a single correction. Admin only; rejected items are never touched; a second click changes nothing."""

from urllib.parse import urlencode

from semantic_pipeline_v2.evidence_links import EvidenceLinks
from semantic_pipeline_v2.workspace import review_database
from test_catalog_graph import DATASET, OPEN, link, pg_bin, seed, session
from test_workspace import running_server

ROUTE = "/api/catalog-correction/approve-all"


def test_approve_all_links_and_concepts(tmp_path):
    query = "?" + urlencode({"dataset": DATASET})
    with review_database(tmp_path / "state", pg_bin()) as database, running_server(
            database, evidence_dir=tmp_path / "evidence") as call:
        definition = seed(database)
        EvidenceLinks(database).replace(DATASET, OPEN, [
            link("c-open", OPEN, definition, "Customer owes 9 days"), link("c-two", OPEN, definition, "Second clause"),
            link("c-rej", OPEN, definition, "Rejected clause")])
        assert call("POST", "/api/catalog-correction", {"dataset": DATASET, "kind": "evidence_link", "chunkId": "c-rej",
                                                         "definitionId": definition, "patch": {"status": "rejected"}})[0] == 200
        assert call("POST", "/api/catalog-correction", {"dataset": DATASET, "kind": "concept", "conceptId": "mixed",
                                                         "patch": {"status": "rejected"}})[0] == 200
        items = [{"chunkId": c, "definitionId": definition} for c in ("c-open", "c-two", "c-rej")]
        body = {"dataset": DATASET, "kind": "evidence_link", "items": items}
        # a malformed or unknown item changes nothing
        assert call("POST", ROUTE, {**body, "items": items + [{"chunkId": "zzz", "definitionId": definition}]})[0] == 400
        assert call("POST", ROUTE, {**body, "kind": "concept_link"})[0] == 400
        assert call("POST", ROUTE, {**body, "items": "all"})[0] == 400
        status, out = call("POST", ROUTE, body)
        assert status == 200 and out["approved"] == 2 and out["skipped"] == {"rejected": 1, "approved": 0}
        links = call("GET", "/api/evidence-links?" + urlencode({"dataset": DATASET, "sourceId": OPEN}))[1]["links"]
        assert {k["chunk_id"]: k["status"] for k in links} == {"c-open": "corrected", "c-two": "corrected", "c-rej": "rejected"}
        status, again = call("POST", ROUTE, body)          # idempotent: nothing new is written
        assert status == 200 and again["approved"] == 0 and again["skipped"] == {"rejected": 1, "approved": 2}
        concepts = {"dataset": DATASET, "kind": "concept", "items": ["open", "hidden", "mixed"]}
        status, out = call("POST", ROUTE, concepts)
        assert status == 200 and out["approved"] == 2 and out["skipped"]["rejected"] == 1
        read = {c["id"]: c["status"] for c in call("GET", "/api/catalog" + query)[1]["concepts"]}
        assert read == {"open": "corrected", "hidden": "corrected", "mixed": "rejected"}
        # a reader gets 403; so does an admin-gated request from a restricted account
        assert call("POST", ROUTE, body, session(call, "finance_reader"))[0] == 403
        restricted = session(call, "finance_restricted")
        assert call("POST", ROUTE, {**body, "items": [{"chunkId": "c-hid", "definitionId": definition}]}, restricted)[0] == 403
    # the approvals are correction rows: they survive a restart and overlay the pipeline records, as one approval does
    with review_database(tmp_path / "state", pg_bin()) as database, running_server(
            database, evidence_dir=tmp_path / "evidence") as call:
        read = {c["id"]: c["status"] for c in call("GET", "/api/catalog" + query)[1]["concepts"]}
        assert read["open"] == "corrected" and read["mixed"] == "rejected"
        assert EvidenceLinks(database).read(DATASET, OPEN)[0]["status"] == "proposed"
