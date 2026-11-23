"""Per-chunk interpreter calls of one source run on a bounded pool; the result equals the sequential one."""

import json
import threading
import time

import pytest

from semantic_pipeline_v2.evidence_links import EntityKeys, build_links
from semantic_pipeline_v2.evaluate_mapping import evaluate
from semantic_pipeline_v2.interpreter import ChatCache, CachedChat, ChatError, Interpreter
from semantic_pipeline_v2.path1 import Path1Mapper
from semantic_pipeline_v2.workspace import load_workspace
from test_interpreter import DEV, FIXTURES, chunk, lead_chat, no_link


@pytest.fixture(scope="module")
def mfg():
    return load_workspace("manufacturing")


class Slow:
    """A fake chat: the answer depends on the passage; every call sleeps; a passage with FAIL raises."""
    available, model = True, "slow"

    def __init__(self, delay=0.05):
        self.delay, self.active, self.peak, self.lock = delay, 0, 0, threading.Lock()

    def complete(self, system, user):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        time.sleep(self.delay)
        with self.lock:
            self.active -= 1
        data = json.loads(user)
        if "FAIL" in data["passage"]:
            raise ChatError("boom")
        return lead_chat(data)


def build(mfg, workers, monkeypatch, texts, **kw):
    monkeypatch.setenv("V2_INTERPRET_WORKERS", str(workers))
    chat = Slow()
    interpreter = Interpreter(CachedChat(ChatCache("/nonexistent-cache"), chat))
    chunks = [chunk(t, n) for n, t in enumerate(texts)]
    links = build_links(mfg, chunks, Path1Mapper(mfg, None), interpreter, EntityKeys(mfg, "/nonexistent"), **kw)
    return links, chat, interpreter


TEXTS = [f"Average lead time: 14 calendar days, clause {n}." if n % 3 else f"Boilerplate {n}." for n in range(12)]


def test_workers_do_not_change_the_result(mfg, monkeypatch):
    one, chat1, i1 = build(mfg, 1, monkeypatch, TEXTS)
    four, chat4, i4 = build(mfg, 4, monkeypatch, TEXTS)
    assert one == four and [k["chunk_id"] for k in four] == [f"c{n}" for n in range(12)]
    assert chat1.peak == 1 and 1 < chat4.peak <= 4
    assert i1.calls == i4.calls == 12 and i1.chars == i4.chars == i4.chars
    assert i4.chat.calls == 12  # no lost counter update


def test_an_error_surfaces_the_same_way(mfg, monkeypatch):
    texts = ["Average lead time: 14 calendar days.", "FAIL one", "Average lead time: 14 calendar days again.", "FAIL two"]
    for workers in (1, 4):
        monkeypatch.setenv("V2_INTERPRET_WORKERS", str(workers))
        with pytest.raises(ChatError, match="boom"):
            build_links(mfg, [chunk(t, n) for n, t in enumerate(texts)], Path1Mapper(mfg, None),
                        Interpreter(Slow()), EntityKeys(mfg, "/nonexistent"), strict=True)
    one, *_ = build(mfg, 1, monkeypatch, texts)
    four, *_ = build(mfg, 4, monkeypatch, texts)
    assert one == four and [k["status"] for k in four].count("degraded") == 2


def test_recorded_fixtures_replay_identically(monkeypatch):
    reports = []
    for workers in ("1", "4"):
        monkeypatch.setenv("V2_INTERPRET_WORKERS", workers)
        reports.append(evaluate(DEV, "interpret", FIXTURES / "embeddings", chat_cache_dir=FIXTURES / "interpretations"))
    assert reports[0] == reports[1]
