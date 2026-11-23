"""Demo preparation: ingest through the server routes, then build the catalog. Fakes for every provider."""

import io
import json
from pathlib import Path

import pytest

from semantic_pipeline_v2 import prepare_demo, workspace_server
from semantic_pipeline_v2.interpreter import ChatError, Interpreter
from semantic_pipeline_v2.originals import Original
from test_catalog_build import Chat, Embedder
from test_catalog_graph import pg_bin
from test_evidence_labeler import FixtureLabeler

D = "financial-services"
S = lambda key: f"{D}:source:{key}"
REPLY = json.dumps({"interpretation": "A fake reading.", "links": []})


class Interp:
    available, model = True, "scripted"

    def __init__(self, fail_marker=None, junk_marker=None):
        self.fail_marker, self.junk_marker, self.calls = fail_marker, junk_marker, 0

    def complete(self, system, user):
        self.calls += 1
        if self.fail_marker and self.fail_marker in user:
            raise ChatError("provider down")
        if self.junk_marker and self.junk_marker in user:
            return "not json"
        return REPLY


BUILDS = []


@pytest.fixture(autouse=True)
def stub_catalog(monkeypatch):
    """The catalog agent's code is under change: this test only needs the build and confirm routes to be driven."""
    from semantic_pipeline_v2 import concept_catalog
    BUILDS.clear()

    def build(dataset, chunks, records, chat, embedder, *, now=None, previous=None):
        BUILDS.append(len(records))
        return {"version": "cat-fake" + str(len(records)), "stats": {}, "concepts": [
            {"id": "c1", "label": "Fake", "origin": "induced", "status": "proposed", "links": [],
             "evidence": {"chunks": []}}]}

    monkeypatch.setattr(concept_catalog, "build_from_links", build)


def run(tmp_path, *, say=None, interp=None, catalog=None, **kw):
    out = []
    interp = interp or Interp()
    catalog = catalog or Chat()
    report = prepare_demo.prepare(
        tmp_path / "state", pg_bin(), datasets=[D], say=say or out.append, sleep=lambda s: None,
        chunk_embedder=Embedder(), mapping_embedder=None, interpreter=Interpreter(interp),
        evidence_labeler=FixtureLabeler(lambda a, d: {"labels": []}), catalog_chat=catalog, **kw)
    return report, out, interp, catalog


KEYS = ["FIN-001", "FIN-002", "FIN-003"]
ONLY = [S(k) for k in KEYS]


def test_order_holdout_manifest_and_no_passage_text(tmp_path):
    report, out, interp, catalog = run(tmp_path, only=ONLY, hold_out=[S("FIN-003")])
    text = "\n".join(out)
    assert report["failed"] == 0
    # ingest lines come before the catalog lines
    assert text.index("FIN-001") < text.index("FIN-002") < text.index("catalog")
    assert "FIN-003" in text and "held out" in text
    m = json.loads((tmp_path / "state" / "prepared-manifest.json").read_text())
    ds = m["datasets"][D]
    assert set(ds["sources"]) == {S("FIN-001"), S("FIN-002")} and ds["heldOut"] == [S("FIN-003")]
    for rec in ds["sources"].values():
        assert len(rec["revision"]) == 64 and rec["chunks"] >= 1 and "seconds" in rec
    assert ds["catalog"]["version"].startswith("cat-") and m["codeHash"] and m["preparedAt"] and m["models"][D]
    assert m["complete"] is False   # a held-out source is named, the rest is complete
    assert m["calls"]["interpreter"] >= 1
    # no passage text and no key in the output
    assert "SYNTHETIC DEMO DOCUMENT" not in text and "Document ID" not in text


def test_second_run_does_nothing_new(tmp_path):
    run(tmp_path, only=ONLY)
    assert len(BUILDS) == 1
    _, out, interp, catalog = run(tmp_path, only=ONLY)
    assert len(BUILDS) == 1 and interp.calls == 0
    assert "already ingested" in "\n".join(out) and "already built" in "\n".join(out)


def test_failing_source_does_not_stop_the_run_and_exit_is_nonzero(tmp_path):
    report, out, *_ = run(tmp_path, only=ONLY, interp=Interp(fail_marker="FIN-002"))
    assert report["failed"] == 1 and report["exit"] == 1
    m = json.loads((tmp_path / "state" / "prepared-manifest.json").read_text())["datasets"][D]
    assert S("FIN-001") in m["sources"] and S("FIN-003") in m["sources"] and S("FIN-002") not in m["sources"]
    assert m["failed"][S("FIN-002")]
    # a later run with a healthy provider repairs it
    report, *_ = run(tmp_path, only=ONLY)
    assert report["failed"] == 0 and report["exit"] == 0


def test_absent_file_is_reported_and_skipped(tmp_path, monkeypatch):
    real = workspace_server.load_originals

    def load():
        found = real()
        found[S("FIN-002")] = Original(Path(tmp_path), Path("nowhere.md"), "0" * 64, None)
        return found

    monkeypatch.setattr(workspace_server, "load_originals", load)
    report, out, *_ = run(tmp_path, only=ONLY)
    assert report["failed"] == 0
    assert any("FIN-002" in line and "file absent" in line for line in out)
    m = json.loads((tmp_path / "state" / "prepared-manifest.json").read_text())["datasets"][D]
    assert "FIN-002" in str(m["skipped"])


def test_unusable_model_reply_is_stored_degraded_not_retried_and_not_a_failure(tmp_path):
    interp = Interp(junk_marker="FIN-002")
    report, out, *_ = run(tmp_path, only=ONLY, interp=interp)
    assert report["failed"] == 0
    assert any("FIN-002" in line and "degraded=1" in line for line in out)
    _, out, again, _ = run(tmp_path, only=ONLY)
    assert again.calls == 0 and "FIN-002: already ingested" in "\n".join(out)
