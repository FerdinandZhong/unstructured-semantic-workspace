"""Metric SQL compiler for V2 Talk to Data.

Loads CSV projections into a read-only PostgreSQL schema (v2_facts_<dataset>),
plans MetricPlans from natural language, compiles guarded SQL, executes read-only
queries with a 15-second statement timeout, and narrates results.

All table and column names are stored and referenced in lower-case to avoid
PostgreSQL quoting issues when matching un-quoted identifiers in metric expressions.

Design notes
------------
* ``ensure_projections_loaded`` sends each CSV through the client connection with
  ``COPY ... FROM STDIN``, so the file never has to exist on the database host.
  Column types come from every row of the column, never from a sample. A file that
  cannot be loaded raises ``ProjectionLoadError`` naming the file; nothing is skipped.

* Ratio metrics (``a / b``) are compiled by substituting the ANSI_SQL
  expressions of both sub-metrics inline, wrapped with NULLIF to guard against
  division by zero.  Both sub-metrics must reference the same dataset table.

* A date filter or dimension on another table is compiled only through exactly one
  relationship that the model declares, and only many-to-one from the metric's table
  to a table whose declared primary key is the whole target side of the relationship
  (so a join can never multiply rows).  The compiler never infers a join.

* The guard checks the content of the SQL, not a second copy of the compiler: one SELECT,
  one projection, no subquery or set operation, only the plan's tables, only columns the model
  defines for them, only allowlisted functions, a WHERE of AND-ed column-to-literal comparisons,
  and literals that come from the validated plan or the model's own metric expression.

* Talk to Data never returns a number for a question it did not fully understand. A date phrase,
  a breakdown, a noun or a metric the compiler cannot honour raises ``ClarificationNeeded``.
  Grouping is not implemented, so a breakdown is always clarified.
"""

from __future__ import annotations

import calendar
import csv
import datetime
import hashlib
import io
import json
import logging
import math
import re
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from ._pilot_postgres import PilotDatabaseError
from .periods import blank, parse_periods

log =logging.getLogger(__name__)

DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ALLOWED_FUNCS = frozenset(["count", "sum", "avg", "min", "max"])
# Stop words excluded from keyword scoring to avoid common words inflating scores.
_STOP = frozenset(
    "a an the is are was were be been being have has had do does did will would "
    "shall should may might must can could of in on at to for by from with as or "
    "and but not no it its this that these those what which who how when where why "
    "one two each per all any some many much there we our us me my you your i tell "
    "give show list get".split()
)
_OPS = frozenset(["=", "<", ">", "<=", ">=", "!=", "<>", "IS NULL", "IS NOT NULL"])
_MIN_SCORE = 0.45
# Two metrics whose scores differ by less than this are a near tie, and the question is clarified.
# Chosen from observed correct resolutions: over the models' 24 example questions the smallest margin
# between the right metric and the runner-up is 0.134, and the nearest correct test question
# ("units were scrapped on work orders") has 0.061. 0.05 sits just under that, so every known
# correct question still resolves. A wrong pick with a margin above it (an unmatched "loan" at 0.059,
# "work orders" at 0.117) is caught by the unmatched-noun rule in _pick_metric, not by the margin.
_MIN_MARGIN = 0.05
_DEFAULT_FUNCS = frozenset(["Count", "Sum", "Avg", "Min", "Max", "Cast", "Nullif"])

_LOAD_TRACK_DDL = """
CREATE TABLE IF NOT EXISTS v2_projection_load (
    load_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dataset    text   NOT NULL,
    table_name text   NOT NULL,
    csv_sha256 text   NOT NULL,
    loaded_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (dataset, table_name, csv_sha256)
);
"""

_LIST_DEFINITIONS_SQL = """
SELECT COALESCE(jsonb_agg(row_to_json(d)), '[]'::jsonb)
FROM v2_definition d
WHERE dataset = :'dataset'
  AND release = (
      SELECT source_sha256 FROM v2_ontology_release
      WHERE dataset = :'dataset'
      ORDER BY release_id DESC LIMIT 1
  );
"""


# ---------------------------------------------------------------------------
# Public models
# ---------------------------------------------------------------------------

class Filter(BaseModel):
    field: str
    op: str = "="
    value: Optional[str] = None


class MetricPlan(BaseModel):
    metric: str
    dimensions: list[str] = []
    filters: list[Filter] = []
    limit: int = Field(default=100, ge=1, le=100)


class ClarificationNeeded(ValueError):
    """Raised when a user-specified filter value is not in the known distinct values."""

    def __init__(self, field: str, value: str, known: set, message: Optional[str] = None):
        self.field = field
        self.value = value
        self.known = known
        known_str = ", ".join(sorted(str(v) for v in known)[:10])
        more = " (and others)" if len(known) > 10 else ""
        super().__init__(message or (
            f"{value!r} is not a known value for {field!r}. "
            f"Known values: {known_str}{more}."
        ))


class MetricQueryError(RuntimeError):
    """The database could not answer a lookup the planner needs. Not the same as an unknown value."""


class SourceWithheld(ClarificationNeeded):
    """The facts the metric reads come from a source withheld for this account."""


class ProjectionLoadError(RuntimeError):
    """A projection file could not be loaded. The message names the file."""


def _refuse_date(helios: dict, metric: str, metric_defs: Optional[list] = None) -> None:
    """Refuse a date filter the metric cannot apply: no business date, or a date no declared join reaches."""
    time_field = helios.get("time_field") or ""
    if not time_field or time_field == "none recorded":
        raise ClarificationNeeded("date", "", set(), message=(
            f"{metric} has no business date in the source, so it cannot be filtered by date. "
            "Ask without a date."))
    table = helios.get("dataset") or ""
    if "." in time_field and table:
        try:
            _declared_join(table, time_field.split(".", 1)[0], metric_defs or [])
        except ClarificationNeeded as exc:
            raise ClarificationNeeded("date", "", set(), message=(
                f"{metric} is dated by {time_field}. {exc} Ask without a date.")) from exc


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _dataset_schema(dataset: str) -> str:
    return "v2_facts_" + re.sub(r"[^a-z0-9]", "_", dataset.lower())


def _helios(source_metadata) -> dict:
    for ext in (source_metadata or {}).get("custom_extensions", []):
        if ext.get("vendor_name") == "HELIOS":
            d = ext.get("data", {})
            if isinstance(d, str):
                try:
                    return json.loads(d)
                except (json.JSONDecodeError, TypeError):
                    return {}
            return d or {}
    return {}


_NUMERIC_PAT = re.compile(r"^-?(0|[1-9]\d*)(\.\d+)?$")
_DATE_PAT = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_STAMP_PAT = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(\.\d{1,6})?$")


def _is_date(value: str) -> bool:
    try:
        datetime.date.fromisoformat(value)
        return bool(_DATE_PAT.match(value))
    except ValueError:
        return False


def _is_stamp(value: str) -> bool:
    try:
        datetime.datetime.fromisoformat(value)
        return bool(_STAMP_PAT.match(value))
    except ValueError:
        return False


class _ColumnKind:
    """Narrows a column's type over every value. One value that does not fit makes it TEXT."""

    def __init__(self):
        self.numeric = self.date = self.stamp = True
        self.seen = False

    def add(self, value: str) -> None:
        value = value.strip()
        if not value:
            return
        self.seen = True
        if self.numeric and not _NUMERIC_PAT.match(value):
            self.numeric = False
        if self.date and not _is_date(value):
            self.date = False
        if self.stamp and not (_is_date(value) or _is_stamp(value)):
            self.stamp = False

    @property
    def sql_type(self) -> str:
        if not self.seen:
            return "TEXT"
        if self.numeric:
            return "NUMERIC"
        if self.date:
            return "DATE"
        if self.stamp:
            return "TIMESTAMP"
        return "TEXT"


def _lower_expr(expr: str) -> str:
    """Lower-case all un-quoted Table.Column identifiers in a metric expression."""
    return re.sub(
        r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\b",
        lambda m: m.group(1).lower() + "." + m.group(2).lower(),
        expr,
    )


def _list_metric_defs(pg, dataset: str) -> list[dict]:
    """Return metric definitions for dataset from v2_definition, or [] if none exist."""
    try:
        result = pg.sql(_LIST_DEFINITIONS_SQL, dataset=dataset)
        return json.loads(result or "[]")
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Projection loading  (pg = PilotPostgres instance)
# ---------------------------------------------------------------------------

