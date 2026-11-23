"""P2g (E23): the candidate stage of pipeline A is configurable: V12, V30, V50 and the two-step V2S.

Unit tests use hand-written replies (a scripted client, labelled as such) and a deterministic fake embedder.
The last test replays real recorded replies of the adopted variant on gold A2.
"""
import json
from pathlib import Path

import numpy as np
import pytest

from semantic_pipeline_v2.evaluate_mapping import evaluate, select_variant
from semantic_pipeline_v2.evidence_links import EntityKeys, build_links, candidate_settings, interpret_items
from semantic_pipeline_v2.interpreter import Interpreter, InterpreterError, compact_card, validate_shortlist
from semantic_pipeline_v2.path1 import Path1Mapper
from semantic_pipeline_v2.workspace import load_workspace

FIXTURES = Path(__file__).parent / "fixtures"
TEXT = "Average lead time: the supplier promises receipt within 14 calendar days."
ITEM = {"text": TEXT, "context": "Document: Supply agreement", "source_kind": None}


class FakeEmbedder:
    """Deterministic pseudo-random vectors (a test double). Proximity is meaningless; only the count matters."""

    def embed(self, texts):
        return [np.random.default_rng(abs(hash(t)) % 2**32).normal(size=16) for t in texts]


class Scripted:
    """A scripted chat client (a test double): answers the shortlist call and the decision call by hand."""
    available, model = True, "scripted"

    def __init__(self, ids=None, decision=None):
        self.ids, self.decision, self.users = ids, decision, []

    def complete(self, system, user):
        data = json.loads(user)
        self.users.append(data)
        if "definitions" in data:
            return self.ids if isinstance(self.ids, str) else json.dumps({"ids": self.ids})
        return self.decision or json.dumps({"interpretation": "Nothing recorded applies.", "links": []})


@pytest.fixture(scope="module")
def mfg():
    return load_workspace("manufacturing")


@pytest.fixture(scope="module")
def mapper(mfg):
    return Path1Mapper(mfg, FakeEmbedder())


def run(mfg, mapper, chat, **options):
    keys = EntityKeys(mfg, "/nonexistent")
    return interpret_items(mapper, Interpreter(chat), keys, [ITEM], **options)[0]


def test_k_gives_that_many_candidates_from_both_legs(mapper):
    for k in (12, 30, 50):
        rows = mapper.map_many([{**ITEM, "anchor_kind": "native_text"}], k=k)[0]["candidates"]
        assert len(rows) >= k
        assert sum("vector" in r["legs"] for r in rows) == k  # the vector leg is cut at k, not at 12
        assert sum("lexical" in r["legs"] for r in rows) <= k


@pytest.mark.parametrize("variant,n", [("hybrid12", 12), ("hybrid30", 30), ("hybrid50", 50)])
def test_one_call_variants_offer_that_many_full_cards(mfg, mapper, variant, n):
    chat = Scripted()
    [link] = run(mfg, mapper, chat, variant=variant)
    assert len(chat.users) == 1 and len(chat.users[0]["candidates"]) == n
    assert len(link["candidates"]) == n and f"+{variant}:{n}" in link["method"]


def test_v12_offers_the_top_twelve_of_the_default_union(mfg, mapper):
    """V12 is today's behaviour: the candidate list is the top 12 by retrieval rank of the k=12 union."""
    old = sorted(mapper.map_many([{**ITEM, "anchor_kind": "native_text"}])[0]["candidates"], key=lambda r: r["retrievalRank"])[:12]
    chat = Scripted()
    run(mfg, mapper, chat, variant="hybrid12")
    assert [c["id"] for c in chat.users[0]["candidates"]] == [r["id"] for r in old]


def test_compact_card_is_id_label_and_one_bounded_line(mfg):
    card = {"id": "x/y", "label": "L" * 200, "description": "First sentence is here. " + "More text. " * 50,
            "context": "ignored"}
    line = compact_card(card)
    assert set(line) == {"id", "label", "line"} and line["id"] == "x/y"
    assert len(line["label"]) <= 60 and len(line["line"]) <= 100


