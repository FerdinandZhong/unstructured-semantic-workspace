"""Output A: evidence links, up to three per chunk, stored in private PostgreSQL.

A link says which definition a chunk is about, how (`states_value_of` or `is_about`), the value, unit and period
the chunk states, and an entity key when an exact identifier is present. Retrieval proposes 12 candidates, an
interpreter model selects or abstains, deterministic guards check the reply. Without a working interpreter the
link is stored as `degraded`: candidates kept, nothing selected. A chunk the model finds no counterpart for is
stored once with no definition and relation `none`, so the abstention is auditable. A chunk past the configured
limit (`V2_MAX_LINKED_CHUNKS`) is stored as `not_processed`: every chunk has a record.
Each link keeps the selected `definition_id` and adds `canonical_definition_id`: the primary-key field that a
declared many-to-one relationship reaches from the selected field (a field with none is its own canonical id).

Human mapping corrections overlay the stored links when they are read, as `corrected` or `rejected`.
"""

from semantic_pipeline_v2.model_cache import load_yaml
from concurrent.futures import ThreadPoolExecutor
import csv
import json
import os
from pathlib import Path
import re
import threading

import yaml

from .interpreter import (PROMPT_VERSION, PROMPTS, RESTATE_VERSION, SHORTLIST_VERSION, ChatError, InterpreterError,
                          apply_guards)
from .chunk_index import field_passages_enabled
from .semantic_store import _jsonb, _lit, _opt

K = 12  # candidates of variant V12 and the default cut of the retrieval legs
# Candidate stage (action P2g). Methods, frozen on 2026-11-21:
#   lexical, vector      one leg alone, top 12, full cards, one deciding call
#   hybrid12 (was V12)   both legs, top 12: the behaviour before P2g
#   hybrid30, hybrid50   both legs, top 30 or 50 full cards, one deciding call
#   expanded             the model restates the passage in business terms (prompt restate-v1); hybrid retrieval over
#                        passage plus restatement, top 12; a bad reply falls back to plain hybrid (counted in notes)
#   two_step (was V2S)   a wide compact list (the whole catalog at or below WHOLE_CATALOG_MAX definitions, else the
#                        top POOL by retrieval); the model shortlists at most five ids (prompt shortlist-v1); the
#                        decision sees the full cards of the shortlist only. The default.
VARIANTS = {"lexical": 12, "vector": 12, "hybrid12": 12, "hybrid30": 30, "hybrid50": 50, "expanded": 12, "two_step": None}
ALIASES = {"V12": "hybrid12", "V30": "hybrid30", "V50": "hybrid50", "V2S": "two_step"}
LEGS = {"lexical": ("lexical",), "vector": ("vector",)}  # every other method uses both legs
DEFAULT_VARIANT, POOL, WHOLE_CATALOG_MAX, EXTRA = "two_step", 60, 150, 0
MAX_STORED = 50  # at most this many candidates and scores are stored per link


def candidate_settings(variant=None, pool=None, whole_max=None, extra=None):
    """Arguments first, then V2_CANDIDATE_VARIANT, V2_SHORTLIST_POOL, V2_WHOLE_CATALOG_MAX, V2_SHORTLIST_EXTRA, then defaults.
    `extra` adds that many top retrieval candidates to a non-empty shortlist."""
    def number(value, name, default):
        try:
            return max(0, int(value if value is not None else os.environ.get(name, "")))
        except ValueError:
            return default

    variant = variant or os.environ.get("V2_CANDIDATE_VARIANT", "").strip() or DEFAULT_VARIANT
    variant = ALIASES.get(variant, variant)
    if variant not in VARIANTS:
        raise ValueError(f"unknown candidate variant {variant!r}; use one of {', '.join(VARIANTS)}")
    return {"variant": variant, "pool": number(pool, "V2_SHORTLIST_POOL", POOL) or POOL,
            "whole_max": number(whole_max, "V2_WHOLE_CATALOG_MAX", WHOLE_CATALOG_MAX),
            "extra": number(extra, "V2_SHORTLIST_EXTRA", EXTRA)}
