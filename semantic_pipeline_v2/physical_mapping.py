"""Output C, physical mapping (finance only). TEMPLATE-BOUND: it works only for a document type whose table the
finance model declares in advance (a dataset with `origin: extracted`). Outputs A and B are the general mapping.

Flow: ingest of a templated form adds deterministic evidence links (`template_links`), method `template:<type>`,
relation `states_value_of`, to the same store as the model's links. The preview builds rows only from those links.
A confirm by an admin writes the rows to the private PostgreSQL table, each cell keeping its source, revision, chunk id
and quoted span in `v2_physical_rows`. A value that fails the column's type or unit check is `rejected` and not written.
The stored cell holds the NRIC-masked value. Human corrections (`evidence_links.overlay`) are applied to the template links before the rows are built:
a rejected link shows as a cell "removed by a reviewer", a link remapped to another definition gives no cell, and a
corrected value is checked again and marked `provenance: corrected` with its actor. A correction made after a preview
makes the preview stale: confirm refuses (`StalePreview`) until a new preview.
"""

from semantic_pipeline_v2.model_cache import load_yaml
import datetime
import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

import yaml
from multimedia_rag.fields import mask_text

from .definition_cards import ROOT
from .evidence_links import _record
from .metric_sql import _dataset_schema
from .semantic_store import _jsonb, _lit

METHOD = "template:"
SQL_TYPE = {"String": "text", "Integer": "bigint", "Decimal": "numeric", "Date": "date"}
_DDL = """
    CREATE TABLE IF NOT EXISTS v2_physical_rows (
        row_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        dataset text NOT NULL, source_id text NOT NULL, source_revision text NOT NULL,
        table_name text NOT NULL, row_key text NOT NULL,
        status text NOT NULL CHECK (status IN ('previewed', 'written', 'rejected')),
        cells jsonb NOT NULL, actor text NOT NULL, recorded_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (dataset, source_revision, table_name, row_key));
"""


class StalePreview(ValueError):
    """The stored preview no longer matches the rows built from the links with today's corrections."""


def _ext(node):
    for item in node.get("custom_extensions") or []:
        if item.get("vendor_name") == "HELIOS":
            raw = item.get("data")
            return json.loads(raw) if isinstance(raw, str) else dict(raw or {})
    return {}


@lru_cache(maxsize=4)
def _model(path, mtime):
    return load_yaml(Path(path).read_bytes())


def declared(workspace):
    """The predefined tables of the workspace's model: [{name, template, columns, key, definitions}]."""
    structured = workspace["knowledge"]["structured"]
    source = next(s for s in workspace["sources"] if s["id"] == structured["modelSource"])
    path = ROOT / source["citation"]["path"]
    model = _model(str(path), path.stat().st_mtime_ns)
    tables = []
    for dataset in model["datasets"]:
        data = _ext(dataset)
        if data.get("origin") != "extracted":
            continue
        columns = []
        for field in dataset["fields"]:
            extra = _ext(field)
            columns.append({"name": field["name"], "datatype": field["datatype"], "system": "system" in extra.get("provenance", ""),
                            "currency": extra.get("currency_column"),
                            "definition": f"{workspace['id']}:definition:{model['name']}/field/{dataset['name']}/{field['name']}"})
        tables.append({"name": dataset["name"], "label": data.get("label") or dataset["name"], "template": data["template"],
                       "printed": PRINTED.get(dataset["name"]), "columns": columns, "key": dataset["primary_key"]})
    return tables


# Second template kind: a PRINTED one-page report, read as label-and-value passages ("Label: value" field passages or
# an OCR pipe table "| Label | value |"). Column -> (printed label, value parser). A printed label with no column of
# the same meaning is left out. Kept in code, not in the finance model, so the model revision that the prepared demo
# state is bound to does not change. ponytail: one report layout (the STR); add a map per printed report type.
_STR_REPORT = "SUSPICIOUS TRANSACTION REPORT"
PRINTED = {
    "STR_Transaction": {"match": _STR_REPORT, "type": "printed-report", "labels": {
        "AccountNo": ("Account number", None),
        "AmountOriginalCurrency": ("Total amount involved", "currency"),
        "AmountOriginal": ("Total amount involved", "amount"),
        "StartDate": ("Transaction period", "first_date"),
        "CounterpartyName": ("Counterparty", None),
        "SourceCountry": ("Source / destination", "first_country"),
        "DestinationCountry": ("Source / destination", "second_country")}},
    "STR_AccountEntity": {"match": _STR_REPORT, "type": "printed-report", "labels": {
        "AccNum": ("Account number", None),
        "AccBalCurr": ("Account balance", "currency"),
        "AccBalAmount": ("Account balance", "amount")}},
}
_PAIR = re.compile(r"^[ \t]*\|?[ \t]*\**([A-Za-z][^:|*\n]*?)\**[ \t]*[:|][ \t]*(.*?)[ \t]*\|?[ \t]*$", re.M)
_MONEY = re.compile(r"([A-Z]{3})\s+(.+)")
# ponytail: a few country names only; an unknown name gives no cell. Use an ISO 3166 table when reports name others.
_COUNTRY = {"singapore": "SG", "malaysia": "MY", "indonesia": "ID", "thailand": "TH", "hong kong": "HK", "china": "CN",
            "india": "IN", "united kingdom": "GB", "united states": "US"}


