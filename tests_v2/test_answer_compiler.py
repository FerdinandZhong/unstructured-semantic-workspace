"""P4 compiler additions: a calendar year as a half-open range, several filters at once, the derived
line-to-product relationship and one-value field lookups. Offline compile tests first, then private PostgreSQL."""

import pytest

from semantic_pipeline_v2.metric_sql import (
    _BREAKDOWN, ClarificationNeeded, Filter, MetricPlan, _compile_sql, _offline_plan, _refuse_unparsed_date,
    _dataset_schema, guard_limits_for, guard_sql, query_field, query_metric)
from semantic_pipeline_v2.workspace import load_workspace
from semantic_pipeline_v2.access import project_workspace
from test_metric_sql_second_pass import MFG_DEFS, metric

SCHEMA = _dataset_schema("manufacturing")
ORDERED = "How many units were ordered in 2024?"


def plan_for(question, defs=MFG_DEFS):
    plan, picked = _offline_plan(question, defs, None, "manufacturing", None)
    return plan, picked


def scoped(dataset):
    prepared = {"datasets": [load_workspace(dataset)], "reviewAvailable": False}
    return project_workspace(prepared, {"datasets": [dataset], "withheld": [], "role": "restricted"})["datasets"][0]


# ---------------------------------------------------------------- (a) a calendar year

def test_the_compiler_reads_periods_through_the_one_parser():
    # 2026-11-22: a quarter, a month and a bare year resolve (one parser, semantic_pipeline_v2.periods); they used to
    # be clarified. The year stays a half-open range; other periods are inclusive ranges of days.
    quarter, _ = plan_for("How many units were ordered in the second quarter of 2024?")
    assert [(f.op, f.value) for f in quarter.filters] == [(">=", "2024-04-01"), ("<=", "2024-06-30")]
    month, _ = plan_for("How many units were ordered in July 2024?")
    assert [(f.op, f.value) for f in month.filters] == [(">=", "2024-07-01"), ("<=", "2024-07-31")]
    bare, _ = plan_for("How many units were ordered 2024?")
    assert [(f.op, f.value) for f in bare.filters] == [(">=", "2024-01-01"), ("<", "2025-01-01")]


def test_year_becomes_a_half_open_range_on_the_time_field():
    plan, picked = plan_for(ORDERED)
    assert [(f.field, f.op, f.value) for f in plan.filters] == [
        ("Sales_SalesOrderHeader.OrderDate", ">=", "2024-01-01"), ("Sales_SalesOrderHeader.OrderDate", "<", "2025-01-01")]
    sql = " ".join(_compile_sql(picked, plan, "manufacturing", MFG_DEFS).split())
    assert ("CAST(sales_salesorderheader.orderdate AS DATE) >= '2024-01-01' AND "
            "CAST(sales_salesorderheader.orderdate AS DATE) < '2025-01-01'") in sql
    assert f"JOIN {SCHEMA}.sales_salesorderheader ON" in sql
    tables = {"sales_salesorderdetail", "sales_salesorderheader"}
    columns, literals, funcs = guard_limits_for(picked, plan, MFG_DEFS, tables)
    guard_sql(sql, tables, SCHEMA, columns=columns, literals=literals, funcs=funcs)


def test_year_boundaries_have_no_gap_and_no_overlap():
    for year in ("1999", "2099"):
        plan, _ = plan_for(f"How many units were ordered in {year}?")
        assert [f.value for f in plan.filters] == [f"{year}-01-01", f"{int(year) + 1}-01-01"]


def test_year_is_refused_when_the_metric_has_no_time_field():
    with pytest.raises(ClarificationNeeded, match="no business date"):
        plan_for("How many units are in stock in 2024?")


@pytest.mark.parametrize("question, why", [
    ("How many units were ordered in 2024 and in 2025?", "one period"),
    ("How many units were ordered in 2024 on 2024-05-01?", "one period"),
    ("How many units were ordered last year?", "date wording"),
    ("How many units were ordered in Q3?", "date wording"),                     # a quarter with no year is never guessed
])
def test_unparsed_or_ambiguous_years_are_never_defaulted(question, why):
    with pytest.raises(ClarificationNeeded, match=why):
        plan_for(question)


def test_year_with_a_single_date_metric_is_refused():
    with pytest.raises(ClarificationNeeded):
        _refuse_unparsed_date("How many units in 2024-13-45?")      # an impossible date is still not understood


def test_at_least_is_not_a_ranking_word():
    assert not _BREAKDOWN.search("commits to order at least 71 units")
    assert not _BREAKDOWN.search("at most five orders")
    assert _BREAKDOWN.search("which product sold the most")
    assert _BREAKDOWN.search("the least ordered product")


# ---------------------------------------------------------------- (b) several filters

def test_customer_product_and_year_compile_together_through_one_declared_join():
    plan = MetricPlan(metric="sales_ordered_units", filters=[
        Filter(field="Sales_SalesOrderHeader.CustomerID", value="29641"), Filter(field="ProductID", value="954"),
        Filter(field="Sales_SalesOrderHeader.OrderDate", op=">=", value="2024-01-01"),
        Filter(field="Sales_SalesOrderHeader.OrderDate", op="<", value="2025-01-01")])
    picked = metric(MFG_DEFS, "sales_ordered_units")
    sql = _compile_sql(picked, plan, "manufacturing", MFG_DEFS)
    flat = " ".join(sql.split())
    assert flat.count(f"JOIN {SCHEMA}") == 1
    assert "sales_salesorderheader.customerid = '29641'" in flat and "sales_salesorderdetail.productid = '954'" in flat
    tables = {"sales_salesorderdetail", "sales_salesorderheader"}
    columns, literals, funcs = guard_limits_for(picked, plan, MFG_DEFS, tables)
    guard_sql(sql, tables, SCHEMA, columns=columns, literals=literals, funcs=funcs)