MAX_LINKED_CHUNKS = 256  # default; covers every current demo source. One model call per chunk.
STATUSES = ("proposed", "degraded", "corrected", "rejected", "not_processed")
CONTRACT = ("chunk_id", "source_id", "source_revision", "span", "definition_id", "canonical_definition_id", "relation",
            "stated_value", "unit", "period", "entity_key", "entity_keys", "interpretation", "candidates", "scores", "method",
            "model_revision", "status", "actor", "recorded_at")


def max_linked_chunks():
    """Chunks interpreted per source: `V2_MAX_LINKED_CHUNKS`, else the default."""
    try:
        return max(0, int(os.environ.get("V2_MAX_LINKED_CHUNKS", "")))
    except ValueError:
        return MAX_LINKED_CHUNKS

_DDL = """
    CREATE TABLE IF NOT EXISTS v2_evidence_links (
        link_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        dataset text NOT NULL,
        chunk_id text NOT NULL,
        source_id text NOT NULL,
        source_revision text NOT NULL,
        span jsonb NOT NULL,
        definition_id text,
        canonical_definition_id text,
        relation text NOT NULL CHECK (relation IN ('states_value_of', 'is_about', 'none')),
        stated_value text,
        unit text,
        period text,
        entity_key text NOT NULL DEFAULT 'unresolved',
        entity_keys jsonb NOT NULL DEFAULT '[]'::jsonb,
        interpretation text,
        candidates jsonb NOT NULL,
        scores jsonb NOT NULL,
        method text NOT NULL,
        model_revision text,
        status text NOT NULL,
        actor text NOT NULL,
        notes jsonb NOT NULL DEFAULT '{}'::jsonb,
        recorded_at timestamptz NOT NULL DEFAULT now()
    );
    -- idempotent migration of the one-link-per-chunk table: new column, wider status check, one link per definition
    ALTER TABLE v2_evidence_links ADD COLUMN IF NOT EXISTS canonical_definition_id text;
    ALTER TABLE v2_evidence_links ADD COLUMN IF NOT EXISTS entity_keys jsonb NOT NULL DEFAULT '[]'::jsonb;
    ALTER TABLE v2_evidence_links DROP CONSTRAINT IF EXISTS v2_evidence_links_dataset_chunk_id_key;
    ALTER TABLE v2_evidence_links DROP CONSTRAINT IF EXISTS v2_evidence_links_status_check;
    ALTER TABLE v2_evidence_links ADD CONSTRAINT v2_evidence_links_status_check
        CHECK (status IN ('proposed', 'degraded', 'corrected', 'rejected', 'not_processed'));
    CREATE UNIQUE INDEX IF NOT EXISTS v2_evidence_links_one_per_definition
        ON v2_evidence_links (dataset, chunk_id, COALESCE(definition_id, ''));
    CREATE INDEX IF NOT EXISTS v2_evidence_links_source ON v2_evidence_links (dataset, source_id);
"""


# ---------------------------------------------------------------- entity keys

_KEY_SUFFIX = ("ID", "Number")


def _spaced_name(column):
    return r"\s?".join(re.findall(r"[A-Z][a-z]*|[a-z]+", column)) or column


