"""Clarification hints, round 2 (24 November 2026): defects a live new-user walk found.

1. Two missing slots: the latest date with every value of the short second slot; the other dates in the note.
2. A write request that names a declared table offers the files with rows of that table.
3. The flat write clarification: one chip per file, recordings with approved values too, the files the request's words
   name first, never two chips with one label.
4. A plan that failed twice: a plain reason; the technical text stays in the trace; the measures tried as choices.
5. A write clarification carries a truthful `semantics` object (release present, or no release).
7. An account without the write right gets the writer's refusal at once: no clarification and no hints.
Also: the record clarification names the record kind when its name column is known.
Every model is a FAKE.
"""
import csv
import json
from collections import Counter
from types import SimpleNamespace

import semantic_pipeline_v2.answer_path as ap
from semantic_pipeline_v2 import ask_actions, ask_router, workspace_server
from semantic_pipeline_v2.workspace import load_workspace
from test_answer_plan import CASA, FIN, MFG, Script, plan, run
from test_answer_plan7 import Releases, visible_all
from test_ask_clarify_semantic import CONCEPTS, DS, OLE, PUSH, fake_req, finance, visible_links  # noqa: F401
from test_ask_history import served  # noqa: F401  (a fixture)
from test_i18n_server_text import dictionary, shape
from test_ask_router_sentences import Chat, model_write



# ---------------------------------------------------------------- 1. date and currency

def test_two_missing_slots_offer_the_latest_date_with_every_currency_by_row_count(answer_pg):
    result = run(answer_pg, FIN, "How much money moved through the accounts?", Script([plan(measure=CASA)]))
    with open("demo_data/finance/local/finance_workbook/CASA_Txn.csv", encoding="utf-8") as stream:
        counts = Counter(r["Currency"] for r in csv.DictReader(stream))
    order = sorted(counts, key=lambda c: (-counts[c], c))
    assert order == ["HKD", "EUR", "USD", "JPY", "SGD"]            # by row count; SGD and JPY tie at 54, then by name
    assert [h["label"] for h in result["hints"]] == [f"6 November 2026 · {c}" for c in order]
    assert result["hints"][-1]["text"] == "How much money moved through the accounts on 6 November 2026 for currency SGD?"
    assert "Other business dates: 5 November 2026 and 4 November 2026; type one to use it." in result["hintNote"]


# ---------------------------------------------------------------- 2. a named table wins over a concept match

NAMED = "Save the reports into the transactions table"


def test_a_request_that_names_a_declared_table_offers_the_files_with_rows_of_it(finance):  # noqa: F811
    workspace, principal, readable = finance
    found = ask_actions._candidates(fake_req(workspace, principal), principal, workspace, NAMED, readable)
    assert found["tables"][0]["name"] == "STR_Transaction"
    assert [f["id"] for f in found["tables"][0]["files"]] == OLE[2:]
    text = ask_router.clarification(found)
    assert text == ('Which files do you mean? The request names the STR_Transaction table. Files with rows for '
                    'STR_Transaction: customer-form3.pdf, customer-form4.pdf, customer-form5.pdf. '
                    'Name the files, or say "all of these".')
    hints = ask_actions._write_hints(found)["hints"]
    assert all(h["text"].endswith("to the STR_Transaction table") for h in hints)
    assert not any("m4a" in h["text"] for h in hints)


# ---------------------------------------------------------------- 3. the flat write clarification

FORMS = [{"id": f"{DS}:source:cleaned_pdf_{n}", "title": f"customer-form{n}_clean.pdf", "kind": "pdf"} for n in (1, 2, 3)] + [
    {"id": f"{DS}:source:cleaned_audio", "title": "customer-call.m4a", "kind": "audio"},
    {"id": f"{DS}:source:jade_call", "title": "jade_logistics_rm_call.mp3", "kind": "audio"}]
TWO = ["STR_Transaction", "STR_AccountEntity"]


def test_the_flat_clarification_puts_the_named_recording_first_and_one_chip_per_file():
    tables = {FORMS[0]["id"]: TWO, FORMS[1]["id"]: TWO, FORMS[2]["id"]: ["STR_Transaction"],
              FORMS[3]["id"]: [ask_actions.CALL_TABLE], FORMS[4]["id"]: []}
    out = ask_actions._flat_write_hints(FORMS, lambda f: tables[f["id"]], "Save the call")
    labels = [h["label"] for h in out["hints"]]
    assert labels[0] == "customer-call.m4a" and "jade_logistics_rm_call.mp3" not in labels     # no approved values
    assert len(labels) == len(set(labels)) == 4
    assert out["hints"][1] == {"label": "customer-form1.pdf", "detail": "Rows for STR_Transaction, STR_AccountEntity",
                               "text": "Write customer-form1.pdf to a table"}
    assert out["hints"][3]["text"] == "Write customer-form3.pdf to the STR_Transaction table"
    context = {"forms": FORMS}
    call = ask_router.route(out["hints"][0]["text"], context)
    assert call["tool"] == "write" and call["args"]["source"] == {"kind": "call", "sourceId": FORMS[3]["id"]}
    both = ask_router.route(out["hints"][1]["text"], context)
    assert both["tool"] == "write" and both["args"]["source"] == {"kind": "form", "sourceId": FORMS[0]["id"]}


