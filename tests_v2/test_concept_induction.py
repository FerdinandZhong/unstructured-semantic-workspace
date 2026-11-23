"""Concept induction (pipeline B, step 1). Model replies here are hand-written test doubles, not recordings,
except `test_replay_recorded_replies`, which replays real recorded replies offline."""

import json
from pathlib import Path

import pytest

from semantic_pipeline_v2 import concept_induction as ci

CHUNKS = [
    {"chunk_id": "c1", "title": "Doc A", "text": "The customer commits to order at least 71 units each year."},
    {"chunk_id": "c2", "title": "Doc A", "text": "Quantities are counted on sales orders dated within the period."},
    {"chunk_id": "c3", "title": "Doc B", "text": "The supplier delivers within 14 days of the purchase order."},
]


def concept(label="Minimum purchase commitment", **over):
    base = {"label": label, "definition": "A promise to order a minimum quantity.", "synonyms": [],
            "examples": [], "type": "obligation", "unit": None, "grain": None, "evidence": ["c1"]}
    return {**base, **over}


class Fake:
    """Hand-written replies: `induce` is a list of replies in call order, `merge` one reply."""

    def __init__(self, induce, merge=None):
        self.induce, self.merge, self.calls = list(induce), merge, []

    def complete(self, system, user):
        task = json.loads(user)["task"]
        self.calls.append((task, system, user))
        if task == "induce":
            return json.dumps(self.induce.pop(0))
        return json.dumps(self.merge if self.merge is not None else {"groups": []})


def run(replies, merge=None, chunks=CHUNKS, **kw):
    return ci.induce(chunks, Fake(replies, merge), **kw)


def test_valid_reply_gives_contract_fields():
    out = run([{"concepts": [concept(examples=["at least 71 units"], unit="units", grain="each year")]}])
    assert len(out) == 1
    c = out[0]
    assert set(c) == {"id", "label", "definition", "synonyms", "examples", "type", "unit", "grain", "evidence",
                      "origin", "status"}
    assert (c["origin"], c["status"], c["evidence"], c["examples"]) == ("document", "proposed", ["c1"], ["at least 71 units"])
    assert c["id"].startswith("doc:") and c["unit"] == "units" and c["grain"] == "each year"


def test_concept_without_evidence_is_dropped():
    out = run([{"concepts": [concept(), concept("Ghost", evidence=[])]}])
    assert [c["label"] for c in out] == ["Minimum purchase commitment"]


def test_evidence_id_never_offered_raises():
    with pytest.raises(ci.InductionError):
        run([{"concepts": [concept(evidence=["c9"])]}])


def test_non_verbatim_example_is_removed():
    out = run([{"concepts": [concept(examples=["at least 71 units", "ships within 3 days"])]}])
    assert out[0]["examples"] == ["at least 71 units"]


def test_example_must_come_from_an_evidence_chunk():
    out = run([{"concepts": [concept(examples=["delivers within 14 days"])]}])  # text of c3, evidence is c1
    assert out[0]["examples"] == []


@pytest.mark.parametrize("bad", [
    concept(label=" "), concept(definition=""), concept(type="thing"), concept(synonyms="x"),
    concept(evidence="c1"), {"label": "x"},
])
def test_invalid_concept_raises(bad):
    with pytest.raises(ci.InductionError):
        run([{"concepts": [bad]}])


@pytest.mark.parametrize("reply", ["not json", "[]", {"concepts": "x"}, {}])
def test_malformed_reply_raises_and_nothing_is_substituted(reply):
    chat = type("C", (), {"complete": lambda self, s, u: reply if isinstance(reply, str) else json.dumps(reply)})()
    with pytest.raises(ci.InductionError):
        ci.induce(CHUNKS, chat)


def test_same_label_in_two_batches_merges_deterministically():
    a = concept(evidence=["c1"], synonyms=["minimum order"])
    b = concept("minimum purchase  commitment", evidence=["c2"], definition="A promise to order at least a stated minimum quantity of a product in a period.")
    out = run([{"concepts": [a]}, {"concepts": [b]}], chunks=CHUNKS[:2], batch_chars=70)
    assert len(out) == 1
    assert out[0]["evidence"] == ["c1", "c2"] and out[0]["synonyms"] == ["minimum order"]
    assert out[0]["definition"] == "A promise to order at least a stated minimum quantity of a product in a period."


def test_model_merge_pass_joins_listed_ids():
    out = run([{"concepts": [concept("Order floor", evidence=["c1"]), concept("Quantity counting", type="measure", evidence=["c2"])]}],
              merge={"groups": [{"ids": ["m1", "m2"], "label": "Order quantity", "definition": "Ordered quantity rules."}]})
    assert len(out) == 1 and out[0]["label"] == "Order quantity" and out[0]["evidence"] == ["c1", "c2"]
    assert "Order floor" in out[0]["synonyms"]


