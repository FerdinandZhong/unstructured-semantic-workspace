"""Tests for SemanticStore: concept import from OSSIE YAML via HELIOS extensions."""

import os
from pathlib import Path

import pytest


FINANCE_YAML = (
    Path(__file__).resolve().parents[1]
    / "demo_data/finance/finance.ossie.yaml"
)
MANUFACTURING_YAML = (
    Path(__file__).resolve().parents[1]
    / "demo_data/manufacturing/semantics/manufacturing.ossie.yaml"
)


@pytest.fixture()
def db(tmp_path):
    pg_bin = os.environ.get("V2_PG_BIN")
    if not pg_bin:
        pytest.skip("set V2_PG_BIN to run semantic store tests")
    from semantic_pipeline_v2._pilot_postgres import PilotPostgres
    with PilotPostgres.start(Path(pg_bin)) as database:
        yield database


def test_import_ossie_fills_v2_concept_finance(db):
    """import_ossie populates v2_concept rows for the finance model."""
    from semantic_pipeline_v2.semantic_store import SemanticStore

    store = SemanticStore(db)
    store.import_ossie("financial-services", FINANCE_YAML)

    count = db.sql(
        "SELECT count(*) FROM v2_concept WHERE dataset = 'financial-services';"
    )
    assert int(count) >= 1, "Expected at least one concept row for financial-services"

    # risk_rating concept must be present with no_adverse_media constraint
    rows = db.sql(
        "SELECT concept_id FROM v2_concept"
        " WHERE dataset = 'financial-services' AND concept_id = 'risk_rating';"
    )
    assert rows, "risk_rating concept not found in v2_concept"


def test_import_ossie_fills_v2_concept_manufacturing(db):
    """import_ossie populates v2_concept rows for the manufacturing model."""
    from semantic_pipeline_v2.semantic_store import SemanticStore

    store = SemanticStore(db)
    store.import_ossie("manufacturing", MANUFACTURING_YAML)

    count = db.sql(
        "SELECT count(*) FROM v2_concept WHERE dataset = 'manufacturing';"
    )
    assert int(count) >= 1, "Expected at least one concept row for manufacturing"


def test_import_ossie_concept_idempotent(db):
    """Calling import_ossie twice on the same YAML does not duplicate concept rows."""
    from semantic_pipeline_v2.semantic_store import SemanticStore

    store = SemanticStore(db)
    store.import_ossie("financial-services", FINANCE_YAML)
    count1 = int(
        db.sql("SELECT count(*) FROM v2_concept WHERE dataset = 'financial-services';")
    )

    # Second import — same SHA, should be a no-op
    store.import_ossie("financial-services", FINANCE_YAML)
    count2 = int(
        db.sql("SELECT count(*) FROM v2_concept WHERE dataset = 'financial-services';")
    )

    assert count1 == count2, "Second import changed concept row count (not idempotent)"


def test_import_ossie_data_driven_reject_covers_risk_rating(db):
    """ConceptCatalog.all_definition_ids_with_constraint works after import_ossie.

    This also verifies the data-driven reject path with the legacy hard-coded
    check disabled (evidence_labeler._validated no longer has the legacy check).
    """
    from semantic_pipeline_v2.semantic_store import SemanticStore
    from semantic_pipeline_v2.workspace import load_workspace
    from semantic_pipeline_v2.concept_mapping import ConceptCatalog

    store = SemanticStore(db)
    store.import_ossie("financial-services", FINANCE_YAML)

    ws = load_workspace("financial-services")
    cc = ConceptCatalog(ws)

    constrained = cc.all_definition_ids_with_constraint("no_adverse_media")
    # high_risk_customers metric is linked to risk_rating concept (no_adverse_media)
    high_risk_id = "financial-services:definition:finance_workbook_demo/metric/high_risk_customers"
    assert high_risk_id in constrained, (
        f"Expected {high_risk_id} in constrained defs; got {constrained}"
    )
    # The promoted model links the RiskRating field to risk_rating, so the field is covered too.
    risk_rating_field = "financial-services:definition:finance_workbook_demo/field/Customers/RiskRating"
    assert risk_rating_field in constrained


# ---------------------------------------------------------------------------
# T4 / T32c: enriched model reaches the store
# ---------------------------------------------------------------------------

import json  # noqa: E402

import yaml  # noqa: E402

MODEL_FILES = {"financial-services": FINANCE_YAML, "manufacturing": MANUFACTURING_YAML}


def _helios(node):
    for item in node.get("custom_extensions") or []:
        if item.get("vendor_name") == "HELIOS":
            return json.loads(item["data"])
    return {}


