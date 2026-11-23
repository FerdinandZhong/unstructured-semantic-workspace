"""Start-up model import and the admin catalog build (preview, then confirm). No live call: scripted chat and embedder."""

import io
import json
from pathlib import Path
from urllib.parse import urlencode

import numpy as np
import pytest

from semantic_pipeline_v2.chunk_index import ChunkIndex, units_from_pages
from semantic_pipeline_v2.core import EmbeddingSpace
from semantic_pipeline_v2.evidence_links import EvidenceLinks
from semantic_pipeline_v2.semantic_store import SemanticStore
from semantic_pipeline_v2.workspace import review_database
from semantic_pipeline_v2.workspace_server import create_server, import_models
from test_catalog_graph import link, pg_bin, session
from test_workspace import running_server

SPACE = EmbeddingSpace("fake", "v1", 8, "test")
DATASET = "financial-services"
SOURCE = f"{DATASET}:source:finance-note"


class Embedder:
    space = SPACE

    def embed(self, texts):
        return [np.eye(8)[len(t) % 8].tolist() for t in texts]


class Chat:
    available = True

    def __init__(self):
        self.calls = 0

    def complete(self, system, user):
        self.calls += 1   # one valid empty reply per task: since 2026-11-22 a chunk with a stated value is also induced
        task = json.loads(user).get("task")
        return json.dumps({"same": []} if task == "same" else {"decisions": []} if task == "match" else {"concepts": []})


def field(n):
    from semantic_pipeline_v2.workspace import load_workspace
    return load_workspace(DATASET)["knowledge"]["structured"]["fields"][n]["id"]


def ingest(tmp_path, database):
    """One source in the chunk index, one stored evidence link per chunk."""
    index = ChunkIndex(tmp_path / "evidence" / "chunk-index", SPACE)
    index.ingest(scope=DATASET, source_id=SOURCE, revision="b" * 64, kind="document", name="Note",
                 units=units_from_pages([{"index": 0, "markdown": "Customer NRIC S1234567D settles within 9 days."}]))
    definition = field(0)
    rows = index.read(DATASET, SOURCE)
    EvidenceLinks(database).replace(DATASET, SOURCE, [
        link(r["chunk_id"], SOURCE, definition, r["text"]) for r in rows])
    return rows


def test_import_models_is_explicit_idempotent_and_survives_a_broken_path(tmp_path):
    with review_database(tmp_path / "state", pg_bin()) as database:
        store = SemanticStore(database)
        say = io.StringIO()
        assert store.list_definitions(DATASET) == []
        broken = {"manufacturing": tmp_path / "missing.yaml"}
        import_models(database, say=lambda m: say.write(m + "\n"), only=broken)
        assert "manufacturing: model import failed" in say.getvalue() and store.list_definitions(DATASET) == []
        say = io.StringIO()
        import_models(database, say=lambda m: say.write(m + "\n"))
        out = say.getvalue()
        assert "financial-services: model imported" in out and "manufacturing: model imported" in out, out
        assert store.list_definitions(DATASET) and store.list_definitions("manufacturing")
        say = io.StringIO()
        import_models(database, say=lambda m: say.write(m + "\n"))
        assert say.getvalue().count("already present") == 2


def test_graph_is_empty_without_the_import_and_has_definitions_with_it(tmp_path):
    query = "?" + urlencode({"dataset": DATASET})
    with review_database(tmp_path / "state", pg_bin()) as database:
        with running_server(database, evidence_dir=tmp_path / "evidence") as call:
            assert call("GET", "/api/graph" + query)[1]["empty"] is True
        import_models(database, say=lambda m: None)
        with running_server(database, evidence_dir=tmp_path / "evidence") as call:
            graph = call("GET", "/api/graph" + query)[1]
            assert not graph["empty"] and {n["kind"] for n in graph["nodes"]} == {"definition"}


