"""A clarification on the Ask page carries hints the person can click (24 November 2026).

The person rarely knows what a clarification wants. Code (no model) adds `hints` [{label, text, detail?}] and an
optional `hintNote`, from the semantic layer and the data the answer path already reads:
- a choice among things the semantic layer knows (definitions of one reviewed name, records with one name, files to
  write): one hint per candidate (`hintKind` "choice");
- a required slot of one chosen definition with a short set of values (business dates, currencies, years): completed
  questions (`hintKind` "question"); a large set (products) gets only `hintNote` with a few examples;
- no real choice: no hints.
Every hint text is a standalone question or request; a clicked hint skips the rewrite model. Every model is a FAKE.
"""
import json
from types import SimpleNamespace

import pytest

import semantic_pipeline_v2.answer_path as ap
from semantic_pipeline_v2 import ask_actions, ask_router, identifier_mask, ui_lang
from semantic_pipeline_v2.evidence_links import EntityKeys
from semantic_pipeline_v2.workspace import load_workspace
from test_answer_plan import ADMIN, FIN, MFG, CASA, Script, plan, run
from test_answer_plan7 import CARD_AMOUNT, CASA_AMOUNT, FULL, Releases, concept, visible_all
from test_answer_plan7 import ASKED as TAKINGS_ASKED
from test_ask_history import served  # noqa: F401  (a fixture)
from test_i18n_server_text import dictionary, shape

DATES = ("4 November 2026", "5 November 2026", "6 November 2026")
CURRENCIES = ("EUR", "HKD", "JPY", "SGD", "USD")


def shape_ok(result, kind):
    assert result["status"] == "clarify", result.get("reason")
    assert 0 < len(result["hints"]) <= ap.MAX_HINTS and result["hintKind"] == kind
    for hint in result["hints"]:
        assert set(hint) <= {"label", "text", "detail", "definition"} and hint["label"] and hint["text"]


# ---------------------------------------------------------------- a required slot with a short set of values

def test_a_missing_business_date_and_currency_offer_completed_questions_with_real_values(answer_pg):
    result = run(answer_pg, FIN, "What was the CASA transaction value?", Script([plan(measure=CASA)]))
    shape_ok(result, "question")
    assert result["reason"].startswith("CASA transaction value needs business date and currency")   # the label, not the code
    assert len(result["hints"]) == 5                       # the latest date with each of the five currencies
    first = result["hints"][0]
    assert first["text"].startswith("What was the CASA transaction value on 6 November 2026 for currency ")   # latest first
    assert first["text"].endswith("?") and first["label"].startswith("6 November 2026 · ")
    for hint in result["hints"]:
        assert any(d in hint["text"] for d in DATES) and any(c in hint["text"] for c in CURRENCIES)
    for value in DATES + CURRENCIES:
        assert value in result["hintNote"]


def test_a_clicked_date_and_currency_hint_is_answered(answer_pg):
    hint = run(answer_pg, FIN, "What was the CASA transaction value?", Script([plan(measure=CASA)]))["hints"][0]
    currency = hint["label"].split(" · ")[1]
    again = run(answer_pg, FIN, hint["text"], Script([plan(measure=CASA, time=["D1"],
                                                          filters=[{"field": "field/CASA_Txn/Currency", "value": currency}])]))
    assert again["status"] == "answered" and again["facts"][0]["filters"]["BusinessDate"] == "2026-11-06"


def test_a_metric_that_needs_only_a_business_date_offers_the_dates(answer_pg):
    asked = "How many customers were there?"
    result = run(answer_pg, FIN, asked, Script([plan(measure="metric/customers_on_business_date")]))
    shape_ok(result, "question")
    assert [h["text"] for h in result["hints"]] == [f"How many customers were there on {d}?" for d in reversed(DATES)]
    again = run(answer_pg, FIN, result["hints"][0]["text"], Script([plan(measure="metric/customers_on_business_date", time=["D1"])]))
    assert again["status"] == "answered"


def test_a_missing_period_offers_the_years_the_data_holds(answer_pg):
    result = run(answer_pg, MFG, "What was the sales line revenue?", Script([plan(measure="metric/sales_line_revenue")]))
    shape_ok(result, "question")
    assert all(h["text"].startswith("What was the sales line revenue in ") for h in result["hints"])
    assert "Dates in the data run from " in result["hintNote"]
    year = result["hints"][0]["label"]
    again = run(answer_pg, MFG, result["hints"][0]["text"], Script([plan(measure="metric/sales_line_revenue", time=["D1"])]))
    assert again["status"] == "answered" and year in json.dumps(again["facts"][0]["filters"])


def test_an_unknown_value_of_a_short_list_offers_the_known_values(answer_pg):
    asked = "What was the CASA transaction value in Singapore dollars on 5 November 2026?"
    result = run(answer_pg, FIN, asked, Script([plan(measure=CASA, time=["D1"],
                                                    filters=[{"field": "field/CASA_Txn/Currency", "value": "Singapore dollars"}])]))
    shape_ok(result, "question")
    assert {h["label"] for h in result["hints"]} <= set(CURRENCIES)
    assert "What was the CASA transaction value in SGD on 5 November 2026?" in [h["text"] for h in result["hints"]]


