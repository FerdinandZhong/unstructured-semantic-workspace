"""Task 1 acceptance tests: schema, import/export, stale-write and correction stability.

All tests require a local PostgreSQL binary; they skip when V2_PG_BIN is unset.
"""

import os
from pathlib import Path

import pytest

from semantic_pipeline_v2._pilot_postgres import PilotPostgres
from semantic_pipeline_v2.semantic_store import SemanticStore, StaleCorrection


# ---------------------------------------------------------------------------
# Minimal valid Ossie YAML factory
# ---------------------------------------------------------------------------

def _make_yaml(desc_total: str = "Total distinct rows.",
               desc_sum: str = "Sum of values.") -> bytes:
    """Return a valid Ossie 0.2.0.dev0 YAML with two metrics.

    desc_total controls metric total_x, desc_sum controls metric sum_y.
    Keeping desc_total identical across two calls while changing desc_sum
    verifies that the definition_hash for total_x is unaffected.
    """
    return f"""\
version: 0.2.0.dev0
name: storetest
description: Minimal model for SemanticStore core tests.
datasets:
- name: T
  source: T
  description: Test table.
  primary_key:
  - id
  fields:
  - name: id
    expression:
      dialects:
      - dialect: ANSI_SQL
        expression: id
    description: Row identifier.
    datatype: Integer
  - name: val
    expression:
      dialects:
      - dialect: ANSI_SQL
        expression: val
    description: Numeric value.
    datatype: Float
metrics:
- name: total_x
  expression:
    dialects:
    - dialect: ANSI_SQL
      expression: COUNT(DISTINCT T.id)
  description: {desc_total}
  datatype: Integer
- name: sum_y
  expression:
    dialects:
    - dialect: ANSI_SQL
      expression: SUM(T.val)
  description: {desc_sum}
  datatype: Float
""".encode()


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    pg_bin = os.environ.get("V2_PG_BIN")
    if not pg_bin:
        pytest.skip("set V2_PG_BIN to run SemanticStore core integration tests")
    with PilotPostgres.start(Path(pg_bin)) as database:
        yield database


# ---------------------------------------------------------------------------
# Acceptance tests
# ---------------------------------------------------------------------------

def test_import_twice_gives_one_release(db, tmp_path):
    """Importing the same YAML twice produces exactly one v2_ontology_release row."""
    store = SemanticStore(db)
    p = tmp_path / "model.yaml"
    p.write_bytes(_make_yaml())

    r1 = store.import_ossie("test", p, allow_unlinted=True)
    r2 = store.import_ossie("test", p, allow_unlinted=True)

    assert r1["source_sha256"] == r2["source_sha256"]
    count = db.sql(
        "SELECT count(*) FROM v2_ontology_release WHERE dataset = 'test';"
    )
    assert count == "1"


def test_export_round_trips(db, tmp_path):
    """export_ossie returns the original raw bytes (byte-stable on the normalised form)."""
    store = SemanticStore(db)
    raw = _make_yaml()
    p = tmp_path / "model.yaml"
    p.write_bytes(raw)

    store.import_ossie("test", p, allow_unlinted=True)
    exported = store.export_ossie("test")

    assert exported == raw


def test_stale_edit_rejected(db, tmp_path):
    """record_correction raises StaleCorrection when previousRevision is outdated."""
    store = SemanticStore(db)
    p = tmp_path / "model.yaml"
    p.write_bytes(_make_yaml())
    store.import_ossie("test", p, allow_unlinted=True)

    defs = store.list_definitions("test")
    metric = next(d for d in defs if d["kind"] == "metric")
    anchor = store.correction_anchor("test", metric["id"], metric["definition_hash"])

    # First correction uses the anchor as the initial previous_revision.
    rev1 = store.record_correction(
        "test", metric["id"], metric["definition_hash"],
        "replace", "some_target", anchor, "user1", "req-stale-1",
    )

    # Reusing the original anchor as previous_revision is now stale.
    with pytest.raises(StaleCorrection):
        store.record_correction(
            "test", metric["id"], metric["definition_hash"],
            "replace", "other_target", anchor, "user1", "req-stale-2",
        )

    # Chaining with the correct rev1 must succeed.
    rev2 = store.record_correction(
        "test", metric["id"], metric["definition_hash"],
        "remove", None, rev1, "user1", "req-stale-3",
    )
    assert rev2 != rev1


