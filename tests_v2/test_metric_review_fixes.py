"""C0 review fixes: Talk to Data never returns a number for a question it did not fully understand.

Offline only. A recording fake stands in for the database: it serves the model definitions and
raises ``Reached`` on any other statement, so a test can prove that a question was refused
before SQL ran, or that it got as far as running SQL.
"""

from __future__ import annotations

import datetime
import json

import pytest

from semantic_pipeline_v2.access import project_workspace
from semantic_pipeline_v2.metric_sql import (
    ClarificationNeeded,
    Filter,
    MetricPlan,
    MetricQueryError,
    SourceWithheld,
    _compile,
    _list_distinct_values,
    _metric_name,
    _offline_plan,
    _pick_metric,
    _semantics,
    extract_dates,
    guard_sql,
    guard_limits_for,
    query_metric,
)
from semantic_pipeline_v2.talk_to_data import TalkRequest, respond
from semantic_pipeline_v2.workspace import load_workspace
from test_metric_sql_second_pass import FINANCE_OSSIE, MFG_DEFS, metric, model_defs, workspace

FIN_DEFS = model_defs(FINANCE_OSSIE)


class Reached(Exception):
    """The code under test sent SQL to the database."""


class FakePg:
    def __init__(self, defs):
        self.defs = defs

    def sql(self, script, **kwargs):
        if "v2_definition" in script:
            return json.dumps(self.defs)
        raise Reached(script)


def ask(defs, dataset, question, **kwargs):
    return query_metric(workspace(dataset), question, FakePg(defs), **kwargs)


# ---------------------------------------------------------------------------
# 1. A date phrase that is not understood is never dropped
# ---------------------------------------------------------------------------

UNPARSED_DATES = [   # 2026-11-22: "in November 2026" and "in Q3 2025" now resolve (test below)
    "What is the total sales line revenue in Q3?",
    "What is the total sales line revenue in November?",
    "What is the total sales line revenue on 06/11/2025?",
    "What is the total sales line revenue last month?",
    "What is the total sales line revenue on 31 February 2026?",
    "What is the total sales line revenue since 2024-01-01?",
    "What is the total sales line revenue between March and May?",
    "What is the total sales line revenue this year?",
    "What is the total sales line revenue on 2026-13-45?",
]


@pytest.mark.parametrize("question", UNPARSED_DATES)
def test_unparsed_date_phrase_is_clarified_not_dropped(question):
    with pytest.raises(ClarificationNeeded, match="not understand|did not understand"):
        ask(MFG_DEFS, "manufacturing", question)


def test_a_calendar_year_is_now_a_range_and_reaches_the_database():
    with pytest.raises(Reached, match=r">= '2025-01-01'.*< '2026-01-01'"):    # P4: no longer refused
        ask(MFG_DEFS, "manufacturing", "What is the total sales line revenue in 2025?")


def test_clarification_names_the_phrase_it_did_not_understand():
    with pytest.raises(ClarificationNeeded) as caught:
        ask(MFG_DEFS, "manufacturing", "What is the total sales line revenue in Q3?")
    assert "Q3" in str(caught.value)


@pytest.mark.parametrize("question, low, high", [
    ("What is the total sales line revenue in November 2026?", "2026-11-01", "2026-11-30"),
    ("What is the total sales line revenue in Q3 2025?", "2025-07-01", "2025-09-30"),
])
def test_a_month_or_a_quarter_with_its_year_reaches_the_database_as_a_range(question, low, high):
    with pytest.raises(Reached, match=rf">= '{low}'.*<= '{high}'"):
        ask(MFG_DEFS, "manufacturing", question)


def test_planner_path_also_refuses_an_unparsed_date():
    plan = json.dumps({"metric": "sales_line_revenue", "filters": []})

    class Planner:
        available = True

        def plan(self, combined, options):
            return plan

    with pytest.raises(ClarificationNeeded):
        ask(MFG_DEFS, "manufacturing", "total sales line revenue last month", interpreter=Planner())


def test_range_with_a_shared_month_is_parsed_against_the_calendar():
    found, rest = extract_dates("between 4 and 6 November 2026")
    assert found == [datetime.date(2026, 11, 4).isoformat(), datetime.date(2026, 11, 6).isoformat()]
    assert "between" not in rest and "2026" not in rest
    assert extract_dates("from 30 to 31 February 2026")[0] == []


def test_a_fully_parsed_date_still_reaches_the_database():
    with pytest.raises(Reached):
        ask(MFG_DEFS, "manufacturing", "What is the total sales line revenue on 2024-03-01?")


def test_a_reversed_range_is_clarified():
    # 2026-11-22: the one period parser reports a reversed range as wording it cannot resolve
    with pytest.raises(ClarificationNeeded, match="did not understand"):
        _offline_plan("What is the total sales order count from 2024-03-05 to 2024-03-01?",
                      MFG_DEFS, None, "manufacturing", None)


