"""Path 1 (C3): content-as-prompt mapping. Tests T10 to T12, cards, recorded embeddings, server wiring.

Vectors come from the recorded mistral-embed fixture in tests_v2/fixtures/embeddings. A text that was never
recorded raises; no test substitutes a fake vector for a missing one.
"""

import json
from pathlib import Path
from urllib.parse import urlencode

import numpy as np
import pytest

from semantic_pipeline_v2 import workspace_server
from semantic_pipeline_v2.definition_cards import load_cards
from semantic_pipeline_v2.embedding_cache import (CachedEmbedder, EmbeddingCache, EmbeddingMiss,
                                                  RecordedEmbedder)
from semantic_pipeline_v2.evaluate_mapping import ARMS, evaluate
from semantic_pipeline_v2.mapping_corrections import CorrectionContext, overlay_mappings
from semantic_pipeline_v2.path1 import Path1Mapper, compatibility
from semantic_pipeline_v2.workspace import load_workspace
from test_evidence_labeler import FixtureLabeler
from test_workspace import running_server

FIXTURES = Path(__file__).parent / "fixtures"
CACHE = FIXTURES / "embeddings"
DEV = {"financial-services": FIXTURES / "dev" / "path1_dev_finance.jsonl",
       "manufacturing": FIXTURES / "dev" / "path1_dev_manufacturing.jsonl"}
F = "financial-services:definition:finance_workbook_demo/"
M = "manufacturing:definition:adventureworks_manufacturing_demo/"


@pytest.fixture(scope="module")
def workspaces():
    return {name: load_workspace(name) for name in DEV}


@pytest.fixture(scope="module")
def recorded():
    return RecordedEmbedder(EmbeddingCache(CACHE))


def item(text, context="", kind=None):
    return {"text": text, "context": context, "source_kind": kind, "anchor_kind": "native_text"}


# ---------------------------------------------------------------- cards

def test_cards_have_two_variants_and_never_invent_metadata(workspaces):
    cards = load_cards(workspaces["manufacturing"])
    lead = cards[M + "field/Purchasing_ProductVendor/AverageLeadTime"]
    assert lead["definition_only"].startswith("AverageLeadTime")
    assert "Average supplier lead time" in lead["context"] and "lead time" in lead["context"]
    assert "Unit: days" in lead["context"] and "Purchasing_ProductVendor" in lead["context"]
    assert "Unit:" not in cards[M + "field/Production_Product/Color"]["context"]
    assert "grain" not in lead["context"].lower()
    assert len(cards) == len(workspaces["manufacturing"]["knowledge"]["structured"]["fields"]) + len(
        workspaces["manufacturing"]["knowledge"]["structured"]["metrics"])
    assert all(c["definition_only"] and c["context"] for c in cards.values())


# ---------------------------------------------------------------- cache

def test_cache_round_trip_miss_raises_and_substitutes_nothing(tmp_path):
    cache = EmbeddingCache(tmp_path)
    cache.put("space-a", "alpha", [1.0, 0.0, 0.5])
    cache.save(model="m", dimension=3)
    reloaded = EmbeddingCache(tmp_path)
    assert reloaded.get("space-a", "alpha") == pytest.approx([1.0, 0.0, 0.5])
    assert reloaded.get("space-b", "alpha") is None  # keyed by embedding space
    manifest = json.loads((tmp_path / "embeddings.json").read_text())
    assert manifest["model"] == "m" and manifest["dimension"] == 3 and manifest["count"] == 1 and manifest["recorded"]
    replay = RecordedEmbedder(reloaded, space_id="space-a")
    assert replay.embed(["alpha"])[0] == pytest.approx([1.0, 0.0, 0.5])
    with pytest.raises(EmbeddingMiss) as error:
        replay.embed(["alpha", "never recorded"])
    assert "never recorded" in str(error.value) or "1 text" in str(error.value)


def test_cached_embedder_records_misses_with_the_live_embedder_only(tmp_path):
    class Live:
        calls = []

        def embed(self, texts):
            self.calls.append(list(texts))
            return [[float(len(t)), 1.0] for t in texts]

    live = Live()
    embedder = CachedEmbedder(EmbeddingCache(tmp_path), live, space_id="s")
    assert embedder.embed(["ab", "abc"]) == [[2.0, 1.0], [3.0, 1.0]]
    assert embedder.embed(["ab"]) == [[2.0, 1.0]]
    assert live.calls == [["ab", "abc"]]  # the second call is served from the cache


