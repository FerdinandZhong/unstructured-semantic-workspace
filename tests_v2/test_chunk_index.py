"""Chunk index: V1 splitters through an adapter, one Lance row per chunk, three indexes, versions.

The vectors in this file are 4-dimensional mechanical test vectors. They are not model embeddings.
Recorded mistral-embed vectors live in tests_v2/fixtures when the live recording has run.
"""

from hashlib import sha256
import json
from urllib.parse import urlencode

import pytest

from multimedia_rag import chunking as v1
from semantic_pipeline_v2 import chunk_index, workspace_server
from semantic_pipeline_v2.core import EmbeddingSpace
from test_evidence_labeler import FixtureLabeler
from test_workspace import running_server

SPACE = EmbeddingSpace("test-vectors", "mechanical", 4, "chunk-retrieval")
REV1, REV2 = "a" * 64, "b" * 64
V1_KEYS = {"id", "document_id", "filename", "text", "start", "end", "strategy", "references"}


def prose(count=14):
    return [{"text": f"## Section {i}\n\nKiln {i} holds firing temperature near {1100 + i} degrees. "
                     + "Operators log each ramp step and compare it with the approved schedule. " * 2,
             "page": i // 5} for i in range(count)]


class KeywordEmbedder:
    """Maps text to a 4-d count vector of marker words. Deterministic and offline."""
    def embed(self, texts):
        words = ("kiln", "invoice", "audit", "pump")
        return [[float(t.lower().count(w)) + 0.01 for w in words] for t in texts]


class Failing:
    def embed(self, texts):
        raise RuntimeError("provider down")


# T5
@pytest.mark.parametrize("config", [
    v1.ChunkConfig(strategy="character", size=300, overlap=0),
    v1.ChunkConfig(strategy="token", size=120, overlap=12),
    v1.ChunkConfig(strategy="markdown", size=300, overlap=30),
    v1.ChunkConfig(strategy="separator", size=300, overlap=30),
])
def test_adapter_matches_v1_chunking(config):
    units = prose()
    text, spans = v1.assemble(units)
    expected = v1.chunks_for({"id": "doc", "name": "f.md", "kind": "document", "text": text, "spans": spans}, config)
    got = chunk_index.chunk_units(units, config, document_id="doc", name="f.md", kind="document")
    assert len(expected) > 1
    assert [{k: v for k, v in c.items() if k in V1_KEYS} for c in got] == expected


def test_auto_strategy_follows_v1_rule_for_transcripts():
    units = [{"text": f"Speaker line {i}. " * 30, "speaker": "RM", "startSeconds": i * 5.0, "endSeconds": i * 5.0 + 4}
             for i in range(6)]
    got = chunk_index.chunk_units(units, v1.ChunkConfig(strategy="auto"), document_id="d", name="call", kind="audio")
    assert {c["strategy"] for c in got} == {"separator"}


# T6
def test_empty_document_has_no_chunk():
    assert chunk_index.chunk_units([], v1.ChunkConfig(), document_id="d", name="f", kind="document") == []
    assert chunk_index.chunk_units(chunk_index.units_from_pages([{"index": 0, "markdown": "  \n "}]),
                                   v1.ChunkConfig(), document_id="d", name="f", kind="document") == []


def test_large_table_stays_whole_and_keeps_page():
    rows = "\n".join(f"| Item {i} | SGD {i * 10}.00 |" for i in range(80))
    markdown = "Intro paragraph about stock.\n\n| Name | Value |\n| --- | --- |\n" + rows + "\n\nClosing note."
    units = chunk_index.units_from_pages([{"index": 2, "markdown": markdown}])
    chunks = chunk_index.chunk_units(units, v1.ChunkConfig(), document_id="d", name="f", kind="document")
    tables = [c for c in chunks if c["element_kind"] == "table"]
    assert len(tables) == 1 and len(tables[0]["text"]) > 800
    assert "Item 0 " in tables[0]["text"] and "Item 79 " in tables[0]["text"]
    assert tables[0]["page"] == 2
    assert [c["order"] for c in chunks] == list(range(len(chunks)))
    assert any("Intro paragraph" in c["text"] for c in chunks) and any("Closing note" in c["text"] for c in chunks)


def test_transcript_keeps_speaker_and_time():
    observations = [{"quote": f"Turn {i}. " + "words " * 40, "citation": {
        "kind": "transcript", "speaker": "RM" if i % 2 else "Client",
        "startSeconds": i * 10.0, "endSeconds": i * 10.0 + 9}} for i in range(8)]
    units = chunk_index.units_from_observations(observations)
    chunks = chunk_index.chunk_units(units, v1.ChunkConfig(strategy="auto", size=300, overlap=30),
                                     document_id="d", name="call", kind="audio")
    assert len(chunks) > 1
    assert all(c["element_kind"] == "transcript" and c["speaker"] and c["end_seconds"] > c["start_seconds"] >= 0
               for c in chunks)
    assert chunks[0]["start_seconds"] == 0.0 and chunks[-1]["end_seconds"] == 79.0


def ingest(index, rev, source="ds:source:s1", scope="ds", embedder=None, units=None, **options):
    return index.ingest(scope=scope, source_id=source, revision=rev, units=units or prose(), kind="document",
                        config=v1.ChunkConfig(size=300, overlap=30), embedder=embedder, **options)


# T7
def test_three_indexes_hybrid_search_and_scope_prefilter(tmp_path):
    index = chunk_index.ChunkIndex(tmp_path, SPACE)
    ingest(index, REV1, "ds:source:kiln", "ds", KeywordEmbedder())
    audit = [{"text": f"Audit invoice {i} was reviewed by compliance. " * 8} for i in range(6)]
    ingest(index, REV1, "other:source:audit", "other", KeywordEmbedder(), units=audit)
    built = index.build_indexes()
    assert {"fts", "scalar:scope", "scalar:source_id"} <= set(built["built"])
    assert built["vector"]["status"] == "exact_search" and built["vector"]["threshold"] == chunk_index.VECTOR_INDEX_MIN_ROWS
    names = {i.index_type for i in index.table.list_indices()}
    assert {"FTS", "BTree"} <= names
    vector = KeywordEmbedder().embed(["audit invoice"])[0]
    result = index.search(["other"], "audit invoice", vector, limit=5)
    assert result["mode"] == "hybrid" and result["rows"]
    assert {r["scope"] for r in result["rows"]} == {"other"} and "vector" not in result["rows"][0]
    assert all("Audit" in r["text"] for r in result["rows"][:2])
    # the restricted scope gets none of the withheld rows even when they match best
    kiln_vector = KeywordEmbedder().embed(["kiln temperature"])[0]
    hidden = index.search(["other"], "kiln temperature", kiln_vector, limit=20)
    assert all(r["scope"] == "other" for r in hidden["rows"])
    assert not any(r["source_id"] == "ds:source:kiln" for r in hidden["rows"])
    withheld = index.search(["ds", "other"], "audit invoice", vector, exclude_sources=["other:source:audit"], limit=20)
    assert not any(r["source_id"] == "other:source:audit" for r in withheld["rows"])
    with pytest.raises(ValueError):
        index.search([], "kiln", vector)
    keyword = index.search(["ds"], "firing temperature", None)
    assert keyword["mode"] == "keyword" and keyword["rows"] and {r["scope"] for r in keyword["rows"]} == {"ds"}


def test_vector_index_is_built_at_the_threshold(tmp_path, monkeypatch):
    monkeypatch.setattr(chunk_index, "VECTOR_INDEX_MIN_ROWS", 5)
    index = chunk_index.ChunkIndex(tmp_path, SPACE)
    ingest(index, REV1, embedder=KeywordEmbedder())
    built = index.build_indexes()
    assert built["vector"]["status"] == "built" and "vector" in built["built"]
    assert "IvfFlat" in {i.index_type for i in index.table.list_indices()}
    assert index.search(["ds"], "kiln", KeywordEmbedder().embed(["kiln"])[0])["rows"]


def test_null_vectors_do_not_break_hybrid_search(tmp_path):
    index = chunk_index.ChunkIndex(tmp_path, SPACE)
    ingest(index, REV1, "ds:source:a", embedder=KeywordEmbedder())
    ingest(index, REV1, "ds:source:b", embedder=Failing(), units=[{"text": "Pump seal maintenance log " * 20}])
    index.build_indexes()
    out = index.search(["ds"], "pump seal", KeywordEmbedder().embed(["pump"])[0])
    assert out["mode"] == "hybrid" and out["rows"]


# T8
def test_new_revision_keeps_old_version_readable_and_same_revision_is_idempotent(tmp_path):
    index = chunk_index.ChunkIndex(tmp_path, SPACE)
    first = ingest(index, REV1, embedder=KeywordEmbedder())
    count = index.table.count_rows()
    again = ingest(index, REV1, embedder=KeywordEmbedder())
    assert index.table.count_rows() == count == first["rows"] == again["rows"]
    second = ingest(index, REV2, units=prose()[:6], embedder=KeywordEmbedder())
    current = index.read("ds", "ds:source:s1")
    assert {r["source_revision"] for r in current} == {REV2} and len(current) == second["rows"]
    old = index.read_revision("ds:source:s1", REV1)
    assert len(old) == count and {r["source_revision"] for r in old} == {REV1}
    assert old != current
    with pytest.raises(KeyError):
        index.read_revision("ds:source:s1", "c" * 64)
    assert chunk_index.ChunkIndex(tmp_path, SPACE).read_revision("ds:source:s1", REV1)  # survives reopen


def test_embedding_space_mismatch_fails_closed(tmp_path):
    chunk_index.ChunkIndex(tmp_path, SPACE)
    with pytest.raises(ValueError):
        chunk_index.ChunkIndex(tmp_path, EmbeddingSpace("test-vectors", "mechanical", 8, "chunk-retrieval"))


# T9
@pytest.mark.parametrize("embedder, reason", [(Failing(), "RuntimeError"), (None, "no_embedder_configured")])
def test_embedder_failure_stores_chunks_without_invented_vectors(tmp_path, embedder, reason):
    index = chunk_index.ChunkIndex(tmp_path, SPACE)
    status = ingest(index, REV1, embedder=embedder)
    assert status["embedding"] == {"status": "not embedded", "reason": reason}
    assert status["rows"] > 1
    rows = index.table.to_arrow().to_pylist()
    assert all(r["vector"] is None and r["embed_status"] == "not embedded" and r["embed_reason"] == reason
               and r["embedding_space"] is None for r in rows)
    assert index.search(["ds"], "firing temperature", None)["rows"]
    healed = ingest(index, REV1, embedder=KeywordEmbedder())
    assert healed["embedding"]["status"] == "embedded" and index.table.count_rows() == status["rows"]


def test_bad_vectors_count_as_not_embedded(tmp_path):
    class Short:
        def embed(self, texts):
            return [[1.0] for _ in texts]
    status = ingest(chunk_index.ChunkIndex(tmp_path, SPACE), REV1, embedder=Short())
    assert status["embedding"]["status"] == "not embedded"


def test_rows_carry_the_documented_fields_and_labels(tmp_path):
    index = chunk_index.ChunkIndex(tmp_path, SPACE)
    label = {"quote": "firing temperature near 1103 degrees", "definitionId": "m:def/Temp"}
    ingest(index, REV1, embedder=KeywordEmbedder(), labels=[label])
    rows = index.table.to_arrow().to_pylist()
    row = next(r for r in rows if "m:def/Temp" in r["labels"])
    assert "1103" in row["text"]
    assert row["content_hash"] == sha256(index.artifact(row["content_hash"]).encode()).hexdigest()
    assert row["catalog_entry_id"] is None and row["catalog_version"] is None
    assert row["embedding_space"] == SPACE.identity and row["embed_status"] == "embedded"
    for name in ("chunk_id", "scope", "source_id", "source_revision", "order", "page", "start_offset",
                 "end_offset", "element_kind", "chunk_strategy", "chunk_size", "chunk_overlap",
                 "chunker_revision", "created_at"):
        assert row[name] is not None
    assert [r["order"] for r in sorted(rows, key=lambda r: r["order"])] == list(range(len(rows)))


# Server
def test_ingest_writes_chunk_rows_and_readers_without_access_are_refused(tmp_path, monkeypatch):
    dataset = "financial-services"
    source = f"{dataset}:source:jade-adverse-media"
    principal = {"id": "admin", "role": "admin", "datasets": [dataset], "withheld": []}
    monkeypatch.setattr(workspace_server.WorkspaceRequest, "principal", lambda _: principal)
    monkeypatch.setattr(workspace_server, "resolve_session", lambda *_: principal)
    labeler = FixtureLabeler(lambda anchors, definitions: {"labels": []})
    query = urlencode({"dataset": dataset, "sourceId": source})
    body = {"dataset": dataset, "sourceId": source, "mode": "text"}
    with running_server(evidence_dir=tmp_path / "evidence", evidence_labeler=labeler,
                        chunk_embedder=KeywordEmbedder(), chunk_space=SPACE) as call:
        assert call("GET", "/api/chunks?" + query) == (200, {"chunks": []})
        status, result = call("POST", "/api/ocr", body)
        assert status == 200 and result["chunkIndex"]["status"] == "saved"
        assert result["chunkIndex"]["rows"] >= 1 and result["chunkIndex"]["embedding"]["status"] == "embedded"
        assert "passages" in json.dumps(result["evidence"])  # UI payload unchanged
        status, read = call("GET", "/api/chunks?" + query)
        assert status == 200 and read["chunks"] and all("vector" not in c for c in read["chunks"])
        assert "opaque cross-border payment flows" in " ".join(c["text"] for c in read["chunks"])
        principal["withheld"] = [source]
        assert call("GET", "/api/chunks?" + query)[0] == 403
        principal["withheld"] = []
        principal["datasets"] = []
        assert call("GET", "/api/chunks?" + query)[0] == 403


def test_ingest_without_embedder_reports_not_embedded(tmp_path, monkeypatch):
    dataset = "financial-services"
    source = f"{dataset}:source:jade-adverse-media"
    principal = {"id": "admin", "role": "admin", "datasets": [dataset], "withheld": []}
    monkeypatch.setattr(workspace_server.WorkspaceRequest, "principal", lambda _: principal)
    monkeypatch.setattr(workspace_server, "resolve_session", lambda *_: principal)
    labeler = FixtureLabeler(lambda anchors, definitions: {"labels": []})
    with running_server(evidence_dir=tmp_path / "evidence", evidence_labeler=labeler,
                        chunk_embedder=None, chunk_space=SPACE) as call:
        status, result = call("POST", "/api/ocr", {"dataset": dataset, "sourceId": source, "mode": "text"})
        assert status == 200 and result["chunkIndex"]["embedding"] == {
            "status": "not embedded", "reason": "no_embedder_configured"}


# P0: the rows of a (source, revision) are one replaceable set, and a failed add keeps the previous rows
def test_reingest_same_revision_replaces_rows_instead_of_appending(tmp_path):
    index = chunk_index.ChunkIndex(tmp_path, SPACE)
    for size in (300, 500):
        index.ingest(scope="ds", source_id="ds:source:s1", revision=REV1, units=prose(), kind="document",
                     config=v1.ChunkConfig(size=size, overlap=30))
    rows = index.read("ds", "ds:source:s1")
    assert {r["chunk_size"] for r in rows} == {500}
    assert len(rows) == len({r["chunk_id"] for r in rows}) == index.table.count_rows()
    index.ingest(scope="ds", source_id="ds:source:s1", revision=REV1, units=[], kind="document")
    assert index.read("ds", "ds:source:s1") == []


def test_failed_add_leaves_previous_rows_readable(tmp_path, monkeypatch):
    index = chunk_index.ChunkIndex(tmp_path, SPACE)
    ingest(index, REV1)
    before = index.read("ds", "ds:source:s1")
    assert before

    def failing_add(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(type(index.table), "add", failing_add)
    with pytest.raises(OSError):
        ingest(index, REV2)
    monkeypatch.undo()
    assert index.read("ds", "ds:source:s1") == before