# ---------------------------------------------------------------- a large set: a note, no chips

def test_a_missing_product_gets_a_note_with_examples_and_no_chips(answer_pg):
    result = run(answer_pg, MFG, "How many units are in stock?", Script([plan(measure="metric/inventory_units")]))
    assert result["status"] == "clarify" and "needs product" in result["reason"]
    assert not result.get("hints")
    assert result["hintNote"].startswith("Name one product, for example: ") and "The data holds " in result["hintNote"]


def test_a_value_recorded_per_row_gets_a_note_with_record_names(answer_pg):
    result = run(answer_pg, MFG, "What is the list price?", Script([plan(measure="field/Production_Product/ListPrice", aggregate="value")]))
    assert result["status"] == "clarify" and "recorded per row" in result["reason"]
    assert not result.get("hints") and result["hintNote"].startswith("Name one product, for example: ")


# ---------------------------------------------------------------- a choice the semantic layer knows

def test_two_definitions_of_one_reviewed_name_are_offered_with_their_meaning(answer_pg, monkeypatch):
    monkeypatch.setenv("V2_ASK_CATALOG", "published")
    two = concept("takings", "Takings", ["takings"], [(CASA_AMOUNT, "exactMatch"), (CARD_AMOUNT, "exactMatch")])
    catalog = ap.published_catalog(Releases([two]), FIN, visible_all)
    ws = load_workspace(FIN)
    ask = lambda q, chat: ap.answer(q, ws, ADMIN(FIN), database=answer_pg, chunks=None, entity_keys=EntityKeys(ws),  # noqa: E731
                                    chat=chat, catalog=catalog)
    result = ask(TAKINGS_ASKED, Script([plan(measure=CASA_AMOUNT, aggregate="sum")]))
    shape_ok(result, "choice")
    assert len(result["hints"]) == 2 and all(h.get("detail") is not None for h in result["hints"])
    assert len({h["label"] for h in result["hints"]}) == 2
    assert all("takings" not in h["text"].lower() for h in result["hints"])
    casa = next(h for h in result["hints"] if "CASA" in h["label"] or "current" in h["label"].lower())
    again = ask(casa["text"], Script([plan(measure=CASA_AMOUNT, aggregate="sum", time=[])]))
    assert again["status"] != "clarify" or "more than one definition" not in again["reason"], again["reason"]


def test_records_with_one_name_are_offered_one_by_one_by_their_key(answer_pg):
    ws = load_workspace(MFG)
    asked = "How many orders did Friendly Bike Shop place?"
    ctx = ap._Ctx(asked, ws, ADMIN(MFG), answer_pg, None, None, EntityKeys(ws), None, None, None)
    found, _ = ap._entities(ctx)
    [named] = [e for e in found if e.get("ambiguous")]
    result = run(answer_pg, MFG, asked, Script([plan(measure="metric/sales_order_count", entities=[f"E{found.index(named) + 1}"])]))
    shape_ok(result, "choice")
    assert len(result["hints"]) == len(named["ambiguous"])
    for hint, key in zip(result["hints"], named["ambiguous"]):
        assert hint["label"].startswith("Friendly Bike Shop") and key in hint["text"]
        again = ap._Ctx(hint["text"], ws, ADMIN(MFG), answer_pg, None, None, EntityKeys(ws), None, None, None)
        entities, _ = ap._entities(again)
        assert not any(e.get("ambiguous") for e in entities)
        assert any(e["value"] == key and e["table"] == "Sales_Store" for e in entities), entities


def test_a_value_per_group_offers_one_total_and_the_values_of_a_short_group(answer_pg):
    asked = "What was the CASA transaction value on 5 November 2026 per channel?"
    result = run(answer_pg, FIN, asked, Script([plan(measure=CASA, time=["D1"], group_by="field/CASA_Txn/Channel",
                                                     filters=[{"field": "field/CASA_Txn/Currency", "value": "SGD"}])]))
    shape_ok(result, "question")
    total = result["hints"][0]
    assert total["label"] == ap.ONE_TOTAL
    assert total["text"] == "What was the CASA transaction value on 5 November 2026 for currency SGD?"
    assert all(h["text"].startswith(total["text"][:-1] + " for channel ") for h in result["hints"][1:])
    assert len(result["hints"]) >= 2
    again = run(answer_pg, FIN, total["text"], Script([plan(measure=CASA, time=["D1"],
                                                           filters=[{"field": "field/CASA_Txn/Currency", "value": "SGD"}])]))
    assert again["status"] == "answered"


def test_recorded_and_document_figures_offer_the_two_questions():
    ctx = SimpleNamespace(question="What was the limit?", offers=None)
    out = ap._hints(ctx, "figures", {})
    assert out["hintKind"] == "question"
    assert [h["label"] for h in out["hints"]] == [ap.RECORDED, ap.DOCUMENT]
    assert [h["text"] for h in out["hints"]] == ["What was the limit? Give the recorded figure.",
                                                 "What was the limit? Give the value the document states."]


