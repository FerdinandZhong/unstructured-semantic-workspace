"""Entity 360 (2026-11-22): one report on one customer, or on one entity known only from documents. Read-only.

GET /api/entity/search?dataset&q and POST /api/entity/report {dataset, entity}. Financial Services only.

Entities keep the key they are known by and are never merged: `customer:<CustomerID>` (a row of the Customers table),
`entity:<key>` (an entity of the workspace registry, scoped to its documents) and `stated:<id>` (a Customer ID a
document states that no Customers row holds). A name in a document never resolves to a Customer ID.

Sections and their sources:
  profile, holdings   SQL. Every query starts from the compiler's guarded SQL (`metric_sql.query_field`); breakdowns
                      change only its projection and add a GROUP BY, as `answer_detail` does. Totals are SQL sums.
  evidence            Output A links that carry this entity's key (how: stated, derived, inherited), or, for a
                      registry entity, the links of its scoped documents (`scoped`) and passages naming it (`named`).
  concepts            Output B concepts whose evidence passages are among this entity's passages.
  comparisons         A value a passage states beside the value the table records, compared by code.
  summary             Model text that cites the facts and passages above; code drops any sentence whose numbers or
                      names are not in what it cites. A failed call leaves the rest of the report.
Every link, passage and concept passes `chunk_visible` for the account; public `extraction_only` files are never
attached to an entity. Reports are cached in memory per (dataset, entity, account, link revision).
"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
from collections import OrderedDict
from threading import Lock
from urllib.parse import parse_qs, urlsplit

import sqlglot
import sqlglot.expressions as sge

from . import answer_detail
from ._pilot_postgres import PilotDatabaseError
from .chunk_index import visible as chunk_visible
from .extracted_fields import mask_extracted
from .metric_sql import ClarificationNeeded, Filter, MetricQueryError, SourceWithheld, column_range, query_field

DATASET = "financial-services"
SAMPLE = "entity:jade-logistics"
MAX_RESULTS = 20
MAX_BODY = 4096
MAX_PASSAGES = 12            # passages offered to the summary model
SUMMARY_LABEL = "Generated summary, for review"
CACHE, _CACHE_LOCK, CACHE_SIZE = OrderedDict(), Lock(), 200     # ponytail: in memory; a restart recomputes
SYSTEM = {"SourceBatch"}
COMMON = {"I", "A"}


class _Refusal(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------- the model and its guarded SQL

class _Model:
    """Tables and fields of the dataset's semantic model, from the workspace."""

    def __init__(self, ws):
        st = ws["knowledge"]["structured"]
        self.ws, self.prefix = ws, f"{ws['id']}:definition:{st['namespace']}/field/"
        self.columns, self.titles = {}, {}
        for f in st["fields"]:
            table, column = f["name"].split("/", 1)
            self.columns.setdefault(table, []).append(column)
            self.titles[table, column] = f.get("title") or column
        self.dates = {}

    def fid(self, table, column):
        return f"{self.prefix}{table}/{column}"

    def latest(self, pg, table):
        """The latest business date of a snapshot table, else None."""
        if "BusinessDate" not in self.columns.get(table, ()):
            return None
        if table not in self.dates:
            self.dates[table] = column_range(pg, self.ws["id"], table, "BusinessDate")[1]
        return self.dates[table]

    def guarded(self, pg, table, column, filters, aggregate="count", *, dated=True):
        """(fact, tree, conditions, aggregate node) of the compiler's guarded SQL for one aggregate."""
        date = self.latest(pg, table) if dated else None
        fact = query_field(self.ws, self.fid(table, column), [Filter(field=c, value=v) for c, v in filters], pg,
                           dates=(date,) if date else (), aggregate=aggregate)
        tree, _, _, conds, agg = answer_detail._parse(fact["sql"])
        return fact, tree, conds, agg

    @staticmethod
    def select(pg, tree, conds, projections, *, group=0, limit=500):
        """Rows of the guarded query with its projection replaced. Projections are model column expressions."""
        q = tree.select(*[(p if isinstance(p, sge.Expression) else sqlglot.parse_one(p, read="postgres")).as_(a, quoted=True)
                          for p, a in projections], append=False)
        q.set("where", None)
        q.set("limit", None)
        if conds:
            q = q.where(sge.and_(*conds))
        if group:
            q = q.group_by(*[str(i) for i in range(1, group + 1)])
        q = q.order_by(*[str(i) for i in range(1, max(group, 1) + 1)]).limit(limit)
        return answer_detail._json(pg, q.sql(dialect="postgres"))

    def row(self, pg, table, filters):
        """The business fields of the one row of `table` the filters select (latest snapshot), as [{definition, column,
        value}], or [] when no row."""
        key = filters[0][0]
        _, tree, conds, _ = self.guarded(pg, table, key, filters)
        columns = [c for c in self.columns[table] if c not in SYSTEM]
        rows = self.select(pg, tree, conds, [(f"{table.lower()}.{c.lower()}", c) for c in columns], limit=5)
        return [[{"definition": self.fid(table, c), "column": c, "value": r[c]} for c in columns] for r in rows]


