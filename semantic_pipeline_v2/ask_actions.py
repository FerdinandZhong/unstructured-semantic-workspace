"""Step 6, contracts C2, C3 and C4 on the server: connections, writing a table from Ask, and the Ask router.

Write (C3, user decision of 22 November 2026): the LOCAL private PostgreSQL write is the default and always works.
Form rows go to the template tables as in step 3 (`physical_mapping`); an answer result goes to `v2_ask.v2_ask_<slug>`,
insert-only, so a replay adds nothing. Every preview also GENERATES the Cloudera Data Engineering job that would write
the same rows to Iceberg: the PySpark script, the job spec and a `cde-cli spark submit` line with credential
placeholders. The job is SUBMITTED only when the Iceberg connection is configured, the preview named it, and the user
confirms; otherwise the reply says so plainly. Nothing is simulated: no job id, status or Iceberg count is invented.

Preview is read-only. Confirm needs the preview's fingerprint and its one-use approval (10 minutes). Admin only.
A job failure is reported with the job name; the local write is never rolled back.

After a confirm, `snapshots` holds each written table as it now stands, read back from the table itself (total rows and
the first 20 rows, system columns hidden). Every reply of /api/ask, preview and confirm carries `trace` (ask_trace.py):
the steps the server recorded as they ran.

Answer handles come from `answer_detail.Details` (the server-held facts of each answer; the browser never sends SQL).
The question written with an answer result is the one `/api/ask` answered, or `source.question`, else the fact label.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
from datetime import datetime, timezone
from collections import OrderedDict
from pathlib import Path
from threading import Lock
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import sqlglot.expressions as sge

from . import answer_detail, ask_trace
from ._pilot_postgres import PilotDatabaseError
from .ask_router import _LAKE, _NOT_A_TABLE, _ONE_TABLE, WHY, clarification, route, routed
from .ask_trace import Progress, Trace
from . import ui_lang
from .chunk_index import visible as chunk_visible
from .connections import ICEBERG, LABELS, POSTGRES, Connections, run_test
from .trino_target import LABEL as TRINO_LABEL, from_env as trino_from_env
from .extracted_fields import mask_extracted
from .iceberg_target import ALLOWED, IcebergTarget, load as load_iceberg, table_name as iceberg_table
from .metric_sql import MetricQueryError, SourceWithheld, _dataset_schema, _list_metric_defs
from .physical_mapping import (_MONEY, SQL_TYPE, StalePreview, _first_date, build_rows, check_cell, declared,
                               fingerprint as rows_fingerprint, labelled_fields, row_values)
from .semantic_store import _lit

CAP = 10000
APPROVAL_SECONDS = 600
SCHEMA = "v2_ask"
NOT_SUBMITTED = "Iceberg: Spark job generated, not submitted (no live connection)"
NOT_CHOSEN = "Iceberg: Spark job generated, not submitted (the local database was chosen as the target)"
# With the Trino route (user decision, 23 November 2026) the direct write is Trino and the Spark job is for download only.
SPARK_DOWNLOAD = "Spark job: generated for download, not submitted. Run it yourself with your own arguments."
MAX_BODY = 64 * 1024
TABLE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
SNAPSHOT_ROWS = 20
SYSTEM_COLUMNS = [("v2_question", "String"), ("v2_written_at", "Timestamp"), ("v2_principal", "String")]
MAX_SOURCES = 20
CALL_TABLE = "ext_call_fact"          # long format: one row per stated value of a recording, keyed by customer_id
CALL_COLUMNS = [(n, "String") for n in ("customer_id", "field", "definition", "value", "unit", "period", "passage",
                                        "source_file", "chunk_id")]
JOB_MESSAGES = {"running": "The Spark job is running in Cloudera Data Engineering.",
                "completed": "The Spark job finished. Its post-check found every confirmed row in Iceberg.",
                "failed": "The Spark job failed or was killed in Cloudera Data Engineering. Inspect its logs there.",
                "unknown": "The job status could not be read from Cloudera Data Engineering."}


class _Refusal(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------- plans: the exact rows a confirm writes

def _source_ids(workspace, ctx, source):
    """The sources of one write, each readable by the account. `sourceIds` writes several together (23 November 2026)."""
    ids = source.get("sourceIds") if "sourceIds" in source else [source.get("sourceId")]
    if not isinstance(ids, list) or not 1 <= len(ids) <= MAX_SOURCES or len(set(map(str, ids))) != len(ids) \
            or not all(isinstance(i, str) for i in ids):
        raise _Refusal(400, f"Name one to {MAX_SOURCES} different sources")
    if not all(chunk_visible(workspace, ctx.principal, i) for i in ids):
        raise _Refusal(403, "Source access withheld for this account")
    return ids


def _title(workspace, source_id):
    return next((s.get("title") or source_id for s in workspace["sources"] if s["id"] == source_id), source_id)


WRITE_DENIED = "Writing a table requires an admin account"


def may_write(principal):
    """The one write right: an admin account. The writer and the Ask router both ask this."""
    return principal.get("role") == "admin"


def _tables_of(workspace, links, source_id):
    """The declared tables the writer's own `build_rows` fills from one source's links."""
    return list(dict.fromkeys(r["table"] for r in build_rows(workspace, [k for k in links if k["source_id"] == source_id])))


def _one_table(workspace, like):
    """The one declared table a request named ("transaction", "str_transaction", "account entity"), or None when it
    named none. A name that fits no declared table, or more than one, is refused with the declared names."""
    if not isinstance(like, str) or not like.strip():
        return None
    key = lambda text: re.sub(r"[^a-z0-9]", "", text.lower())
    names = [t["name"] for t in declared(workspace)]
    # The whole phrase, or any of its words ("transactions", "right transaction"), names the table.
    words = {key(like)} | {key(w)[:-1] if key(w).endswith("s") else key(w) for w in like.split()}
    found = [n for n in names if any(len(w) >= 4 and w in key(n) for w in words)]
    if len(found) != 1:
        raise _Refusal(400, f"No single declared table is named \"{like.strip()[:40]}\". The declared tables are: " + ", ".join(names) + ".")
    return found[0]


def _form_plan(ctx, workspace, source):
    ids = _source_ids(workspace, ctx, source)
    rows, links = [], {}
    for source_id in ids:
        links[source_id] = ctx.links(source_id, workspace)
        built = build_rows(workspace, links[source_id])
        if not built:
            raise _Refusal(404, "This document has no rows of a predefined table" if len(ids) == 1
                           else f"{_title(workspace, source_id)} has no rows of a predefined table")
        rows += built if len(ids) == 1 else [{**r, "sourceId": source_id} for r in built]
    source_id = ids[0]
    only = _one_table(workspace, source.get("tableLike"))
    if only:        # the request named one declared table: the rows of the others are left out of this write
        rows = [r for r in rows if r["table"] == only]
    kept = [r for r in rows if r["status"] == "previewed"]
    schema, tables = _dataset_schema(workspace["id"]), []
    for table in declared(workspace):
        mine = [row_values(workspace, r) for r in kept if r["table"] == table["name"]]
        if not mine:
            continue
        columns = [{"name": c["name"], "type": c["datatype"]} for c in table["columns"] if any(c["name"] in v for v in mine)]
        tables.append({"name": f"{schema}.{table['name'].lower()}", "iceberg": iceberg_table(table["name"]),
                       "columns": columns, "rowCount": len(mine), "sample": mine[:10]})
    warnings = [f"{len(rows) - len(kept)} rows failed a type or unit check and are not written."] if len(kept) < len(rows) else []
    if only:
        warnings.append(f"Only {only} is written, as the request named it. The other declared tables are left out.")
    return {"kind": "form", "sourceId": source_id, "sourceIds": ids, "rows": rows, "links": links, "kept": kept,
            "tables": tables, "warnings": warnings, "source": ",".join(ids)}


def _call_plan(ctx, workspace, source):
    """Rows of the long table ext_call_fact: one row per value that an APPROVED link of a recording states, keyed by the
    one customer ID that recording states. Values come from model-read links, so only links a person approved count."""
    rows, titles, skipped = [], [], 0
    for source_id in _source_ids(workspace, ctx, source):
        record = next((s for s in workspace["sources"] if s["id"] == source_id), None)
        if record is None or record.get("kind") != "audio":
            raise _Refusal(404, f"{_title(workspace, source_id)} is not a recording")
        stated = [k for k in ctx.links(source_id, workspace) if k.get("relation") == "states_value_of"
                  and k.get("stated_value") not in (None, "") and k.get("definition_id")]
        approved = [k for k in stated if k.get("status") == "corrected"]       # approved or corrected by a person
        skipped += len(stated) - len(approved)
        column = lambda k: k["definition_id"].split("/field/")[-1].split("/")       # [table, column]
        customers = sorted({str(k["stated_value"]) for k in approved if column(k)[-1] == "CustomerID"})
        if len(customers) != 1:
            raise _Refusal(409, f"{record.get('title') or source_id} states {len(customers)} customer IDs in its approved links. "
                                "The table needs exactly one: approve or reject the customer ID links on the Evidence step.")
        titles.append(record.get("title") or source_id)
        seen = set()
        for k in approved:
            table, name = column(k)[-2:]
            key = (table, name, str(k["stated_value"]))
            if name == "CustomerID" or key in seen:
                continue
            seen.add(key)
            rows.append({"customer_id": customers[0], "field": " ".join(re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z0-9]+", name)).lower(),
                         "definition": f"{table}.{name}", "value": str(k["stated_value"]), "unit": k.get("unit"),
                         "period": k.get("period"), "passage": str((k.get("span") or {}).get("text") or "")[:500],
                         "source_file": titles[-1], "chunk_id": k.get("chunk_id")})
    if not rows:
        raise _Refusal(404, "This recording has no approved stated value besides its customer ID")
    warnings = [f"{skipped} stated value{'s are' if skipped != 1 else ' is'} not approved and {'are' if skipped != 1 else 'is'} not written."] \
        if skipped else []
    return {"kind": "call", "dataset": workspace["id"], "table": CALL_TABLE, "columns": CALL_COLUMNS, "rows": rows,
            "question": "Stated in " + ", ".join(titles), "warnings": warnings,
            "sourceIds": list(source.get("sourceIds") or [source["sourceId"]]),
            "source": ",".join(source.get("sourceIds") or [source["sourceId"]]),
            "tables": [{"name": f"{SCHEMA}.{CALL_TABLE}", "iceberg": CALL_TABLE,
                        "columns": [{"name": n, "type": t} for n, t in CALL_COLUMNS + SYSTEM_COLUMNS],
                        "rowCount": len(rows), "sample": rows[:10]}]}


def _slug(text):
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", (text or "").lower())).strip("_")[:33] or "result"


# ---------------------------------------------------------------- discovered table: a new table from a document's labels

MIN_FIELDS = 3
DOC_SYSTEM = [("v2_source_id", "String"), ("v2_source_revision", "String"), ("v2_row_key", "String")]
DOC_KEYS = [n for n, _ in DOC_SYSTEM]
PROPOSED = "The table is proposed from the document's own fields, not from a declared model table."
NO_DECLARED = "No declared model table matches this document."
# ponytail: a short list of SQL reserved words a label may spell; a name in it gets "_field". Quote identifiers if more bite.
_RESERVED = set("all and any as asc case check column constraint create default desc distinct do else end false for foreign "
                "from grant group having in into is join limit not null offset on or order primary references select table "
                "then to true union unique user using when where with".split())


_IDENTIFIER = re.compile(r"\b(number|no|id|reference|ref|code)$", re.I)      # "Account number", not "Number of items"


def _infer(value, label=""):
    """(kind, typed, currency) by the existing cell parsers: Date, Money ("CCY amount"), Integer, Decimal, else String.
    No guess: a value with a leading zero or any other form stays text, as written. A field whose label names an
    identifier stays text too: as a number it would lose leading zeros and escape the identifier mask."""
    if _IDENTIFIER.search(label.strip()):
        return "String", value, None
    whole = re.fullmatch(r"\d{1,2} [A-Za-z]+ \d{4}", value)
    typed, bad = check_cell("Date", _first_date(value) if whole else value)
    if bad is None:
        return "Date", typed, None
    money = _MONEY.fullmatch(value)
    number = re.compile(r"-?(0|[1-9]\d*)(\.\d+)?")
    if money and number.fullmatch(money[2].replace(",", "")):
        return "Money", money[2].replace(",", ""), money[1]
    if number.fullmatch(value):
        return ("Decimal" if "." in value else "Integer"), value, None
    return "String", value, None


def _discover(fields):
    """[(column name, field)] for one document: snake case of the label, with the section when two sections share it."""
    sections = {}
    for f in fields:
        sections.setdefault(f["label"].lower(), set()).add(f["section"])
    out, used = [], set()
    for f in fields:
        name = _slug(f"{f['section']} {f['label']}" if len(sections[f["label"].lower()]) > 1 and f["section"] else f["label"])
        name = ("f_" + name if name[0].isdigit() else name) + ("_field" if name in _RESERVED else "")
        base, n = name, 2
        while name in used:      # ponytail: a label spelling "<other label> currency" can still meet a currency column
            name, n = f"{base}_{n}", n + 1
        used.add(name)
        out.append((name, f))
    return out


def _definitions(links, named):
    """{column: definition id} where a proposed or approved link states a value and its quote covers that field only."""
    out = {}
    for name, f in named:
        for k in links or []:
            quote = str((k.get("notes") or {}).get("quote") or (k.get("span") or {}).get("text") or "")
            if (k.get("relation") == "states_value_of" and k.get("definition_id") and k.get("status") in ("proposed", "corrected")
                    and k.get("chunk_id") == f["chunk"]["chunk_id"] and f["quote"] in quote
                    and not any(g is not f and g["chunk"]["chunk_id"] == f["chunk"]["chunk_id"] and g["quote"] in quote for _, g in named)):
                out.setdefault(name, k["definition_id"])
    return out


def _document_plan(ctx, workspace, source, table=None, fallback=False):
    """One wide table per label set: a row per document, typed by code from its own values. The table name is `doc_`, the
    slug of the document's title and a digest of its column names, so the same labels always land in the same table.
    ponytail: no ADD COLUMN evolution; a table that exists with other columns is refused. No schema editor yet."""
    ids, docs = _source_ids(workspace, ctx, source), []
    reader = getattr(ctx, "chunks", None)
    for source_id in ids:
        title, fields = labelled_fields(reader(source_id, workspace) if reader else [])
        if len(fields) < MIN_FIELDS:
            raise _Refusal(404, "This document has fewer than three labelled fields, so no table can be proposed from it"
                           if len(ids) == 1 else f"{_title(workspace, source_id)} has fewer than three labelled fields")
        docs.append((source_id, title or _title(workspace, source_id), _discover(fields)))
    groups = {hashlib.sha256(json.dumps(sorted(n for n, _ in named)).encode()).hexdigest() for *_, named in docs}
    if len(groups) > 1:
        raise _Refusal(409, "These documents have different labelled fields, so they do not share one table. Write them separately.")
    group, (_, title, named) = groups.pop(), docs[0]
    proposed = f"doc_{_slug(title)[:24].strip('_')}_{group[:6]}"
    name = proposed if table is None else table
    if not isinstance(name, str) or not TABLE.match(name):
        raise _Refusal(400, "A table name is lower-case letters, digits and underscores (up to 41 characters)")
    name = name if name.startswith("ext_") else "ext_" + name
    if not TABLE.match(name):
        raise _Refusal(400, "The table name is too long")
    kinds = {}
    for _, _, fields in docs:
        for n, f in fields:
            kinds.setdefault(n, set()).add(_infer(f["value"], f["label"])[0])
    kinds = {n: k.pop() if len(k) == 1 else "String" for n, k in kinds.items()}      # the files disagree: text
    columns = []
    for n, _ in named:
        kind = kinds[n]
        columns += [(n, "Decimal"), (n + "_currency", "String")] if kind == "Money" else [(n, kind)]
    rows, meaning = [], {}
    for source_id, _, fields in docs:
        row = {}
        for n, f in fields:
            kind, typed, currency = _infer(f["value"], f["label"])
            row.update({n: typed, n + "_currency": currency} if kinds[n] == "Money" else {n: typed if kind == kinds[n] else f["value"]})
        rows.append({**row, "v2_source_id": source_id, "v2_source_revision": fields[0][1]["chunk"]["source_revision"], "v2_row_key": "1"})
        for n, d in _definitions(ctx.links(source_id, workspace), fields).items():
            meaning.setdefault(n, d)
    columns += DOC_SYSTEM
    try:
        existing = json.loads(ctx.pg.sql(
            "SELECT COALESCE(json_agg(column_name::text ORDER BY ordinal_position), '[]') FROM information_schema.columns"
            f" WHERE table_schema = {_lit(SCHEMA)} AND table_name = {_lit(name)};") or "[]")
    except PilotDatabaseError:
        raise _Refusal(503, "The data could not be queried.") from None
    wanted = [n for n, _ in columns] + ["v2_written_at", "v2_principal"]
    if existing and existing != wanted:
        differ = [n for n in wanted if n not in existing] + [n for n in existing if n not in wanted] or ["column order"]
        raise _Refusal(409, f"Table {SCHEMA}.{name} already exists with other columns: {', '.join(differ)}. Name another table.")
    titles = [t for _, t, _ in docs]
    return {"kind": "document", "dataset": workspace["id"], "table": name, "columns": columns, "rows": rows, "keys": DOC_KEYS,
            "question": "Fields of " + ", ".join(titles), "group": group, "sourceIds": ids, "source": ",".join(ids),
            "warnings": ([NO_DECLARED] if fallback else []) + [PROPOSED],
            "tables": [{"name": f"{SCHEMA}.{name}", "iceberg": name, "discovered": True, "proposed": name.removeprefix("ext_"),
                        "columns": [{"name": n, "type": t, "definition": meaning.get(n)} for n, t in columns + SYSTEM_COLUMNS[1:]],
                        "rowCount": len(rows), "sample": rows[:10]}]}


def _answer_plan(ctx, workspace, source, table, questions):
    handle = source.get("handle")
    try:
        entry = ctx.details.resolve(handle, workspace["id"], ctx.principal) if isinstance(handle, str) else None
        if entry is None or "fact" not in entry:
            raise LookupError(handle)
        fact, tree, base, conds, _, fields = answer_detail._prepare(entry, workspace, ctx.pg)
    except LookupError:
        raise _Refusal(404, "This answer is no longer held by the server. Ask the question again.") from None
    except SourceWithheld:
        raise _Refusal(403, "The source behind this answer is withheld for this account.") from None
    except (MetricQueryError, PilotDatabaseError):
        raise _Refusal(503, "The data could not be queried.") from None
    question = source.get("question") if isinstance(source.get("question"), str) and source["question"].strip() \
        else questions.get(handle) or fact.get("label") or ""
    question = mask_extracted(question.strip()[:2000])     # it is written and embedded in the script
    name = table if table is not None else _slug(fact.get("label") or question)
    if not isinstance(name, str) or not TABLE.match(name.removeprefix("v2_ask_") or "x"):
        raise _Refusal(400, "A table name is lower-case letters, digits and underscores (up to 41 characters)")
    name = name if name.startswith("v2_ask_") else "v2_ask_" + name
    if not TABLE.match(name):
        raise _Refusal(400, "The table name is too long")
    source_table = base.name.lower()
    business = [(t, c) for (t, c) in fields if t == source_table and c not in answer_detail.SYSTEM]
    if not business:
        raise _Refusal(404, "This answer has no business columns to write")
    columns = [(answer_detail._name(fields, t, c).lower(),
                (fields[t, c].get("source_metadata") or {}).get("datatype") or "String") for t, c in business]
    query = tree.select(*[sge.alias_(sge.column(c, table=t), n, quoted=True) for (t, c), (n, _) in zip(business, columns)],
                        append=False)
    query.set("where", None)
    query.set("limit", None)
    query.set("order", None)
    if conds:
        query = query.where(sge.and_(*conds))
    query = query.order_by(*[str(i) for i in range(1, len(columns) + 1)]).limit(CAP)
    try:
        rows = answer_detail._json(ctx.pg, query.sql(dialect="postgres"))
        total = answer_detail._json(ctx.pg, answer_detail._query(tree, [sge.alias_(sge.Count(this=sge.Star()), "n")], conds))[0]["n"]
        existing = json.loads(ctx.pg.sql(
            "SELECT COALESCE(json_agg(column_name::text ORDER BY ordinal_position), '[]') FROM information_schema.columns"
            f" WHERE table_schema = {_lit(SCHEMA)} AND table_name = {_lit(name)};") or "[]")
    except (MetricQueryError, PilotDatabaseError):
        raise _Refusal(503, "The data could not be queried.") from None
    wanted = [n for n, _ in columns] + [n for n, _ in SYSTEM_COLUMNS]
    if existing and existing != wanted:
        raise _Refusal(409, f"Table {SCHEMA}.{name} already exists with other columns. Name another table.")
    rows = mask_extracted(rows)
    warnings = [f"At most {CAP:,} rows are written."]
    if total > len(rows):
        warnings.append(f"The answer covers {total:,} rows; only the first {len(rows):,} are written.")
    return {"kind": "answer", "dataset": workspace["id"], "table": name, "columns": columns, "rows": rows,
            "question": question, "warnings": warnings, "source": handle,
            "tables": [{"name": f"{SCHEMA}.{name}", "iceberg": name,
                        "columns": [{"name": n, "type": t} for n, t in columns + SYSTEM_COLUMNS],
                        "rowCount": len(rows), "sample": rows[:10]}]}


def _suggest(ctx, workspace, plan):
    """A grouping hint: the other readable sources whose evidence links fill the same tables as this plan, so they can
    be written in the same job. Derived by code from the links; no model is asked.
    ponytail: each candidate's links are read once per preview; cache per source revision if a dataset has hundreds."""
    if plan["kind"] == "answer":
        return {"sources": [], "reason": ""}
    builder = {"call": _call_plan, "form": _form_plan, "document": _document_plan}[plan["kind"]]
    wanted = plan.get("group") or {t["name"] for t in plan["tables"]}
    chosen, found = set(plan["sourceIds"]), []
    for record in workspace["sources"]:
        if record["id"] in chosen or record.get("kind") != ("audio" if plan["kind"] == "call" else "pdf") \
                or not chunk_visible(workspace, ctx.principal, record["id"]):
            continue
        try:
            other = builder(ctx, workspace, {"sourceId": record["id"]})
        except _Refusal:
            continue
        if (other.get("group") or {t["name"] for t in other["tables"]}) == wanted:
            found.append({"sourceId": record["id"], "title": record.get("title") or record["id"],
                          "rows": sum(t["rowCount"] for t in other["tables"])})
    out = {"sources": found[:MAX_SOURCES - len(chosen)],
           "reason": "Their labelled fields are the same, so their rows go to the same new table." if plan["kind"] == "document"
           else "Their evidence links fill the same tables, so one job can write them together."}
    hint = _hint(getattr(ctx, "chat", None), workspace, plan, out["sources"])
    if hint:
        out["hint"] = {"text": hint, "label": HINT_LABEL}
    return out


