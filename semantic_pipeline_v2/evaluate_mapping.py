"""Evaluate path 1 on a gold file.

    .venv-v2/bin/python -B -m semantic_pipeline_v2.evaluate_mapping --gold FILE [FILE ...] \
        --arm lexical|hybrid|hybrid_context|interpret [--record] [--cache DIR] [--chat-cache DIR] [--lines]

Gold line: id, dataset, source, source_sha256, locator, passage, expected (acceptable definition ids,
empty = abstain), relation (exact|related|none), category, rationale. Optional: context, source_kind.
`hybrid` uses the label key and definition-only cards. `hybrid_context` uses the passage, its context
and the context cards. `--record` embeds passages that are not cached with the live model (needs
MISTRAL_API_KEY). Without it a missing vector stops the run; nothing is substituted.

`interpret` is `hybrid_context` retrieval followed by the interpreter model. `--variant` picks the candidate method
(lexical, vector, hybrid12, hybrid30, hybrid50, expanded, two_step; see evidence_links.py). `--offered-only` runs the
retrieval alone, without a chat call, for every method but `expanded`. The report adds `offered_*` shares per scope
and category, `cost` per chunk, and with `--lines` one row per gold line. Extra gold keys are ignored; a new
`category` value is its own group. It reads
`context` from the gold line. Gold relations `exact`/`related`/`none` count as `states_value_of`/`is_about`/`none`.
Optional gold keys `stated_value` and `entity` add the value and entity accuracies. With `--record` an uncached
embedding or chat call goes live; without it a missing recording stops the run. `--lines` adds one row per line.
"""

import argparse
from collections import defaultdict
import json
import os
from pathlib import Path

from .embedding_cache import DEFAULT_DIR, CachedEmbedder, EmbeddingCache, RecordedEmbedder
from .evidence_links import ALIASES, K, VARIANTS, EntityKeys, candidate_settings, interpret_items
from .interpreter import (DEFAULT_DIR as CHAT_DIR, DEFAULT_MODEL, PROMPTS, CachedChat, ChatCache, Interpreter,
                          MistralChat, _norm)
from .path1 import ARMS, THRESHOLDS, Path1Mapper
from .workspace import load_workspace


def _rate(num, den):
    return None if not den else round(num / den, 4)


def metrics(pairs):
    pos = [(r, x) for r, x in pairs if r["expected"]]
    neg = [(r, x) for r, x in pairs if not r["expected"]]
    chosen = [(r, x) for r, x in pairs if x["decision"] == "selected"]
    right = [(r, x) for r, x in chosen if x["selected"] in r["expected"]]
    right_pos = [(r, x) for r, x in right if r["expected"]]
    reached = sum(any(c["id"] in r["expected"] and c["retrievalRank"] <= 12 for c in x["candidates"]) for r, x in pos)
    return {
        "n": len(pairs), "n_positive": len(pos), "n_abstain": len(neg),
        "recall_at_12": _rate(reached, len(pos)),
        "selection_precision": _rate(len(right), len(chosen)), "n_selected": len(chosen),
        "recall": _rate(len(right_pos), len(pos)),
        "abstention_accuracy": _rate(sum(x["decision"] != "selected" for _, x in neg), len(neg)),
        "false_link_rate": _rate(sum(x["decision"] == "selected" for _, x in neg), len(neg)),
        "relation_accuracy": _rate(sum(x["relation"] == r["relation"] for r, x in right_pos), len(right_pos)),
        "ambiguous": sum(x["decision"] == "ambiguous" for _, x in pairs),
        "degraded": sum(bool(x["degraded"]) for _, x in pairs),
    }


def _scope(pairs):
    by_category = defaultdict(list)
    for pair in pairs:
        by_category[pair[0].get("category") or "uncategorised"].append(pair)
    return {**metrics(pairs), "categories": {c: metrics(p) for c, p in sorted(by_category.items())}}


NEW_RELATION = {"exact": "states_value_of", "related": "is_about", "none": "none"}


