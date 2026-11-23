"""C6: one edit surface. Correction notes, optional decisions on any mapping, role limits."""

import os
from pathlib import Path

import pytest

from semantic_pipeline_v2.workspace import load_workspace, review_database
from test_workspace import running_server

DATASET = "financial-services"
CORRECTION = "/api/mapping-correction"


def editable_mapping(call):
    status, response = call("GET", "/api/workspace")
    assert status == 200
    catalog = next(item for item in response["datasets"] if item["id"] == DATASET)
    mapping = next(item for item in catalog["knowledge"]["mappings"] if item.get("correctionRevision"))
    model = catalog["knowledge"]["structured"]
    return mapping, next(item["id"] for item in model["fields"] + model["metrics"] if item["id"] != mapping.get("targetId"))


def pg_bin():
    value = os.environ.get("V2_PG_BIN")
    if not value:
        pytest.skip("set V2_PG_BIN for private PostgreSQL integration tests")
    return Path(value)


def test_correction_note_persists_with_actor_and_time(tmp_path):
    with review_database(tmp_path / "state", pg_bin()) as database, running_server(database) as call:
        mapping, target = editable_mapping(call)
        request = {"dataset": DATASET, "sourceId": mapping["sourceId"], "observationId": mapping["observationId"],
                   "action": "replace", "targetId": target, "correctionRevision": mapping["correctionRevision"],
                   "requestId": "note-1", "note": "Rating scale confirmed with the data owner."}
        assert call("POST", CORRECTION, {**request, "note": "x" * 481})[0] == 400
        assert call("POST", CORRECTION, {**request, "note": 7})[0] == 400
        assert call("POST", CORRECTION, request)[0] == 200
        saved, _ = editable_mapping(call)
        assert saved["correctionNote"] == request["note"]
        assert saved["correctionActor"] == "admin" and saved["correctionAt"]
        assert saved["correctionAction"] == "replace"
    with review_database(tmp_path / "state", pg_bin()) as database, running_server(database) as call:
        assert editable_mapping(call)[0]["correctionNote"] == request["note"]


def test_decision_on_any_mapping_survives_restart_and_is_optional(tmp_path):
    workspace = load_workspace(DATASET)
    ids = [item["id"] for item in workspace["knowledge"]["mappings"]]
    chosen = ids[-1]
    assert chosen != f"{DATASET}:mapping:risk_rating"
    with review_database(tmp_path / "state", pg_bin()) as database, running_server(database) as call:
        listing = f"/api/review?dataset={DATASET}"
        assert call("GET", listing) == (200, {"receipts": []})
        status, current = call("GET", f"{listing}&mappingId={chosen}")
        assert status == 200 and current["receipt"] is None
        body = {"dataset": DATASET, "mappingId": chosen, "decision": "request_evidence",
                "note": "Need the receipt.", "evidenceRevision": current["evidenceRevision"]}
        status, receipt = call("POST", "/api/review", body)
        assert status == 200 and receipt["mappingId"] == chosen
        assert call("GET", listing) == (200, {"receipts": [receipt]})
        assert call("POST", "/api/review", {**body, "evidenceRevision": "stale"})[0] == 409
        assert call("POST", "/api/review", {**body, "mappingId": DATASET + ":mapping:absent"})[0] == 400
    with review_database(tmp_path / "state", pg_bin()) as database, running_server(database) as call:
        assert call("GET", f"/api/review?dataset={DATASET}&mappingId={chosen}")[1]["receipt"] == receipt
        assert call("GET", f"/api/review?dataset={DATASET}") == (200, {"receipts": [receipt]})


def test_reader_cannot_write_correction_or_decision(tmp_path):
    with review_database(tmp_path / "state", pg_bin()) as database, running_server(database) as call:
        mapping, target = editable_mapping(call)
        _, _, headers = call("POST", "/api/session", {"userId": "finance_reader"}, include_headers=True)
        reader = {"Cookie": "; ".join(value.split(";", 1)[0] for name, value in headers if name == "Set-Cookie")}
        request = {"dataset": DATASET, "sourceId": mapping["sourceId"], "observationId": mapping["observationId"],
                   "action": "replace", "targetId": target, "correctionRevision": mapping["correctionRevision"],
                   "requestId": "reader-1", "note": "n"}
        assert call("POST", CORRECTION, request, reader)[0] == 403
        assert call("POST", "/api/review", {"dataset": DATASET, "mappingId": mapping["id"],
                    "decision": "keep_separate", "note": "n", "evidenceRevision": "x"}, reader)[0] == 403
        assert call("GET", f"/api/review?dataset={DATASET}", headers=reader)[0] == 403
        assert database.sql("SELECT count(*) FROM v2_mapping_corrections;") == "0"
        assert database.sql("SELECT count(*) FROM review_receipts;") == "0"


