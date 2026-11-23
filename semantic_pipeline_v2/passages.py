"""Build source-bound passages for the Knowledge inspector from retained evidence."""

from collections import Counter
from hashlib import sha256
import re


def passage(items, kind, text, title, **extra):
    first, last = items[0], items[-1]
    citation = dict(first["citation"])
    for key in ("endLine", "endSeconds"):
        if key in last["citation"]:
            citation[key] = last["citation"][key]
    ids = [item["id"] for item in items]
    key = sha256(("\n".join(ids) + "\n" + kind + "\n" + text).encode()).hexdigest()[:24]
    return {"id": first["sourceId"] + ":passage:" + key, "sourceId": first["sourceId"],
            "kind": kind, "title": title, "text": text, "observationIds": ids,
            "citation": citation, **extra}


def _excerpt(text):
    first = text.splitlines()[0].strip("# *")
    return first if len(first) <= 100 else first[:97].rsplit(" ", 1)[0] + "…"


def _table_members(table, items):
    for item in items:
        ref = item["citation"]
        if ref.get("page") != table["page"]:
            continue
        table_id = ref.get("tableId")
        if table.get("placement") == "inline":
            if table_id or ref.get("kind") != "ocr_markdown":
                continue
        elif table_id != table["id"] and not table["id"].startswith(str(table_id) + "-"):
            continue
        if table["startLine"] <= ref.get("startLine", 0) <= table["endLine"]:
            yield item


def from_observations(items, tables=()):
    """All input observations belong to one source revision. Raw evidence stays intact."""
    items = [item for item in items if item.get("observationRole") != "context" and item.get("quote")]
    result, covered = [], set()
    captions = Counter(table.get("caption") for table in tables)
    for table in tables:
        members = list(_table_members(table, items))
        if not members or not table["rows"]:
            continue
        covered.update(item["id"] for item in members)
        rows = table["rows"]
        text = "\n".join(" · ".join(row) for row in rows)
        caption = table.get("caption")
        representative = next((row for row in rows if len(row) == 2 and
                               re.search(r"(?:[A-Z]{3}\s+[\d,]+\.\d{2}|[$€£]\d)", row[1])), rows[0])
        title = caption if caption and captions[caption] == 1 else _excerpt(" — ".join(representative))
        result.append(passage(members, "table", text, title, rows=rows))
    group = []

    def flush():
        if group:
            ref = group[0]["citation"]
            kind = "transcript" if ref.get("kind") == "transcript" else "article"
            text = "\n".join(item["quote"] for item in group)
            title = _excerpt(max((item["quote"] for item in group), key=len)) if kind == "transcript" else group[0]["title"]
            result.append(passage(group, kind, text, title))
            group.clear()

    for item in items:
        if item["id"] in covered:
            continue
        ref = item["citation"]
        if group:
            previous = group[-1]["citation"]
            contiguous = (ref.get("startLine", 0) <= previous.get("endLine", 0) + 2 and
                          ref.get("startSeconds", 0) <= previous.get("endSeconds", 0) + 3)
            separate = any(ref.get(key) != previous.get(key) for key in ("page", "kind", "speaker", "tableId"))
            if separate or not contiguous or sum(len(o["quote"]) + 1 for o in group) + len(item["quote"]) > 800:
                flush()
        group.append(item)
    flush()
    return result


def article_body(record, items, raw):
    """Retain intervening source paragraphs and qualifications between curated anchors."""
    lines = raw.decode().splitlines()
    start = min(item["citation"]["startLine"] for item in items)
    end = max(item["citation"]["endLine"] for item in items)
    text = "\n".join(lines[start - 1:end])
    return passage(items, "article", text, record["title"])