HINT_LABEL = "AI-written hint, from the evidence links. Check the preview before you confirm."
HINT_PROMPT = ("You help a data steward. Files were read and their evidence links fill fields of the same tables. In ONE plain "
               "sentence of at most 30 words, say why the suggested files can be written together with the chosen files. Name the "
               "tables and two or three of the shared fields. Use only the file names, table names and field names you are "
               "given. State no number and no value. "
               'Reply as JSON: {"reason": "..."}')


def _hint(chat, workspace, plan, suggested):
    """One model-written sentence on why the suggested sources fit the chosen ones, or None. The model sees names only
    (files, tables, fields); code has already decided the group, so a failed or odd reply only drops the sentence."""
    if chat is None or not suggested:
        return None
    titles = {f["name"].split("/", 1)[-1].lower(): f.get("title") or f["name"] for f in workspace["knowledge"]["structured"]["fields"]}
    structured = workspace["knowledge"]["structured"]
    labels = {str(d["name"]).lower(): d.get("title") or d["name"] for d in structured.get("datasets") or []}
    business = lambda name: name.lower() != "sourcerevision" and not name.lower().endswith("seq") and not name.startswith("v2_")
    user = json.dumps({"chosen": [_title(workspace, i) for i in plan["sourceIds"]], "suggested": [s["title"] for s in suggested],
                       "tables": [{"table": labels.get(t["name"].split(".")[-1].lower(), t["name"].split(".")[-1]),
                                   "fields": [titles.get(c["name"].lower(), c["name"]) for c in t["columns"] if business(c["name"])][:20]}
                                  for t in plan["tables"]]}, ensure_ascii=False)
    try:
        reason = json.loads(chat.complete(ui_lang.chinese(HINT_PROMPT, "the reason"), user)).get("reason")
    except Exception:          # any provider or reply failure: no hint, the suggestion stands
        return None
    if ui_lang.current() == ui_lang.ZH and isinstance(reason, str) and ui_lang.magnitude(reason):
        return None            # a converted figure (万, 亿): the hint states no number
    return reason.strip() if isinstance(reason, str) and 10 <= len(reason.strip()) <= 300 else None


