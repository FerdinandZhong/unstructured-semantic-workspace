"""Exercise the common prepared-source workflow without a network or provider."""

from copy import deepcopy
from contextlib import contextmanager
import csv
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Thread

import pytest

from semantic_pipeline_v2 import workspace
from semantic_pipeline_v2.workspace_server import create_server


@pytest.mark.parametrize("dataset", workspace.DATASETS)
def test_shared_stages_and_exact_evidence(dataset):
    record = workspace.load_workspace(dataset)
    assert [stage["id"] for stage in workspace.STAGES] == ["ingest", "knowledge", "review", "entity"]
    ingest = workspace.stage_view(record, "ingest")
    model = workspace.stage_view(record, "ontology")
    assert record["catalog"]["fieldCount"] == len(model["fields"])
    assert record["catalog"]["tableCount"] == len(model["datasets"])
    assert workspace.stage_view(record, "source", model["modelSource"]) in ingest["sources"]
    assert workspace.stage_view(record, "review")
    for observation in workspace.stage_view(record, "knowledge")["observations"]:
        citation = observation["citation"]
        if citation.get("kind") == "transcript":
            archive = json.loads((workspace.ROOT / workspace.FINANCE / "jade_transcript.json").read_text())
            assert archive["audioSha256"] == citation["sha256"]
            assert observation["quote"] == archive["text"][citation["startOffset"]:citation["endOffset"]]
            continue
        evidence = (workspace.ROOT / citation["path"]).read_bytes()
        assert workspace.digest(evidence) == citation["sha256"]
        lines = evidence.decode().splitlines()
        assert "\n".join(lines[citation["startLine"] - 1:citation["endLine"]]) == observation["quote"]

    answer = workspace.stage_view(record, "entity")[0]["answer"]
    assert answer["status"] == "evidence_only"
    assert answer["observationIds"]


def test_finance_preserves_inventory_without_private_source_citations_or_identity():
    record = workspace.load_workspace("financial-services")
    rows = {item["id"].split(":")[-1]: item for item in record["sources"]}
    assert sum(item.get("status") == "xfa_render_required" for item in rows.values()) == 2
    for key in ("cleaned_audio", "jade_call"):
        assert rows[key]["visibility"] == "presenter_metadata_only"
        assert "citation" not in rows[key]
    assert rows["cleaned_audio"]["examples"] == []
    assert rows["cleaned_audio"]["transcriptStatus"] == "unavailable"
    assert rows["jade_call"]["transcriptStatus"] in ("available", "unavailable")
    assert all(item["citation"]["kind"] == "transcript" for item in rows["jade_call"]["examples"])
    for number in range(1, 6):
        assert "citation" not in rows[f"cleaned_pdf_{number}"]
    jade, = record["entities"]
    assert jade["sourceIds"] == ["financial-services:source:jade-adverse-media"]
    assert "no recorded risk rating" in jade["answer"]["text"]


def test_finance_workbook_counts_only_valid_prepared_tables(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "FINANCE_PROJECTIONS", tmp_path / "projections")
    key = "financial-services:source:workbook"

    def read():
        finance = workspace.load_workspace("financial-services")
        return finance, workspace.stage_view(finance, "source", key)

    finance, workbook = read()
    table_info = workbook["structuredImport"]
    assert table_info["synthetic"] is True
    assert table_info["tableCount"] == 11 and table_info["availableTableCount"] == 0
    assert table_info["status"] == "unavailable"
    assert all(table["rowCount"] is None for table in table_info["tables"])
    model = finance["knowledge"]["structured"]
    all_tables = {table["name"]: table for table in table_info["tables"]}
    assert set(all_tables) == {definition["name"] for definition in model["datasets"]
                              if definition.get("origin") != "extracted"}  # Output C tables hold form rows, not workbook CSVs

    workspace.FINANCE_PROJECTIONS.mkdir()
    workbook_hash = workbook["citation"]["sha256"]
    for table, info in all_tables.items():
        columns = [field["name"].split("/", 1)[1] for field in model["fields"]
                   if field["name"].split("/", 1)[0] == table]
        with (workspace.FINANCE_PROJECTIONS / info["filename"]).open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(columns)
            payload = [workbook_hash if column == "SourceBatch" else "local-only secret"
                       for column in columns]
            writer.writerows([payload, payload])
    _, loaded = read()
    result = loaded["structuredImport"]
    assert loaded["citation"] == workbook["citation"]
    assert result["status"] == "available" and result["availableTableCount"] == 11
    assert all(table["rowCount"] == 2 for table in result["tables"])
    assert "local-only secret" not in json.dumps(loaded)

    first = workspace.FINANCE_PROJECTIONS / "Customers.csv"
    first.write_text(first.read_text().replace(workbook_hash, "wrong batch"))
    _, partial = read()
    result = partial["structuredImport"]
    assert result["status"] == "unavailable" and result["availableTableCount"] == 10
    assert next(table for table in result["tables"] if table["name"] == "Customers")["rowCount"] is None

    portable = workspace.export_bytes()
    assert b'"availableTableCount": 0' in portable and b"local-only secret" not in portable