def test_step_one_offers_the_whole_catalog_at_or_below_the_setting(mfg, mapper):
    total = len(mapper.cards)
    chat = Scripted(ids=[])
    run(mfg, mapper, chat, variant="two_step", pool=20, whole_max=total)
    assert len(chat.users[0]["definitions"]) == total
    chat = Scripted(ids=[])
    run(mfg, mapper, chat, variant="two_step", pool=20, whole_max=total - 1)
    assert len(chat.users[0]["definitions"]) == 20


def test_empty_shortlist_abstains_without_a_second_call(mfg, mapper):
    chat = Scripted(ids=[])
    [link] = run(mfg, mapper, chat, variant="two_step")
    assert len(chat.users) == 1 and link["definition_id"] is None and link["relation"] == "none"
    assert link["status"] == "proposed" and link["candidates"] == [] and "+two_step:" in link["method"]


def test_shortlist_goes_to_the_decision_and_is_recorded(mfg, mapper):
    ids = list(mapper.cards)[:3]
    chat = Scripted(ids=ids)
    [link] = run(mfg, mapper, chat, variant="two_step", whole_max=10**6)
    assert len(chat.users) == 2 and [c["id"] for c in chat.users[1]["candidates"]] == ids
    assert link["candidates"] == ids and link["method"].endswith(f"+two_step:{len(mapper.cards)}>3")


@pytest.mark.parametrize("ids", [["not-offered"], [f"id{n}" for n in range(6)], "not json", {"ids": []}, ["a", "a"]])
def test_bad_shortlists_are_refused_and_nothing_is_substituted(mfg, mapper, ids):
    chat = Scripted(ids=ids if not isinstance(ids, dict) else json.dumps(ids))
    [link] = run(mfg, mapper, chat, variant="two_step")
    if ids == {"ids": []}:  # a valid empty shortlist, kept here to show the contrast
        assert link["status"] == "proposed"
        return
    assert link["status"] == "degraded" and link["definition_id"] is None and len(chat.users) == 1
    with pytest.raises(InterpreterError):
        run(mfg, mapper, Scripted(ids=ids), variant="two_step", strict=True)


def test_validate_shortlist_rules():
    assert validate_shortlist('{"ids": ["a", "b"]}', {"a", "b", "c"}) == ["a", "b"]
    for bad in ('{"ids": ["z"]}', '{"ids": "a"}', '{"ids": [], "x": 1}', '{"ids": [1]}', "[]"):
        with pytest.raises(InterpreterError):
            validate_shortlist(bad, {"a", "b"})


def test_settings_come_from_arguments_then_environment(monkeypatch):
    monkeypatch.delenv("V2_CANDIDATE_VARIANT", raising=False)
    assert candidate_settings()["variant"] == "two_step"  # the adopted default
    monkeypatch.setenv("V2_CANDIDATE_VARIANT", "hybrid30")
    assert candidate_settings()["variant"] == "hybrid30" and candidate_settings(variant="hybrid12")["variant"] == "hybrid12"
    with pytest.raises(ValueError):
        candidate_settings(variant="V99")


def test_selection_rule_on_hand_written_numbers():
    stats = {"hybrid12": {"recall": 0.5, "false_link_rate": 0.10}, "hybrid30": {"recall": 0.6, "false_link_rate": 0.12},
             "hybrid50": {"recall": 0.7, "false_link_rate": 0.14}, "two_step": {"recall": 0.65, "false_link_rate": 0.11}}
    assert select_variant(stats) == "two_step"  # V50 is 0.04 above V12's false-link rate: out; V2S has the best recall left
    worse = {**stats, "two_step": {"recall": 0.65, "false_link_rate": 0.14}, "hybrid30": {"recall": 0.6, "false_link_rate": 0.14}}
    assert select_variant(worse) == "hybrid12"  # none qualifies: keep the baseline
    assert select_variant({**stats, "hybrid30": {"recall": 0.6, "false_link_rate": 0.13}}, margin=0.03) == "two_step"