def _fingerprint(plan, binding):
    if plan["kind"] == "form":
        return rows_fingerprint(plan["rows"], binding)
    body = json.dumps([plan["dataset"], plan["table"], plan["columns"], plan["rows"], plan["question"]], sort_keys=True)
    return hashlib.sha256((body + binding).encode()).hexdigest()


# ---------------------------------------------------------------- local writes with a read-back count

def _values(row, columns):
    return [None if row.get(n) is None else str(row.get(n)) for n, _ in columns]


def write_answer(pg, plan, actor):
    """Insert the plan's rows into v2_ask.<table> once: a row equal (null-safe) to a stored row with the same question is
    not inserted again. Returns {written, found, expected}; `found` is read back from the table."""
    target, columns = f"{SCHEMA}.{plan['table']}", plan["columns"]
    asked = [] if plan.get("keys") else [plan["question"]]        # a document table is keyed by its own system columns
    names = [n for n, _ in columns] + ["v2_question"] * len(asked)
    kinds = [SQL_TYPE.get(t, "text") for _, t in columns] + ["text"] * len(asked)
    ddl = ", ".join(f"{n} {k}" for n, k in zip(names, kinds)) + ", v2_written_at timestamptz NOT NULL, v2_principal text NOT NULL"
    if not plan["rows"]:
        pg.sql(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}; CREATE TABLE IF NOT EXISTS {target} ({ddl});")
        return {"written": 0, "found": 0, "expected": 0}
    values = ",\n".join("(" + ", ".join(("NULL" if v is None else _lit(v)) + "::" + k
                                         for v, k in zip(_values(r, columns) + asked, kinds)) + ")"
                        for r in plan["rows"])
    match = " AND ".join(f"t.{n} IS NOT DISTINCT FROM s.{n}" for n in plan.get("keys") or names)
    source = f"(VALUES {values}) AS s({', '.join(names)})"
    out = pg.sql(
        f"BEGIN;\nCREATE SCHEMA IF NOT EXISTS {SCHEMA};\nCREATE TABLE IF NOT EXISTS {target} ({ddl});\n"
        f"WITH ins AS (INSERT INTO {target} ({', '.join(names)}, v2_written_at, v2_principal) "
        f"SELECT s.*, now(), {_lit(actor)} FROM {source} WHERE NOT EXISTS (SELECT 1 FROM {target} t WHERE {match}) "
        "RETURNING 1) SELECT count(*) FROM ins;\nCOMMIT;")
    written = int(out.split()[-1])
    found = int(pg.sql(f"SELECT count(*) FROM {target} t WHERE EXISTS (SELECT 1 FROM {source} WHERE {match});"))
    return {"written": written, "found": found, "expected": len(plan["rows"])}


