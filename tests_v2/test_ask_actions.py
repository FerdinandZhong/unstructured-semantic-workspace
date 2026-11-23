"""Step 6, contract C3: write a table from Ask (plan T6, T7, T8, T9 no-write).

The local private PostgreSQL write is the default and always works. The Spark job for Iceberg is always generated and
shown; it is submitted only to a FAKE CDE transport here. No test contacts CDE, Polaris or a control plane.
"""
import json
from types import SimpleNamespace
from threading import RLock

import httpx
import pytest

from semantic_pipeline_v2 import ask_actions, iceberg_target, workspace_server
from semantic_pipeline_v2.answer_detail import SYSTEM, Details
from semantic_pipeline_v2.ask_actions import CAP, NOT_SUBMITTED, Writer
from semantic_pipeline_v2.iceberg_target import IcebergTarget
from semantic_pipeline_v2.metric_sql import Filter
from semantic_pipeline_v2.physical_mapping import PhysicalRows, build_rows, row_values, template_links
from semantic_pipeline_v2.workspace import load_workspace, review_database
from test_answer_detail import ADMIN, FIN, metric_fact, reply_of, scoped
from test_iceberg_target import SETTINGS, TOKEN, Cde
from test_interpreter import pg_bin
from test_physical_mapping import CHUNK, DATASET, FORM_1, needs_forms, read_form, server
from test_workspace import running_server

CREDENTIAL_WORDS = (TOKEN, "Bearer", "KEY-SECRET")


def ctx(pg, *, principal=ADMIN, workspace=None, details=None, iceberg=None, links=None):
    return SimpleNamespace(principal=principal, pg=pg, workspace=workspace, details=details or Details(),
                           connections=None, iceberg=iceberg or (lambda: None), jobs=iceberg_target.IcebergJobs(pg),
                           physical=PhysicalRows(pg), links=links, lock=RLock())


def no_credential(value):
    text = json.dumps(value) if not isinstance(value, str) else value
    return not any(word in text for word in CREDENTIAL_WORDS)


# ---------------------------------------------------------------- form rows (T6), no PDFs needed

@pytest.fixture
def form_pg(tmp_path):
    with review_database(tmp_path / "state", pg_bin()) as database:
        yield database


def form_ctx(pg, **kw):
    workspace = load_workspace(DATASET)
    return ctx(pg, principal={**ADMIN, "datasets": [DATASET]}, workspace=workspace,
               links=lambda source, *_: template_links(workspace, [CHUNK]), **kw)


def form_body(target="postgres"):
    return {"dataset": DATASET, "source": {"kind": "form", "sourceId": "s"}, "target": target}


def test_form_preview_equals_the_rows_written_and_the_rows_in_the_generated_spark_job(form_pg):
    c, writer = form_ctx(form_pg), Writer()
    status, preview = writer.preview(c, form_body("iceberg_cde"))
    assert status == 200 and preview["fingerprint"] and preview["approval"]
    [table] = preview["tables"]
    expected = {k: str(v) for k, v in row_values(c.workspace, build_rows(c.workspace, c.links("s"))[0]).items()}
    assert table["rowCount"] == 1 and {k: str(v) for k, v in table["sample"][0].items()} == expected
    spark = preview["spark"]
    assert spark["state"] == "generated" and "MERGE INTO lakehouse.demo_silver.ext_str_transaction" in spark["script"]
    assert spark["spec"]["spark"]["conf"]["cde.iceberg.enabled"] == "true"
    assert "cde-cli" in spark["command"] and "$CDE_ACCESS_KEY_ID" in spark["command"]
    assert "$POLARIS_OAUTH_CREDENTIAL" in spark["command"] and no_credential(spark)
    assert NOT_SUBMITTED in preview["warnings"]
    payload = json.loads(json.loads(spark["script"].split("PAYLOAD = json.loads(", 1)[1].split(")\n", 1)[0]))
    record = dict(zip([n.split()[0] for n in payload["tables"][0]["schema"].split(", ")], payload["tables"][0]["rows"][0]))
    assert {k: v for k, v in record.items() if not k.startswith("v2_") and v is not None} == \
        {k.lower(): v for k, v in expected.items()}
    status, done = writer.confirm(c, {"fingerprint": preview["fingerprint"], "approval": preview["approval"]})
    assert status == 200 and done["status"] == "written" and done["postgres"] == {"written": 1, "found": 1, "expected": 1}
    assert done["job"] is None and done["labels"] == ["Written to the local database: 1 rows", NOT_SUBMITTED]


def test_form_wrong_fingerprint_is_refused_the_approval_is_one_use_and_a_replay_inserts_nothing(form_pg):
    c, writer = form_ctx(form_pg), Writer()
    _, preview = writer.preview(c, form_body())
    assert writer.confirm(c, {"fingerprint": "0" * 64, "approval": preview["approval"]})[0] == 409
    _, preview = writer.preview(c, form_body())
    body = {"fingerprint": preview["fingerprint"], "approval": preview["approval"]}
    assert writer.confirm(c, body)[1]["postgres"]["written"] == 1
    assert writer.confirm(c, body)[0] == 409                          # one use
    _, again = writer.preview(c, form_body())
    status, replay = writer.confirm(c, {"fingerprint": again["fingerprint"], "approval": again["approval"]})
    assert status == 200 and replay["postgres"] == {"written": 0, "found": 1, "expected": 1}