def test_invalid_merge_group_is_skipped_and_counted_never_applied():
    stats = {}
    chat = Fake([{"concepts": [concept(), concept("Other", evidence=["c2"]), concept("Third", evidence=["c3"])]}], merge={"groups": [
        {"ids": ["x", "y"], "label": "L", "definition": "D"},                       # unknown ids
        {"ids": ["m1", "m2"], "label": "Joined", "definition": "D"},                # valid
        {"ids": ["m2", "m3"], "label": "Again", "definition": "D"}]})               # m2 already used
    out = ci.induce(CHUNKS, chat, stats=stats)
    assert sorted(c["label"] for c in out) == ["Joined", "Third"] and stats["merge_groups_skipped"] == 2
    with pytest.raises(ci.InductionError):  # a reply that is not {"groups": [...]} still raises
        ci.induce(CHUNKS, Fake([{"concepts": [concept(), concept("Other", evidence=["c2"])]}], merge={"x": 1}))


def test_granularity_changes_only_the_instruction():
    reply = [{"concepts": [concept()]}]
    coarse, fine = Fake(reply), Fake(reply)
    ci.induce(CHUNKS, coarse, granularity="coarse")
    ci.induce(CHUNKS, fine, granularity="fine")
    assert coarse.calls[0][2] == fine.calls[0][2] and coarse.calls[0][1] != fine.calls[0][1]
    with pytest.raises(ValueError):
        ci.induce(CHUNKS, coarse, granularity="medium")


def test_stability_counts_matches_and_flags_single_run_concepts():
    a = [{"label": "Minimum purchase commitment", "synonyms": [], "evidence": ["c1"]},
         {"label": "Delivery lead time", "synonyms": ["lead time"], "evidence": ["c3"]},
         {"label": "Warranty", "synonyms": [], "evidence": ["c2"]}]
    b = [{"label": "minimum purchase commitment", "synonyms": [], "evidence": ["c1"]},
         {"label": "Supplier lead time", "synonyms": ["Lead time"], "evidence": ["c3"]},
         {"label": "Payment terms", "synonyms": [], "evidence": ["c2"]}]
    report = ci.stability(a, b, shared=["c1", "c2", "c3"])
    assert report["matched"] == 2
    assert report["agreement"] == round(2 / 4, 3)
    assert report["only_in_a"] == ["Warranty"] and report["only_in_b"] == ["Payment terms"]


def test_stability_ignores_concepts_outside_the_shared_chunks():
    a = [{"label": "X", "synonyms": [], "evidence": ["c1"]}, {"label": "Y", "synonyms": [], "evidence": ["c9"]}]
    b = [{"label": "X", "synonyms": [], "evidence": ["c1"]}]
    report = ci.stability(a, b, shared=["c1"])
    assert report["agreement"] == 1.0 and report["not_comparable"] == ["Y"]


def test_manufacturing_chunks_are_stable_and_unique():
    chunks = ci.dataset_chunks("manufacturing")
    assert len(chunks) > 5 and len({c["chunk_id"] for c in chunks}) == len(chunks)
    assert all(c["text"].strip() and c["title"] for c in chunks)
    assert chunks == ci.dataset_chunks("manufacturing")


def test_replay_recorded_replies():
    """Real replies recorded on 2026-11-21 replay offline; a changed prompt is a miss, never a substitute."""
    cache = ci.InductionCache(ci.DEFAULT_DIR)
    assert len(cache) > 0, "no recorded replies"
    chunks = ci.dataset_chunks("manufacturing")
    out = ci.induce(chunks, ci.CachedChat(cache, None, ci.DEFAULT_MODEL), granularity="coarse")
    offered = {c["chunk_id"] for c in chunks}
    assert out and all(c["evidence"] and set(c["evidence"]) <= offered for c in out)
    assert all(c["type"] in ci.TYPES and c["label"] and c["definition"] for c in out)


# ---------------------------------------------------------------- follow-up: unit/grain check, finance PDFs

def test_unit_and_grain_kept_only_when_their_words_are_in_the_evidence():
    stats = {}
    chat = Fake([{"concepts": [
        concept(unit="units", grain="per year"),            # "units" is in c1, "per year" is not ("each year")
        concept("Counting", type="measure", evidence=["c2"], unit="sales orders", grain="period"),
    ]}])
    out = ci.induce(CHUNKS, chat, stats=stats)
    assert (out[0]["unit"], out[0]["grain"]) == ("units", None)
    assert (out[1]["unit"], out[1]["grain"]) == ("sales orders", "period")
    assert stats["unit_grain_removed"] == 1


