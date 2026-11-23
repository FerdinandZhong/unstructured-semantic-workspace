"""Step 6 progress: the real steps of one Ask request, recorded by the server as they run (plan 2026-11-22 step 6).

A `Trace` is an observer. The answer path and the writer call `mark(stage)` when a stage starts and `note(...)` to add
counts they already hold; both are no-ops outside a traced request. Recording makes no model call and no query.
The detail of a finished step comes from the reply the user receives, so a trace never names more than the reply does
(a restricted reader's withheld sources included). An in-flight trace is readable through `Progress` by the same
account and session for the same dataset only, and it is forgotten shortly after the request ends.
"""

from __future__ import annotations

import contextvars
import hashlib
import re
import time
from threading import Lock

_CURRENT = contextvars.ContextVar("ask_trace", default=None)
REQUEST_ID = re.compile(r"^[a-f0-9]{16,64}$")
RETAIN_SECONDS = 30

ANSWER = [("route", "Route"), ("understand", "Understand the question"), ("plan", "Plan"), ("query", "Query the data"),
          ("read", "Read the documents"), ("decide", "Check and decide")]
PREVIEW = [("route", "Route"), ("preview", "Build the preview"), ("spark", "Generate the Spark job"),
           ("local", "Write to the local database"), ("iceberg", "Submit to Iceberg")]
CONFIRM = [("local", "Write to the local database"), ("iceberg", "Submit to Iceberg")]
SEMANTIC = [("route", "Route"), ("lookup", "Look up the term")]
CONNECTIONS = [("route", "Route"), ("connections", "Read the connections")]


class Trace:
    def __init__(self, steps, clock=time.monotonic):
        self.clock, self.lock, self.t0 = clock, Lock(), clock()
        self.steps = [{"key": k, "title": t, "state": "waiting", "startMs": None, "ms": None, "detail": {}} for k, t in steps]
        self.running = None
        self.ended = None

    def _step(self, key):
        return next(s for s in self.steps if s["key"] == key)

    def _close(self, now):
        if self.running is not None:
            step = self._step(self.running)
            step["state"], step["ms"] = "done", max(0, round((now - self.t0) * 1000) - step["startMs"])
            self.running = None

    def start(self, key):
        with self.lock:
            if self.running == key:       # already running (the caller marked it before loading what the stage reads)
                return
            now = self.clock()
            self._close(now)
            step = self._step(key)
            step["state"], step["startMs"], step["ms"] = "running", round((now - self.t0) * 1000), None
            self.running = key

    def note(self, **detail):
        with self.lock:
            if self.running is not None:
                self._step(self.running)["detail"].update(detail)

    def count(self, name):
        with self.lock:
            if self.running is not None:
                detail = self._step(self.running)["detail"]
                detail[name] = detail.get(name, 0) + 1

    def end(self):
        """Close the running step. Returns the key of the last step that ran (where the request stopped)."""
        with self.lock:
            last = self.running or next((s["key"] for s in reversed(self.steps) if s["state"] == "done"), None)
            self._close(self.clock())
            self.ended = self.clock()
            return last

    def view(self, live=False):
        """The steps; a live view leaves out the detail (it is added from the reply when the request ends)."""
        with self.lock:
            return [{**s, "detail": {}} if live else dict(s) for s in self.steps]


def mark(stage):
    trace = _CURRENT.get()
    if trace is not None:
        trace.start(stage)


def note(**detail):
    trace = _CURRENT.get()
    if trace is not None:
        trace.note(**detail)


def count(name):
    trace = _CURRENT.get()
    if trace is not None:
        trace.count(name)


class active:
    """`with active(trace):` makes `mark` and `note` record into `trace` in this thread; the trace ends on exit, so an
    error never leaves a request "running" in the progress view."""

    def __init__(self, trace):
        self.trace = trace

    def __enter__(self):
        self.token = _CURRENT.set(self.trace)
        return self.trace

    def __exit__(self, *_):
        _CURRENT.reset(self.token)
        self.trace.end()