def _customer_ids(model, pg):
    """{CustomerID: (type, segment)} at the latest business date, from the guarded Customers query."""
    try:
        _, tree, conds, _ = model.guarded(pg, "Customers", "CustomerID", [])
    except ValueError:      # the model's definitions are not imported in this state
        raise _Refusal(503, "The customer tables are not loaded in this workspace.") from None
    rows = model.select(pg, tree, conds, [("customers.customerid", "id"), ("customers.customertype", "type"),
                                          ("customers.customersegment", "segment")], limit=10000)
    return {r["id"]: (r["type"], r["segment"]) for r in rows}


# ---------------------------------------------------------------- entities

def _public(ws, source_id):
    source = next((s for s in ws["sources"] if s["id"] == source_id), {})
    return source.get("scope") == "extraction_only" or str(source.get("kind", "")).startswith("public")


def _visible_links(raw, ws, principal, links):
    return [k for k in links if chunk_visible(raw, principal, k["source_id"]) and not _public(ws, k["source_id"])]


def _parts(ident):
    """(table, column) of a field definition id, else (table or None, None)."""
    tail = str(ident or "").split("/")
    if "field" in tail[:-2]:
        return tail[-2], tail[-1]
    return (tail[-1] if "dataset" in tail[:-1] else None), None


def _stated_ids(links, customers):
    """{stated Customer ID: [source ids]} that a passage states and no Customers row holds."""
    out = {}
    for k in links:
        if k["relation"] == "states_value_of" and _parts(k["definition_id"])[1] == "CustomerID" and k.get("stated_value") \
                and k["stated_value"] not in customers and re.fullmatch(r"[A-Za-z0-9-]{3,40}", k["stated_value"]):
            out.setdefault(k["stated_value"], []).append(k["source_id"])
    return out


def _similar(a, b):
    """True when two different IDs have the same letters and the same number (CUS48271, CUS048271)."""
    x, y = re.fullmatch(r"([A-Za-z]*)0*(\d+)", a), re.fullmatch(r"([A-Za-z]*)0*(\d+)", b)
    return a != b and bool(x and y) and x[1].upper() == y[1].upper() and x[2] == y[2]


def _registry(ws):
    return {e["id"].split(":entity:", 1)[1]: e for e in ws.get("entities") or []}


def search(model, pg, ws, links, q):
    customers = _customer_ids(model, pg)
    wanted = q.strip().casefold()
    found = [{"id": "entity:" + k, "kind": "document", "label": e["title"], "detail": "Known from documents only"}
             for k, e in _registry(ws).items()]
    found += [{"id": "stated:" + v, "kind": "document", "label": v,
               "detail": "Customer ID stated in a document; no Customers row"} for v in sorted(_stated_ids(links, customers))]
    found += [{"id": "customer:" + c, "kind": "customer", "label": c, "detail": " · ".join(x for x in t if x)}
              for c, t in sorted(customers.items())]
    hits = [r for r in found if not wanted or wanted in r["label"].casefold() or wanted in r["id"].split(":", 1)[1].casefold()]
    return {"results": hits[:MAX_RESULTS], "more": len(hits) > MAX_RESULTS}


