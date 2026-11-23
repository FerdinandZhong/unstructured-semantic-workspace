"""The detail behind an answer (2026-11-22): rows behind a figure, a context series and suggested next questions.
The browser sends only a server-issued handle; every query is derived from the compiler's own guarded SQL."""

import json

import pytest

from semantic_pipeline_v2 import answer_detail
from semantic_pipeline_v2.answer_detail import Details, respond
from semantic_pipeline_v2.metric_sql import Filter, MetricPlan, _list_metric_defs, compute_metric, query_field
from semantic_pipeline_v2.workspace import load_workspace
from semantic_pipeline_v2.access import project_workspace

FIN, MFG = "financial-services", "manufacturing"
ADMIN = {"id": "admin", "role": "admin", "datasets": [FIN, MFG], "withheld": []}
FIN_ONLY = {"id": "reader", "role": "restricted", "datasets": [FIN], "withheld": []}


def scoped(dataset, withheld=()):
    prepared = {"datasets": [load_workspace(dataset)], "reviewAvailable": False}
    return project_workspace(prepared, {"datasets": [dataset], "withheld": list(withheld), "role": "restricted"})["datasets"][0]


def metric_id(dataset, name):
    model = "finance_workbook_demo" if dataset == FIN else "adventureworks_manufacturing_demo"
    return f"{dataset}:definition:{model}/metric/{name}"


def metric_fact(pg, dataset, name, filters):
    defs = _list_metric_defs(pg, dataset)
    d = next(x for x in defs if x["id"] == metric_id(dataset, name))
    fact = compute_metric(scoped(dataset), d, MetricPlan(metric=name, filters=filters), pg, defs, max_hops=3)
    assert fact is not None
    return fact


def reply_of(question, fact=None, *, status="answered", reason=None, measure=None, definitions=None):
    """A reply in the answer path's shape (answer_path._shape plus the readable filters)."""
    facts = [] if fact is None else [{"definition": fact["definitionId"], "label": fact["label"], "value": fact["value"],
                                      "sql": fact["sql"], "kind": fact.get("kind", "metric"), "filters": {}}]
    return {"status": status, "question": question, "refusalReason": reason, "facts": facts,
            "definitions": definitions if definitions is not None else [f["definition"] for f in facts],
            "plan": {"measure": measure, "time_field": None}}


def ask(details, reply, dataset, mode, *, principal=ADMIN, workspace=None, pg=None, handle=None):
    held = details.register(reply, dataset, ADMIN)
    handle = handle or (held["facts"][0]["detail"]["id"] if mode in ("rows", "series") else held["detail"])
    return respond(details, {"dataset": dataset, "handle": handle, "mode": mode}, principal,
                   workspace if workspace is not None else scoped(dataset), pg)


# ---------------------------------------------------------------- rows behind a figure

def test_rows_behind_a_count_are_as_many_as_the_count(answer_pg):
    fact = metric_fact(answer_pg, FIN, "customers_on_business_date",
                       [Filter(field="BusinessDate", value="2026-11-06"), Filter(field="CustomerType", value="Corporate")])
    assert fact["value"] == 12
    status, out = ask(Details(), reply_of("How many corporate customers?", fact), FIN, "rows", pg=answer_pg)
    assert status == 200 and out["total"] == 12 and len(out["rows"]) == 12
    assert {row["CustomerType"] for row in out["rows"]} == {"Corporate"}


def test_rows_behind_a_sum_add_up_to_the_sum_through_a_declared_join(answer_pg):
    fact = metric_fact(answer_pg, MFG, "sales_ordered_units", [
        Filter(field="Sales_SalesOrderHeader.CustomerID", value="29641"), Filter(field="ProductID", value="954"),
        Filter(field="Sales_SalesOrderHeader.OrderDate", op=">=", value="2024-01-01"),
        Filter(field="Sales_SalesOrderHeader.OrderDate", op="<", value="2025-01-01")])
    assert fact["value"] == 51
    details = Details()
    held = details.register(reply_of("How many units?", fact), MFG, ADMIN)
    assert held["facts"][0]["detail"]["tables"] == ["sales_salesorderdetail", "sales_salesorderheader"]
    status, out = respond(details, {"dataset": MFG, "handle": held["facts"][0]["detail"]["id"], "mode": "rows"},
                          ADMIN, scoped(MFG), answer_pg)
    assert status == 200 and out["total"] == len(out["rows"]) < 50
    assert out["measure"] == "OrderQty"
    assert sum(float(row["OrderQty"]) for row in out["rows"]) == 51