# ---------------------------------------------------------------- the detail of each step, from the reply

def _set(steps, key, state, **detail):
    for s in steps:
        if s["key"] == key:
            s["state"] = state
            s["detail"].update(detail)


def answer_steps(trace, reply):
    """Finish an answer trace from the reply. The step that ran last is where a refusal or a clarification stopped."""
    stop = trace.end()
    steps = trace.view()
    status, plan = reply.get("status"), reply.get("plan") or {}
    by_key = {s["key"]: s for s in steps}
    understand = by_key["understand"]["detail"]
    understand.update(entities=[{"key": e.get("key"), "how": e.get("how")} for e in reply.get("entities") or []],
                      concepts=[c.get("label") for c in (reply.get("semantics") or {}).get("concepts") or []])
    if plan:
        by_key["plan"]["detail"].update(
            measure=plan.get("measure"), aggregate=plan.get("aggregate"), time=plan.get("time") or [],
            filters=[{"field": f.get("field"), "op": f.get("op", "="), "value": f.get("value")}
                     for f in plan.get("filters") or [] if isinstance(f, dict)],
            needs=[{"need": n.get("need"), "type": n.get("type")} for n in plan.get("evidence") or []])
    by_key["query"]["detail"].update(facts=[{"definition": f.get("definition"), "value": f.get("value"),
                                             "tables": (f.get("detail") or {}).get("tables") or [], "sql": f.get("sql")}
                                            for f in reply.get("facts") or []])
    by_key["read"]["detail"].update(cited=len(reply.get("citations") or []),
                                    documents=list(dict.fromkeys(c.get("title") or "" for c in reply.get("citations") or [])))
    by_key["decide"]["detail"].update(status=status, reason=reply.get("reason"), refusalReason=reply.get("refusalReason"),
                                      comparison=reply.get("comparison"), conclusion=reply.get("conclusion"))
    if status != "answered" and stop in by_key:
        by_key[stop]["detail"]["stopped"] = {"status": status, "reason": reply.get("reason") or reply.get("error"),
                                             "guard": reply.get("refusalReason")}
        if status == "refused":
            by_key[stop]["state"] = "failed"
    for s in steps:
        if s["state"] == "waiting":
            s["state"] = "skipped"
    if status == "answered" and not plan.get("measure") and not reply.get("facts"):
        by_key["query"]["state"] = "skipped"
    return steps


def finish(trace, **details):
    """Close a trace and set the detail of named steps: details = {key: {"state"?: ..., **detail}}."""
    trace.end()
    steps = trace.view()
    for key, detail in details.items():
        detail = dict(detail)
        state = detail.pop("state", None)
        for s in steps:
            if s["key"] == key:
                s["detail"].update(detail)
                if state:
                    s["state"] = state
    return steps


# ---------------------------------------------------------------- in-flight traces, per account, session and dataset

class Progress:
    """ponytail: in server memory, a dict scanned on every call; enough for a demo server with a few users."""

    def __init__(self):
        self.lock, self.items = Lock(), {}

    @staticmethod
    def owner(principal, session, dataset):
        return hashlib.sha256(f"{principal['id']}\0{session or ''}\0{dataset}".encode()).hexdigest()

    def _purge(self):
        for key in [k for k, (_, t) in self.items.items() if t.ended is not None and t.clock() - t.ended > RETAIN_SECONDS]:
            del self.items[key]

    def add(self, request_id, owner, trace):
        if not isinstance(request_id, str) or not REQUEST_ID.match(request_id):
            return
        with self.lock:
            self._purge()
            if request_id not in self.items:          # an id in use (another request) is never taken over
                self.items[request_id] = (owner, trace)

    def read(self, request_id, owner):
        with self.lock:
            self._purge()
            held = self.items.get(request_id)
        if held is None or held[0] != owner:
            return None
        trace = held[1]
        return {"steps": trace.view(live=True), "done": trace.ended is not None}
