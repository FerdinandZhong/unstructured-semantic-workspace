"""Step 6 progress panel and table snapshot (22 November 2026), written before the code.

The trace is recorded by the server as the stages run; it is an observer (the reply minus `trace` is byte-identical).
An in-flight trace is readable by the same account, session and dataset only, and only shortly after it ends.
After a confirmed write, the reply carries a snapshot read back from the written table itself.
"""
import json

import pytest

from semantic_pipeline_v2 import ask_actions, ask_trace
from semantic_pipeline_v2.ask_trace import ANSWER, Progress, Trace, active, answer_steps
from semantic_pipeline_v2.workspace import review_database
from test_answer_plan import FIN, MFG, Script, plan, run
from test_ask_actions import ADMIN, answer_body, answer_ctx, form_body, form_ctx, form_pg  # noqa: F401
from test_ask_actions import Writer
from test_interpreter import pg_bin
from test_physical_mapping import DATASET, server
from test_workspace import running_server

KEYS = [k for k, _ in ANSWER]
CASA = "metric/casa_transaction_value"
SGD = "What was the CASA transaction value in SGD on 5 November 2026?"
ID = "ab" * 16


def answered():
    return Script([plan(measure=CASA, filters=[{"field": "field/CASA_Txn/Currency", "value": "SGD"}], time=["D1"])])


def refused():
    bad = plan(measure="metric/deposit_balance")
    return Script([bad, bad])


def clarify():
    return Script([plan(measure=CASA, time=["D1"], filters=[{"field": "field/CASA_Txn/Currency", "value": "Singapore dollars"}])])


def traced(pg, dataset, question, chat, docs=None, principal=None, trace=None):
    trace = trace or Trace(ANSWER)
    trace.start("route")
    with active(trace):
        reply = run(pg, dataset, question, chat, docs, principal)
    return reply, answer_steps(trace, reply)


def timed(steps):
    starts = [s["startMs"] for s in steps if s["startMs"] is not None]
    return starts == sorted(starts) and all(s["ms"] is None or s["ms"] >= 0 for s in steps)


@pytest.mark.parametrize("make", [answered, refused, clarify])
def test_the_trace_is_an_observer_the_reply_is_byte_identical(answer_pg, make):
    bare = run(answer_pg, FIN, SGD, make())
    reply, steps = traced(answer_pg, FIN, SGD, make())
    assert json.dumps(bare) == json.dumps(reply)
    assert [s["key"] for s in steps] == KEYS and timed(steps)


def test_an_answer_trace_has_every_step_done_with_its_detail(answer_pg):
    reply, steps = traced(answer_pg, FIN, SGD, answered())
    assert reply["status"] == "answered"
    by = {s["key"]: s for s in steps}
    assert all(s["state"] == "done" for s in steps), [(s["key"], s["state"]) for s in steps]
    assert by["plan"]["detail"]["measure"] == CASA and by["plan"]["detail"]["modelCalls"] == 1
    assert by["understand"]["detail"]["periods"] == ["5 November 2026"]
    [fact] = by["query"]["detail"]["facts"]
    assert fact["sql"] and fact["value"] == 356152.44
    assert by["decide"]["detail"]["status"] == "answered" and by["decide"]["detail"]["conclusion"] == 356152.44


def test_a_refusal_fails_the_step_that_refused_and_skips_the_rest(answer_pg):
    reply, steps = traced(answer_pg, FIN, SGD, refused())
    by = {s["key"]: s for s in steps}
    assert reply["status"] == "refused"
    assert by["plan"]["state"] == "failed" and by["plan"]["detail"]["modelCalls"] == 2
    assert by["plan"]["detail"]["stopped"]["guard"] == "not planned"
    assert [by[k]["state"] for k in ("route", "understand", "query", "read", "decide")] == ["done", "done", "skipped", "skipped", "skipped"]


def test_a_clarification_stops_at_the_query_step(answer_pg):
    reply, steps = traced(answer_pg, FIN, SGD, clarify())
    by = {s["key"]: s for s in steps}
    assert reply["status"] == "clarify"
    assert by["query"]["state"] == "done" and by["query"]["detail"]["stopped"]["status"] == "clarify"
    assert by["read"]["state"] == "skipped" and by["decide"]["state"] == "skipped"