def write_form(ctx, workspace, plan, actor, added=None):
    """The step-3 write (store preview then confirm, both bound to today's links), with a read-back count. `added`, when
    given, receives the new rows per table ("<schema>.<table>": n)."""
    before, now = set(), set()
    for source_id in plan["sourceIds"]:
        before |= {(source_id, r["table"], r["rowKey"]) for r in ctx.physical.read(workspace["id"], source_id) if r["status"] == "written"}
        ctx.physical.preview(workspace, source_id, plan["links"][source_id], actor)
        stored = ctx.physical.confirm(workspace, source_id, actor, links=plan["links"][source_id])
        now |= {(source_id, r["table"], r["rowKey"]) for r in stored if r["status"] == "written"}
    schema, tables, found = _dataset_schema(workspace["id"]), {t["name"]: t for t in declared(workspace)}, 0
    if added is not None:
        for _, table, _ in now - before:
            added[f"{schema}.{table.lower()}"] = added.get(f"{schema}.{table.lower()}", 0) + 1
    for row in plan["kept"]:
        values, table = row_values(workspace, row), tables[row["table"]]
        where = " AND ".join(f"{k.lower()} = {_lit(str(values[k]))}::{SQL_TYPE[next(c['datatype'] for c in table['columns'] if c['name'] == k)]}"
                             for k in table["key"])
        found += int(ctx.pg.sql(f"SELECT count(*) FROM {schema}.{table['name'].lower()} WHERE {where};"))
    return {"written": len(now - before), "found": found, "expected": len(plan["kept"])}


def snapshot(pg, table, added):
    """The table as it now stands in the local database: its total and its first rows (system columns hidden)."""
    schema, name = table.split(".", 1)
    columns = json.loads(pg.sql(
        "SELECT COALESCE(json_agg(column_name::text ORDER BY ordinal_position), '[]') FROM information_schema.columns"
        f" WHERE table_schema = {_lit(schema)} AND table_name = {_lit(name)};") or "[]")
    shown = [c for c in columns if not c.startswith("v2_") and c not in answer_detail.SYSTEM]
    rows = "'[]'::json"
    if shown:
        listed = ", ".join('"' + c.replace('"', '""') + '"' for c in shown)
        order = ", ".join(str(i) for i in range(1, len(shown) + 1))
        rows = (f"(SELECT COALESCE(json_agg(t), '[]') FROM (SELECT {listed} FROM {table} ORDER BY {order} "
                f"LIMIT {SNAPSHOT_ROWS}) t)")
    out = json.loads(pg.sql(f"SELECT json_build_object('total', (SELECT count(*) FROM {table}), 'rows', {rows});"))
    return {"target": POSTGRES, "table": table, "total": out["total"], "added": added, "columns": shown,
            "rows": out["rows"], "readAt": datetime.now(timezone.utc).isoformat(timespec="seconds")}


# ---------------------------------------------------------------- preview, confirm, status, script

