"""The detail behind one answer (2026-11-22): the rows behind a figure, a context series for a chart, and suggested
next questions. Read-only.

The browser never sends SQL. The answer route issues an opaque handle per fact and per reply, held here and bound to
the dataset and the account. Rows and series are derived from the SQL the compiler built and the guard passed for that
fact: the same FROM, JOIN and WHERE, with only the projection, one removed condition and a GROUP BY changed. The caller
applies the same dataset gate as the answer route; withheld sources are refused with the compiler's own check, and
every reply is masked."""

from __future__ import annotations

import calendar
import datetime
import json
import re
import secrets
from collections import OrderedDict
from threading import Lock

import sqlglot
import sqlglot.expressions as sge

from ._pilot_postgres import PilotDatabaseError
from .extracted_fields import mask_extracted
from .metric_sql import (
    _IDENT, MetricQueryError, SourceWithheld, _check_sources, _dataset_schema, _helios, _list_metric_defs,
    column_range, table_date_field)
from .periods import parse_periods

SYSTEM = {"sourcebatch", "sourcerevision", "rowguid", "modifieddate", "groupseq", "transactionseq"}
ROWS = 50
MAX_POINTS = 62          # a day series up to 62 days; a longer span is read per month
MAX_CATEGORIES = 12
MODES = ("rows", "series", "next")
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}")
_AGGREGATES = {sge.Sum: "sum", sge.Count: "count", sge.Min: "min", sge.Max: "max"}
_ORDINAL = {1: "first", 2: "second", 3: "third", 4: "fourth"}


class Details:
    """Server-held handles. ponytail: in memory, last 2,000 answers; a restart forgets them and the page asks again."""

    def __init__(self, limit=2000):
        self._items, self._lock, self._limit = OrderedDict(), Lock(), limit

    def _put(self, entry):
        token = secrets.token_hex(16)     # hex: never mistaken for an NRIC by the response mask
        with self._lock:
            self._items[token] = entry
            while len(self._items) > self._limit:
                self._items.popitem(last=False)
        return token

    def register(self, reply, dataset, principal):
        """The reply with `detail` on each fact ({id, rows, tables}) and on the reply (an id for suggestions)."""
        bound = {"dataset": dataset, "principal": principal["id"]}
        facts = []
        for fact in reply.get("facts") or []:
            shape = describe(fact)
            facts.append({**fact, "detail": {"id": self._put({**bound, "fact": fact}), **shape}} if shape else fact)
        plan = reply.get("plan") or {}
        entry = {**bound, "question": reply.get("question") or "", "status": reply.get("status"),
                 "reason": reply.get("refusalReason"), "definitions": list(reply.get("definitions") or []),
                 "measure": plan.get("measure"), "timeField": plan.get("time_field")}
        return {**reply, "facts": facts, "detail": self._put(entry)}

    def resolve(self, token, dataset, principal):
        with self._lock:
            entry = self._items.get(token)
        if entry is None or entry["dataset"] != dataset or entry["principal"] != principal["id"]:
            raise LookupError(token)
        return entry


def respond(details, body, principal, workspace, pg):
    """(HTTP status, payload) of POST /api/answer/detail {dataset, handle, mode}."""
    if not isinstance(body, dict) or set(body) != {"dataset", "handle", "mode"} or body["mode"] not in MODES \
            or not all(isinstance(body[k], str) for k in body):
        return 400, {"error": "Require a dataset, an answer handle and a mode (rows, series or next)"}
    if body["dataset"] not in principal["datasets"] or workspace is None:
        return 403, {"error": "Dataset access withheld for this account"}
    try:
        entry = details.resolve(body["handle"], body["dataset"], principal)
        if (body["mode"] == "next") == ("fact" in entry):
            raise LookupError(body["handle"])
    except LookupError:
        return 404, {"error": "This answer is no longer held by the server. Ask the question again."}
    try:
        out = {"rows": rows, "series": series, "next": suggestions}[body["mode"]](entry, workspace, pg)
    except SourceWithheld:
        return 403, {"error": "The source behind this answer is withheld for this account."}
    except (MetricQueryError, PilotDatabaseError):
        return 503, {"error": "The data could not be queried."}
    return 200, mask_extracted(out)


# ---------------------------------------------------------------- the fact's own SQL

