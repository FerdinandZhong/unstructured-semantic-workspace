"""OCR tables and document reconstruction retain real cells and source order."""

from semantic_pipeline_v2.ocr_tables import document_tables, restore_document_tables


def test_detached_refs_order_headings_blanks_escaped_pipes_and_trailing_text():
    page = {
        "index": 0,
        "markdown": "# Report\n\n[tbl-b](tbl-b.md) commentary\n\n## Next\n[tbl-a](tbl-a.md)",
        "tables": [
            {"id": "tbl-a.md", "content": "| Label | Value |\n| --- | --- |\n| Period | 2 Jan 2026 |"},
            {"id": "tbl-b.md", "content": "| Balance |  |\n| Example \\| literal | SGD 20 |\nNo table text."},
        ],
    }
    result = document_tables([page, {**page, "index": 1}])
    tables = result["tables"]
    assert [(table["page"], table["id"]) for table in tables] == [
        (1, "tbl-b.md"), (1, "tbl-a.md"), (2, "tbl-b.md"), (2, "tbl-a.md"),
    ]
    assert tables[0]["caption"] == "Report"
    assert tables[0]["rows"] == [["Balance", ""], ["Example | literal", "SGD 20"]]
    assert tables[0]["headerRows"] == 0
    assert tables[0]["format"] == "markdown"
    assert tables[1]["caption"] == "Next"
    assert tables[1]["rows"][0] == ["Label", "Value"] and tables[1]["headerRows"] == 1
    page = result["documentPages"][0]
    assert page["order"] == "source_order"
    assert [item["kind"] for item in page["blocks"]] == ["markdown", "table", "markdown", "table"]
    assert page["blocks"][2]["markdown"].startswith(" commentary")


def test_inline_html_and_markdown_grid_keep_original_positions_and_merged_cells():
    result = document_tables([{
        "index": 3,
        "markdown": ("Intro <table><caption>HTML caption</caption>\n"
                     "<tr><th>Label</th><th>Value</th></tr>\n"
                     "<tr><td rowspan='2'>Type</td><td></td></tr>\n"
                     "<tr><td>Second</td></tr>\n</table> End HTML.\n\n"
                     "+---+---+\n| First | B |\n+---+---+\n| Third |  |\n+---+---+\nClosing."),
    }])
    first, second = result["tables"]
    assert first["caption"] == "HTML caption" and first["headerRows"] == 1
    assert first["format"] == "html" and second["format"] == "plain"
    assert first["rows"] == [["Label", "Value"], ["Type", ""], ["", "Second"]]
    assert second["rows"] == [["First", "B"], ["Third", ""]]
    document = result["documentPages"][0]
    assert document["page"] == 4
    assert [item["kind"] for item in document["blocks"]] == ["markdown", "table", "markdown", "table", "markdown"]
    assert "End HTML." in document["blocks"][2]["markdown"]
    assert document["blocks"][-1]["markdown"] == "Closing."


def test_historical_observations_restore_real_rows_without_assuming_missing_content():
    observations = []
    for group, length in enumerate((5, 5, 6)):
        for line in range(1, length + 1):
            observations.append({"quote": f"| label {line} | value |", "citation": {
                "kind": "ocr_table", "page": 1, "tableId": f"tbl-{group}.md", "startLine": line
            }})
    observations.append({"quote": "[tbl-missing](tbl-missing.md)", "citation": {
        "kind": "ocr_markdown", "page": 1, "startLine": 3
    }})
    original = restore_document_tables(observations)
    assert [len(table["rows"]) for table in original["tables"]] == [5, 5, 6]
    assert all(table["format"] == "markdown" for table in original["tables"])
    assert original["documentPages"][0]["order"] == "retained_fragments"
    assert original["documentPages"][0]["blocks"][0]["kind"] == "missing_table"
    assert all(table["caption"] is None for table in original["tables"])
    empty = restore_document_tables(observations[-1:])
    assert empty["tableStatus"] == "unavailable" and empty["tables"] == []


def test_identical_distinct_tables_keep_provenance_and_fences_remain_literal():
    text = (
        "[tbl-a](tbl-a.md)\n\n"
        "```markdown\n| Label | Value |\n| --- | --- |\n| Count | 12 |\n"
        "[tbl-missing](tbl-missing.md)\n```\n\n"
        "| Label | Value |\n| --- | --- |\n| Count | 12 |\n\n"
        "<table><tr><th>Label</th><th>Value</th></tr>"
        "<tr><td>Count</td><td>12</td></tr></table>"
    )
    result = document_tables([{"index": 0, "markdown": text, "tables": [
        {"id": "tbl-a.md", "content": "| Label | Value |\n| --- | --- |\n| Count | 12 |"},
    ]}])
    assert [table["id"] for table in result["tables"]] == ["tbl-a.md", "inline-1", "inline-2"]
    assert all(table["rows"] == [["Label", "Value"], ["Count", "12"]] for table in result["tables"])
    assert sum(block["kind"] == "table" for block in result["documentPages"][0]["blocks"]) == 3
    assert any("[tbl-missing](tbl-missing.md)" in block.get("markdown", "")
               for block in result["documentPages"][0]["blocks"])