def test_an_approval_expires_after_ten_minutes(form_pg):
    now = [1000.0]
    c, writer = form_ctx(form_pg), Writer(clock=lambda: now[0])
    _, preview = writer.preview(c, form_body())
    now[0] += 601
    assert writer.confirm(c, {"fingerprint": preview["fingerprint"], "approval": preview["approval"]})[0] == 409


def test_the_script_download_is_admin_only_and_bound_to_the_fingerprint(form_pg):
    c, writer = form_ctx(form_pg), Writer()
    _, preview = writer.preview(c, form_body())
    status, script, name = writer.script(c, preview["fingerprint"])
    assert status == 200 and script == preview["spark"]["script"] and name == "v2_str_rows.py"
    assert writer.script(c, "f" * 64)[0] == 404
    assert writer.script(ctx(form_pg, principal={**ADMIN, "role": "reader"}), preview["fingerprint"])[0] == 403
    assert writer.script(ctx(form_pg, principal={**ADMIN, "id": "other"}), preview["fingerprint"])[0] == 404


def test_a_reader_is_refused_preview_confirm_and_status(form_pg):
    c, writer = form_ctx(form_pg), Writer()
    c.principal = {**c.principal, "role": "reader"}
    assert writer.preview(c, form_body())[0] == 403
    assert writer.confirm(c, {"fingerprint": "x", "approval": "y"})[0] == 403
    assert writer.job_status(c, {"job": "v2-x"})[0] == 403


# ---------------------------------------------------------------- answer result (T7, T8)

def answer_ctx(pg, **kw):
    c = ctx(pg, workspace=scoped(FIN), **kw)
    fact = metric_fact(pg, FIN, "customers_on_business_date",
                       [Filter(field="BusinessDate", value="2026-11-06"), Filter(field="CustomerType", value="Corporate")])
    held = c.details.register(reply_of("How many corporate customers?", fact), FIN, ADMIN)
    return c, held["facts"][0]["detail"]["id"], fact


def answer_body(handle, table, target="postgres"):
    return {"dataset": FIN, "source": {"kind": "answer", "handle": handle, "question": "How many corporate customers?"},
            "target": target, "table": table}


def test_answer_preview_counts_the_facts_rows_states_the_cap_and_drops_system_columns(answer_pg):
    c, handle, fact = answer_ctx(answer_pg)
    status, preview = Writer().preview(c, answer_body(handle, "corp_t7"))
    assert status == 200
    [table] = preview["tables"]
    assert table["name"] == "v2_ask.v2_ask_corp_t7" and table["rowCount"] == fact["value"] == 12
    names = [col["name"] for col in table["columns"]]
    assert names[-3:] == ["v2_question", "v2_written_at", "v2_principal"]
    assert not {n.lower() for n in names} & SYSTEM and "customertype" in names
    assert len(table["sample"]) == 10 and {r["customertype"] for r in table["sample"]} == {"Corporate"}
    assert any("10,000" in w for w in preview["warnings"])
    assert "MERGE INTO lakehouse.demo_silver.v2_ask_corp_t7" in preview["spark"]["script"] and no_credential(preview)


def test_the_cap_limits_the_rows_and_says_so(answer_pg, monkeypatch):
    monkeypatch.setattr(ask_actions, "CAP", 5)
    c, handle, _ = answer_ctx(answer_pg)
    _, preview = Writer().preview(c, answer_body(handle, "corp_cap"))
    assert preview["tables"][0]["rowCount"] == 5 and any("12" in w and "5" in w for w in preview["warnings"])
    assert CAP == 10000


def test_answer_confirm_writes_locally_reads_back_and_a_replay_inserts_nothing(answer_pg):
    c, handle, _ = answer_ctx(answer_pg)
    writer = Writer()
    _, preview = writer.preview(c, answer_body(handle, "corp_write"))
    status, done = writer.confirm(c, {"fingerprint": preview["fingerprint"], "approval": preview["approval"]})
    assert status == 200 and done["postgres"] == {"written": 12, "found": 12, "expected": 12}
    assert done["labels"][0] == "Written to the local database: 12 rows"
    count = answer_pg.sql("SELECT count(*) FROM v2_ask.v2_ask_corp_write WHERE v2_principal = 'admin'"
                          " AND v2_question = 'How many corporate customers?';")
    assert count == "12"
    _, again = writer.preview(c, answer_body(handle, "corp_write"))
    assert writer.confirm(c, {"fingerprint": again["fingerprint"], "approval": again["approval"]})[1]["postgres"]["written"] == 0