# ---------------------------------------------------------------------------
# 2. Wrong metric on a near tie or an unmatched noun
# ---------------------------------------------------------------------------

def _example_cases():
    for defs in (FIN_DEFS, MFG_DEFS):
        for mdef in defs:
            if mdef["kind"] == "metric":
                for example in _semantics(mdef)["examples"]:
                    yield pytest.param(defs, mdef, example, id=f"{_metric_name(mdef)}::{example[:40]}")


@pytest.mark.parametrize("defs,mdef,example", list(_example_cases()))
def test_every_model_example_question_resolves_to_its_own_metric(defs, mdef, example):
    picked, _ = _pick_metric(example, defs)
    assert picked is not None and _metric_name(picked) == _metric_name(mdef)


def test_work_orders_is_not_answered_with_sales_orders():
    with pytest.raises(ClarificationNeeded) as caught:
        _pick_metric("number of work orders", MFG_DEFS)
    assert "Sales order count" in str(caught.value) or "Open work orders" in str(caught.value)
    assert "work" in str(caught.value).lower()


def test_loan_value_is_not_answered_with_casa_value():
    with pytest.raises(ClarificationNeeded) as caught:
        _pick_metric("loan transaction value 2026-11-06 SGD", FIN_DEFS)
    assert "loan" in str(caught.value).lower()
    assert "CASA transaction value" in str(caught.value)


def test_a_noun_the_model_names_but_the_metric_does_not_is_refused():
    # 'purchasing' names a dataset in the model; no sales metric speaks of it.
    with pytest.raises(ClarificationNeeded):
        _pick_metric("total purchasing revenue", MFG_DEFS)


def test_a_clear_question_is_still_answered():
    picked, _ = _pick_metric("total sales line revenue", MFG_DEFS)
    assert _metric_name(picked) == "sales_line_revenue"


# ---------------------------------------------------------------------------
# 3. Breakdowns are refused, never silently collapsed into a grand total
# ---------------------------------------------------------------------------

BREAKDOWNS = [
    "total sales line revenue by product",
    "sales line revenue per customer",
    "sales line revenue for each territory",
    "sales line revenue split by product",
    "top 5 products by sales line revenue",
    "which product has the most sales line revenue",
]


@pytest.mark.parametrize("question", BREAKDOWNS)
def test_breakdown_question_is_clarified(question):
    with pytest.raises(ClarificationNeeded, match="group|breakdown|one total"):
        ask(MFG_DEFS, "manufacturing", question)


def test_planner_plan_with_dimensions_is_clarified():
    plan = json.dumps({"metric": "sales_line_revenue", "dimensions": ["ProductID"], "filters": []})

    class Planner:
        available = True

        def plan(self, combined, options):
            return plan

    with pytest.raises(ClarificationNeeded, match="ProductID"):
        ask(MFG_DEFS, "manufacturing", "sales line revenue", interpreter=Planner())


def test_compile_refuses_dimensions():
    plan = MetricPlan(metric="sales_line_revenue", dimensions=["ProductID"])
    with pytest.raises(ClarificationNeeded):
        _compile(metric(MFG_DEFS, "sales_line_revenue"), plan, "manufacturing", MFG_DEFS)


# ---------------------------------------------------------------------------
# 4. Facts are gated by source access
# ---------------------------------------------------------------------------

def _scoped(dataset, denied=()):
    prepared = {"datasets": [load_workspace(dataset)], "reviewAvailable": False}
    principal = {"datasets": [dataset], "withheld": list(denied), "role": "restricted"}
    return project_workspace(prepared, principal)["datasets"][0]


def test_withheld_workbook_refuses_facts_before_any_sql():
    ws = _scoped("financial-services", ["financial-services:source:workbook"])
    with pytest.raises(SourceWithheld, match="withheld"):
        query_metric(ws, "How many customers were there on 2026-11-06?", FakePg(FIN_DEFS))


def test_withheld_workbook_gives_a_clarify_reply_in_respond():
    ws = _scoped("financial-services", ["financial-services:source:workbook"])
    reply = respond(ws, TalkRequest("financial-services", "How many customers were there on 2026-11-06?"),
                    database=FakePg(FIN_DEFS))
    assert reply["status"] == "clarify" and "withheld" in reply["summary"] and reply["facts"] == []


def test_allowed_workbook_and_unrelated_withheld_source_still_reach_the_database():
    for denied in ([], ["financial-services:source:jade-adverse-media"]):
        ws = _scoped("financial-services", denied)
        with pytest.raises(Reached):
            query_metric(ws, "How many customers were there on 2026-11-06?", FakePg(FIN_DEFS))