def test_correction_survives_unrelated_definition_change(db, tmp_path):
    """A correction on definition A remains valid after a release that only changes B."""
    store = SemanticStore(db)

    # v1: both metrics at original descriptions.
    p1 = tmp_path / "v1.yaml"
    p1.write_bytes(_make_yaml(desc_total="Total distinct rows.", desc_sum="Sum of values."))
    store.import_ossie("test", p1, allow_unlinted=True)

    defs_v1 = {d["source_id"].split("/")[-1]: d for d in store.list_definitions("test")}
    total_x_v1 = defs_v1["total_x"]
    sum_y_v1 = defs_v1["sum_y"]

    anchor_v1 = store.correction_anchor(
        "test", total_x_v1["id"], total_x_v1["definition_hash"]
    )
    rev1 = store.record_correction(
        "test", total_x_v1["id"], total_x_v1["definition_hash"],
        "replace", "some_target", anchor_v1, "user1", "req-unrelated-1",
    )

    # v2: sum_y description changed; total_x is identical to v1.
    p2 = tmp_path / "v2.yaml"
    p2.write_bytes(_make_yaml(desc_total="Total distinct rows.", desc_sum="CHANGED sum of values."))
    store.import_ossie("test", p2, allow_unlinted=True)

    defs_v2 = {d["source_id"].split("/")[-1]: d for d in store.list_definitions("test")}
    total_x_v2 = defs_v2["total_x"]
    sum_y_v2 = defs_v2["sum_y"]

    # total_x content is unchanged: same definition_hash.
    assert total_x_v2["definition_hash"] == total_x_v1["definition_hash"]
    # sum_y content changed: different definition_hash.
    assert sum_y_v2["definition_hash"] != sum_y_v1["definition_hash"]

    # Correction on total_x is still chainable using rev1 as previous_revision.
    rev2 = store.record_correction(
        "test", total_x_v1["id"], total_x_v1["definition_hash"],
        "remove", None, rev1, "user1", "req-unrelated-2",
    )
    assert rev2 != rev1


# ---------------------------------------------------------------------------
# P0: import is one transaction, repairs a half import and refuses a model that fails the project lint
# ---------------------------------------------------------------------------

def test_failed_import_leaves_no_release_row(db, tmp_path):
    """A statement that fails late (concept insert here) must roll back the release row too."""
    import json
    from semantic_pipeline_v2._pilot_postgres import PilotDatabaseError

    class Saboteur:
        def __init__(self, inner):
            self.inner = inner

        def sql(self, script, **params):
            return self.inner.sql(script.replace("INSERT INTO v2_concept", "INSERT INTO v2_missing_table"), **params)

    p = tmp_path / "model.yaml"
    data = json.dumps({"concepts": [{"id": "c1", "label": "Concept", "definition": "d"}]})
    p.write_bytes(_make_yaml() + ("custom_extensions:\n- vendor_name: HELIOS\n  data: " + json.dumps(data) + "\n").encode())
    store = SemanticStore(db)
    store.db = Saboteur(db)
    with pytest.raises(PilotDatabaseError):
        store.import_ossie("test", p, allow_unlinted=True)
    assert db.sql("SELECT count(*) FROM v2_ontology_release;") == "0"
    assert db.sql("SELECT count(*) FROM v2_definition;") == "0"


def test_release_without_definitions_is_repaired(db, tmp_path):
    import hashlib
    store = SemanticStore(db)
    raw = _make_yaml()
    p = tmp_path / "model.yaml"
    p.write_bytes(raw)
    db.sql("INSERT INTO v2_ontology_release (dataset, source_sha256, source_hex) VALUES ('test', :'s', :'h');",
           s=hashlib.sha256(raw).hexdigest(), h=raw.hex())
    assert store.list_definitions("test") == []
    store.import_ossie("test", p, allow_unlinted=True)
    assert {d["kind"] for d in store.list_definitions("test")} >= {"metric", "field"}
    assert db.sql("SELECT count(*) FROM v2_ontology_release;") == "1"


def test_import_refuses_a_model_that_fails_the_project_lint(db, tmp_path):
    store = SemanticStore(db)
    p = tmp_path / "model.yaml"
    p.write_bytes(_make_yaml())
    with pytest.raises(ValueError, match="lint"):
        store.import_ossie("test", p)
    assert db.sql("SELECT count(*) FROM v2_ontology_release;") == "0"