def test_embedder_failure_degrades_to_lexical_and_says_so(workspaces):
    class Down:
        def embed(self, texts):
            raise RuntimeError("provider down")

    mapper = Path1Mapper(workspaces["financial-services"], Down())
    [result] = mapper.map_many([item("Risk rating: High", "Customer profile")])
    assert result["degraded"] == "embedder_failed:RuntimeError"
    assert all(c["legs"] == ["lexical"] for c in result["candidates"])
    [bare] = Path1Mapper(workspaces["financial-services"], None).map_many([item("Risk rating: High")])
    assert bare["degraded"] == "no_embedder"
    with pytest.raises(RuntimeError):
        Path1Mapper(workspaces["financial-services"], Down(), strict=True).map_many([item("Risk rating: High")])


def test_unrecorded_text_degrades_visibly(workspaces, recorded):
    [result] = Path1Mapper(workspaces["financial-services"], recorded).map_many(
        [item("A sentence nobody ever recorded 7f3a9")])
    assert result["degraded"] == "embedder_failed:EmbeddingMiss"


# ---------------------------------------------------------------- T12 compatibility

def test_t12_commitment_versus_actual_same_unit_is_related_not_exact(workspaces):
    cards = load_cards(workspaces["manufacturing"])
    received = cards[M + "field/Purchasing_PurchaseOrderDetail/ReceivedQty"]
    ordered = cards[M + "field/Purchasing_PurchaseOrderDetail/OrderQty"]
    commitment = "Quantity ordered from the supplier: 1,000 units"
    assert compatibility(commitment, ordered)["relation"] == "exact"
    result = compatibility(commitment, received)
    assert result["relation"] == "related" and any("commitment" in r for r in result["reasons"])
    assert result["selectable"] is True


def test_t12_different_unit_is_rejected(workspaces):
    cards = load_cards(workspaces["manufacturing"])
    lead = cards[M + "field/Purchasing_ProductVendor/AverageLeadTime"]  # unit: days
    result = compatibility("Quantity ordered from the supplier: 1,000 units", lead)
    assert result["relation"] == "rejected" and any("unit" in r for r in result["reasons"])
    price = cards[M + "field/Sales_SalesOrderDetail/UnitPrice"]  # currency
    assert compatibility("Weight of the part: 2.4 kg", price)["relation"] == "rejected"


def test_value_type_and_time_basis_are_checked(workspaces):
    cards = load_cards(workspaces["manufacturing"])
    due = cards[M + "field/Sales_SalesOrderHeader/DueDate"]
    assert compatibility("Order quantity: 12", due)["relation"] == "rejected"
    finance = load_cards(workspaces["financial-services"])
    on_date = finance[F + "metric/customers_on_business_date"]
    assert compatibility("Distinct customers across the period: 134", on_date)["relation"] == "rejected"
    assert compatibility("Customers on business date: 51", on_date)["relation"] == "exact"


def test_t12_allegation_is_never_exact_to_the_recorded_risk_rating(workspaces):
    cards = load_cards(workspaces["financial-services"])
    rating = cards[F + "field/Customers/RiskRating"]
    for text in ("Jade Logistics is allegedly linked to suspicious payment flows.",
                 "Risk rating: High, according to unverified reports."):
        result = compatibility(text, rating, constrained=True)
        assert result["relation"] != "exact" and result["selectable"] is False
    adverse = compatibility("Risk rating: High", rating, source_kind="adverse-media", constrained=True)
    assert adverse["relation"] == "rejected"
    assert compatibility("Risk rating: High", rating, constrained=True)["relation"] == "exact"


def test_allegation_is_not_selected_by_the_mapper(workspaces, recorded):
    mapper = Path1Mapper(workspaces["financial-services"], recorded)
    [result] = mapper.map_many([item("Local press reported the firm as a high risk customer.",
                                      "Adverse media article", "adverse-media")])
    assert result["decision"] != "selected"
    assert all(c["relation"] != "exact" for c in result["candidates"]
               if "risk" in c["id"].lower() and c["id"].startswith(F))


# ---------------------------------------------------------------- T11 and T10

def test_t11_paraphrase_with_no_shared_word_reaches_the_shortlist_only_with_vectors(workspaces, recorded):
    ws = workspaces["financial-services"]
    passage = item("Number of people we bank: 51", "Customer snapshot summary")
    target = F + "metric/customers_on_business_date"
    lexical = Path1Mapper(ws, None, arm="lexical").map_many([passage])[0]
    hybrid = Path1Mapper(ws, recorded, arm="hybrid_context").map_many([passage])[0]
    top12 = lambda r: [c["id"] for c in r["candidates"] if c["retrievalRank"] <= 12]  # noqa: E731
    assert target not in top12(lexical)
    assert target in top12(hybrid)
    assert any("vector" in c["legs"] for c in hybrid["candidates"] if c["id"] == target)
    assert hybrid["degraded"] is None and hybrid["embeddingSpace"] and hybrid["cardVariant"] == "context"


