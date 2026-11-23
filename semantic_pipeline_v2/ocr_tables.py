"""Project structured tables from original OCR payloads or retained table observations."""

from collections import defaultdict
from html.parser import HTMLParser
from pathlib import PurePosixPath
import re


_SEPARATOR = re.compile(r"^\s*:?-{3,}:?\s*$")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)(?:\s+#+)?\s*$")
_REFERENCE = re.compile(r"\[[^\]]+\]\(([^\s)]+\.(?:md|html))\)")
_GRID = re.compile(r"^\s*[+┌┏├┝└┗╞╔╚╟╠][+─━┬┳┼╪┴┻╧╤═┐┓┤┥┘┛╡╗╝╢╣\-=: ]+\s*$")
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})(.*)$")
SOURCE_TEXT_LIMIT = 1024 * 1024


def _pipe_cells(line):
    line = line.strip().replace("│", "|").replace("┃", "|").replace("║", "|")
    if "|" not in line:
        return None
    starts_edge = line.startswith("|")
    cells, current = [], []
    previous_escape = False
    backtick = False
    delimiter_at_end = False
    for char in line:
        delimiter_at_end = False
        if previous_escape:
            if char not in "|" and char != "\\":
                current.append("\\")
            current.append(char)
            previous_escape = False
        elif char == "\\":
            previous_escape = True
        elif char == "`":
            backtick = not backtick
            current.append(char)
        elif char == "|" and not backtick:
            cells.append("".join(current).strip())
            current.clear()
            delimiter_at_end = True
        else:
            current.append(char)
    if previous_escape:
        current.append("\\")
    if not delimiter_at_end:
        cells.append("".join(current).strip())
    if starts_edge:
        cells = cells[1:]
    return cells if len(cells) > 1 or (starts_edge and delimiter_at_end and cells) else None


def _framed_rows(pending):
    """Identify fully bounded OCR rows when a Markdown separator is missing."""
    run = []
    for number, framed, cells in (*pending, (None, False, ())):
        if run and (not framed or number != run[-1][0] + 1):
            if len(run) > 1 and all(len(row) == len(run[0][1]) for _, row in run):
                yield run[0][0], [row for _, row in run], 0, None, run[-1][0], "markdown"
            run = []
        if framed:
            run.append((number, cells))


def _pipe_tables(text, *, detached):
    rows, header_rows, first = [], 0, None
    pending = []
    grid_start = None
    last = None
    for number, line in enumerate([*text.splitlines(), ""], 1):
        if _GRID.fullmatch(line):
            if grid_start is None:
                grid_start = number
            if rows:
                last = number
            continue
        cells = _pipe_cells(line)
        if cells:
            last = number
            if all(_SEPARATOR.fullmatch(cell) for cell in cells):
                if len(rows) == 1:
                    header_rows = 1
                continue
            if first is None:
                first = grid_start or number
            rows.append(cells)
            trimmed = line.strip()
            framed = (len(cells) > 1 and trimmed[0] in "|│┃║"
                      and trimmed[-1] in "|│┃║" and not trimmed.endswith(r"\|"))
            pending.append((number, framed, cells))
            continue
        regular = rows and all(len(row) == len(rows[0]) for row in rows)
        detached_rows = detached and (len(rows) > 1 or any(row[1] for row in pending))
        if rows and (header_rows or (regular and (detached_rows or (grid_start is not None and len(rows) > 1)))):
            syntax = "markdown" if header_rows or (grid_start is None and all(row[1] for row in pending)) else "plain"
            yield first, rows, header_rows, None, last, syntax
        elif rows:
            yield from _framed_rows(pending)
        rows, header_rows, first, grid_start = [], 0, None, None
        pending = []


