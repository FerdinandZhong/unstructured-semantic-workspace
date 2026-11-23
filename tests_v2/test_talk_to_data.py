"""K3/K4 acceptance for scoped local interpretation and Spark draft preparation."""

import ast
import io
import json
import os
import re
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

from semantic_pipeline_v2.access import project_workspace
from semantic_pipeline_v2.talk_to_data import MistralInterpretation, TalkError, TalkRequest, respond
from semantic_pipeline_v2.workspace import load_workspace, review_database
from test_workspace import running_server


def scoped(dataset, denied=()):
    prepared = {"datasets": [load_workspace(dataset)], "reviewAvailable": False}
    principal = {"datasets": [dataset], "withheld": list(denied), "role": "restricted"}
    return project_workspace(prepared, principal)["datasets"][0]


_PG_BIN = os.environ.get("V2_PG_BIN")
_ROOT = Path(__file__).resolve().parents[1]
_FINANCE_CSV = _ROOT / "demo_data/finance/local/finance_workbook"
_FINANCE_OSSIE = _ROOT / "demo_data/finance/finance.ossie.yaml"


def test_customer_count_via_metric_sql(tmp_path):
    """T9: customer count via compiled metric SQL — requires real PostgreSQL."""
    if not _PG_BIN:
        pytest.skip("set V2_PG_BIN to exercise metric SQL customer count")
    from semantic_pipeline_v2._pilot_postgres import PilotPostgres
    from semantic_pipeline_v2.semantic_store import SemanticStore
    from semantic_pipeline_v2.metric_sql import ensure_projections_loaded

    with PilotPostgres.start(Path(_PG_BIN), data_directory=tmp_path / "pgdata") as pg:
        SemanticStore(pg).import_ossie("financial-services", _FINANCE_OSSIE)
        ensure_projections_loaded(pg, "financial-services", _FINANCE_CSV)
        workspace = scoped("financial-services")
        reply = respond(workspace, TalkRequest("financial-services",
                        "How many customers were there on 2026-11-06?"), database=pg)
        assert reply["status"] == "grounded", f"status={reply['status']}: {reply['summary']}"
        assert reply["facts"][0]["value"] == 51
        assert reply["facts"][0]["batchHash"]
        assert "SELECT" in reply["facts"][0]["sql"].upper()
        # Follow-up keeps context via _conversational_question.
        followup = TalkRequest("financial-services", "What about 2026-11-04?", history=(
            {"role": "user", "text": "How many customers were there on 2026-11-06?"},
        ))
        followup_reply = respond(workspace, followup, database=pg)
        assert followup_reply["facts"][0]["value"] == 50


def test_opaque_datatype_gives_partial_status():
    """Proposal with an Opaque-typed field must yield partial status when admin=True."""
    workspace = scoped("manufacturing")
    # Monkeypatch _spark_draft to return None (simulates Opaque dtype).
    import semantic_pipeline_v2.talk_to_data as ttd
    original = ttd._spark_draft
    try:
        ttd._spark_draft = lambda dataset, proposal: None
        query = TalkRequest("manufacturing",
                            "Build a dataset with ordered quantity on sales order lines", "proposal")
        result = respond(workspace, query, admin=True)
        assert result["status"] == "partial", f"expected partial, got {result['status']}"
        assert result["sparkDraft"] is None
    finally:
        ttd._spark_draft = original


def test_manufacturing_candidates_exclude_withheld_contract():
    name = "manufacturing"
    workspace = scoped(name, [name + ":source:MFG-003"])
    reply = respond(workspace, TalkRequest(name, "What is the promised delivery date for order 51131?"))
    assert reply["status"] == "grounded"
    assert reply["evidence"]
    assert all(item["sourceId"] != name + ":source:MFG-003" for item in reply["evidence"])
    assert reply["facts"] == []
    assert "Product order schedule" in reply["summary"]
    assert "promised delivery date for SalesOrderID 51131 is 2024-06-10" in reply["summary"]
    assert "2024-06-17" not in reply["summary"]


def test_competing_definitions_and_separate_promises_are_explicit():
    workspace = scoped("manufacturing")
    reply = respond(workspace, TalkRequest("manufacturing", "What does OrderQty mean?"))
    assert reply["status"] == "clarify"
    assert "Sales_SalesOrderDetail/OrderQty" in reply["summary"]
    assert "Purchasing_PurchaseOrderDetail/OrderQty" in reply["summary"]
    explicit = respond(workspace, TalkRequest("manufacturing", "What does Sales_SalesOrderDetail/OrderQty mean?"))
    assert explicit["status"] == "grounded"
    assert "The scoped model defines Sales_SalesOrderDetail/OrderQty" in explicit["summary"]
    evidence = respond(workspace, TalkRequest("manufacturing", "What is the promised delivery date for order 51131?"))
    assert "2024-06-10" in evidence["summary"] and "2024-06-17" in evidence["summary"]
    assert "does not resolve the effective version" in evidence["summary"]