def test_adopted_variant_replays_recorded_replies_on_gold_a2():
    """Real recorded replies (live Mistral, 2026-11-21): the report is reproducible offline."""
    report = evaluate([FIXTURES / "gold" / "pathA2_gold.jsonl"], "interpret", FIXTURES / "embeddings",
                      chat_cache_dir=FIXTURES / "interpretations_a2", variant="two_step")
    assert report["variant"] == "two_step" and report["overall"]["n"] == 82 and report["live_chat_calls"] == 0
    assert report["overall"]["degraded"] == 0  # re-recorded 2026-11-21 after P2e: no recorded shortlist was refused (was 1)
    assert report["cost"]["calls_per_chunk"] > 1


def test_single_leg_methods_use_only_their_leg(mfg, mapper):
    for variant, leg in (("lexical", "lexical"), ("vector", "vector")):
        chat = Scripted()
        [link] = run(mfg, mapper, chat, variant=variant)
        assert len(chat.users[0]["candidates"]) <= 12 and f"+{variant}:12" in link["method"]
    rows = mapper.map_many([{**ITEM, "anchor_kind": "native_text"}], k=12, legs=("vector",))[0]["candidates"]
    assert len(rows) == 12 and all(r["legs"] == ["vector"] for r in rows)
    assert [r["proximity"] for r in rows] == sorted((r["proximity"] for r in rows), reverse=True)  # ranked by cosine


def test_expanded_restates_first_then_retrieves_over_passage_plus_restatement(mfg, mapper):
    class Chat(Scripted):
        def complete(self, system, user):
            data = json.loads(user)
            if "definitions" not in data and "candidates" not in data:
                self.users.append(data)
                return json.dumps({"restatement": "A supplier promises a delivery lead time in days."})
            return super().complete(system, user)

    chat = Chat()
    [link] = run(mfg, mapper, chat, variant="expanded")
    assert "restatement" not in chat.users[0] and len(chat.users) == 2 and len(link["candidates"]) == 12
    assert link["notes"]["restatement"].startswith("A supplier") and link["notes"]["expandedFallback"] is False
    assert "+expanded:12" in link["method"]


@pytest.mark.parametrize("bad", ["not json", '{"restatement": ""}', '{"restatement": "x", "id": 1}', '{"other": "x"}'])
def test_expanded_falls_back_to_plain_hybrid_on_a_bad_restatement_and_counts_it(mfg, mapper, bad):
    class Chat(Scripted):
        def complete(self, system, user):
            data = json.loads(user)
            return bad if "candidates" not in data else super().complete(system, user)

    plain = Scripted()
    run(mfg, mapper, plain, variant="hybrid12")
    chat = Chat()
    [link] = run(mfg, mapper, chat, variant="expanded", degrade_bad_replies=True)
    assert link["notes"]["expandedFallback"] is True and link["status"] == "proposed"
    assert [c["id"] for c in chat.users[0]["candidates"]] == [c["id"] for c in plain.users[0]["candidates"]]


def test_old_names_are_aliases():
    assert candidate_settings(variant="V2S")["variant"] == "two_step" and candidate_settings(variant="V12")["variant"] == "hybrid12"


def test_offered_only_needs_no_chat_and_unknown_gold_keys_and_categories_are_kept(tmp_path):
    rows = [json.loads(line) for line in (FIXTURES / "gold" / "pathA2_gold.jsonl").read_text().splitlines()[:6]]
    gold = tmp_path / "g.jsonl"
    gold.write_text("\n".join(json.dumps({**r, "category": "brand_new_group", "surprise": {"x": 1}}) for r in rows))
    report = evaluate([gold], "interpret", FIXTURES / "embeddings", chat_cache_dir=tmp_path / "none", variant="vector",
                      offered_only=True, lines=True)
    assert report["cost"]["calls_per_chunk"] == 0 and report["live_chat_calls"] == 0
    assert "brand_new_group" in report["overall"]["categories"] and len(report["lines"]) == 6
    assert report["overall"]["offered_strict"] is not None and "offered" in report["lines"][0]
    with pytest.raises(SystemExit):
        evaluate([gold], "interpret", FIXTURES / "embeddings", chat_cache_dir=tmp_path / "none", variant="expanded",
                 offered_only=True)