class HtmlTables(HTMLParser):
    """Read returned HTML as data and keep grid positions without running markup."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self.depth = 0
        self.hidden = 0
        self.rows = []
        self.row = None
        self.cell = None
        self.caption = None
        self.header_rows = 0
        self.spans = {}
        self.first = None

    @staticmethod
    def _span(value):
        return max(1, min(int(value), 100)) if isinstance(value, str) and value.isdecimal() and len(value) < 4 else 1

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
            return
        if self.hidden:
            return
        if tag == "table":
            self.depth += 1
            if self.depth == 1:
                self.rows, self.spans, self.caption = [], {}, None
                self.header_rows, self.first = 0, self.getpos()[0]
        if self.depth != 1:
            return
        if tag == "tr":
            self.row, self.column, self.is_header = [], 0, True
        elif tag == "caption":
            self.caption = []
        elif tag in {"th", "td"} and self.row is not None:
            attributes = dict(attrs)
            self.cell = []
            self.width = self._span(attributes.get("colspan"))
            self.height = self._span(attributes.get("rowspan"))
            self.is_header &= tag == "th"
        elif tag in {"br", "div", "p", "li"} and self.cell is not None:
            self.cell.append(" ")

    def _covered(self):
        while self.column in self.spans:
            remaining = self.spans.pop(self.column) - 1
            if remaining:
                self.spans[self.column] = remaining
            self.row.append("")
            self.column += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1
            return
        if self.hidden:
            return
        if self.depth == 1:
            if tag in {"th", "td"} and self.cell is not None:
                self._covered()
                value = " ".join("".join(self.cell).split())
                self.row.extend([value, *[""] * (self.width - 1)])
                if self.height > 1:
                    for index in range(self.column, self.column + self.width):
                        self.spans[index] = self.height - 1
                self.column += self.width
                self.cell = None
            elif tag == "tr" and self.row is not None:
                self._covered()
                if self.is_header and self.row and self.header_rows == len(self.rows):
                    self.header_rows += 1
                self.rows.append(self.row)
                self.row = None
            elif tag == "caption" and isinstance(self.caption, list):
                self.caption = " ".join("".join(self.caption).split())
            elif tag == "table" and self.rows:
                self.tables.append((self.first, self.rows, self.header_rows, self.caption or None,
                                    self.getpos()[0], "html"))
        if tag == "table" and self.depth:
            self.depth -= 1

    def handle_data(self, data):
        if self.hidden or self.depth != 1:
            return
        if self.cell is not None:
            self.cell.append(data)
        elif isinstance(self.caption, list):
            self.caption.append(data)


def _fenced_lines(lines):
    fence = None
    for number, line in lines:
        match = _FENCE.fullmatch(line)
        if fence is None and match:
            fence = match[1]
            yield number
        elif fence is not None:
            yield number
            if match and not match[2].strip() and match[1][0] == fence[0] and len(match[1]) >= len(fence):
                fence = None


def _tables(text, *, detached=False):
    lines = list(enumerate(text.splitlines(), 1))
    fenced = set(_fenced_lines(lines))
    text = "\n".join("" if number in fenced else line for number, line in lines)
    parser = HtmlTables()
    parser.feed(text)
    parser.close()
    # Parse both for OCR responses containing one inline HTML table and another Markdown table.
    markdown = re.sub(r"<table\b.*?</table\s*>", lambda match: "\n" * match[0].count("\n"),
                      text, flags=re.IGNORECASE | re.DOTALL)
    return sorted([*parser.tables, *_pipe_tables(markdown, detached=detached)], key=lambda table: table[0])


def _reference_headings(lines):
    captions = {}
    heading = None
    lines = list(lines)
    fenced = set(_fenced_lines(lines))
    for number, line in lines:
        if number in fenced:
            continue
        if match := _HEADING.fullmatch(line):
            heading = match[1].strip()
        for match in _REFERENCE.finditer(line):
            captions.setdefault(_reference_id(match[1]), heading)
    return captions


def _reference_id(value):
    ident = PurePosixPath(value).name
    return ident.rsplit(".", 1)[0] if ident.endswith((".md", ".html")) else ident


def _entry(page, ident, table, caption, *, source, start_line=None, end_line=None, inline=False):
    first, rows, header_rows, embedded_caption, last, syntax = table
    return {
        "page": page, "id": ident, "caption": embedded_caption or caption,
        "rows": rows, "headerRows": header_rows, "locator": f"page {page}, table {ident}",
        "source": source, "startLine": start_line or first, "endLine": end_line or last,
        "placement": "inline" if inline else "detached", "format": syntax,
    }


def _distinct(tables):
    seen = set()
    for table in tables:
        key = table["page"], table["id"]
        if key in seen:
            continue
        seen.add(key)
        yield table


def _attachment(content, position):
    if not isinstance(content, dict) or not isinstance(content.get("content"), str):
        return None
    ident = content.get("id") or f"detached-{position}"
    return (ident, content["content"]) if isinstance(ident, str) else None


def project_tables(pages):
    """Use only the tables and heading references supplied in this OCR result."""
    tables = []
    for item in pages:
        page = item["index"] + 1
        captions = _reference_headings(enumerate(item["markdown"].splitlines(), 1))
        inline = _tables(item["markdown"])
        for position, content in enumerate(item.get("tables") or (), 1):
            attachment = _attachment(content, position)
            if attachment is None:
                continue
            ident, text = attachment
            for subindex, table in enumerate(_tables(text, detached=True), 1):
                stable = ident if subindex == 1 else f"{ident}-{subindex}"
                tables.append(_entry(page, stable, table, captions.get(_reference_id(ident)), source="ocr_output"))
        for position, table in enumerate(inline, 1):
            ident = _inline_id(tables, page, position)
            tables.append(_entry(page, ident, table, None, source="ocr_output", inline=True))
    return list(_distinct(tables))


def _retained_text(observations):
    detached = defaultdict(dict)
    body = defaultdict(dict)
    for entry in observations:
        citation = entry.get("citation", {})
        page, line = citation.get("page"), citation.get("startLine")
        if type(page) is not int or type(line) is not int or page < 1 or line < 1:
            continue
        quote = entry.get("quote")
        if not isinstance(quote, str):
            continue
        if citation.get("kind") == "ocr_table" and isinstance(citation.get("tableId"), str):
            detached[page, citation["tableId"]].setdefault(line, quote)
        elif citation.get("kind") == "ocr_markdown":
            body[page].setdefault(line, quote)
    return detached, body


def tables_from_observations(observations):
    """Restore only retained row text. A reference to a missing table supplies no cells."""
    detached, body = _retained_text(observations)
    tables = []
    for (page, ident), rows in sorted(detached.items(), key=lambda item: (item[0][0], min(item[1]), item[0][1])):
        ordered = sorted(rows.items())
        caption = _reference_headings(sorted(body[page].items())).get(_reference_id(ident))
        for subindex, table in enumerate(_tables("\n".join(row for _, row in ordered), detached=True), 1):
            stable = ident if subindex == 1 else f"{ident}-{subindex}"
            tables.append(_entry(page, stable, table, caption, source="stored_observations",
                                 start_line=ordered[table[0] - 1][0], end_line=ordered[table[4] - 1][0]))
    for page, lines in sorted(body.items()):
        # Keep gaps explicit so a jump between unrelated paragraphs cannot form a table.
        contiguous = []
        for line, quote in sorted(lines.items()):
            if contiguous and line != contiguous[-1][0] + 1:
                _restore_inline(page, contiguous, tables)
                contiguous = []
            contiguous.append((line, quote))
        _restore_inline(page, contiguous, tables)
    return list(_distinct(tables))


def _restore_inline(page, lines, tables):
    for table in _tables("\n".join(text for _, text in lines)):
        first = lines[table[0] - 1][0]
        ident = _inline_id(tables, page, first)
        tables.append(_entry(page, ident, table, None, source="stored_observations",
                             start_line=first, end_line=lines[table[4] - 1][0], inline=True))


def _inline_id(tables, page, position):
    occupied = {table["id"] for table in tables if table["page"] == page}
    ident = f"inline-{position}"
    duplicate = 1
    while ident in occupied:
        duplicate += 1
        ident = f"inline-{position}-{duplicate}"
    return ident


def _markdown_block(text, start, end):
    return {"kind": "markdown", "markdown": text, "startLine": start, "endLine": end}


def _table_block(kind, ident, start, end):
    return {"kind": kind, "tableId": ident, "startLine": start, "endLine": end}


def _document_page(page, body, available, *, restored):
    blocks = []
    inline = defaultdict(list)
    references = {}
    trailing = defaultdict(list)
    for table in available:
        if table["placement"] == "inline":
            inline[table["startLine"]].append(table)
        else:
            references.setdefault(_reference_id(table["id"]), []).append(table)
        if restored and type(table.get("afterBodyLine")) is int:
            trailing[table["afterBodyLine"]].append(table)
    used = set()
    skip = 0
    emitted = []
    fenced = set(_fenced_lines(body))
    for number, line in body:
        if number <= skip:
            if number == skip and (closing := re.search(r"</table\s*>(.*)$", line, re.IGNORECASE)):
                if closing[1].strip():
                    blocks.append(_markdown_block(closing[1], number, number))
            continue
        if number in inline:
            entries = inline[number]
            # An inline HTML table can share its opening/closing line with narration.
            fragments = re.split(r"(</?table\b[^>]*>)", line, flags=re.IGNORECASE)
            if len(fragments) > 1 and fragments[0].strip():
                blocks.append(_markdown_block(fragments[0], number, number))
            for table in entries:
                blocks.append(_table_block("table", table["id"], number, table["endLine"]))
                emitted.append(table)
                used.add(table["id"])
                skip = max(skip, table["endLine"])
            if (len(fragments) > 1 and re.match(r"</table", fragments[-2], re.IGNORECASE)
                    and fragments[-1].strip()):
                blocks.append(_markdown_block(fragments[-1], number, number))
            continue
        offset = 0
        for match in (() if number in fenced else _REFERENCE.finditer(line)):
            ref = PurePosixPath(match[1]).name
            linked = references.get(_reference_id(ref))
            if not linked and not ref.startswith("tbl-"):
                continue
            if line[offset:match.start()].strip():
                blocks.append(_markdown_block(line[offset:match.start()], number, number))
            if linked:
                for table in linked:
                    if table["id"] in used:
                        continue
                    blocks.append(_table_block("table", table["id"], number, number))
                    emitted.append(table)
                    used.add(table["id"])
            else:
                blocks.append(_table_block("missing_table", ref, number, number))
            offset = match.end()
        remaining = line[offset:]
        if remaining or offset == 0:
            previous = blocks[-1] if blocks else None
            if previous and previous["kind"] == "markdown" and previous["endLine"] == number - 1:
                previous["markdown"] += "\n" + remaining
                previous["endLine"] = number
            else:
                blocks.append(_markdown_block(remaining, number, number))
        for table in trailing[number]:
            if table["id"] not in used:
                blocks.append(_table_block("table", table["id"], number, number))
                emitted.append(table)
                used.add(table["id"])
    orphaned = [table for table in available if table["id"] not in used]
    for table in orphaned:
        blocks.append(_table_block("table", table["id"], table["startLine"], table["endLine"]))
    order = ("retained_fragments" if restored else
             "source_then_detached" if orphaned else "source_order")
    return {"page": page, "order": order, "blocks": blocks}, [*emitted, *orphaned]


def document_tables(pages):
    """Return canonical tables and ordered document blocks for an original OCR response."""
    projected = project_tables(pages)
    documents, tables = [], []
    for item in pages:
        page = item["index"] + 1
        document, ordered_tables = _document_page(page, list(enumerate(item["markdown"].splitlines(), 1)),
                                                  [table for table in projected if table["page"] == page],
                                                  restored=False)
        documents.append(document)
        tables.extend(ordered_tables)
    raw_pages = (
        (item["index"] + 1, item["markdown"],
         (source for i, value in enumerate(item.get("tables") or (), 1)
          if (source := _attachment(value, i)) is not None))
        for item in pages
    )
    return {"tables": tables, "tableStatus": "available", "documentPages": documents,
            **_source_pages(raw_pages, tables, restored=False)}


def _limited_text(text, remaining):
    encoded = text.encode()
    if len(encoded) <= remaining:
        return text, remaining - len(encoded), False
    return encoded[:remaining].decode("utf-8", "ignore"), 0, True


def _source_pages(pages, tables, *, restored):
    available = {(table["page"], table["id"]) for table in tables if table.get("placement") != "inline"}
    remaining = SOURCE_TEXT_LIMIT
    attached = 0
    any_trimmed = False
    result = []
    for page, text, attachments in pages:
        text, remaining, truncated = _limited_text(text, remaining)
        fragments = []
        for ident, fragment in attachments:
            if attached == 100 or not remaining:
                truncated = True
                break
            fragment, remaining, clipped = _limited_text(fragment, remaining)
            fragments.append({"id": ident, "text": fragment, "parsed": (page, ident) in available})
            attached += 1
            truncated |= clipped
        if text or fragments or truncated:
            result.append({"page": page, "kind": "retained_fragments" if restored else "ocr_output",
                           "text": text, "attachments": fragments, "truncated": truncated})
        any_trimmed |= truncated
    present = any(page["text"] or page["attachments"] for page in result)
    status = "unavailable" if not present else "partial" if restored or any_trimmed else "available"
    return {"sourcePages": result, "sourceStatus": status}


def restore_source_pages(observations, tables):
    """Retain only actual saved quotations and detached attachment fragments."""
    detached, body = _retained_text(observations)
    by_page = defaultdict(list)
    for (page, ident), lines in sorted(detached.items()):
        by_page[page].append((ident, "\n".join(line for _, line in sorted(lines.items()))))
    pages = (
        (page, "\n".join(line for _, line in sorted(body[page].items())), by_page[page])
        for page in sorted(set(body) | set(by_page))
    )
    return _source_pages(pages, tables, restored=True)


def restore_document_tables(observations, *, retained_tables=None):
    """Represent surviving historical text and tables without assuming lost placement."""
    projections = tables_from_observations(observations) if retained_tables is None else retained_tables
    observations_by_page = defaultdict(dict)
    for entry in observations:
        loc = entry.get("citation", {})
        page, line = loc.get("page"), loc.get("startLine")
        if (loc.get("kind") == "ocr_markdown" and type(page) is int and page > 0 and type(line) is int
                and line > 0 and isinstance(entry.get("quote"), str)):
            observations_by_page[page].setdefault(line, entry["quote"])
    documents, tables = [], []
    for page in sorted(set(observations_by_page) | {table["page"] for table in projections}):
        document, ordered_tables = _document_page(page, sorted(observations_by_page[page].items()),
                                                  [table for table in projections if table["page"] == page],
                                                  restored=True)
        documents.append(document)
        tables.extend(ordered_tables)
    return {"tables": tables, "tableStatus": "available" if tables else "unavailable",
            "documentPages": documents, **restore_source_pages(observations, tables)}
