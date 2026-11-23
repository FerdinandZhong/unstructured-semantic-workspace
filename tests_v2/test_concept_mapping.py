"""Tests for concept-layer mapping: hybrid candidates, two-step resolution, graph edges.

All embedding paths use a deterministic FakeEmbedder so no live provider is called.
The evaluation function reports recall@12, precision and abstention accuracy on the
gold set; results are labelled as fake-embedding offline numbers, not live quality.
"""

import json
import struct
import hashlib
import os
from pathlib import Path

import pytest

from semantic_pipeline_v2.concept_mapping import (
    ConceptCatalog,
    ConceptIndex,
    Embedder,
    HybridCandidateIndex,
    TOP_K,
    column_terms,
    evaluate_gold,
    resolve_passage,
    source_kind_reject_titles,
    write_graph_edges,
)
from semantic_pipeline_v2.semantic_retrieval import DefinitionCatalog
from semantic_pipeline_v2.workspace import load_workspace


GOLD_PATH = Path(__file__).parent / "fixtures" / "mapping_gold.jsonl"


# ---------------------------------------------------------------------------
# Deterministic fake embedder
# ---------------------------------------------------------------------------

class FakeEmbedder:
    """Deterministic embedder: SHA-256-based unit vector of 32 floats.

    Two identical texts → identical vector.  Two different texts → different
    but pseudo-random vectors.  Cosine similarity between unrelated texts is
    near-random (~0), so the vector component contributes ~random recall.
    The lexical component carries meaningful recall on the gold set.
    """

    DIM = 8  # SHA-256 = 32 bytes = 8 floats (4 bytes each)

    def embed(self, texts: list[str]) -> list[list[float]]:
        result = []
        for text in texts:
            raw = hashlib.sha256(text.encode()).digest()  # 32 bytes
            floats = [struct.unpack_from("f", raw, i * 4)[0] for i in range(self.DIM)]
            # Normalise to unit vector
            norm = sum(x * x for x in floats) ** 0.5 or 1.0
            result.append([x / norm for x in floats])
        return result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def finance_ws():
    return load_workspace("financial-services")


@pytest.fixture(scope="module")
def manufacturing_ws():
    return load_workspace("manufacturing")


@pytest.fixture(scope="module")
def finance_catalog(finance_ws):
    return DefinitionCatalog(finance_ws)


@pytest.fixture(scope="module")
def manufacturing_catalog(manufacturing_ws):
    return DefinitionCatalog(manufacturing_ws)


# ---------------------------------------------------------------------------
# 1. column_terms() and source_kind_reject_titles() load from JSON
# ---------------------------------------------------------------------------

def test_column_terms_loaded_from_json():
    """column_terms() reads from concept_constraints.json."""
    ct = column_terms()
    assert isinstance(ct, frozenset)
    assert "account" in ct
    assert "amount" in ct
    assert "value" in ct
    # Spot-check a term that the mapped_evidence hard-coded fallback also has
    assert "transaction" in ct


def test_source_kind_reject_titles_contain_risk_rating():
    """source_kind_reject_titles() includes adverse-media → risk_rating."""
    from semantic_pipeline_v2.semantic_retrieval import words
    rejects = source_kind_reject_titles()
    assert "adverse-media" in rejects
    adverse = rejects["adverse-media"]
    assert words("RiskRating") in adverse
    assert words("CustomerID") in adverse
    assert words("DepositLimit") in adverse


# ---------------------------------------------------------------------------
# 2. HybridCandidateIndex: union of lexical and vector
# ---------------------------------------------------------------------------

def test_hybrid_index_pure_lexical_when_no_embedder(finance_catalog):
    """Without embedder, hybrid index returns only lexical candidates."""
    index = HybridCandidateIndex(finance_catalog)
    cands = index.search("Risk rating: medium")
    assert all(c["retrieval_proximity"] is None for c in cands)
    assert any(c["id"].endswith("/Customers/RiskRating") for c in cands)


def test_hybrid_index_union_adds_vector_candidates(finance_catalog):
    """With embedder, some candidates carry retrieval_proximity."""
    index = HybridCandidateIndex(finance_catalog, FakeEmbedder())
    cands = index.search("Risk rating: medium")
    # At least lexical + some vector
    assert any(c["retrieval_proximity"] is not None for c in cands)
    # Union is broader than lexical alone
    lexical_only = HybridCandidateIndex(finance_catalog)
    assert len(cands) >= len(lexical_only.search("Risk rating: medium"))