class EntityKeys:
    """Exact identifiers only. A name never resolves; a bare number resolves only after its column label."""

    def __init__(self, workspace, data_dir=None):
        from .definition_cards import ROOT
        from .workspace import FACTORY, FINANCE_PROJECTIONS

        structured = workspace["knowledge"]["structured"]
        source = next(s for s in workspace["sources"] if s["id"] == structured["modelSource"])
        model = load_yaml((ROOT / source["citation"]["path"]).read_bytes())
        self.keys = {d["name"]: [c for c in d.get("primary_key") or [] if c.endswith(_KEY_SUFFIX)]
                     for d in model.get("datasets", [])}
        self.single_key = {d["name"]: d["primary_key"][0] for d in model["datasets"] if len(d.get("primary_key") or []) == 1}
        self.parents = {}  # (table, column) -> the primary-key (table, column) a declared relationship reaches
        for rel in model.get("relationships", []):
            target = set(next((d.get("primary_key") or [] for d in model["datasets"] if d["name"] == rel["to"]), []))
            for f, t in zip(rel["from_columns"], rel["to_columns"]):
                if t in target and (rel["from"], f) != (rel["to"], t):
                    self.parents.setdefault((rel["from"], f), (rel["to"], t))
        self.children = {}  # table -> [(fk column, parent table, parent key column)] for ID/Number columns
        for (table, column), (parent, key) in self.parents.items():
            if column.endswith(_KEY_SUFFIX):
                self.children.setdefault(table, []).append((column, *self._canon(parent, key)))
        self.dir = Path(data_dir) if data_dir else (
            FINANCE_PROJECTIONS if workspace["id"] == "financial-services" else ROOT / FACTORY / "data" / "csv")
        self._values, self._rows, self._labels = {}, {}, None

    def _canon(self, table, column):
        seen = set()
        while (table, column) in self.parents and (table, column) not in seen and self.single_key.get(table) != column:
            seen.add((table, column))
            table, column = self.parents[table, column]
        return table, column

    def canonical_id(self, ident):
        """The primary-key field a field id reaches through declared relationships; any other id is its own.
        A field that is the whole single-column primary key of its table already identifies that entity."""
        head, table, column = ident.rsplit("/", 2)
        if not head.endswith("/field"):
            return ident
        table, column = self._canon(table, column)
        return f"{head}/{table}/{column}"

    def values(self, table, column):
        if (table, column) not in self._values:
            found = set()
            path = self.dir / f"{table}.csv"
            if path.is_file() and not path.is_symlink():
                with path.open(encoding="utf-8", newline="") as stream:
                    reader = csv.reader(stream)      # one column by position: a whole-table DictReader is 3x slower
                    head = next(reader, [])
                    at = head.index(column) if column in head else None
                    found = {row[at] for row in reader if at is not None and len(row) > at and row[at]}
            self._values[table, column] = found
        return self._values[table, column]

    def row(self, table, column, value):
        """The first data row of the table whose key column has this value."""
        if (table, column) not in self._rows:
            rows = {}
            path = self.dir / f"{table}.csv"
            if path.is_file() and not path.is_symlink():
                with path.open(encoding="utf-8", newline="") as stream:
                    for r in csv.DictReader(stream):
                        rows.setdefault(r.get(column), r)
            self._rows[table, column] = rows
        return self._rows[table, column].get(value)

    def _label_patterns(self):
        """Per own key column: a pattern for `<entity word>[ ID][:#] <number>`. The words come from the model:
        the key column without its ID/Number suffix and the last part of the table name. A word that only
        names the last part of the key (order, entity) is weak: it counts when one column holds the number."""
        if self._labels is None:  # built aside and assigned whole: parallel chunks never see a half-built list
            labels, weak = [], {}
            for table, columns in self.keys.items():
                for column in columns:
                    if self._canon(table, column) != (table, column):
                        continue
                    stem = re.sub(r"(ID|Number)$", "", column)
                    last = re.findall(r"[A-Z][a-z]*|[a-z]+", stem)[-1:] if stem else []
                    strong = [w for w in (stem, table.rsplit("_", 1)[-1]) if w]
                    weak_words = [w.lower() for w in last if w != stem]
                    tail = r"(?:\s?(?:ID|Number|No))?\s*[:#]?\s*(\d+)\b"
                    pat = lambda words: re.compile(  # noqa: E731
                        r"(?i)(?<![A-Za-z])(?:" + "|".join(_spaced_name(w) for w in words) + r")s?" + tail)
                    labels.append((table, column, pat(strong) if strong else None,
                                   pat(weak_words) if weak_words else None))
            self._labels = labels
        return self._labels

    def stated(self, text):
        """[(table, column, value)] for every exact identifier in the text, on any table of the model."""
        tokens, found, labelled = set(re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]*", text)), [], {}
        for table, column, strong, weak in self._label_patterns():
            known = self.values(table, column)
            found += [(table, column, t) for t in sorted(tokens & known)
                      if re.search(r"\d", t) and re.search(r"[A-Za-z]", t)]
            for n, pattern in enumerate((strong, weak)):
                for number in (pattern.findall(text) if pattern else []):
                    if number in known:
                        (found.append((table, column, number)) if n == 0
                         else labelled.setdefault(number, []).append((table, column)))
        for number, cols in labelled.items():  # a weak label counts only when one column holds the number
            if len(cols) == 1 and not any(f[2] == number for f in found):
                found.append((*cols[0], number))
        found = list(dict.fromkeys(found))
        # one column name and value on several tables is one identifier: keep the table it is the whole key of
        owners = {(c, v) for t, c, v in found if self.single_key.get(t) == c}
        return [f for f in found if (f[1], f[2]) not in owners or self.single_key.get(f[0]) == f[1]]

    def chunk_keys(self, text):
        """The entity keys a passage and its context state, plus the owners they reach through declared
        relationships (`derived`, with the path in `note`)."""
        out = [{"key": f"{t}.{c}={v}", "how": "stated"} for t, c, v in self.stated(text)]
        seen, queue = {k["key"] for k in out}, [(t, c, v, 1) for t, c, v in self.stated(text)]
        while queue:
            table, column, value, depth = queue.pop(0)
            row = self.row(table, column, value)
            for fk, parent, key in self.children.get(table, ()) if row and depth <= 2 else ():
                if fk != column and row.get(fk) in self.values(parent, key):
                    name = f"{parent}.{key}={row[fk]}"
                    if name not in seen:
                        seen.add(name)
                        out.append({"key": name, "how": "derived", "note": f"{table}.{column}={value} -> {fk}"})
                        queue.append((parent, key, row[fk], depth + 1))
        return out

    def key_value(self, table, column, texts):
        """The one existing value of a key column found as a token in the texts, else None (none or several)."""
        known = self.values(table, column)
        found = {t for text in texts for t in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]*", text)} & known
        return next(iter(found)) if len(found) == 1 else None

    def resolve(self, table, text):
        """'Table.Column=value' for each exact identifier in the text, joined by '; ', or 'unresolved'."""
        tokens, found = set(re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]*", text)), []
        for column in self.keys.get(table, ()):
            known = self.values(table, column)
            mixed = {v for v in known if re.search(r"\d", v) and re.search(r"[A-Za-z]", v)}
            found += [f"{table}.{column}={t}" for t in sorted(tokens & mixed)]
            for number in re.findall(rf"(?i)\b{_spaced_name(column)}\s*[:#]?\s*(\d+)\b", text):
                if number in known:  # a bare number counts only right after its column label
                    found.append(f"{table}.{column}={number}")
        return "; ".join(dict.fromkeys(found)) or "unresolved"


