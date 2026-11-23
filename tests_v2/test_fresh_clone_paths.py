"""AMP-T3 support (plan 2026-11-23-v2-zh-amp, A3): paths outside the repository are optional and configurable.

With no variable set the source tree keeps today's locations; a fresh clone without them still loads."""
from pathlib import Path

import pytest

from semantic_pipeline_v2 import concept_catalog as cc, core, originals
from semantic_pipeline_v2.workspace import ROOT


def test_local_originals_folder_comes_from_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("V2_LOCAL_ORIGINALS", str(tmp_path))
    found = originals.load_originals()["financial-services:source:cleaned_pdf_1"]
    assert found.root == tmp_path


def test_local_originals_default_to_downloads(monkeypatch):
    monkeypatch.delenv("V2_LOCAL_ORIGINALS", raising=False)
    assert originals.load_originals()["financial-services:source:cleaned_pdf_1"].root == Path.home() / "Downloads"


def test_the_v1_checkout_comes_from_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("V2_V1_REPO", str(tmp_path))
    assert core.v1_repository() == tmp_path
    assert originals.load_originals()["financial-services:source:jade_call"].root == tmp_path


def test_the_v1_checkout_default_is_the_single_sibling_with_that_suffix(monkeypatch, tmp_path):
    monkeypatch.delenv("V2_V1_REPO", raising=False)
    siblings = sorted(ROOT.parent.glob("*-knowledge-journey"))
    expected = siblings[0] if len(siblings) == 1 else None
    found = core.v1_repository()
    assert found.parent == ROOT.parent and found.name.endswith("-knowledge-journey")
    if expected is not None:      # this machine: the existing sibling, as before the change
        assert found == expected
    assert core.PROTECTED_ROOTS[0] == found


def test_schema_check_is_skipped_when_the_schema_file_is_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(cc, "SCHEMA", tmp_path / "absent.json")
    assert cc.schema_valid({"anything": 1}) is None


@pytest.mark.skipif(not cc.SCHEMA.is_file(), reason="sibling schema checkout absent")
def test_schema_check_runs_when_present():
    assert cc.schema_valid({"not": "a model"}) is False