def _resolve(entity, model, pg, ws, raw, principal, links):
    kind, _, value = entity.partition(":")
    customers = _customer_ids(model, pg)
    if kind == "customer" and value in customers:
        return {"id": entity, "kind": "customer", "label": value, "key": f"Customers.CustomerID={value}", "value": value,
                "similar": [v for v in _stated_ids(links, customers) if _similar(v, value)]}
    if kind == "entity" and value in _registry(ws):
        e = _registry(ws)[value]
        sources = [s for s in e.get("sourceIds") or [] if chunk_visible(raw, principal, s)]
        return {"id": entity, "kind": "document", "label": e["title"],
                "description": "Known from documents only. No reviewed link connects it to a row of the Customers table.",
                "sources": sources, "registry": True}
    if kind == "stated" and value in _stated_ids(links, customers):
        return {"id": entity, "kind": "document", "label": value, "value": value,
                "similar": [c for c in customers if _similar(c, value)],
                "description": "A Customer ID that a document states. No row of the Customers table holds it."}
    return None


def _matches(entity, links):
    """[(link, how, note)] of the links that belong to the entity."""
    out = []
    if entity["kind"] == "customer":
        for k in links:
            entry = next((e for e in k.get("entity_keys") or [] if e.get("key") == entity["key"]), None)
            if entry:
                out.append((k, entry.get("how") or "stated", entry.get("note")))
    elif entity.get("registry"):
        name = re.compile(r"(?<![A-Za-z])" + r"\s+".join(map(re.escape, entity["label"].split())) + r"(?![A-Za-z])", re.I)
        for k in links:
            if k["source_id"] in entity["sources"]:
                out.append((k, "scoped", "The workspace scopes this document to the entity."))
            elif name.search(k["span"]["text"]):
                out.append((k, "named", "The passage names the entity."))
    else:
        value = entity["value"]
        stating = {k["chunk_id"] for k in links if k["relation"] == "states_value_of"
                   and _parts(k["definition_id"])[1] == "CustomerID" and k.get("stated_value") == value}
        sources = {k["source_id"] for k in links if k["chunk_id"] in stating}
        only = {s for s in sources if set(_stated_ids([k for k in links if k["source_id"] == s], set())) == {value}}
        for k in links:
            if k["chunk_id"] in stating:
                out.append((k, "stated", "The passage states this Customer ID."))
            elif k["source_id"] in only:
                out.append((k, "inherited", "The one Customer ID this document states."))
    return out


# ---------------------------------------------------------------- comparison and summary, in code

def _clean(text):
    return re.sub(r"\s+", " ", str(text)).strip().casefold()


def _number(text):
    found = re.fullmatch(r"(?:[A-Za-z]{3}|[$€£¥])?\s*(-?[\d,]*\.?\d+)\s*(?:[A-Za-z]{3})?", str(text).strip())
    try:
        return float(found[1].replace(",", "")) if found else None
    except ValueError:
        return None


def _date(text):
    text = re.sub(r"(?i)(\d+)(st|nd|rd|th)\b", r"\1", str(text).strip())
    for fmt in ("%Y-%m-%d", "%d %B %Y", "%d %b %Y", "%B %d %Y", "%B %d, %Y"):
        try:
            return datetime.datetime.strptime(text[:10] if fmt == "%Y-%m-%d" else text, fmt).date()
        except ValueError:
            continue
    return None


def compare(stated, recorded):
    """agree | differ | not comparable. Text is compared without case and spacing; numbers and dates by value."""
    if stated is None or recorded is None or str(stated).strip() == "":
        return "not comparable"
    if _clean(stated) == _clean(recorded):
        return "agree"
    s, r = _number(stated), _number(recorded)
    if s is not None and r is not None:
        return "agree" if abs(s - r) < 0.005 else "differ"
    s, r = _date(stated), _date(recorded)
    if s and r:
        return "agree" if s == r else "differ"
    if _number(recorded) is not None or _date(recorded) is not None:
        return "not comparable"
    return "differ"


