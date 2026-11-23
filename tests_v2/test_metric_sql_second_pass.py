"""Stage 0 second pass (C0): non-zero synthetic checks, loud loading, declared joins, G9.

Every reference value is computed here with the standard library (csv, sqlite3) from
the same files the database loads. Nothing is copied from the SQL under test.
The synthetic tables below are SYNTHETIC test data, labelled as such. They exist because
the demo data gives zero for four metrics.
"""

from __future__ import annotations

import ast
import copy
import csv
import json
import math
import os
import re
import sqlite3
from pathlib import Path

import pytest

from semantic_pipeline_v2.core import digest, normalize
from semantic_pipeline_v2.metric_sql import (
    ClarificationNeeded,
    Filter,
    MetricPlan,
    _compile_sql,
    _dataset_schema,
    guard_sql,
    query_metric,
)

ROOT = Path(__file__).resolve().parents[1]
FINANCE_OSSIE = ROOT / "demo_data/finance/finance.ossie.yaml"
MFG_OSSIE = ROOT / "demo_data/manufacturing/semantics/manufacturing.ossie.yaml"
PG_BIN = os.environ.get("V2_PG_BIN")


def model_defs(path: Path) -> list[dict]:
    """Definitions shaped like the rows the store returns, built offline from the model file."""
    raw = path.read_bytes()
    out = []
    for record in normalize(raw, digest(raw))["records"]:
        _, kind, _ = record["source_id"].split("/", 2)
        meta = record["source_metadata"]
        out.append({"id": "x:" + record["source_id"], "kind": kind, "source_id": record["source_id"],
                    "label": meta.get("label") or meta.get("name") or "", "definition": record["definition"],
                    "expression": record["expression"], "source_metadata": meta})
    return out


def metric(defs, name):
    return next(d for d in defs if d["kind"] == "metric" and d["source_id"].endswith("/" + name))


# ---------------------------------------------------------------------------
# Declared one-hop joins (offline compile tests)
# ---------------------------------------------------------------------------

MFG_DEFS = model_defs(MFG_OSSIE)
HEADER_DATE = "Sales_SalesOrderHeader.OrderDate"


def revenue_plan(value="2022-05-30", field=HEADER_DATE):
    return MetricPlan(metric="sales_line_revenue", filters=[Filter(field=field, op="=", value=value)])


def test_date_on_header_compiles_through_the_declared_relationship():
    sql = _compile_sql(metric(MFG_DEFS, "sales_line_revenue"), revenue_plan(), "manufacturing", MFG_DEFS)
    flat = " ".join(sql.split())
    schema = _dataset_schema("manufacturing")
    assert f"FROM {schema}.sales_salesorderdetail JOIN {schema}.sales_salesorderheader ON " in flat
    assert "sales_salesorderdetail.salesorderid = sales_salesorderheader.salesorderid" in flat
    assert "sales_salesorderheader.orderdate" in flat and "'2022-05-30'" in flat
    guard_sql(sql, {"sales_salesorderdetail", "sales_salesorderheader"}, schema)
    # The joined table is not allow-listed unless the compiler names it.
    with pytest.raises(ValueError, match="not in allowed"):
        guard_sql(sql, {"sales_salesorderdetail"}, schema)


def test_join_is_refused_when_the_model_declares_no_relationship():
    # The model declares line-to-product (derived) but no line-to-subcategory join, so none may be inferred.
    plan = revenue_plan(value="Mountain", field="Production_ProductSubcategory.Name")
    with pytest.raises(ClarificationNeeded, match="declare"):
        _compile_sql(metric(MFG_DEFS, "sales_line_revenue"), plan, "manufacturing", MFG_DEFS)