def _rows(db, sql, **params):
    return json.loads(db.sql(sql, **params) or "[]")


@pytest.mark.parametrize("dataset", MODEL_FILES)
def test_t4_concepts_are_stored_with_synonyms_and_broader(db, dataset):
    from semantic_pipeline_v2.semantic_store import SemanticStore

    SemanticStore(db).import_ossie(dataset, MODEL_FILES[dataset])
    model = yaml.safe_load(MODEL_FILES[dataset].read_bytes())
    expected = {c["id"]: c for c in _helios(model)["concepts"]}
    stored = {r["concept_id"]: r for r in _rows(
        db, "SELECT COALESCE(jsonb_agg(row_to_json(c)), '[]'::jsonb) FROM v2_concept c "
            "WHERE dataset = :'ds';", ds=dataset)}
    assert set(stored) == set(expected)
    for cid, concept in expected.items():
        row = stored[cid]
        assert row["label"] == concept["label"]
        assert row["definition"] == concept["definition"]
        assert row["broader"] == concept.get("broader")
        assert row["synonyms"] == (concept.get("synonyms") or [])
    assert any(r["synonyms"] for r in stored.values())


@pytest.mark.parametrize("dataset", MODEL_FILES)
def test_t4_definitions_carry_concept_unit_grain_and_required_filters(db, dataset):
    from semantic_pipeline_v2.semantic_store import SemanticStore

    SemanticStore(db).import_ossie(dataset, MODEL_FILES[dataset])
    model = yaml.safe_load(MODEL_FILES[dataset].read_bytes())
    namespace = model["name"]
    expected = {}
    for table in model["datasets"]:
        for field in table["fields"]:
            data = _helios(field)
            expected[f"{namespace}/field/{table['name']}/{field['name']}"] = {
                "concept_id": data.get("concept"), "unit": data.get("unit"),
                "grain": None, "time_field": None, "required_filters": None}
    for metric in model["metrics"]:
        data = _helios(metric)
        expected[f"{namespace}/metric/{metric['name']}"] = {
            "concept_id": data.get("concept"), "unit": data.get("unit"), "grain": data.get("grain"),
            "time_field": data.get("time_field"), "required_filters": data.get("required_filters")}
    stored = {r["source_id"]: r for r in _rows(
        db, "SELECT COALESCE(jsonb_agg(row_to_json(d)), '[]'::jsonb) FROM v2_definition d "
            "WHERE dataset = :'ds' AND kind IN ('field', 'metric');", ds=dataset)}
    assert set(stored) == set(expected)
    for source_id, want in expected.items():
        got = {k: stored[source_id][k] for k in want}
        assert got == want, source_id
    assert sum(1 for w in expected.values() if w["concept_id"]) > 20
    if dataset == "financial-services":  # the manufacturing metrics require no filter
        assert sum(1 for w in expected.values() if w["required_filters"]) >= 1


@pytest.mark.parametrize("dataset", MODEL_FILES)
def test_t32_every_stored_concept_reference_resolves(db, dataset):
    from semantic_pipeline_v2.semantic_store import SemanticStore

    SemanticStore(db).import_ossie(dataset, MODEL_FILES[dataset])
    dangling = db.sql(
        "SELECT count(*) FROM v2_definition d WHERE d.dataset = :'ds' AND d.concept_id IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM v2_concept c WHERE c.dataset = d.dataset "
        "AND c.release = d.release AND c.concept_id = d.concept_id);", ds=dataset)
    assert dangling == "0"


@pytest.mark.parametrize("dataset", MODEL_FILES)
def test_t4_export_round_trips_and_second_import_keeps_one_release(db, dataset):
    from semantic_pipeline_v2.semantic_store import SemanticStore

    store = SemanticStore(db)
    first = store.import_ossie(dataset, MODEL_FILES[dataset])
    second = store.import_ossie(dataset, MODEL_FILES[dataset])
    assert first["release_id"] == second["release_id"]
    assert db.sql("SELECT count(*) FROM v2_ontology_release WHERE dataset = :'ds';", ds=dataset) == "1"
    assert yaml.safe_load(store.export_ossie(dataset)) == yaml.safe_load(MODEL_FILES[dataset].read_bytes())


def test_t4_schema_migration_is_idempotent(db):
    from semantic_pipeline_v2.semantic_store import SemanticStore

    SemanticStore(db)
    SemanticStore(db)
    columns = db.sql("SELECT string_agg(column_name, ',' ORDER BY column_name) FROM information_schema.columns "
                     "WHERE table_name = 'v2_concept';")
    assert "synonyms" in columns.split(",")
