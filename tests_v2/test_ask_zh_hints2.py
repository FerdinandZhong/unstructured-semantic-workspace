"""Clarification hints, round 3 (24 November 2026): three defects a live Chinese walk on manufacturing found.

1. "One total, not per group" kept every value of the grouping field as a filter (answer 0). The one-total text holds
   the measure and the conditions that are not the grouping field; periods read "in 2023" / "on 6 November 2026".
   Each per-value chip filters to its one value and is labelled with the field's own label.
2. A choice offered after a planning failure led to the same refusal. Its text is built fresh from the definition; a
   clicked definition that still cannot be planned never offers the same choice again.
3. A refusal named one record twice.
Every model is a FAKE; the SQL is real (the test database).
"""
import json

import semantic_pipeline_v2.answer_path as ap
from semantic_pipeline_v2.evidence_links import EntityKeys
from semantic_pipeline_v2.workspace import load_workspace
from test_answer_plan import ADMIN, FIN, MFG, Index, Script, plan, run

COUNT = "metric/sales_order_count"
GROUP = "field/Sales_SalesOrderHeader/TerritoryID"
EVERY = [{"field": GROUP, "value": str(n)} for n in range(1, 11)]


def ask(pg, question, chat, definition=None, dataset=MFG):
    ws = load_workspace(dataset)
    return ap.answer(question, ws, ADMIN(dataset), database=pg, chunks=Index({}), entity_keys=EntityKeys(ws), chat=chat,
                     definition=definition)


# ---------------------------------------------------------------- 1. one total, not per group

def test_one_total_drops_the_grouping_field_and_gives_the_sum(answer_pg):
    asked = "How many orders were placed in each sales region in 2023?"
    result = run(answer_pg, MFG, asked, Script([plan(measure=COUNT, time=["D1"], group_by=GROUP, filters=EVERY)]))
    assert result["status"] == "clarify", result.get("reason")
    total, *chips = result["hints"]
    assert total["label"] == ap.ONE_TOTAL
    assert total["text"] == "What was the Sales order count in 2023?", total["text"]
    assert "territory" not in total["text"].lower() and not any(f" {n}?" in total["text"] or f" {n} " in total["text"] for n in range(1, 11))
    assert chips and all(c["label"].startswith("Sales territory ID ") for c in chips), [c["label"] for c in chips]
    for chip in chips:
        value = chip["label"].rsplit(" ", 1)[1]
        assert chip["text"] == f"What was the Sales order count in 2023 for territory id {value}?", chip["text"]
    whole = run(answer_pg, MFG, total["text"], Script([plan(measure=COUNT, time=["D1"])]))
    four = run(answer_pg, MFG, "How many orders were placed in sales territory 4 in 2023?",
               Script([plan(measure=COUNT, time=["D1"], filters=[{"field": GROUP, "value": "4"}])]))
    assert whole["status"] == four["status"] == "answered", (whole.get("reason"), four.get("reason"))
    assert float(whole["conclusion"]) > float(four["conclusion"]) > 0, (whole["conclusion"], four["conclusion"])


def test_the_finance_one_total_reads_on_the_day(answer_pg):
    asked = "Which customer made the most transactions on 6 November 2026?"
    result = run(answer_pg, FIN, asked, Script([plan(measure="metric/casa_transaction_count", time=["D1"],
                                                     group_by="field/CASA_Txn/CustomerID")]))
    assert result["hints"][0]["text"] == "What was the CASA transaction count on 6 November 2026?", result["hints"][0]


# ---------------------------------------------------------------- 2. a choice after a planning failure moves forward

BAD = plan(measure="metric/sales_ordered_units", filters=[{"field": "field/Production_Product/ProductCategoryID", "value": "1"}])
UNITS = "metric/sales_ordered_units"


def test_the_first_click_after_a_planning_failure_moves_forward(answer_pg):
    first = ask(answer_pg, "How many bicycles have we sold?", Script([BAD, BAD]))
    assert first["refusalReason"] == "not planned"
    [choice] = first["hints"]
    assert choice["text"] == "What were the Sales ordered units?" and choice["definition"].endswith(UNITS), choice
    second = ask(answer_pg, choice["text"], Script([plan(measure=UNITS)]), choice["definition"])
    assert second["status"] == "clarify" and second["reason"].startswith("Sales ordered units needs period"), second["reason"]
    texts = [h["text"] for h in second["hints"]]
    assert texts == [f"What were the Sales ordered units in {h['label']}?" for h in second["hints"]], texts
    assert not any("Use " in t for t in texts)
    third = ask(answer_pg, texts[0], Script([plan(measure=UNITS, time=["D1"])]), second["hints"][0]["definition"])
    assert third["status"] == "answered", third.get("reason")


def test_a_chosen_definition_that_still_cannot_be_planned_offers_its_slots_not_itself(answer_pg):
    first = ask(answer_pg, "How many bicycles have we sold?", Script([BAD, BAD]))
    choice = first["hints"][0]
    again = ask(answer_pg, choice["text"], Script([BAD, BAD]), choice["definition"])
    assert again["status"] == "refused" and again["refusalReason"] == "not planned"
    assert again["reason"] == ap.NOT_PLANNED_CHOSEN.format(label="Sales ordered units"), again["reason"]
    assert choice["text"] not in [h["text"] for h in again.get("hints") or ()]
    assert all(h["text"].startswith("What were the Sales ordered units in ") for h in again["hints"]), again.get("hints")


# ---------------------------------------------------------------- 3. one record named once

def test_a_refusal_names_each_record_once(answer_pg):
    asked = "How many orders did the dealer Lakeside Cycle Hub place?"
    result = run(answer_pg, MFG, asked, Script([plan(measure=COUNT, parties=["Lakeside Cycle Hub", "Lakeside Cycle Hub"])]))
    assert result["refusalReason"] == "unknown party", result.get("reason")
    assert result["reason"].count("Lakeside Cycle Hub") == 1, result["reason"]