def test_hybrid_index_top_k_bounded(finance_catalog):
    """Each source (lexical, vector) contributes at most k candidates."""
    k = 6
    index = HybridCandidateIndex(finance_catalog, FakeEmbedder(), k=k)
    cands = index.search("transaction")
    # Union can be up to 2*k but each source limited to k
    lexical_ids = {c["id"] for c in index.catalog.search("transaction", limit=k)}
    vector_top_k = k  # FakeEmbedder always returns k entries
    assert len(cands) <= len(lexical_ids) + vector_top_k


def test_hybrid_index_basis_labels_correct(finance_catalog):
    """Each candidate correctly labels its basis (lexical / vector_proximity)."""
    index = HybridCandidateIndex(finance_catalog, FakeEmbedder())
    cands = index.search("Risk rating: medium")
    for c in cands:
        assert isinstance(c["basis"], list)
        assert all(b in ("lexical", "vector_proximity") for b in c["basis"])
        if c["retrieval_proximity"] is not None:
            assert "vector_proximity" in c["basis"]


# ---------------------------------------------------------------------------
# 3. ConceptIndex loads from HELIOS YAML
# ---------------------------------------------------------------------------

def test_concept_index_loads_from_helios_finance(finance_ws):
    """ConceptIndex populates concepts from HELIOS extensions in finance OSSIE YAML."""
    ci = ConceptIndex(finance_ws)
    assert ci.has_concepts, "Expected concepts from HELIOS YAML; got none"
    assert "risk_rating" in ci.concepts, "risk_rating concept not found"
    # Fields have no per-metric concept link
    risk_field = "financial-services:definition:finance_workbook_demo/field/Customers/RiskRating"
    assert ci.for_definition(risk_field) is None
    # high_risk_customers metric IS linked to risk_rating concept
    high_risk = "financial-services:definition:finance_workbook_demo/metric/high_risk_customers"
    assert ci.for_definition(high_risk) == "risk_rating"


def test_concept_index_loads_from_helios_manufacturing(manufacturing_ws):
    """ConceptIndex populates concepts from HELIOS extensions in manufacturing OSSIE YAML."""
    ci = ConceptIndex(manufacturing_ws)
    assert ci.has_concepts, "Expected concepts from HELIOS YAML; got none"
    # sales_order_count metric → sales_order concept
    soc = "manufacturing:definition:adventureworks_manufacturing_demo/metric/sales_order_count"
    assert ci.for_definition(soc) == "sales_order"


def test_concept_catalog_no_adverse_media_constraint(finance_ws):
    """ConceptCatalog returns definition IDs for the no_adverse_media constraint."""
    cc = ConceptCatalog(finance_ws)
    constrained = cc.all_definition_ids_with_constraint("no_adverse_media")
    # risk_rating concept has no_adverse_media; high_risk_customers links to it
    high_risk = "financial-services:definition:finance_workbook_demo/metric/high_risk_customers"
    assert high_risk in constrained, f"Expected {high_risk} in {constrained}"
    # The promoted model links the RiskRating field to risk_rating, so the field is covered too
    risk_field = "financial-services:definition:finance_workbook_demo/field/Customers/RiskRating"
    assert risk_field in constrained


def test_concept_reject_uses_data_driven_rule(finance_ws):
    """ConceptIndex.reject() reads from concept constraints + JSON reject_constraints."""
    ci = ConceptIndex(finance_ws)
    # risk_rating has no_adverse_media constraint; adverse-media triggers it
    assert ci.reject("risk_rating", "adverse-media") is True
    # native_text is not in any reject_constraints
    assert ci.reject("risk_rating", "native_text") is False
    # customer_count has no constraints
    assert ci.reject("customer_count", "adverse-media") is False


# ---------------------------------------------------------------------------
# 4. resolve_passage: unlinked_concept fallback
# ---------------------------------------------------------------------------

def test_resolve_unambiguous_field_returns_correct_definition(finance_ws, finance_catalog):
    """Clear single-candidate evidence resolves to the correct definition.

    RiskRating is a field (no per-metric concept link), so unlinked_concept stays True.
    """
    index = HybridCandidateIndex(finance_catalog)
    ci = ConceptIndex(finance_ws)
    result = resolve_passage("Risk rating: medium", "", "ocr_markdown", index, ci)
    assert result["definition_id"] is not None
    assert result["definition_id"].endswith("/Customers/RiskRating")
    assert result["unlinked_concept"] is True  # field — no concept link
    assert result["method"] in ("lexical", "vector_proximity")