# ---------------------------------------------------------------- 4. a plan that failed twice

def test_a_plan_invalid_twice_shows_a_plain_reason_and_the_measure_tried_as_a_choice(answer_pg):
    bad = plan(measure="metric/active_accounts", time=["D1"], filters=[{"field": "field/Accounts/CustomerType", "value": "Corporate"}])
    asked = "How many accounts of corporate customers were active on 6 November 2026?"
    result = run(answer_pg, FIN, asked, Script([bad, bad]))
    assert result["status"] == "refused" and result["refusalReason"] == "not planned"
    assert result["reason"] == ap.NOT_PLANNED and "filters[0]" in result["plannerError"]       # shown: the plain reason only
    assert result["hintKind"] == "choice"
    assert result["hints"] == [{"label": "Active accounts", "detail": result["hints"][0]["detail"],
                                "text": "What were the Active accounts?", "definition": result["hints"][0]["definition"]}]
    assert result["hints"][0]["definition"].endswith("metric/active_accounts")       # the choice carries its definition


def test_the_technical_planner_text_stays_in_the_trace(served):  # noqa: F811
    ask, chat, _ = served
    bad = plan(measure="metric/sales_ordered_units", filters=[{"field": "field/Production_Product/ProductCategoryID", "value": "1"}])
    chat.script = Script([bad, bad])
    out = ask("How many bikes did we sell?")
    assert out["result"]["reason"] == ap.NOT_PLANNED
    step = next(s for s in out["trace"] if s["key"] == "plan")
    assert "is not a field id" in step["detail"]["error"]


# ---------------------------------------------------------------- 5. semantics of a write clarification

def test_a_write_clarification_carries_the_published_release_or_says_there_is_none(finance, monkeypatch):  # noqa: F811
    monkeypatch.setenv("V2_ASK_CATALOG", "published")
    workspace, principal, readable = finance
    req = fake_req(workspace, principal)
    found = ask_actions._candidates(req, principal, workspace, PUSH, readable)
    req.server.releases = Releases([])
    with_release = ask_actions._clarify_semantics(req, workspace, principal, found)
    assert with_release["mode"] == "published" and with_release["release"] == {"version": 1, "catalogVersion": "cat-1"}
    assert [c["label"] for c in with_release["concepts"]] == ["Suspicious transaction"]
    assert with_release["concepts"][0]["matched"] and with_release["concepts"][0]["via"] in ("label", "synonym")
    req.server.releases = None
    none = ask_actions._clarify_semantics(req, workspace, principal, found)
    assert none["release"] is None


# ---------------------------------------------------------------- 7. no write right: the writer's refusal at once

def test_a_reader_asking_to_write_gets_the_writers_refusal_and_no_clarification(tmp_path, monkeypatch):
    from test_interpreter import pg_bin
    from test_physical_mapping import server
    from test_workspace import running_server
    from semantic_pipeline_v2.workspace import review_database
    _, options = server(tmp_path, monkeypatch, role="reader")
    links = visible_links(load_workspace(DS))
    monkeypatch.setattr(workspace_server.WorkspaceRequest, "catalog_rows", lambda self, ws, p: ("v1", [dict(c) for c in CONCEPTS], links))
    monkeypatch.setattr(workspace_server.WorkspaceRequest, "visible_links",
                        lambda self, ws, p, source=None: [k for k in links if source in (None, k["source_id"])])
    options.update(iceberg=lambda: None, interpreter=SimpleNamespace(chat=Chat(model_write(*OLE[2:], table="STR_Transaction"))))
    with review_database(tmp_path / "pg", pg_bin()) as database, running_server(database, **options) as call:
        for text, history in ((PUSH, None), ("all of these", [{"text": PUSH, "status": "clarify", "said": "Which files?"}])):
            body = {"dataset": DS, "text": text, "context": {}, **({"history": history} if history else {})}
            status, out = call("POST", "/api/ask", body)
            assert status == 403 and out["tool"] == "write", out
            assert out["result"] == {"error": ask_actions.WRITE_DENIED}, out


# ---------------------------------------------------------------- the record clarification

def test_a_value_recorded_per_row_names_the_record_kind(answer_pg):
    result = run(answer_pg, MFG, "What is the list price?", Script([plan(measure="field/Production_Product/ListPrice", aggregate="value")]))
    assert result["reason"] == "List price is recorded per row. Name the product you mean."


def test_new_fixed_sentences_have_chinese_entries():
    entries = {shape(k): v for k, v in dictionary().items()}
    for text in (ap.NOT_PLANNED, ap.NOTE_OTHER_DATES, ask_router.NAMED_TABLE):
        assert entries.get(shape(text), "").strip(), text