def test_grounded_spark_draft_is_admin_only_and_unbound():
    name = "manufacturing"
    workspace = scoped(name)
    query = TalkRequest(name, "Build a dataset with ordered quantity on sales order lines", "proposal")
    viewer = respond(workspace, query)
    assert viewer["proposal"]["table"] == "Sales_SalesOrderDetail"
    assert viewer["sparkDraft"] is None
    admin = respond(workspace, query, admin=True)
    draft = admin["sparkDraft"]
    assert draft["source"]["table"] == "Sales_SalesOrderDetail"
    assert draft["target"]["status"] == "unbound"
    ast.parse(draft["code"])
    assert 'mode("errorifexists")' in draft["code"] and "eval(" not in draft["code"]
    assert "OrderQty" in draft["code"]
    ambiguous = respond(workspace, TalkRequest(name, "Build an ordered quantity dataset", "proposal"), admin=True)
    assert ambiguous["status"] == "clarify" and ambiguous["sparkDraft"] is None


def test_unrelated_request_has_no_invented_plan():
    workspace = scoped("financial-services")
    reply = respond(workspace, TalkRequest("financial-services", "Forecast the temperature on Mars", "proposal"), admin=True)
    assert reply["status"] == "unsupported"
    assert reply["proposal"] is None and reply["sparkDraft"] is None


def test_mistral_only_reranks_known_metadata_and_rejects_invented_indexes():
    captured = []

    def opener(output):
        def open_request(request, *, timeout):
            captured.append(json.loads(request.data))
            return io.BytesIO(json.dumps({"choices": [{"message": {"content": json.dumps(output)}}]}).encode())
        return SimpleNamespace(open=open_request)

    workspace = scoped("manufacturing", ["manufacturing:source:MFG-003"])
    request = TalkRequest("manufacturing", "What is the promised delivery date for order 51131?",
                          interpretation="mistral")
    reply = respond(workspace, request, interpreter=MistralInterpretation("fixture", opener=opener({"indexes": ["0"]})))
    assert reply["interpretation"] == "mistral" and len(reply["definitions"]) == 1
    sent = captured[0]["messages"][-1]["content"]
    assert "2024-06-17" not in sent and '"question"' in sent
    with pytest.raises(TalkError, match="valid scoped"):
        respond(workspace, request, interpreter=MistralInterpretation("fixture", opener=opener({"indexes": ["999"]})))


def test_generated_pyspark_project_calls_typed_projection_without_truncating(monkeypatch):
    workspace = scoped("manufacturing")
    request = TalkRequest("manufacturing", "Build a dataset with ordered quantity on sales order lines", "proposal")
    draft = respond(workspace, request, admin=True)["sparkDraft"]
    selections = []

    class Column:
        def __init__(self, name):
            self.name = name

        def cast(self, kind):
            self.kind = kind
            return self

        def alias(self, name):
            assert name == self.name
            return self

    def select(*columns):
        selections.extend((column.name, column.kind) for column in columns)
        return frame

    frame = SimpleNamespace(columns=["SalesOrderID", "SalesOrderDetailID", "OrderQty", "ProductID"], select=select)
    reader = SimpleNamespace()
    reader.option = lambda name, value: reader
    reader.csv = lambda source: frame
    spark = SimpleNamespace(read=reader)
    package, sql = ModuleType("pyspark"), ModuleType("pyspark.sql")
    sql.SparkSession = SimpleNamespace()
    sql.functions = SimpleNamespace(col=Column)
    monkeypatch.setitem(sys.modules, "pyspark", package)
    monkeypatch.setitem(sys.modules, "pyspark.sql", sql)
    scope = {"__name__": "generated_draft"}
    exec(compile(draft["code"], "<generated_draft>", "exec"), scope)
    assert scope["project"](spark, "file:///approved.csv") is frame
    assert ("OrderQty", "long") in selections
    frame.columns.remove("OrderQty")
    with pytest.raises(ValueError, match="Missing modeled input columns"):
        scope["project"](spark, "file:///approved.csv")