def interpret_metrics(pairs):
    """`metrics` plus three-value relation, stated-value and entity accuracy. Each pair is (gold, prediction)."""
    base = metrics(pairs)
    gold_rel = lambda r: NEW_RELATION.get(r["relation"], r["relation"])  # noqa: E731
    valued = [(r, x) for r, x in pairs if r.get("stated_value")]
    entitled = [(r, x) for r, x in pairs if "entity" in r]
    base["relation_accuracy"] = _rate(sum(x["relation"] == gold_rel(r) for r, x in pairs), len(pairs))
    base["relation_accuracy_on_correct_links"] = _rate(
        sum(x["relation"] == gold_rel(r) for r, x in pairs if x["decision"] == "selected" and x["selected"] in r["expected"]),
        sum(x["decision"] == "selected" and x["selected"] in r["expected"] for r, x in pairs))
    base["stated_value_accuracy"] = _rate(sum(_norm(x["stated_value"] or "") == _norm(r["stated_value"])
                                              for r, x in valued), len(valued))
    base["n_with_stated_value"] = len(valued)
    base["entity_accuracy"] = _rate(sum(
        (r["entity"] is None and x["entity_key"] == "unresolved") or
        (r["entity"] is not None and str(r["entity"]) in x["entity_key"]) for r, x in entitled), len(entitled))
    base["n_with_entity"] = len(entitled)
    # by identifier value, not text: gold `customer:29641` matches a predicted key `Sales_Customer.CustomerID=29641`
    values = lambda x: {k["key"].split("=", 1)[1] for k in x.get("entity_keys", [])}  # noqa: E731
    base["entity_value_accuracy"] = _rate(sum(
        (not values(x)) if r["entity"] is None else str(r["entity"]).rsplit(":", 1)[-1] in values(x)
        for r, x in entitled), len(entitled))
    # canonical: both sides follow declared relationships to the primary-key field. Strict numbers stay as above.
    hit = lambda r, x: x["decision"] == "selected" and x.get("canonical") in r.get("expected_canonical", [])  # noqa: E731
    chosen = [(r, x) for r, x in pairs if x["decision"] == "selected"]
    positives = [(r, x) for r, x in pairs if r["expected"]]
    base["canonical_precision"] = _rate(sum(hit(r, x) for r, x in chosen), len(chosen))
    base["canonical_recall"] = _rate(sum(hit(r, x) for r, x in positives), len(positives))
    # a chunk may carry up to three links: the first is scored above; this counts a positive row any link serves
    base["recall_any_link"] = _rate(sum(any(c in r["expected"] for c in x.get("all_selected", [])) for r, x in positives),
                                    len(positives))
    base["canonical_recall_any_link"] = _rate(sum(any(c in r.get("expected_canonical", []) for c in x.get("all_canonical", []))
                                                  for r, x in positives), len(positives))
    # share of positive rows whose right definition the deciding call was offered (strict, canonical) and, for
    # two_step, the wide list of step one (a whole-catalog list offers everything)
    base["offered_strict"] = _rate(sum(any(c["id"] in r["expected"] for c in x["candidates"]) for r, x in positives), len(positives))
    base["offered_canonical"] = _rate(sum(any(c in r.get("expected_canonical", []) for c in x.get("candidate_canonical", []))
                                          for r, x in positives), len(positives))
    wide = [(r, x) for r, x in positives if x.get("step1")]
    base["offered_step1"] = _rate(sum(x["step1"]["wholeCatalog"] or any(e in x["step1"].get("ids", []) for e in r["expected"])
                                      for r, x in wide), len(wide))
    base["links_per_chunk"] = _rate(sum(len(x.get("all_selected", [])) for _, x in pairs), len(pairs))
    return base


def _interpret_scope(pairs):
    by_category = defaultdict(list)
    for pair in pairs:
        by_category[pair[0].get("category") or "uncategorised"].append(pair)
    return {**interpret_metrics(pairs), "categories": {c: interpret_metrics(p) for c, p in sorted(by_category.items())}}


