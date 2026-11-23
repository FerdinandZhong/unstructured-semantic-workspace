"""P2e part 1: the wider document corpus is registered as workspace sources (tests written before the code)."""
import json
from pathlib import Path

import pytest

from semantic_pipeline_v2 import access, concept_induction as ci, workspace
from semantic_pipeline_v2.originals import checked_original, load_originals

ROOT = workspace.ROOT
REGISTRY = json.loads((ROOT / "demo_data/extra_sources.json").read_text())
COUNTS = {"financial-services": 17, "manufacturing": 22}   # implementer-written: 12+4+1 and 14+8
NATIVE = ROOT / "demo_data/manufacturing/data/native-documents"


def present(entry):
    return (ROOT / entry["path"]).is_file()


def rows(dataset):
    record = workspace.load_workspace(dataset)
    return record, {r["id"].split(":")[-1]: r for r in record["sources"]}


@pytest.mark.parametrize("dataset", COUNTS)
def test_registered_with_kind_label_scope_and_hash(dataset):
    assert len(REGISTRY[dataset]) == COUNTS[dataset]
    _, by_key = rows(dataset)
    for entry in REGISTRY[dataset]:
        r = by_key[entry["key"]]
        assert r["kind"] == entry["kind"] and r["label"] == entry["label"] and r["scope"] == entry["scope"]
        assert r["citation"] == {"path": entry["path"], "sha256": entry["sha256"]}
        if entry["kind"] == "synthetic-document":
            assert r["label"] == "Synthetic"
        if entry["kind"] == "public-document":
            assert r["scope"] == "extraction_only" and r["attribution"]
        if present(entry):
            assert workspace.digest((ROOT / entry["path"]).read_bytes()) == entry["sha256"]


def test_no_planted_truth_and_no_customer_link():
    for dataset in COUNTS:
        record, _ = rows(dataset)
        text = json.dumps(record)
        assert '"statements"' not in text and '"stated"' not in text and "planted" not in text
        new = {f"{dataset}:source:{e['key']}" for e in REGISTRY[dataset]}
        for entity in record["entities"]:
            assert not new & set(entity["sourceIds"])


@pytest.mark.parametrize("dataset", COUNTS)
def test_every_registered_source_has_chunks(dataset):
    skipped = []
    chunks = ci.dataset_chunks(dataset, skipped)
    have = {c["source"] for c in chunks}
    for entry in REGISTRY[dataset]:
        source = f"{dataset}:source:{entry['key']}"
        if not present(entry):
            pytest.skip(f"local data absent: {entry['path']}")
        assert source in have, source


def test_restricted_principal_cannot_read_a_withheld_new_source():
    record, by_key = rows("financial-services")
    key = "adverse-pacific-foods"
    principal = {"datasets": ["financial-services"], "role": "restricted",
                 "withheld": [f"financial-services:source:{key}"]}
    projected = access.project_workspace({"datasets": [record], "reviewAvailable": False}, principal)
    row = next(s for s in projected["datasets"][0]["sources"] if s["id"].endswith(key))
    assert row["access"] == "withheld" and "citation" not in row
    assert "Pacific" not in json.dumps(row)


def test_originals_resolve_and_match_pins():
    originals = load_originals()
    for dataset in COUNTS:
        for entry in REGISTRY[dataset]:
            if not present(entry):
                continue
            original = originals[f"{dataset}:source:{entry['key']}"]
            assert original.digest == entry["sha256"]
            with checked_original(original) as stream:
                assert stream is not None


def test_export_is_current(capsys):
    assert (ROOT / workspace.EXPORT).read_bytes() == workspace.export_bytes()
