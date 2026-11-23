"""Chunk index: the metadata of unstructured data.

One LanceDB row per chunk with text, position, labels, scope, vector and (later) a catalog entry.
V1 splitters run unchanged through `multimedia_rag.chunking`; this module only shapes their input
and output. Single writer, like `core.Evidence`.

Why a separate table, not `core.Evidence`: that table needs a non-null vector, a fixed column set
and chunk text that is a byte substring of the raw artifact. A chunk row needs a nullable vector
(embedding can fail), source revision, labels, time and speaker fields. `EmbeddingSpace` and its
manifest check are reused so one index never mixes vector spaces.
"""

from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
from importlib import metadata
import json
import math
import os
import re
import sys
import time
from pathlib import Path

from multimedia_rag.chunking import ChunkConfig, assemble, chunks_for

from .core import EmbeddingSpace, atomic_json

# ponytail: IVF_FLAT needs no codebook training and trained fine on 10 rows in lancedb 0.41.0, but an
# index only pays off on larger tables. Below this row count search is exact (brute force).
VECTOR_INDEX_MIN_ROWS = 256
BATCH = 16
EMBED_MODEL, EMBED_DIMENSION = "mistral-embed", 1024
TABLE = "chunks"
_TABLE_LINE = re.compile(r"^\s*\|.*\|\s*$")


# ---------------------------------------------------------------- units and chunking

def _split_tables(markdown):
    """Yield (is_table, text) blocks; a table is a run of pipe-delimited lines."""
    block, table = [], False
    for line in markdown.split("\n"):
        is_row = bool(_TABLE_LINE.match(line))
        if is_row != table and block:
            yield table, "\n".join(block)
            block = []
        table = is_row
        block.append(line)
    if block:
        yield table, "\n".join(block)


def field_passages_enabled():
    """On by default; `V2_FIELD_PASSAGES=0` turns `_field_units` off."""
    return os.environ.get("V2_FIELD_PASSAGES", "").strip() != "0"


def units_from_pages(pages, *, fields=None):
    """OCR pages, local text and XFA field listings: pages of markdown.

    With `fields` (default `V2_FIELD_PASSAGES`) a detached OCR table is read in place of its `[tbl-N.md](tbl-N.md)`
    reference, and a label-and-value layout becomes one `field` unit per pair (see `_field_units`)."""
    fields = field_passages_enabled() if fields is None else fields
    units = []
    for page in pages:
        markdown = _inline_tables(page) if fields else page.get("markdown") or ""
        for is_table, text in _split_tables(markdown):
            if text.strip():
                found = _field_units(text, is_table) if fields else None
                units += [{**u, "page": page["index"]} for u in found] if found else [
                    {"text": text, "page": page["index"], "element": "table" if is_table else "text"}]
    return units


# ---------------------------------------------------------------- label-and-value layouts (V2_FIELD_PASSAGES)

_LABEL = r"[A-Za-z][A-Za-z0-9 /&()'.,-]{0,59}"
_GAP_PAIR = re.compile(rf"^ {{0,3}}({_LABEL}?) {{3,}}(\S.{{0,300}}?)\s*$")   # pypdf layout: label, 3+ spaces, value
_BOLD = re.compile(r"^\*\*(.+?)\*\*$")
MIN_PAIRS = 3


def _label_like(text):
    return 0 < len(text.split()) <= 6 and not re.search(r"[.:;!?]$", text) and re.search(r"[A-Za-z]", text)


def _inline_tables(page):
    """The page markdown with each `[id](id)` reference to a detached markdown table replaced by the table."""
    from .ocr_tables import _REFERENCE, _reference_id
    tables = {_reference_id(t["id"]): t["content"] for t in page.get("tables") or ()
              if isinstance(t, dict) and isinstance(t.get("id"), str) and isinstance(t.get("content"), str)
              and not t["content"].lstrip().lower().startswith("<table")}   # ponytail: HTML tables stay references
    markdown = page.get("markdown") or ""
    return _REFERENCE.sub(lambda m: "\n" + tables[_reference_id(m[1])].strip() + "\n"
                          if _reference_id(m[1]) in tables else m[0], markdown)