_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers(text):
    out = set()
    for n in _NUM.findall(text):
        try:
            out.add(float(n.rstrip(",").replace(",", "")))
        except ValueError:
            pass
    return out


def _supported(sentence, cited):
    hay = cited.casefold()
    if not _numbers(sentence) <= _numbers(cited):
        return False
    for i, word in enumerate(re.findall(r"[A-Za-z][A-Za-z0-9-]*", sentence)):
        named = any(c.isdigit() for c in word) or (len(word) > 1 and word.isupper()) or (i > 0 and word[0].isupper())
        if named and word not in COMMON and word.casefold() not in hay:
            return False
    return True


def validate_summary(sentences, sources):
    """(kept, dropped count). A sentence is kept when it cites only known ids and every number and named value in it
    appears in what it cites. `sources` is {id: text}."""
    kept = []
    for s in sentences if isinstance(sentences, list) else []:
        text, cites = (s.get("text"), s.get("cites")) if isinstance(s, dict) else (None, None)
        if not isinstance(text, str) or not text.strip() or not isinstance(cites, list) or not cites \
                or not all(isinstance(c, str) and c in sources for c in cites):
            continue
        text = re.sub(r"\s*[\(\[]\s*[FP]\d+(?:\s*,\s*[FP]\d+)*\s*[\)\]]", "", text).strip()   # ids the model put in the text
        if _supported(text, "\n".join(sources[c] for c in cites)):
            kept.append({"text": text, "cites": list(dict.fromkeys(cites))})
    kept = kept[:6]
    return kept, (len(sentences) if isinstance(sentences, list) else 0) - len(kept)


SYSTEM_PROMPT = """You write a short summary of one bank customer or party for a relationship manager to review.
Use only the facts and passages you are given. Write 3 to 6 short sentences. Each sentence states facts and cites the
ids it uses (F... for database facts, P... for document passages). Every name, code, place, date and number in a
sentence must appear word for word in an item it cites: do not translate, expand, abbreviate or reformat them (write
"SG" if the item says "SG", not "Singapore"). Do not compute new numbers, do not add totals, do not judge, recommend or
speculate. Say "the report alleges" for an allegation, and keep it apart from what the tables record.
Put the ids only in "cites", never in the text. Return JSON only: {"sentences": [{"text": "...", "cites": ["F1", "P2"]}]}"""


def summarize(chat, label, facts, passages):
    sources = {**{f["id"]: f["text"] for f in facts}, **{p["id"]: p["text"] for p in passages}}
    if chat is None:
        return {"available": False, "label": SUMMARY_LABEL, "sentences": [], "dropped": 0,
                "reason": "No model is configured on this server, so no summary was written."}
    if not sources:
        return {"available": False, "label": SUMMARY_LABEL, "sentences": [], "dropped": 0,
                "reason": "There are no facts or passages to summarize."}
    user = json.dumps({"entity": label, "facts": facts, "passages": [{k: p[k] for k in ("id", "source", "text")} for p in passages]},
                      ensure_ascii=False)
    try:
        reply = json.loads(chat.complete(SYSTEM_PROMPT, user))
        kept, dropped = validate_summary(reply.get("sentences") if isinstance(reply, dict) else None, sources)
    except Exception:          # any provider or reply failure: the summary is unavailable, the report stands
        return {"available": False, "label": SUMMARY_LABEL, "sentences": [], "dropped": 0,
                "reason": "The model call failed. The rest of the report does not depend on it."}
    return {"available": True, "label": SUMMARY_LABEL, "sentences": kept, "dropped": dropped,
            **({"reason": "Every sentence the model wrote failed validation."} if not kept else {})}


# ---------------------------------------------------------------- sections

def _profile(model, pg, cid):
    rows = model.row(pg, "Customers", [("CustomerID", cid)])
    rm = []
    if "Customer_RM_Relationship" in model.columns:
        for assignment in model.row(pg, "Customer_RM_Relationship", [("CustomerID", cid)]):
            values = {f["column"]: f["value"] for f in assignment}
            manager = model.row(pg, "RelationshipManagers", [("RMID", values["RMID"])]) if values.get("RMID") else []
            rm.append({**values, "fields": [f for f in assignment if f["column"] not in ("CustomerID", "BusinessDate")]
                       + [f for f in (manager[0] if manager else []) if f["column"] not in ("RMID", "BusinessDate")]})
    return {"rows": bool(rows), "fields": rows[0] if rows else [], "rm": rm, "source": "SQL"}