def test_finance_chunks_include_xfa_forms_and_are_masked():
    pytest.importorskip("pypdf")
    chunks = ci.dataset_chunks("finance")
    titles = {c["title"] for c in chunks}
    assert {"customer-form1.pdf", "customer-form2.pdf"} <= titles
    assert len({c["chunk_id"] for c in chunks}) == len(chunks)
    import re
    assert not any(re.search(r"\b[STFGM]\d{7}[A-Z]\b", c["text"]) for c in chunks)
    assert ci.dataset_chunks("finance") == chunks


def test_missing_pinned_pdf_is_reported_not_skipped(monkeypatch, tmp_path):
    real = ci.load_originals()
    broken = {k: v.__class__(tmp_path, v.relative, v.digest, v.size) if k.endswith("cleaned_pdf_1") else v
              for k, v in real.items()}
    monkeypatch.setattr(ci, "load_originals", lambda: broken)
    with pytest.raises(ci.InductionError, match="cleaned_pdf_1"):
        ci.dataset_chunks("finance")


def test_extra_key_in_a_concept_is_ignored_but_a_missing_key_raises():
    out = run([{"concepts": [{**concept(), "attributes": ["x"]}]}])
    assert "attributes" not in out[0] and out[0]["label"] == "Minimum purchase commitment"


# ---------------------------------------------------------------- one bad reply must not abort the run (2026-11-22)

class Live:
    """A live client double: replies in call order, the last one repeats."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), 0

    def complete(self, system, user):
        self.calls += 1
        return json.dumps(self.replies[min(self.calls, len(self.replies)) - 1])


def test_a_list_valued_unit_is_retried_and_the_invalid_reply_is_not_cached(tmp_path):
    bad = {"concepts": [concept(unit=["units", "percent"])]}
    good = {"concepts": [concept()]}
    live = Live(bad, good)
    cache = ci.InductionCache(tmp_path)
    stats = {}
    out = ci.induce(CHUNKS, ci.CachedChat(cache, live), stats=stats)
    assert [c["label"] for c in out] == ["Minimum purchase commitment"] and live.calls == 2
    assert len(cache) == 1 and json.loads(cache.get(next(iter(cache._rows)))) == good   # the bad reply was never kept
    assert not stats.get("failed_items") and stats["model_units"] == 1


def test_a_reply_that_stays_invalid_fails_that_unit_and_the_run_continues(tmp_path):
    texts = [{"chunk_id": f"c{n}", "title": "D", "text": "x" * 40} for n in (1, 2, 3)]

    class Chat:
        def __init__(self):
            self.calls = 0

        def complete(self, system, user):
            data = json.loads(user)
            if data["task"] == "merge":
                return json.dumps({"groups": []})
            ids = [c["id"] for c in data["chunks"]]
            if ids == ["c1"]:
                return json.dumps({"concepts": [concept(unit=["units", "percent"], evidence=["c1"])]})
            self.calls += 1
            return json.dumps({"concepts": [concept(f"Concept {ids[0]}", evidence=ids[:1])]})

    stats = {}
    out = ci.induce(texts, Chat(), stats=stats, batch_chars=50)
    assert [c["label"] for c in out] == ["Concept c2", "Concept c3"]
    assert stats["failed_items"] == [{"task": "induce", "reason": "unit must be text or null"}]
    assert stats["model_units"] == 4   # three batches and the merge pass


def test_a_recorded_invalid_reply_is_not_retried_offline(tmp_path):
    cache = ci.InductionCache(tmp_path)
    bad = {"concepts": [concept(unit=["units"])]}
    system = ci._BASE + ci.GRANULARITY["coarse"]
    user = json.dumps({"task": "induce", "chunks": [{"id": f"c{n}", "title": c["title"], "text": c["text"]}
                                                   for n, c in enumerate(CHUNKS, 1)]}, ensure_ascii=False)
    cache.put(ci.prompt_key(ci.DEFAULT_MODEL, system, user), json.dumps(bad))
    stats = {}
    assert ci.induce(CHUNKS, ci.CachedChat(cache), stats=stats) == []   # no ChatMiss: the invalid reply is a failed unit
    assert len(stats["failed_items"]) == 1


def test_without_a_stats_dict_an_invalid_reply_still_raises():
    with pytest.raises(ci.InductionError):
        run([{"concepts": [concept(unit=["units"])]}])


def test_failures_above_the_stated_share_fail_loudly():
    ok = {"model_units": 20, "failed_items": [{"task": "t", "reason": "r"}] * 2}   # exactly the share
    assert ci.check_failure_share(ok)["share"] == 0.1
    with pytest.raises(ci.InductionError, match="3 of 20"):
        ci.check_failure_share({"model_units": 20, "failed_items": [{"task": "t", "reason": "r"}] * 3})
