"""
Task 2 acceptance tests: Ossie metric validator and new metrics.

Reference values computed 2026-11-20 from the pinned demo CSV files using stdlib csv.
These values are the expected figures for Task 3 (Talk to Data compiler) to match.
They are independent stdlib computations and do NOT use the metric expressions.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
MFG_CSV = REPO / "demo_data/manufacturing/data/csv"
FIN_CSV = REPO / "demo_data/finance/local/finance_workbook"
MFG_YAML = REPO / "demo_data/manufacturing/semantics/manufacturing.ossie.yaml"
FIN_YAML = REPO / "demo_data/finance/finance.ossie.yaml"


# ---------------------------------------------------------------------------
# Validator unit tests
# ---------------------------------------------------------------------------

def test_validator_accepts_sum():
    from semantic_pipeline_v2.core import _validate_metric_expr
    tables = {"Sales_SalesOrderDetail": {"SalesOrderID", "LineTotal", "UnitPrice", "OrderQty"}}
    result = _validate_metric_expr("SUM(Sales_SalesOrderDetail.LineTotal)", tables, set())
    assert result == {"Sales_SalesOrderDetail"}


def test_validator_accepts_count_distinct():
    from semantic_pipeline_v2.core import _validate_metric_expr
    tables = {"Sales_SalesOrderHeader": {"SalesOrderID", "OrderDate"}}
    result = _validate_metric_expr(
        "COUNT(DISTINCT Sales_SalesOrderHeader.SalesOrderID)", tables, set()
    )
    assert result == {"Sales_SalesOrderHeader"}


def test_validator_accepts_avg():
    from semantic_pipeline_v2.core import _validate_metric_expr
    tables = {"Sales_SalesOrderDetail": {"UnitPrice"}}
    result = _validate_metric_expr("AVG(Sales_SalesOrderDetail.UnitPrice)", tables, set())
    assert result == {"Sales_SalesOrderDetail"}


def test_validator_accepts_min_max():
    from semantic_pipeline_v2.core import _validate_metric_expr
    tables = {"Sales_SalesOrderDetail": {"UnitPrice"}}
    assert _validate_metric_expr("MIN(Sales_SalesOrderDetail.UnitPrice)", tables, set()) == {
        "Sales_SalesOrderDetail"
    }
    assert _validate_metric_expr("MAX(Sales_SalesOrderDetail.UnitPrice)", tables, set()) == {
        "Sales_SalesOrderDetail"
    }


def test_validator_accepts_case_when_null():
    from semantic_pipeline_v2.core import _validate_metric_expr
    tables = {"Production_WorkOrder": {"EndDate", "OrderQty"}}
    result = _validate_metric_expr(
        "COUNT(CASE WHEN Production_WorkOrder.EndDate IS NULL THEN 1 END)", tables, set()
    )
    assert result == {"Production_WorkOrder"}


def test_validator_accepts_case_when_comparison():
    from semantic_pipeline_v2.core import _validate_metric_expr
    tables = {"Sales_SalesOrderHeader": {"ShipDate", "DueDate"}}
    result = _validate_metric_expr(
        "COUNT(CASE WHEN Sales_SalesOrderHeader.ShipDate > Sales_SalesOrderHeader.DueDate THEN 1 END)",
        tables,
        set(),
    )
    assert result == {"Sales_SalesOrderHeader"}


def test_validator_accepts_case_when_string_literal():
    from semantic_pipeline_v2.core import _validate_metric_expr
    tables = {"Accounts": {"Status", "AccountNumber"}}
    result = _validate_metric_expr(
        "COUNT(CASE WHEN Accounts.Status = 'Active' THEN 1 END)", tables, set()
    )
    assert result == {"Accounts"}


def test_validator_accepts_ratio():
    from semantic_pipeline_v2.core import _validate_metric_expr
    result = _validate_metric_expr(
        "production_scrapped_units / production_requested_units",
        {},
        {"production_scrapped_units", "production_requested_units"},
    )
    assert result == set()


def test_validator_rejects_unknown_function():
    from semantic_pipeline_v2.core import _validate_metric_expr
    tables = {"Sales_SalesOrderDetail": {"LineTotal"}}
    with pytest.raises(ValueError):
        _validate_metric_expr("STDDEV(Sales_SalesOrderDetail.LineTotal)", tables, set())


def test_validator_rejects_unknown_table():
    from semantic_pipeline_v2.core import _validate_metric_expr
    tables = {"Sales_SalesOrderDetail": {"LineTotal"}}
    with pytest.raises(ValueError, match="unknown table"):
        _validate_metric_expr("SUM(NonExistent.LineTotal)", tables, set())


def test_validator_rejects_unknown_column():
    from semantic_pipeline_v2.core import _validate_metric_expr
    tables = {"Sales_SalesOrderDetail": {"LineTotal"}}
    with pytest.raises(ValueError, match="unknown column"):
        _validate_metric_expr("SUM(Sales_SalesOrderDetail.NoSuchCol)", tables, set())


def test_validator_rejects_ratio_unknown_metric():
    from semantic_pipeline_v2.core import _validate_metric_expr
    with pytest.raises(ValueError, match="unknown metric"):
        _validate_metric_expr("a / b", {}, {"a"})


def test_validator_rejects_unqualified_column():
    from semantic_pipeline_v2.core import _validate_metric_expr
    tables = {"Sales_SalesOrderDetail": {"LineTotal"}}
    with pytest.raises(ValueError, match="qualified"):
        _validate_metric_expr("SUM(LineTotal)", tables, set())


def test_validator_rejects_multi_table_case_when():
    from semantic_pipeline_v2.core import _validate_metric_expr
    tables = {"TableA": {"col1"}, "TableB": {"col2"}}
    with pytest.raises(ValueError, match="single table"):
        _validate_metric_expr(
            "COUNT(CASE WHEN TableA.col1 > TableB.col2 THEN 1 END)", tables, set()
        )


# ---------------------------------------------------------------------------
# Manufacturing model full compilation
# ---------------------------------------------------------------------------

def _load_model(path: Path) -> dict:
    raw = path.read_bytes()
    checksum = hashlib.sha256(raw).hexdigest()
    from semantic_pipeline_v2.core import normalize
    return normalize(raw, checksum)


def test_manufacturing_model_compiles():
    model = _load_model(MFG_YAML)
    assert model["namespace"] == "adventureworks_manufacturing_demo"
    metric_ids = {r["source_id"] for r in model["records"] if "/metric/" in r["source_id"]}
    expected_new = {
        "adventureworks_manufacturing_demo/metric/sales_order_count",
        "adventureworks_manufacturing_demo/metric/sales_line_revenue",
        "adventureworks_manufacturing_demo/metric/average_unit_price",
        "adventureworks_manufacturing_demo/metric/production_scrap_rate",
        "adventureworks_manufacturing_demo/metric/late_dispatch_orders",
        "adventureworks_manufacturing_demo/metric/open_work_orders",
    }
    assert expected_new <= metric_ids, f"Missing metrics: {expected_new - metric_ids}"


def test_finance_model_compiles():
    model = _load_model(FIN_YAML)
    assert model["namespace"] == "finance_workbook_demo"
    metric_ids = {r["source_id"] for r in model["records"] if "/metric/" in r["source_id"]}
    expected_new = {
        "finance_workbook_demo/metric/active_accounts",
        "finance_workbook_demo/metric/casa_transaction_value",
        "finance_workbook_demo/metric/casa_transaction_count",
        "finance_workbook_demo/metric/credit_card_transaction_value",
        "finance_workbook_demo/metric/large_casa_transaction_count",
        "finance_workbook_demo/metric/high_risk_customers",
    }
    assert expected_new <= metric_ids, f"Missing metrics: {expected_new - metric_ids}"


# ---------------------------------------------------------------------------
# HELIOS extension completeness
# ---------------------------------------------------------------------------

def _helios_data(metric: dict) -> dict:
    for ext in metric.get("custom_extensions", []):
        if ext.get("vendor_name") == "HELIOS":
            d = ext["data"]
            return json.loads(d) if isinstance(d, str) else d
    return {}


def test_helios_extensions_have_required_fields():
    """Every metric's HELIOS extension must include concept, grain, unit, time_field, review_state."""
    for label, path in [("manufacturing", MFG_YAML), ("finance", FIN_YAML)]:
        model = yaml.safe_load(path.read_bytes())
        for metric in model.get("metrics", []):
            name = metric["name"]
            helios = _helios_data(metric)
            assert helios, f"{label}/{name}: no HELIOS extension"
            for field in ("concept", "grain", "unit", "time_field", "review_state"):
                assert field in helios, (
                    f"{label}/{name}: missing '{field}' in HELIOS extension; got {list(helios)}"
                )