# ---------------------------------------------------------------- chunk context

_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.M)


def chunk_context(title, chunk, artifact=None):
    """Document title, nearest preceding heading, speaker: what a reader sees around the passage."""
    parts = [f"Document: {title}"] if title else []
    if artifact:
        headings = _HEADING.findall(artifact[:chunk["start_offset"]])
        if headings:
            parts.append(f"Section: {headings[-1]}")
    if chunk.get("speaker"):
        parts.append(f"Speaker: {chunk['speaker']}")
    return "\n".join(parts)


# ---------------------------------------------------------------- build

_POOLS, _POOLS_LOCK = {}, threading.Lock()


def _pool_map(fn, *iterables):
    """`map(fn, ...)` as a list, the calls on a thread pool shared by the process: `V2_INTERPRET_WORKERS` threads
    (default 4; 1 is the plain sequential loop), so sources that run in parallel do not multiply the workers.
    Results keep their order; the first failure in order is raised, as in the loop."""
    try:
        workers = max(1, int(os.environ.get("V2_INTERPRET_WORKERS", "4")))
    except ValueError:
        workers = 4
    if workers == 1:
        return list(map(fn, *iterables))
    with _POOLS_LOCK:
        pool = _POOLS.setdefault(workers, ThreadPoolExecutor(max_workers=workers, thread_name_prefix="interpret"))
    return list(pool.map(fn, *iterables))