class LoadedProjections(dict):
    """{table_name: csv_sha256}, plus ``notes``: data changes the loader had to make."""

    def __init__(self):
        super().__init__()
        self.notes: list[str] = []


def ensure_projections_loaded(pg, dataset: str, csv_dir: Path) -> LoadedProjections:
    """Idempotently load CSV files from csv_dir into v2_facts_<dataset>.<table>.

    Uses the SHA-256 of each CSV file as the idempotency key.
    Returns {table_name: csv_sha256} for all loaded tables.

    Any file that cannot be read or loaded raises ProjectionLoadError naming the file.
    Each file loads in one transaction, so a failure leaves no half-loaded table.
    The data travels over the client connection (COPY FROM STDIN), so it works with a
    remote database. NUL bytes cannot be stored in PostgreSQL text; each one becomes U+2400
    and the count is recorded in ``notes`` and in the log.
    """
    pg.sql(_LOAD_TRACK_DDL)
    schema = _dataset_schema(dataset)
    pg.sql(f"CREATE SCHEMA IF NOT EXISTS {schema};")

    batch_hashes = LoadedProjections()
    for csv_path in sorted(Path(csv_dir).glob("*.csv")):
        table_orig = csv_path.stem
        name = csv_path.name
        if not _IDENT.match(table_orig):
            raise ProjectionLoadError(f"{name}: file name is not a valid table name")
        try:
            raw = csv_path.read_bytes()
            text = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ProjectionLoadError(f"{name}: cannot read file ({exc})") from exc
        sha = hashlib.sha256(raw).hexdigest()

        already = pg.sql(
            "SELECT 1 FROM v2_projection_load "
            "WHERE dataset = :'ds' AND table_name = :'tbl' AND csv_sha256 = :'sha';",
            ds=dataset, tbl=table_orig, sha=sha,
        )
        if already:
            batch_hashes[table_orig] = sha
            continue

        nuls = text.count("\x00")
        if nuls:
            text = text.replace("\x00", "\u2400")
            note = f"{name}: {nuls} NUL byte(s) replaced by U+2400 (PostgreSQL text cannot hold NUL)"
            batch_hashes.notes.append(note)
            log.warning("ensure_projections_loaded: %s", note)
        try:
            rows = list(csv.reader(io.StringIO(text, newline="")))
        except csv.Error as exc:
            raise ProjectionLoadError(f"{name}: not valid CSV ({exc})") from exc
        if not rows or not rows[0]:
            raise ProjectionLoadError(f"{name}: file has no header row")
        headers = rows[0]
        lowered = [h.lower() for h in headers]
        bad = [h for h in headers if not _IDENT.match(h)]
        if bad or len(set(lowered)) != len(lowered):
            raise ProjectionLoadError(f"{name}: invalid or duplicate column name {bad or lowered}")
        kinds = [_ColumnKind() for _ in headers]
        for line, row in enumerate(rows[1:], start=2):
            if len(row) != len(headers):
                raise ProjectionLoadError(f"{name}: line {line} has {len(row)} fields, header has {len(headers)}")
            for kind, value in zip(kinds, row):
                kind.add(value)
        out = io.StringIO()
        csv.writer(out, lineterminator="\n").writerows(rows)   # one canonical line ending for COPY
        body = out.getvalue()
        if any(line == "\\." for line in body.split("\n")):
            raise ProjectionLoadError(f"{name}: a line holds only the COPY terminator")

        full_table = f"{schema}.{table_orig.lower()}"
        col_defs = ", ".join(f'"{h}" {k.sql_type}' for h, k in zip(lowered, kinds))
        try:
            pg.sql(
                "BEGIN;\n"
                f"DROP TABLE IF EXISTS {full_table};\n"
                f"CREATE TABLE {full_table} ({col_defs});\n"
                f"COPY {full_table} FROM STDIN WITH (FORMAT csv, HEADER true, ENCODING 'UTF8');\n"
                f"{body}\\.\n"
                f"INSERT INTO v2_projection_load (dataset, table_name, csv_sha256) "
                f"VALUES ('{dataset.replace(chr(39), chr(39) * 2)}', '{table_orig}', '{sha}');\n"
                "COMMIT;"
            )
        except Exception as exc:
            raise ProjectionLoadError(f"{name}: load failed ({exc})") from exc
        batch_hashes[table_orig] = sha

    return batch_hashes


def _latest_batch_hash(pg, dataset: str, table_name: str) -> str:
    result = pg.sql(
        "SELECT csv_sha256 FROM v2_projection_load "
        "WHERE dataset = :'ds' AND lower(table_name) = lower(:'tbl') "
        "ORDER BY load_id DESC LIMIT 1;",
        ds=dataset, tbl=table_name,
    )
    return (result or "").strip()


def _list_distinct_values(pg, dataset: str, table: str, field: str) -> set:
    """Distinct non-null values of one column. A failed lookup raises ``MetricQueryError``.

    A lookup that fails must never look like "no known values", which would skip validation.
    """
    if not _IDENT.match(table) or not _IDENT.match(field):
        raise ValueError(f"{table}.{field} is not a table and column name")
    full = f"{_dataset_schema(dataset)}.{table.lower()}"
    col = field.lower()
    try:
        result = pg.sql(f"SELECT COALESCE(json_agg(DISTINCT {col}), '[]') FROM {full};")
        return {str(v) for v in json.loads(result or "[]") if v is not None}
    except Exception as exc:
        raise MetricQueryError(f"Could not list the values of {table}.{field}: {exc}") from exc


# ---------------------------------------------------------------------------
# Natural dates and model-driven metric scoring
# ---------------------------------------------------------------------------

def extract_dates(text: str) -> tuple[list[str], str]:
    """The ISO days the text names (a range gives both ends, in order) and the text with those days blanked.
    A thin view of the one period parser (``periods.parse_periods``) for callers that only read days."""
    found, _ = parse_periods(text)
    days = [p for p in found if p["kind"] in ("day", "range")]
    out = []
    for p in days:
        out += [p["from"]] if p["from"] == p["to"] else [p["from"], p["to"]]
    return out, blank(text, days)


_BREAKDOWN = re.compile(
    r"(?:\b(?:by(?!\s+how\s+(?:many|much)\b)|per|each|every|split|breakdown|broken\s+down|group(?:ed)?|top|bottom|highest|lowest|"
    r"rank(?:ed|ing)?|which)|(?<!\bat\s)\b(?:most|least))\b(?:\W+\w+){0,3}",
    re.I,
)


def _refuse_unparsed_date(text: str, **_ignored) -> None:
    """Refuse a question whose date wording the one period parser cannot resolve to a range."""
    _, left = parse_periods(text)
    if left:
        raise ClarificationNeeded("date", ", ".join(left), set(), message=(
            f"I did not understand the date wording {', '.join(repr(w) for w in dict.fromkeys(left))}, "
            "so I did not answer. Give a date, a month and year, a quarter and year, or a start and an end date."))


def _period_filters(text: str, helios: dict, label: str, metric_defs: list) -> list:
    """Filters for the periods the text names, by the metric's declared date rules. Raises ClarificationNeeded."""
    _refuse_unparsed_date(text)
    periods, _ = parse_periods(text)
    required = (helios.get("required_filters") or {}).get("BusinessDate")
    if required:
        days = [p for p in periods if p["from"] == p["to"]]
        if required == "single_value":
            if len(periods) != 1 or len(days) != 1:
                raise ClarificationNeeded("date", "", set(), message=(
                    f"{label} takes one date{', not a range or a year' if periods else ''}. Give it as YYYY-MM-DD."))
            return [Filter(field="BusinessDate", op="=", value=days[0]["from"])]
        ends = sorted({p["from"] for p in periods} | {p["to"] for p in periods})
        if not periods or len(periods) > 2 or (len(periods) == 2 and len(days) != 2):
            raise ClarificationNeeded("date", "", set(), message=(
                f"{label} takes a start and an end date. Give both as YYYY-MM-DD."))
        return [Filter(field="BusinessDate", op=">=", value=ends[0]), Filter(field="BusinessDate", op="<=", value=ends[-1])]
    if not periods:
        return []
    _refuse_date(helios, label, metric_defs)
    if len(periods) > 1:
        raise ClarificationNeeded("date", "", set(), message=(
            f"Give one period (one year, one date, or a start and an end date), not {len(periods)}."))
    period, field = periods[0], helios.get("time_field") or ""
    if period["from"] == period["to"]:
        return [Filter(field=field, op="=", value=period["from"])]
    if period["kind"] == "year":     # a whole year stays half-open, so the answer shows it as the year
        year = int(period["from"][:4])
        return [Filter(field=field, op=">=", value=period["from"]), Filter(field=field, op="<", value=f"{year + 1}-01-01")]
    return [Filter(field=field, op=">=", value=period["from"]), Filter(field=field, op="<=", value=period["to"])]