class Writer:
    """Approvals and generated scripts. ponytail: in server memory; a restart forgets them and the user previews again."""

    def __init__(self, clock=time.time):
        self.clock, self.lock = clock, Lock()
        self.approvals, self.scripts, self.questions = {}, OrderedDict(), OrderedDict()

    def remember_question(self, handle, question):
        with self.lock:
            self.questions[handle] = question
            while len(self.questions) > 2000:
                self.questions.popitem(last=False)

    @staticmethod
    def _workspace(ctx, dataset, kind):
        if not isinstance(dataset, str) or dataset not in ctx.principal["datasets"]:
            return None
        finder = getattr(ctx, "workspace_for", None)
        if finder is not None:
            return finder(dataset, kind)
        return ctx.workspace if ctx.workspace and ctx.workspace["id"] == dataset else None

    def _target(self, ctx, target):
        """(live target or None, generator target, binding)."""
        configured = ctx.iceberg()
        live = configured if target == ICEBERG and configured is not None and configured.problem() is None else None
        generator = live or IcebergTarget.generator(configured)
        return live, generator, target + (live.binding() if live else "")

    def _plan(self, ctx, request):
        source = request["source"]
        kind = source.get("kind")
        workspace = self._workspace(ctx, request["dataset"], kind)
        if workspace is None:
            raise _Refusal(403, "Dataset access withheld for this account")
        if kind == "form":
            try:
                return workspace, _form_plan(ctx, workspace, source)
            except _Refusal as error:      # no declared table: a document with enough labelled fields gets a new one
                if error.status != 404:
                    raise
                try:
                    return workspace, _document_plan(ctx, workspace, source, request.get("table"), fallback=True)
                except _Refusal:
                    raise error from None
        if kind == "document":
            return workspace, _document_plan(ctx, workspace, source, request.get("table"))
        if kind == "answer":
            return workspace, _answer_plan(ctx, workspace, source, request.get("table"), self.questions)
        if kind == "call":
            return workspace, _call_plan(ctx, workspace, source)
        raise _Refusal(400, "The source kind is form, document, call or answer")

    def _spark(self, generator, workspace, plan, fp, actor):
        if plan["kind"] == "form":
            script, spec, payload = generator.build(workspace, plan["rows"], source_id=plan["sourceId"], actor=actor, fingerprint=fp)
        else:
            script, spec, payload = generator.build_answer(plan["table"], plan["columns"], [_values(r, plan["columns"]) for r in plan["rows"]],
                                                     question=plan["question"], actor=actor, fingerprint=fp, keys=plan.get("keys"))
        return {"name": spec["name"], "script": script, "spec": spec, "command": generator.command(spec), "state": "generated"}, payload

    @staticmethod
    def _trino(ctx):
        """The Trino write route, when one is configured: then no Spark job is submitted."""
        finder = getattr(ctx, "trino", None)
        return finder() if finder is not None else None

    def preview(self, ctx, body):
        if not may_write(ctx.principal):
            return 403, {"error": WRITE_DENIED}
        if (not isinstance(body, dict) or not {"dataset", "source"} <= set(body) <= {"dataset", "source", "target", "table"}
                or not isinstance(body["source"], dict) or body.get("target", POSTGRES) not in (POSTGRES, ICEBERG)
                or not isinstance(body.get("table", ""), str)):
            return 400, {"error": "Require a dataset, a source (form or answer) and a target (postgres or iceberg_cde)"}
        target = body.get("target", POSTGRES)
        ask_trace.mark("preview")
        try:
            workspace, plan = self._plan(ctx, body)
            live, generator, binding = self._target(ctx, target)
        except _Refusal as error:
            return error.status, {"error": str(error)}
        fp = _fingerprint(plan, binding)
        ask_trace.mark("spark")
        spark, payload = self._spark(generator, workspace, plan, fp, ctx.principal["id"])
        through_trino = self._trino(ctx) is not None
        for table in plan["tables"]:
            table["icebergName"] = f"{ALLOWED[0]}.{ALLOWED[1]}.{table.pop('iceberg')}"
        warnings = list(plan["warnings"])
        if through_trino:
            warnings.append(SPARK_DOWNLOAD)
            try:        # ask the lakehouse now, so a refusal shows on the preview and not after Confirm
                warnings[:0] = self._trino(ctx).refusals(payload)
            except Exception:       # ponytail: a check that cannot run says nothing; the confirm still reports its own result
                pass
        elif live is None:
            configured = ctx.iceberg()
            warnings.append(NOT_CHOSEN if configured is not None and configured.problem() is None else NOT_SUBMITTED)
            if target == ICEBERG:
                warnings.append("The Iceberg connection is not ready: "
                                + (configured.problem() if configured is not None else "not configured")
                                + ". The rows are written to the local database only.")
        now = self.clock()
        approval = secrets.token_hex(16)     # hex: never mistaken for an NRIC by the response mask
        with self.lock:
            for key in [k for k, v in self.approvals.items() if v["expires"] < now]:
                del self.approvals[key]
            self.approvals[approval] = {"fingerprint": fp, "principal": ctx.principal["id"], "request": {**body, "target": target},
                                        "spark": spark, "payload": payload, "expires": now + APPROVAL_SECONDS}
            self.scripts[fp] = {"principal": ctx.principal["id"], "script": spark["script"], "file": spark["spec"]["spark"]["file"]}
            while len(self.scripts) > 200:
                self.scripts.popitem(last=False)
        reply = mask_extracted({"fingerprint": fp, "approval": approval,
                                "target": {"id": target, "label": TRINO_LABEL if through_trino else LABELS[target],
                                           "submits": through_trino or live is not None},
                                "tables": plan["tables"], "warnings": warnings,
                                "suggested": _suggest(ctx, workspace, plan)})
        reply["source"] = body["source"]        # the card re-previews with it (another target, a suggested group)
        return 200, {**reply, "spark": spark}

    def confirm(self, ctx, body):
        if not may_write(ctx.principal):
            return 403, {"error": WRITE_DENIED}
        if not isinstance(body, dict) or set(body) != {"fingerprint", "approval"} or not all(isinstance(v, str) for v in body.values()):
            return 400, {"error": "Require the preview's fingerprint and approval"}
        with self.lock:
            held = self.approvals.pop(body["approval"], None)      # one use
        if held is None or held["expires"] < self.clock() or held["principal"] != ctx.principal["id"] \
                or held["fingerprint"] != body["fingerprint"]:
            return 409, {"error": "This approval is unknown, used, expired or for other rows. Preview again."}
        request, actor = held["request"], ctx.principal["id"]
        ask_trace.mark("local")
        added, snapshots = {}, None
        try:
            workspace, plan = self._plan(ctx, request)
            live, _, binding = self._target(ctx, request["target"])
            if _fingerprint(plan, binding) != held["fingerprint"]:
                raise _Refusal(409, "The rows or the target settings changed after the preview. Preview again.")
            with ctx.lock:
                local = write_form(ctx, workspace, plan, actor, added) if plan["kind"] == "form" \
                    else write_answer(ctx.pg, plan, actor)
                if plan["kind"] != "form":
                    added[plan["tables"][0]["name"]] = local["written"]
                try:       # the written tables as they now stand; a failed read-back never hides the write
                    snapshots = [snapshot(ctx.pg, t["name"], added.get(t["name"], 0)) for t in plan["tables"]]
                except PilotDatabaseError:
                    snapshots = None
        except _Refusal as error:
            return error.status, {"error": str(error)}
        except StalePreview as error:
            return 409, {"error": str(error)}
        except PilotDatabaseError:
            return 503, {"error": "Private PostgreSQL access is unavailable. Nothing was confirmed."}
        labels = [f"Written to the local database: {local['written']} rows"]
        job, status, iceberg = None, "written", None
        spark = held["spark"]
        trino = self._trino(ctx)
        if trino is not None:   # the same tables and rows, written through Trino; the Spark job is for download only
            ask_trace.mark("iceberg")
            result = trino.write(held["payload"])
            iceberg = result["tables"]
            job = {"name": "Trino write", "runId": None, "status": result["status"], "message": result["message"]}
            status = "written" if result["status"] == "completed" else "failed"
            labels += ["Iceberg: " + result["message"], SPARK_DOWNLOAD]
        elif live is None:
            configured = ctx.iceberg()
            labels.append(NOT_CHOSEN if configured is not None and configured.problem() is None else NOT_SUBMITTED)
        else:   # after the local write; a failure here is reported, never rolled back or faked
            ask_trace.mark("iceberg")
            result = live.submit(spark["script"], spark["spec"])
            job = {"name": result["jobName"], "runId": result.get("runId"), "status": result["status"], "message": result["message"]}
            try:
                ctx.jobs.save(request["dataset"], str(plan["source"]), held["fingerprint"], actor, result)
            except PilotDatabaseError:
                pass
            status = "submitted" if result["status"] == "submitted" else "failed"
            labels.append(f"Iceberg: Spark job {job['name']} submitted to Cloudera Data Engineering" if status == "submitted"
                          else f"Iceberg: Spark job {job['name']} {result['status']}: {result['message']}")
        reply = {"status": status, "postgres": local, "job": job, "labels": labels}
        if iceberg is not None:
            reply["iceberg"] = iceberg
        if snapshots is not None:
            reply["snapshots"] = snapshots
        else:
            reply["snapshotError"] = "The written table could not be read back."
        return 200, mask_extracted(reply)

    def job_status(self, ctx, body):
        if ctx.principal["role"] != "admin":
            return 403, {"error": "Reading a job requires an admin account"}
        if not isinstance(body, dict) or set(body) != {"job"} or not isinstance(body["job"], str):
            return 400, {"error": "Require a job name"}
        try:
            job = ctx.jobs.by_name(body["job"])
            if job is None or job["dataset"] not in ctx.principal["datasets"]:
                return 404, {"error": "No such job"}
            ice = ctx.iceberg()
            if job.get("runId") and ice is not None and ice.problem() is None:
                state = ice.status(job["runId"])
                job = {**job, "status": state, "message": JOB_MESSAGES[state]}
                ctx.jobs.save(job["dataset"], job["sourceId"], job["fingerprint"], job["actor"], job)
        except PilotDatabaseError:
            return 503, {"error": "Private PostgreSQL access is unavailable"}
        return 200, {"name": job["jobName"], "runId": job.get("runId"), "status": job["status"], "message": job["message"]}

    def script(self, ctx, fp):
        if ctx.principal["role"] != "admin":
            return 403, {"error": "Downloading a job script requires an admin account"}
        with self.lock:
            held = self.scripts.get(fp)
        if held is None or held["principal"] != ctx.principal["id"]:
            return 404, {"error": "No generated script for this fingerprint. Preview again."}
        return 200, held["script"], held["file"]


# ---------------------------------------------------------------- traces of a write

def write_steps(trace, action, ok, out):
    """Finish a write trace from the reply. A refusal fails the step that was running (or the first one not run)."""
    last = trace.end()                   # the last step that ran, also when the trace was closed before this call
    steps = trace.view()
    by = {s["key"]: s for s in steps}
    if not ok:
        failed = last if last not in (None, "route") else next(s["key"] for s in steps if s["state"] == "waiting")
        by[failed]["state"], by[failed]["detail"]["error"] = "failed", out.get("error")
        for s in steps:
            if s["state"] == "waiting":
                s["state"] = "skipped"
        return steps
    if action == "preview":
        by["preview"]["detail"]["tables"] = [{"name": t["name"], "rowCount": t["rowCount"], "columns": len(t["columns"])}
                                             for t in out.get("tables") or []]
        by["spark"]["detail"]["name"] = (out.get("spark") or {}).get("name")
        by["local"]["detail"]["text"] = "Waits for Confirm write."
        if (out.get("target") or {}).get("submits"):
            by["iceberg"]["detail"]["text"] = "Written through Trino when you confirm." \
                if "Trino" in str((out.get("target") or {}).get("label")) else "Submitted after the local write when you confirm."
        else:
            by["iceberg"]["state"] = "skipped"
            by["iceberg"]["detail"]["text"] = next((w for w in out.get("warnings") or [] if w.startswith("Iceberg:")), NOT_SUBMITTED)
        return steps
    by["local"]["detail"].update(out.get("postgres") or {})
    by["local"]["detail"]["tables"] = [{"table": t["table"], "total": t["total"], "added": t["added"]}
                                       for t in out.get("snapshots") or []]
    job = out.get("job")
    if job:
        by["iceberg"]["state"] = "failed" if out.get("status") == "failed" else "done"
        by["iceberg"]["detail"].update(job=job)
    else:
        by["iceberg"]["state"] = "skipped"
        by["iceberg"]["detail"]["text"] = next((t for t in out.get("labels") or [] if t.startswith("Iceberg:")), NOT_SUBMITTED)
    return steps


def traced_write(writer, ctx, action, body, trace=None):
    """Preview or confirm with the steps recorded as they run; the reply gains `trace`."""
    trace = trace or Trace(ask_trace.CONFIRM if action == "confirm" else ask_trace.PREVIEW[1:])
    with ask_trace.active(trace):
        status, out = (writer.confirm if action == "confirm" else writer.preview)(ctx, body)
    return status, {**out, "trace": mask_extracted(write_steps(trace, action, status == 200, out))}


def start_trace(req, principal, dataset, steps, request_id, tool, why):
    """A trace whose Route step runs now; registered for live progress under `request_id` when the client sent one."""
    trace = Trace(steps)
    progress = getattr(req.server, "ask_progress", None)
    if progress is not None and isinstance(request_id, str):
        progress.add(request_id, Progress.owner(principal, req.session_token(), dataset), trace)
    trace.start("route")
    trace.note(tool=tool, why=why)
    return trace


# ---------------------------------------------------------------- semantic tool (no model call)