def test_derived_line_to_product_join_compiles_and_is_marked_derived():
    plan = revenue_plan(value="Mountain-100 Silver, 38", field="Production_Product.Name")
    flat = " ".join(_compile_sql(metric(MFG_DEFS, "sales_line_revenue"), plan, "manufacturing", MFG_DEFS).split())
    assert "JOIN v2_facts_manufacturing.production_product ON sales_salesorderdetail.productid = production_product.productid" in flat
    [rel] = [r for r in MFG_DEFS if r["kind"] == "relationship" and r["source_metadata"]["from"] == "Sales_SalesOrderDetail"
             and r["source_metadata"]["to"] == "Production_Product"]
    data = json.loads(next(e["data"] for e in rel["source_metadata"]["custom_extensions"] if e["vendor_name"] == "HELIOS"))
    assert data["derived"] is True and "SpecialOfferProduct" in data["justification"]


def test_join_is_refused_in_the_one_to_many_direction():
    # Header to detail is the reverse of the declared many-to-one relationship and would fan out.
    plan = MetricPlan(metric="sales_order_count", filters=[
        Filter(field="Sales_SalesOrderDetail.OrderQty", op="=", value="1")])
    with pytest.raises(ClarificationNeeded, match="declare"):
        _compile_sql(metric(MFG_DEFS, "sales_order_count"), plan, "manufacturing", MFG_DEFS)


def test_join_is_refused_when_the_target_key_is_not_unique():
    defs = copy.deepcopy(MFG_DEFS)
    for item in defs:
        if item["kind"] == "relationship" and item["source_metadata"]["from"] == "Sales_SalesOrderDetail" \
                and item["source_metadata"]["to"] == "Sales_SalesOrderHeader":
            item["source_metadata"]["to_columns"] = ["CustomerID"]
            item["source_metadata"]["from_columns"] = ["ProductID"]
    with pytest.raises(ClarificationNeeded, match="fan"):
        _compile_sql(metric(defs, "sales_line_revenue"), revenue_plan(), "manufacturing", defs)


def test_injection_through_the_join_path_is_refused_or_neutralised():
    defs = MFG_DEFS
    schema = _dataset_schema("manufacturing")
    base = metric(defs, "sales_line_revenue")
    allowed = {"sales_salesorderdetail", "sales_salesorderheader"}
    # 1. A quote in the value stays one string literal.
    evil = "2022-05-30' OR '1'='1"
    sql = _compile_sql(base, revenue_plan(value=evil), "manufacturing", defs)
    import sqlglot
    import sqlglot.expressions as sge
    guard_sql(sql, allowed, schema)
    literals = [node.this for node in sqlglot.parse_one(sql).find_all(sge.Literal) if node.is_string]
    assert evil in literals
    # 2. A hostile field name never reaches the SQL text.
    for field in (HEADER_DATE + ") OR (1=1", "Sales_SalesOrderHeader.OrderDate; DROP TABLE x", "x.y.z",
                  "Sales_SalesOrderHeader.NoSuchColumn"):
        with pytest.raises(ValueError):
            _compile_sql(base, revenue_plan(field=field), "manufacturing", defs)
    # 3. A hostile operator never reaches the SQL text.
    with pytest.raises(ValueError):
        _compile_sql(base, MetricPlan(metric="x", filters=[
            Filter(field=HEADER_DATE, op="= '1' OR 1 =", value="1")]), "manufacturing", defs)
    # 4. A tampered predicate is caught by the guard's content checks and a stolen join by the table allow-list.
    good = _compile_sql(base, revenue_plan(), "manufacturing", defs)
    tampered = good.replace("LIMIT", "OR 1=1\nLIMIT")
    with pytest.raises(ValueError, match="comparison"):
        guard_sql(tampered, allowed, schema)
    stolen = good.replace("JOIN " + schema + ".sales_salesorderheader", "JOIN " + schema + ".person_password")
    with pytest.raises(ValueError, match="not in allowed"):
        guard_sql(stolen, allowed, schema)


# ---------------------------------------------------------------------------
# G9: dead concept path and the unused constraints file
# ---------------------------------------------------------------------------