def _prediction(decisions):
    """The first link scores as the chunk's choice; `all_*` list every link of the chunk."""
    decision = decisions[0]
    chosen = decision["definition_id"]
    return {"decision": "selected" if chosen else "abstain", "selected": chosen, "relation": decision["relation"],
            "canonical": decision["canonical_definition_id"], "entity_keys": decision["entity_keys"],
            "all_selected": [d["definition_id"] for d in decisions if d["definition_id"]],
            "all_canonical": [d["canonical_definition_id"] for d in decisions if d["definition_id"]],
            "candidates": [{"id": c, "retrievalRank": n} for n, c in enumerate(decision["candidates"], 1)],
            "step1": decision["notes"].get("step1"), "fallback": bool(decision["notes"].get("expandedFallback")), "method": decision["method"],
            "degraded": decision["status"] == "degraded", "stated_value": decision["stated_value"],
            "unit": decision["unit"], "period": decision["period"], "entity_key": decision["entity_key"]}


def select_variant(stats, baseline="hybrid12", margin=0.03):
    """The rule fixed before the P2g run: the variant with the highest recall whose false-link rate is at most
    `margin` above the baseline's; the baseline when none qualifies. `stats` is {variant: {recall, false_link_rate}}."""
    limit = stats[baseline]["false_link_rate"] + margin
    ok = [v for v, m in stats.items() if m["false_link_rate"] <= limit + 1e-9]
    return max(ok, key=lambda v: (stats[v]["recall"], v == baseline))


def evaluate_interpret(rows, cache, embedder, chat_cache_dir, *, record, model, offered_only=False, prompt=None, **options):
    chat_cache = ChatCache(chat_cache_dir)
    live = None
    if record:
        key = os.environ.get("MISTRAL_API_KEY", "").strip()
        live = MistralChat(key, model)
        if not live.available:
            raise SystemExit("--record needs MISTRAL_API_KEY")
    chat = CachedChat(chat_cache, live, model)
    interpreter = Interpreter(chat, model, prompt)  # prompt: --prompt, else V2_INTERPRET_PROMPT, else interp-v3
    version = interpreter.version
    if offered_only:  # retrieval alone: no chat call; each decision is a degraded record that keeps its candidates
        if candidate_settings(**options)["variant"] == "expanded":
            raise SystemExit("--offered-only cannot measure `expanded`: its first step is a chat call")
        interpreter = None
    pairs, cost = defaultdict(list), {}
    try:
        for dataset in sorted({r["dataset"] for r in rows}):
            workspace = load_workspace(dataset)
            mapper = Path1Mapper(workspace, embedder, arm="hybrid_context", strict=True)
            chosen = [r for r in rows if r["dataset"] == dataset]
            items = [{"text": r["passage"], "context": r.get("context", ""), "source_kind": r.get("source_kind")}
                     for r in chosen]
            keys = EntityKeys(workspace)
            calls, chars = (interpreter.calls, interpreter.chars) if interpreter else (0, 0)
            decisions = interpret_items(mapper, interpreter, keys, items, strict=True, degrade_bad_replies=True, **options)
            now = (interpreter.calls, interpreter.chars) if interpreter else (0, 0)
            cost[dataset] = {"calls_per_chunk": round((now[0] - calls) / len(items), 3),
                             "prompt_chars_per_chunk": round((now[1] - chars) / len(items))}
            chosen = [{**r, "expected_canonical": [keys.canonical_id(e) for e in r["expected"]]} for r in chosen]
            pairs[dataset] = [(r, {**_prediction(d), "candidate_canonical": [keys.canonical_id(c) for c in d[0]["candidates"]]})
                              for r, d in zip(chosen, decisions)]
    finally:
        if record:  # keep every paid reply, also when the run stops
            chat_cache.save(model=model, prompt_version=version)
    total = (interpreter.calls, interpreter.chars) if interpreter else (0, 0)
    cost["overall"] = {"calls_per_chunk": round(total[0] / len(rows), 3), "prompt_chars_per_chunk": round(total[1] / len(rows))}
    return pairs, chat, cost, version


