"""Score an Output B catalog against the path 2 gold set (E6).

    .venv-v2/bin/python -B -m semantic_pipeline_v2.evaluate_catalog --gold FILE --catalog FILE [--anchors FILE]
        [--embeddings DIR [--record]]

Gold rows (`path2_gold.jsonl`): id, dataset, `concept_label`, `concept_definition`, `expected_links`
[{definition, predicate}] and `supporting_anchor_ids`. Anchors (`path1_anchors.jsonl`): id, passage. The catalog file
is the pipeline's `<dataset>-catalog.json`: dataset, concepts (label, definition, synonyms, links, evidence.chunks) and
chunks {id: text}.

Matching rule. Candidates are the catalog concepts whose evidence chunks contain the passage of a supporting anchor
(case and whitespace ignored). Among them the concept most similar to the gold concept's label and definition is
chosen. A candidate must reach a stated minimum similarity, else the gold concept is unmatched, so one broad concept
cannot absorb gold concepts it does not mean. Similarity passes when (label words) the gold label overlaps the
concept's label or a synonym with Jaccard >= 0.5, or (definition words) the two definitions overlap with Jaccard >=
0.25, or (embedding, only when a recorded embedder is supplied) cosine >= 0.8 between "label. definition" texts.
Ranking uses the largest of those values. A second coverage number uses the same similarity without any anchor.

Link metrics. Predicted links are those of the matched concepts. A link is correct predicate-agnostically when its
definition is in the gold links of the gold concept, and per predicate when the definition and the predicate both are.
Recall counts every gold link, also those of an unmatched concept. Precision is None when nothing was predicted.
"""

import argparse
import json
import os
from pathlib import Path

import numpy as np

from .concept_catalog import PREDICATES
from .concept_induction import STOP, _jaccard, _norm, _squash

ANCHORS = Path(__file__).resolve().parents[1] / "tests_v2" / "fixtures" / "gold" / "path1_anchors.jsonl"
MINIMUM = {"label_words": 0.5, "definition_words": 0.25, "embedding": 0.8}
RULE = ("anchor candidates (an evidence chunk contains a supporting anchor passage, case and whitespace ignored), "
        "then the candidate most similar to the gold label and definition; a minimum similarity is required "
        "(label words Jaccard >= 0.5, or definition words Jaccard >= 0.25, or embedding cosine >= 0.8 when an "
        "embedder is used), else the gold concept is unmatched")
LABEL_KEYS = ("label", "concept_label", "concept", "name", "title")
DEFINITION_KEYS = ("concept_definition", "definition", "description")


def _ratio(num, den):
    return None if not den else round(num / den, 3)


def _words(text):
    return {w for w in _norm(text or "").split() if w not in STOP}


def _first(row, keys):
    return next((row[k] for k in keys if isinstance(row.get(k), str)), "")


def _text(label, definition):
    return f"{label}. {definition}".strip()


class _Similarity:
    def __init__(self, embed):
        self.embed, self.cache = embed, {}

    def vector(self, text):
        if text not in self.cache:
            raw = np.asarray(self.embed([text])[0], dtype=np.float32)
            self.cache[text] = raw / max(float(np.linalg.norm(raw)), 1e-9)
        return self.cache[text]

    def __call__(self, gold, concept):
        """(passes, rank value) of a concept for a gold row."""
        g_label, g_def = _first(gold, LABEL_KEYS), _first(gold, DEFINITION_KEYS)
        names = [concept["label"], *concept.get("synonyms", [])]
        label_j = max((_jaccard(_words(g_label), _words(n)) for n in names), default=0.0)
        def_j = _jaccard(_words(g_def), _words(concept.get("definition", ""))) if g_def else 0.0
        cos = 0.0
        if self.embed is not None:
            cos = float(self.vector(_text(g_label, g_def)) @ self.vector(_text(concept["label"], concept.get("definition", ""))))
        passes = (label_j >= MINIMUM["label_words"] or def_j >= MINIMUM["definition_words"]
                  or (self.embed is not None and cos >= MINIMUM["embedding"]))
        return passes, max(label_j, def_j, cos)


def _best(similarity, gold, concepts):
    best = None
    for c in concepts:
        passes, rank = similarity(gold, c)
        if passes and (best is None or (rank, -len(c["links"])) > best[0]):
            best = ((rank, -len(c["links"])), c)
    return None if best is None else best[1]