def test_a_filter_on_a_column_or_a_join_the_model_does_not_declare_is_refused():
    picked = metric(MFG_DEFS, "sales_ordered_units")
    for field, error in (("Sales_SalesOrderHeader.NoSuchColumn", ValueError), ("Person_Person.LastName", ClarificationNeeded),
                         ("Sales_SalesOrderHeader.CustomerID; DROP TABLE x", ValueError)):
        with pytest.raises(error):
            _compile_sql(picked, MetricPlan(metric="m", filters=[Filter(field=field, value="1")]), "manufacturing", MFG_DEFS)


# ---------------------------------------------------------------- (c) the derived line-to-product relationship

def test_a_product_filter_joins_through_the_derived_relationship():
    plan = MetricPlan(metric="m", filters=[Filter(field="Production_Product.ProductNumber", value="BK-T79Y-46")])
    flat = " ".join(_compile_sql(metric(MFG_DEFS, "sales_ordered_units"), plan, "manufacturing", MFG_DEFS).split())
    assert f"JOIN {SCHEMA}.production_product ON sales_salesorderdetail.productid = production_product.productid" in flat


# ---------------------------------------------------------------- integration on private PostgreSQL

def test_year_customer_and_product_compute_the_sql_total(answer_pg):
    workspace = scoped("manufacturing")
    bind = lambda m, d: [Filter(field="Sales_SalesOrderHeader.CustomerID", value="29641"),  # noqa: E731
                         Filter(field="ProductID", value="954")]
    fact = query_metric(workspace, ORDERED, answer_pg, bind=bind)
    assert fact["value"] == 51
    assert {(f["field"], f["op"]) for f in fact["filters"]} >= {("Sales_SalesOrderHeader.OrderDate", ">="),
                                                                 ("Sales_SalesOrderHeader.OrderDate", "<")}
    assert "in 2024" in fact["summary"]
    other = query_metric(workspace, "How many units were ordered in 2023?", answer_pg, bind=bind)
    assert other is None or other["value"] != 51         # the range really selects the year


def test_year_totals_add_up_to_the_unfiltered_total(answer_pg):
    workspace = scoped("manufacturing")
    total = query_metric(workspace, "How many units were ordered?", answer_pg)["value"]
    by_year = [query_metric(workspace, f"How many units were ordered in {y}?", answer_pg) for y in range(2022, 2026)]
    assert sum(f["value"] for f in by_year if f) == total


def test_field_lookup_returns_the_one_value_through_the_guard(answer_pg):
    workspace = scoped("manufacturing")
    fid = "manufacturing:definition:adventureworks_manufacturing_demo/field/Sales_SalesOrderHeader/DueDate"
    fact = query_field(workspace, fid, [Filter(field="SalesOrderID", value="51131")], answer_pg)
    assert fact["value"] == "2024-06-10" and fact["kind"] == "field" and "MIN(" in fact["sql"]


def test_field_lookup_refuses_several_values_and_reports_no_rows(answer_pg):
    workspace = scoped("manufacturing")
    fid = "manufacturing:definition:adventureworks_manufacturing_demo/field/Sales_SalesOrderHeader/DueDate"
    with pytest.raises(ClarificationNeeded, match="more than one value"):
        query_field(workspace, fid, [Filter(field="CustomerID", value="29641")], answer_pg)
    assert query_field(workspace, fid, [Filter(field="SalesOrderID", value="1")], answer_pg) is None
    with pytest.raises(ValueError):
        query_field(workspace, "manufacturing:definition:x/field/Nope/Nope", [], answer_pg)


def test_field_lookup_dates_need_exactly_one_date_field(answer_pg):
    finance = scoped("financial-services")
    fid = "financial-services:definition:finance_workbook_demo/field/Customers/CustomerType"
    keys = [Filter(field="CustomerID", value="CUST00008")]
    assert query_field(finance, fid, keys, answer_pg, ("2026-11-06",))["value"] == "Corporate"
    with pytest.raises(ClarificationNeeded, match="one date"):
        query_field(finance, fid, keys, answer_pg, ("2026-11-05", "2026-11-06"))
    manufacturing = scoped("manufacturing")
    due = "manufacturing:definition:adventureworks_manufacturing_demo/field/Sales_SalesOrderHeader/DueDate"
    with pytest.raises(ClarificationNeeded, match="more than one date field"):
        query_field(manufacturing, due, [Filter(field="SalesOrderID", value="51131")], answer_pg, ("2024-06-10",))


def test_field_lookup_never_reads_a_withheld_workbook(answer_pg):
    prepared = {"datasets": [load_workspace("financial-services")], "reviewAvailable": False}
    withheld = project_workspace(prepared, {"datasets": ["financial-services"], "role": "restricted",
                                            "withheld": ["financial-services:source:workbook"]})["datasets"][0]
    from semantic_pipeline_v2.metric_sql import SourceWithheld
    fid = "financial-services:definition:finance_workbook_demo/field/Customers/CustomerType"
    with pytest.raises(SourceWithheld):
        query_field(withheld, fid, [Filter(field="CustomerID", value="CUST00008")], answer_pg)