def _holdings(model, pg, cid):
    fact, tree, conds, agg = model.guarded(pg, "Accounts", "AccountNumber", [("CustomerID", cid)])
    columns = [c for c in ("AccountNumber", "ProductType", "ProductSubType", "Currency", "Status", "OpenDate")
               if c in model.columns["Accounts"]]
    accounts = model.select(pg, tree, conds, [(f"accounts.{c.lower()}", c) for c in columns])
    by_type = model.select(pg, tree, conds, [("accounts.producttype", "type"), ("accounts.status", "status"),
                                             (agg.copy(), "count")], group=2)
    tables = []
    for table in [t for t, cols in model.columns.items() if t.endswith("_Txn")
                  and {"CustomerID", "Amount", "Currency", "TransactionDate"} <= set(cols)]:
        counted, ctree, cconds, cagg = model.guarded(pg, table, "Amount", [("CustomerID", cid)], "count", dated=False)
        if not counted["value"]:
            continue
        summed, stree, sconds, sagg = model.guarded(pg, table, "Amount", [("CustomerID", cid)], "sum", dated=False)
        t = table.lower()
        day = f"CAST({t}.transactiondate AS DATE)"
        currencies = model.select(pg, stree, sconds, [(f"{t}.currency", "currency"), (cagg.copy(), "count"),
                                                      (sagg.copy(), "total")], group=1)
        days = model.select(pg, stree, sconds, [(day, "day"), (cagg.copy(), "count")], group=1)
        amounts = model.select(pg, stree, sconds, [(day, "day"), (f"{t}.currency", "currency"), (sagg.copy(), "total")], group=2)
        for d in days:
            d["day"] = str(d["day"])[:10]
            d["totals"] = {a["currency"]: a["total"] for a in amounts if str(a["day"])[:10] == d["day"]}
        tables.append({"table": table, "definition": model.fid(table, "Amount"), "count": counted["value"],
                       "total": summed["value"], "currencies": currencies, "days": days, "sql": summed["sql"]})
    return {"accounts": {"count": fact["value"], "rows": accounts, "byType": by_type, "businessDate": model.latest(pg, "Accounts")},
            "transactions": tables, "source": "SQL"}


def _recorded(model, pg, entity, link):
    """(recorded value, why not) of the field a passage states, for the row the link's keys identify."""
    table, column = _parts(link["definition_id"])
    if table not in model.columns:
        return None, "The field's table is not in the model."
    filt = next(((e["key"].split("=", 1)[0].split(".", 1)[1], e["key"].split("=", 1)[1])
                 for e in link.get("entity_keys") or [] if e.get("key", "").startswith(table + ".") and "=" in e["key"]), None)
    if filt is None and "CustomerID" in model.columns[table]:
        filt = ("CustomerID", entity["value"])
    if filt is None:
        return None, "No key in the passage identifies a row of this table."
    try:
        date = model.latest(pg, table)
        fact = query_field(model.ws, link["definition_id"], [Filter(field=filt[0], value=filt[1])], pg,
                           dates=(date,) if date else ())
    except ClarificationNeeded:
        return None, "Several rows match, so there is no one recorded value."
    return (None, "No row holds this value.") if fact is None else (fact["value"], None)


def _comparisons(model, pg, entity, matched):
    out = []
    for k, _, _ in matched:
        table, column = _parts(k["definition_id"])
        if k["relation"] != "states_value_of" or column is None or k.get("stated_value") in (None, "") \
                or k["status"] == "rejected" or (table, column) == ("Customers", "CustomerID"):
            continue
        recorded, why = _recorded(model, pg, entity, k)
        out.append({"definition": k["definition_id"], "chunkId": k["chunk_id"], "sourceId": k["source_id"],
                    "sourceTitle": next((s.get("title") for s in model.ws["sources"] if s["id"] == k["source_id"]), None),
                    "stated": k["stated_value"], "unit": k.get("unit"), "recorded": None if recorded is None else str(recorded),
                    "result": compare(k["stated_value"], recorded), **({"why": why} if why else {})})
    return out