def _field_units(text, is_table):
    """`field` units ("Label: value") for a label-and-value layout, else None (the block stays as it is).

    A table qualifies when it has two columns and every label cell is bold: a printed label column. A text block
    qualifies when at least MIN_PAIRS lines are a short label, three or more spaces, and a value that starts in one
    shared column (the layout text of a printed form); only lines with that value column are pairs. In such a block a
    short line between blank lines becomes a `## heading` text unit, so the passage context names its section; other
    lines stay text. Decided from layout only, never from a name or a title."""
    from .ocr_tables import _SEPARATOR, _pipe_cells
    if is_table:
        rows = [_pipe_cells(line) for line in text.split("\n") if line.strip()]
        rows = [r for r in rows if r and not all(_SEPARATOR.match(c) for c in r)]
        pairs = [(_BOLD.match(r[0]), r[1]) for r in rows if len(r) == 2]
        if len(rows) < 2 or len(pairs) != len(rows) or not all(b and _label_like(b[1]) and v for b, v in pairs):
            return None
        return [{"text": f"{b[1].strip()}: {v}", "element": "field"} for b, v in pairs]
    lines = text.split("\n")
    matches = [m if m and _label_like(m[1]) else None for m in map(_GAP_PAIR.match, lines)]
    columns = Counter(m.start(2) for m in matches if m)
    column, count = columns.most_common(1)[0] if columns else (None, 0)
    if count < MIN_PAIRS:
        return None
    matches = [m if m and m.start(2) == column else None for m in matches]
    units, prose = [], []

    def flush():
        if "\n".join(prose).strip():
            units.append({"text": "\n".join(prose).strip(), "element": "text"})
        prose.clear()
    for n, (line, match) in enumerate(zip(lines, matches)):
        alone = not lines[n - 1].strip() if n else True
        alone = alone and (n + 1 == len(lines) or not lines[n + 1].strip())
        if match:
            flush()
            units.append({"text": f"{match[1].strip()}: {match[2].strip()}", "element": "field"})
        elif alone and _label_like(line.strip()) and not re.search(r"\d", line):   # a heading line between blanks
            flush()
            prose.append("## " + line.strip())
        else:
            prose.append(line.strip())
    flush()
    return units


def units_from_observations(observations):
    """Transcript segments with speaker and time (citation fields of media_transcript)."""
    units = []
    for item in observations:
        ref = item["citation"]
        units.append({"text": item["quote"], "element": "transcript", "speaker": ref.get("speaker"),
                      "startSeconds": ref.get("startSeconds"), "endSeconds": ref.get("endSeconds")})
    return units


def chunk_units(units, config, *, document_id, name, kind):
    """V1 chunks for the units. A table is never split. Adds order, page, speaker, seconds."""
    text, spans = assemble(units)
    kept = [u for u in units if u.get("text", "").strip()]
    chunks, run = [], []

    def flush():
        if not run:
            return
        first, last = run[0], run[-1]
        lo = spans[first]["start"]
        doc = {"id": document_id, "name": name, "kind": kind, "text": text[lo:spans[last]["end"]],
               "spans": [{**spans[i], "start": spans[i]["start"] - lo, "end": spans[i]["end"] - lo}
                         for i in range(first, last + 1)]}
        for chunk in chunks_for(doc, config):
            chunk["start"] += lo
            chunk["end"] += lo
            chunk["references"] = [{**r, "start": r["start"] + lo, "end": r["end"] + lo}
                                   for r in chunk["references"]]
            chunks.append(chunk)
        run.clear()

    for i, unit in enumerate(kept):
        element = unit.get("element")
        if element not in ("table", "field"):   # a `field` unit exists only with V2_FIELD_PASSAGES
            run.append(i)
            continue
        flush()
        key = f"{document_id}:{config.model_dump_json()}:{element}:{i}:{unit['text'].strip()}"
        chunks.append({"id": sha256(key.encode()).hexdigest()[:20], "document_id": document_id,
                       "filename": name, "text": unit["text"].strip(), "start": spans[i]["start"],
                       "end": spans[i]["end"], "strategy": f"whole_{element}", "references": [spans[i]]})
    flush()
    chunks.sort(key=lambda c: c["start"])
    for order, chunk in enumerate(chunks):
        refs = chunk["references"]
        pages = [r["page"] for r in refs if r.get("page") is not None]
        starts = [r["startSeconds"] for r in refs if r.get("startSeconds") is not None]
        ends = [r["endSeconds"] for r in refs if r.get("endSeconds") is not None]
        speakers = list(dict.fromkeys(r["speaker"] for r in refs if r.get("speaker")))
        kinds = {r.get("element", "text") for r in refs}
        chunk.update(order=order, page=min(pages) if pages else None,
                     start_seconds=min(starts) if starts else None,
                     end_seconds=max(ends) if ends else None,
                     speaker=", ".join(speakers) or None,
                     element_kind=kinds.pop() if len(kinds) == 1 else "text")
    return chunks