def _parse(sql):
    tree = sqlglot.parse_one(sql, read="postgres")
    base = tree.find(sge.From).this
    joins = [j.this for j in tree.args.get("joins") or []]
    where = tree.args.get("where")
    conds = _and_parts(where.this) if where is not None else []
    return tree, base, joins, conds, tree.expressions[0].this


def _and_parts(node):
    return _and_parts(node.this) + _and_parts(node.expression) if isinstance(node, sge.And) else [node]


def _column_of(cond):
    """(table, column) lower case of a `column op literal` condition, else None."""
    left = getattr(cond, "this", None)
    left = left.this if isinstance(left, sge.Cast) else left
    return (left.table.lower(), left.name.lower()) if isinstance(left, sge.Column) else None


def _literal(cond):
    right = getattr(cond, "expression", None)
    return str(right.this) if isinstance(right, sge.Literal) else None


def _aggregate(fact, agg):
    """sum, count, min or max when the fact aggregates rows; None for a one-value lookup (MIN = MAX of one row)."""
    if fact.get("kind") == "field" and "aggregate" not in (fact.get("filters") or {}):
        return None
    return _AGGREGATES.get(type(agg))


def describe(fact):
    """{rows, tables} of a fact with compiler SQL, or None."""
    try:
        _, base, joins, _, agg = _parse(fact["sql"])
    except (KeyError, TypeError, sqlglot.errors.ParseError, AttributeError):
        return None
    return {"rows": _aggregate(fact, agg) is not None, "tables": [t.name.lower() for t in (base, *joins)]}


def _json(pg, sql):
    """Rows of a read-only query as dicts."""
    try:
        raw = pg.sql("SET statement_timeout = '15s'; SET default_transaction_read_only = on; "
                     f"SELECT COALESCE(json_agg(t), '[]') FROM ({sql}) t;")
    except PilotDatabaseError as error:
        raise MetricQueryError(str(error)) from error
    return json.loads(raw or "[]")


def _fields(defs):
    """{(table lower, column lower): field definition}."""
    out = {}
    for d in defs:
        if d.get("kind") == "field":
            table, column = d["source_id"].split("/")[-2:]
            out[table.lower(), column.lower()] = d
    return out


def _name(fields, table, column):
    d = fields.get((table.lower(), column.lower()))
    return d["source_id"].split("/")[-1] if d else column


def _label(fields, table, column):
    d = fields.get((table.lower(), column.lower()))
    return (d or {}).get("label") or column


def _query(tree, projections, conds, *, group=False, order=None, limit=None):
    q = tree.select(*projections, append=False)
    q.set("where", None)
    q.set("limit", None)
    if conds:
        q = q.where(sge.and_(*conds))
    if group:
        q = q.group_by("1")
    if order is not None:
        q = q.order_by(order)
    if limit:
        q = q.limit(limit)
    return q.sql(dialect="postgres")


def _prepare(entry, workspace, pg):
    fact = entry["fact"]
    tree, base, joins, conds, agg = _parse(fact["sql"])
    names = [t.name.lower() for t in (base, *joins)]
    if any(not _IDENT.match(n) for n in names) or any(
            (t.db or "").lower() != _dataset_schema(entry["dataset"]) for t in (base, *joins)):
        raise MetricQueryError("The fact's SQL does not read this dataset")
    _check_sources(workspace, set(names))
    return fact, tree, base, conds, agg, _fields(_list_metric_defs(pg, entry["dataset"]))


# ---------------------------------------------------------------- rows behind a figure

def rows(entry, workspace, pg):
    fact, tree, base, conds, agg, fields = _prepare(entry, workspace, pg)
    aggregate = _aggregate(fact, agg)
    if aggregate is None:
        return {"rows": None}
    table = base.name.lower()
    visible = [(t, c) for (t, c) in fields if t == table and c not in SYSTEM]
    measured = agg.this.this if isinstance(agg.this, sge.Distinct) else agg.this
    measure = (measured.table.lower(), measured.name.lower()) if isinstance(measured, sge.Column) else None
    if measure and measure not in visible and measure[1] not in SYSTEM:
        visible.append(measure)
    columns = [{"name": _name(fields, t, c), "label": _label(fields, t, c)} for t, c in visible]
    projections = [sge.alias_(sge.column(c, table=t), col["name"], quoted=True) for (t, c), col in zip(visible, columns)]
    order = None
    if aggregate in ("min", "max") and measure:
        order = sge.Ordered(this=sge.column(measure[1], table=measure[0]), desc=aggregate == "max")
    elif visible:
        order = sge.column(visible[0][1], table=visible[0][0])
    found = _json(pg, _query(tree, projections, conds, order=order, limit=ROWS))
    total = _json(pg, _query(tree, [sge.alias_(sge.Count(this=sge.Star()), "n")], conds))[0]["n"]
    return {"columns": columns, "rows": found, "total": total, "aggregate": aggregate,
            "measure": next((c["name"] for (t, c2), c in zip(visible, columns) if (t, c2) == measure), None)}