def _evidence(ws, matched, comparisons):
    titles = {s["id"]: s.get("title") for s in ws["sources"]}
    against = {(c["chunkId"], c["definition"]): c for c in comparisons}
    groups, seen_none, by_chunk = {}, set(), {}
    for k, how, note in matched:
        about = k["definition_id"] if k["relation"] != "none" and k["status"] != "rejected" and k.get("definition_id") else None
        by_chunk.setdefault(k["chunk_id"], set()).add(about)
    for k, how, note in matched:
        about = k["definition_id"] if k["relation"] != "none" and k["status"] != "rejected" and k.get("definition_id") else None
        if about is None and (k["chunk_id"] in seen_none or by_chunk[k["chunk_id"]] - {None}):
            continue
        if about is None:
            seen_none.add(k["chunk_id"])
        c = against.get((k["chunk_id"], about))
        groups.setdefault(about, []).append({
            "chunkId": k["chunk_id"], "sourceId": k["source_id"], "sourceTitle": titles.get(k["source_id"]) or k["source_id"],
            "text": k["span"]["text"][:1500], "relation": k["relation"] if about else "none",
            "statedValue": k.get("stated_value") if about and k["relation"] == "states_value_of" else None, "unit": k.get("unit"), "period": k.get("period"),
            "how": how, "howNote": note, "status": k["status"],
            **({"recorded": c["recorded"], "result": c["result"]} if c else {})})
    ordered = sorted(groups.items(), key=lambda g: (g[0] is None, str(g[0])))
    count = {}
    for about, items in ordered:
        name = _parts(about)[0] if about else "No field"
        count[name or "Other"] = count.get(name or "Other", 0) + len(items)
    return {"groups": [{"definition": about, "items": items} for about, items in ordered],
            "byTable": [{"table": t, "count": n} for t, n in count.items()], "source": "Output A"}


def _concepts(concepts, chunks):
    out = []
    for c in concepts or []:
        mine = [x for x in (c.get("evidence") or {}).get("chunks") or [] if x in chunks]
        if mine:
            out.append({"id": c["id"], "label": c.get("label"), "status": c.get("status"), "type": c.get("type"),
                        "definition": c.get("definition"), "chunks": mine,
                        "links": [{"definition": k.get("definition_id"), "status": k.get("status"), "predicate": k.get("predicate")}
                                  for k in c.get("links") or [] if k.get("definition_id")]})
    return out


def _table_label(table):
    """CASA_Txn -> CASA transactions; CreditCard_Txn -> Credit card transactions; Treasury_LC_Txn -> Treasury LC transactions."""
    parts = table.split("_")
    txn = parts[-1] == "Txn"
    words = " ".join(re.sub(r"(?<=[a-z])(?=[A-Z])", " ", w) for w in (parts[:-1] if txn else parts)).split()
    words = [w if i == 0 or w.isupper() else w.lower() for i, w in enumerate(words)] + (["transactions"] if txn else [])
    return " ".join(words)


def _facts(model, entity, profile, holdings, comparisons):
    facts = []

    def add(text):
        facts.append({"id": f"F{len(facts) + 1}", "text": text})
        return facts[-1]["id"]

    for f in profile["fields"] if profile else []:
        add(f"Customers › {model.titles.get(('Customers', f['column']), f['column'])}: {f['value']}")
    for rm in profile["rm"] if profile else []:
        add("Relationship manager › " + "; ".join(f"{model.titles.get((f['definition'].split('/')[-2], f['column']), f['column'])}: "
                                                    f"{f['value']}" for f in rm["fields"] if f["value"] not in (None, "")))
    if holdings:
        a = holdings["accounts"]
        add(f"Accounts › number of accounts on {a['businessDate']}: {a['count']}")
        for t in holdings["transactions"]:
            add(f"{_table_label(t['table'])} › number of transactions: {t['count']}")
            for c in t["currencies"]:
                add(f"{_table_label(t['table'])} › in {c['currency']}: {c['count']} transactions, total amount {c['total']} {c['currency']}")
    for c in comparisons:
        table, column = _parts(c["definition"])
        add(f"{model.titles.get((table, column), column)}: a document states {c['stated']}; the table records "
            f"{c['recorded'] if c['recorded'] is not None else 'no single value'}; code finds: {c['result']}")
    return facts