# ---------------------------------------------------------------------------
# Concepts present in models
# ---------------------------------------------------------------------------

def _get_model_concepts(path: Path) -> list:
    model = yaml.safe_load(path.read_bytes())
    for ext in model.get("custom_extensions", []):
        if ext.get("vendor_name") == "HELIOS":
            d = ext["data"]
            data = json.loads(d) if isinstance(d, str) else d
            if "concepts" in data:
                return data["concepts"]
    return []


def test_manufacturing_concepts_present():
    concepts = _get_model_concepts(MFG_YAML)
    assert len(concepts) >= 6, f"expected >=6 concepts, got {len(concepts)}"
    ids = {c["id"] for c in concepts}
    for required in ("customer_identifier", "product_identifier", "inventory_position",
                     "sales_order", "revenue", "production_scrap"):
        assert required in ids, f"concept {required!r} missing from manufacturing"


def test_finance_concepts_present():
    concepts = _get_model_concepts(FIN_YAML)
    assert len(concepts) >= 5, f"expected >=5 concepts, got {len(concepts)}"
    ids = {c["id"] for c in concepts}
    for required in ("account_status", "transaction_volume", "risk_rating",
                     "customer_count", "transaction_amount", "large_transaction"):
        assert required in ids, f"concept {required!r} missing from finance"