def test_rows_hide_system_columns_and_stop_at_fifty(answer_pg):
    fact = metric_fact(answer_pg, FIN, "casa_transaction_value",
                       [Filter(field="BusinessDate", value="2026-11-05"), Filter(field="Currency", value="SGD")])
    status, out = ask(Details(), reply_of("CASA value", fact), FIN, "rows", pg=answer_pg)
    assert status == 200 and len(out["rows"]) <= 50 and out["total"] >= len(out["rows"])
    names = {c["name"] for c in out["columns"]}
    assert names and not names & {"SourceBatch", "SourceRevision", "rowguid", "ModifiedDate", "GroupSeq", "TransactionSeq"}
    assert all(set(row) <= names for row in out["rows"])
    if out["total"] <= 50:
        assert round(sum(float(r["Amount"]) for r in out["rows"]), 2) == fact["value"]


def test_a_value_lookup_has_no_rows_and_no_chart(answer_pg):
    fid = "manufacturing:definition:adventureworks_manufacturing_demo/field/Sales_SalesOrderHeader/DueDate"
    fact = query_field(scoped(MFG), fid, [Filter(field="SalesOrderID", value="51131")], answer_pg)
    details = Details()
    held = details.register(reply_of("Due date of 51131", fact), MFG, ADMIN)
    assert held["facts"][0]["detail"]["rows"] is False
    for mode in ("rows", "series"):
        status, out = respond(details, {"dataset": MFG, "handle": held["facts"][0]["detail"]["id"], "mode": mode},
                              ADMIN, scoped(MFG), answer_pg)
        assert status == 200 and out.get("rows") is None and out.get("kind") is None


# ---------------------------------------------------------------- the safety of the route

@pytest.mark.parametrize("body", [
    {"dataset": FIN, "handle": "x", "mode": "rows", "sql": "SELECT 1"},
    {"dataset": FIN, "sql": "SELECT * FROM v2_facts_financial_services.customers", "mode": "rows"},
    {"dataset": FIN, "handle": "x", "mode": "sql"},
    {"dataset": FIN, "handle": 3, "mode": "rows"},
    "SELECT 1",
])
def test_free_sql_and_malformed_requests_are_refused(body):
    status, out = respond(Details(), body, ADMIN, scoped(FIN), None)
    assert status == 400 and "error" in out


def test_an_unknown_handle_is_not_found():
    status, _ = respond(Details(), {"dataset": FIN, "handle": "0" * 32, "mode": "rows"}, ADMIN, scoped(FIN), None)
    assert status == 404


def test_a_handle_is_bound_to_its_dataset_and_its_account(answer_pg):
    fact = metric_fact(answer_pg, FIN, "customers_on_business_date",
                       [Filter(field="BusinessDate", value="2026-11-06"), Filter(field="CustomerType", value="Corporate")])
    details = Details()
    handle = details.register(reply_of("q", fact), FIN, ADMIN)["facts"][0]["detail"]["id"]
    # another dataset (with the same handle)
    assert respond(details, {"dataset": MFG, "handle": handle, "mode": "rows"}, ADMIN, scoped(MFG), answer_pg)[0] == 404
    # another account that can read the dataset still cannot use the admin's handle
    assert respond(details, {"dataset": FIN, "handle": handle, "mode": "rows"}, FIN_ONLY, scoped(FIN), answer_pg)[0] == 404
    # an account without the dataset
    outsider = {"id": "admin", "role": "admin", "datasets": [MFG], "withheld": []}
    assert respond(details, {"dataset": FIN, "handle": handle, "mode": "rows"}, outsider, scoped(FIN), answer_pg)[0] == 403
    assert respond(details, {"dataset": FIN, "handle": handle, "mode": "rows"}, ADMIN, None, answer_pg)[0] == 403


def test_a_withheld_workbook_gives_no_rows_series_or_suggestions(answer_pg):
    fact = metric_fact(answer_pg, FIN, "customers_on_business_date",
                       [Filter(field="BusinessDate", value="2026-11-06"), Filter(field="CustomerType", value="Corporate")])
    withheld = scoped(FIN, ["financial-services:source:workbook"])
    for mode in ("rows", "series", "next"):
        status, out = ask(Details(), reply_of("How many corporate customers on 6 November 2026?", fact), FIN, mode,
                          workspace=withheld, pg=answer_pg)
        assert status == 403 and "rows" not in out and "points" not in out and "suggestions" not in out


def test_values_are_masked(answer_pg, monkeypatch):
    fact = metric_fact(answer_pg, FIN, "customers_on_business_date",
                       [Filter(field="BusinessDate", value="2026-11-06"), Filter(field="CustomerType", value="Corporate")])
    real = answer_detail._json

    def with_nric(pg, sql):
        rows = real(pg, sql)
        return [{**row, "CustomerID": "S1234567D"} if "CustomerID" in row else row for row in rows]
    monkeypatch.setattr(answer_detail, "_json", with_nric)
    status, out = ask(Details(), reply_of("q", fact), FIN, "rows", pg=answer_pg)
    assert status == 200 and out["rows"] and "S1234567D" not in json.dumps(out)