def interpret_items(mapper, interpreter, entity_keys, items, *, strict=False, degrade_bad_replies=False, **options):
    """Decisions per item: retrieval candidates, then the interpreter's links after the guards.

    `items` are {"text", "context", "source_kind"}. Each item yields a list of one to three decisions: one per
    link, or exactly one with relation `none` (no counterpart, or degraded). `strict` re-raises provider errors
    (evaluation), except that `degrade_bad_replies`
    keeps an unusable reply as a degraded record; a missing recorded reply (`ChatMiss`) always raises.
    `options` are the candidate settings (`variant`, `pool`, `whole_max`, `extra`; see `candidate_settings`).
    A decision's `candidates` are the ids the deciding call saw (at most MAX_STORED); `method` names the variant
    and, for V2S, the size of the wide list and of the shortlist.
    """
    setting = candidate_settings(**options)
    variant, pool = setting["variant"], setting["pool"]
    retrieval = [{**i, "anchor_kind": "native_text"} for i in items]
    restated = {}
    if variant == "expanded" and interpreter is not None and interpreter.available:
        def restate(item):  # step one: one sentence in business terms; a bad reply falls back to plain hybrid
            try:
                return interpreter.restate(item["text"], item["context"])
            except (InterpreterError, ChatError) as error:
                if strict and not (degrade_bad_replies and isinstance(error, InterpreterError)):
                    raise
                return None
        for n, text in enumerate(_pool_map(restate, items)):
            restated[n] = text
            if text:
                retrieval[n] = {**retrieval[n], "text": f"{items[n]['text']}\n{text}"}
    results = mapper.map_many(retrieval, k=VARIANTS[variant] or pool, legs=LEGS.get(variant, ("lexical", "vector"))) if items else []
    cards = mapper.cards

    def decide(n, item, result):
        ranked = [r["id"] for r in sorted(result["candidates"], key=lambda r: r["retrievalRank"])]
        rows = {r["id"]: r for r in result["candidates"]}
        tag = f"{variant}:{VARIANTS[variant]}"
        ids = ranked[:VARIANTS[variant] or K]
        notes = {"retrievalDegraded": result["degraded"], "context": item["context"], "variant": variant}
        if variant == "expanded":
            notes["restatement"] = restated.get(n)
            notes["restatePrompt"] = RESTATE_VERSION
            notes["expandedFallback"] = bool(items) and restated.get(n) is None
        keys = entity_keys.chunk_keys(f"{item['text']}\n{item['context']}")

        def base_for(ids):
            return dict(candidates=ids[:MAX_STORED], entity_key=_joined(keys), entity_keys=keys, stated_value=None,
                        unit=None, period=None, definition_id=None, canonical_definition_id=None, relation="none",
                        interpretation=None, scores={i: {"retrieval": rows[i]["retrieval"], "lexical": rows[i]["lexical"],
                                                        "proximity": rows[i]["proximity"]} for i in ids[:MAX_STORED] if i in rows})

        base = base_for(ids)
        if interpreter is None or not interpreter.available:
            if variant == "two_step":  # no model: record what step one would have offered, for offered-share measurement
                whole = len(cards) <= setting["whole_max"]
                notes["step1"] = {"offered": len(cards) if whole else min(pool, len(ranked)), "wholeCatalog": whole,
                                  "ids": [] if whole else ranked[:pool]}
            return [{**base, "method": "retrieval_only", "model_revision": None, "status": "degraded",
                         "notes": {**notes, "degraded": "no_interpreter"}}]
        # V2_INTERPRET_PROMPT=interp-v4: interp-v4 for a passage with commitment wording, interp-v3 for the others
        switched = getattr(interpreter, "version", PROMPT_VERSION) != PROMPT_VERSION
        version = interpreter.version_for(item["text"]) if switched else PROMPT_VERSION
        method = f"interpret:{version}+{result['arm']}+{tag}"
        try:
            if variant == "two_step":
                whole = len(cards) <= setting["whole_max"]
                wide = list(dict.fromkeys(ranked + list(cards))) if whole else ranked[:pool]
                tag = f"two_step:{len(wide)}"
                method = f"interpret:{version}+{result['arm']}+{tag}>!"  # replaced once the shortlist is known
                step1 = {"offered": len(wide), "wholeCatalog": whole, "prompt": PROMPTS[version][2] if switched else SHORTLIST_VERSION}
                if not whole:
                    step1["ids"] = wide
                notes["step1"] = step1
                short = interpreter.shortlist(item["text"], item["context"], [cards[i] for i in wide])
                step1["shortlist"] = short
                ids = short + [i for i in ranked if i not in short][:setting["extra"]] if short else []
                method = f"interpret:{version}+{result['arm']}+{tag}>{len(short)}"
                base = base_for(ids)
                if not short:  # an empty shortlist is an abstention: no second call
                    return [{**base, "method": method, "model_revision": interpreter.model, "status": "proposed",
                                 "interpretation": "No definition of the semantic layer is about this passage.",
                                 "notes": {**notes, "dropped": [], "withdrawn": []}}]
            reply = interpreter.interpret(item["text"], item["context"], [cards[i] for i in ids])
        except (InterpreterError, ChatError) as error:
            if strict and not (degrade_bad_replies and isinstance(error, InterpreterError)):
                raise
            return [{**base, "method": method, "model_revision": interpreter.model, "status": "degraded",
                         "notes": {**notes, "degraded": type(error).__name__}}]
        common = {**base, "interpretation": reply["interpretation"], "method": method,
                  "model_revision": interpreter.model, "status": "proposed"}
        decisions = []
        for link in reply["links"]:
            ident = link["definition_id"]
            guarded = apply_guards(link, item["text"], cards[ident], source_kind=item.get("source_kind"),
                                   constrained=ident in mapper.constrained)
            if guarded["definition_id"] is None:  # a guard withdrew the link
                continue
            if switched:
                guarded = _identifier_guard(guarded, cards[ident], entity_keys)
            canonical = entity_keys.canonical_id(ident)
            table = canonical.rsplit("/", 2)[1] if cards[ident]["kind"] == "field" else cards[ident]["dataset"]
            column = canonical.rsplit("/", 1)[1]
            if (cards[ident]["kind"] == "field" and column in entity_keys.keys.get(table, ())
                    and "allegation_is_not_a_recorded_value" not in guarded["guards"]):
                said = guarded["entity"] if guarded["entity"] and guarded["entity"] in item["text"] + item["context"] else ""
                value = entity_keys.key_value(table, column, [item["text"], said])
                if value is not None:  # a key field's value is the identifier the passage names
                    guarded = {**guarded, "stated_value": value, "relation": "states_value_of",
                               "guards": [*guarded["guards"], "key_value_in_passage"]}
                    keys = keys + [{"key": f"{table}.{column}={value}", "how": "stated"}] if not any(
                        k["key"] == f"{table}.{column}={value}" for k in keys) else keys
            decisions.append({**common, "definition_id": ident, "canonical_definition_id": canonical,
                              "relation": guarded["relation"], "stated_value": guarded["stated_value"],
                              "unit": guarded["unit"], "period": guarded["period"], "entity_keys": keys, "entity_key": _joined(keys),
                              "notes": {**notes, "guards": guarded["guards"], "reason": guarded["reason"],
                                        "dropped": reply["dropped"]}})
        return decisions or [{**common, "notes": {**notes, "dropped": reply["dropped"],
                                                       "withdrawn": [k["definition_id"] for k in reply["links"]]}}]
    return _pool_map(decide, range(len(items)), items, results)