def _first_date(value):
    """'1 October 2026 to 31 October 2026' -> '01/10/2026' (the form's dd/mm/yyyy); otherwise the text, which then
    fails the column's date check."""
    m = re.match(r"\d{1,2} [A-Za-z]+ \d{4}", value)
    try:
        return datetime.datetime.strptime(m[0], "%d %B %Y").strftime("%d/%m/%Y") if m else value
    except ValueError:
        return value


def _country(part):
    part = part.strip()
    return part if re.fullmatch(r"[A-Z]{2}", part) else _COUNTRY.get(part.lower(), "")


_PARSE = {
    None: lambda v: v,
    "currency": lambda v: _MONEY.fullmatch(v)[1] if _MONEY.fullmatch(v) else "",   # no currency: the amount is rejected
    "amount": lambda v: (_MONEY.fullmatch(v)[2] if _MONEY.fullmatch(v) else v).replace(",", ""),
    "first_date": _first_date,
    "first_country": lambda v: _country(v.split("/")[0]),
    "second_country": lambda v: _country(v.split("/")[1]) if v.count("/") == 1 else "",
}


def _form_values(template, columns, chunks):
    paths = "|".join(re.escape(p) for p in template.get("row_paths") or [template["row_path"]])  # one table may read several groups
    line = re.compile(rf"^(?:{paths})\[(\d+)\]/(\w+)\[1\] = (\".*\")$", re.M)
    found = {}
    for chunk in chunks:
        for m in line.finditer(chunk["text"]):
            if m[2] in columns and json.loads(m[3]).strip():   # an empty form field states nothing: no link, no cell
                found[int(m[1]), m[2]] = (chunk, m[0], json.loads(m[3]))
    return found


def _printed_values(printed, columns, chunks):
    """{(1, column): (chunk, quote, value)}: one printed report is one row of each table."""
    stated = {}
    for chunk in chunks:
        for m in _PAIR.finditer(chunk["text"]):
            stated[" ".join(m[1].split()).lower()] = (chunk, m[0].strip(), m[2].strip(" *"))
    found = {}
    for name, (label, parse) in printed["labels"].items():
        if name in columns and label.lower() in stated:
            chunk, quote, raw = stated[label.lower()]
            value = _PARSE[parse](raw).strip()
            if value:
                found[1, name] = (chunk, quote, value)
    return found


_HEADING = re.compile(r"^#+\s+(.+?)\s*$")
_XFA_FIELD = re.compile(r'^xfa:data/(\w+)\[\d+\]/(?:(?:.*/)?(\w+)\[\d+\]/)?(\w+)\[\d+\] = (".*")$')
_RULE = re.compile(r"^[|:\s-]+$")


def labelled_fields(chunks):
    """(title, [{section, label, value, chunk, quote}]) of a label-and-value document, for a discovered table.
    Printed pairs are read with `_PAIR` (field passages, pipe tables), form field lines as XFA `path/Name[1] = "v"`.
    A chunk gives fields only when every line is a pair, a heading or a table rule, so prose is never a field. The
    section is the last `#` heading (a form: the parent group); the title the first heading (a form: its root).
    ponytail: a repeated label within one section keeps its first value (repeated form groups give one row)."""
    from .chunk_index import _label_like
    title, section, fields, seen = None, None, [], set()
    for chunk in chunks:
        pairs, prose = [], False
        for line in (raw.strip() for raw in chunk["text"].split("\n")):
            heading, form, pair = _HEADING.match(line), _XFA_FIELD.match(line), _PAIR.match(line)
            if not line or _RULE.match(line):
                continue
            if heading:
                title, section = title or heading[1], heading[1]
            elif form:
                title = title or form[1]
                pairs.append((form[2] or form[1], form[3], json.loads(form[4]).strip(), line))
            elif pair and _label_like(" ".join(pair[1].split())) and pair[2].strip(" *"):
                pairs.append((section, " ".join(pair[1].split()), pair[2].strip(" *"), line))
            else:
                prose = True
        for where, label, value, quote in [] if prose else pairs:
            if value and (where, label.lower()) not in seen:
                seen.add((where, label.lower()))
                fields.append({"section": where, "label": label, "value": value, "chunk": chunk, "quote": quote})
    return title, fields