def test_t10_evaluation_runs_three_arms_on_the_dev_set(recorded):
    reports = {}
    for arm in ARMS:
        reports[arm] = evaluate([*DEV.values()], arm, CACHE)
        report = reports[arm]
        assert report["arm"] == arm and report["overall"]["n"] == 29
        for scope in (report["overall"], *report["datasets"].values()):
            for key in ("n", "recall_at_12", "selection_precision", "recall", "abstention_accuracy",
                        "false_link_rate", "relation_accuracy", "categories"):
                assert key in scope
        assert {"threshold", "margin", "k"} <= set(report["thresholds"])
        assert bool(report["embedding_space"]) == (arm != "lexical")
    # vectors must add candidates the lexical leg cannot find; they must not lose any
    assert reports["hybrid_context"]["overall"]["recall_at_12"] >= reports["lexical"]["overall"]["recall_at_12"]
    assert reports["hybrid_context"]["overall"]["recall_at_12"] > reports["lexical"]["overall"]["recall_at_12"]


def test_dev_sets_stay_small():
    for path in DEV.values():
        assert len(path.read_text().splitlines()) <= 15


# ---------------------------------------------------------------- server and corrections

def test_ingest_stores_relation_legs_and_a_correction_still_overlays(tmp_path, monkeypatch, recorded):
    dataset = "financial-services"
    source = f"{dataset}:source:jade-adverse-media"
    principal = {"id": "admin", "role": "admin", "datasets": [dataset], "withheld": []}
    monkeypatch.setattr(workspace_server.WorkspaceRequest, "principal", lambda _: principal)
    monkeypatch.setattr(workspace_server, "resolve_session", lambda *_: principal)
    labeler = FixtureLabeler(lambda anchors, definitions: {"labels": []})
    with running_server(evidence_dir=tmp_path / "evidence", evidence_labeler=labeler, chunk_embedder=None,
                        mapping_embedder=recorded) as call:
        status, result = call("POST", "/api/ocr", {"dataset": dataset, "sourceId": source, "mode": "text"})
        assert status == 200 and result["evidenceStatus"] == "saved" and result["mapping"]["degraded"] is None
        mappings = result["evidence"]["mappings"]
        stored = [m for m in mappings if "path1" in m]
        assert stored and all(m["path1"]["degraded"] is None for m in stored)
        first = stored[0]["path1"]
        assert first["embeddingSpace"] and first["cardVariant"] == "context"
        assert first["evidence"]["citation"]["startLine"] and first["thresholds"]
        legs = {leg for m in stored for c in m["path1"]["candidates"] for leg in c["legs"]}
        assert {"lexical", "vector"} <= legs
        assert all(c["relation"] in ("exact", "related", "rejected") for m in stored for c in m["path1"]["candidates"])
        chosen = {m["path1"]["selected"] for m in stored if m["path1"]["selected"]}
        _, read = call("GET", "/api/chunks?" + urlencode({"dataset": dataset, "sourceId": source}))
        assert chosen <= {label for chunk in read["chunks"] for label in chunk["labels"]}  # chunk labels follow selections
    workspace = load_workspace(dataset)
    base = stored[0]
    observation = next(o for o in result["evidence"]["observations"] if o["id"] == base["observationId"])
    target = next(f["id"] for f in workspace["knowledge"]["structured"]["fields"] if f["id"] != base.get("targetId"))
    context = CorrectionContext(workspace, {"datasets": [dataset], "withheld": []}, {})
    anchor = context.anchor(base, observation)
    [projected] = overlay_mappings(context, [base], [observation], {
        anchor: {"action": "replace", "targetId": target, "correctionRevision": "1" * 64}}, editable=True)
    assert projected["status"] == "corrected" and projected["targetId"] == target
    assert projected["path1"] == base["path1"]  # the stored decision is kept beside the correction


def test_degraded_ingest_without_embedder_is_flagged(tmp_path, monkeypatch):
    dataset = "financial-services"
    source = f"{dataset}:source:jade-adverse-media"
    principal = {"id": "admin", "role": "admin", "datasets": [dataset], "withheld": []}
    monkeypatch.setattr(workspace_server.WorkspaceRequest, "principal", lambda _: principal)
    monkeypatch.setattr(workspace_server, "resolve_session", lambda *_: principal)
    labeler = FixtureLabeler(lambda anchors, definitions: {"labels": []})
    with running_server(evidence_dir=tmp_path / "evidence", evidence_labeler=labeler, chunk_embedder=None,
                        mapping_embedder=None) as call:
        status, result = call("POST", "/api/ocr", {"dataset": dataset, "sourceId": source, "mode": "text"})
        assert status == 200
        stored = [m for m in result["evidence"]["mappings"] if "path1" in m]
        assert stored and all(m["path1"]["degraded"] == "no_embedder" for m in stored)
        assert result["mapping"] == {"path": "path1", "degraded": "no_embedder"}