def test_no_real_choice_gives_no_hints(answer_pg):
    unparsed = run(answer_pg, MFG, "How many units were ordered last year?", Script([plan(measure="metric/sales_ordered_units")]))
    assert unparsed["status"] == "clarify" and "hints" not in unparsed


def test_a_failure_while_building_hints_keeps_the_clarification(answer_pg, monkeypatch):
    def broken(*_):
        raise RuntimeError("no hints today")
    monkeypatch.setattr(ap, "_hints", broken)
    result = run(answer_pg, FIN, "What was the CASA transaction value?", Script([plan(measure=CASA)]))
    assert result["status"] == "clarify" and "hints" not in result and "_cause" not in result


# ---------------------------------------------------------------- write clarifications

TITLES = ["customer-form3.pdf", "customer-form4.pdf", "customer-form5.pdf"]
FORMS = [{"id": f"fs:source:ole{n}", "title": t, "kind": "pdf"} for n, t in enumerate(TITLES, 3)]


def test_a_semantic_write_clarification_offers_each_file_and_all_of_them():
    found = {"words": "STR transactions", "concepts": ["Suspicious transaction"], "files": FORMS,
             "tables": [{"name": "STR_Transaction", "files": FORMS}]}
    out = ask_actions._write_hints(found)
    assert out["hintKind"] == "choice"
    assert out["hints"][0] == {"label": "All of these (3 files)",
                               "text": "Write customer-form3.pdf, customer-form4.pdf and customer-form5.pdf to the STR_Transaction table"}
    assert out["hints"][1] == {"label": "customer-form3.pdf", "detail": "Rows for STR_Transaction",
                               "text": "Write customer-form3.pdf to the STR_Transaction table"}
    context = {"forms": FORMS}
    every = ask_router.route(out["hints"][0]["text"], context)
    assert every["tool"] == "write" and every["args"]["source"]["sourceIds"] == [f["id"] for f in FORMS]
    assert every["args"]["source"]["tableLike"] == "str_transaction"
    one = ask_router.route(out["hints"][1]["text"], context)
    assert one["tool"] == "write" and one["args"]["source"]["sourceId"] == FORMS[0]["id"] and "sourceIds" not in one["args"]["source"]


def test_the_flat_write_clarification_offers_the_files_with_rows_of_a_declared_table():
    rows = {FORMS[0]["id"]: ["STR_Transaction"], FORMS[1]["id"]: []}
    out = ask_actions._flat_write_hints(FORMS, lambda form: rows.get(form["id"], []))
    assert out["hints"] == [{"label": "customer-form3.pdf", "detail": "Rows for STR_Transaction",
                             "text": "Write customer-form3.pdf to the STR_Transaction table"}]
    assert ask_actions._flat_write_hints(FORMS, lambda form: []) == {}       # no file has rows: no forced hint


# ---------------------------------------------------------------- masking

VALUES = {"account": {"4111111111111111"}, "customer": {"CUST00004"}, "manager": {"RM001"}}


def test_a_hint_that_masking_would_change_is_dropped_for_a_reader_and_kept_for_an_admin():
    result = {"status": "clarify", "hintKind": "question", "hintNote": "Customers in the data: CUST00004 and CUST00005.",
              "hints": [{"label": "CUST00004", "text": "What was the value for customer CUST00004?"},
                        {"label": "One total", "text": "What was the value?"}]}
    reader = SimpleNamespace(masked=lambda value: identifier_mask.mask(value, VALUES))
    shown = ask_actions._shown_hints(reader, json.loads(json.dumps(result)))
    assert shown["hints"] == [{"label": "One total", "text": "What was the value?"}] and "hintNote" not in shown
    admin = SimpleNamespace(masked=lambda value: value)
    assert ask_actions._shown_hints(admin, json.loads(json.dumps(result))) == result
    only = {"status": "clarify", "hintKind": "choice", "hints": [result["hints"][0]]}
    assert ask_actions._shown_hints(reader, only) == {"status": "clarify"}


# ---------------------------------------------------------------- a clicked hint through /api/ask

def test_a_clicked_hint_is_read_as_sent_without_the_rewrite_model(served):  # noqa: F811
    ask, chat, _ = served
    history = [{"text": "What was the sales line revenue?", "status": "clarify", "said": "sales line revenue needs period"}]
    chat.script = Script([plan(measure="metric/sales_line_revenue", time=["D1"])])
    out = ask("What was the sales line revenue in 2024?", history=history, hint=True)
    assert not chat.of(ui_lang.STANDALONE_SYSTEM)                       # no rewrite call
    assert out["tool"] == "answer" and out["result"]["status"] == "answered", out["result"].get("reason")


# ---------------------------------------------------------------- Chinese

def test_every_fixed_hint_word_has_a_chinese_entry():
    entries = {shape(k): v for k, v in dictionary().items()}
    for text in (ap.ONE_TOTAL, ap.RECORDED, ap.DOCUMENT, ap.NOTE_DATES, ap.NOTE_VALUES, ap.NOTE_RANGE, ap.NOTE_EXAMPLES,
                 ask_actions.ALL_FILES, ask_actions.ROWS_FOR):
        assert entries.get(shape(text), "").strip(), text