def test_mapped_evidence_imports_only_names_that_exist():
    package = ROOT / "semantic_pipeline_v2"
    tree = ast.parse((package / "mapped_evidence.py").read_text())
    concept_tree = ast.parse((package / "concept_mapping.py").read_text())
    defined = {n.name for n in ast.walk(concept_tree) if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "concept_mapping":
            for alias in node.names:
                assert alias.name in defined, f"mapped_evidence imports missing {alias.name}"


def test_rerank_has_no_unused_concept_catalog_argument():
    source = (ROOT / "semantic_pipeline_v2/mapped_evidence.py").read_text()
    assert "resolve_concept" not in source and "write_concept_edges" not in source


def test_one_constraints_file_with_a_correct_docstring():
    package = ROOT / "semantic_pipeline_v2"
    present = sorted(p.name for p in package.glob("concept*constraints.json"))
    assert present == ["concept_constraints.json"]
    source = (package / "mapped_evidence.py").read_text()
    assert "concept_mapping_constraints.json" not in source
    assert source.count("concept_constraints.json") >= 2  # docstring and the loader agree


# ---------------------------------------------------------------------------
# Loud loading (T28) with a fake database: no PostgreSQL needed
# ---------------------------------------------------------------------------

class RecordingPg:
    def __init__(self, inner=None):
        self.inner, self.scripts = inner, []

    def sql(self, script, **params):
        self.scripts.append(script)
        return self.inner.sql(script, **params) if self.inner else ""


def test_unreadable_file_fails_loading_and_names_the_file(tmp_path):
    from semantic_pipeline_v2.metric_sql import ProjectionLoadError, ensure_projections_loaded
    (tmp_path / "Good.csv").write_text("a,b\n1,2\n")
    (tmp_path / "Broken.csv").write_bytes(b"a,b\n1,\xff\xfe\x00\n")
    with pytest.raises(ProjectionLoadError, match="Broken.csv"):
        ensure_projections_loaded(RecordingPg(), "loadtest", tmp_path)


def test_permission_denied_file_fails_loading_and_names_the_file(tmp_path):
    from semantic_pipeline_v2.metric_sql import ProjectionLoadError, ensure_projections_loaded
    blocked = tmp_path / "Locked.csv"
    blocked.write_text("a\n1\n")
    blocked.chmod(0)
    try:
        if os.access(blocked, os.R_OK):
            pytest.skip("running with privileges that ignore file modes")
        with pytest.raises(ProjectionLoadError, match="Locked.csv"):
            ensure_projections_loaded(RecordingPg(), "loadtest", tmp_path)
    finally:
        blocked.chmod(0o600)


def test_hostile_header_is_rejected_before_any_ddl(tmp_path):
    from semantic_pipeline_v2.metric_sql import ProjectionLoadError, ensure_projections_loaded
    (tmp_path / "Bad.csv").write_text('id,"x int); DROP TABLE y; --"\n1,2\n')
    fake = RecordingPg()
    with pytest.raises(ProjectionLoadError, match="Bad.csv"):
        ensure_projections_loaded(fake, "loadtest", tmp_path)
    assert not any("DROP TABLE y" in script for script in fake.scripts)


# ---------------------------------------------------------------------------
# Synthetic fixtures in a private PostgreSQL (T27, T28)
# ---------------------------------------------------------------------------

BATCH = "synthbatch000000000000000000000000000000000000000000000000000000ab"
D1, D2 = "2026-03-01", "2026-03-02"
FIN_DATASET, MFG_DATASET = "financial-services-synthetic", "manufacturing-synthetic"

FIN_FILES = {
    "Customers": [
        "SourceBatch,BusinessDate,CustomerID,CustomerType,IndustryCode,RiskRating",
        f"{BATCH},{D1},C1,Corporate,I1,High", f"{BATCH},{D1},C2,Corporate,I1,High",
        f"{BATCH},{D1},C3,Retail,I2,Low", f"{BATCH},{D1},C4,Retail,I2,Medium",
        f"{BATCH},{D2},C1,Corporate,I1,High", f"{BATCH},{D2},C2,Corporate,I1,Low",
        f"{BATCH},{D2},C5,Retail,I2,Low",
    ],
    "Accounts": [
        "SourceBatch,BusinessDate,AccountNumber,Status,ProductType,BranchCode,Currency",
        f"{BATCH},{D1},A1,Active,Savings,B1,SGD", f"{BATCH},{D1},A1,Active,Savings,B1,SGD",
        f"{BATCH},{D1},A2,Active,Current,B1,SGD", f"{BATCH},{D1},A3,Closed,Current,B2,SGD",
    ],
    "CASA_Txn": [
        "SourceBatch,BusinessDate,TransactionID,Amount,Currency,Channel,BranchCode",
        f"{BATCH},{D1},T1,100.50,SGD,Online,B1", f"{BATCH},{D1},T2,60000,SGD,Online,B1",
        f"{BATCH},{D1},T3,75000.25,USD,Branch,B2", f"{BATCH},{D1},T4,20,SGD,Branch,B2",
        f"{BATCH},{D2},T5,999,SGD,Online,B1",
    ],
    "CreditCard_Txn": [
        "SourceBatch,BusinessDate,TransactionID,Amount,Currency,Channel,BranchCode",
        f"{BATCH},{D1},K1,10.25,USD,Online,B1", f"{BATCH},{D1},K2,5,USD,Online,B1",
        f"{BATCH},{D1},K3,7,SGD,Online,B1",
    ],
}
MFG_FILES = {
    "Sales_SalesOrderHeader": [
        "SalesOrderID,OrderDate,DueDate,ShipDate",
        "1,2024-03-01 00:00:00.000,2024-03-10 00:00:00.000,2024-03-08 00:00:00.000",
        "2,2024-03-01 00:00:00.000,2024-03-10 00:00:00.000,2024-03-12 00:00:00.000",
        "3,2024-03-02 00:00:00.000,2024-03-12 00:00:00.000,2024-03-15 00:00:00.000",
        "4,2024-03-02 00:00:00.000,2024-03-12 00:00:00.000,2024-03-11 00:00:00.000",
    ],
    "Sales_SalesOrderDetail": [
        "SalesOrderID,SalesOrderDetailID,OrderQty,UnitPrice,LineTotal",
        "1,1,2,10.00,20.00", "1,2,1,5.50,5.50", "2,3,3,7.25,21.75", "3,4,4,2.50,10.00", "4,5,1,100.00,100.00",
    ],
    "Production_WorkOrder": [
        "WorkOrderID,OrderQty,ScrappedQty,StartDate,EndDate",
        "1,100,2,2024-03-01 00:00:00.000,2024-03-05 00:00:00.000",
        "2,50,0,2024-03-01 00:00:00.000,",
        "3,200,5,2024-03-02 00:00:00.000,",
        "4,10,1,2024-03-02 00:00:00.000,2024-03-06 00:00:00.000",
    ],
    "Production_ProductInventory": [
        "ProductID,LocationID,Quantity", "1,1,10", "2,1,5", "2,6,7",
    ],
}


def write_files(directory: Path, files: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for name, lines in files.items():
        (directory / f"{name}.csv").write_text("\n".join(lines) + "\n")
    return directory


@pytest.fixture(scope="module")
def synthetic_pg(tmp_path_factory):
    if not PG_BIN:
        pytest.skip("set V2_PG_BIN to run synthetic metric checks")
    from semantic_pipeline_v2._pilot_postgres import PilotPostgres
    from semantic_pipeline_v2.metric_sql import ensure_projections_loaded
    from semantic_pipeline_v2.semantic_store import SemanticStore

    state = tmp_path_factory.mktemp("synthetic_pg")
    with PilotPostgres.start(Path(PG_BIN), data_directory=state / "pgdata") as pg:
        store = SemanticStore(pg)
        store.import_ossie(FIN_DATASET, FINANCE_OSSIE)
        store.import_ossie(MFG_DATASET, MFG_OSSIE)
        ensure_projections_loaded(pg, FIN_DATASET, write_files(state / "fin", FIN_FILES))
        ensure_projections_loaded(pg, MFG_DATASET, write_files(state / "mfg", MFG_FILES))
        yield pg, state


def workspace(dataset):
    return {"id": dataset, "knowledge": {"structured": {"modelSource": "m"}},
            "sources": [{"id": "m", "kind": "structured-model", "citation": {"sha256": "modelrev"}},
                        {"id": "w", "kind": "workbook", "citation": {"sha256": BATCH}}]}


def sqlite_of(files: dict) -> sqlite3.Connection:
    """Independent reference engine: the same rows loaded as text into sqlite."""
    db = sqlite3.connect(":memory:")
    for name, lines in files.items():
        rows = list(csv.reader(lines))
        db.execute(f'CREATE TABLE "{name}" ({", ".join(chr(34) + c + chr(34) for c in rows[0])})')
        db.executemany(f'INSERT INTO "{name}" VALUES ({",".join("?" * len(rows[0]))})',
                       [[v if v != "" else None for v in r] for r in rows[1:]])
    return db


def one(db, sql, *args):
    return db.execute(sql, args).fetchone()[0]


def finance_cases():
    db = sqlite_of(FIN_FILES)
    return [
        ("customers_on_business_date", f"How many customers were there on {D1}?",
         one(db, "select count(distinct CustomerID) from Customers where BusinessDate=?", D1)),
        ("distinct_customers_in_period", f"How many distinct customers appeared between {D1} and {D2}?",
         one(db, "select count(distinct CustomerID) from Customers where BusinessDate between ? and ?", D1, D2)),
        ("active_accounts", f"How many active accounts were there on {D1}?",
         one(db, "select count(distinct AccountNumber) from Accounts where Status='Active' and BusinessDate=?", D1)),
        ("casa_transaction_value", f"What is the total CASA transaction value in SGD on {D1}?",
         one(db, "select sum(cast(Amount as real)) from CASA_Txn where Currency='SGD' and BusinessDate=?", D1)),
        ("casa_transaction_count", f"What is the distinct transaction count on {D1}?",
         one(db, "select count(distinct TransactionID) from CASA_Txn where BusinessDate=?", D1)),
        ("credit_card_transaction_value", f"What is the total credit card transaction value in USD on {D1}?",
         one(db, "select sum(cast(Amount as real)) from CreditCard_Txn where Currency='USD' and BusinessDate=?", D1)),
        ("large_casa_transaction_count", f"What is the large CASA transaction count on {D1}?",
         one(db, "select count(*) from CASA_Txn where cast(Amount as real) > 50000 and BusinessDate=?", D1)),
        ("high_risk_customers", f"How many high risk customers on {D1}?",
         one(db, "select count(*) from Customers where RiskRating='High' and BusinessDate=?", D1)),
    ]


def mfg_cases():
    db = sqlite_of(MFG_FILES)
    day = "2024-03-01"
    return [
        ("sales_order_count", f"What is the total sales order count on {day}?",
         one(db, "select count(distinct SalesOrderID) from Sales_SalesOrderHeader where substr(OrderDate,1,10)=?", day)),
        ("sales_ordered_units", f"How many units were ordered in total on {day}?",
         one(db, "select sum(d.OrderQty) from Sales_SalesOrderDetail d join Sales_SalesOrderHeader h "
                 "on h.SalesOrderID=d.SalesOrderID where substr(h.OrderDate,1,10)=?", day)),
        ("sales_line_revenue", f"What is the total sales line revenue on {day}?",
         one(db, "select sum(cast(d.LineTotal as real)) from Sales_SalesOrderDetail d join Sales_SalesOrderHeader h "
                 "on h.SalesOrderID=d.SalesOrderID where substr(h.OrderDate,1,10)=?", day)),
        ("average_unit_price", f"What is the average unit price on {day}?",
         one(db, "select avg(cast(d.UnitPrice as real)) from Sales_SalesOrderDetail d join Sales_SalesOrderHeader h "
                 "on h.SalesOrderID=d.SalesOrderID where substr(h.OrderDate,1,10)=?", day)),
        ("late_dispatch_orders", f"How many late dispatch orders are there on {day}?",
         one(db, "select count(*) from Sales_SalesOrderHeader where ShipDate > DueDate and substr(OrderDate,1,10)=?", day)),
        ("production_requested_units", f"How many units were requested on work orders on {day}?",
         one(db, "select sum(OrderQty) from Production_WorkOrder where substr(StartDate,1,10)=?", day)),
        ("production_scrapped_units", f"How many units were scrapped on work orders on {day}?",
         one(db, "select sum(ScrappedQty) from Production_WorkOrder where substr(StartDate,1,10)=?", day)),
        ("production_scrap_rate", f"What is the production scrap rate on {day}?",
         one(db, "select sum(ScrappedQty)*1.0/sum(OrderQty) from Production_WorkOrder where substr(StartDate,1,10)=?", day)),
        ("open_work_orders", f"How many open work orders are there on {day}?",
         one(db, "select count(*) from Production_WorkOrder where EndDate is null and substr(StartDate,1,10)=?", day)),
        ("inventory_units", "How many units are in inventory?",
         one(db, "select sum(Quantity) from Production_ProductInventory")),
    ]


def test_every_finance_metric_matches_a_nonzero_independent_reference(synthetic_pg):
    pg, _ = synthetic_pg
    names = []
    for name, question, expected in finance_cases():
        assert expected, f"reference for {name} must be non-zero"
        fact = query_metric(workspace(FIN_DATASET), question, pg)
        assert fact is not None, f"{name}: no answer for {question!r}"
        assert math.isclose(float(fact["value"]), float(expected), rel_tol=1e-9), (name, fact["value"], expected)
        assert fact["definitionId"].endswith("/" + name), (name, fact["definitionId"])
        names.append(name)
    model = {d["source_id"].rsplit("/", 1)[1] for d in model_defs(FINANCE_OSSIE) if d["kind"] == "metric"}
    # Output C metrics read form rows, not workbook CSVs; test_physical_mapping.py exercises them.
    model -= {"reported_suspicious_transactions", "reported_suspicious_transaction_value"}
    assert set(names) == model, "every finance metric must be exercised"


def test_every_manufacturing_metric_matches_a_nonzero_independent_reference(synthetic_pg):
    pg, _ = synthetic_pg
    names = []
    for name, question, expected in mfg_cases():
        assert expected, f"reference for {name} must be non-zero"
        fact = query_metric(workspace(MFG_DATASET), question, pg)
        assert fact is not None, f"{name}: no answer for {question!r}"
        assert math.isclose(float(fact["value"]), float(expected), rel_tol=1e-6), (name, fact["value"], expected)
        assert fact["definitionId"].endswith("/" + name), (name, fact["definitionId"])
        names.append(name)
    model = {d["source_id"].rsplit("/", 1)[1] for d in model_defs(MFG_OSSIE) if d["kind"] == "metric"}
    assert set(names) == model, "every manufacturing metric must be exercised"


# --- T28 against a real database --------------------------------------------------

def column_types(pg, schema, table):
    rows = pg.sql(f"SELECT column_name || ':' || data_type FROM information_schema.columns "
                  f"WHERE table_schema='{schema}' AND table_name='{table}' ORDER BY ordinal_position;")
    return dict(line.split(":", 1) for line in rows.splitlines())


def test_late_value_is_not_mistyped_and_load_uses_the_client_connection(synthetic_pg):
    from semantic_pipeline_v2.metric_sql import ensure_projections_loaded
    pg, state = synthetic_pg
    directory = state / "typed"
    directory.mkdir()
    lines = ["id,qty,code,day,stamp"]
    for i in range(1, 31):
        qty = "N/A" if i == 26 else str(i)          # numeric for 25 rows, then text
        lines.append(f"{i},{qty},{'007' if i == 1 else i},2026-01-{i:02d},2026-01-{i:02d} 00:00:00.000")
    (directory / "Typed.csv").write_text("\n".join(lines) + "\n")
    recorder = RecordingPg(pg)
    ensure_projections_loaded(recorder, "typedtest", directory)
    types = column_types(pg, "v2_facts_typedtest", "typed")
    assert types["id"] == "numeric"
    assert types["qty"] == "text", "a value after row 20 must decide the type"
    assert types["code"] == "text", "a zero-padded identifier must not become a number"
    assert types["day"] == "date" and types["stamp"].startswith("timestamp")
    assert pg.sql("SELECT count(*) FROM v2_facts_typedtest.typed WHERE qty = 'N/A';") == "1"
    assert pg.sql("SELECT count(*) FROM v2_facts_typedtest.typed;") == "30"
    joined = "\n".join(recorder.scripts)
    assert "FROM STDIN" in joined and str(directory) not in joined, "the file must not need to be on the database host"


def test_failed_copy_fails_loading_names_the_file_and_leaves_nothing_behind(synthetic_pg):
    from semantic_pipeline_v2.metric_sql import ProjectionLoadError, ensure_projections_loaded
    pg, state = synthetic_pg
    directory = state / "ragged"
    directory.mkdir()
    (directory / "Ragged.csv").write_text("a,b\n1,2\n3,4,5\n")
    with pytest.raises(ProjectionLoadError, match="Ragged.csv"):
        ensure_projections_loaded(pg, "raggedtest", directory)
    assert pg.sql("SELECT count(*) FROM v2_projection_load WHERE dataset='raggedtest';") == "0"
    assert pg.sql("SELECT count(*) FROM information_schema.tables WHERE table_schema='v2_facts_raggedtest';") == "0"



# ---------------------------------------------------------------------------
# Natural dates and the remaining Talk to Data defects (T10)
# ---------------------------------------------------------------------------

def test_natural_dates_are_read_and_impossible_dates_are_left_alone():
    from semantic_pipeline_v2.metric_sql import extract_dates
    assert extract_dates("on 6 Nov 2026 and Nov 7, 2026")[0] == ["2026-11-06", "2026-11-07"]
    assert extract_dates("as of 2026-11-06")[0] == ["2026-11-06"]
    assert extract_dates("on 31 February 2026")[0] == []
    assert "2026" not in extract_dates("count for 6 November 2026")[1]


class _Plan:
    available = True

    def __init__(self, payload):
        self.payload = payload

    def plan(self, combined, options):
        return self.payload


def test_unknown_filter_value_asks_for_clarification_and_names_known_values(synthetic_pg):
    import json
    pg, _ = synthetic_pg
    payload = json.dumps({"metric": "casa_transaction_count", "filters": [
        {"field": "BusinessDate", "op": "=", "value": D1}, {"field": "Channel", "op": "=", "value": "Carrier pigeon"}]})
    with pytest.raises(ClarificationNeeded, match="Carrier pigeon") as caught:
        query_metric(workspace(FIN_DATASET), "casa count", pg, interpreter=_Plan(payload))
    assert {"Online", "Branch"} <= caught.value.known
    good = json.dumps({"metric": "casa_transaction_count", "filters": [
        {"field": "BusinessDate", "op": "=", "value": D1}, {"field": "SourceBatch", "op": "=", "value": BATCH},
        {"field": "Channel", "op": "=", "value": "Online"}]})
    fact = query_metric(workspace(FIN_DATASET), "casa count", pg, interpreter=_Plan(good))
    assert fact["value"] == 2, "T1 and T2 were on the Online channel on the first day"


def test_a_plan_that_carries_sql_in_a_field_is_refused_without_reaching_the_database(synthetic_pg):
    import json
    pg, _ = synthetic_pg
    payload = json.dumps({"metric": "casa_transaction_count", "filters": [
        {"field": "Channel = 'x' OR 1=1 --", "op": "=", "value": "x"}]})
    recorder = RecordingPg(pg)
    try:
        fact = query_metric(workspace(FIN_DATASET), "casa count", recorder, interpreter=_Plan(payload))
    except ValueError:
        fact = None
    assert fact is None
    assert not any("OR 1=1" in script for script in recorder.scripts)