def ui_writes(code):
    """Endpoints written with an explicit literal POST. Any other way to choose a method fails the scan.

    Every `method` token must be a literal GET or POST, so a variable, a computed key, PUT, PATCH or DELETE
    cannot hide. A call passing its options as a variable or spread is refused too (the callLocal wrapper
    itself, which forwards its own `options` parameter, is the only allowed forward).
    """
    import re
    for token in re.finditer(r"\bmethod\b", code):
        assert re.match(r"""method\s*:\s*['"](?:GET|POST)['"]""", code[token.start():token.start() + 40]), \
            code[token.start() - 30:token.start() + 40]
    assert not re.search(r"\.open\(\s*['\"]?(?:PUT|PATCH|DELETE|POST)", code, re.I), "XHR open"
    for call in re.finditer(r"(?:callLocal|fetch|request)\(\s*[^,)]+,\s*([A-Za-z_.][\w.]*|\.\.\.\w+)\s*[,)]", code):
        assert call.group(1) in {"options"}, call.group(0)
    return re.findall(r"""(?:callLocal|fetch|request)\(\s*([^,)]+)[^;]*?method\s*:\s*['"]POST""", code, re.S)


def test_write_scan_catches_variable_and_other_verbs():
    import re
    for bad in ("fetch('/api/x', {method: verb})", "fetch('/api/x', {method:'PUT'})", "x.open('DELETE', '/a')",
                "callLocal('/api/x', init)", "fetch('/a', {method:'patch'})"):
        with pytest.raises(AssertionError):
            ui_writes(bad)
    # the old literal-POST pattern saw none of these
    assert not re.search(r"method\s*:\s*['\"]POST", "fetch('/a', {method: verb})")
    assert ui_writes("callLocal('/api/get?a=1'); fetch(u, {method:'GET'})") == []
    assert ui_writes("callLocal('/api/y', {method:'POST', body:b})") == ["'/api/y'"]


UI = Path(__file__).resolve().parents[1] / "docs/design/v2-workspace"
OWNED = ("workspace.js", "ingest.js", "knowledge.js", "review.js", "entity.js", "catalog.js", "talk-to-data.js")
# The React pages are scanned from their sources; the built pipeline.js is minified and not scanned.
OWNED += tuple(f"app/src/{name}" for name in ("main.jsx", "lib.js", "ui.jsx", "Flow.jsx", "Evidence.jsx",
                                              "Concepts.jsx", "Model.jsx", "Ask.jsx",
                                              "FormRows.jsx", "ApproveAll.jsx", "AskTools.jsx", "Entity360.jsx",
                                              # Ask progress panel (2026-11-22): reads GET /api/ask/progress only, no write
                                              "AskProgress.jsx"))


def test_ui_has_one_edit_surface_and_known_write_calls():
    import re
    code = {name: (UI / name).read_text() for name in OWNED}
    everything = "\n".join(code.values())
    for gone in ("showEvidenceDecision", "Open evidence decision", "upload-prompt", "uploadPrompt",
                 "review-rationale", "data-action=\"approve\"", "'approve'"):
        assert gone not in everything, gone
    # Every write goes through callLocal or fetch with an explicit POST. Only these endpoints are written.
    assert {item.strip("'\"` ") for item in ui_writes(everything)} == {
        "/api/session", "/api/mapping-correction", "/api/review", "/api/ocr", "/api/talk-to-data",
            # Output C preview and confirm: admin-gated, template-bound, not a second edit surface
            "/api/physical-rows/' + action",
            # E11: Output A and B corrections, and the structured answer (read-only, POST for the body)
            "/api/catalog-correction", "/api/answer",
            # 2026-11-22: the rows, series and suggestions behind an answer (read-only, a server-issued handle), and
            # one confirmed batch of approvals (admin, the same correction rows as one approval)
            "/api/answer/detail", "/api/catalog-correction/approve-all",
            # step 6 harness (plan 2026-11-22-v2-step6-harness): the router, the write preview and its confirm (admin,
            # one-use approval), the job status read, and the connection save, import and test (admin)
            "/api/ask", "/api/ask/write/preview", "/api/ask/write/confirm", "/api/ask/write/status",
            "/api/connections/' + item.id", "/api/connections/' + item.id + '/import", "/api/connections/' + item.id + '/test",
            # Output B build: admin-only preview, then confirm of the previewed version
            "/api/catalog/build/' + action",
            # Entity 360 (2026-11-22): builds one customer report on request; read-only, POST only for the body
            "/api/entity/report",
            # 2026-11-23: the rail reads whether publishing now would change anything; the release preview is read-only
            # (admin, writes nothing), POST only for the body
            "/api/publish/preview"}, ui_writes(everything)
    assert not re.search(r"\.sendBeacon|XMLHttpRequest|WebSocket", everything)
    # Confirmations remain for publication and for sending a file to Mistral.
    assert "publish-confirm" in code["workspace.js"] and "ingest-ocr-confirm" in code["ingest.js"]
