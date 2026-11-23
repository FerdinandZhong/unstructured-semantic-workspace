"""Extract V1 field patterns from separately located V2 text fragments."""

from dataclasses import dataclass
from html.parser import HTMLParser
import re

from multimedia_rag.fields import extract_fields, mask_text


_PASSTHROUGH = {"image_base64", "id", "sourceId", "targetId", "observationId",
                "candidateIds", "sha256", "sourceSha256", "modelSha256",
                "originalTargetId", "originalCandidateIds", "correctionRevision"}


def mask_extracted(value):
    """Keep source identities and encoded images intact when masking result text."""
    if isinstance(value, str):
        return mask_text(value)
    if isinstance(value, dict):
        return {key: item if key in _PASSTHROUGH else mask_extracted(item) for key, item in value.items()}
    if isinstance(value, list):
        return [mask_extracted(item) for item in value]
    return value


class TableText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.chunks = []
        self.hidden = False

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden = True
        elif tag == "br" and not self.hidden:
            self.chunks.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = False
        elif not self.hidden and tag in {"td", "th", "tr"}:
            self.chunks.append("\n" if tag == "tr" else " | ")

    def handle_data(self, data):
        if not self.hidden:
            self.chunks.append(data.replace("\r", " ").replace("\n", " "))

    def text(self):
        return "\n".join(" ".join(line.strip(" |").split()) for line in "".join(self.chunks).splitlines()
                         if line.strip(" |"))


@dataclass(frozen=True)
class Fragment:
    index: int
    text: str
    kind: str
    locator: str
    table_id: str | None = None

    def location(self, first, last):
        location = {"kind": self.kind, "locator": self.locator, "startLine": first, "endLine": last}
        if self.kind.startswith("ocr_"):
            location["page"] = self.index + 1
        if self.table_id is not None:
            location["tableId"] = self.table_id
        return location


def fragments(page, kind):
    index = page["index"]
    locator = {"xfa_fields": "embedded form data", "native_text": "original text",
               "transcript": "timestamped transcript"}.get(kind, "page body")
    yield Fragment(index, page["markdown"], kind, locator)
    if kind != "ocr_markdown":
        return
    for name in ("header", "footer"):
        if isinstance(page.get(name), str) and page[name].strip():
            yield Fragment(index, page[name], f"ocr_{name}", f"page {name}")
    for position, table in enumerate(page.get("tables") or (), 1):
        if not isinstance(table, dict) or not isinstance(table.get("content"), str):
            continue
        ident = table.get("id")
        ident = ident if isinstance(ident, str) and ident else str(position)
        content = table["content"]
        if table.get("format") == "html" or content.lstrip().lower().startswith("<table"):
            parser = TableText()
            parser.feed(content)
            parser.close()
            content = parser.text()
        if content.strip():
            yield Fragment(index, content, "ocr_table", f"table {ident}", ident)


def field_values(pages, kind):
    """Return masked V1 field values without merging distinct source locations."""
    result, seen = [], set()
    for page in pages:
        for fragment in fragments(page, kind):
            previous, first = 0, 1
            for field in extract_fields(fragment.text, []):
                first += fragment.text.count("\n", previous, field["start"])
                previous = field["start"]
                last = first + fragment.text.count("\n", previous, field["end"] - 1)
                source = fragment.location(first, last)
                key = (fragment.index, fragment.kind, fragment.table_id, first, last, field["type"], field["value"])
                if key in seen:
                    continue
                seen.add(key)
                label = field_label(fragment, field)
                result.append({"type": field["type"], "label": label,
                               "value": field["value"], "source": source})
                if len(result) == 500:
                    return result
    return result


def field_label(fragment, field):
    text, offset = fragment.text, field["start"]
    start = text.rfind("\n", 0, offset) + 1
    prefix = text[start:offset]
    label = ""
    if fragment.kind == "ocr_table":
        cells = re.split(r"(?<!\\)\|", prefix.lstrip(" |"))
        if len(cells) > 1:
            label = cells[0]
    elif delimiter := re.match(r"\s*([^:=\n]+?)\s*[:=]", prefix):
        label = delimiter[1]
    label = label.strip(" *`_")
    return label if 0 < len(label) <= 100 else field["label"] or field["type"]