def test_manufacturing_does_not_apply_westside_evidence_to_another_account():
    record = workspace.load_workspace("manufacturing")
    westside, supplier, second_account = record["entities"]
    assert len(westside["sourceIds"]) == 5
    assert supplier["sourceIds"] == ["manufacturing:source:MFG-006"]
    assert second_account["sourceIds"] == ["manufacturing:source:MFG-007"]
    assert "Delivery remains unknown" in westside["answer"]["text"]
    contaminated = deepcopy(record)
    contaminated["entities"][2]["observationIds"].extend(westside["observationIds"])
    with pytest.raises(ValueError, match="outside its source scope"):
        workspace.validate_links(contaminated)


def test_no_cross_dataset_selection_or_review(tmp_path):
    record = workspace.load_workspace("financial-services")
    with pytest.raises(ValueError, match="unavailable"):
        workspace.stage_view(record, "source", "manufacturing:source:MFG-003")
    with pytest.raises(ValueError, match="unavailable"):
        workspace.record_review(record, "manufacturing:mapping:order-quantity", "keep_separate", None,
                                note="A rehearsal note", evidence_revision="unknown")
    assert not list(tmp_path.iterdir())


def test_review_database_refuses_unmarked_existing_state(tmp_path):
    sentinel = tmp_path / "unrelated-data"
    sentinel.write_text("preserve this")
    with pytest.raises(ValueError, match="unmarked, nonempty"):
        with workspace.review_database(tmp_path, None):
            pytest.fail("An unrelated directory must never open a database")
    assert list(tmp_path.iterdir()) == [sentinel]
    assert sentinel.read_text() == "preserve this"


def test_local_review_replay_conflict_and_scope(tmp_path):
    pg_bin = os.environ.get("V2_PG_BIN")
    if not pg_bin:
        pytest.skip("set V2_PG_BIN for private PostgreSQL integration tests")
    finance = workspace.load_workspace("financial-services")
    factory = workspace.load_workspace("manufacturing")
    case = "financial-services:mapping:risk_rating"
    state = tmp_path / "isolated"
    with workspace.review_database(state, Path(pg_bin)) as database, running_server(database) as call:
        query = f"?dataset=financial-services&mappingId={case}"
        status, current = call("GET", "/api/review" + query)
        assert status == 200 and current["receipt"] is None
        first = {"dataset": "financial-services", "mappingId": case, "decision": "keep_separate",
                 "note": "A source allegation cannot set recorded risk.",
                 "evidenceRevision": current["evidenceRevision"]}
        assert call("POST", "/api/review", {**first, "evidenceRevision": "stale"})[0] == 409
        assert call("POST", "/api/review", {**first, "note": " "})[0] == 400
        assert call("POST", "/api/review", {**first, "note": "x" * 481})[0] == 400
        status, receipt = call("POST", "/api/review", first)
        assert status == 200
        assert receipt["mode"] == "local_rehearsal" and receipt["publication"] == "none"
        assert receipt["note"] == first["note"]
        assert call("POST", "/api/review", first) == (200, receipt)
        assert call("POST", "/api/review", {**first, "decision": "request_evidence"})[0] == 409
        assert call("POST", "/api/review", {**first, "note": "Different note"})[0] == 409
        assert call("GET", "/api/review" + query) == (200, {**current, "receipt": receipt})
        selection = "manufacturing:mapping:delivery-vs-due-date"
        workspace.record_review(factory, selection, "request_evidence", database,
                                note="Check for receipt.", evidence_revision=workspace.review_revision(factory, selection))
        assert database.sql("SELECT count(*) FROM review_receipts") == "2"
    with workspace.review_database(state, Path(pg_bin)) as restarted:
        assert workspace.get_review(finance, case, restarted) == receipt