def _identifier_guard(link, card, entity_keys):
    """interp-v4 only: an identifier field keeps a stated value only when it is a value its column holds and no
    unit comes with it. A promised quantity ("71 units") is never the value of a product number."""
    value = link["stated_value"]
    if card.get("role") != "identifier" or card.get("kind") != "field" or value is None:
        return link
    if not link["unit"] and value in entity_keys.values(card["dataset"], card["name"]):
        return link
    return {**link, "stated_value": None, "unit": None, "period": None, "relation": "is_about",
            "guards": [*link["guards"], "identifier_value_not_held"]}


def _joined(keys):
    return "; ".join(k["key"] for k in keys) or "unresolved"


def _inherit(records, entity_keys_obj):
    """Per source: when the document states exactly one key for a table, chunks with no key of that table inherit it."""
    by_chunk = {}
    for r in records:
        by_chunk.setdefault((r["source_id"], r["chunk_id"]), []).append(r)
    by_source = {}
    for (source, _), group in by_chunk.items():
        by_source.setdefault(source, []).append(group)
    for groups in by_source.values():
        subjects = {}
        for group in groups:
            for k in group[0]["entity_keys"]:
                if k["how"] == "stated":
                    subjects.setdefault(k["key"].split(".", 1)[0], set()).add(k["key"])
        for table, found in subjects.items():
            if len(found) != 1:
                continue
            (key,) = found
            for group in groups:
                if not any(k["key"].split(".", 1)[0] == table for k in group[0]["entity_keys"]):
                    keys = [*group[0]["entity_keys"], {"key": key, "how": "inherited",
                                                       "note": "the one key this document states for " + table}]
                    for r in group:
                        r["entity_keys"], r["entity_key"] = keys, _joined(keys)
    return records