def build(model, pg, ws, raw, principal, entity, links, concepts, chat):
    """The report of one resolved entity. `links` are already visible to the account."""
    matched = _matches(entity, links)
    customer = entity["kind"] == "customer"
    profile = _profile(model, pg, entity["value"]) if customer else {"rows": False, "fields": [], "rm": [], "source": "SQL"}
    holdings = _holdings(model, pg, entity["value"]) if customer else None
    comparisons = _comparisons(model, pg, entity, matched) if customer else []
    evidence = _evidence(ws, matched, comparisons)
    chunks = {k["chunk_id"] for k, _, _ in matched}
    related = _concepts(concepts, chunks)
    facts = _facts(model, entity, profile if customer else None, holdings, comparisons)
    ranked = sorted({k["chunk_id"]: (k, how) for k, how, _ in matched}.values(),
                    key=lambda pair: (pair[0]["relation"] != "states_value_of", pair[1] != "stated"))
    titles = {s["id"]: s.get("title") for s in ws["sources"]}
    passages = [{"id": f"P{n + 1}", "source": titles.get(k["source_id"]) or k["source_id"], "sourceId": k["source_id"],
                 "chunkId": k["chunk_id"], "text": mask_extracted(k["span"]["text"][:700])}
                for n, (k, _) in enumerate(ranked[:MAX_PASSAGES])]
    summary = summarize(chat, entity["label"], facts, passages)
    missing, notes = [], []
    if not customer:
        missing.append("No rows for this entity in the structured tables: it is known from documents only.")
        notes.append(f"No link states that {entity['label']} is a customer of the Customers table. "
                     "It is shown separately (not linked).")
    elif not holdings["transactions"]:
        missing.append("No transactions for this customer in the transaction tables.")
    if customer:
        notes.append("The Customers table holds no customer name. The customer is identified by its Customer ID.")
        if not comparisons:
            missing.append("No passage states a value that the tables also record for this customer.")
    if not evidence["groups"]:
        missing.append("No document passage this account may read is linked to this entity.")
    if not related:
        missing.append("No concept of the catalog draws on this entity's passages.")
    if entity.get("similar"):
        notes.append(f"{', '.join(entity['similar'])} has a similar ID to {entity['label']}. No link states that they are "
                     "the same party, so they are shown separately (not linked).")
    if principal.get("withheld"):
        notes.append("Sources withheld for this account are left out of this report.")
    public = {k: v for k, v in entity.items() if k in ("id", "kind", "label", "description", "key")}
    return {"entity": public, "businessDate": model.latest(pg, "Customers") if customer else None,
            "profile": profile, "holdings": holdings, "evidence": evidence, "concepts": related,
            "comparisons": comparisons, "summary": summary,
            "citations": {**{f["id"]: {"kind": "fact", "text": f["text"]} for f in facts},
                          **{p["id"]: {"kind": "passage", "sourceId": p["sourceId"], "chunkId": p["chunkId"],
                                       "source": p["source"]} for p in passages}},
            "missing": missing, "notes": notes}


# ---------------------------------------------------------------- HTTP

def _body(req):
    size = int(req.headers.get("Content-Length", "0") or 0)
    if not 0 < size <= MAX_BODY or req.headers.get("Transfer-Encoding") or req.headers.get_content_type() != "application/json":
        raise ValueError
    return json.loads(req.rfile.read(size))