def test_progress_is_live_while_the_model_runs_and_bound_to_its_owner():
    now = [100.0]
    progress, trace = Progress(), Trace(ANSWER, clock=lambda: now[0])
    owner = Progress.owner({"id": "admin"}, "session-1", FIN)
    progress.add(ID, owner, trace)
    trace.start("route")
    trace.start("understand")
    trace.start("plan")
    live = progress.read(ID, owner)
    assert live["done"] is False and [s["state"] for s in live["steps"]] == ["done", "done", "running", "waiting", "waiting", "waiting"]
    assert all(s["detail"] == {} for s in live["steps"])
    assert progress.read(ID, Progress.owner({"id": "reader"}, "session-1", FIN)) is None       # another account
    assert progress.read(ID, Progress.owner({"id": "admin"}, "session-2", FIN)) is None        # another session
    assert progress.read(ID, Progress.owner({"id": "admin"}, "session-1", MFG)) is None        # another dataset
    progress.add(ID, Progress.owner({"id": "reader"}, "s", FIN), Trace(ANSWER))                # never taken over
    assert progress.read(ID, owner) is not None
    trace.end()
    now[0] += ask_trace.RETAIN_SECONDS - 1
    assert progress.read(ID, owner)["done"] is True
    now[0] += 2
    assert progress.read(ID, owner) is None


def test_the_live_state_is_the_real_stage_seen_from_inside_the_model_call(answer_pg):
    progress, trace = Progress(), Trace(ANSWER)
    owner = Progress.owner({"id": "a"}, "", FIN)
    progress.add(ID, owner, trace)
    seen = []

    class Peek(Script):
        def complete(self, system, user):
            seen.append([s["state"] for s in progress.read(ID, owner)["steps"]])
            return super().complete(system, user)
    traced(answer_pg, FIN, SGD, Peek(answered().plans), trace=trace)
    assert seen == [["done", "done", "running", "waiting", "waiting", "waiting"]]


def test_a_restricted_readers_trace_names_no_withheld_source(answer_pg):
    need = {"need": "the promised delivery date", "type": "date", "party": None, "required": True}
    docs = {"schedule": "The promised delivery date for order 51131 is 2024-06-10.",
            "amendment": "The promised delivery date for order 51131 changes from 2024-06-10 to 2024-06-17.",
            "memo": "The warehouse closes at 18:00 on Fridays."}
    found = {"found": [{"need": 1, "clause": 1, "value": "2024-06-10"}], "supporting": [], "summary": ""}
    reader = {"id": "r", "role": "reader", "datasets": [MFG], "withheld": ["amendment"]}
    reply, steps = traced(answer_pg, MFG, "What promised delivery date applies to order 51131?",
                          Script([plan(evidence=[need], answer="date")], [found]), docs, principal=reader)
    assert reply["status"] == "refused"
    text = json.dumps(steps)
    for word in ("amendment", "2024-06-17"):
        assert word not in json.dumps(reply) and word not in text


# ---------------------------------------------------------------- writes: trace and snapshot

def test_a_form_write_preview_and_confirm_have_ordered_traces_and_a_snapshot_of_the_table(form_pg):
    c, writer = form_ctx(form_pg), Writer()
    status, preview = ask_actions.traced_write(writer, c, "preview", form_body())
    assert status == 200 and [s["key"] for s in preview["trace"]] == ["preview", "spark", "local", "iceberg"]
    # nothing will be submitted (no live connection): known at the preview, so that step is skipped there already
    assert [s["state"] for s in preview["trace"]] == ["done", "done", "waiting", "skipped"] and timed(preview["trace"])
    assert preview["trace"][3]["detail"]["text"] == ask_actions.NOT_SUBMITTED
    assert preview["trace"][0]["detail"]["tables"][0]["rowCount"] == 1
    status, done = ask_actions.traced_write(writer, c, "confirm", {"fingerprint": preview["fingerprint"], "approval": preview["approval"]})
    assert status == 200 and [s["key"] for s in done["trace"]] == ["local", "iceberg"]
    assert [s["state"] for s in done["trace"]] == ["done", "skipped"] and timed(done["trace"])
    assert done["trace"][1]["detail"]["text"] == ask_actions.NOT_SUBMITTED
    [snap] = done["snapshots"]
    table = preview["tables"][0]["name"]
    assert snap["target"] == "postgres" and snap["table"] == table and snap["added"] == 1 and snap["readAt"]
    assert snap["total"] == int(form_pg.sql(f"SELECT count(*) FROM {table};"))
    stored = json.loads(form_pg.sql(f"SELECT COALESCE(json_agg(t), '[]') FROM {table} t;"))
    assert snap["columns"] and not set(snap["columns"]) & {"sourcerevision", "transactionseq"}      # system columns hidden
    assert [{k: str(v) for k, v in r.items()} for r in snap["rows"]] == [{k: str(r[k]) for k in snap["columns"]} for r in stored]
    status, again = ask_actions.traced_write(writer, c, "preview", form_body())
    status, replay = ask_actions.traced_write(writer, c, "confirm", {"fingerprint": again["fingerprint"], "approval": again["approval"]})
    assert replay["snapshots"][0]["added"] == 0 and replay["snapshots"][0]["total"] == snap["total"]