def check_cell(datatype, value, *, currency=None, needs_currency=False):
    """(typed value, None) or (None, reason). A String is stored NRIC-masked."""
    value = (value or "").strip()
    if not value:
        return None, "empty value"
    if datatype == "String":
        return mask_text(value), None
    if datatype == "Integer":
        return (value, None) if re.fullmatch(r"-?\d+", value) else (None, "not an integer")
    if datatype == "Decimal":
        if not re.fullmatch(r"-?\d+(\.\d+)?", value):
            return None, "not a decimal number"
        if needs_currency and not re.fullmatch(r"[A-Z]{3}", currency or ""):
            return None, "no valid ISO currency stated for this amount"
        return value, None
    if datatype == "Date":
        try:
            return datetime.datetime.strptime(value, "%d/%m/%Y").date().isoformat(), None
        except ValueError:
            return None, "not a dd/mm/yyyy date"
    return None, f"unsupported column type {datatype}"


# ---------------------------------------------------------------- links

def template_links(workspace, chunks):
    """Deterministic links: a form field whose name is exactly a declared column, or a printed label the table maps
    to a column (`PRINTED`). Not a model interpretation."""
    links = []
    text = "\n".join(c["text"] for c in chunks)
    for table in declared(workspace):
        template, printed = table["template"], table.get("printed")
        columns = {c["name"]: c for c in table["columns"] if not c["system"]}
        if all(f"{path} = {json.dumps(value)}" in text for path, value in template["match"].items()):
            kind, found = template["type"], _form_values(template, columns, chunks)
        elif printed and printed["match"] in text:
            kind, found = printed["type"], _printed_values(printed, columns, chunks)
        else:
            continue
        for (row, name), (chunk, quote, value) in sorted(found.items()):
            column = columns[name]
            currency = found.get((row, column["currency"]), (0, 0, None))[2] if column["currency"] else None
            links.append(_record(chunk, dict(
                definition_id=column["definition"], canonical_definition_id=column["definition"], relation="states_value_of",
                stated_value=value, unit=currency, period=None, entity_key="unresolved", entity_keys=[], interpretation=None,
                candidates=[column["definition"]], scores={}, method=METHOD + kind, model_revision=None,
                status="proposed", notes={"table": table["name"], "column": name, "row": row, "quote": quote})))
    return links


def merge_links(model_links, template):
    """Template links win over a model link for the same chunk and definition; a chunk they cover has no `none` record."""
    taken = {(k["chunk_id"], k["definition_id"]) for k in template}
    covered = {k["chunk_id"] for k in template}
    keys = {k["chunk_id"]: k for k in model_links if k.get("entity_keys")}   # what the chunk states is not lost with its record
    return [k for k in model_links if (k["chunk_id"], k["definition_id"]) not in taken
            and not (k["relation"] == "none" and k["chunk_id"] in covered)] + [
        {**k, "entity_key": keys[k["chunk_id"]]["entity_key"], "entity_keys": keys[k["chunk_id"]]["entity_keys"]}
        if k["chunk_id"] in keys and not k.get("entity_keys") else k for k in template]


def relink(workspace, store, index, source_id):
    """Recompute the template links of one stored source from its stored chunks; its model links stay. No model call.
    For a source ingested before its template kind existed. Returns the number of template links."""
    dataset = workspace["id"]
    kept = [k for k in store.read(dataset, source_id) if not k["method"].startswith(METHOD)]
    template = template_links(workspace, index.read(dataset, source_id))
    store.replace(dataset, source_id, merge_links(kept, template))
    for k in template:
        index.add_labels(k["chunk_id"], [k["definition_id"]])   # as ingest does
    return len(template)


# ---------------------------------------------------------------- rows

