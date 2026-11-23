"""Tests for metric_sql: guard, Mistral path, narrative audit, and PostgreSQL integration.

Offline unit tests (guard, narrative audit, Mistral path) run without PostgreSQL.
Opt-in integration tests require V2_PG_BIN set to the pgserver binary directory.

Manufacturing projections total ~31 MB (4 CSV files).  They are loaded once per
module via the ``manufacturing_pg`` fixture so each test file shares one database
load.  Expect 20-40 s startup for the manufacturing fixture on first run.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

import pytest

from semantic_pipeline_v2.metric_sql import (
    ClarificationNeeded,
    Filter,
    MetricPlan,
    _compile_sql,
    _offline_plan,
    audit_narrative,
    ensure_projections_loaded,
    guard_sql,
    query_metric,
)

ROOT = Path(__file__).resolve().parents[1]
FINANCE_CSV = ROOT / "demo_data/finance/local/finance_workbook"
FINANCE_OSSIE = ROOT / "demo_data/finance/finance.ossie.yaml"
FINANCE_DATASET = "financial-services"
FINANCE_SCHEMA = "v2_facts_financial_services"

MFG_CSV = ROOT / "demo_data/manufacturing/data/csv"
MFG_OSSIE = ROOT / "demo_data/manufacturing/semantics/manufacturing.ossie.yaml"
MFG_DATASET = "manufacturing"
MFG_SCHEMA = "v2_facts_manufacturing"


# ---------------------------------------------------------------------------
# Guard tests (no PostgreSQL)
# ---------------------------------------------------------------------------

def test_guard_rejects_multiple_statements():
    sql = "SELECT count(*) AS metric_value FROM s.t LIMIT 100; DROP TABLE s.t;"
    with pytest.raises(ValueError, match="single SELECT"):
        guard_sql(sql, {"t"}, "s")


def test_guard_rejects_disallowed_table():
    sql = "SELECT count(*) AS metric_value FROM s.evil LIMIT 100"
    with pytest.raises(ValueError, match="not in allowed"):
        guard_sql(sql, {"customers"}, "s")


def test_guard_rejects_disallowed_schema():
    sql = "SELECT count(*) AS metric_value FROM badschema.customers LIMIT 100"
    with pytest.raises(ValueError, match="disallowed schema"):
        guard_sql(sql, {"customers"}, "goodschema")


def test_guard_rejects_missing_limit():
    sql = "SELECT count(*) AS metric_value FROM s.customers"
    with pytest.raises(ValueError, match="LIMIT"):
        guard_sql(sql, {"customers"}, "s")


def test_guard_rejects_limit_over_100():
    sql = "SELECT count(*) AS metric_value FROM s.customers LIMIT 101"
    with pytest.raises(ValueError, match="LIMIT"):
        guard_sql(sql, {"customers"}, "s")


def test_guard_accepts_valid_sql():
    sql = (
        "SELECT count(DISTINCT customers.customerid) AS metric_value\n"
        f"FROM {FINANCE_SCHEMA}.customers\n"
        "WHERE businessdate = '2026-11-06'\n"
        "LIMIT 100"
    )
    guard_sql(sql, {"customers"}, FINANCE_SCHEMA)


def test_guard_tampered_sql_is_stopped_by_content_checks():
    """The guard reads the SQL itself: an injected OR, a foreign literal or a foreign column is refused."""
    valid = (
        f"SELECT count(DISTINCT customers.customerid) AS metric_value\n"
        f"FROM {FINANCE_SCHEMA}.customers\n"
        "WHERE customers.businessdate = '2026-11-06'\n"
        "LIMIT 100"
    )
    columns = {"customers": {"customerid", "businessdate"}}
    literals = {"2026-11-06", "100"}
    guard_sql(valid, {"customers"}, FINANCE_SCHEMA, columns=columns, literals=literals)
    for tampered, reason in (
        (valid.replace("LIMIT", "OR 1=1\nLIMIT"), "comparison"),
        (valid.replace("2026-11-06", "2026-11-07"), "literal"),
        (valid.replace("customers.customerid", "customers.secret"), "column"),
    ):
        with pytest.raises(ValueError, match=reason):
            guard_sql(tampered, {"customers"}, FINANCE_SCHEMA, columns=columns, literals=literals)


# ---------------------------------------------------------------------------
# Narrative audit tests (no PostgreSQL)
# ---------------------------------------------------------------------------

def test_narrative_audit_passes_for_own_value():
    rows = [{"metric_value": "51"}]
    narrative = "51 — customers on business date on 2026-11-06. Grain: customer snapshot."
    assert audit_narrative(narrative, rows) is True


def test_narrative_audit_fails_when_number_not_in_rows():
    """A narrative containing a hardcoded number not from rows fails the audit."""
    rows = [{"metric_value": "51"}]
    # 999 is not in rows and is not a year/month/day.
    bad_narrative = "51 — customers, plus estimated 999 inactive accounts."
    assert audit_narrative(bad_narrative, rows) is False


def test_narrative_audit_allows_date_numbers():
    """Year/month/day numbers in a date string are allowed even if not in rows."""
    rows = [{"metric_value": "164"}]
    narrative = "164 — active accounts on 2026-11-06. Grain: daily snapshot."
    # "2026", "11", "06" are date parts → allowed; "164" is in rows.
    assert audit_narrative(narrative, rows) is True


# ---------------------------------------------------------------------------
# Mistral plan path tests (no PostgreSQL, no network)
# ---------------------------------------------------------------------------

class _FakePlanClient:
    """Fake Mistral plan client for tests."""

    def __init__(self, responses: list[str]):
        self._responses = iter(responses)
        self._calls: list[str] = []

    @property
    def available(self):
        return True

    def plan(self, combined: str, metric_options: list) -> str:
        self._calls.append(combined)
        return next(self._responses)


def _minimal_metric_defs_for_active_accounts():
    return [{
        "kind": "metric",
        "id": "fin/metric/active_accounts",
        "source_id": "finance_workbook_demo/metric/active_accounts",
        "label": "Active accounts",
        "definition": "count of distinct active accounts",
        "source_metadata": {
            "custom_extensions": [{
                "vendor_name": "HELIOS",
                "data": json.dumps({
                    "dataset": "Accounts",
                    "grain": "daily snapshot",
                    "unit": "accounts",
                    "time_field": "BusinessDate",
                    "required_filters": {
                        "BusinessDate": "single_value",
                        "SourceBatch": "single_value",
                    },
                    "concept": "account_status",
                    "review_state": "approved",
                }),
            }],
        },
        "expression": {"dialects": [{"dialect": "ANSI_SQL",
                                      "expression": "COUNT(DISTINCT CASE WHEN Accounts.Status = 'Active' THEN Accounts.AccountNumber END)"}]},
    }]


class _SimpleFakePg:
    """PilotPostgres stand-in that always returns '[]' for sql calls."""

    def sql(self, query, **params):
        return "[]"


def test_mistral_plan_path_valid_first_response():
    """Mistral path extracts MetricPlan from valid first response."""
    from semantic_pipeline_v2.metric_sql import _mistral_plan

    metric_defs = _minimal_metric_defs_for_active_accounts()
    payload = json.dumps({
        "metric": "active_accounts",
        "filters": [
            {"field": "BusinessDate", "op": "=", "value": "2026-11-06"},
            {"field": "SourceBatch", "op": "=", "value": "abc123"},
        ],
    })
    client = _FakePlanClient([payload])
    plan, mdef = _mistral_plan(
        "How many active accounts on 2026-11-06? context: batch abc123",
        metric_defs,
        client,
        _SimpleFakePg(),
        "financial-services",
        "abc123",
    )
    assert plan.metric == "active_accounts"
    assert len(plan.filters) == 2
    # combined was passed to the client
    assert "2026-11-06" in client._calls[0]


def test_mistral_plan_path_repair_retry_succeeds():
    """One repair retry succeeds after first invalid response."""
    from semantic_pipeline_v2.metric_sql import _mistral_plan

    metric_defs = _minimal_metric_defs_for_active_accounts()
    good = json.dumps({"metric": "active_accounts", "filters": []})
    client = _FakePlanClient(["not json at all", good])
    plan, _ = _mistral_plan(
        "active accounts",
        metric_defs,
        client,
        _SimpleFakePg(),
        "financial-services",
        None,
    )
    assert plan.metric == "active_accounts"
    assert len(client._calls) == 2
    # Second call contains the error from the first attempt.
    assert "repair" in client._calls[1].lower()


def test_mistral_plan_path_fails_after_two_bad_responses():
    """ValueError raised after two consecutive invalid responses."""
    from semantic_pipeline_v2.metric_sql import _mistral_plan

    metric_defs = _minimal_metric_defs_for_active_accounts()
    client = _FakePlanClient(["bad1", "bad2"])
    with pytest.raises(ValueError, match="repair"):
        _mistral_plan(
            "active accounts",
            metric_defs,
            client,
            _SimpleFakePg(),
            "financial-services",
            None,
        )


def test_mistral_plan_receives_combined_context():
    """Mistral client receives the full combined (context-folded) string."""
    from semantic_pipeline_v2.metric_sql import _mistral_plan

    metric_defs = _minimal_metric_defs_for_active_accounts()
    good = json.dumps({"metric": "active_accounts", "filters": []})
    combined = "Active accounts question. Prior context: snapshot from 2026-11-05."
    client = _FakePlanClient([good])
    _mistral_plan(combined, metric_defs, client, _SimpleFakePg(), "financial-services", None)
    assert client._calls[0] == combined


# ---------------------------------------------------------------------------
# ClarificationNeeded: metric-defined fixed filters must NOT raise
# ---------------------------------------------------------------------------

def test_offline_plan_does_not_raise_for_metric_fixed_filter():
    """_offline_plan applies metric-defined fixed filters silently (no ClarificationNeeded)."""
    metric_def = {
        "kind": "metric",
        "label": "high risk customers",
        "definition": "count of customers with high risk rating",
        "source_id": "financial-services/high_risk_customers",
        "source_metadata": {
            "custom_extensions": [{
                "vendor_name": "HELIOS",
                "data": json.dumps({
                    "dataset": "Customers",
                    "grain": "customer snapshot",
                    "unit": "customers",
                    "time_field": "BusinessDate",
                    "required_filters": {
                        "BusinessDate": "single_value",
                        "SourceBatch": "single_value",
                        "RiskRating": "High",
                    },
                    "concept": "risk_rating",
                    "review_state": "approved",
                }),
            }],
        },
        "expression": {"dialects": [{"dialect": "ANSI_SQL",
                                     "expression": "COUNT(CASE WHEN Customers.RiskRating = 'High' THEN 1 END)"}]},
    }
    # Fake pg returns no "High" value — metric-defined filter must NOT trigger ClarificationNeeded.
    class _FakePg:
        def sql(self, q, **p):
            return json.dumps(["Low", "Medium"])

    # Must not raise; should return a valid plan with RiskRating='High' applied.
    result = _offline_plan(
        "high risk customers 2026-11-06",
        [metric_def],
        _FakePg(),
        "financial-services",
        "batchhash",
    )
    assert result is not None
    plan, _ = result
    risk_filters = [f for f in plan.filters if f.field == "RiskRating"]
    assert len(risk_filters) == 1
    assert risk_filters[0].value == "High"


# ---------------------------------------------------------------------------
# Integration tests — require real PostgreSQL via V2_PG_BIN
# ---------------------------------------------------------------------------

PG_BIN = os.environ.get("V2_PG_BIN")


@pytest.fixture(scope="module")
def finance_pg(tmp_path_factory):
    """Start a private PostgreSQL, import finance OSSIE, load CSV projections."""
    if not PG_BIN:
        pytest.skip("set V2_PG_BIN to run metric SQL integration tests")
    from semantic_pipeline_v2._pilot_postgres import PilotPostgres
    from semantic_pipeline_v2.semantic_store import SemanticStore

    state = tmp_path_factory.mktemp("finance_pg")
    with PilotPostgres.start(Path(PG_BIN), data_directory=state / "pgdata") as pg:
        store = SemanticStore(pg)
        store.import_ossie(FINANCE_DATASET, FINANCE_OSSIE)
        ensure_projections_loaded(pg, FINANCE_DATASET, FINANCE_CSV)
        yield pg


@pytest.fixture(scope="module")
def manufacturing_pg(tmp_path_factory):
    """Start a private PostgreSQL, import manufacturing OSSIE, load CSV projections.

    Loading ~31 MB of CSV data (4 tables).  Expect 20-40 s on first run.
    Fixture is module-scoped so the load happens once for all manufacturing tests.
    """
    if not PG_BIN:
        pytest.skip("set V2_PG_BIN to run metric SQL integration tests")
    from semantic_pipeline_v2._pilot_postgres import PilotPostgres
    from semantic_pipeline_v2.semantic_store import SemanticStore

    state = tmp_path_factory.mktemp("manufacturing_pg")
    with PilotPostgres.start(Path(PG_BIN), data_directory=state / "pgdata") as pg:
        store = SemanticStore(pg)
        store.import_ossie(MFG_DATASET, MFG_OSSIE)
        ensure_projections_loaded(pg, MFG_DATASET, MFG_CSV)
        yield pg


def _finance_workspace():
    """Load the financial-services workspace with an accessible workbook source."""
    from semantic_pipeline_v2.workspace import load_workspace
    from semantic_pipeline_v2.access import project_workspace
    prepared = {"datasets": [load_workspace("financial-services")], "reviewAvailable": False}
    principal = {"datasets": ["financial-services"], "withheld": [], "role": "restricted"}
    return project_workspace(prepared, principal)["datasets"][0]


_MFG_WORKSPACE = {
    "id": "manufacturing",
    "sources": [],
    "knowledge": {"structured": {}},
}


# ---------------------------------------------------------------------------
# Finance integration tests
# ---------------------------------------------------------------------------

def _customer_count_assertions(fact, date="2026-11-06"):
    assert fact is not None, "query_metric returned None"
    assert fact["value"] == 51, f"expected 51, got {fact['value']}"
    assert "SELECT" in fact["sql"].upper()
    assert fact["batchHash"]
    assert fact["modelRevision"], "a fact must carry the model revision"
    assert date in fact["sql"]


def test_customer_count_on_2026_11_06_returns_51(finance_pg):
    """T25: the plain question, with no 'business date' wording, returns 51."""
    workspace = _finance_workspace()
    fact = query_metric(workspace, "How many customers were there on 2026-11-06?", finance_pg)
    _customer_count_assertions(fact)


@pytest.mark.parametrize("question", [
    "customer count for 6 Nov 2026",
    "how many clients did we have on 2026-11-06",
    "number of customers as of 2026-11-06",
    "How many customers were there on 6 November 2026?",
])
def test_customer_count_natural_phrasings_return_51(finance_pg, question):
    """T25: other natural phrasings bind a bare date to the metric's time field."""
    _customer_count_assertions(query_metric(_finance_workspace(), question, finance_pg))