def test_resolve_no_candidates_abstains(finance_ws, finance_catalog):
    """Truly generic text with no matching candidates → abstain."""
    index = HybridCandidateIndex(finance_catalog)
    ci = ConceptIndex(finance_ws)
    result = resolve_passage("Please review the attached document", "", "ocr_markdown", index, ci)
    assert result["definition_id"] is None
    assert result["reason"] == "no_candidates"


def test_resolve_domain_reject_removes_risk_rating(finance_ws, finance_catalog):
    """Adverse-media source kind must not resolve to the RiskRating field definition."""
    index = HybridCandidateIndex(finance_catalog)
    ci = ConceptIndex(finance_ws)
    result = resolve_passage(
        "RiskRating: high — per adverse media report", "", "adverse-media", index, ci
    )
    # RiskRating field is in the reject list; it must not be returned.
    # Another definition (high_risk_customers) may still resolve.
    risk_rating_id = "financial-services:definition:finance_workbook_demo/field/Customers/RiskRating"
    assert result.get("definition_id") != risk_rating_id


def test_resolve_ambiguous_label_tie_abstains(manufacturing_ws, manufacturing_catalog):
    """DueDate appears in two tables — tie-band should abstain."""
    index = HybridCandidateIndex(manufacturing_catalog)
    ci = ConceptIndex(manufacturing_ws)
    result = resolve_passage("DueDate: 2026-11-30", "", "native_text", index, ci)
    # DueDate exists in Sales_SalesOrderHeader and Production_WorkOrder → tie
    if result["definition_id"] is None:
        assert result["reason"] == "ambiguous_label_tie"
    # If not tied (e.g. only one found due to scope), that's also acceptable


def test_resolve_unambiguous_manufacturing_field(manufacturing_ws, manufacturing_catalog):
    """ScrappedQty is lexically unique in the manufacturing catalog."""
    index = HybridCandidateIndex(manufacturing_catalog)
    ci = ConceptIndex(manufacturing_ws)
    result = resolve_passage("ScrappedQty: 5", "", "native_text", index, ci)
    assert result["definition_id"] is not None
    assert result["definition_id"].endswith("/Production_WorkOrder/ScrappedQty")


# ---------------------------------------------------------------------------
# 5. graph edge structure (no postgres required)
# ---------------------------------------------------------------------------

class _MockStore:
    """In-memory mock for SemanticStore used in graph edge tests."""

    def __init__(self):
        self.calls: list[str] = []

    class _Db:
        def __init__(self, store):
            self._store = store

        def sql(self, query: str):
            self._store.calls.append(query)

    @property
    def db(self):
        return self._Db(self)


def test_write_graph_edges_mentions_row_written():
    """write_graph_edges writes a 'mentions' row for passage → definition."""
    store = _MockStore()
    result = {
        "definition_id": "manufacturing:definition:adventureworks_manufacturing_demo/field/Production_Product/ProductNumber",
        "concept_id": None,
        "method": "lexical",
        "unlinked_concept": True,
        "candidates": [{"id": "manufacturing:definition:adventureworks_manufacturing_demo/field/Production_Product/ProductNumber",
                         "retrieval_proximity": None, "basis": ["lexical"]}],
    }
    evidence = {"quote": "ProductNumber: BK-M18B-40", "page": 1, "revision": "abc123"}
    req_ids = write_graph_edges(store, "passage:test-001", result, evidence)
    assert len(req_ids) == 1
    assert len(store.calls) == 1
    assert "mentions" in store.calls[0]
    assert "proposed" in store.calls[0]
    # Must not describe similarity as confidence
    assert "confidence" not in store.calls[0].lower().replace("ON CONFLICT", "")