def semantic(req, principal, workspace, term):
    """Concepts (any status, labelled), definitions and up to three passages for a term, from the catalog and model."""
    wanted = term.lower()
    concepts, definitions, passages = [], [], []
    try:
        _, catalog, _ = req.catalog_rows(workspace, principal)
    except (PilotDatabaseError, AttributeError, KeyError, TypeError):
        catalog = []
    for c in catalog:
        names = [str(c.get("label") or "")] + [str(s) for s in c.get("synonyms") or []]
        if any(n and (wanted in n.lower() or n.lower() in wanted) for n in names):
            concepts.append({"id": c["id"], "label": c.get("label"), "status": c.get("status"),
                             "definition": c.get("definition"), "synonyms": c.get("synonyms") or [],
                             "definitions": [k.get("definition") for k in c.get("links") or [] if k.get("definition")]})
            passages += [p for p in c.get("evidenceDetail") or [] if p.get("quote")]
    linked = {d for c in concepts for d in c["definitions"]}
    pg = req.server.review_store
    for d in _list_metric_defs(pg, workspace["id"]) if pg is not None else []:
        if d["id"] in linked or wanted in str(d.get("label") or "").lower() or wanted == d["id"].rsplit("/", 1)[-1].lower():
            definitions.append({"id": d["id"], "kind": d.get("kind"), "label": d.get("label"), "definition": d.get("definition")})
    return {"term": term, "concepts": concepts, "definitions": definitions[:10], "passages": passages[:3]}


# ---------------------------------------------------------------- clarification from the semantic layer (no model call)

# Words that name no concept: articles, pronouns, counts and the action verbs of the router.
_STOP = set("""a an the all any every each both these those this that them it its of to into in on for from with and or
as at by my our your me us please some few one two three four five six seven eight nine ten write save export add insert
load store put create push copy send append import upload persist record ingest move table lake lakehouse iceberg file
document doc""".split())


def _words(text):
    """[(as written, key)] of the content words of a text: lower case, a plural "s" dropped, stop words left out."""
    out = []
    for word in re.findall(r"[A-Za-z][A-Za-z0-9]*", str(text or "")):
        key = word.lower()
        key = key[:-1] if len(key) > 3 and key.endswith("s") and not key.endswith("ss") else key
        if key not in _STOP:
            out.append((word, key))
    return out


def _candidates(req, principal, workspace, text, forms):
    """{words, concepts, files, tables} for a request to clarify, or None when the semantic layer finds no readable file.
    A concept (Output B, as the `semantic` tool reads it) or a model definition matches when every word of its label,
    of one synonym, or of the definition's name ("STR_Transaction") stands in the request; the longest such name wins.
    `files` are the readable sources with an Output A link to it; `tables` the one declared table the request names
    (or the only one) and the files among them with rows of it, by the writer's own `build_rows`."""
    keys = {k for _, k in _words(text)}
    try:
        _, catalog, links = req.catalog_rows(workspace, principal)
    except (PilotDatabaseError, AttributeError, KeyError, TypeError):
        catalog, links = [], req.visible_links(workspace, principal)
    pg = req.server.review_store
    hits = []                  # (covered words, label, definition ids, source ids)

    def covered(names):
        sets = [w for w in ({k for _, k in _words(n)} for n in names if n) if w and w <= keys]
        return max(sets, key=len, default=None)
    for c in catalog:
        got = covered([c.get("label"), *(c.get("synonyms") or [])])
        if got:
            via = "label" if covered([c.get("label")]) == got else "synonym"
            hits.append((got, c.get("label") or c["id"], {k.get("definition") for k in c.get("links") or []},
                         {p.get("sourceId") for p in c.get("evidenceDetail") or []},
                         {"id": c["id"], "label": c.get("label") or c["id"], "status": c.get("status"), "via": via,
                          "matched": " ".join(dict.fromkeys(w for w, k in _words(text) if k in got)),
                          "definitions": sorted(str(k.get("definition")) for k in c.get("links") or [] if k.get("definition")),
                          "used": False}))
    for d in _list_metric_defs(pg, workspace["id"]) if pg is not None else []:
        got = covered([d.get("label"), d["id"].rsplit("/", 1)[-1]])
        if got:
            hits.append((got, d.get("label") or d["id"].rsplit("/", 1)[-1], {d["id"]}, set(), {"definition": d["id"]}))
    one = _ONE_TABLE.search(text)
    like = one and " ".join(w for w in one[1].lower().split() if w not in _NOT_A_TABLE)
    try:
        table = _one_table(workspace, like) if like else None
    except _Refusal:
        table = None
    if table:       # a declared table the request names wins over a concept its words match
        with_rows = [{"id": f["id"], "title": f.get("title") or f["id"]} for f in forms if f.get("kind") != "audio"
                     and chunk_visible(workspace, principal, f["id"]) and table in _tables_of(workspace, links, f["id"])][:MAX_SOURCES]
        if with_rows:
            return {"words": like, "concepts": [table], "files": with_rows, "tables": [{"name": table, "files": with_rows}],
                    "named": True, "semantic": [h[4] for h in hits]}
    if not hits:
        return None
    top = max(len(h[0]) for h in hits)
    hits = [h for h in hits if len(h[0]) == top][:3]
    defs = {d for h in hits for d in h[2] if d}
    # a table definition stands for its fields: links state fields
    sources = {k["source_id"] for k in links if k.get("definition_id") in defs
               or any((k.get("definition_id") or "").startswith(d.replace("/dataset/", "/field/") + "/") for d in defs if "/dataset/" in d)}
    sources |= {s for h in hits for s in h[3]}
    files = [{"id": f["id"], "title": f.get("title") or f["id"]} for f in forms
             if f["id"] in sources and chunk_visible(workspace, principal, f["id"])][:MAX_SOURCES]
    if not files:
        return None
    rows = {}
    for f in files:
        for name in _tables_of(workspace, links, f["id"]):
            rows.setdefault(name, []).append(f)
    named = [t for t in rows if {k for _, k in _words(t)} <= keys]
    table = named[0] if len(named) == 1 else next(iter(rows)) if len(rows) == 1 else None
    words = " ".join(dict.fromkeys(w for w, k in _words(text) if any(k in h[0] for h in hits)))
    return {"words": words, "concepts": list(dict.fromkeys(h[1] for h in hits)), "files": files,
            "tables": [{"name": table, "files": rows[table]}] if table else [], "semantic": [h[4] for h in hits]}


def _clarify_semantics(req, workspace, principal, found):
    """The C1 `semantics` of a write clarification: the published release the answer path would read (or none), and
    the concepts and definitions the request's words matched in the catalog rows the clarification used."""
    from . import answer_path
    if answer_path.catalog_mode() != "published":
        return {"mode": "off", "release": None, "concepts": [], "definitionsUsed": []}
    catalog = answer_path.published_catalog(getattr(req.server, "releases", None), workspace["id"],
                                            lambda source: chunk_visible(workspace, principal, source))
    hits = (found or {}).get("semantic") or []
    return {"mode": "published", "release": (catalog or {}).get("release"), "concepts": [h for h in hits if "id" in h],
            "definitionsUsed": [h["definition"] for h in hits if "definition" in h]}


ALL_FILES = "All of these ({count} files)"
ROWS_FOR = "Rows for {table}"


def _write_line(files, table):
    names = [f["title"] for f in files]
    listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return f"Write {listed} to the {table} table" if table else f"Write {listed} to a table"


def _write_hints(found):
    """Hints of a write clarification from `_candidates`: every file with rows (all of them first), else every file
    that states the matched concept. Each text is a request the router writes from by its rules, with no model."""
    table = found["tables"][0] if found["tables"] else None
    files = table["files"] if table else found["files"]
    name = table["name"] if table else None
    hints = [{"label": ALL_FILES.format(count=len(files)), "text": _write_line(files, name)}] if len(files) > 1 else []
    hints += [{"label": f["title"], **({"detail": ROWS_FOR.format(table=name)} if name else {}), "text": _write_line([f], name)}
              for f in files]
    return {"hints": hints[:6], "hintKind": "choice"}


def _flat_write_hints(forms, tables_of, text=""):
    """Hints of the flat write clarification: one per readable file with something to write (`tables_of(form)`: its
    declared tables, or the call table for a recording with approved values). Files whose title holds a word of the
    request come first. A file with rows of several tables writes them all, as the writer does; no label repeats."""
    asked = {k for _, k in _words(text)}
    hints, seen = [], set()
    for f in sorted(forms, key=lambda f: not asked & {k for _, k in _words(re.sub(r"[_.-]", " ", f.get("title") or ""))}):
        tables = tables_of(f)
        if not f.get("title") or not tables or f["title"] in seen:
            continue
        seen.add(f["title"])
        one = tables[0] if len(tables) == 1 and f.get("kind") != "audio" else None
        hints.append({"label": f["title"], "detail": ROWS_FOR.format(table=", ".join(tables)), "text": _write_line([f], one)})
    return {"hints": hints[:6], "hintKind": "choice"} if hints else {}