# ---------------------------------------------------------------- the context series of a chart

def test_a_metric_on_one_business_date_gives_a_daily_series(answer_pg):
    fact = metric_fact(answer_pg, FIN, "casa_transaction_value",
                       [Filter(field="BusinessDate", value="2026-11-05"), Filter(field="Currency", value="SGD")])
    status, out = ask(Details(), reply_of("q", fact), FIN, "series", pg=answer_pg)
    assert status == 200 and out["kind"] == "date" and out["grain"] == "day" and out["axis"] == "Business date"
    assert [p["key"] for p in out["points"]] == ["2026-11-04", "2026-11-05", "2026-11-06"]
    assert [p["key"] for p in out["points"] if p["answered"]] == ["2026-11-05"]
    assert next(p["value"] for p in out["points"] if p["answered"]) == fact["value"]


def test_a_count_over_a_year_gives_a_monthly_series_whose_answered_months_add_up(answer_pg):
    fact = metric_fact(answer_pg, MFG, "sales_order_count", [
        Filter(field="TerritoryID", value="4"), Filter(field="OrderDate", op=">=", value="2023-01-01"),
        Filter(field="OrderDate", op="<=", value="2023-12-31")])
    status, out = ask(Details(), reply_of("q", fact), MFG, "series", pg=answer_pg)
    assert status == 200 and out["kind"] == "date" and out["grain"] == "month"
    answered = [p for p in out["points"] if p["answered"]]
    assert len(answered) == 12 and sum(p["value"] for p in answered) == fact["value"]
    assert len(out["points"]) <= 62


def test_a_filtered_count_without_a_date_gives_one_bar_per_value(answer_pg):
    fact = metric_fact(answer_pg, MFG, "sales_order_count", [Filter(field="TerritoryID", value="4")])
    status, out = ask(Details(), reply_of("q", fact), MFG, "series", pg=answer_pg)
    assert status == 200 and out["kind"] == "category" and 1 < len(out["points"]) <= 12
    assert [p["value"] for p in out["points"] if p["answered"]] == [fact["value"]]


# ---------------------------------------------------------------- suggested next questions

def suggest(pg, dataset, question, measure, status, reason=None, fact=None):
    reply = reply_of(question, fact, status=status, reason=reason, measure=measure,
                     definitions=[] if fact is None else None)
    code, out = ask(Details(), reply, dataset, "next", pg=pg)
    assert code == 200
    return [s["question"] for s in out["suggestions"]]


def test_a_period_outside_the_data_suggests_the_nearest_dates(answer_pg):
    asked = suggest(answer_pg, FIN, "How many accounts were active at the end of October 2026?", "metric/active_accounts",
                    "refused", "period outside the data")
    assert asked == ["How many accounts were active on 4 November 2026?", "How many accounts were active on 5 November 2026?"]
    asked = suggest(answer_pg, MFG, "How many sales orders were placed in the first quarter of 2026?",
                    "metric/sales_order_count", "refused", "period outside the data")
    assert asked == ["How many sales orders were placed in the second quarter of 2025?",
                     "How many sales orders were placed in the first quarter of 2025?"]


def test_an_unknown_party_suggests_nothing(answer_pg):
    assert suggest(answer_pg, FIN, "What is the recorded risk rating of Jade Logistics?", "field/Customers/RiskRating",
                   "refused", "unknown party") == []


def test_a_clarification_is_completed_with_values_from_the_data(answer_pg):
    asked = suggest(answer_pg, FIN, "What was the total value of current and savings account transactions?",
                    "metric/casa_transaction_value", "clarify")
    assert 1 <= len(asked) <= 3
    assert all(q.startswith("What was the total value of current and savings account transactions for transaction currency ")
               and q.endswith(" on 6 November 2026?") for q in asked)


def test_an_answer_suggests_the_previous_and_the_next_period(answer_pg):
    fact = metric_fact(answer_pg, FIN, "casa_transaction_value",
                       [Filter(field="BusinessDate", value="2026-11-05"), Filter(field="Currency", value="SGD")])
    question = "What was the total value of current and savings account transactions in Singapore dollars on 5 November 2026?"
    asked = suggest(answer_pg, FIN, question, "metric/casa_transaction_value", "answered", fact=fact)
    assert asked == [question.replace("5 November", "4 November"), question.replace("5 November", "6 November")]