# ---------------------------------------------------------------- the context series of a chart

def _span(conds):
    """(from, to) inclusive ISO days of the date conditions on one column."""
    low = high = None
    for c in conds:
        value = _literal(c)[:10]
        if isinstance(c, sge.EQ):
            low = high = value
        elif isinstance(c, (sge.GTE, sge.GT)):
            low = value
        elif isinstance(c, sge.LTE):
            high = value
        elif isinstance(c, sge.LT):
            high = (datetime.date.fromisoformat(value) - datetime.timedelta(days=1)).isoformat()
    return low, high


def series(entry, workspace, pg):
    fact, tree, base, conds, agg, fields = _prepare(entry, workspace, pg)
    aggregate = _aggregate(fact, agg)
    metric = fact.get("kind", "metric") == "metric"
    timed = {k for k, d in fields.items() if ((d.get("source_metadata") or {}).get("dimension") or {}).get("is_time")}
    dated = [c for c in conds if _column_of(c) in timed and _ISO.match(_literal(c) or "")]
    if dated and (metric or aggregate in ("sum", "count")):
        ref = _column_of(dated[0])
        mine = [c for c in conds if _column_of(c) == ref]
        keep = [c for c in conds if _column_of(c) != ref]
        low, high = _span(mine)
        day = f"CAST({ref[0]}.{ref[1]} AS DATE)"
        edges = _json(pg, _query(tree, [sqlglot.parse_one(f"MIN({day})").as_("a"), sqlglot.parse_one(f"MAX({day})").as_("b")], keep))[0]
        if not edges["a"]:
            return {"kind": None}
        snapshot = fields[ref].get("concept_id") == "snapshot_date"
        days = (datetime.date.fromisoformat(edges["b"][:10]) - datetime.date.fromisoformat(edges["a"][:10])).days
        grain = "day" if snapshot or days <= MAX_POINTS else "month"
        key = day if grain == "day" else f"CAST(DATE_TRUNC('month', {ref[0]}.{ref[1]}) AS DATE)"
        found = _json(pg, _query(tree, [sqlglot.parse_one(key).as_("k"), agg.copy().as_("v")], keep, group=True,
                                 order=sqlglot.parse_one("1"), limit=5000))
        points = []
        for row in found:
            start = str(row["k"])[:10]
            end = start if grain == "day" else _month_end(start)
            points.append({"key": start, "value": row["v"], "answered": bool(low and high and start <= high and end >= low)})
        if len(points) > MAX_POINTS:      # keep the points nearest the answered period
            first = next((i for i, p in enumerate(points) if p["answered"]), len(points) - 1)
            start = max(0, min(first - MAX_POINTS // 2, len(points) - MAX_POINTS))
            points = points[start:start + MAX_POINTS]
        return {"kind": "date", "grain": grain, "axis": _label(fields, *ref), "unit": _unit(fact, pg, entry),
                "period": {"from": low, "to": high}, "points": points}
    # a categorical filter: an equality on a non-date column; whether it has at most 12 values is read from the data
    chosen = next((c for c in conds if isinstance(c, sge.EQ) and _column_of(c) and _column_of(c)[1] not in SYSTEM
                   and _column_of(c) not in timed), None)
    if chosen is not None and aggregate in ("sum", "count"):
        ref = _column_of(chosen)
        keep = [c for c in conds if c is not chosen]
        found = _json(pg, _query(tree, [sge.column(ref[1], table=ref[0]).as_("k"), agg.copy().as_("v")], keep, group=True,
                                 order=sqlglot.parse_one("1"), limit=MAX_CATEGORIES + 1))
        if not 1 < len(found) <= MAX_CATEGORIES:
            return {"kind": None, "why": "The field holds more than 12 values." if found else None}
        value = _literal(chosen)
        return {"kind": "category", "axis": _label(fields, *ref), "unit": _unit(fact, pg, entry), "value": value,
                "points": [{"key": str(r["k"]), "value": r["v"], "answered": str(r["k"]) == value} for r in found]}
    return {"kind": None}


def _month_end(start):
    d = datetime.date.fromisoformat(start)
    return d.replace(day=calendar.monthrange(d.year, d.month)[1]).isoformat()


def _unit(fact, pg, entry):
    if fact.get("kind", "metric") != "metric":
        return "count" if "count" in str((fact.get("filters") or {}).get("aggregate")) else None
    d = next((x for x in _list_metric_defs(pg, entry["dataset"]) if x["id"] == fact.get("definition")), {})
    return _helios(d.get("source_metadata") or {}).get("unit")


# ---------------------------------------------------------------- suggested next questions

def _short(ident):
    return re.sub(r"^.*?:definition:[^/]+/", "", ident or "")


def _measure(entry, defs):
    wanted = [_short(i) for i in entry["definitions"]] + [entry["measure"]]
    by_short = {_short(d["id"]): d for d in defs if d.get("kind") in ("metric", "field")}
    return next((by_short[w] for w in wanted if w in by_short), None)


def _date_column(defs, measure, time_field):
    """(table, column) the measure's period applies to, or None."""
    if time_field and time_field.startswith("field/") and time_field.count("/") == 2:
        return tuple(time_field.split("/")[1:])
    if measure["kind"] == "metric":
        h = _helios(measure.get("source_metadata") or {})
        base = h.get("dataset")
        if (h.get("required_filters") or {}).get("BusinessDate"):
            return base, "BusinessDate"
        field = h.get("time_field") or ""
        if field and field != "none recorded":
            table, _, column = field.rpartition(".")
            return table or base, column
        return None
    table = measure["source_id"].split("/")[-2]
    column = table_date_field(defs, table)[0]
    return (table, column) if column else None


def _phrase(kind, start):
    d = datetime.date.fromisoformat(start)
    month = calendar.month_name[d.month]
    if kind == "day":
        return f"{d.day} {month} {d.year}"
    if kind == "month":
        return f"{month} {d.year}"
    if kind == "quarter":
        return f"the {_ORDINAL[(d.month - 1) // 3 + 1]} quarter of {d.year}"
    if kind == "half":
        return f"the {'first' if d.month <= 6 else 'second'} half of {d.year}"
    return str(d.year)


_MONTHS = {"month": 1, "quarter": 3, "half": 6, "year": 12}


def _floor(kind, day):
    d = datetime.date.fromisoformat(day)
    step = _MONTHS[kind]
    return datetime.date(d.year, (d.month - 1) // step * step + 1, 1)


def _shift(kind, start, n):
    """The start of the period `n` units after the one starting at `start`."""
    months = start.year * 12 + start.month - 1 + n * _MONTHS[kind]
    return datetime.date(months // 12, months % 12 + 1, 1)


def _end(kind, start):
    return _shift(kind, start, 1) - datetime.timedelta(days=1)


def _replace(question, period, kind, start):
    """The question with its period's words replaced by the phrase of another period."""
    before, after = question[:period["start"]], question[period["end"]:]
    if kind == "day" and re.search(r"\bat\s+$", before):
        before = re.sub(r"\bat(\s+)$", r"on\1", before)
    return before + _phrase(kind, start) + after


def _distinct_days(pg, dataset, table, column):
    if not _IDENT.match(table) or not _IDENT.match(column):
        return []
    found = _json(pg, f"SELECT DISTINCT CAST({column.lower()} AS DATE) AS d FROM {_dataset_schema(dataset)}.{table.lower()} "
                      "ORDER BY 1 LIMIT 400")
    return [str(r["d"])[:10] for r in found if r["d"]]


def _candidates(kind, around, low, high, days, n, step):
    """Up to `n` periods of `kind` starting at the period that holds `around` and moving by `step` (-1 or +1), that
    overlap the data [low, high]. A day on a snapshot table is one of the days the data holds."""
    out = []
    if kind == "day":
        pool = days or []
        if pool:
            ordered = sorted(pool, reverse=step < 0)
            return [d for d in ordered if (d >= around if step > 0 else d <= around)][:n]
        d = datetime.date.fromisoformat(around)
        while len(out) < n and low <= d.isoformat() <= high:
            out.append(d.isoformat())
            d += datetime.timedelta(days=step)
        return out
    start = _floor(kind, around)
    while len(out) < n:
        end = _end(kind, start)
        if end.isoformat() < low or start.isoformat() > high:
            break
        out.append(start.isoformat())
        start = _shift(kind, start, step)
    return out


def suggestions(entry, workspace, pg):
    """{"suggestions": [{label, question, why}]}: up to three questions built by code from the reply and the data."""
    defs = _list_metric_defs(pg, entry["dataset"])
    measure = _measure(entry, defs)
    question = entry["question"]
    if measure is None or entry["reason"] == "unknown party":
        return {"suggestions": []}
    where = _date_column(defs, measure, entry["timeField"])
    tables = {measure["source_id"].split("/")[-2].lower()} if measure["kind"] == "field" else set()
    if measure["kind"] == "metric":
        tables.add(str(_helios(measure.get("source_metadata") or {}).get("dataset") or "").lower())
    if where:
        tables.add(where[0].lower())
    _check_sources(workspace, {t for t in tables if t})
    fields = _fields(defs)
    found, unresolved = parse_periods(question)
    out = []
    if where and (where[0].lower(), where[1].lower()) in fields:
        table, column = where
        snapshot = fields[table.lower(), column.lower()].get("concept_id") == "snapshot_date"
        days = _distinct_days(pg, entry["dataset"], table, column) if snapshot else None
        low, high = column_range(pg, entry["dataset"], table, column)
        period = found[0] if len(found) == 1 else None
        kind = period and ("day" if period["kind"] in ("day", "month_end") else period["kind"])
        if low and period and kind in ("day", *_MONTHS):
            if entry["status"] == "refused" and entry["reason"] == "period outside the data":
                after = period["from"] > high
                picks = _candidates(kind, high if after else low, low, high, days, 2, -1 if after else 1)
                out = [{"label": _phrase(kind, p), "question": _replace(question, period, kind, p),
                        "why": "nearest in the data"} for p in picks]
            elif entry["status"] == "answered":
                for step, why in ((-1, "previous period"), (1, "next period")):
                    if kind == "day":
                        picks = [d for d in _candidates("day", period["from"], low, high, days, 2, step) if d != period["from"]]
                    else:
                        picks = _candidates(kind, _shift(kind, _floor(kind, period["from"]), step).isoformat(), low, high,
                                            None, 1, step)
                    out += [{"label": _phrase(kind, picks[0]), "question": _replace(question, period, kind, picks[0]),
                             "why": why}] if picks else []
        elif low and entry["status"] == "clarify" and not found and not unresolved:
            dates = [("day", d) for d in sorted(days or [], reverse=True)[:3]] if snapshot else \
                [("month", _floor("month", high).isoformat()), ("year", _floor("year", high).isoformat())]
            values = _missing_values(pg, entry["dataset"], measure, question, fields)
            stem = question.rstrip().rstrip("?").rstrip()
            when = lambda kind, start: f" {'on' if kind == 'day' else 'in'} {_phrase(kind, start)}"   # noqa: E731
            if values:
                label, picks = values
                out = [{"label": f"{v}, {_phrase(*dates[0])}" if dates else v,
                        "question": f"{stem} for {label.lower()} {v}{when(*dates[0]) if dates else ''}?",
                        "why": "completes the question"} for v in picks[:3]]
            else:
                out = [{"label": _phrase(k, s), "question": f"{stem}{when(k, s)}?",
                        "why": "completes the question"} for k, s in dates[:3]]
    return {"suggestions": out[:3]}


def _missing_values(pg, dataset, measure, question, fields):
    """(field label, values most rows hold first) of the first value the metric requires once that the question does
    not name, when that field holds at most 12 values; else None."""
    if measure["kind"] != "metric":
        return None
    h = _helios(measure.get("source_metadata") or {})
    table = str(h.get("dataset") or "")
    for column, kind in (h.get("required_filters") or {}).items():
        if kind != "single_value" or column in ("BusinessDate", "SourceBatch") or not _IDENT.match(column) \
                or not _IDENT.match(table):
            continue
        found = _json(pg, f"SELECT {column.lower()} AS v, COUNT(*) AS n FROM {_dataset_schema(dataset)}.{table.lower()} "
                          f"WHERE {column.lower()} IS NOT NULL GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT {MAX_CATEGORIES + 1}")
        values = [str(r["v"]) for r in found]
        if not values or len(values) > MAX_CATEGORIES:
            return None
        if any(re.search(rf"(?i)(?<!\w){re.escape(v)}(?!\w)", question) for v in values):
            continue
        return _label(fields, table, column), values
    return None