def test_an_unknown_or_foreign_handle_is_refused(answer_pg):
    c, handle, _ = answer_ctx(answer_pg)
    assert Writer().preview(c, answer_body("nope", "x"))[0] == 404
    other = ctx(answer_pg, principal={**ADMIN, "id": "someone"}, workspace=c.workspace, details=c.details)
    assert Writer().preview(other, answer_body(handle, "x"))[0] == 404


def test_a_job_failure_keeps_the_local_write_and_reports_failed_with_the_job_name(answer_pg):
    cde = Cde(http=500)
    c, handle, _ = answer_ctx(answer_pg, iceberg=lambda: IcebergTarget(SETTINGS, TOKEN, transport=httpx.MockTransport(cde)))
    writer = Writer()
    _, preview = writer.preview(c, answer_body(handle, "corp_fail", "iceberg_cde"))
    assert preview["spark"]["state"] == "generated" and NOT_SUBMITTED not in preview["warnings"]
    status, done = writer.confirm(c, {"fingerprint": preview["fingerprint"], "approval": preview["approval"]})
    assert status == 200 and done["status"] == "failed" and done["postgres"]["written"] == 12
    job = done["job"]
    assert job["name"].startswith("v2-ask-rows-") and job["status"] == "failed" and "HTTP 500" in job["message"]
    assert job["name"] in done["labels"][1] and no_credential(done) and len(cde.calls) == 1
    assert answer_pg.sql("SELECT count(*) FROM v2_ask.v2_ask_corp_fail;") == "12"     # never rolled back


def test_a_submitted_job_reads_its_status_once(answer_pg):
    cde = Cde(state="succeeded")
    c, handle, _ = answer_ctx(answer_pg, iceberg=lambda: IcebergTarget(SETTINGS, TOKEN, transport=httpx.MockTransport(cde)))
    writer = Writer()
    _, preview = writer.preview(c, answer_body(handle, "corp_ok", "iceberg_cde"))
    _, done = writer.confirm(c, {"fingerprint": preview["fingerprint"], "approval": preview["approval"]})
    assert done["status"] == "submitted" and done["job"]["runId"] == 77 and len(cde.calls) == 4
    status, job = writer.job_status(c, {"job": done["job"]["name"]})
    assert status == 200 and job["status"] == "completed" and cde.calls[-1][0] == "GET"


# ---------------------------------------------------------------- routes: /api/ask never writes (T9)

def test_ask_routes_never_write_and_a_reader_gets_403_on_write_routes(tmp_path, monkeypatch):
    principal, options = server(tmp_path, monkeypatch)
    options["iceberg"] = lambda: None
    with review_database(tmp_path / "pg", pg_bin()) as database, running_server(database, **options) as call:
        status, out = call("POST", "/api/ask", {"dataset": DATASET, "text": "show the connections", "context": {}})
        assert status == 200 and out["tool"] == "connections" and out["result"]["targets"][0]["id"] == "postgres"
        status, out = call("POST", "/api/ask", {"dataset": DATASET, "text": "write this to Iceberg",
                                                "context": {"lastAnswer": None}})
        assert status == 200 and out["tool"] == "write" and "error" in out["result"]
        status, out = call("POST", "/api/ask", {"dataset": DATASET, "text": "What does facility mean?", "context": {}})
        assert status == 200 and out["tool"] == "semantic" and out["result"]["term"] == "facility"
        assert call("POST", "/api/ask/write/confirm", {"fingerprint": "x", "approval": "y"})[0] == 409
        principal["role"] = "reader"
        for path in ("/api/ask/write/preview", "/api/ask/write/confirm", "/api/ask/write/status"):
            assert call("POST", path, {"dataset": DATASET})[0] == 403
        assert call("GET", "/api/ask/write/script?fingerprint=" + "f" * 64)[0] == 403
        assert database.sql("SELECT count(*) FROM information_schema.schemata WHERE schema_name = 'v2_ask';") == "0"


@needs_forms
def test_ask_names_a_form_and_returns_a_preview_without_writing(tmp_path, monkeypatch):
    _, options = server(tmp_path, monkeypatch)
    options["iceberg"] = lambda: None
    with review_database(tmp_path / "pg", pg_bin()) as database, running_server(database, **options) as call:
        read_form(call, FORM_1)
        status, out = call("POST", "/api/ask", {"dataset": DATASET, "context": {"lastAnswer": None},
                                                "text": "load the suspicious transaction report from customer-form1 into the lakehouse"})
        assert status == 200 and out["tool"] == "write" and out["result"]["tables"][0]["rowCount"] >= 1
        assert out["result"]["spark"]["state"] == "generated"
        assert call("GET", "/api/physical-rows?dataset=" + DATASET + "&sourceId=" + FORM_1)[1]["rows"] == []
        preview = out["result"]
        status, done = call("POST", "/api/ask/write/confirm", {"fingerprint": preview["fingerprint"],
                                                               "approval": preview["approval"]})
        assert status == 200 and done["status"] == "written" and done["postgres"]["written"] >= 1
        status, script = call("GET", "/api/ask/write/script?fingerprint=" + preview["fingerprint"])
        assert status == 200 and b"MERGE INTO" in script