# ---------------------------------------------------------------------------
# Stdlib csv reference values for Task 3
# Reference values are independent of the metric expressions.
# ---------------------------------------------------------------------------

def _csv_rows(path: Path) -> list:
    """Return list of dicts from a CSV file, skipping empty rows."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        return [r for r in csv.DictReader(f)]


def test_csv_ref_sales_order_count():
    """31465 distinct SalesOrderIDs in SalesOrderHeader. Metric: COUNT(DISTINCT ...SalesOrderID)."""
    rows = _csv_rows(MFG_CSV / "Sales_SalesOrderHeader.csv")
    computed = len({r["SalesOrderID"] for r in rows})
    assert computed == 31465


def test_csv_ref_sales_line_revenue():
    """109846381.40 total LineTotal. Metric: SUM(Sales_SalesOrderDetail.LineTotal)."""
    rows = _csv_rows(MFG_CSV / "Sales_SalesOrderDetail.csv")
    computed = round(sum(float(r["LineTotal"]) for r in rows), 2)
    assert math.isclose(computed, 109846381.40, rel_tol=1e-5)


def test_csv_ref_average_unit_price():
    """~465.09 average UnitPrice. Metric: AVG(Sales_SalesOrderDetail.UnitPrice)."""
    rows = _csv_rows(MFG_CSV / "Sales_SalesOrderDetail.csv")
    prices = [float(r["UnitPrice"]) for r in rows]
    computed = sum(prices) / len(prices)
    assert math.isclose(computed, 465.093496, rel_tol=1e-4)


def test_csv_ref_production_scrap_rate():
    """~0.002363 scrap rate. Metric: production_scrapped_units / production_requested_units."""
    rows = _csv_rows(MFG_CSV / "Production_WorkOrder.csv")
    scrapped = sum(float(r["ScrappedQty"]) for r in rows)
    ordered = sum(float(r["OrderQty"]) for r in rows)
    assert math.isclose(scrapped / ordered, 0.002363, abs_tol=1e-5)


def test_csv_ref_late_dispatch_orders():
    """0 late dispatch orders in demo (ShipDate > DueDate). Metric: late_dispatch_orders.
    Each row is one order header, not an order line; metric name reflects this."""
    rows = _csv_rows(MFG_CSV / "Sales_SalesOrderHeader.csv")
    computed = sum(1 for r in rows if r["ShipDate"] > r["DueDate"])
    assert computed == 0  # all orders dispatched on time in demo fixture


def test_csv_synthetic_late_dispatch_orders():
    """Synthetic: 2 of 3 orders have ShipDate after DueDate. Label: synthetic."""
    import io
    data = "SalesOrderID,ShipDate,DueDate\n1,2026-11-10,2026-11-05\n2,2026-11-06,2026-11-08\n3,2026-11-07,2026-11-04\n"
    rows = list(csv.DictReader(io.StringIO(data)))
    computed = sum(1 for r in rows if r["ShipDate"] > r["DueDate"])
    assert computed == 2  # synthetic: orders 1 and 3 are late


def test_csv_ref_open_work_orders():
    """0 open work orders in demo (EndDate IS NULL). Metric: open_work_orders."""
    rows = _csv_rows(MFG_CSV / "Production_WorkOrder.csv")
    computed = sum(1 for r in rows if not r.get("EndDate", "").strip())
    assert computed == 0  # all work orders have EndDate in demo fixture


def test_csv_synthetic_open_work_orders():
    """Synthetic: 2 of 4 work orders have empty EndDate. Label: synthetic."""
    import io
    data = "WorkOrderID,OrderQty,ScrappedQty,EndDate\n1,10,0,2026-11-01\n2,5,0,\n3,8,1,2026-11-03\n4,3,0,\n"
    rows = list(csv.DictReader(io.StringIO(data)))
    computed = sum(1 for r in rows if not r.get("EndDate", "").strip())
    assert computed == 2  # synthetic: work orders 2 and 4 have no EndDate


def test_csv_ref_active_accounts():
    """active_accounts = COUNT(DISTINCT CASE WHEN Accounts.Status='Active' THEN Accounts.AccountNumber END).
    The Accounts CSV has 493 snapshot rows across 3 daily snapshots (Nov 4-6 2026).
    492 rows are Active (1 is Inactive), but 230 distinct AccountNumbers are Active across all snapshots.
    Per day (filtered to one BusinessDate): Nov-04 gives 162, Nov-05 gives 166, Nov-06 gives 164.
    The metric requires BusinessDate=single_value, so the per-day count is the operational result.
    The all-snapshots distinct count (230) equals the total active portfolio in this demo file."""
    rows = _csv_rows(FIN_CSV / "Accounts.csv")
    # Overall distinct active accounts across all snapshot dates
    distinct_active_all = len({r["AccountNumber"] for r in rows if r["Status"] == "Active"})
    assert distinct_active_all == 230

    # Per-day distinct active accounts (metric requires BusinessDate filter)
    from collections import defaultdict
    by_date: dict = defaultdict(set)
    for r in rows:
        if r["Status"] == "Active":
            by_date[r["BusinessDate"]].add(r["AccountNumber"])
    per_day = {d: len(ids) for d, ids in by_date.items()}
    assert per_day["2026-11-04"] == 162
    assert per_day["2026-11-05"] == 166
    assert per_day["2026-11-06"] == 164


def test_csv_ref_casa_transaction_value():
    """8319031.86 total CASA Amount. Metric: casa_transaction_value = SUM(CASA_Txn.Amount).
    This is a transaction-volume measure, not an account balance."""
    rows = _csv_rows(FIN_CSV / "CASA_Txn.csv")
    computed = round(sum(float(r["Amount"]) for r in rows), 2)
    assert math.isclose(computed, 8319031.86, rel_tol=1e-5)


def test_csv_ref_casa_transaction_count():
    """335 distinct CASA transactions. Metric: COUNT(DISTINCT CASA_Txn.TransactionID)."""
    rows = _csv_rows(FIN_CSV / "CASA_Txn.csv")
    computed = len({r["TransactionID"] for r in rows})
    assert computed == 335


def test_csv_ref_credit_card_transaction_value():
    """723020.50 total CreditCard Amount. Metric: SUM(CreditCard_Txn.Amount)."""
    rows = _csv_rows(FIN_CSV / "CreditCard_Txn.csv")
    computed = round(sum(float(r["Amount"]) for r in rows), 2)
    assert math.isclose(computed, 723020.50, rel_tol=1e-5)


def test_csv_ref_large_casa_transaction_count():
    """0 CASA transactions > 50000 in demo. Metric: large_casa_transaction_count.
    No STR flag exists in this workbook; threshold proxy only."""
    rows = _csv_rows(FIN_CSV / "CASA_Txn.csv")
    computed = sum(1 for r in rows if float(r["Amount"]) > 50000)
    assert computed == 0  # demo data has no transactions above 50000


def test_csv_synthetic_large_casa_transaction_count():
    """Synthetic: 2 of 4 CASA transactions exceed 50000. Label: synthetic."""
    import io
    data = "TransactionID,Amount\nT1,25000\nT2,75000\nT3,10000\nT4,120000\n"
    rows = list(csv.DictReader(io.StringIO(data)))
    computed = sum(1 for r in rows if float(r["Amount"]) > 50000)
    assert computed == 2  # synthetic: T2 and T4 exceed threshold


def test_csv_ref_high_risk_customers():
    """0 customers with RiskRating='High' in demo. Metric: high_risk_customers.
    Demo data has only Medium and Low RiskRating values.
    Filter is on the recorded RiskRating column only (never on adverse media or allegations)."""
    rows = _csv_rows(FIN_CSV / "Customers.csv")
    computed = sum(1 for r in rows if r["RiskRating"] == "High")
    assert computed == 0  # no High-risk customers in demo fixture


def test_csv_synthetic_high_risk_customers():
    """Synthetic: 2 of 4 customers are High risk. Label: synthetic."""
    import io
    data = "CustomerID,RiskRating\nC1,High\nC2,Medium\nC3,High\nC4,Low\n"
    rows = list(csv.DictReader(io.StringIO(data)))
    computed = sum(1 for r in rows if r["RiskRating"] == "High")
    assert computed == 2  # synthetic: C1 and C3 are High risk