# ---------------------------------------------------------------- embedder

class MistralEmbedder:
    """`mistral-embed`, 1024 dimensions, batches of 16. The key comes only from the caller."""

    def __init__(self, api_key, *, client=None):
        if not api_key and client is None:
            raise ValueError("an API key is required")
        self.space = EmbeddingSpace(EMBED_MODEL, "provider-alias", EMBED_DIMENSION, "chunk-retrieval")
        self._client = client
        self._key = api_key

    def embed(self, texts):
        if self._client is None:
            from mistralai.client import Mistral
            self._client = Mistral(api_key=self._key)
        vectors = []
        for i in range(0, len(texts), BATCH):
            batch = texts[i:i + BATCH]
            response = self._create(batch)
            rows = sorted(response.data, key=lambda r: r.index)
            if [r.index for r in rows] != list(range(len(batch))):
                raise ValueError("embedding batch incomplete")
            vectors.extend(list(r.embedding) for r in rows)
        return vectors


    def _create(self, batch):
        """One provider call, repeated after a dropped connection, a timeout or a provider error (bounded: the waits
        of EMBED_BACKOFF). The last error is raised unchanged."""
        for wait in (*EMBED_BACKOFF, None):
            try:
                return self._client.embeddings.create(model=EMBED_MODEL, inputs=batch)
            except Exception as error:  # noqa: BLE001 - the SDK raises several types; the message is never logged
                if wait is None:
                    raise
                print(f"embedding retry after {type(error).__name__}; waiting {wait}s", file=sys.stderr, flush=True)
                time.sleep(wait)


EMBED_BACKOFF = (2, 10, 30)   # seconds before each repeat of one embedding batch


def configured_embedder(environ):
    key = environ.get("MISTRAL_API_KEY", "").strip()
    return MistralEmbedder(key) if key else None


# ---------------------------------------------------------------- table

def _q(value):
    return "'" + str(value).replace("'", "''") + "'"


def _ts(moment):
    return "timestamp '" + moment.strftime("%Y-%m-%d %H:%M:%S.%f") + "'"


def _schema(dimension):
    import pyarrow as pa
    s, i = pa.string(), pa.int64()
    return pa.schema([
        ("chunk_id", s), ("workspace_id", s), ("scope", s), ("source_id", s), ("source_revision", s),
        ("content_hash", s), ("order", i), ("page", i), ("start_offset", i), ("end_offset", i),
        ("start_seconds", pa.float64()), ("end_seconds", pa.float64()), ("speaker", s),
        ("element_kind", s), ("chunk_strategy", s), ("chunk_size", i), ("chunk_overlap", i),
        ("chunker_revision", s), ("text", s), ("labels", pa.list_(s)),
        ("catalog_entry_id", s), ("catalog_version", s),
        ("embedding_space", s), ("vector", pa.list_(pa.float32(), dimension)),
        ("embed_status", s), ("embed_reason", s), ("created_at", pa.timestamp("us", tz="UTC")),
    ])


def _chunker_revision():
    try:
        return "v1-chunking+mistralai-search-toolkit==" + metadata.version("mistralai-search-toolkit")
    except metadata.PackageNotFoundError:
        return "v1-chunking"