def build_rows(workspace, links):
    """Preview rows of every declared table, from template links only: [{table, rowKey, status, cells, keys}].
    `links` may carry human corrections (see the module note)."""
    tables = {t["name"]: t for t in declared(workspace)}
    grouped = {}
    for k in links:
        if (k["method"].startswith(METHOD) and k["relation"] in ("states_value_of", "is_about")
                and k["notes"].get("table") in tables and (k["status"] in ("proposed", "corrected")
                                                           or (k["status"] == "rejected" and "originalStatus" in k["notes"]))):
            grouped.setdefault((k["notes"]["table"], k["notes"]["row"], k["source_revision"]), []).append(k)
    rows = []
    for (name, number, revision), group in sorted(grouped.items()):
        columns = {c["name"]: c for c in tables[name]["columns"]}
        # a link remapped to another definition (relation or definition changed) no longer states this column
        by_column = {k["notes"]["column"]: k for k in group if k["status"] == "rejected" or (
            k["relation"] == "states_value_of" and k["definition_id"] == columns[k["notes"]["column"]]["definition"])}
        cells = []
        for column in columns.values():
            k = by_column.get(column["name"])
            if k is None:
                continue
            quote = k["notes"]["quote"]
            start = k["span"]["start"] + k["span"]["text"].find(quote)
            where = {"sourceId": k["source_id"], "sourceRevision": k["source_revision"], "chunkId": k["chunk_id"],
                     "quote": mask_text(quote), "span": {"start": start, "end": start + len(quote)}}
            if k["status"] == "rejected":
                cells.append({"column": column["name"], "value": None, "typed": None, "status": "removed",
                              "reason": "removed by a reviewer", "unit": None, "provenance": "corrected",
                              "actor": k.get("actor"), **where})
                continue
            currency = by_column[column["currency"]]["stated_value"] if (
                column["currency"] in by_column and by_column[column["currency"]]["status"] != "rejected") else None
            typed, reason = check_cell(column["datatype"], k["stated_value"], currency=currency,
                                       needs_currency=bool(column["currency"]))
            cells.append({"column": column["name"], "value": mask_text(k["stated_value"]), "typed": typed,
                          "status": "ok" if reason is None else "rejected", "reason": reason, "unit": k["unit"], **where,
                          **({"provenance": "corrected", "actor": k.get("actor")} if k["status"] == "corrected" else {})})
        rows.append({"table": name, "rowKey": str(number), "revision": revision, "number": number, "cells": cells,
                     "status": "previewed" if any(c["status"] == "ok" for c in cells) else "rejected"})
    return rows


def row_values(workspace, row):
    """{column: typed value} written for one stored row: its ok cells, the source revision and the row position.
    PostgreSQL and the Iceberg target both write exactly these values."""
    table = next(t for t in declared(workspace) if t["name"] == row["table"])
    ok = {c["column"]: c["typed"] for c in row["cells"] if c["status"] == "ok"}
    position = next(c for c in table["key"] if c != "SourceRevision")   # the key is the revision plus the group position
    ok.update(SourceRevision=row["cells"][0]["sourceRevision"], **{position: row["rowKey"]})
    return ok


def fingerprint(rows, binding=""):
    """Digest of the rows a confirm writes (previewed or written; status itself excluded) and of the target settings.
    A confirm that names it writes only when the rows and settings are still the previewed ones."""
    kept = [[r["table"], r["rowKey"], r["cells"]] for r in rows if r["status"] in ("previewed", "written")]
    return hashlib.sha256((json.dumps(sorted(kept, key=lambda r: (r[0], r[1])), sort_keys=True) + binding).encode()).hexdigest()