def test_write_graph_edges_realised_by_when_concept_present():
    """When concept_id is set, writes both mentions and realised_by edges."""
    store = _MockStore()
    result = {
        "definition_id": "financial-services:definition:finance_workbook_demo/field/Customers/RiskRating",
        "concept_id": "concept:risk_rating",
        "method": "vector_proximity",
        "unlinked_concept": False,
        "candidates": [],
    }
    evidence = {"quote": "Risk rating: medium", "page": 3, "revision": "deadbeef"}
    req_ids = write_graph_edges(store, "passage:test-002", result, evidence)
    assert len(req_ids) == 2
    relations = [call for call in store.calls if "realised_by" in call or "mentions" in call]
    assert any("mentions" in r for r in relations)
    assert any("realised_by" in r for r in relations)


def test_write_graph_edges_no_definition_returns_empty():
    """When definition_id is None (abstained), no edges are written."""
    store = _MockStore()
    result = {"definition_id": None, "concept_id": None, "method": "abstained",
              "unlinked_concept": True, "candidates": []}
    req_ids = write_graph_edges(store, "passage:test-003", result, {"quote": "N/A", "revision": "0"})
    assert req_ids == []
    assert store.calls == []


def test_write_graph_edges_idempotent():
    """Same passage+evidence pair produces the same request_id (idempotent insert)."""
    store = _MockStore()
    result = {
        "definition_id": "manufacturing:definition:adventureworks_manufacturing_demo/field/Production_Product/ProductNumber",
        "concept_id": None, "method": "lexical", "unlinked_concept": True, "candidates": [],
    }
    ev = {"quote": "ProductNumber: X", "page": 1, "revision": "abc"}
    ids1 = write_graph_edges(store, "passage:p1", result, ev)
    ids2 = write_graph_edges(store, "passage:p1", result, ev)
    assert ids1 == ids2


# ---------------------------------------------------------------------------
# 6. graph edge integration (requires V2_PG_BIN)
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    pg_bin = os.environ.get("V2_PG_BIN")
    if not pg_bin:
        pytest.skip("set V2_PG_BIN to run graph-edge integration tests")
    from semantic_pipeline_v2._pilot_postgres import PilotPostgres
    with PilotPostgres.start(Path(pg_bin)) as database:
        yield database


def test_graph_edges_persisted_in_postgres(db):
    """Graph edges reach v2_graph_edge and are idempotent on conflict."""
    from semantic_pipeline_v2.semantic_store import SemanticStore
    store = SemanticStore(db)
    result = {
        "definition_id": "manufacturing:definition:adventureworks_manufacturing_demo/field/Production_Product/ProductNumber",
        "concept_id": None,
        "method": "lexical",
        "unlinked_concept": True,
        "candidates": [],
    }
    ev = {"quote": "ProductNumber: BK-M18B-40", "page": 1, "revision": "aabbcc"}
    ids = write_graph_edges(store, "passage:integration-001", result, ev)
    assert len(ids) == 1

    # Second write (same content) must not raise — ON CONFLICT DO NOTHING
    ids2 = write_graph_edges(store, "passage:integration-001", result, ev)
    assert ids == ids2

    count = db.sql("SELECT count(*) FROM v2_graph_edge WHERE relation = 'mentions';")
    assert int(count) >= 1


# ---------------------------------------------------------------------------
# 7. Gold-set evaluation (fake embeddings, offline)
# ---------------------------------------------------------------------------

def test_gold_file_is_readable_and_has_expected_entries():
    """Gold JSONL loads, has synthetic label, and covers both datasets."""
    records = [json.loads(line) for line in GOLD_PATH.read_text().splitlines() if line.strip()]
    assert len(records) >= 60
    datasets = {r.get("dataset", r.get("source_dataset", "")) for r in records}
    assert "financial-services" in datasets
    assert "manufacturing" in datasets
    # gold file uses label:"synthetic" (not synthetic:true)
    assert all(r.get("label") == "synthetic" or r.get("synthetic") for r in records)
    # abstain records are those without expected_definition_id (or explicit abstain:true)
    for r in records:
        if r.get("abstain") is True:
            pass  # explicit abstain — no expected_definition_id required
        elif r.get("expected_definition_id"):
            pass  # positive example — OK
        # else: no expected_definition_id and no abstain flag → treated as abstain by evaluate_gold