def test_withheld_contract_does_not_block_manufacturing_facts():
    ws = _scoped("manufacturing", ["manufacturing:source:MFG-003", "manufacturing:source:MFG-007"])
    with pytest.raises(Reached):
        query_metric(ws, "What is the total sales order count on 2024-03-01?", FakePg(MFG_DEFS))


# ---------------------------------------------------------------------------
# 5. The guard checks content, not a second copy of the compiler
# ---------------------------------------------------------------------------

def _guarded(defs, name, plan, dataset):
    mdef = metric(defs, name)
    sql, tables = _compile(mdef, plan, dataset, defs)
    return mdef, sql, tables, guard_limits_for(mdef, plan, defs, tables)


def _all_compiled_metrics():
    for defs, dataset, extra in ((FIN_DEFS, "financial-services", {"Currency": "SGD"}), (MFG_DEFS, "manufacturing", {})):
        for mdef in defs:
            if mdef["kind"] == "metric":
                yield pytest.param(defs, dataset, mdef, id=_metric_name(mdef))


@pytest.mark.parametrize("defs,dataset,mdef", list(_all_compiled_metrics()))
def test_every_compiled_metric_passes_the_content_guard(defs, dataset, mdef):
    from semantic_pipeline_v2.metric_sql import _dataset_schema, _helios
    helios = _helios(mdef["source_metadata"])
    filters = [Filter(field="BusinessDate", op="=", value="2026-11-06")] if "BusinessDate" in (helios.get("required_filters") or {}) else []
    plan = MetricPlan(metric=_metric_name(mdef), filters=filters)
    sql, tables = _compile(mdef, plan, dataset, defs)
    columns, literals, funcs = guard_limits_for(mdef, plan, defs, tables)
    guard_sql(sql, tables, _dataset_schema(dataset), columns=columns, literals=literals, funcs=funcs)


def _valid():
    plan = MetricPlan(metric="customers_on_business_date",
                      filters=[Filter(field="BusinessDate", op="=", value="2026-11-06")])
    mdef, sql, tables, limits = _guarded(FIN_DEFS, "customers_on_business_date", plan, "financial-services")
    return sql, tables, limits


def _run_guard(sql, tables, limits):
    columns, literals, funcs = limits
    guard_sql(sql, tables, "v2_facts_financial_services", columns=columns, literals=literals, funcs=funcs)


def test_content_guard_accepts_the_compiled_sql():
    _run_guard(*_valid())


@pytest.mark.parametrize("edit,match", [
    (lambda s: s.replace("WHERE", "WHERE 1 = 1 OR"), "OR|literal|comparison"),
    (lambda s: s.replace("'2026-11-06'", "'2099-01-01'"), "literal"),
    (lambda s: s + " UNION SELECT 1", "single SELECT|set operation|LIMIT"),
    (lambda s: "WITH x AS (SELECT 1) " + s, "CTE|WITH|subquer"),
    (lambda s: s.replace("= '2026-11-06'", "IN (SELECT customerid FROM v2_facts_financial_services.customers)"),
     "subquer|comparison"),
    (lambda s: s.replace("customers.customerid", "customers.passwordhash"), "column"),
    (lambda s: s.replace("FROM v2_facts_financial_services.customers",
                         "FROM v2_facts_financial_services.customers, v2_facts_financial_services.accounts"), "table"),
    (lambda s: s.replace("COUNT(", "pg_sleep(10) + COUNT("), "function"),
    (lambda s: s.replace("COUNT(", "LOWER(").replace("DISTINCT ", ""), "function"),
    (lambda s: s.replace("= '2026-11-06'", "= batchdate"), "comparison|column"),
])
def test_tampered_sql_is_stopped_by_the_guard(edit, match):
    sql, tables, limits = _valid()
    tampered = edit(sql)
    assert tampered != sql
    with pytest.raises(ValueError, match=match):
        _run_guard(tampered, tables, limits)


# ---------------------------------------------------------------------------
# 6. No silent choices
# ---------------------------------------------------------------------------

def test_two_dates_for_a_single_date_metric_are_clarified():
    with pytest.raises(ClarificationNeeded, match="one date|single"):
        _offline_plan("How many active accounts were there on 2026-11-04 and 2026-11-06?",
                      FIN_DEFS, FakePg(FIN_DEFS), "financial-services", "batch")


class BrokenPg:
    def sql(self, script, **kwargs):
        raise RuntimeError("connection lost")


def test_distinct_value_lookup_failure_is_an_error_not_an_unknown_value():
    with pytest.raises(MetricQueryError, match="connection lost"):
        _list_distinct_values(BrokenPg(), "financial-services", "Customers", "CustomerType")
    assert not issubclass(MetricQueryError, ClarificationNeeded)


def test_distinct_value_lookup_rejects_a_bad_identifier():
    with pytest.raises(ValueError):
        _list_distinct_values(FakePg([]), "financial-services", "Customers", "x; DROP TABLE y")