class PhysicalRows:
    def __init__(self, database):
        self.database = database
        database.sql(_DDL)

    def read(self, dataset, source_id):
        out = self.database.sql(f"""
            SELECT COALESCE(jsonb_agg(jsonb_build_object('table', table_name, 'rowKey', row_key, 'status', status,
                'cells', cells, 'actor', actor, 'recordedAt', recorded_at) ORDER BY table_name, row_key::int), '[]'::jsonb)
            FROM v2_physical_rows WHERE dataset = {_lit(dataset)} AND source_id = {_lit(source_id)};""")
        return json.loads(out)

    def preview(self, workspace, source_id, links, actor):
        """Store the previewed rows of one source. A written row stays; nothing reaches a target table."""
        rows = build_rows(workspace, links)
        dataset = workspace["id"]
        values = ",\n".join("(" + ", ".join((_lit(dataset), _lit(source_id), _lit(r["revision"]), _lit(r["table"]),
                                              _lit(r["rowKey"]), _lit(r["status"]), _jsonb(r["cells"]), _lit(actor))) + ")"
                            for r in rows)
        insert = ("INSERT INTO v2_physical_rows (dataset, source_id, source_revision, table_name, row_key, status, cells, actor)"
                  f" VALUES {values} ON CONFLICT (dataset, source_revision, table_name, row_key) DO NOTHING;") if rows else ""
        self.database.sql(f"BEGIN;\nDELETE FROM v2_physical_rows WHERE dataset = {_lit(dataset)} AND source_id = {_lit(source_id)}"
                          f" AND status <> 'written';\n{insert}\nCOMMIT;")
        return self.read(dataset, source_id)

    def confirm(self, workspace, source_id, actor, links=None):
        """Write the previewed rows of one source to the target tables, once. Returns the rows now stored.
        With `links` (corrections applied), a preview that differs from the rows they build is stale: StalePreview."""
        if links is not None:
            now = {(r["table"], r["rowKey"]): r["cells"] for r in build_rows(workspace, links)}
            for row in self.read(workspace["id"], source_id):
                if row["status"] == "previewed" and json.loads(json.dumps(now.get((row["table"], row["rowKey"])))) != row["cells"]:
                    raise StalePreview("A correction was made after the preview. Preview the rows again.")
        dataset, schema = workspace["id"], _dataset_schema(workspace["id"])
        tables = {t["name"]: t for t in declared(workspace)}
        script = ["BEGIN;", f"CREATE SCHEMA IF NOT EXISTS {schema};"]
        for table in tables.values():
            columns = ", ".join(f"{c['name'].lower()} {SQL_TYPE[c['datatype']]}" for c in table["columns"])
            key = ", ".join(c.lower() for c in table["key"])
            script.append(f"CREATE TABLE IF NOT EXISTS {schema}.{table['name'].lower()} ({columns}, PRIMARY KEY ({key}));")
        pending = [r for r in self.read(dataset, source_id) if r["status"] == "previewed"]
        for row in pending:
            table = tables[row["table"]]
            ok = row_values(workspace, row)
            names = [c for c in (col["name"] for col in table["columns"]) if c in ok]
            kinds = {c["name"]: SQL_TYPE[c["datatype"]] for c in table["columns"]}
            script.append(f"INSERT INTO {schema}.{table['name'].lower()} ({', '.join(n.lower() for n in names)}) VALUES "
                          f"({', '.join(_lit(str(ok[n])) + '::' + kinds[n] for n in names)}) ON CONFLICT DO NOTHING;")
            script.append(f"UPDATE v2_physical_rows SET status = 'written', actor = {_lit(actor)}, recorded_at = now() "
                          f"WHERE dataset = {_lit(dataset)} AND source_id = {_lit(source_id)} AND table_name = {_lit(row['table'])}"
                          f" AND row_key = {_lit(row['rowKey'])} AND status = 'previewed';")
        script.append("COMMIT;")
        self.database.sql("\n".join(script))
        return self.read(dataset, source_id)


def main(argv=None):
    """python -m semantic_pipeline_v2.physical_mapping --state-dir DIR [--dataset D] [SOURCE ...]
    Relink stored sources (all sources with stored links when none is named). The server must be stopped: the state
    is locked while it runs."""
    import argparse
    import os
    from .chunk_index import ChunkIndex
    from .core import EmbeddingSpace
    from .evidence_links import EvidenceLinks
    from .workspace import load_workspace, review_database
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--pg-bin", type=Path, default=os.environ.get("V2_PG_BIN"))
    parser.add_argument("--dataset", default="financial-services")
    parser.add_argument("source", nargs="*")
    args = parser.parse_args(argv)
    root = args.state_dir / "mapped-evidence" / "chunk-index"
    index = ChunkIndex(root, EmbeddingSpace(**json.loads((root / "embedding-space.json").read_text())))
    with review_database(args.state_dir, args.pg_bin) as database:
        workspace, store = load_workspace(args.dataset), EvidenceLinks(database)
        for source in args.source or sorted({k["source_id"] for k in store.read(args.dataset)}):
            print(f"{source}: {relink(workspace, store, index, source)} template links", flush=True)


if __name__ == "__main__":
    main()