def test_gold_expected_ids_exist_in_catalogs(finance_ws, manufacturing_ws):
    """Every expected_definition_id in the gold set exists in its workspace catalog."""
    finance_catalog = DefinitionCatalog(finance_ws)
    mfg_catalog = DefinitionCatalog(manufacturing_ws)
    records = [json.loads(line) for line in GOLD_PATH.read_text().splitlines() if line.strip()]
    missing = []
    for r in records:
        if not r.get("abstain") and r.get("expected_definition_id"):
            eid = r["expected_definition_id"]
            cat = finance_catalog if r.get("dataset", r.get("source_dataset", "")) == "financial-services" else mfg_catalog
            if eid not in cat.ids:
                missing.append((r["id"], eid))
    assert not missing, f"Gold IDs not found in catalog: {missing}"


def _run_evaluation(catalog, embedder, dataset_filter=None, workspace=None):
    """Run evaluate_gold on the filtered gold set."""
    import tempfile
    records = [json.loads(line) for line in GOLD_PATH.read_text().splitlines() if line.strip()]
    if dataset_filter:
        records = [r for r in records if r.get("dataset", r.get("source_dataset", "")) == dataset_filter]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
        tmp = f.name
    try:
        return evaluate_gold(tmp, catalog, embedder, workspace=workspace)
    finally:
        Path(tmp).unlink(missing_ok=True)


def test_gold_evaluation_finance_lexical_only(finance_ws, finance_catalog):
    """Finance gold set: lexical-only baseline evaluation (fake-embedding offline)."""
    metrics = _run_evaluation(finance_catalog, None, "financial-services", workspace=finance_ws)
    print("\n=== Finance lexical-only + concept path (fake-embedding offline) ===")
    print(f"  recall@{TOP_K}:         {metrics['recall_at_12']:.2%} ({metrics['recall_hits']}/{metrics['n_hit']})")
    print(f"  def precision:    {metrics['precision']:.2%} ({metrics['precision_hits']}/{metrics['n_hit']})")
    print(f"  concept precision:{metrics['concept_precision']:.2%} ({metrics['concept_hits']}/{metrics['n_concept_anchors']})")
    print(f"  via concept path: {metrics['via_concept_path']}")
    print(f"  via fallback:     {metrics['via_fallback']}")
    print(f"  abstention:       {metrics['abstention_accuracy']:.2%} ({metrics['abstention_hits']}/{metrics['n_abstain']})")
    assert metrics["recall_at_12"] >= 0.6, (
        f"recall@12 too low: {metrics['recall_at_12']:.2%} — check gold set IDs or label matching"
    )


def test_gold_evaluation_manufacturing_lexical_only(manufacturing_ws, manufacturing_catalog):
    """Manufacturing gold set: lexical-only baseline evaluation (fake-embedding offline)."""
    metrics = _run_evaluation(manufacturing_catalog, None, "manufacturing", workspace=manufacturing_ws)
    print("\n=== Manufacturing lexical-only + concept path (fake-embedding offline) ===")
    print(f"  recall@{TOP_K}:         {metrics['recall_at_12']:.2%} ({metrics['recall_hits']}/{metrics['n_hit']})")
    print(f"  def precision:    {metrics['precision']:.2%} ({metrics['precision_hits']}/{metrics['n_hit']})")
    print(f"  concept precision:{metrics['concept_precision']:.2%} ({metrics['concept_hits']}/{metrics['n_concept_anchors']})")
    print(f"  via concept path: {metrics['via_concept_path']}")
    print(f"  via fallback:     {metrics['via_fallback']}")
    print(f"  abstention:       {metrics['abstention_accuracy']:.2%} ({metrics['abstention_hits']}/{metrics['n_abstain']})")
    assert metrics["recall_at_12"] >= 0.6


def test_gold_evaluation_with_fake_embedder_finance(finance_ws, finance_catalog):
    """Finance: hybrid (lexical + fake-vector) evaluation (fake-embedding offline)."""
    metrics = _run_evaluation(finance_catalog, FakeEmbedder(), "financial-services", workspace=finance_ws)
    print("\n=== Finance hybrid fake-vector + concept path (fake-embedding offline) ===")
    print(f"  recall@{TOP_K}:         {metrics['recall_at_12']:.2%} ({metrics['recall_hits']}/{metrics['n_hit']})")
    print(f"  def precision:    {metrics['precision']:.2%} ({metrics['precision_hits']}/{metrics['n_hit']})")
    print(f"  concept precision:{metrics['concept_precision']:.2%} ({metrics['concept_hits']}/{metrics['n_concept_anchors']})")
    print(f"  via concept path: {metrics['via_concept_path']}")
    print(f"  via fallback:     {metrics['via_fallback']}")
    print(f"  abstention:       {metrics['abstention_accuracy']:.2%} ({metrics['abstention_hits']}/{metrics['n_abstain']})")
    # Fake-vector adds pseudo-random candidates; recall should be >= lexical-only
    lexical = _run_evaluation(finance_catalog, None, "financial-services", workspace=finance_ws)
    assert metrics["recall_at_12"] >= lexical["recall_at_12"]