def _shown_hints(req, result):
    """The result without a hint or note that masking would change: a clicked text must never send a masked value,
    and a note of masked values tells nothing. An admin reads identifiers, so keeps them all."""
    hints = [h for h in result.get("hints") or () if req.masked(h) == h]
    if hints:
        result["hints"] = hints
    else:
        result.pop("hints", None)
        result.pop("hintKind", None)
    if "hintNote" in result and req.masked(result["hintNote"]) != result["hintNote"]:
        result.pop("hintNote")
    return result


_ALL = re.compile(r"^\s*(all|all of (these|them|those)|all (these|those)|these|those|全部|全部这些|这些|都要)\s*[.!。！]?\s*$", re.I)


def _all_of_these(req, principal, workspace, text, history, forms):
    """The write route of "all of these" right after a clarification: the files it found with rows of its table,
    found again by code from the clarified request (the browser sends no file id). None otherwise."""
    last = history[-1] if history else {}
    if last.get("status") != "clarify" or not _ALL.match(text):
        return None
    asked = last.get("understood") or last["text"]
    found = _candidates(req, principal, workspace, asked, forms)
    if not found or not found["tables"]:
        return None
    table = found["tables"][0]
    source = {"kind": "form", "sourceId": table["files"][0]["id"], "tableLike": table["name"]}
    if len(table["files"]) > 1:
        source["sourceIds"] = [f["id"] for f in table["files"]]
    return {"tool": "write", "args": {"source": source, "target": "iceberg_cde" if _LAKE.search(asked) else "postgres"}}


# ---------------------------------------------------------------- HTTP

def attach(server, evidence_dir, *, from_store=True):
    """Called once by create_server: the connections store beside the state's evidence folder (`<state-dir>`), the
    writer, and (unless a test injected a target) an Iceberg target that reads that store first, the environment second."""
    store = Connections(Path(evidence_dir).parent / "connections.json") if evidence_dir is not None else None
    server.connections, server.ask_writer, server.ask_progress = store, Writer(), Progress()
    if from_store and store is not None:
        server.iceberg = lambda: load_iceberg(os.environ, store=store)


def _body(req):
    size = int(req.headers.get("Content-Length", "0") or 0)
    if not 0 < size <= MAX_BODY or req.headers.get("Transfer-Encoding") or req.headers.get_content_type() != "application/json":
        raise ValueError
    return json.loads(req.rfile.read(size))


def _ctx(req, principal):
    server = req.server

    def workspace_for(dataset, kind):
        if kind in ("form", "document"):   # template rows read the unscoped model, under the per-source gate (as step 3)
            workspace = server.workspaces.get(dataset)
            return workspace if workspace is not None and chunk_visible(workspace, principal, "") else None
        return next((w for w in req.workspace_payload(principal, include_corrections=False)["datasets"] if w["id"] == dataset), None)

    return SimpleNamespace(principal=principal, pg=server.review_store, workspace=None, workspace_for=workspace_for,
                           details=server.answer_details, connections=server.connections, iceberg=server.iceberg or (lambda: None),
                           jobs=server.iceberg_jobs, physical=server.physical_rows, lock=server.evidence_write_lock,
                           trino=lambda: trino_from_env(os.environ),
                           chat=getattr(server.interpreter, "chat", None) if server.interpreter is not None else None,
                           links=lambda source_id, workspace: req.visible_links(workspace, principal, source_id),
                           chunks=lambda source_id, workspace: server.chunk_index.read(workspace["id"], source_id)
                           if server.chunk_index is not None else [])


def handle(req, method):
    """Every /api/connections* and /api/ask* route. Same-origin is already checked by the caller."""
    parts = urlsplit(req.path)
    path = parts.path
    principal = req.principal()
    if principal is None:
        return
    server, writer, store = req.server, getattr(req.server, "ask_writer", None), getattr(req.server, "connections", None)
    if writer is None or server.review_store is None:
        req.send_json(503, {"error": "Private PostgreSQL access is unavailable"})
        return
    try:
        if method == "GET" and path == "/api/connections":
            if store is None:
                raise _Refusal(503, "The connections store is unavailable")
            req.send_json(200, store.listing(True))
            return
        if method == "GET" and path == "/api/ask/progress":      # read-only: the steps of one in-flight request
            query = parse_qs(parts.query)
            owner = Progress.owner(principal, req.session_token(), (query.get("dataset") or [""])[0])
            out = server.ask_progress.read((query.get("id") or [""])[0], owner)
            if out is None:
                raise _Refusal(404, "No request with this id is in progress for this session")
            req.send_json(200, out)
            return
        if method == "GET" and path == "/api/ask/write/script":
            query = parse_qs(parts.query)
            fp = (query.get("fingerprint") or [""])[0]
            status, out, *name = writer.script(_ctx(req, principal), fp)
            if status != 200:
                req.send_json(status, out)
                return
            req.reply(200, out.encode(), "text/x-python; charset=utf-8",
                      [("Content-Disposition", f'attachment; filename="{name[0]}"')])
            return
        if method != "POST":
            raise _Refusal(404, "Action unavailable")
        try:
            body = _body(req)
        except (ValueError, UnicodeError):
            raise _Refusal(400, "Require a bounded JSON request") from None
        found = re.fullmatch(r"/api/connections/([a-z_]+)(?:/(test|import))?", path)
        if found:
            _connections(req, principal, store, found[1], found[2], body)
            return
        if path in ("/api/ask/write/preview", "/api/ask/write/confirm", "/api/ask/write/status"):
            action = path.rsplit("/", 1)[1]
            ctx = _ctx(req, principal)
            status, out = writer.job_status(ctx, body) if action == "status" else traced_write(writer, ctx, action, body)
            req.send_json(status, out)
            return
        if path == "/api/ask":
            _ask(req, principal, writer, store, body)
            return
        raise _Refusal(404, "Action unavailable")
    except _Refusal as error:
        req.send_json(error.status, {"error": str(error)})
    except PilotDatabaseError:
        req.send_json(503, {"error": "Private PostgreSQL access is unavailable"})


def _connections(req, principal, store, target, action, body):
    if principal["role"] != "admin":
        raise _Refusal(403, "Changing or testing a connection requires an admin account")
    if store is None:
        raise _Refusal(503, "The connections store is unavailable")
    try:
        if action is None:
            if not isinstance(body, dict) or set(body) != {"fields"}:
                raise ValueError("Require {\"fields\": {...}}")
            req.send_json(200, store.save(target, body["fields"]))
        elif action == "import":
            if body != {"from": "workspace"}:
                raise ValueError("Require {\"from\": \"workspace\"}")
            req.send_json(200, store.import_workspace(target))
        elif target == POSTGRES:
            try:
                ok = req.server.review_store.sql("SELECT 1;") == "1"
            except PilotDatabaseError:
                ok = False
            result = {"ok": ok, "checks": [{"name": "Local database", "ok": ok, "required": True,
                                            "message": "The private PostgreSQL answered." if ok else "The private PostgreSQL did not answer."}]}
            store.record_test(POSTGRES, result)
            req.send_json(200, result)
        elif target == ICEBERG:
            result = run_test(store.values(ICEBERG))
            store.record_test(ICEBERG, result)
            req.send_json(200, result)
        else:
            raise KeyError(target)
    except KeyError:
        raise _Refusal(404, "Unknown connection") from None
    except ValueError as error:
        raise _Refusal(400, str(error)) from None