def _context(req, principal, dataset):
    """(projected workspace, raw workspace, model) of a permitted dataset, or a refusal."""
    raw = req.server.workspaces.get(dataset) if isinstance(dataset, str) else None
    if raw is None:
        raise _Refusal(404, "Unknown dataset")
    if dataset not in principal["datasets"] or not chunk_visible(raw, principal, ""):
        raise _Refusal(403, "Dataset access withheld for this account")
    if dataset != DATASET:
        raise _Refusal(404, "The customer view is not built for this dataset")
    if req.server.review_store is None:
        raise _Refusal(503, "Private PostgreSQL access is unavailable")
    ws = next(w for w in req.workspace_payload(principal, include_corrections=False)["datasets"] if w["id"] == dataset)
    return ws, raw, _Model(ws)


def _reads(req, raw, principal, *, catalog):
    """(catalog version, concepts, links) the account may read; concepts None when the catalog is unavailable."""
    if catalog and req.server.semantic is not None:
        try:
            return req.catalog_rows(raw, principal)
        except PilotDatabaseError:
            pass
    return None, None, req.visible_links(raw, principal)


def handle(req, method):
    """Every /api/entity* route. Same-origin is already checked by the caller."""
    parts = urlsplit(req.path)
    principal = req.principal()
    if principal is None:
        return
    pg = req.server.review_store
    try:
        if method == "GET" and parts.path == "/api/entity/search":
            try:
                query = parse_qs(parts.query, keep_blank_values=True, strict_parsing=True, max_num_fields=2)
                if set(query) != {"dataset", "q"} or any(len(v) != 1 for v in query.values()) or len(query["q"][0]) > 100:
                    raise ValueError
            except ValueError:
                raise _Refusal(400, "Require a dataset and a search text q of up to 100 characters") from None
            ws, raw, model = _context(req, principal, query["dataset"][0])
            _, _, links = _reads(req, raw, principal, catalog=False)
            req.send_json(200, mask_extracted(search(model, pg, ws, _visible_links(raw, ws, principal, links), query["q"][0])))
            return
        if method == "POST" and parts.path == "/api/entity/report":
            try:
                body = _body(req)
                if not isinstance(body, dict) or set(body) != {"dataset", "entity"} \
                        or not all(isinstance(v, str) for v in body.values()) or len(body["entity"]) > 200:
                    raise ValueError
            except (ValueError, UnicodeError):
                raise _Refusal(400, "Require a dataset and an entity") from None
            ws, raw, model = _context(req, principal, body["dataset"])
            version, concepts, links = _reads(req, raw, principal, catalog=True)
            links = _visible_links(raw, ws, principal, links)
            entity = _resolve(body["entity"], model, pg, ws, raw, principal, links)
            if entity is None:
                raise _Refusal(404, "No such customer or entity for this account")
            chunks = {k["chunk_id"] for k, _, _ in _matches(entity, links)}
            concepts = [c for c in concepts or [] if set((c.get("evidence") or {}).get("chunks") or []) & chunks]
            revision = hashlib.sha256(json.dumps([version, [(k["chunk_id"], k.get("definition_id"), k["status"], k.get("stated_value"),
                                                             str(k.get("recorded_at"))) for k in links if k["chunk_id"] in chunks],
                                                  [(c["id"], c.get("status"), c.get("links")) for c in concepts]],
                                                 sort_keys=True, default=str).encode()).hexdigest()
            key = (body["dataset"], body["entity"], principal["id"], revision)
            with _CACHE_LOCK:
                held = CACHE.get(key)
            if held is None:
                chat = getattr(req.server.interpreter, "chat", None) if req.server.interpreter is not None else None
                held = mask_extracted(build(model, pg, ws, raw, principal, entity, links, concepts, chat))
                with _CACHE_LOCK:
                    CACHE[key] = held
                    while len(CACHE) > CACHE_SIZE:
                        CACHE.popitem(last=False)
            req.send_json(200, {**held, "revision": revision[:12]})
            return
        raise _Refusal(404, "Action unavailable")
    except _Refusal as error:
        req.send_json(error.status, {"error": str(error)})
    except SourceWithheld:
        req.send_json(403, {"error": "The structured source is withheld for this account."})
    except (PilotDatabaseError, MetricQueryError, ClarificationNeeded):
        req.send_json(503, {"error": "The data could not be queried."})