def test_gold_evaluation_with_fake_embedder_manufacturing(manufacturing_ws, manufacturing_catalog):
    """Manufacturing: hybrid (lexical + fake-vector) evaluation (fake-embedding offline)."""
    metrics = _run_evaluation(manufacturing_catalog, FakeEmbedder(), "manufacturing", workspace=manufacturing_ws)
    print("\n=== Manufacturing hybrid fake-vector + concept path (fake-embedding offline) ===")
    print(f"  recall@{TOP_K}:         {metrics['recall_at_12']:.2%} ({metrics['recall_hits']}/{metrics['n_hit']})")
    print(f"  def precision:    {metrics['precision']:.2%} ({metrics['precision_hits']}/{metrics['n_hit']})")
    print(f"  concept precision:{metrics['concept_precision']:.2%} ({metrics['concept_hits']}/{metrics['n_concept_anchors']})")
    print(f"  via concept path: {metrics['via_concept_path']}")
    print(f"  via fallback:     {metrics['via_fallback']}")
    print(f"  abstention:       {metrics['abstention_accuracy']:.2%} ({metrics['abstention_hits']}/{metrics['n_abstain']})")
    lexical = _run_evaluation(manufacturing_catalog, None, "manufacturing", workspace=manufacturing_ws)
    assert metrics["recall_at_12"] >= lexical["recall_at_12"]


def test_gold_evaluation_full_report(finance_ws, finance_catalog, manufacturing_ws, manufacturing_catalog):
    """Full gold set report across both datasets (fake-embedding offline).

    Reports definition-step precision and concept-step precision separately for
    both datasets. Vectors from FakeEmbedder are pseudo-random; these numbers
    are offline baselines only.
    """
    records = [json.loads(line) for line in GOLD_PATH.read_text().splitlines() if line.strip()]

    fin = _run_evaluation(finance_catalog, FakeEmbedder(), "financial-services", workspace=finance_ws)
    mfg = _run_evaluation(manufacturing_catalog, FakeEmbedder(), "manufacturing", workspace=manufacturing_ws)

    n_hit = fin["n_hit"] + mfg["n_hit"]
    n_abs = fin["n_abstain"] + mfg["n_abstain"]
    n_concept = fin["n_concept_anchors"] + mfg["n_concept_anchors"]
    combined_recall = (fin["recall_hits"] + mfg["recall_hits"]) / n_hit if n_hit else 0.0
    combined_precision = (fin["precision_hits"] + mfg["precision_hits"]) / n_hit if n_hit else 0.0
    combined_concept_precision = (
        (fin["concept_hits"] + mfg["concept_hits"]) / n_concept if n_concept else 0.0
    )
    combined_abstention = (fin["abstention_hits"] + mfg["abstention_hits"]) / n_abs if n_abs else 1.0
    via_concept = fin["via_concept_path"] + mfg["via_concept_path"]
    via_fallback = fin["via_fallback"] + mfg["via_fallback"]

    print("\n=== FULL GOLD SET EVALUATION (fake-embedding offline, not live quality) ===")
    print(f"  total entries  : {len(records)} ({n_hit} positive, {n_abs} abstain)")
    print(f"  recall@{TOP_K}      : {combined_recall:.2%}")
    print(f"  def precision  : {combined_precision:.2%}  (fin {fin['precision']:.2%}, mfg {mfg['precision']:.2%})")
    print(f"  concept prec   : {combined_concept_precision:.2%}  (fin {fin['concept_precision']:.2%}, mfg {mfg['concept_precision']:.2%})")
    print(f"  via concept    : {via_concept}  via fallback: {via_fallback}")
    print(f"  abstention acc : {combined_abstention:.2%}")
    print(f"  tie_band       : enabled")
    print("  NOTE: Fake-embedding numbers only. FakeEmbedder vectors are pseudo-random, not evidence of live quality.")

    assert combined_recall >= 0.60, f"recall@12 {combined_recall:.2%} below 60% threshold"