def _ask(req, principal, writer, store, body):
    if (not isinstance(body, dict) or not {"dataset", "text"} <= set(body) <= {"dataset", "text", "context", "requestId", "history", "hint", "definition"}
            or not isinstance(body["dataset"], str) or not isinstance(body["text"], str) or len(body["text"]) > 2000
            or not isinstance(body.get("definition") or "", str) or len(body.get("definition") or "") > 300
            or not isinstance(body.get("context") or {}, dict) or not isinstance(body.get("history") or [], list)):
        raise _Refusal(400, "Require a dataset and a text of up to 2,000 characters")
    dataset = body["dataset"]
    raw = req.server.workspaces.get(dataset)
    if dataset not in principal["datasets"] or raw is None or not chunk_visible(raw, principal, ""):
        raise _Refusal(403, "Dataset access withheld for this account")
    context = body.get("context") or {}
    forms = [{"id": s["id"], "title": s.get("title"), "kind": s.get("kind")} for s in raw["sources"]
             if chunk_visible(raw, principal, s["id"])]
    routing = {"lastAnswer": context.get("lastAnswer"), "lastForm": context.get("lastForm"), "forms": forms}
    chat, history = getattr(req.server.interpreter, "chat", None), _history(body.get("history"))
    english = standalone = note = None
    accepted = _all_of_these(req, principal, raw, body["text"], history, forms)
    try:        # Z7: a Chinese text in Chinese mode is routed and answered in its checked English form
        # a clicked clarification hint is a standalone question built by code: read as sent, never rewritten
        if accepted is None and body.get("hint") is not True and ui_lang.follows(body["text"], history):
            english, standalone, note = _follow_up(chat, body["text"], history, routing)
        if english is None and accepted is None:
            english = ui_lang.english(chat, body["text"])
    except ui_lang.NotUnderstood:
        trace = start_trace(req, principal, dataset, ask_trace.ANSWER, body.get("requestId"), "answer", WHY["answer"])
        result = ui_lang.refusal(body["text"], dataset)
        req.send_json(200, {"tool": "answer", "result": result, "trace": mask_extracted(ask_trace.answer_steps(trace, result))})
        return
    shown = {"question": body["text"], "questionEnglish": english} if english else {}
    if body.get("hint") is True and ui_lang.current() == ui_lang.ZH:
        # a clicked choice shows its Chinese label as the question; the English it sent is what was asked
        shown = {"question": body["text"], "questionEnglish": body["text"]}
    # the definition a clicked choice carries (plan fixed to it); a typed question never fixes one
    chosen_definition = body.get("definition") if body.get("hint") is True else None
    if standalone:
        shown = {**shown, "question": body["text"], "questionStandalone": standalone}
    if chat is not None:       # the declared tables, for a model-routed action only (the rules never need them)
        try:
            routing["tables"] = [t["name"] for t in declared(raw)]
        except (KeyError, StopIteration, OSError):
            routing["tables"] = []
    chosen, how = (accepted, "clarified") if accepted else routed(chat, standalone or english or body["text"], routing,
                                                                     lambda like: _one_table(raw, like))
    tool, args = chosen["tool"], chosen["args"]
    # an account without the write right gets the writer's refusal at once: never a clarification or hints to write
    denied = (tool == "write" or "clarify" in args) and not may_write(principal)
    if denied:
        tool = "write"
    steps = {"connections": ask_trace.CONNECTIONS, "semantic": ask_trace.SEMANTIC, "write": ask_trace.PREVIEW}.get(tool, ask_trace.ANSWER)
    trace = start_trace(req, principal, dataset, steps, body.get("requestId"), tool, WHY[tool])
    if how != "rules":         # a model was asked: the route detail says so (no key when the rules alone chose)
        trace.note(route=how, why=WHY[how])
    if standalone or note:
        trace.note(**({"standalone": standalone} if standalone else {"context": note}))
    if tool == "connections":
        with ask_trace.active(trace):
            ask_trace.mark("connections")
            listing = store.listing(True) if store else {"targets": []}
        trace_steps = ask_trace.finish(trace, connections={"targets": len(listing["targets"]),
                                                           "configured": sum(bool(t.get("configured")) for t in listing["targets"])})
        req.send_json(200, {"tool": tool, "result": listing, "trace": trace_steps, **shown})
    elif tool == "semantic":
        with ask_trace.active(trace):
            ask_trace.mark("lookup")
            result = mask_extracted(semantic(req, principal, raw, args["term"]))
        trace_steps = ask_trace.finish(trace, lookup={"term": result["term"], "concepts": len(result["concepts"]),
                                                      "definitions": len(result["definitions"]), "passages": len(result["passages"])})
        req.send_json(200, {"tool": tool, "result": result, "trace": mask_extracted(trace_steps), **shown})
    elif tool == "write":
        if denied:
            result = {"error": WRITE_DENIED}
            req.send_json(403, {"tool": tool, "result": result, "trace": write_steps(trace, "preview", False, result), **shown})
            return
        if args["source"] is None:
            result = {"error": "Nothing to write yet. Ask a question or name a form first."}
            req.send_json(200, {"tool": tool, "result": result, "trace": write_steps(trace, "preview", False, result), **shown})
            return
        status, out = traced_write(writer, _ctx(req, principal), "preview",
                                   {"dataset": dataset, "source": args["source"], "target": args["target"]}, trace)
        req.send_json(status, {"tool": tool, "result": {k: v for k, v in out.items() if k != "trace"}, "trace": out["trace"], **shown})
    else:
        with ask_trace.active(trace):
            # a clarification names what the semantic layer finds for the request's words, else every readable file
            found = _candidates(req, principal, raw, args["question"], forms) if "clarify" in args else None
            asked = clarification(found) if found else args.get("clarify")
            result = _answer(req, principal, writer, dataset, args["question"], chosen_definition) if "clarify" not in args \
                else {"status": "clarify", "reason": asked, "summary": asked, "question": args["question"],
                      "dataset": dataset, "definitions": [], "facts": [], "citations": [], "conclusion": None,
                      "entities": [], "notes": [],         # the answer path's clarification shape: an action to name
                      **({"candidates": mask_extracted({k: found[k] for k in ("concepts", "files", "tables")})} if found else {}),
                      "semantics": _clarify_semantics(req, raw, principal, found),
                      **_clarify_hints(req, principal, raw, found, forms, args["question"])}
            if result.get("status") == "clarify":
                result = _shown_hints(req, result)
        trace_steps = mask_extracted(ask_trace.answer_steps(trace, result))
        req.send_json(200, {"tool": "answer", "result": {**result, **shown}, "trace": trace_steps, **shown})


def _clarify_hints(req, principal, raw, found, forms, question):
    """Hints of a write clarification; {} when they cannot be built (the clarification stands without them)."""
    try:
        if found:
            return _write_hints(found)
        links = req.visible_links(raw, principal)

        def tables_of(f):
            if f.get("kind") != "audio":
                return _tables_of(raw, links, f["id"])
            approved = any(k["source_id"] == f["id"] and k.get("relation") == "states_value_of" and k.get("status") == "corrected"
                           and k.get("stated_value") not in (None, "") for k in links)
            return [CALL_TABLE] if approved else []
        return _flat_write_hints(forms, tables_of, question)
    except Exception:
        return {}


HISTORY_TURNS, HISTORY_FIELD = 6, 300


def _history(items):
    """The last six earlier turns of a chat session, as data: {text, status, said?, source?, understood?}, each string cut to 300
    characters. Anything else in an item, and an item without a text, is dropped. The browser holds these, so they
    are editable by the person: they only shape the question text, never the dataset, the account or a source gate."""
    turns = []
    for item in items or []:
        if isinstance(item, dict) and isinstance(item.get("text"), str) and item["text"].strip():
            turns.append({k: item[k][:HISTORY_FIELD] for k in ("text", "status", "said", "source", "understood") if isinstance(item.get(k), str)})
    return turns[-HISTORY_TURNS:]


def _follow_up(chat, text, history, routing):
    """(english, standalone, note) for a text that may lean on its earlier turns: one model call, checked by code.
    A write, a term or the connections are routed as typed (a write keeps lastAnswer and lastForm). A rewrite that
    fails a check is dropped with `note`; a Chinese text then takes the Z7 path (english None)."""
    zh = ui_lang.is_chinese(text)
    if not zh and route(text, routing)["tool"] != "answer":
        return None, None, None
    out = ui_lang.standalone(chat, text, history) if chat is not None else None
    english = out["english"] if out and zh else None
    if english and route(english, routing)["tool"] != "answer":
        return english, None, None
    found = out and out["standalone"]
    # A question never turns into a write through a rewrite. An answer to a clarification may: the person was asked
    # which files or table, and the write is still only a preview.
    asked = bool(history) and history[-1].get("status") == "clarify"
    if found and route(found, routing)["tool"] not in (("answer", "semantic", "write") if asked else ("answer", "semantic")):
        found = None
    if not found:
        return english, None, ui_lang.NOT_STANDALONE
    return english, (None if found == (english or text) else found), None


def _answer(req, principal, writer, dataset, question, definition=None):
    """The `/api/answer` logic for one question; each fact handle remembers the question for a later write."""
    from . import answer_path                      # looked up at call time, so the route and its tests share one name
    from .workspace_server import entity_keys_of
    if not question:
        return {"status": "clarify", "error": "Ask a question."}
    server = req.server
    ask_trace.mark("understand")      # loading the account's view of the model is part of understanding the question
    workspace = next((w for w in req.workspace_payload(principal, include_corrections=False)["datasets"] if w["id"] == dataset), None)
    if workspace is None:
        raise _Refusal(403, "Dataset access withheld for this account")
    try:
        reply = answer_path.answer(question, workspace, principal, database=server.review_store, chunks=server.chunk_index,
                       links=(lambda: req.visible_links(workspace, principal)) if server.evidence_links is not None else None,
                       entity_keys=entity_keys_of(server, workspace), chat=getattr(server.interpreter, "chat", None),
                       embedder=server.chunk_embedder, definition=definition,
                       # C1/D3, as in `/api/answer`: approved concepts of the latest release, scoped to this reader
                       catalog=answer_path.published_catalog(getattr(server, "releases", None), workspace["id"],
                                                             lambda source: chunk_visible(workspace, principal, source)))
    except (OSError, ValueError, KeyError):
        raise _Refusal(500, "Internal server error") from None
    held = server.answer_details.register(reply, dataset, principal)
    for fact in held.get("facts") or []:
        if fact.get("detail"):
            writer.remember_question(fact["detail"]["id"], question)
    return mask_extracted(held)
