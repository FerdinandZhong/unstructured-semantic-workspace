"""P0 review defects: a decision is bound to correction state, and the review route honours withheld sources."""

from test_edit_surface import CORRECTION, DATASET, editable_mapping, pg_bin
from semantic_pipeline_v2.workspace import review_database
from test_workspace import running_server


def decide(call, mapping_id, note="kept"):
    listing = f"/api/review?dataset={DATASET}&mappingId={mapping_id}"
    _, current = call("GET", listing)
    body = {"dataset": DATASET, "mappingId": mapping_id, "decision": "keep_separate", "note": note,
            "evidenceRevision": current["evidenceRevision"]}
    return call("POST", "/api/review", body), current["evidenceRevision"], listing


def test_decision_goes_stale_after_a_correction_changes_the_mapping(tmp_path):
    with review_database(tmp_path / "state", pg_bin()) as database, running_server(database) as call:
        mapping, target = editable_mapping(call)
        (status, receipt), first, listing = decide(call, mapping["id"])
        assert status == 200
        assert call("GET", f"/api/review?dataset={DATASET}") == (200, {"receipts": [receipt]})
        request = {"dataset": DATASET, "sourceId": mapping["sourceId"], "observationId": mapping["observationId"],
                   "action": "replace", "targetId": target, "correctionRevision": mapping["correctionRevision"],
                   "requestId": "stale-1", "note": "n"}
        assert call("POST", CORRECTION, request)[0] == 200
        assert call("GET", f"/api/review?dataset={DATASET}") == (200, {"receipts": []})
        _, after = call("GET", listing)
        assert after["receipt"] is None and after["evidenceRevision"] != first
        # the old revision is refused, a fresh decision records, and the first receipt is untouched
        stale = {"dataset": DATASET, "mappingId": mapping["id"], "decision": "keep_separate", "note": "n",
                 "evidenceRevision": first}
        assert call("POST", "/api/review", stale)[0] == 409
        (status, second), _, _ = decide(call, mapping["id"], "again")
        assert status == 200 and second["evidenceRevision"] != receipt["evidenceRevision"]
        assert database.sql("SELECT count(*) FROM review_receipts;") == "2"


def test_review_route_withholds_sources_from_an_admin_with_a_restriction(tmp_path):
    with review_database(tmp_path / "state", pg_bin()) as database, running_server(database) as call:
        mapping, _ = editable_mapping(call)
        (status, receipt), revision, listing = decide(call, mapping["id"])
        assert status == 200
        database.sql("INSERT INTO v2_demo_restrictions VALUES ('admin', :'source');", source=mapping["sourceId"])
        assert call("GET", listing)[0] == 403
        assert all(item["mappingId"] != mapping["id"] for item in call("GET", f"/api/review?dataset={DATASET}")[1]["receipts"])
        body = {"dataset": DATASET, "mappingId": mapping["id"], "decision": "request_evidence", "note": "n",
                "evidenceRevision": revision}
        assert call("POST", "/api/review", body)[0] == 403
        assert database.sql("SELECT count(*) FROM review_receipts;") == "1"
