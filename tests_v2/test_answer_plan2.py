"""Answer path round 2 (2026-11-22): periods through one parser with a data-range check, tolerant plan validation with
named repairs, field aggregates (row counts, event-table ranges, comparison filters, joined filters), the party tie by
default, the declared `scope_required` policy and the configurable plan/read model. Each test states one general
mechanism with scripted model replies; no exam question is encoded."""
import csv
import json

import pytest

import semantic_pipeline_v2.answer_path as ap
from test_answer_plan import FIN, MFG, Script, plan, run

FINANCE = "demo_data/finance/local/finance_workbook/"


def rows(table):
    with open(FINANCE + table + ".csv", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


# ---------------------------------------------------------------- periods

def test_a_month_resolves_to_a_range_on_the_time_field(answer_pg):
    chat = Script([plan(measure="metric/sales_ordered_units", entities=["E1"], time=["D1"])])
    result = run(answer_pg, MFG, "How many units did customer 29641 order in August 2024?", chat)
    assert result["status"] == "answered", result["reason"]
    assert result["facts"][0]["filters"]["OrderDate"] == {"from": "2024-08-01", "to": "2024-08-31"}


@pytest.mark.parametrize("dataset, question, measure", [
    (MFG, "How many sales orders were placed in the first quarter of 2026?", "metric/sales_order_count"),
    (FIN, "How many accounts were active at the end of October 2026?", "metric/active_accounts"),
])
def test_a_period_wholly_outside_the_data_is_refused_with_the_data_range(answer_pg, dataset, question, measure):
    result = run(answer_pg, dataset, question, Script([plan(measure=measure, time=["D1"])]))
    assert result["status"] == "refused" and result["refusalReason"] == "period outside the data", result["reason"]
    assert "holds" in result["reason"] and not result["facts"] and result["conclusion"] is None


def test_date_words_that_only_describe_a_document_never_block_an_answer(answer_pg):
    need = {"need": "units the customer must take", "type": "number", "party": None, "required": True}
    docs = {"amendment": "The buyer commits to take at least 40 units across all products during 2024."}
    chat = Script([plan(evidence=[need])], [{"found": [{"need": 1, "clause": 1, "value": "40 units"}], "supporting": [], "summary": ""}])
    result = run(answer_pg, MFG, "The supply agreement was amended last July. How many units must the buyer take across all products?", chat, docs)
    assert result["status"] == "answered" and result["conclusion"] == "40 units"


def test_unresolved_wording_is_ignored_once_the_plan_names_its_period(answer_pg):
    chat = Script([plan(measure="metric/sales_ordered_units", entities=["E1"], time=["D1"])])
    result = run(answer_pg, MFG, "Customer 29641 was reviewed last year. How many units did it order in 2024?", chat)
    assert result["status"] == "answered", result["reason"]


def test_unresolved_wording_with_a_dated_measure_and_no_period_is_a_clarification(answer_pg):
    result = run(answer_pg, MFG, "How many sales orders came in last quarter?", Script([plan(measure="metric/sales_order_count")]))
    assert result["status"] == "clarify" and "date wording" in result["reason"]


# ---------------------------------------------------------------- tolerant validation and named repairs

def test_container_shapes_are_coerced(answer_pg):
    reply = {**plan(measure="metric/sales_ordered_units"), "entities": "E1", "time": "D1", "filters": {}}
    result = run(answer_pg, MFG, "How many units did customer 29641 order in 2024?", Script([reply]))
    assert result["status"] == "answered" and result["conclusion"] > 0, result["reason"]


def test_ids_in_the_wrong_slot_are_moved_or_dropped(answer_pg):
    reply = plan(measure="metric/sales_ordered_units", entities=["field/Sales_Customer/CustomerID", "D1"], time=["E1"])
    result = run(answer_pg, MFG, "How many units did customer 29641 order in 2024?", Script([reply]))
    assert result["status"] == "answered", result["reason"]
    assert result["facts"][0]["filters"] == {"CustomerID": 29641, "OrderDate": {"from": "2024-01-01", "to": "2024-12-31"}}


def test_a_repair_names_each_broken_item_and_what_is_allowed_there(answer_pg):
    bad = plan(measure="metric/active_accounts", time=["D1"], filters=[{"field": "field/Accounts/CustomerType", "value": "Corporate"}])
    chat = Script([bad, bad])
    result = run(answer_pg, FIN, "How many accounts of corporate customers were active on 6 November 2026?", chat)
    repair = chat.calls[1][1]
    assert "filters[0].field" in repair and "field/Customers/CustomerType" in repair
    assert result["status"] == "refused" and result["refusalReason"] == "not planned"
    assert result["reason"].startswith("The question could not be planned")


# ---------------------------------------------------------------- field aggregates

def test_count_counts_matching_rows_by_the_table_key(answer_pg):
    chat = Script([plan(measure="field/RelationshipManagers/Status", aggregate="count", time=["D1"],
                        filters=[{"field": "field/RelationshipManagers/Status", "value": "Active"}])])
    result = run(answer_pg, FIN, "How many relationship managers are active as at 6 November 2026?", chat)
    want = len({r["RMID"] for r in rows("RelationshipManagers") if r["BusinessDate"] == "2026-11-06" and r["Status"] == "Active"})
    assert result["status"] == "answered" and result["conclusion"] == want > 1, result["reason"]
    assert result["facts"][0]["filters"] == {"Status": "Active", "BusinessDate": "2026-11-06", "aggregate": "count"}


def test_a_date_range_is_allowed_on_an_event_table_whose_rows_do_not_repeat(answer_pg):
    account = next(r["AccountNumber"] for r in rows("CASA_Txn"))
    want = round(sum(float(r["Amount"]) for r in rows("CASA_Txn") if r["AccountNumber"] == account and r["DebitCredit"] == "CR"), 2)
    chat = Script([plan(measure="field/CASA_Txn/Amount", aggregate="sum", entities=["E1"], time=["D1"],
                        filters=[{"field": "field/CASA_Txn/DebitCredit", "value": "CR"}])])
    result = run(answer_pg, FIN, f"What credits did account {account} take in from 4 to 6 November 2026?", chat)
    assert result["status"] == "answered" and result["conclusion"] == pytest.approx(want), result["reason"]
    assert result["facts"][0]["filters"]["BusinessDate"] == {"from": "2026-11-04", "to": "2026-11-06"}


def test_a_range_on_a_table_whose_rows_repeat_across_snapshots_is_a_clarification(answer_pg):
    chat = Script([plan(measure="field/Accounts/AccountNumber", aggregate="count", time=["D1"])])
    result = run(answer_pg, FIN, "How many accounts were there from 4 to 6 November 2026?", chat)
    assert result["status"] == "clarify" and "one date" in result["reason"]


def test_a_comparison_filter_on_a_measure(answer_pg):
    fx = rows("Treasury_FX_Txn")
    account = next(r["AccountNumber"] for r in fx)
    want = sum(1 for r in fx if r["AccountNumber"] == account and float(r["Amount"]) > 40000)
    chat = Script([plan(measure="field/Treasury_FX_Txn/Amount", aggregate="count", entities=["E1"], time=["D1"],
                        filters=[{"field": "field/Treasury_FX_Txn/Amount", "op": "gt", "value": 40000}])])
    result = run(answer_pg, FIN, f"How many deals above 40,000 did account {account} book from 4 to 6 November 2026?", chat)
    assert result["status"] == "answered" and result["conclusion"] == want, result["reason"]
    assert result["facts"][0]["filters"]["Amount"] == {"gt": 40000}


def test_a_between_filter_gives_a_from_to_condition(answer_pg):
    chat = Script([plan(measure="field/Treasury_FX_Txn/Amount", aggregate="count", time=["D1"],
                        filters=[{"field": "field/Treasury_FX_Txn/Amount", "op": "between", "value": [10000, 20000]}])])
    result = run(answer_pg, FIN, "How many FX deals between 10,000 and 20,000 were booked on 6 November 2026?", chat)
    assert result["status"] == "answered", result["reason"]
    assert result["facts"][0]["filters"]["Amount"] == {"gte": 10000, "lte": 20000}


def test_a_field_aggregate_filters_through_one_declared_join(answer_pg):
    corporate = {r["CustomerID"] for r in rows("Customers") if r["BusinessDate"] == "2026-11-06" and r["CustomerType"] == "Corporate"}
    want = len({r["AccountNumber"] for r in rows("Accounts") if r["BusinessDate"] == "2026-11-06" and r["CustomerID"] in corporate})
    chat = Script([plan(measure="field/Accounts/AccountNumber", aggregate="count", time=["D1"],
                        filters=[{"field": "field/Customers/CustomerType", "value": "Corporate"}])])
    result = run(answer_pg, FIN, "How many accounts do corporate customers hold on 6 November 2026?", chat)
    assert result["status"] == "answered" and result["conclusion"] == want > 0, result["reason"]
    assert result["facts"][0]["filters"]["CustomerType"] == "Corporate"


def test_the_planner_is_told_when_a_metric_needs_what_the_question_lacks():
    assert "field aggregate" in ap.PLAN_SYSTEM and "needs" in ap.PLAN_SYSTEM


# ---------------------------------------------------------------- party tie by default

def test_a_need_with_no_party_is_tied_to_the_records_the_question_states(answer_pg):
    need = {"need": "the date the amendment changed the promise", "type": "date", "party": None, "required": True}
    docs = {"other": "The amendment changes the delivery promise for SalesOrderID 99999 on 2024-07-01."}
    wrong = {"found": [{"need": 1, "clause": 1, "value": "2024-07-01"}], "supporting": [], "summary": ""}
    chat = Script([plan(evidence=[need], answer="date")], [wrong, wrong])
    result = run(answer_pg, MFG, "On what date did the amendment change the promise for sales order 51131?", chat, docs)
    assert result["status"] == "refused" and result["conclusion"] is None and not result["citations"]
    assert len(chat.calls) == 3 and "not about" in chat.calls[2][1]          # the reader was asked again, then refused
    assert "No document of" in result["reason"] and "51131" in result["reason"]


def test_a_refusal_from_the_party_check_says_no_document_of_that_party_states_it(answer_pg):
    need = {"need": "the payment term", "type": "text", "party": "Harbour Traders", "required": True}
    docs = {"other": "Payment term for Ocean Supplies: invoices are payable within 45 days."}
    wrong = {"found": [{"need": 1, "clause": 1, "value": "45 days"}], "supporting": [], "summary": ""}
    chat = Script([plan(evidence=[need], answer="text", parties=["Harbour Traders"])], [wrong, wrong])
    result = run(answer_pg, MFG, "Within how many days must Harbour Traders pay its invoices?", chat, docs)
    assert result["status"] == "refused" and result["refusalReason"] == "no supported answer"
    assert "No document of Harbour Traders states the payment term" in result["reason"]


# ---------------------------------------------------------------- scope_required

def test_a_measure_declared_per_entity_asked_with_no_entity_is_a_clarification(answer_pg):
    result = run(answer_pg, MFG, "How many units are in stock?", Script([plan(measure="metric/inventory_units")]))
    assert result["status"] == "clarify" and "product" in result["reason"].lower() and not result["facts"]
    named = run(answer_pg, MFG, "How many units of product 954 are in stock?", Script([plan(measure="metric/inventory_units", entities=["E1"])]))
    assert named["status"] == "answered" and named["conclusion"] == 148


# ---------------------------------------------------------------- the plan and read model

def test_the_plan_and_read_model_comes_from_the_environment(monkeypatch):
    class Chat:
        model = "mistral-small-latest"

        def with_model(self, model):
            other = Chat()
            other.model = model
            return other
    monkeypatch.delenv("V2_ANSWER_MODEL", raising=False)
    assert ap._answer_chat(Chat()).model == "mistral-small-latest"
    monkeypatch.setenv("V2_ANSWER_MODEL", "mistral-medium-latest")
    assert ap._answer_chat(Chat()).model == "mistral-medium-latest"


# ---------------------------------------------------------------- the fact-shape contract of dev_wide (2026-11-22)

def test_a_year_is_shown_as_an_inclusive_from_to_range(answer_pg):
    chat = Script([plan(measure="metric/sales_order_count", entities=["E1"], time=["D1"])])
    result = run(answer_pg, MFG, "How many sales orders did customer 29641 place in 2024?", chat)
    assert result["facts"][0]["filters"] == {"CustomerID": 29641, "OrderDate": {"from": "2024-01-01", "to": "2024-12-31"}}
    assert result["conclusion"] == 3


def test_a_joined_filter_whose_name_the_base_table_shares_is_qualified(answer_pg):
    chat = Script([plan(measure="field/Accounts/AccountNumber", aggregate="count", time=["D1"],
                        filters=[{"field": "field/Customers/BranchCode", "value": "BR001"}])])
    result = run(answer_pg, FIN, "How many accounts do customers of branch BR001 hold on 6 November 2026?", chat)
    assert result["status"] == "answered", result["reason"]
    assert "Customers.BranchCode" in result["facts"][0]["filters"] and "BranchCode" not in result["facts"][0]["filters"]


def test_a_difference_is_its_size():
    assert ap.compute("difference", 21, 40) == 19 and ap.compute("difference", 40, 21) == 19


def test_the_plan_can_name_the_date_field_a_period_restricts(answer_pg):
    chat = Script([plan(measure="field/CASA_Txn/Amount", aggregate="sum", time=["D1"], time_field="field/CASA_Txn/TransactionDate",
                        filters=[{"field": "field/CASA_Txn/Currency", "value": "SGD"}])])
    result = run(answer_pg, FIN, "What was the total of SGD CASA transactions dated 5 November 2026?", chat)
    assert result["status"] == "answered", result["reason"]
    assert result["facts"][0]["filters"]["TransactionDate"] == "2026-11-05" and "BusinessDate" not in result["facts"][0]["filters"]
