"""Recorded embeddings: a cache keyed by embedding space and text hash, so tests replay real vectors offline.

Files in one cache directory:
  embeddings.npz   float32 vectors (compressed) and the keys, row for row;
  embeddings.json  manifest: model, dimension, space ids, recorded date, row count.

`RecordedEmbedder` replays the cache and raises `EmbeddingMiss` for a text that was never recorded.
It never invents a vector. `CachedEmbedder` calls a live embedder only for misses and stores the result.
Record from the command line (the key is read from the environment and never written):

    MISTRAL_API_KEY=... .venv-v2/bin/python -B -m semantic_pipeline_v2.embedding_cache record \
        --definitions --gold tests_v2/fixtures/dev/path1_dev_finance.jsonl
"""

import argparse
from datetime import date
from hashlib import sha256
import json
import os
from pathlib import Path

import numpy as np

from .chunk_index import EMBED_DIMENSION, EMBED_MODEL, MistralEmbedder
from .core import EmbeddingSpace

DEFAULT_SPACE = EmbeddingSpace(EMBED_MODEL, "provider-alias", EMBED_DIMENSION, "chunk-retrieval")
DEFAULT_DIR = Path(__file__).resolve().parents[1] / "tests_v2" / "fixtures" / "embeddings"


class EmbeddingMiss(KeyError):
    """A text was not recorded in this embedding space. No vector is substituted."""

    def __str__(self):
        return self.args[0]


def _key(space_id, text):
    return f"{space_id}:{sha256(text.encode()).hexdigest()}"


class EmbeddingCache:
    def __init__(self, root):
        self.root = Path(root)
        self._rows = {}
        archive = self.root / "embeddings.npz"
        if archive.exists():
            with np.load(archive, allow_pickle=False) as data:
                self._rows = dict(zip(data["keys"].tolist(), data["vectors"]))

    def __len__(self):
        return len(self._rows)

    def get(self, space_id, text):
        row = self._rows.get(_key(space_id, text))
        return None if row is None else [float(x) for x in row]

    def put(self, space_id, text, vector):
        self._rows[_key(space_id, text)] = np.asarray(vector, dtype=np.float32)

    def save(self, *, model=EMBED_MODEL, dimension=None):
        self.root.mkdir(parents=True, exist_ok=True)
        keys = sorted(self._rows)
        vectors = np.stack([self._rows[k] for k in keys]).astype(np.float32) if keys else np.zeros((0, 0), np.float32)
        np.savez_compressed(self.root / "embeddings.npz", keys=np.array(keys), vectors=vectors)
        (self.root / "embeddings.json").write_text(json.dumps({
            "model": model, "dimension": dimension or int(vectors.shape[1]), "recorded": date.today().isoformat(),
            "count": len(keys), "spaces": sorted({k.split(":", 1)[0] for k in keys}),
            "note": "Synthetic or public-licence demo text and Ossie definition text only.",
        }, indent=1))


class CachedEmbedder:
    """Cache first; the live embedder fills misses. With no live embedder a miss raises."""

    def __init__(self, cache, live=None, *, space_id=None, autosave=False):
        self.cache, self.live, self.autosave = cache, live, autosave
        self.space = getattr(live, "space", None) or DEFAULT_SPACE
        self.space_id = space_id or self.space.identity

    def embed(self, texts):
        missing = list(dict.fromkeys(t for t in texts if self.cache.get(self.space_id, t) is None))
        if missing:
            if self.live is None:
                raise EmbeddingMiss(f"{len(missing)} text(s) were never recorded, first: {missing[0][:80]!r}")
            for text, vector in zip(missing, self.live.embed(missing)):
                self.cache.put(self.space_id, text, vector)
            if self.autosave:
                self.cache.save()
        return [self.cache.get(self.space_id, t) for t in texts]


class RecordedEmbedder(CachedEmbedder):
    def __init__(self, cache, *, space_id=None):
        super().__init__(cache, None, space_id=space_id)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("action", choices=["record"])
    parser.add_argument("--cache", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--definitions", action="store_true", help="both card variants of both live models")
    parser.add_argument("--gold", type=Path, nargs="*", default=[], help="JSONL files: embed passages for the hybrid arms")
    args = parser.parse_args(argv)
    key = os.environ.get("MISTRAL_API_KEY", "").strip()
    if not key:
        raise SystemExit("MISTRAL_API_KEY is required to record")
    from .definition_cards import load_cards
    from .path1 import Path1Mapper
    from .workspace import load_workspace

    cache = EmbeddingCache(args.cache)
    before = len(cache)
    embedder = CachedEmbedder(cache, MistralEmbedder(key))
    try:
        if args.definitions:
            for name in ("financial-services", "manufacturing"):
                cards = load_cards(load_workspace(name))
                for variant in ("definition_only", "context"):
                    embedder.embed([c[variant] for c in cards.values()])
        for path in args.gold:
            rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
            for name in sorted({r["dataset"] for r in rows}):
                items = [{"text": r["passage"], "context": r.get("context", ""), "source_kind": r.get("source_kind"),
                          "anchor_kind": "native_text"} for r in rows if r["dataset"] == name]
                for arm in ("hybrid", "hybrid_context"):
                    Path1Mapper(load_workspace(name), embedder, arm=arm, strict=True).map_many(items)
    except Exception as error:  # never echo provider text: it could carry request headers
        cache.save()
        raise SystemExit(f"recording stopped: {type(error).__name__}") from None
    cache.save()
    print(f"recorded {len(cache) - before} new vectors; cache holds {len(cache)}")


if __name__ == "__main__":
    main()