def test_catalog_build_preview_writes_nothing_and_confirm_writes_that_version(tmp_path):
    with review_database(tmp_path / "state", pg_bin()) as database:
        import_models(database, say=lambda m: None)
        ingest(tmp_path, database)
        chat = Chat()
        store = SemanticStore(database)
        with running_server(database, evidence_dir=tmp_path / "evidence", chunk_embedder=Embedder(),
                            catalog_chat=chat) as call:
            body = {"dataset": DATASET}
            status, preview = call("POST", "/api/catalog/build/preview", body)
            assert status == 200, preview
            assert preview["version"].startswith("cat-") and preview["counts"]["concepts"] == len(preview["concepts"])
            assert "S1234567D" not in str(preview)
            assert preview["counts"]["concepts"] >= 1 and preview["counts"]["links"] >= 1
            assert store.latest_catalog_version(DATASET) is None          # nothing written
            assert call("POST", "/api/catalog/build/confirm", {**body, "version": "cat-other"})[0] == 409
            status, done = call("POST", "/api/catalog/build/confirm", {**body, "version": preview["version"]})
            assert status == 200 and done["version"] == preview["version"]
            assert store.latest_catalog_version(DATASET) == preview["version"]
            assert len(store.read_catalog(DATASET, preview["version"])) == len(preview["concepts"])
            # a reader gets 403 on both
            reader = session(call, "finance_reader")
            assert call("POST", "/api/catalog/build/preview", body, reader)[0] == 403
            assert call("POST", "/api/catalog/build/confirm", {**body, "version": "x"}, reader)[0] == 403


def test_confirm_is_refused_when_links_changed_since_the_preview(tmp_path):
    with review_database(tmp_path / "state", pg_bin()) as database:
        import_models(database, say=lambda m: None)
        rows = ingest(tmp_path, database)
        with running_server(database, evidence_dir=tmp_path / "evidence", chunk_embedder=Embedder(),
                            catalog_chat=Chat()) as call:
            body = {"dataset": DATASET}
            version = call("POST", "/api/catalog/build/preview", body)[1]["version"]
            definition = field(1)
            EvidenceLinks(database).replace(DATASET, SOURCE, [link(r["chunk_id"], SOURCE, definition, r["text"]) for r in rows])
            status, error = call("POST", "/api/catalog/build/confirm", {**body, "version": version})
            assert status == 409 and "changed" in error["error"]
            assert SemanticStore(database).latest_catalog_version(DATASET) is None


def test_build_is_refused_without_a_chat_client_and_without_sources(tmp_path):
    with review_database(tmp_path / "state", pg_bin()) as database:
        import_models(database, say=lambda m: None)
        with running_server(database, evidence_dir=tmp_path / "evidence", chunk_embedder=Embedder(),
                            catalog_chat=None) as call:
            body = {"dataset": DATASET}
            status, error = call("POST", "/api/catalog/build/preview", body)
            assert status == 503 and "chat" in error["error"].lower()
            assert call("POST", "/api/catalog/build/confirm", {**body, "version": "x"})[0] == 503
        with running_server(database, evidence_dir=tmp_path / "evidence", chunk_embedder=Embedder(),
                            catalog_chat=Chat()) as call:
            status, error = call("POST", "/api/catalog/build/preview", {"dataset": DATASET})
            assert status == 409 and "ingest" in error["error"].lower()


def test_after_a_confirmed_version_a_new_source_is_previewed_as_an_incremental_update(tmp_path):
    """Task 5 (2026-11-22): the same preview and confirm route; a confirmed version plus one new source gives an
    incremental preview that names the new source and keeps every earlier concept id."""
    second = f"{DATASET}:source:second-note"
    with review_database(tmp_path / "state", pg_bin()) as database:
        import_models(database, say=lambda m: None)
        ingest(tmp_path, database)
        index = ChunkIndex(tmp_path / "evidence" / "chunk-index", SPACE)   # chunks now, links after the confirm
        index.ingest(scope=DATASET, source_id=second, revision="c" * 64, kind="document", name="Second",
                     units=units_from_pages([{"index": 0, "markdown": "The customer pays within 30 days."}]))
        with running_server(database, evidence_dir=tmp_path / "evidence", chunk_embedder=Embedder(),
                            catalog_chat=Chat()) as call:
            body = {"dataset": DATASET}
            first = call("POST", "/api/catalog/build/preview", body)[1]
            assert first["incremental"] is None
            assert call("POST", "/api/catalog/build/confirm", {**body, "version": first["version"]})[0] == 200
            EvidenceLinks(database).replace(DATASET, second, [
                {**link(r["chunk_id"], second, field(0), r["text"]), "source_revision": "c" * 64}
                for r in index.read(DATASET, second)])
            status, nxt = call("POST", "/api/catalog/build/preview", body)
            assert status == 200, nxt
            assert nxt["incremental"]["new_sources"] == [second] and nxt["version"] != first["version"]
            assert {c["id"] for c in first["concepts"]} <= {c["id"] for c in nxt["concepts"]}