def evaluate(gold_paths, arm, cache_dir=DEFAULT_DIR, *, record=False, chat_cache_dir=CHAT_DIR, model=DEFAULT_MODEL,
             lines=False, offered_only=False, prompt=None, **options):
    rows = [json.loads(line) for path in gold_paths for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")]
    cache = EmbeddingCache(cache_dir)
    embedder, space = None, None
    if arm != "lexical":
        if record:
            from .chunk_index import MistralEmbedder
            key = os.environ.get("MISTRAL_API_KEY", "").strip()
            if not key:
                raise SystemExit("--record needs MISTRAL_API_KEY")
            embedder = CachedEmbedder(cache, MistralEmbedder(key))
        else:
            embedder = RecordedEmbedder(cache)
        space = embedder.space.identity
    if arm == "interpret":
        pairs, chat, cost, version = evaluate_interpret(rows, cache, embedder, chat_cache_dir, record=record, model=model,
                                                        offered_only=offered_only, prompt=prompt, **options)
        setting = candidate_settings(**options)
        if record:
            cache.save()
        report = {
            "arm": arm, "gold": [str(p) for p in gold_paths], "embedding_space": space, "model": model,
            "prompt_version": version,
            "variant": setting["variant"], "offered_only": offered_only, "candidate_settings": setting, "cost": {**cost["overall"], "expanded_fallbacks": sum(x["fallback"] for v in pairs.values() for _, x in v)},
            "settings": {"k": K, "retrieval_arm": "hybrid_context", "card_variant": "context", "temperature": 0,
                         "top_p": 1, "max_tokens": 900, "response_format": "json_object"},
            "live_chat_calls": chat.calls,
            "overall": _interpret_scope([p for v in pairs.values() for p in v]),
            "datasets": {d: {**_interpret_scope(p), "cost": cost[d]} for d, p in sorted(pairs.items())},
        }
        if lines:
            report["lines"] = [{"id": r["id"], "dataset": r["dataset"], "category": r.get("category"),
                                "offered": [c["id"] for c in x["candidates"]], "method": x["method"], "expected": r["expected"], "relation": r["relation"],
                                "selected": x["selected"], "canonical": x["canonical"], "all_selected": x["all_selected"],
                                "predicted_relation": x["relation"],
                                "stated_value": x["stated_value"], "entity_key": x["entity_key"]}
                               for v in pairs.values() for r, x in v]
        return report
    pairs = defaultdict(list)
    for dataset in sorted({r["dataset"] for r in rows}):
        mapper = Path1Mapper(load_workspace(dataset), embedder, arm=arm, strict=True)
        chosen = [r for r in rows if r["dataset"] == dataset]
        results = mapper.map_many([{"text": r["passage"], "context": r.get("context", ""),
                                    "source_kind": r.get("source_kind"), "anchor_kind": "native_text"}
                                   for r in chosen])
        pairs[dataset] = list(zip(chosen, results))
    if record:
        cache.save()
    return {
        "arm": arm, "gold": [str(p) for p in gold_paths], "embedding_space": space,
        "thresholds": THRESHOLDS, "overall": _scope([p for v in pairs.values() for p in v]),
        "datasets": {d: _scope(p) for d, p in sorted(pairs.items())},
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--gold", type=Path, nargs="+", required=True)
    parser.add_argument("--arm", choices=(*ARMS, "interpret"), required=True)
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--cache", type=Path, default=DEFAULT_DIR, help="embedding cache directory")
    parser.add_argument("--chat-cache", type=Path, default=CHAT_DIR, help="recorded interpreter replies")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--lines", action="store_true")
    parser.add_argument("--variant", choices=(*VARIANTS, *ALIASES),
                        help="candidate method (default: V2_CANDIDATE_VARIANT, else two_step)")
    parser.add_argument("--offered-only", action="store_true", help="retrieval only: no chat call; report the offered shares")
    parser.add_argument("--pool", type=int, help="two_step: top N offered in step one")
    parser.add_argument("--whole-max", type=int, help="two_step: catalogs this small are offered whole in step one")
    parser.add_argument("--extra", type=int, help="two_step: top retrieval candidates added to a non-empty shortlist")
    parser.add_argument("--prompt", choices=tuple(PROMPTS),
                        help="decision prompt (default: V2_INTERPRET_PROMPT, else interp-v3); interp-v4 also uses shortlist-v3")
    args = parser.parse_args(argv)
    print(json.dumps(evaluate(args.gold, args.arm, args.cache, record=args.record, chat_cache_dir=args.chat_cache,
                              model=args.model, lines=args.lines, variant=args.variant, pool=args.pool,
                              whole_max=args.whole_max, extra=args.extra, offered_only=args.offered_only,
                              prompt=args.prompt), indent=1))


if __name__ == "__main__":
    main()