def test_no_monkeypatch_uses_real_projections_directory(finance_pg):
    """The finance projection is loaded from the real FINANCE_CSV path."""
    import hashlib
    customers_csv = FINANCE_CSV / "Customers.csv"
    expected_sha = hashlib.sha256(customers_csv.read_bytes()).hexdigest()
    result = finance_pg.sql(
        "SELECT csv_sha256 FROM v2_projection_load "
        "WHERE dataset = :'ds' AND table_name = 'Customers' "
        "ORDER BY load_id DESC LIMIT 1;",
        ds=FINANCE_DATASET,
    )
    assert result.strip() == expected_sha


def test_customer_count_followup_keeps_context(finance_pg):
    """Follow-up 'What about 2026-11-04?' returns 50 with combined context."""
    from semantic_pipeline_v2.talk_to_data import _conversational_question
    workspace = _finance_workspace()
    context = "Count distinct customers 2026-11-04 to 2026-11-06"
    combined = _conversational_question("What about 2026-11-04?", context)
    fact = query_metric(workspace, combined, finance_pg)
    assert fact is not None
    assert fact["value"] == 50


def test_finance_active_accounts_on_2026_11_06(finance_pg):
    """active_accounts on 2026-11-06 = 164 (stdlib-csv reference)."""
    workspace = _finance_workspace()
    fact = query_metric(
        workspace,
        "How many active accounts were there on 2026-11-06?",
        finance_pg,
    )
    assert fact is not None, "query_metric returned None for active_accounts"
    assert fact["value"] == 164, f"expected 164, got {fact['value']}"