def score(gold_rows, anchors, catalog, embed=None):
    passages_of = {a["id"]: _squash(a["passage"]).lower() for a in anchors}
    gold_rows = [g for g in gold_rows if g["dataset"] == catalog["dataset"]]
    similarity = _Similarity(embed)
    text_of = {c["id"]: [_squash(catalog["chunks"].get(e, "")).lower() for e in c["evidence"]["chunks"]]
               for c in catalog["concepts"]}
    matched, unmatched, free = {}, [], {"matched": {}, "unmatched": []}
    for g in gold_rows:
        passages = [passages_of[a] for a in g["supporting_anchor_ids"] if a in passages_of and passages_of[a]]
        candidates = [c for c in catalog["concepts"] if any(p in t for p in passages for t in text_of[c["id"]])]
        best = _best(similarity, g, candidates)
        if best is None:
            unmatched.append(g["id"])
        else:
            matched[g["id"]] = best
        alone = _best(similarity, g, catalog["concepts"])
        if alone is None:
            free["unmatched"].append(g["id"])
        else:
            free["matched"][g["id"]] = alone["id"]
    by = {p: {"gold": 0, "predicted": 0, "correct": 0} for p in PREDICATES}
    agnostic = {"gold": 0, "predicted": 0, "correct": 0}
    for g in gold_rows:
        expected = {(l["definition"], l["predicate"]) for l in g["expected_links"]}
        defs = {d for d, _ in expected}
        got = [(l["definition_id"], l["predicate"]) for l in matched[g["id"]]["links"]] if g["id"] in matched else []
        for d, p in expected:
            by[p]["gold"] += 1
        agnostic["gold"] += len(defs)
        agnostic["predicted"] += len(got)
        agnostic["correct"] += sum(d in defs for d, _ in got)
        for d, p in got:
            by[p]["predicted"] += 1
            by[p]["correct"] += (d, p) in expected
    for item in (*by.values(), agnostic):
        item["precision"], item["recall"] = _ratio(item["correct"], item["predicted"]), _ratio(item["correct"], item["gold"])
    none = [g for g in gold_rows if not g["expected_links"]]
    covered_none = [g for g in none if g["id"] in matched]
    kept = [g for g in covered_none if not matched[g["id"]]["links"]]
    return {
        "dataset": catalog["dataset"], "matching_rule": RULE, "similarity_minimum": MINIMUM,
        "embedding": "used" if embed is not None else "not used", "gold_concepts": len(gold_rows),
        "covered": len(matched), "coverage": _ratio(len(matched), len(gold_rows)), "unmatched": unmatched,
        "matched_concept": {k: v["id"] for k, v in matched.items()},
        "coverage_by_similarity_alone": {"covered": len(free["matched"]),
                                         "coverage": _ratio(len(free["matched"]), len(gold_rows)),
                                         "unmatched": free["unmatched"], "matched_concept": free["matched"]},
        "links": {"predicate_agnostic": agnostic, "by_predicate": by},
        "no_counterpart": {"gold": len(none), "covered": len(covered_none), "kept_unlinked": len(kept),
                           "share_of_covered": _ratio(len(kept), len(covered_none)),
                           "unmatched": len(none) - len(covered_none)}}


def _rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def _embedder(directory, record):
    """A recorded embedder over `directory`; with `record` and MISTRAL_API_KEY a miss is embedded live and stored."""
    from .chunk_index import MistralEmbedder
    from .embedding_cache import CachedEmbedder, EmbeddingCache
    cache = EmbeddingCache(directory)
    live = None
    if record:
        key = os.environ.get("MISTRAL_API_KEY", "").strip()
        if not key:
            raise SystemExit("--record needs MISTRAL_API_KEY")
        live = MistralEmbedder(key)
    cached = CachedEmbedder(cache, live)

    def embed(texts):
        out = cached.embed(texts)
        if record:
            cache.save()
        return out
    return embed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--gold", required=True, type=Path)
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--anchors", type=Path, default=ANCHORS)
    parser.add_argument("--embeddings", type=Path, help="recorded embedding cache; adds embedding similarity")
    parser.add_argument("--record", action="store_true", help="embed missing texts live and store them")
    args = parser.parse_args(argv)
    embed = _embedder(args.embeddings, args.record) if args.embeddings else None
    print(json.dumps(score(_rows(args.gold), _rows(args.anchors),
                           json.loads(args.catalog.read_text(encoding="utf-8")), embed=embed), indent=1))


if __name__ == "__main__":
    main()