def _record(chunk, decision):
    return {"chunk_id": chunk["chunk_id"], "source_id": chunk["source_id"], "source_revision": chunk["source_revision"],
            "span": {"start": chunk["start_offset"], "end": chunk["end_offset"], "page": chunk["page"],
                     "text": chunk["text"]}, "actor": "pipeline", **decision}


def build_links(workspace, chunks, mapper, interpreter, entity_keys, *, title=None, source_kind=None,
                artifact=None, strict=False, limit=None, **options):
    """Links of one source: every chunk has a record. The first `limit` chunks are interpreted (default
    `max_linked_chunks()`); each later chunk gets one `not_processed` record."""
    limit = max_linked_chunks() if limit is None else limit
    done, rest = chunks[:limit], chunks[limit:]
    heading = set()
    if field_passages_enabled():  # a chunk of section headings only (a label-and-value layout makes them) needs no call
        heading = {c["chunk_id"] for c in done if all(_HEADING.fullmatch(x) for x in c["text"].split("\n") if x.strip())}
    asked = [c for c in done if c["chunk_id"] not in heading]
    items = [{"text": c["text"][:2000], "context": chunk_context(title, c, artifact), "source_kind": source_kind}
             for c in asked]
    decided = iter(interpret_items(mapper, interpreter, entity_keys, items, strict=strict, **options))
    only_heading = dict(candidates=[], scores={}, stated_value=None, unit=None, period=None, definition_id=None,
                        canonical_definition_id=None, relation="none", method="heading_only", model_revision=None,
                        status="proposed", interpretation="A section heading only: nothing to link.",
                        notes={"reason": "section heading only"})
    links = []
    for c in done:
        if c["chunk_id"] in heading:
            keys = entity_keys.chunk_keys(f"{c['text'][:2000]}\n{chunk_context(title, c, artifact)}")
            links.append(_record(c, {**only_heading, "entity_keys": keys, "entity_key": _joined(keys)}))
        else:
            links += [_record(c, d) for d in next(decided)]
    skipped = dict(candidates=[], scores={}, entity_key="unresolved", entity_keys=[], stated_value=None, unit=None, period=None,
                   definition_id=None, canonical_definition_id=None, relation="none", interpretation=None,
                   method="not_processed", model_revision=None, status="not_processed",
                   notes={"limit": limit, "reason": "chunk limit reached"})
    for c in rest:
        keys = entity_keys.chunk_keys(f"{c['text'][:2000]}\n{chunk_context(title, c, artifact)}")
        links.append(_record(c, {**skipped, "entity_keys": keys, "entity_key": _joined(keys)}))
    return _inherit(links, entity_keys)


# ---------------------------------------------------------------- store and overlay