def test_an_answer_write_snapshot_is_read_from_the_table_with_system_columns_hidden(answer_pg):
    c, handle, _ = answer_ctx(answer_pg)
    writer = Writer()
    _, preview = ask_actions.traced_write(writer, c, "preview", answer_body(handle, "corp_snap"))
    _, done = ask_actions.traced_write(writer, c, "confirm", {"fingerprint": preview["fingerprint"], "approval": preview["approval"]})
    [snap] = done["snapshots"]
    assert snap["table"] == "v2_ask.v2_ask_corp_snap" and snap["total"] == 12 and snap["added"] == 12
    assert len(snap["rows"]) == 12 and not any(k.startswith("v2_") for r in snap["rows"] for k in r)
    assert snap["columns"] == list(snap["rows"][0])
    count = answer_pg.sql("SELECT count(*) FROM v2_ask.v2_ask_corp_snap;")
    assert snap["total"] == int(count)


def test_a_failed_write_step_is_failed_and_a_reader_gets_403_without_a_snapshot(form_pg):
    c, writer = form_ctx(form_pg), Writer()
    status, out = ask_actions.traced_write(writer, c, "confirm", {"fingerprint": "x", "approval": "y"})
    assert status == 409 and out["trace"][0]["state"] == "failed" and "snapshots" not in out
    c.principal = {**c.principal, "role": "reader"}
    status, out = ask_actions.traced_write(writer, c, "confirm", {"fingerprint": "x", "approval": "y"})
    assert status == 403 and "snapshots" not in out


# ---------------------------------------------------------------- routes

def test_ask_and_answer_routes_return_a_trace_and_progress_is_bound_to_the_session(tmp_path, monkeypatch):
    principal, options = server(tmp_path, monkeypatch)
    options["iceberg"] = lambda: None
    with review_database(tmp_path / "pg", pg_bin()) as database, running_server(database, **options) as call:
        status, out = call("POST", "/api/ask", {"dataset": DATASET, "text": "show the connections", "requestId": ID})
        assert status == 200 and [s["key"] for s in out["trace"]] == ["route", "connections"]
        assert out["trace"][0]["detail"]["tool"] == "connections" and out["trace"][0]["detail"]["why"]
        status, live = call("GET", f"/api/ask/progress?id={ID}&dataset={DATASET}")
        assert status == 200 and live["done"] is True and [s["state"] for s in live["steps"]] == ["done", "done"]
        assert call("GET", f"/api/ask/progress?id={ID}&dataset={DATASET}", headers={"Cookie": ""})[0] == 404
        assert call("GET", f"/api/ask/progress?id={ID}&dataset=other")[0] == 404
        assert call("GET", f"/api/ask/progress?id={'cd' * 16}&dataset={DATASET}")[0] == 404
        status, out = call("POST", "/api/answer", {"dataset": DATASET, "question": "How many customers?"})
        assert status == 200 and [s["key"] for s in out["trace"]] == KEYS
        assert out["status"] == "refused" and out["trace"][1]["state"] == "failed"      # no model: refused at once
        status, out = call("POST", "/api/ask", {"dataset": DATASET, "text": "write this to Iceberg", "context": {}})
        assert status == 200 and out["trace"][0]["detail"]["tool"] == "write"
        monkeypatch.setattr(ask_trace, "RETAIN_SECONDS", -1)
        assert call("GET", f"/api/ask/progress?id={ID}&dataset={DATASET}")[0] == 404
        principal["role"] = "reader"
        status, out = call("POST", "/api/ask/write/confirm", {"fingerprint": "x", "approval": "y"})
        assert status == 403 and "snapshots" not in out


def test_a_refused_preview_fails_the_preview_step_not_the_next_one():
    """A document with no table rows stops while the preview is built; the Spark step never ran."""
    from semantic_pipeline_v2 import ask_trace
    from semantic_pipeline_v2.ask_actions import write_steps
    trace = ask_trace.Trace(ask_trace.PREVIEW)
    trace.start("route")
    with ask_trace.active(trace):
        ask_trace.mark("preview")                   # the plan refuses here
    steps = {s["key"]: s for s in write_steps(trace, "preview", False, {"error": "no rows"})}
    assert steps["preview"]["state"] == "failed" and steps["preview"]["detail"]["error"] == "no rows"
    assert steps["spark"]["state"] == "skipped" and "error" not in steps["spark"]["detail"]
    nothing = ask_trace.Trace(ask_trace.PREVIEW)    # stopped at routing: the first step not run carries the reason
    nothing.start("route")
    steps = {s["key"]: s for s in write_steps(nothing, "preview", False, {"error": "nothing to write"})}
    assert steps["route"]["state"] == "done" and steps["preview"]["state"] == "failed"