class ChunkIndex:
    def __init__(self, root, space):
        import lancedb
        self.space, self.root = space, Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        manifest = self.root / "embedding-space.json"
        if manifest.exists() and json.loads(manifest.read_text()) != self._space_dict():
            raise ValueError("embedding space mismatch")
        self.db = lancedb.connect(str(self.root / "lance"))
        schema = _schema(space.dimension)
        self.table = self.db.create_table(TABLE, schema=schema, exist_ok=True)
        if not self.table.schema.equals(schema):
            raise ValueError("persisted chunk schema mismatch")
        if self.table.count_rows(f"embedding_space IS NOT NULL AND embedding_space != {_q(space.identity)}"):
            raise ValueError("persisted row embedding space mismatch")
        atomic_json(manifest, self._space_dict())
        (self.root / "artifacts").mkdir(exist_ok=True)

    def _space_dict(self):
        from dataclasses import asdict
        return asdict(self.space)

    def artifact(self, content_hash):
        """The assembled extraction text that chunk offsets refer to."""
        if not re.fullmatch("[a-f0-9]{64}", content_hash):
            raise ValueError("invalid content hash")
        return (self.root / "artifacts" / content_hash).read_text()

    # -- write
    def ingest(self, *, scope, source_id, revision, units, kind, config=None, embedder=None,
               labels=(), name=None):
        if not scope or not source_id or not re.fullmatch("[a-f0-9]{64}", revision):
            raise ValueError("scope, source id and a sha256 revision are required")
        config = config or ChunkConfig()
        text, _ = assemble(units)
        content_hash = sha256(text.encode()).hexdigest()
        (self.root / "artifacts" / content_hash).write_text(text)
        chunks = chunk_units(units, config, document_id=f"{source_id}@{revision}",
                             name=name or source_id, kind=kind)
        vectors, status = self._embed(embedder, [c["text"] for c in chunks])
        now, revision_tag = datetime.now(timezone.utc), _chunker_revision()
        rows = []
        for chunk, vector in zip(chunks, vectors):
            rows.append({
                "chunk_id": chunk["id"], "workspace_id": scope, "scope": scope, "source_id": source_id,
                "source_revision": revision, "content_hash": content_hash, "order": chunk["order"],
                "page": chunk["page"], "start_offset": chunk["start"], "end_offset": chunk["end"],
                "start_seconds": chunk["start_seconds"], "end_seconds": chunk["end_seconds"],
                "speaker": chunk["speaker"], "element_kind": chunk["element_kind"],
                "chunk_strategy": chunk["strategy"], "chunk_size": config.size,
                "chunk_overlap": config.overlap, "chunker_revision": revision_tag, "text": chunk["text"],
                "labels": sorted({lab["definitionId"] for lab in labels
                                  if lab.get("quote") and lab["quote"] in chunk["text"]}),
                "catalog_entry_id": None, "catalog_version": None,
                "embedding_space": self.space.identity if vector else None, "vector": vector,
                "embed_status": "embedded" if vector else "not embedded",
                "embed_reason": None if vector else status["reason"], "created_at": now,
            })
        import pyarrow as pa
        table = self.table
        # Add first, then drop every older row of the source (all revisions, all settings): a failed add leaves
        # the previous rows readable. ponytail: a crash between the two steps leaves old and new rows together
        # until the next ingest; a Lance transaction API would close that window.
        if rows:
            table.add(pa.Table.from_pylist(rows, schema=table.schema))
        table.delete(f"source_id = {_q(source_id)} AND created_at < {_ts(now)}")
        self.build_indexes()
        tag = self._tag(source_id, revision)
        if tag in table.tags.list():
            table.tags.update(tag, table.version)
        else:
            table.tags.create(tag, table.version)
        return {"status": "saved", "rows": len(rows), "embedding": status,
                "version": table.version, "tag": tag}

    def add_labels(self, chunk_id, definition_ids):
        """Union more definition ids into one chunk's labels (the interpreter's selections)."""
        rows = self.table.search().where(f"chunk_id = {_q(chunk_id)}").limit(1).to_list()
        if rows and (new := sorted(set(rows[0]["labels"] or []) | set(definition_ids))) != list(rows[0]["labels"] or []):
            self.table.update(where=f"chunk_id = {_q(chunk_id)}", values={"labels": new})

    def _embed(self, embedder, texts):
        none = [None] * len(texts)
        if not texts:
            return [], {"status": "embedded"}
        if embedder is None:
            return none, {"status": "not embedded", "reason": "no_embedder_configured"}
        try:
            vectors = embedder.embed(texts)
            if len(vectors) != len(texts) or any(
                    len(v) != self.space.dimension or not all(math.isfinite(x) for x in v) for v in vectors):
                raise ValueError("wrong embedding shape")
        except Exception as error:  # a dependency failure must never lose the chunks
            return none, {"status": "not embedded", "reason": type(error).__name__}
        return [[float(x) for x in v] for v in vectors], {"status": "embedded"}

    # ponytail: rebuilds every index per ingest (replace=True); incremental optimize if ingest volume grows.
    def build_indexes(self):
        from lancedb.index import FTS, BTree, IvfFlat
        table, built = self.table, []
        if not table.count_rows():
            return {"built": built, "vector": {"status": "empty", "threshold": VECTOR_INDEX_MIN_ROWS}}
        table.create_index("text", config=FTS(), replace=True)
        built.append("fts")
        for column in ("scope", "source_id"):
            table.create_index(column, config=BTree(), replace=True)
            built.append("scalar:" + column)
        embedded = table.count_rows("vector IS NOT NULL")
        vector = {"status": "exact_search", "threshold": VECTOR_INDEX_MIN_ROWS, "embedded_rows": embedded}
        if embedded >= VECTOR_INDEX_MIN_ROWS:
            table.create_index("vector", config=IvfFlat(distance_type="cosine"), replace=True)
            vector["status"] = "built"
            built.append("vector")
        return {"built": built, "vector": vector}

    # -- read
    @staticmethod
    def _tag(source_id, revision):
        return f"ingest-{sha256(source_id.encode()).hexdigest()[:16]}-{revision[:16]}"

    @staticmethod
    def _clean(rows):
        return [{k: v.isoformat() if isinstance(v, datetime) else v for k, v in row.items()
                 if k != "vector" and not k.startswith("_")} for row in rows]

    def read(self, scope, source_id):
        where = f"scope = {_q(scope)} AND source_id = {_q(source_id)}"
        return sorted(self._clean(self.table.search().where(where).limit(100000).to_list()),
                      key=lambda r: r["order"])

    def read_revision(self, source_id, revision):
        """Rows exactly as they stood when that revision was ingested (Lance tag)."""
        tag = self._tag(source_id, revision)
        if tag not in self.table.tags.list():
            raise KeyError("revision was never ingested")
        old = self.db.open_table(TABLE)
        old.checkout(tag)
        where = f"source_id = {_q(source_id)} AND source_revision = {_q(revision)}"
        return sorted(self._clean(old.search().where(where).limit(100000).to_list()), key=lambda r: r["order"])

    def search(self, scopes, text, vector=None, *, exclude_sources=(), limit=10):
        """Hybrid (vector + full text, RRF) when a vector is given, keyword-only otherwise.

        Scope is a mandatory pre-filter. The caller derives scopes and withheld sources from the principal.
        """
        if not scopes or not 1 <= limit <= 100:
            raise ValueError("scope and bounded limit required")
        if vector is not None:
            self._check_vector(vector)
        where = f"scope IN ({', '.join(_q(s) for s in scopes)})"
        if exclude_sources:
            where += f" AND source_id NOT IN ({', '.join(_q(s) for s in exclude_sources)})"
        if not self.table.count_rows():
            return {"mode": "keyword", "rows": []}
        text = (text or "").strip()
        if vector is not None and text:
            # Two legs fused with LanceDB's RRFReranker. The built-in hybrid query fails on rows whose
            # vector is null (not embedded), so the legs run separately and never select the vector.
            from lancedb.rerankers import RRFReranker
            cols = [n for n in self.table.schema.names if n != "vector"]
            by_vector = (self.table.search(vector).where(where + " AND vector IS NOT NULL", prefilter=True)
                         .select(cols).with_row_id(True).limit(limit).to_arrow())
            by_text = (self.table.search(text, query_type="fts").where(where, prefilter=True)
                       .select(cols).with_row_id(True).limit(limit).to_arrow())
            fused = RRFReranker().rerank_hybrid(text, by_vector, by_text).slice(0, limit)
            return {"mode": "hybrid", "rows": self._clean(fused.to_pylist())}
        if vector is not None:
            query = self.table.search(vector).where(where + " AND vector IS NOT NULL", prefilter=True).select(
                [n for n in self.table.schema.names if n != "vector"]).limit(limit)
            return {"mode": "vector", "rows": self._clean(query.to_list())}
        query = self.table.search(text, query_type="fts").where(where, prefilter=True).limit(limit)
        return {"mode": "keyword", "rows": self._clean(query.to_list())}

    def _check_vector(self, vector):
        if len(vector) != self.space.dimension or not all(math.isfinite(x) for x in vector):
            raise ValueError("wrong embedding space or dimension")


def visible(workspace, principal, source_id):
    """Same rule as `MappedEvidence._visible_key`: dataset, source and model source must be visible."""
    return (workspace["id"] in principal["datasets"] and source_id not in principal["withheld"]
            and workspace["knowledge"]["structured"]["modelSource"] not in principal["withheld"])