def _csv_amount(table, currency, date):
    """Independent stdlib reference: sum of Amount for one currency on one business date."""
    import csv
    with (FINANCE_CSV / f"{table}.csv").open(newline="") as handle:
        rows = [r for r in csv.DictReader(handle)
                if r["Currency"] == currency and r["BusinessDate"] == date]
    assert rows, "reference slice must not be empty"
    return round(sum(float(r["Amount"]) for r in rows), 2)


def _csv_distinct_count(table, column, date):
    """Independent stdlib reference: distinct values of one column on one business date."""
    import csv
    with (FINANCE_CSV / f"{table}.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    return len({r[column] for r in rows if r["BusinessDate"] == date}), len(rows)


def test_finance_casa_transaction_count_on_2026_11_06(finance_pg):
    """T26: casa_transaction_count for one date equals an independent per-date csv count.

    The file holds 335 rows over three dates; the per-date number is computed here
    from the file, never copied from the SQL under test.
    """
    expected, whole_file = _csv_distinct_count("CASA_Txn", "TransactionID", "2026-11-06")
    assert 0 < expected < whole_file, "the per-date reference must differ from the whole-file count"
    fact = query_metric(
        _finance_workspace(),
        "What is the distinct transaction count on 2026-11-06?",
        finance_pg,
    )
    assert fact is not None, "query_metric returned None for casa_transaction_count"
    assert fact["value"] == expected, f"expected {expected}, got {fact['value']}"
    assert "casa_txn" in fact["sql"] and "businessdate = '2026-11-06'" in fact["sql"]


def test_finance_casa_transaction_value_needs_one_currency_and_date(finance_pg):
    """casa_transaction_value for SGD on 2026-11-06 equals the independent csv sum."""
    workspace = _finance_workspace()
    fact = query_metric(
        workspace,
        "What is the total CASA transaction value in SGD on 2026-11-06?",
        finance_pg,
    )
    assert fact is not None, "query_metric returned None for casa_transaction_value"
    expected = _csv_amount("CASA_Txn", "SGD", "2026-11-06")
    assert math.isclose(float(fact["value"]), expected, rel_tol=1e-6), (
        f"expected ~{expected}, got {fact['value']}"
    )
    assert "currency = 'SGD'" in fact["sql"] and "businessdate = '2026-11-06'" in fact["sql"]


def test_finance_credit_card_transaction_value_needs_one_currency_and_date(finance_pg):
    """credit_card_transaction_value for USD on 2026-11-04 equals the independent csv sum."""
    workspace = _finance_workspace()
    fact = query_metric(
        workspace,
        "What is the total credit card transaction value in USD on 2026-11-04?",
        finance_pg,
    )
    assert fact is not None, "query_metric returned None for credit_card_transaction_value"
    expected = _csv_amount("CreditCard_Txn", "USD", "2026-11-04")
    assert math.isclose(float(fact["value"]), expected, rel_tol=1e-6), (
        f"expected ~{expected}, got {fact['value']}"
    )


def test_finance_value_metric_without_currency_asks_for_one(finance_pg):
    """A source-currency value metric never sums across currencies."""
    workspace = _finance_workspace()
    with pytest.raises(ClarificationNeeded, match="Currency"):
        query_metric(workspace, "What is the total CASA transaction value on 2026-11-06?", finance_pg)


def test_finance_large_casa_transaction_count_on_2026_11_06(finance_pg):
    """large_casa_transaction_count on 2026-11-06 = 0 (no transactions > 50000)."""
    workspace = _finance_workspace()
    fact = query_metric(
        workspace,
        "What is the large CASA transaction count on 2026-11-06?",
        finance_pg,
    )
    assert fact is not None, "query_metric returned None for large_casa_transaction_count"
    assert fact["value"] == 0, f"expected 0, got {fact['value']}"


def test_finance_high_risk_customers_on_2026_11_06(finance_pg):
    """high_risk_customers on 2026-11-06 = 0 (no High-risk customers in demo).

    This test also verifies that metric-defined fixed filter RiskRating='High' is
    applied without raising ClarificationNeeded.
    """
    workspace = _finance_workspace()
    fact = query_metric(
        workspace,
        "How many high risk customers on 2026-11-06?",
        finance_pg,
    )
    assert fact is not None, "query_metric returned None for high_risk_customers"
    assert fact["value"] == 0, f"expected 0, got {fact['value']}"


# ---------------------------------------------------------------------------
# Manufacturing integration tests  (note: ~31 MB CSV load, module-scoped)
# ---------------------------------------------------------------------------

def test_mfg_sales_order_count(manufacturing_pg):
    """sales_order_count = 31465 distinct SalesOrderIDs (stdlib-csv reference)."""
    fact = query_metric(
        _MFG_WORKSPACE,
        "What is the total sales order count?",
        manufacturing_pg,
    )
    assert fact is not None, "query_metric returned None for sales_order_count"
    assert fact["value"] == 31465, f"expected 31465, got {fact['value']}"


def test_mfg_sales_line_revenue(manufacturing_pg):
    """sales_line_revenue = 109846381.40 total LineTotal (stdlib-csv reference)."""
    fact = query_metric(
        _MFG_WORKSPACE,
        "What is the total sales line revenue?",
        manufacturing_pg,
    )
    assert fact is not None, "query_metric returned None for sales_line_revenue"
    assert math.isclose(float(fact["value"]), 109846381.40, rel_tol=1e-4), (
        f"expected ~109846381.40, got {fact['value']}"
    )


def test_mfg_average_unit_price(manufacturing_pg):
    """average_unit_price ≈ 465.09 (stdlib-csv reference)."""
    fact = query_metric(
        _MFG_WORKSPACE,
        "What is the average unit price?",
        manufacturing_pg,
    )
    assert fact is not None, "query_metric returned None for average_unit_price"
    assert math.isclose(float(fact["value"]), 465.093496, rel_tol=1e-3), (
        f"expected ~465.09, got {fact['value']}"
    )


def test_mfg_production_scrap_rate(manufacturing_pg):
    """production_scrap_rate ≈ 0.002363 (ratio metric via NULLIF; stdlib-csv ref)."""
    fact = query_metric(
        _MFG_WORKSPACE,
        "What is the production scrap rate?",
        manufacturing_pg,
    )
    assert fact is not None, "query_metric returned None for production_scrap_rate"
    assert math.isclose(float(fact["value"]), 0.002363, abs_tol=1e-5), (
        f"expected ~0.002363, got {fact['value']}"
    )


def test_mfg_late_dispatch_orders(manufacturing_pg):
    """late_dispatch_orders = 0 (no late orders in demo fixture)."""
    fact = query_metric(
        _MFG_WORKSPACE,
        "How many late dispatch orders are there?",
        manufacturing_pg,
    )
    assert fact is not None, "query_metric returned None for late_dispatch_orders"
    assert fact["value"] == 0, f"expected 0, got {fact['value']}"


def test_mfg_open_work_orders(manufacturing_pg):
    """open_work_orders = 0 (no open work orders in demo fixture)."""
    fact = query_metric(
        _MFG_WORKSPACE,
        "How many open work orders are there?",
        manufacturing_pg,
    )
    assert fact is not None, "query_metric returned None for open_work_orders"
    assert fact["value"] == 0, f"expected 0, got {fact['value']}"


def test_mfg_sales_ordered_units(manufacturing_pg):
    """sales_ordered_units = 274914 total OrderQty (stdlib-csv reference)."""
    fact = query_metric(
        _MFG_WORKSPACE,
        "How many units were ordered in total?",
        manufacturing_pg,
    )
    assert fact is not None, "query_metric returned None for sales_ordered_units"
    assert fact["value"] == 274914, f"expected 274914, got {fact['value']}"


def test_mfg_inventory_units(manufacturing_pg):
    """inventory_units = 335974 total Quantity in inventory (stdlib-csv reference)."""
    fact = query_metric(
        _MFG_WORKSPACE,
        "How many units are in inventory?",
        manufacturing_pg,
    )
    assert fact is not None, "query_metric returned None for inventory_units"
    assert fact["value"] == 335974, f"expected 335974, got {fact['value']}"


def test_mfg_inventory_units_refuses_a_date_filter(manufacturing_pg):
    """inventory_units has no business date; ModifiedDate is an audit column and is never filtered."""
    with pytest.raises(ClarificationNeeded, match="no business date"):
        query_metric(
            _MFG_WORKSPACE,
            "How many units are in inventory on 2008-06-30?",
            manufacturing_pg,
        )


def _mfg_order_day_reference():
    """Independent reference for one order date: busiest day in the header file, by sqlite over the csv."""
    import csv
    import sqlite3
    db = sqlite3.connect(":memory:")
    for table, columns in (("Sales_SalesOrderHeader", ("SalesOrderID", "OrderDate")),
                           ("Sales_SalesOrderDetail", ("SalesOrderID", "OrderQty", "LineTotal", "UnitPrice"))):
        with (MFG_CSV / f"{table}.csv").open(newline="") as handle:
            reader = csv.DictReader(handle)
            db.execute(f"CREATE TABLE {table} ({', '.join(columns)})")
            db.executemany(f"INSERT INTO {table} VALUES ({','.join('?' * len(columns))})",
                           ([row[c] for c in columns] for row in reader))
    day = db.execute("SELECT substr(OrderDate,1,10) d FROM Sales_SalesOrderHeader GROUP BY d "
                     "ORDER BY count(*) DESC, d LIMIT 1").fetchone()[0]
    revenue, units, average = db.execute(
        "SELECT sum(CAST(d.LineTotal AS REAL)), sum(CAST(d.OrderQty AS REAL)), avg(CAST(d.UnitPrice AS REAL)) "
        "FROM Sales_SalesOrderDetail d JOIN Sales_SalesOrderHeader h ON h.SalesOrderID = d.SalesOrderID "
        "WHERE substr(h.OrderDate,1,10) = ?", (day,)).fetchone()
    orders = db.execute("SELECT count(DISTINCT SalesOrderID) FROM Sales_SalesOrderHeader "
                        "WHERE substr(OrderDate,1,10) = ?", (day,)).fetchone()[0]
    return day, revenue, units, average, orders


def test_mfg_sales_line_metrics_for_one_order_date_join_through_the_declared_relationship(manufacturing_pg):
    """Sales-line metrics are dated on the order header; the date filter crosses one declared relationship."""
    day, revenue, units, average, orders = _mfg_order_day_reference()
    assert revenue and units and average and orders
    cases = [
        (f"What is the total sales line revenue on {day}?", revenue, "JOIN"),
        (f"How many units were ordered in total on {day}?", units, "JOIN"),
        (f"What is the average unit price on {day}?", average, "JOIN"),
        (f"What is the total sales order count on {day}?", orders, ""),
    ]
    for question, expected, token in cases:
        fact = query_metric(_MFG_WORKSPACE, question, manufacturing_pg)
        assert fact is not None, question
        assert math.isclose(float(fact["value"]), float(expected), rel_tol=1e-6), (question, fact["value"], expected)
        assert token in fact["sql"] and day in fact["sql"]