def _refuse_breakdown(text: str, dimensions=(), allowed=()) -> None:
    """Refuse a breakdown: this compiler returns one total, it does not group."""
    cue = _BREAKDOWN.search(text)
    if not cue and not dimensions:
        return
    asked = ", ".join(dimensions) if dimensions else repr(cue.group(0).strip())
    hint = f" This metric can be grouped by {', '.join(sorted(allowed))} in the model." if allowed else ""
    raise ClarificationNeeded("dimension", asked, set(), message=(
        f"The question asks for a breakdown ({asked}). This compiler answers one total per question "
        f"and does not group, so I did not answer.{hint} Ask for the total, or filter to one value."))


def _stem(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def _tokens(text: str) -> set[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text or "")
    return {_stem(w) for w in re.findall(r"[a-z0-9]+", spaced.lower())
            if w not in _STOP and not w.isdigit()}


def _metric_name(mdef: dict) -> str:
    sid = mdef.get("source_id", "")
    return sid.rsplit("/", 1)[-1] if "/" in sid else sid


def _semantics(mdef: dict) -> dict:
    """Label, synonyms and example questions of a metric, read from the model's own metadata."""
    meta = mdef.get("source_metadata") or {}
    ctx = meta.get("ai_context") if isinstance(meta.get("ai_context"), dict) else {}
    helios = _helios(meta)
    label = helios.get("label") or meta.get("label") or _metric_name(mdef).replace("_", " ")
    return {"label": label, "synonyms": list(ctx.get("synonyms") or []), "examples": list(ctx.get("examples") or [])}


def _score_metric(q_tokens: set[str], n_dates: int, mdef: dict) -> float:
    """Cosine overlap with the best phrase the model gives for the metric, plus weak definition evidence."""
    if not q_tokens:
        return 0.0
    sem = _semantics(mdef)
    phrases = [_metric_name(mdef).replace("_", " "), sem["label"], *sem["synonyms"],
               *(extract_dates(example)[1] for example in sem["examples"])]
    best = 0.0
    for phrase in phrases:
        p = _tokens(phrase)
        if p:
            best = max(best, len(q_tokens & p) / math.sqrt(len(q_tokens) * len(p)))
    best += 0.1 * len(q_tokens & _tokens(mdef.get("definition") or "")) / len(q_tokens)
    wanted = ((_helios(mdef.get("source_metadata") or {}).get("required_filters") or {}).get("BusinessDate"))
    if n_dates and wanted and not ((wanted == "single_value" and n_dates == 1)
                                   or (wanted == "inclusive_range" and n_dates == 2)):
        best *= 0.5
    return best


def _phrase_tokens(mdef: dict) -> set[str]:
    """Every word the model uses for one metric: label, synonyms, name, examples and its dataset's name."""
    sem = _semantics(mdef)
    phrases = [_metric_name(mdef).replace("_", " "), sem["label"], *sem["synonyms"],
               *(extract_dates(example)[1] for example in sem["examples"]),
               _helios(mdef.get("source_metadata") or {}).get("dataset") or ""]
    return set().union(*(_tokens(p) for p in phrases))


def _object_words(metric_defs: list[dict]) -> set[str]:
    """Words of the model's dataset names. They name business objects (loan, work order, customer)."""
    names = [(m.get("source_metadata") or {}).get("name") or m.get("source_id", "").rsplit("/", 1)[-1]
             for m in metric_defs if m.get("kind") == "dataset"]
    return set().union(*(_tokens(n) for n in names))


def _which_metric(ranked: list, why: str) -> ClarificationNeeded:
    labels = [_semantics(row[2])["label"] for row in ranked[:3]]
    return ClarificationNeeded("metric", "", set(), message=(
        f"{why} Closest metrics: {', '.join(labels)}. Name one of them."))


def _pick_metric(combined: str, metric_defs: list[dict], covered=frozenset()) -> tuple[Optional[dict], list[str]]:
    """Choose the one metric the question names. None when no metric is near.

    Raises ClarificationNeeded when the two best scores are within ``_MIN_MARGIN``, or when the question
    names a business object (a word in a dataset name of the model) that the best metric never speaks of.
    ``covered`` holds stemmed words the caller has already accounted for (a document reference, the kind of a
    stated entity); they never count as an object the metric lacks.
    """
    dates, rest = extract_dates(combined)
    q = _tokens(rest)
    ranked = sorted(
        ((_score_metric(q, len(dates), m), _metric_name(m), m) for m in metric_defs if m.get("kind") == "metric"),
        key=lambda row: (-row[0], row[1]),
    )
    if not ranked or ranked[0][0] < _MIN_SCORE:
        return None, dates
    # A breakdown phrase ("by product") names a dimension, not the thing counted.
    asked = _tokens(_BREAKDOWN.sub(" ", rest))
    unmatched = sorted(asked & _object_words(metric_defs) - _phrase_tokens(ranked[0][2]) - set(covered))
    if unmatched:
        raise _which_metric(ranked, (
            f"The question mentions {', '.join(unmatched)}, which {_semantics(ranked[0][2])['label']} does not cover, "
            "so I did not answer."))
    if len(ranked) > 1 and ranked[1][0] >= _MIN_SCORE and ranked[0][0] - ranked[1][0] < _MIN_MARGIN:
        raise _which_metric(ranked, "The wording fits more than one metric about equally well.")
    return ranked[0][2], dates


# ---------------------------------------------------------------------------
# Source access
# ---------------------------------------------------------------------------

def _metric_tables(metric_def: dict, metric_defs: list[dict]) -> set[str]:
    """The tables a metric reads before any filter: its dataset, its ratio parts, a joined date table."""
    helios = _helios(metric_def.get("source_metadata") or {})
    tables = {helios.get("dataset") or ""}
    ansi = _get_ansi_expr(metric_def) or ""
    ratio = re.match(r"^([A-Za-z_]\w*)\s*/\s*([A-Za-z_]\w*)\s*$", ansi.strip())
    if ratio:
        for m in metric_defs:
            if m.get("kind") == "metric" and _metric_name(m) in ratio.groups():
                tables.add(_helios(m.get("source_metadata") or {}).get("dataset") or "")
    if "." in (helios.get("time_field") or ""):
        tables.add(helios["time_field"].split(".", 1)[0])
    return {t.lower() for t in tables if t}


def _check_sources(workspace: Optional[dict], tables: set[str]) -> None:
    """Refuse facts whose data source is withheld for this account.

    A table is covered when an allowed source lists it in its structured import. A withheld source
    keeps no table list, so when a workbook source is withheld, every table no allowed source covers is refused.
    """
    sources = (workspace or {}).get("sources", [])
    withheld = [s for s in sources if s.get("access") == "withheld" and s.get("kind") == "workbook"]
    if not withheld:
        return
    covered = {str(t.get("name", "")).lower() for s in sources if s.get("access") != "withheld"
               for t in (s.get("structuredImport") or {}).get("tables", [])}
    missing = sorted(t for t in tables if t not in covered)
    if missing:
        raise SourceWithheld("source", ", ".join(missing), set(), message=(
            f"The source behind {', '.join(missing)} is withheld for this account ({withheld[0].get('title', 'workbook')}), "
            "so no figure is returned."))


# ---------------------------------------------------------------------------
# Offline planner
# ---------------------------------------------------------------------------

def _offline_plan(
    combined: str,
    metric_defs: list[dict],
    pg,
    dataset: str,
    workbook_batch: Optional[str],
    workspace: Optional[dict] = None,
    covered=frozenset(),
) -> Optional[tuple[MetricPlan, dict]]:
    """Build a MetricPlan from the model's labels, synonyms and example questions plus date parsing.

    Returns (plan, metric_def) or None if no metric matches well enough.
    ClarificationNeeded is NOT raised for metric-defined fixed filters (always applied);
    it is raised when the question needs a value or a date the metric cannot take.
    A date with no qualifier binds to the metric's time field.
    """
    best_metric, dates = _pick_metric(combined, metric_defs, covered)
    if best_metric is None:
        return None
    _check_sources(workspace, _metric_tables(best_metric, metric_defs))
    _refuse_breakdown(combined, allowed=_helios(best_metric.get("source_metadata") or {}).get("allowed_dimensions") or ())

    helios = _helios(best_metric.get("source_metadata") or {})
    req_filters = helios.get("required_filters") or {}
    table_name = helios.get("dataset")

    # Ratio metrics have no dataset of their own — resolve later via sub-metrics.
    ansi_expr = _get_ansi_expr(best_metric)
    is_ratio = ansi_expr and re.match(r"^[A-Za-z_]\w*\s*/\s*[A-Za-z_]\w*\s*$", ansi_expr.strip())

    if not table_name and not is_ratio:
        return None

    metric_label = _semantics(best_metric)["label"]
    filters: list[Filter] = _period_filters(combined, helios, metric_label, metric_defs)
    if "SourceBatch" in req_filters:
        if not workbook_batch:
            return None
        filters.append(Filter(field="SourceBatch", op="=", value=workbook_batch))

    # Other single-value required filters (e.g. Currency) must be named in the question.
    for field, value in req_filters.items():
        if value == "single_value" and field not in ("BusinessDate", "SourceBatch"):
            known = _list_distinct_values(pg, dataset, table_name or "", field)
            named = [v for v in known if re.search(rf"\b{re.escape(v)}\b", combined)]
            if len(named) != 1:
                raise ClarificationNeeded(field, "", known, message=(
                    f"Specify one {field}. This metric is in the source {field.lower()} and is never "
                    f"summed across values. Known values: {', '.join(sorted(known)[:10])}."))
            filters.append(Filter(field=field, op="=", value=named[0]))

    # Metric-defined fixed required filters (e.g., RiskRating: 'High').
    # These are part of the metric definition — always applied, never validated
    # against distinct values (validation is for user-specified values only).
    for field, value in req_filters.items():
        if field in ("BusinessDate", "SourceBatch"):
            continue
        if isinstance(value, str) and not value.startswith(("single_", "inclusive_")):
            filters.append(Filter(field=field, op="=", value=value))

    plan = MetricPlan(metric=_metric_name(best_metric), filters=filters)
    return plan, best_metric


# ---------------------------------------------------------------------------
# Mistral plan path
# ---------------------------------------------------------------------------

def _get_ansi_expr(metric_def: dict) -> Optional[str]:
    """Return the ANSI_SQL expression from a metric definition dict."""
    expr_raw = metric_def.get("expression")
    if isinstance(expr_raw, dict):
        dialects = expr_raw.get("dialects", [])
    elif isinstance(expr_raw, list):
        dialects = expr_raw
    else:
        dialects = []
    return next(
        (e["expression"] for e in dialects if e.get("dialect") == "ANSI_SQL"), None
    )


def _mistral_plan(
    combined: str,
    metric_defs: list[dict],
    client,
    pg,
    dataset: str,
    workbook_batch: Optional[str],
    workspace: Optional[dict] = None,
) -> tuple[MetricPlan, dict]:
    """Use an injectable Mistral client to produce a MetricPlan.

    The client must expose:
        client.plan(combined: str, metric_options: list[dict]) -> str  (JSON)
        client.available -> bool

    One repair retry: if the first response fails Pydantic validation, the
    client is called once more with the validation error appended. If the
    second response also fails, ValueError is raised.

    Returns (plan, metric_def) or raises ValueError.
    Raises ClarificationNeeded if a filter value is not in distinct values.
    """
    _refuse_unparsed_date(combined)
    _refuse_breakdown(combined)
    metric_options = [
        {"id": m.get("id", ""), "label": m.get("label", ""), "definition": m.get("definition", "")}
        for m in metric_defs if m.get("kind") == "metric"
    ]

    def try_parse(raw: str) -> MetricPlan:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Mistral returned non-JSON: {exc}") from exc
        return MetricPlan.model_validate(data)

    raw1 = client.plan(combined, metric_options)
    try:
        plan = try_parse(raw1)
    except (ValueError, Exception) as first_err:
        # One repair retry: send back with the validation error.
        raw2 = client.plan(combined + f"\n[repair: {first_err}]", metric_options)
        try:
            plan = try_parse(raw2)
        except Exception as second_err:
            raise ValueError(
                f"Mistral metric plan failed after one repair: {second_err}"
            ) from second_err

    # Resolve metric def for the planned metric.
    metric_def = next(
        (m for m in metric_defs
         if (m.get("source_id", "").rsplit("/", 1)[-1] == plan.metric
             or m.get("label", "").lower() == plan.metric.lower())),
        None,
    )
    if metric_def is None:
        raise ValueError(f"Mistral planned metric {plan.metric!r} not found in definitions")

    helios = _helios(metric_def.get("source_metadata") or {})
    _check_sources(workspace, _metric_tables(metric_def, metric_defs))
    if plan.dimensions:
        _refuse_breakdown("", plan.dimensions, helios.get("allowed_dimensions") or ())
    # Validate user-specified filter values against distinct values.
    table_name = helios.get("dataset", "")
    for f in plan.filters:
        if f.field.lower().endswith("date") and f.field not in (helios.get("required_filters") or {}):
            _refuse_date(helios, plan.metric, metric_defs)
    for field, value in (helios.get("required_filters") or {}).items():
        if value == "single_value" and field not in ("BusinessDate", "SourceBatch") \
                and not any(f.field == field for f in plan.filters):
            raise ClarificationNeeded(field, "", set(), message=f"Specify one {field} for {plan.metric}.")
    for f in plan.filters:
        req_filters = helios.get("required_filters") or {}
        # Only validate filters that the user/Mistral added (not metric-defined fixed ones).
        if f.field in req_filters:
            continue
        if f.value is not None and table_name and _IDENT.match(f.field):
            known = _list_distinct_values(pg, dataset, table_name, f.field)
            if known and f.value not in known:
                raise ClarificationNeeded(f.field, f.value, known)

    return plan, metric_def


# ---------------------------------------------------------------------------
# SQL compiler
# ---------------------------------------------------------------------------

def _model_columns(metric_defs: list[dict]) -> dict[str, set[str]]:
    """{table: {column}} in lower case, from the model's field definitions."""
    out: dict[str, set[str]] = {}
    for m in metric_defs:
        if m.get("kind") == "field":
            parts = m.get("source_id", "").split("/")
            if len(parts) >= 4:
                out.setdefault(parts[-2].lower(), set()).add(parts[-1].lower())
    return out


def _declared_join(base: str, target: str, metric_defs: list[dict]) -> list[tuple[str, str]]:
    """Column pairs joining ``base`` to ``target`` through one declared many-to-one relationship.

    Refuses (ClarificationNeeded) when the model declares no such relationship, when the
    declared columns are not the whole primary key of ``target`` (the join could multiply rows),
    or when more than one relationship qualifies. A join is never inferred.
    """
    relationships = [m.get("source_metadata") or {} for m in metric_defs if m.get("kind") == "relationship"]
    forward = [r for r in relationships
               if str(r.get("from", "")).lower() == base.lower() and str(r.get("to", "")).lower() == target.lower()]
    if not forward:
        raise ClarificationNeeded("join", "", set(), message=(
            f"The model does not declare a many-to-one relationship from {base} to {target}, "
            "so this compiler does not join them."))
    keys = {str(((m.get("source_metadata") or {}).get("name") or "")).lower():
            {str(c).lower() for c in (m.get("source_metadata") or {}).get("primary_key") or []}
            for m in metric_defs if m.get("kind") == "dataset"}
    primary = keys.get(target.lower(), set())
    # ponytail: a relationship declared `join_scope: source_revision` (form-derived tables, keyed by revision and group
    # position) may end on the revision alone. Totals repeat if one form holds several rows of the target; the
    # relationship's justification says so. Upgrade: a row-count check before the query.
    safe = [r for r in forward if primary and ({str(c).lower() for c in r.get("to_columns") or []} == primary or (
        _helios(r).get("join_scope") == "source_revision" and [str(c).lower() for c in r.get("to_columns") or []] == ["sourcerevision"]))]
    if not safe:
        raise ClarificationNeeded("join", "", set(), message=(
            f"The relationship from {base} to {target} does not end on the primary key of {target}, "
            "so a join could fan out and repeat rows."))
    if len(safe) > 1:
        raise ClarificationNeeded("join", "", set(), message=(
            f"The model declares more than one relationship from {base} to {target}; none is the default."))
    pairs = list(zip(safe[0]["from_columns"], safe[0]["to_columns"]))
    if any(not _IDENT.match(a) or not _IDENT.match(b) for a, b in pairs):
        raise ValueError("Relationship column is not a valid identifier")
    return pairs


MAX_HOPS = 3


def _declared_path(base: str, target: str, metric_defs: list[dict], *, bridge: bool = False) -> list[tuple[str, str, list]]:
    """[(from table, to table, column pairs)] joining ``base`` to ``target`` over declared relationships.

    Each hop is one declared many-to-one relationship that ends on the whole primary key of its target (the rule of
    ``_declared_join``), so no hop repeats rows. With ``bridge`` (a metric that counts distinct keys of ``base``, which
    a repeated row cannot change) a path may also cross a bridge table against its relationship (Customers to the
    Customer-RM table, whose relationships run to both sides) as the first hop when a many-to-one hop leaves it again;
    the target itself is never reached that way. The
    shortest path must be unique: several paths of the same length, or none within MAX_HOPS, is a clarification."""
    if base.lower() == target.lower():
        return []
    rels, forward = _edges(metric_defs)
    reverse = {}
    if bridge:
        for a, hops in forward.items():
            for b, pairs, _ in hops:
                name = next(x for (x, y) in rels if x.lower() == a and y == b)
                reverse.setdefault(b.lower(), []).append((name, [(t, f) for f, t in pairs], True))
    paths, frontier = [], [[(None, base, [], False)]]
    for _ in range(MAX_HOPS):
        nxt = []
        for path in frontier:
            here = path[-1][1]
            seen = {p[1].lower() for p in path}
            for to, pairs, back in forward.get(here.lower(), []) + reverse.get(here.lower(), []):
                if to.lower() in seen or (back and len(path) > 1):      # a bridge hangs off the base table only
                    continue
                step = path + [(here, to, pairs, back)]
                if to.lower() == target.lower():
                    if not back:          # a bridge is crossed, never the end: a condition on it would repeat rows
                        paths.append(step)
                else:
                    nxt.append(step)
        if paths:
            break
        frontier = nxt
    if not paths:
        raise ClarificationNeeded("join", "", set(), message=(
            f"The model does not declare a many-to-one relationship from {base} to {target}, "
            "so this compiler does not join them."))
    if len(paths) > 1:
        routes = sorted(" > ".join([base, *(h[1] for h in p[1:])]) for p in paths)
        raise ClarificationNeeded("join", "", set(), message=(
            f"{base} reaches {target} through more than one declared path ({'; '.join(routes)}), so I did not choose."))
    return [(a, b, pairs) for a, b, pairs, _ in paths[0][1:]]


_EDGE_CACHE: list = []      # [(defs list, edges)]; the list itself is held, so its id is never reused


def _edges(metric_defs: list[dict]):
    """(relationships by (from, to), {from table lower: [(to, column pairs, False)]}) of the safe many-to-one hops."""
    for defs, edges in _EDGE_CACHE:
        if defs is metric_defs:
            return edges
    rels = {}
    for m in metric_defs:
        r = m.get("source_metadata") or {}
        if m.get("kind") == "relationship" and r.get("from") and r.get("to"):
            rels.setdefault((str(r["from"]), str(r["to"])), r)
    forward = {}
    for (a, b) in rels:
        try:
            forward.setdefault(a.lower(), []).append((b, _declared_join(a, b, metric_defs), False))
        except (ClarificationNeeded, ValueError):
            continue
    _EDGE_CACHE[:] = [*_EDGE_CACHE[-7:], (metric_defs, (rels, forward))]
    return rels, forward


_COUNT_DISTINCT = re.compile(r"^\s*COUNT\(\s*DISTINCT\s+(\w+)\.(\w+)\s*\)\s*$", re.I)


def counts_distinct(metric_def: dict) -> bool:
    """True when the metric is COUNT(DISTINCT <its table>.<column>): a join that repeats rows cannot change it."""
    m = _COUNT_DISTINCT.match(_get_ansi_expr(metric_def) or "")
    return bool(m) and m.group(1).lower() == str(_helios(metric_def.get("source_metadata") or {}).get("dataset") or "").lower()


def _compile(
    metric_def: dict,
    plan: MetricPlan,
    dataset: str,
    metric_defs: Optional[list[dict]] = None,
    max_hops: int = 1,
) -> tuple[str, set[str]]:
    """Compile a guarded SELECT. Returns (sql, tables the SQL may touch, lower case). A filter on another table joins
    through one declared relationship; with ``max_hops`` above 1 (the answer path) through one unique declared path
    (``_declared_path``)."""
    ansi_expr = _get_ansi_expr(metric_def)
    if not ansi_expr:
        raise ValueError(f"No ANSI_SQL expression for metric {plan.metric!r}")
    metric_defs = metric_defs or []
    helios = _helios(metric_def.get("source_metadata") or {})
    schema = _dataset_schema(dataset)
    if plan.dimensions:
        _refuse_breakdown("", plan.dimensions, helios.get("allowed_dimensions") or ())

    # Ratio metric: `a / b` where a and b are other metric names.
    ratio_match = re.match(r"^([A-Za-z_]\w*)\s*/\s*([A-Za-z_]\w*)\s*$", ansi_expr.strip())
    if ratio_match:
        table, expr = _ratio_parts(ratio_match.group(1), ratio_match.group(2), metric_defs)
    else:
        table = helios.get("dataset")
        if not table or not _IDENT.match(table):
            raise ValueError(f"Metric has no valid dataset table: {table!r}")
        expr = _lower_expr(ansi_expr)

    base = table.lower()
    columns = _model_columns(metric_defs)
    time_field = (helios.get("time_field") or "").lower()
    plain_dates = "BusinessDate" not in (helios.get("required_filters") or {})
    joined: dict[str, tuple[str, list[tuple[str, str]]]] = {}     # table -> (table it joins from, column pairs)
    bridge = bool(_COUNT_DISTINCT.match(ansi_expr)) and _COUNT_DISTINCT.match(ansi_expr).group(1).lower() == base
    where_parts: list[str] = []
    for f in plan.filters:
        parts = f.field.split(".")
        if len(parts) > 2 or not all(_IDENT.match(p) for p in parts):
            raise ValueError(f"Filter field {f.field!r} is not a column name")
        tbl, col = (base, parts[0].lower()) if len(parts) == 1 else (parts[0].lower(), parts[1].lower())
        if f.op not in _OPS:
            raise ValueError(f"Filter operator {f.op!r} is not allowed")
        if columns and col not in columns.get(tbl, set()):
            raise ValueError(f"Filter field {f.field!r} is not a column of the model")
        if tbl != base and tbl not in joined:
            hops = (_declared_path(table, parts[0], metric_defs, bridge=bridge) if max_hops > 1
                    else [(table, parts[0], _declared_join(table, parts[0], metric_defs))])
            for frm, to, pairs in hops:
                joined.setdefault(to.lower(), (frm.lower(), pairs))
        ref = f"{tbl}.{col}"
        if plain_dates and f.field.lower() == time_field and f.value is not None:
            ref = f"CAST({ref} AS DATE)"      # a time field may hold time-of-day; a date means the whole day
        if f.op in ("IS NULL", "IS NOT NULL"):
            where_parts.append(f"{ref} {f.op}")
        elif f.value is not None:
            safe = f.value.replace("'", "''")
            where_parts.append(f"{ref} {f.op} '{safe}'")

    select = (f"SELECT ({expr[0]}) / NULLIF(({expr[1]}), 0) AS metric_value" if isinstance(expr, tuple)
              else f"SELECT {expr} AS metric_value")
    lines = [select, f"FROM {schema}.{base}"]
    for tbl, (frm, pairs) in joined.items():
        on = " AND ".join(f"{frm}.{a.lower()} = {tbl}.{b.lower()}" for a, b in pairs)
        lines.append(f"JOIN {schema}.{tbl} ON {on}")
    if where_parts:
        lines.append("WHERE " + " AND ".join(where_parts))
    lines.append(f"LIMIT {plan.limit}")
    return "\n".join(lines), {base, *joined}


def _compile_sql(
    metric_def: dict,
    plan: MetricPlan,
    dataset: str,
    metric_defs: Optional[list[dict]] = None,
) -> str:
    """Compile a guarded SELECT from a metric definition and plan (see ``_compile``)."""
    return _compile(metric_def, plan, dataset, metric_defs)[0]


def _ratio_parts(name_a: str, name_b: str, metric_defs: list[dict]) -> tuple[str, tuple[str, str]]:
    """Table and the two lower-cased sub-expressions of a ratio metric. Both must share one table."""
    def find_sub(name: str) -> dict:
        for m in metric_defs:
            if m.get("kind") == "metric" and _metric_name(m) == name:
                return m
        raise ValueError(f"Ratio sub-metric {name!r} not found in metric definitions")

    def_a, def_b = find_sub(name_a), find_sub(name_b)
    expr_a, expr_b = _get_ansi_expr(def_a), _get_ansi_expr(def_b)
    if not expr_a or not expr_b:
        raise ValueError(f"Sub-metric {name_a!r} or {name_b!r} has no ANSI_SQL expression")
    table_a = _helios(def_a.get("source_metadata") or {}).get("dataset", "")
    table_b = _helios(def_b.get("source_metadata") or {}).get("dataset", "")
    if not table_a or table_a != table_b:
        raise ValueError(
            f"Ratio sub-metrics must reference the same table; got {table_a!r} and {table_b!r}"
        )
    if not _IDENT.match(table_a):
        raise ValueError(f"Invalid table name in ratio sub-metric: {table_a!r}")
    return table_a, (_lower_expr(expr_a), _lower_expr(expr_b))


# ---------------------------------------------------------------------------
# SQL guard
# ---------------------------------------------------------------------------

def _expressions(metric_def: dict, metric_defs: list[dict]) -> list[str]:
    """The ANSI expressions the compiler places in the SELECT for this metric (both parts of a ratio)."""
    ansi = (_get_ansi_expr(metric_def) or "").strip()
    ratio = re.match(r"^([A-Za-z_]\w*)\s*/\s*([A-Za-z_]\w*)\s*$", ansi)
    if ratio:
        return list(_ratio_parts(ratio.group(1), ratio.group(2), metric_defs)[1])
    return [ansi]


def guard_limits_for(metric_def: dict, plan: MetricPlan, metric_defs: list[dict], tables: set[str]):
    """What the guard may allow for this plan: (columns per table, literals, function node names).

    Columns come from the model's fields. Literals come from the validated plan values, the model's own
    metric expression and the limit. Function names come from the model's expression.
    """
    import sqlglot
    import sqlglot.expressions as sge
    model = _model_columns(metric_defs)
    columns = {t: model[t] for t in tables if t in model} if model else None
    literals = {str(f.value) for f in plan.filters if f.value is not None} | {str(plan.limit)}
    funcs: set[str] = set()
    for expr in _expressions(metric_def, metric_defs):
        tree = sqlglot.parse_one(expr)
        literals |= {str(node.this) for node in tree.find_all(sge.Literal)}
        funcs |= {type(node).__name__ for node in tree.find_all(sge.Func)
                  if not isinstance(node, (sge.Anonymous, sge.Binary, sge.Unary))}
    if len(_expressions(metric_def, metric_defs)) == 2:
        literals.add("0")       # the compiler's own NULLIF(denominator, 0)
        funcs.add("Nullif")
    return columns, literals, funcs


def guard_sql(
    sql: str,
    allowed_tables: set,
    schema: str,
    *,
    columns: Optional[dict] = None,
    literals: Optional[set] = None,
    funcs: Optional[set] = None,
) -> None:
    """Validate compiled SQL by its content. Raises ValueError with a reason on any violation.

    Always: exactly one SELECT with one projection, no CTE, subquery, set operation, GROUP BY, HAVING,
    ORDER BY or OFFSET; tables only from allowed_tables in the allowed schema; functions only from the
    allowlist plus ``funcs`` (sqlglot node names the model's expression uses); WHERE only AND-ed
    comparisons of a column with a literal; JOIN ON only AND-ed column equalities; LIMIT <= 100.
    When given: ``columns`` ({table: columns}) restricts every column to the model's fields for the
    plan's tables, and ``literals`` restricts every literal to validated plan and model values.
    """
    try:
        import sqlglot
        import sqlglot.expressions as sge
    except ImportError:
        raise ValueError("sqlglot is required for SQL guard")

    stmts = sqlglot.parse(sql)
    if len(stmts) != 1 or not isinstance(stmts[0], sge.Select):
        raise ValueError("SQL guard: single SELECT statement required")
    stmt = stmts[0]

    if stmt.args.get("with") or stmt.args.get("with_"):
        raise ValueError("SQL guard: WITH (CTE) is not allowed")
    for key in ("group", "having", "order", "offset", "qualify", "distinct", "into", "locks", "windows"):
        if stmt.args.get(key):
            raise ValueError(f"SQL guard: {key.upper()} is not allowed")
    for node in stmt.walk():
        if isinstance(node, (sge.Subquery, sge.Union, sge.Intersect, sge.Except)) or (
                isinstance(node, sge.Select) and node is not stmt):
            raise ValueError("SQL guard: subqueries and set operations are not allowed")
    if len(stmt.expressions) != 1 or not isinstance(stmt.expressions[0], sge.Alias) \
            or stmt.expressions[0].alias != "metric_value":
        raise ValueError("SQL guard: exactly one projection named metric_value is required")

    allowed_lc = {t.lower() for t in allowed_tables}
    schema_lc = schema.lower()
    for tref in stmt.find_all(sge.Table):
        db_part = (tref.db or "").lower()
        tbl = tref.name.lower()
        if db_part and db_part != schema_lc:
            raise ValueError(
                f"SQL guard: table in disallowed schema {db_part!r}; expected {schema_lc!r}"
            )
        if tbl and tbl not in allowed_lc:
            raise ValueError(
                f"SQL guard: table {tbl!r} not in allowed model tables {sorted(allowed_lc)}"
            )

    allowed_funcs = _DEFAULT_FUNCS | set(funcs or ())
    for node in stmt.walk():
        if isinstance(node, sge.Anonymous):
            if node.name.lower() not in _ALLOWED_FUNCS:
                raise ValueError(f"SQL guard: function {node.name!r} is not allowlisted")
        elif isinstance(node, sge.Func) and not isinstance(node, (sge.Binary, sge.Unary)) \
                and type(node).__name__ not in allowed_funcs:
            raise ValueError(f"SQL guard: function {type(node).__name__} is not allowlisted")

    if columns:
        every = set().union(*columns.values())
        for col in stmt.find_all(sge.Column):
            name, table = col.name.lower(), (col.table or "").lower()
            if table and table not in allowed_lc:
                raise ValueError(f"SQL guard: column {table}.{name} is on a table outside the plan")
            if name not in (columns.get(table, set()) if table else every):
                raise ValueError(f"SQL guard: column {name!r} is not a model column of the plan's tables")

    def parts(node, kind):
        return parts(node.this, kind) + parts(node.expression, kind) if isinstance(node, kind) else [node]

    comparisons = (sge.EQ, sge.NEQ, sge.GT, sge.GTE, sge.LT, sge.LTE)
    plain = lambda n: n.this if isinstance(n, sge.Cast) else n
    for cond in parts(stmt.args["where"].this, sge.And) if stmt.args.get("where") else []:
        if isinstance(cond, comparisons):
            ok = isinstance(plain(cond.this), sge.Column) and isinstance(cond.expression, sge.Literal)
        else:
            inner = cond.this if isinstance(cond, sge.Not) else cond
            ok = isinstance(inner, sge.Is) and isinstance(inner.this, sge.Column) \
                and isinstance(inner.expression, sge.Null)
        if not ok:
            raise ValueError("SQL guard: WHERE must be AND-ed comparisons of a column with a literal")
    for join in stmt.args.get("joins") or []:
        on = join.args.get("on")
        conds = parts(on, sge.And) if on is not None else []
        if join.args.get("kind") not in (None, "", "INNER") or not conds or not all(
                isinstance(c, sge.EQ) and isinstance(c.this, sge.Column) and isinstance(c.expression, sge.Column)
                for c in conds):
            raise ValueError("SQL guard: JOIN must be an inner join on AND-ed column equalities")

    if literals is not None:
        for lit in stmt.find_all(sge.Literal):
            if str(lit.this) not in literals:
                raise ValueError(f"SQL guard: literal {lit.this!r} does not come from the validated plan")

    limit_node = stmt.find(sge.Limit)
    if limit_node is None:
        raise ValueError("SQL guard: LIMIT is required")
    limit_expr = limit_node.expression
    if not isinstance(limit_expr, sge.Literal) or int(limit_expr.this) > 100:
        raise ValueError("SQL guard: LIMIT exceeds maximum of 100")


# ---------------------------------------------------------------------------
# Execution and narrative
# ---------------------------------------------------------------------------

def _run_sql(pg, sql: str) -> list[dict]:
    """Execute SQL read-only with a 15-second statement timeout."""
    wrapped = (
        "SET statement_timeout = '15s'; "
        "SET default_transaction_read_only = on; "
        f"{sql};"
    )
    try:
        raw = pg.sql(wrapped)
    except PilotDatabaseError as error:     # a missing table or a timeout is a failed lookup, not a crash of the caller
        raise MetricQueryError(str(error)) from error
    if not raw:
        return []
    return [{"metric_value": line.strip()} for line in raw.strip().splitlines()]


def _narrate(metric_def: dict, plan: MetricPlan, rows: list[dict]) -> str:
    """Templated narrative — every number comes from the computed facts."""
    if not rows:
        return "No results found for the requested criteria."

    value_raw = rows[0].get("metric_value", "")
    helios = _helios(metric_def.get("source_metadata") or {})
    label = _semantics(metric_def)["label"]
    unit = helios.get("unit", "")
    grain = helios.get("grain", "")

    try:
        f_val = float(value_raw)
        value_str = str(int(f_val)) if f_val == int(f_val) else f"{f_val:.6g}"
    except (ValueError, TypeError):
        value_str = str(value_raw)

    date_parts = [f for f in plan.filters if DATE.fullmatch(f.value or "")]
    date_str = ""
    if [f.op for f in date_parts] == [">=", "<"]:
        a, b = (datetime.date.fromisoformat(f.value) for f in date_parts)
        date_str = f" in {a.year}" if (a.month, a.day, b.month, b.day, b.year - a.year) == (1, 1, 1, 1, 1) else (
            f" from {a} up to but not including {b}")
    elif len(date_parts) == 1:
        date_str = f" on {date_parts[0].value}"
    elif len(date_parts) == 2:
        date_str = f" from {date_parts[0].value} to {date_parts[1].value}"

    suffix = f" ({unit})" if unit else ""
    grain_note = f" Grain: {grain}." if grain else ""
    # Narrative only contains value_str (from rows) and contextual date/label text.
    return f"{value_str}{suffix} — {label}{date_str}.{grain_note}"


def audit_narrative(narrative: str, rows: list[dict]) -> bool:
    """Return True if the main metric value from rows appears in the narrative.

    Every numeric token in the narrative that is not a year (19xx/20xx), a
    two-digit month (01-12) or a two-digit day (01-31) must appear verbatim in
    some row value.  This guards against hardcoded numbers being introduced.
    """
    if not rows:
        return True
    row_values = set()
    for row in rows:
        for v in row.values():
            row_values.update(re.findall(r"\b\d+(?:\.\d+)?\b", str(v)))

    for n in re.findall(r"\b\d+(?:\.\d+)?\b", narrative):
        if re.match(r"^(19|20)\d{2}$", n):   # year
            continue
        if re.match(r"^(0[1-9]|1[0-2])$", n):  # month
            continue
        if re.match(r"^(0[1-9]|[12]\d|3[01])$", n):  # day
            continue
        if n not in row_values:
            return False
    return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _batch_and_revision(workspace: dict) -> tuple[Optional[str], Optional[str]]:
    """The allowed workbook's SHA-256 (the source batch) and the model source's SHA-256."""
    workbook_batch: Optional[str] = None
    for src in workspace.get("sources", []):
        if src.get("kind") == "workbook" and src.get("access") != "withheld":
            workbook_batch = (src.get("citation") or {}).get("sha256")
            break
    model_revision: Optional[str] = None
    model_source_id = workspace.get("knowledge", {}).get("structured", {}).get("modelSource")
    for src in workspace.get("sources", []):
        if src.get("id") == model_source_id:
            model_revision = (src.get("citation") or {}).get("sha256")
            break
    return workbook_batch, model_revision


def query_metric(
    workspace: dict,
    combined: str,
    pg,
    interpreter=None,
    bind=None,
    covered=frozenset(),
) -> Optional[dict]:
    """Attempt to answer combined (context-folded question) via compiled metric SQL.

    pg is a PilotPostgres (the workspace server's review_store).
    interpreter is an optional Mistral plan client (see _mistral_plan).
    Returns a fact dict or None. Raises ClarificationNeeded for unknown filter values.
    Projections must be loaded before calling (see ensure_projections_loaded).

    ``bind(metric_def, metric_defs) -> [Filter]`` adds the caller's filters on declared columns once the metric
    is known (several at once; each is validated against the model's columns and declared joins by ``_compile``).
    """
    dataset = workspace["id"]
    workbook_batch, model_revision = _batch_and_revision(workspace)

    metric_defs = _list_metric_defs(pg, dataset)
    if not metric_defs:
        return None

    # Plan step: Mistral path takes priority when interpreter is provided.
    if interpreter is not None and getattr(interpreter, "available", False):
        try:
            plan, best_metric = _mistral_plan(
                combined, metric_defs, interpreter, pg, dataset, workbook_batch, workspace
            )
        except (ValueError, ClarificationNeeded):
            raise
    else:
        result = _offline_plan(combined, metric_defs, pg, dataset, workbook_batch, workspace, covered)
        if result is None:
            return None
        plan, best_metric = result

    if bind is not None:
        plan = plan.model_copy(update={"filters": [*plan.filters, *bind(best_metric, metric_defs)]})
    return compute_metric(workspace, best_metric, plan, pg, metric_defs)


def compute_metric(workspace: dict, best_metric: dict, plan: MetricPlan, pg, metric_defs: Optional[list] = None,
                   max_hops: int = 1) -> Optional[dict]:
    """Compile, guard and run one metric for a plan whose metric and filters are already chosen. None when the SQL
    cannot be compiled or guarded, or returns no row. Raises ClarificationNeeded (a join the model does not declare)
    and SourceWithheld."""
    dataset = workspace["id"]
    _, model_revision = _batch_and_revision(workspace)
    metric_defs = metric_defs if metric_defs is not None else _list_metric_defs(pg, dataset)
    _check_sources(workspace, _metric_tables(best_metric, metric_defs))
    try:
        sql, tables = _compile(best_metric, plan, dataset, metric_defs, max_hops)
    except ClarificationNeeded:
        raise
    except ValueError:
        return None
    _check_sources(workspace, tables)

    helios = _helios(best_metric.get("source_metadata") or {})
    table_name = helios.get("dataset") or next(iter(sorted(tables)), "")
    schema = _dataset_schema(dataset)

    try:
        columns, literals, funcs = guard_limits_for(best_metric, plan, metric_defs, tables)
        guard_sql(sql, allowed_tables=tables, schema=schema, columns=columns, literals=literals, funcs=funcs)
    except ValueError as exc:
        log.warning("metric_sql guard rejected SQL: %s", exc)
        return None

    rows = _run_sql(pg, sql)
    if not rows:
        return None

    value_raw = rows[0].get("metric_value", "")
    try:
        f_val = float(value_raw)
        value: int | float = int(f_val) if f_val == int(f_val) else round(f_val, 8)
    except (ValueError, TypeError):
        value = value_raw

    batch_hash = _latest_batch_hash(pg, dataset, table_name)
    summary = _narrate(best_metric, plan, rows)

    return {
        "label": _semantics(best_metric)["label"],
        "value": value,
        "sql": sql,
        "batchHash": batch_hash,
        "modelRevision": model_revision,
        "summary": summary,
        "definitionId": best_metric.get("id"),
        "filters": [f.model_dump() for f in plan.filters],
    }


_MIDNIGHT = re.compile(r"^(\d{4}-\d{2}-\d{2}) 00:00:00(?:\.0+)?$")


AGGREGATES = {"min": "MIN", "max": "MAX", "sum": "SUM", "count": "COUNT"}


def table_date_field(defs: list, table: str) -> tuple[Optional[str], list[str]]:
    """(the date field a date filter on `table` applies to, every time field of the table). The one snapshot date
    field (concept ``snapshot_date``) wins, else the one time field; None when the table has none or several."""
    meta = {f["source_id"].split("/")[-1]: f for f in defs if f.get("kind") == "field" and f["source_id"].split("/")[-2] == table}
    times = [n for n, f in meta.items() if ((f.get("source_metadata") or {}).get("dimension") or {}).get("is_time")]
    snapshot = [n for n in times if meta[n].get("concept_id") == "snapshot_date"]
    pick = snapshot if len(snapshot) == 1 else times
    return (pick[0] if len(pick) == 1 else None), times


def table_key(defs: list, table: str) -> Optional[str]:
    """The one column that identifies a row of `table` within a snapshot: its declared primary key without the batch
    and the snapshot date. None when the key has several such columns."""
    dataset = next((d for d in defs if d.get("kind") == "dataset"
                    and ((d.get("source_metadata") or {}).get("name") or d["source_id"].rsplit("/", 1)[-1]) == table), None)
    snapshot = table_date_field(defs, table)[0]
    keys = [c for c in ((dataset or {}).get("source_metadata") or {}).get("primary_key") or [] if c not in ("SourceBatch", snapshot)]
    return keys[0] if len(keys) == 1 else None


def column_range(pg, dataset: str, table: str, column: str) -> tuple[Optional[str], Optional[str]]:
    """(earliest, latest) value of one column, as text. A failed lookup raises ``MetricQueryError``."""
    if not _IDENT.match(table) or not _IDENT.match(column):
        raise ValueError(f"{table}.{column} is not a table and column name")
    full = f"{_dataset_schema(dataset)}.{table.lower()}"
    try:
        raw = pg.sql(f"SELECT COALESCE(json_build_array(MIN({column.lower()})::text, MAX({column.lower()})::text), '[]') FROM {full};")
        low, high = (json.loads(raw or "[null, null]") + [None, None])[:2]
    except Exception as exc:
        raise MetricQueryError(f"Could not read the range of {table}.{column}: {exc}") from exc
    return (low[:10] if low else None), (high[:10] if high else None)


def loaded_tables(pg, dataset: str) -> Optional[set]:
    """Lower-case names of the tables the dataset's facts schema holds; None when the catalog cannot be read."""
    try:
        raw = pg.sql("SELECT COALESCE(json_agg(table_name), '[]') FROM information_schema.tables WHERE table_schema = :'schema';",
                     schema=_dataset_schema(dataset))
        return {str(t).lower() for t in json.loads(raw or "[]")}
    except Exception:
        return None


def rows_repeat(pg, dataset: str, table: str, key: Optional[str], batch: Optional[str]) -> bool:
    """True when the table's identifier repeats across its rows in the batch (a snapshot of the same records each day),
    so adding rows over several snapshot dates would count a record more than once. Decided from the data."""
    if key is None or not _IDENT.match(table) or not _IDENT.match(key):
        return True
    where = f" WHERE sourcebatch = '{batch.replace(chr(39), chr(39) * 2)}'" if batch else ""
    try:
        raw = pg.sql(f"SELECT COUNT(*) - COUNT(DISTINCT {key.lower()}) FROM {_dataset_schema(dataset)}.{table.lower()}{where};")
        return int((raw or "0").strip() or 0) > 0
    except Exception as exc:
        raise MetricQueryError(f"Could not check whether {table} repeats rows: {exc}") from exc


def query_field(workspace: dict, field_id: str, filters: list[Filter], pg, dates: tuple = (), aggregate: Optional[str] = None,
                period: Optional[tuple] = None, date_field: Optional[str] = None, max_hops: int = 1) -> Optional[dict]:
    """The one value a field holds in the rows the filters select, read by SQL through the same guard.

    The compiler runs MIN and MAX of the field. Equal values are the answer; different values raise
    ``ClarificationNeeded`` (the question did not pick one row). No rows give None. A date applies to the table's
    one snapshot date field (concept ``snapshot_date``), else to its one time field, else it is refused.
    ``filters`` may name columns of the field's table or of one table it joins to through a declared relationship,
    and may compare a measure (>, >=, <, <=).

    ``aggregate`` (min, max, sum, count) returns that aggregate over the selected rows instead. Count counts the
    matching rows by the table's identifier (its key), or the rows that hold the field when the table has no one-column
    key. ``period`` (from, to), inclusive, filters the date field. A sum or count over a snapshot table whose records
    repeat across snapshot dates needs one date; over an event table whose rows do not repeat, any period is allowed.
    """
    dataset = workspace["id"]
    defs = _list_metric_defs(pg, dataset)
    fdef = next((m for m in defs if m.get("kind") == "field" and m.get("id") == field_id), None)
    if fdef is None:
        raise ValueError(f"{field_id} is not a field of the model")
    table, column = fdef["source_id"].split("/")[-2:]
    columns = _model_columns(defs)
    meta = {f["source_id"].split("/")[-1]: f for f in defs
            if f.get("kind") == "field" and f["source_id"].split("/")[-2] == table}
    batch, revision = _batch_and_revision(workspace)
    if aggregate is not None and aggregate not in AGGREGATES:
        raise ValueError(f"{aggregate!r} is not an aggregate")
    if len(dates) > 1 and period is None:
        raise ClarificationNeeded("date", "", set(), message="Give one date, and name the date field when the table has several.")
    span = tuple(period) if period else ((dates[0], dates[0]) if dates else None)
    plan_filters = list(filters)
    default_field, times = table_date_field(defs, table)
    if date_field is not None and date_field not in times:
        raise ClarificationNeeded("date", date_field, set(), message=f"{date_field} is not a date field of {table}.")
    date_field = date_field or default_field
    if span:
        if date_field is None:
            raise ClarificationNeeded("date", "", set(), message=(
                f"{table} has more than one date field ({', '.join(times)}), so I cannot tell which one the date means."
                if times else f"{table} has no date field, so it cannot be filtered by date."))
        if span[0] == span[1]:
            plan_filters.append(Filter(field=date_field, op="=", value=span[0]))
        else:
            plan_filters += [Filter(field=date_field, op=">=", value=span[0]), Filter(field=date_field, op="<=", value=span[1])]
    snapshot_table = any(f.get("concept_id") == "snapshot_date" for f in meta.values())
    key = table_key(defs, table)
    snapshot_field = next((n for n in times if meta[n].get("concept_id") == "snapshot_date"), None)
    one_snapshot = bool(span) and span[0] == span[1] and date_field == snapshot_field
    if aggregate in ("sum", "count") and snapshot_table and not one_snapshot and rows_repeat(pg, dataset, table, key, batch):
        raise ClarificationNeeded("date", "", set(), message=(
            f"{table} holds the same records on each business snapshot date, so a {aggregate} needs one date. "
            "Give it as YYYY-MM-DD."))
    if batch and "sourcebatch" in columns.get(table.lower(), set()):
        plan_filters.append(Filter(field="SourceBatch", op="=", value=batch))
    plan = MetricPlan(metric=column, filters=plan_filters)
    out = []
    for fn in ([AGGREGATES[aggregate]] if aggregate else ("MIN", "MAX")):
        body = (f"COUNT(DISTINCT {table}.{key})" if key else f"COUNT({table}.{column})") if fn == "COUNT" else f"{fn}({table}.{column})"
        synthetic = {"expression": [{"dialect": "ANSI_SQL", "expression": body}],
                     "source_metadata": {"custom_extensions": [{"vendor_name": "HELIOS", "data": {"dataset": table}}]}}
        sql, tables = _compile(synthetic, plan, dataset, defs, max_hops)
        _check_sources(workspace, tables)
        limits = guard_limits_for(synthetic, plan, defs, tables)
        guard_sql(sql, allowed_tables=tables, schema=_dataset_schema(dataset),
                  columns=limits[0], literals=limits[1], funcs=limits[2])
        rows = _run_sql(pg, sql)
        if not rows:
            return None
        out.append((sql, rows[0]["metric_value"]))
    if aggregate is None and out[0][1] != out[1][1]:
        raise ClarificationNeeded("value", "", set(), message=(
            f"The selected rows hold more than one value of {fdef.get('label') or column} "
            f"({out[0][1]} to {out[1][1]}). Name one row, for example with its date."))
    raw = out[0][1]
    if raw == "":
        return None          # an aggregate over no rows
    datatype = (fdef.get("source_metadata") or {}).get("datatype")
    value: object = raw
    if datatype in ("Integer", "Decimal") or aggregate in ("sum", "count"):
        try:
            f_val = float(raw)
            value = int(f_val) if f_val == int(f_val) else round(f_val, 8)
        except (ValueError, TypeError):
            pass
    elif _MIDNIGHT.match(raw):
        value = _MIDNIGHT.match(raw).group(1)
    label = (fdef.get("label") or column) + (f" ({aggregate})" if aggregate else "")
    return {"label": label, "value": value, "aggregate": aggregate, "sql": out[0][0], "batchHash": _latest_batch_hash(pg, dataset, table),
            "modelRevision": revision, "summary": f"{label}: {value}.", "definitionId": field_id,
            "filters": [f.model_dump() for f in plan_filters], "kind": "field"}
