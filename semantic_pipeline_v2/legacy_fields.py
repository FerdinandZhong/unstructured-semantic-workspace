"""Recover historical OCR display metadata from the same verified original."""

import re
from collections import defaultdict

from pypdf import PdfReader
from pypdf.errors import PyPdfError
from multimedia_rag.fields import mask_text

from .extracted_fields import field_values
from .ocr import MAX_DOCUMENT
from .originals import checked_original


def _caption_positions(pdf_text, tables, observations):
    """Bind a caption only when all cells and a unique OCR heading agree on its page."""
    def normalize(value):
        return " ".join(value.split())

    body_lines = defaultdict(list)
    for obs in observations:
        citation = obs["citation"]
        if citation.get("kind") == "ocr_markdown":
            heading = re.sub(r"^#{1,6}\s+", "", obs["quote"].strip())
            body_lines[normalize(heading)].append(citation["startLine"])
    # PDF layout mode inserts blank lines and uses multiple spaces between columns.
    lines = [line.strip() for line in pdf_text.splitlines() if line.strip()]
    grid = [tuple(normalize(cell) for cell in re.split(r"\s{2,}", line)) for line in lines]
    first_row_positions = defaultdict(list)
    for index, row in enumerate(grid):
        first_row_positions[row].append(index)
    result = {}
    for table in tables:
        rows = [tuple(normalize(cell) for cell in row) for row in table["rows"]]
        if not rows or any(not cell for row in rows for cell in row):
            continue
        starts = [i for i in first_row_positions[rows[0]] if grid[i:i + len(rows)] == rows]
        if len(starts) != 1 or starts[0] == 0:
            continue
        heading = normalize(lines[starts[0] - 1])
        matches = body_lines.get(heading, [])
        if len(matches) == 1:
            result[table["id"]] = {"caption": heading, "afterBodyLine": matches[0]}
    return result


def restore_table_positions(evidence, original):
    """Enrich a permitted historical response. Never alter retained cells or the store."""
    if (not evidence or not original or original.content_type[0] != "application/pdf"
            or original.digest != evidence.get("sourceSha256")):
        return evidence
    by_page = defaultdict(list)
    for table in evidence.get("tables", []):
        if table.get("source") == "stored_observations" and not table.get("caption"):
            by_page[table["page"]].append(table)
    if not by_page or len(by_page) > 100:
        return evidence
    positions = {}
    with checked_original(original, retain=True) as (status, size, stream):
        if status != "available" or size > MAX_DOCUMENT:
            return evidence
        try:
            reader = PdfReader(stream)
            if reader.is_encrypted:
                return evidence
            for page, tables in by_page.items():
                if not 1 <= page <= len(reader.pages):
                    continue
                text = reader.pages[page - 1].extract_text(extraction_mode="layout")
                if not text or len(text) > 1_048_576:
                    continue
                observations = [obs for obs in evidence["observations"]
                                if obs["citation"].get("page") == page]
                found = _caption_positions(text, tables, observations)
                positions.update({(page, ident): binding for ident, binding in found.items()})
        except (PyPdfError, ValueError, OSError, IndexError, KeyError, RecursionError):
            return evidence
    if not positions:
        return evidence
    return {**evidence, "tables": [
        {**table, **positions.get((table["page"], table["id"]), {})} for table in evidence["tables"]
    ]}


def recover_pdf_fields(evidence, original):
    """Decorate an authorized detail response without changing the saved OCR or mappings."""
    if (not evidence or evidence.get("fieldStatus") == "available" or not original
            or original.content_type[0] != "application/pdf"
            or original.digest != evidence.get("sourceSha256")):
        return evidence
    pages = sorted({citation["page"] for obs in evidence.get("observations", [])
                    if (citation := obs.get("citation", {})).get("kind") == "ocr_markdown"
                    and type(citation.get("page")) is int and citation["page"] > 0})
    if not pages or len(pages) > 100:
        return evidence
    with checked_original(original, retain=True) as (status, size, stream):
        if status != "available" or size > MAX_DOCUMENT:
            return evidence
        try:
            reader = PdfReader(stream)
            if reader.is_encrypted or pages[-1] > len(reader.pages):
                return evidence
            result = []
            for number in pages:
                text = reader.pages[number - 1].extract_text(extraction_mode="layout")
                if not text or not text.strip() or len(text) > 1_048_576:
                    return evidence
                lines = text.splitlines()
                rows = field_values([{"index": number - 1, "markdown": text}], "native_text")
                for row in rows:
                    where = row["source"]
                    where.update(page=number, kind="pdf_text_layer", locator="PDF text layer")
                    line = lines[where["startLine"] - 1].strip()
                    columns = re.split(r"\s{2,}", line, maxsplit=1)
                    if len(columns) == 2 and row["value"] in columns[1]:
                        row["label"] = mask_text(columns[0])
                result.extend(rows[:500 - len(result)])
                if len(result) == 500:
                    break
        except (PyPdfError, ValueError, OSError, IndexError, KeyError, RecursionError):
            return evidence
    return {**evidence, "fields": result, "fieldStatus": "available",
            "fieldOrigin": "pinned_pdf_text_layer"}