def test_http_permissions_for_agent_and_optional_mistral(tmp_path):
    pg_bin = os.environ.get("V2_PG_BIN")
    if not pg_bin:
        pytest.skip("set V2_PG_BIN to exercise hosted agent permissions")
    offline = MistralInterpretation()
    body = {"dataset": "manufacturing", "question": "Build a dataset with ordered quantity on sales order lines",
            "mode": "proposal"}
    with review_database(tmp_path / "state", Path(pg_bin)) as database, running_server(
        database, talk_interpretation=offline,
    ) as call:
        assert call("GET", "/api/talk-to-data") == (200, {"mistralAvailable": False})
        status, result = call("POST", "/api/talk-to-data", body)
        assert status == 200 and result["sparkDraft"]
        assert result["ontology"][0]["title"] == "Ordered quantity"
        assert any("Dispatch and receipt require separate evidence" in item for item in result["proposal"]["limitations"])
        assert call("POST", "/api/talk-to-data", {**body, "interpretation": "mistral"})[0] == 503
        status, _, headers = call("POST", "/api/session", {"userId": "finance_restricted"}, include_headers=True)
        assert status == 200
        cookie = "; ".join(value.split(";", 1)[0] for name, value in headers if name == "Set-Cookie")
        assert call("POST", "/api/talk-to-data", body, {"Cookie": cookie})[0] == 403
        status, result = call("POST", "/api/talk-to-data", {
            "dataset": "financial-services", "question": "What does the customer risk rating definition mean?",
        }, {"Cookie": cookie})
        assert status == 200 and result["sparkDraft"] is None
        assert all("jade" not in evidence["sourceId"] for evidence in result["evidence"])
        assert result["ontology"][0]["title"] == "Customer risk rating"
        assert "rating scale" in result["ontology"][0]["limit"]


TALK_SOURCE = Path(__file__).resolve().parents[1] / "semantic_pipeline_v2/talk_to_data.py"


def test_talk_to_data_holds_no_dataset_specific_rules():
    """T29: no dataset id, dataset phrase or word list in the source; meaning comes from model metadata."""
    tree = ast.parse(TALK_SOURCE.read_text())
    strings = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    banned = ("manufacturing", "financial", "finance", "sales order", "purchase order", "work order",
              "deposit", "balance", "salesorder", "demo_data/manufacturing")
    for text in strings:
        lowered = text.lower()
        assert not any(term in lowered for term in banned), f"dataset-specific term in {text!r}"
        assert not re.fullmatch(r"[a-z]+(?: [a-z]+){7,}", text), f"word list in source: {text[:40]!r}"
    assert not re.search(r"workspace\[.id.\]\s*==", TALK_SOURCE.read_text()), "branch on a dataset id"


def test_table_qualifiers_come_from_model_metadata_not_regexes():
    workspace = scoped("manufacturing")
    expected = {
        "Build a dataset with ordered quantity on sales order lines": "Sales_SalesOrderDetail",
        "Build a dataset with ordered quantity on purchase order lines": "Purchasing_PurchaseOrderDetail",
        "Build a dataset with ordered quantity on work orders": "Production_WorkOrder",
    }
    for question, table in expected.items():
        reply = respond(workspace, TalkRequest("manufacturing", question, "proposal"), admin=True)
        assert reply["status"] == "grounded", (question, reply["summary"])
        assert reply["proposal"]["table"] == table, (question, reply["proposal"] and reply["proposal"]["table"])
    unqualified = respond(workspace, TalkRequest("manufacturing", "Build an ordered quantity dataset", "proposal"), admin=True)
    assert unqualified["status"] == "clarify" and unqualified["proposal"] is None


def test_other_dataset_ordered_quantity_wording_is_not_special_cased():
    """The finance workspace has no ordered quantity; no manufacturing wording may leak into its answers."""
    reply = respond(scoped("financial-services"),
                    TalkRequest("financial-services", "Build a dataset with ordered quantity", "proposal"))
    assert "sales order lines" not in reply["summary"].lower()


def test_unsafe_sql_is_refused_not_executed():
    """T10: a plan whose filter field carries SQL is refused; nothing reaches the database."""
    from semantic_pipeline_v2.metric_sql import MetricPlan, Filter, _compile_sql
    from test_metric_sql_second_pass import MFG_DEFS, metric
    plan = MetricPlan(metric="sales_order_count", filters=[
        Filter(field="SalesOrderID = 1; DROP TABLE x; --", op="=", value="1")])
    with pytest.raises(ValueError):
        _compile_sql(metric(MFG_DEFS, "sales_order_count"), plan, "manufacturing", MFG_DEFS)