class EvidenceLinks:
    def __init__(self, database):
        self.database = database
        database.sql(_DDL)

    def replace(self, dataset, source_id, links):
        """Swap every link of one source in one transaction. Re-ingest is idempotent."""
        rows = ",\n".join("(" + ", ".join((
            _lit(dataset), _lit(k["chunk_id"]), _lit(source_id), _lit(k["source_revision"]), _jsonb(k["span"]),
            _opt(k["definition_id"]), _opt(k.get("canonical_definition_id")), _lit(k["relation"]), _opt(k["stated_value"]), _opt(k["unit"]),
            _opt(k["period"]), _lit(k["entity_key"]), _jsonb(k.get("entity_keys", [])), _opt(k["interpretation"]), _jsonb(k["candidates"]),
            _jsonb(k["scores"]), _lit(k["method"]), _opt(k["model_revision"]), _lit(k["status"]),
            _lit(k["actor"]), _jsonb(k["notes"]))) + ")" for k in links)
        insert = f"""INSERT INTO v2_evidence_links (dataset, chunk_id, source_id, source_revision, span, definition_id,
            canonical_definition_id, relation, stated_value, unit, period, entity_key, entity_keys, interpretation, candidates, scores, method,
            model_revision, status, actor, notes) VALUES {rows};""" if links else ""
        self.database.sql(f"BEGIN;\nDELETE FROM v2_evidence_links WHERE dataset = {_lit(dataset)} "
                          f"AND source_id = {_lit(source_id)};\n{insert}\nCOMMIT;")

    def read(self, dataset, source_id=None):
        where = "dataset = :'dataset'" + (" AND source_id = :'source'" if source_id else "")
        params = {"dataset": dataset, **({"source": source_id} if source_id else {})}
        output = self.database.sql(f"""
            SELECT COALESCE(jsonb_agg(to_jsonb(t) - 'dataset' - 'link_id' ORDER BY source_id, (span->>'start')::int, chunk_id, link_id),
                            '[]'::jsonb)
            FROM v2_evidence_links t WHERE {where};""", **params)
        return json.loads(output)


def overlay(links, mappings, observations, canonical=lambda ident: ident):
    """Apply human corrections to links. `mappings` come from the corrected read of the same source."""
    quotes = {o["id"]: o["quote"] for o in observations}
    fixes = sorted(((quotes[m["observationId"]], m) for m in mappings
                    if m.get("correctionAction") in ("replace", "remove") and m.get("observationId") in quotes),
                   key=lambda pair: pair[1]["observationId"])
    out = []
    for link in links:
        text = link["span"]["text"]
        siblings = [k for k in links if k.get("chunk_id") == link.get("chunk_id")]
        # a correction names the link it replaces; with several links in a chunk only that link changes
        # (a name that matches none of them falls on the first link of the chunk)
        replace = next((m for q, m in fixes if q in text and m["correctionAction"] == "replace"
                        and (m.get("originalTargetId") in (None, link.get("definition_id"))
                             or (siblings[0] is link and m.get("originalTargetId") not in
                                 {k.get("definition_id") for k in siblings}))), None)
        remove = next((m for q, m in fixes if q in text and m["correctionAction"] == "remove"
                       and m.get("originalTargetId") == link.get("definition_id")), None)
        fix = replace or remove
        if fix is None:
            out.append(link)
            continue
        actor, at = fix.get("correctionActor") or "reviewer", fix.get("correctionAt")
        notes = {**link.get("notes", {}), "originalDefinitionId": link["definition_id"],
                 "originalStatus": link["status"]}
        if replace:
            out.append({**link, "definition_id": replace["targetId"], "canonical_definition_id": canonical(replace["targetId"]), "relation": "is_about", "stated_value": None,
                        "unit": None, "period": None, "status": "corrected",
                        "actor": actor, "recorded_at": at, "notes": notes})
        else:
            out.append({**link, "status": "rejected", "actor": actor, "recorded_at": at, "notes": notes})
    return out


def to_jsonl(links):
    return "".join(json.dumps(link, ensure_ascii=False, sort_keys=True) + "\n" for link in links)