def test_cli_catalog_and_export_freshness():
    def cli(*args):
        return subprocess.run(
            [sys.executable, "-B", "-m", "semantic_pipeline_v2.workspace", *args],
            capture_output=True, text=True, check=True,
        ).stdout

    catalog = json.loads(cli())
    assert [entry["id"] for entry in catalog] == list(workspace.DATASETS)
    assert Path(cli("--check-export").strip()).is_file()
    assert json.loads(cli("--dataset", "banking", "--stage", "ingest")) == workspace.stage_view(
        workspace.load_workspace("financial-services"), "ingest"
    )


@contextmanager
def running_server(database=None, *, bootstrap=True, **server_options):
    with create_server(0, database, **server_options) as server:
        worker = Thread(target=server.serve_forever)
        worker.start()

        cookie = ""

        def call(method, path, body=None, headers=None, *, include_headers=False):
            client = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                headers = {
                    "Origin": f"http://127.0.0.1:{server.server_port}",
                    "Content-Type": "application/json", "Cookie": cookie, **(headers or {}),
                }
                client.request(method, path, json.dumps(body) if body is not None else None, headers)
                response = client.getresponse()
                data = response.read()
                if response.getheader("Content-Type", "").startswith("application/json"):
                    data = json.loads(data)
                if include_headers:
                    return response.status, data, response.getheaders()
                return response.status, data
            finally:
                client.close()

        try:
            if database is not None and bootstrap:
                _, _, headers = call("GET", "/", include_headers=True)
                cookie = "; ".join(value.split(";", 1)[0] for name, value in headers if name == "Set-Cookie")
            yield call
        finally:
            server.shutdown()
            worker.join()


def test_http_fails_closed_without_pg_and_checks_origin():
    with running_server() as call:
        status, payload = call("GET", "/api/workspace")
        assert status == 503 and "unavailable" in payload["error"]
        for path in ("/", "/index.html"):
            status, payload = call("GET", path)
            assert status == 200 and b'name="workspace-runtime" content="local"' in payload
        assert call("GET", "/../../demo_data/finance/sources.json")[0] == 404
        assert call("GET", "/api/workspace", headers={"Host": "example.invalid"})[0] == 403
        item = {"dataset": "financial-services", "mappingId": "financial-services:mapping:risk_rating",
                "decision": "keep_separate", "note": "Rehearsal", "evidenceRevision": "pending"}
        assert call("POST", "/api/review", item, {"Origin": "http://example.invalid"})[0] == 403
        assert call("POST", "/api/review", {"dataset": "financial-services"})[0] == 400
        assert call("POST", "/api/review", item)[0] == 503


def test_manufacturing_row_counts_and_finance_filled_from_documents(tmp_path):
    csv_dir = workspace.ROOT / workspace.FACTORY / "data/csv"
    absent = workspace.load_workspace("manufacturing", local_projections=False)["knowledge"]["structured"]
    info = absent["structuredImport"]
    assert info["status"] == "unavailable" and info["reason"] == "local data absent"
    assert info["tableCount"] == 25 and all(t["rowCount"] is None for t in info["tables"])
    # a CSV folder with matching headers counts rows; a bad header or a missing file stays unavailable
    columns = {}
    for field in absent["fields"]:
        table, column = field["name"].split("/", 1)
        columns.setdefault(table, []).append(column)
    for name, header in columns.items():
        with (tmp_path / f"{name}.csv").open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(header)
            writer.writerows([["x"] * len(header)] * 3)
    counted = workspace.workbook_import(absent, None, local=True, directory=tmp_path)
    assert counted["status"] == "available" and "reason" not in counted
    assert all(t["rowCount"] == 3 for t in counted["tables"])
    (tmp_path / "Person_Address.csv").write_text("wrong\n1\n")
    (tmp_path / "Sales_Store.csv").unlink()
    partial = workspace.workbook_import(absent, None, local=True, directory=tmp_path)
    assert partial["availableTableCount"] == 23 and partial["reason"] == "local data absent"
    if csv_dir.is_dir():     # git-ignored local data: the real files give real counts
        local = workspace.load_workspace("manufacturing")["knowledge"]["structured"]["structuredImport"]
        assert local["status"] == "available" and all(t["rowCount"] > 0 for t in local["tables"])
    # Output C tables are named, not counted, and never reported as workbook tables
    finance = workspace.load_workspace("financial-services", local_projections=False)
    filled = finance["sources"][0]["structuredImport"]["fromDocuments"]
    assert {t["name"] for t in filled} == {"STR_Transaction", "STR_AccountEntity"}
    assert not {t["name"] for t in filled} & {t["name"] for t in finance["sources"][0]["structuredImport"]["tables"]}