def test_separatorless_ocr_table_requires_consecutive_fully_framed_equal_width_rows():
    document = (
        "Retained note | still prose\n"
        "| Date | Category | Description | Amount |\n"
        "| 3 Jun | Credit | Initial payment | SGD 50.00 |\n"
        "| 4 Jun | Debit | Transfer \\| local | SGD 10.00 |\n"
        "| 5 Jun | Debit | | SGD 2.00 |\n"
        "Another note | retained below\n"
        "| One-off row | preserved as text |\n"
        "| Different | row | width |\n"
        "```text\n| this | remains | fenced | code |\n| also | remains | fenced | code |\n```"
    )
    projected = document_tables([{"index": 0, "markdown": document}])
    table, = projected["tables"]
    assert table["rows"] == [
        ["Date", "Category", "Description", "Amount"],
        ["3 Jun", "Credit", "Initial payment", "SGD 50.00"],
        ["4 Jun", "Debit", "Transfer | local", "SGD 10.00"],
        ["5 Jun", "Debit", "", "SGD 2.00"],
    ]
    assert (table["startLine"], table["endLine"], table["headerRows"]) == (2, 5, 0)
    first, table_block, last = projected["documentPages"][0]["blocks"]
    assert first["markdown"] == "Retained note | still prose"
    assert table_block["kind"] == "table"
    assert "Another note | retained below" in last["markdown"]
    assert "| this | remains | fenced | code |" in last["markdown"]


def test_old_ocr_separatorless_inline_table_restores_from_contiguous_rows():
    texts = ["| A | B | C | D |", "| 1 | 2 | 3 | 4 |", "| 5 | 6 | 7 | 8 |", "| 9 | | 11 | 12 |"]
    observations = [{"quote": text, "citation": {
        "kind": "ocr_markdown", "page": 1, "startLine": line,
    }} for line, text in enumerate(texts, 10)]
    result = restore_document_tables(observations)
    table, = result["tables"]
    assert table["headerRows"] == 0 and table["startLine"] == 10 and table["endLine"] == 13
    assert table["rows"][-1] == ["9", "", "11", "12"]


def test_unparseable_attachment_and_ragged_rows_are_preserved_as_source_text():
    body = (
        "# Report\n[tbl-valid](tbl-valid.md)\n[tbl-broken](tbl-broken.md)\n\n"
        "| One | Two |\n| Three | Four |\n| Five | Six | Seven |"
    )
    broken = "Field A\nUncertain OCR value\nBroken row | no framing | lost column"
    page = {"index": 0, "markdown": body, "tables": [
        {"id": "tbl-valid.md", "content": "| A | B |\n| C | D |"},
        {"id": "tbl-broken.md", "content": broken},
    ]}
    result = document_tables([page])
    table, = result["tables"]
    assert table["id"] == "tbl-valid.md"
    assert result["sourceStatus"] == "available"
    raw, = result["sourcePages"]
    assert raw["text"] == body and raw["kind"] == "ocr_output"
    assert raw["attachments"][1] == {"id": "tbl-broken.md", "text": broken, "parsed": False}
    blocks = result["documentPages"][0]["blocks"]
    assert any(block["kind"] == "missing_table" and block["tableId"] == "tbl-broken.md" for block in blocks)
    assert any("| Five | Six | Seven |" in block.get("markdown", "") for block in blocks)


def test_historical_raw_fallback_keeps_only_existing_text_and_bounds_output(monkeypatch):
    from semantic_pipeline_v2 import ocr_tables
    observations = [{"quote": "[tbl-missing](tbl-missing.md)", "citation": {
        "kind": "ocr_markdown", "page": 1, "startLine": 1,
    }}]
    result = restore_document_tables(observations)
    assert result["sourceStatus"] == "partial"
    assert result["sourcePages"][0]["kind"] == "retained_fragments"
    assert result["sourcePages"][0]["attachments"] == []
    assert restore_document_tables([])["sourceStatus"] == "unavailable"
    monkeypatch.setattr(ocr_tables, "SOURCE_TEXT_LIMIT", 4)
    result = document_tables([{"index": 0, "markdown": "áéíóú"}])
    assert result["sourceStatus"] == "partial"
    assert result["sourcePages"][0]["text"] == "áé"
    assert result["sourcePages"][0]["truncated"]
