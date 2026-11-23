"""Check the OCR UI against a mocked provider boundary on the local workspace."""

from html import escape
import sys

from playwright.sync_api import expect, sync_playwright


FINANCE = "financial-services"
REPORT_INTRO = (
    "# Report sampled from document\n\n"
    "Review the **record** and its tables. [Read more](/mock-ocr-never-load)\n\n"
    "- Keep the source order.\n- Read each table once.\n\n"
    "<script>window.ocrSourceScriptRan = true</script>"
)
REPORT_BETWEEN = "Account section follows the first table."
REPORT_TAIL = "The transaction table ends the extracted document."
RAGGED_LINES = "| Irregular label | first value |\n| Extra column | middle | last |\n| Trailing cell | final value |"
MALFORMED_ATTACHMENT = (
    "<table><tr><td>Readable partial label S<span>123</span>4567D\n"
    "<script>window.ocrFallbackExecuted = true</script>\n"
    '<img src="/mock-ocr-never-load">\nLast attachment line'
)
UNPARSED_ID = "tbl-unparsed.html"
REUSED_ID = "tbl-valid.md"

# Matches the three pinned report sections. Use placeholder IDs in mock responses.
REPORT_SECTIONS = (
    ("Report Information", [
        ["STR reference", "SYN-STR-0013"],
        ["Form version", "Compatibility copy, PDF 1.4"],
        ["Submission date", "8 November 2026"],
        ["Reporting institution", "Synthetic Bank Singapore Ltd"],
        ["Customer number", "CUST-EXAMPLE"],
    ]),
    ("Account Information", [
        ["Account number", "1234567890000000"],
        ["Account type", "Current account"],
        ["Account status", "Active"],
        ["Account balance", "SGD 33,120.10"],
        ["Account holder", "Arjun Nair"],
    ]),
    ("Suspicious Transaction", [
        ["Transaction period", "1 October 2026 to 31 October 2026"],
        ["Transaction type", "International wire transfer"],
        ["Total amount involved", "SGD 156,750.00"],
        ["Counterparty", "Northstar Consulting LLC"],
        ["Source / destination", "Singapore / Singapore"],
        ["Primary indicator", "Activity inconsistent with declared profile"],
    ]),
)


def report_tables():
    identifiers = ("inline-1", "tbl-1.html", "inline-2")
    return [
        {
            "page": 1, "id": identifiers[index],
            "caption": caption, "rows": rows,
            "headerRows": int(index == 0), "locator": "page table",
        }
        for index, (caption, rows) in enumerate(REPORT_SECTIONS)
    ]


def provider_report_page(tables):
    attachments = []
    blocks = [REPORT_INTRO]
    for index, table in enumerate(tables):
        identifier = table["id"]
        rows = table["rows"]
        if index == 1:
            blocks.append(REPORT_BETWEEN)
        blocks.append(f"## {table['caption']}")
        if index:
            body = "".join(
                "<tr>" + "".join(f"<td>{escape(value)}</td>" for value in row) + "</tr>"
                for row in rows
            )
            content = f"<table>{body}</table>"
        else:
            rendered = ["| " + " | ".join(row) + " |" for row in rows]
            rendered.insert(1, "| --- | --- |")
            content = "\n".join(rendered)
        if index == 1:
            blocks.append(f"[{identifier}]({identifier})")
            attachments.append({"id": identifier, "content": content})
        else:
            blocks.append(content)
    blocks.append(REPORT_TAIL)
    return {"index": 0, "markdown": "\n\n".join(blocks), "tables": attachments}


def report_document(tables):
    blocks = [{"kind": "markdown", "markdown": REPORT_INTRO}]
    for index, table in enumerate(tables):
        heading = f"## {table['caption']}"
        if index == 1:
            heading = REPORT_BETWEEN + "\n\n" + heading
        blocks.extend([{"kind": "markdown", "markdown": heading},
                       {"kind": "table", "tableId": table["id"]}])
    blocks.append({"kind": "markdown", "markdown": REPORT_TAIL})
    return [{"page": 1, "order": "source_order", "blocks": blocks}]


def check_report_tables(page):
    results = page.locator(".ingest-results")
    expect(results.get_by_role("heading", name="Extracted document", exact=True)).to_be_visible()
    document = results.locator(".ocr-document")
    expect(document.get_by_role("heading", name="Report sampled from document")).to_be_visible()
    expect(document.get_by_role("listitem")).to_have_text(["Keep the source order.", "Read each table once."])
    expect(document.locator("strong")).to_have_text("record")
    expect(document.locator("script, a[href='/mock-ocr-never-load']")).to_have_count(0)
    assert page.evaluate("!window.ocrSourceScriptRan")
    tables = results.locator(".ocr-data-table")
    expect(tables).to_have_count(3)
    for index, (caption, expected_rows) in enumerate(REPORT_SECTIONS):
        table = tables.nth(index)
        expect(table.locator("caption")).to_contain_text(caption)
        expect(table.locator("tr")).to_have_count(len(expected_rows))
        for row_index, cells in enumerate(expected_rows):
            expect(table.locator("tr").nth(row_index).locator("td, th")).to_have_text(cells)
    expect(tables.first.locator("thead tr")).to_have_count(1)
    expect(results.get_by_role("heading", name="Table summary")).to_have_count(0)
    expect(results.get_by_role("table", name="Extracted fields")).to_have_count(0)
    text = document.inner_text()
    order = ["Review the record", "STR reference", REPORT_BETWEEN, "Account balance",
             "Transaction period", REPORT_TAIL]
    positions = [text.index(phrase) for phrase in order]
    assert positions == sorted(positions)
    results.get_by_role("button", name="Fields (1)", exact=True).click()
    expect(document).not_to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")


def fallback_result():
    tables = [{
        "page": page, "id": REUSED_ID, "caption": f"Table on page {page}",
        "rows": [["Field", "Value"], ["Verified field", value]], "headerRows": 1,
        "format": "markdown", "locator": f"page {page}, table {REUSED_ID}",
    } for page, value in ((1, "**Page one only**"), (4, "**Page four only**"))]
    source_pages = [
        {
            "page": 1, "kind": "ocr_output",
            "text": f"# Malformed page\n\n{RAGGED_LINES}\n\n[{UNPARSED_ID}]({UNPARSED_ID})\n[{REUSED_ID}]({REUSED_ID})",
            "attachments": [{"id": UNPARSED_ID, "text": MALFORMED_ATTACHMENT, "parsed": False}],
            "truncated": False,
        },
        {"page": 4, "kind": "ocr_output", "text": f"# Second document page\n[{REUSED_ID}]({REUSED_ID})",
         "attachments": [], "truncated": False},
    ]
    return {
        "tables": tables, "tableStatus": "available",
        "sourcePages": source_pages, "sourceStatus": "available",
        "documentPages": [
            {"page": 1, "order": "source_order", "blocks": [
                {"kind": "markdown", "markdown": f"# Malformed page\n\n{RAGGED_LINES}"},
                {"kind": "missing_table", "tableId": UNPARSED_ID},
                {"kind": "table", "tableId": REUSED_ID},
            ]},
            {"page": 4, "order": "source_order", "blocks": [
                {"kind": "markdown", "markdown": "# Second document page"},
                {"kind": "table", "tableId": REUSED_ID},
            ]},
        ],
    }


def check_fallback(page):
    document = page.locator(".ingest-results .ocr-document")
    expect(document.locator(".ocr-document-page")).to_have_count(2)
    first, fourth = (document.locator(".ocr-document-page").nth(index) for index in (0, 1))
    expect(first.get_by_role("heading", name="Page 1", exact=True)).to_be_visible()
    expect(fourth.get_by_role("heading", name="Page 4", exact=True)).to_be_visible()
    expect(first.locator(".ocr-data-table")).to_have_count(1)
    expect(fourth.locator(".ocr-data-table")).to_have_count(1)
    expect(first.locator(".ocr-data-table tbody strong")).to_have_text("Page one only")
    expect(fourth.locator(".ocr-data-table tbody strong")).to_have_text("Page four only")
    expect(fourth).not_to_contain_text("Readable partial label")
    unreadable = first.locator(".ocr-table-unreadable")
    expect(unreadable).to_contain_text("has no readable cell structure")
    expect(unreadable.locator("pre")).to_contain_text("Readable partial label S12****7D")
    expect(unreadable.locator("pre")).to_contain_text("Last attachment line")
    assert unreadable.locator("pre").text_content().splitlines()[1] == "<script>window.ocrFallbackExecuted = true</script>"
    expect(first.locator(".ocr-unparsed-section pre")).to_be_visible()
    assert first.locator(".ocr-unparsed-section pre").text_content() == RAGGED_LINES
    expect(document.locator("script, img[src='/mock-ocr-never-load']")).to_have_count(0)
    assert page.evaluate("!window.ocrFallbackExecuted")
    raw = first.locator(".ocr-source-details")
    raw.get_by_text("OCR source text", exact=True).click()
    expect(raw.locator("pre").first).to_be_visible()
    assert RAGGED_LINES in raw.locator("pre").first.text_content()
    expect(raw).to_contain_text("Readable partial label S12****7D")
    expect(raw).not_to_contain_text("S1234567D")
    expect(first.locator(".ocr-data-table")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")


def exercise(base, width):
    errors = []
    requests = []
    fail_next = False
    target_definition = None
    saved_mode = None
    report_mode = False
    fallback_mode = False

    def mock_ocr(route):
        nonlocal fail_next
        if route.request.method == "GET":
            route.fulfill(json={"available": True, "model": "mistral-ocr-latest"})
            return
        request = route.request.post_data_json
        requests.append(request)
        if fail_next:
            fail_next = False
            route.fulfill(status=502, json={"error": "Mistral OCR connection failed or timed out."})
            return
        source = request["sourceId"]
        mode = request["mode"]
        local = mode != "ocr"
        filename = source.rsplit(":", 1)[-1]
        model = {"text": "utf8-text", "xfa-fields": "xfa-datasets"}.get(mode, "mistral-ocr-latest")
        passage = {"xfa-fields": "Form.Report.Reference = SYNTHETIC", "text": "Exact local text"}.get(
            mode, "Customer risk rating is supplied in the source."
        )
        observation = {
            "id": f"{FINANCE}:observation:mock-{mode}", "title": "Mock processing evidence",
            "sourceId": source, "quote": passage,
            "citation": {"locator": "embedded form data"} if mode == "xfa-fields" else {"page": 1},
        }
        field = {"type": "rating", "label": "Rating field", "value": "Found as written",
                 "source": {"kind": "ocr_table", "page": 1, "tableId": "tbl-1.html"}}
        if fallback_mode:
            payload = fallback_result()
            route.fulfill(json={
                "status": "completed", "provider": "mistral", "model": model,
                "source": {"id": source, "filename": filename},
                "pages": [{"index": item["page"] - 1, "markdown": item["text"]}
                          for item in payload["sourcePages"]],
                "evidenceStatus": "saved", "fields": [], "fieldStatus": "available",
                **payload,
            })
            return
        if report_mode:
            tables = report_tables()
            route.fulfill(json={
                "status": "completed", "provider": "mistral", "model": model,
                "source": {"id": source, "filename": filename}, "pages": [provider_report_page(tables)],
                "tables": tables, "tableStatus": "available",
                "documentPages": report_document(tables),
                "evidenceStatus": "saved", "fields": [field], "fieldStatus": "available",
            })
            return
        route.fulfill(json={
            "status": "completed", "provider": "local" if local else "mistral", "model": model,
            "source": {"id": source, "filename": filename},
            "pages": [{"index": 0, "markdown": passage if local else "[tbl-1.html](tbl-1.html)",
                       **({"tables": [{"id": "tbl-1.html", "content": "<table><tr><td>Risk rating</td><td>Readable table value<img src='/mock-ocr-never-load'></td><td>S<span>123</span>4567D</td></tr></table>"}]} if not local else {})}],
            "evidenceStatus": "saved", "fields": [] if local else [field], "fieldStatus": "available",
            **({"annotation": {"findings": [{"label": "Rating field", "value": "Found as written"}]}} if not local else {}),
            "evidence": {
                "sourceId": source,
                "observations": [observation, {**observation, "id":f"{FINANCE}:observation:unmatched-{mode}",
                                               "title":"Unclassified contract amount", "quote":"An amount without a currency."}],
                "mappings": [{
                    "id": f"{FINANCE}:mapping:mock-{mode}", "title": "Risk rating mapping",
                    "description": "Review this meaning", "sourceId": source,
                    "observationId": observation["id"], "targetId": target_definition, "status": "candidate",
                }, {
                    "id": f"{FINANCE}:mapping:unmatched-{mode}", "title": "Unmapped",
                    "observationId": f"{FINANCE}:observation:unmatched-{mode}", "status": "unmapped",
                }],
            },
        })

    def mock_saved(route):
        if saved_mode is None:
            route.fulfill(json={"evidence": None})
            return
        fields = [{
            "type": "amount", "label": "Reported amount", "value": "100.00",
            "source": {"kind": "pdf_text_layer", "page": 3, "startLine": 7, "endLine": 7},
        }] if saved_mode in {"recovered", "report"} else []
        if saved_mode == "report":
            fields.append({**fields[0], "source": {**fields[0]["source"], "startLine": 9, "endLine": 9}})
        route.fulfill(json={"evidence": {
            "sourceId": f"{FINANCE}:source:cleaned_pdf_3",
            "fieldStatus": "available" if fields else "unavailable",
            "fieldOrigin": "pinned_pdf_text_layer" if fields else None,
            "fields": fields,
            **({"tables": report_tables(), "tableStatus": "available",
               "tableOrigin": "pinned_pdf_text_layer", "documentPages": report_document(report_tables())}
               if saved_mode == "report" else {}),
            **(fallback_result() if saved_mode == "fallback" else {}),
            "observations": [{
                "id": f"{FINANCE}:observation:processed-check", "quote": "[tbl-7.html](tbl-7.html)",
                "citation": {"kind": "ocr_markdown", "page": 3, "startLine": 7, "endLine": 7},
            }],
            "mappings": [],
        }})

    with sync_playwright() as playwright, playwright.chromium.launch() as browser:
        page = browser.new_page(viewport={"width": width, "height": 930})
        page.on("pageerror", lambda error: errors.append(str(error)))
        resource_requests = []
        page.on("request", lambda request: resource_requests.append(request.url))
        page.route("**/api/ocr", mock_ocr)
        page.route("**/api/mapped-evidence?*", mock_saved)

        def visit(source):
            page.goto(base + f"#{FINANCE}/ingest/{source}")
            expect(page.locator("#original-title")).to_be_visible()

        def action(name):
            return page.locator(f'[data-action="{name}"]')

        visit("cleaned_pdf_3")
        expect(page.get_by_role("button", name="Run OCR")).to_be_enabled()
        target_definition = page.evaluate("""() => window.V2_WORKSPACE_DATA.datasets
          .find(item => item.id === 'financial-services').knowledge.structured.fields
          .find(field => field.name === 'Customers/RiskRating').id""")
        if page.locator("details.ingest-options").get_attribute("open") is None:
            page.locator("details.ingest-options > summary").click()
        expect(page.get_by_role("checkbox", name="Guide annotation")).to_be_checked()
        pages = page.locator('[data-setting="pages"]')
        pages.fill("2-1")
        expect(action("ingest-run")).to_be_disabled()
        pages.fill("1")
        page.locator('[data-setting="prompt"]').fill("Find the rating as written.")
        page.locator('[data-setting="tables"]').select_option("html")
        action("ingest-run").click()
        confirmation = page.locator("dialog[open]")
        expect(confirmation).to_contain_text("Send this original to Mistral OCR")
        expect(confirmation).to_contain_text("Find the rating as written.")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        assert not requests
        confirmation.get_by_role("button", name="Send to Mistral", exact=True).click()
        expect(page.locator(".ingest-results")).to_contain_text("Mistral OCR results")
        expect(page.locator(".ingest-results")).to_contain_text("Found as written")
        page.get_by_role("button", name="Fields (1)", exact=True).click()
        fields = page.get_by_role("table", name="Extracted fields")
        expect(fields.get_by_role("columnheader")).to_have_text(["Type", "Value", "Location"])
        expect(fields).to_contain_text("Rating field")
        expect(fields).to_contain_text("Page 1 · Table tbl-1.html")
        expect(page.locator(".ocr-document")).not_to_be_visible()
        page.get_by_role("button", name="Content", exact=True).click()
        expect(page.locator(".ocr-document .ocr-data-table")).to_contain_text("Readable table value")
        expect(page.locator(".ocr-document .ocr-data-table")).to_contain_text("S12****7D")
        expect(page.locator(".ocr-document .ocr-data-table")).not_to_contain_text("S1234567D")
        assert not any(url.endswith("/mock-ocr-never-load") for url in resource_requests)
        expect(page.locator(".result-mapping")).to_contain_text("1 suggested definition")
        expect(page.get_by_label("Find evidence")).to_have_count(0)
        expect(page.get_by_text("Unclassified contract amount", exact=True)).to_have_count(0)
        expect(page.locator(".ocr-document-body")).not_to_contain_text("[tbl-1.html]")
        expect(page.locator(".ingest-results")).not_to_contain_text("Prepared source inventory")
        assert requests[0] == {
            "dataset": FINANCE, "mode": "ocr", "sourceId": f"{FINANCE}:source:cleaned_pdf_3",
            "options": {
                "pages": "1", "includeImageBase64": True, "extractHeader": False, "extractFooter": False,
                "tableFormat": "html", "definitionGuided": True, "annotationPrompt": "Find the rating as written.",
            },
        }

        page.get_by_role("link", name="Open in Knowledge").click()
        expect(page.locator("#graph-definitions")).to_contain_text("customer-form3.pdf")
        expect(page.locator("#graph-definitions")).to_be_visible()
        expect(page.locator("#graph-definitions")).to_contain_text("Risk rating")
        expect(page.locator("#graph-definitions")).not_to_contain_text("Unclassified contract amount")
        expect(page.locator("#workspace-main")).not_to_contain_text("Order and line records")
        page.get_by_role("link", name="Inspect cited source").click()
        expect(page.locator(".ingest-results")).to_contain_text("Selected evidence")
        expect(page.locator(".ingest-results")).to_contain_text("Customer risk rating is supplied in the source.")
        page.goto(base + f"#{FINANCE}/knowledge")
        expect(page.get_by_role("heading", name="Evidence-to-definition mappings")).to_be_visible()

        visit("cleaned_pdf_3")
        page.get_by_role("button", name="Configure", exact=True).click()
        fail_next = True
        action("ingest-run").click()
        page.get_by_role("button", name="Send to Mistral", exact=True).click()
        expect(page.get_by_role("alert")).to_contain_text("connection failed")
        expect(page.locator(".ingest-results")).not_to_contain_text("Found as written")

        report_mode = True
        page.get_by_role("button", name="Configure", exact=True).click()
        action("ingest-run").click()
        page.get_by_role("button", name="Send to Mistral", exact=True).click()
        check_report_tables(page)
        expect(page.get_by_role("table", name="Extracted fields")).to_contain_text("Rating field")
        expect(page.locator(".ocr-document-body")).not_to_contain_text("[tbl-1.html]")
        report_mode = False

        fallback_mode = True
        page.get_by_role("button", name="Configure", exact=True).click()
        action("ingest-run").click()
        page.get_by_role("button", name="Send to Mistral", exact=True).click()
        check_fallback(page)
        fallback_mode = False

        visit("cleaned_pdf_1")
        expect(page.get_by_role("button", name="Read form fields")).to_be_enabled()
        expect(page.get_by_role("button", name="Run OCR")).to_have_count(0)
        action("ingest-form").click()
        expect(page.locator(".ingest-results")).to_contain_text("Embedded form data")
        expect(page.locator(".ingest-results")).not_to_contain_text("Page 1")
        assert requests[-1]["mode"] == "xfa-fields" and "options" not in requests[-1]

        visit("jade")
        page.get_by_role("button", name="Read text", exact=True).click()
        expect(page.locator(".ingest-results")).to_contain_text("Exact local text")
        assert requests[-1]["mode"] == "text" and "options" not in requests[-1]

        saved_mode = "recovered"
        visit("cleaned_pdf_3")
        page.reload()
        page.get_by_role("button", name="Results", exact=True).click()
        expect(page.locator(".ingest-results")).to_contain_text("Saved results")
        expect(page.locator(".ingest-results")).to_contain_text("Recovered from the original PDF text.")
        expect(page.get_by_role("table", name="Extracted fields")).to_contain_text("100.00")
        expect(page.locator(".ingest-results")).to_contain_text("Page 3 · Line 7")
        expect(page.locator(".ingest-results")).not_to_contain_text("rerun extraction")
        saved_mode = "report"
        submitted = len(requests)
        page.reload()
        page.get_by_role("button", name="Results", exact=True).click()
        check_report_tables(page)
        expect(page.get_by_role("table", name="Extracted fields")).to_contain_text("100.00")
        row = page.get_by_role("table", name="Extracted fields").locator("tbody tr")
        expect(row).to_have_count(1)
        expect(row).to_contain_text("Line 7")
        expect(row).to_contain_text("Line 9")
        assert len(requests) == submitted
        saved_mode = "fallback"
        page.reload()
        page.get_by_role("button", name="Results", exact=True).click()
        check_fallback(page)
        assert len(requests) == submitted
        saved_mode = "legacy"
        page.reload()
        page.get_by_role("button", name="Results", exact=True).click()
        expect(page.locator(".ingest-results")).to_contain_text("The saved result has table references without readable cells.")
        expect(page.locator(".ingest-results")).to_contain_text("rerun extraction")
        expect(page.locator(".ingest-results")).not_to_contain_text("[tbl-7.html]")
        expect(page.locator(".ocr-table-unreadable, .ocr-data-table, .ocr-source-details")).to_have_count(0)
        assert not any(url.endswith("/mock-ocr-never-load") for url in resource_requests)
        assert not errors, errors
        print(f"OCR, saved results, fallback, definition graph, XFA, and pinned text passed at {width}px.")


if __name__ == "__main__":
    base = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8137/").rstrip("/") + "/"
    for width in (1440, 980, 390):
        exercise(base, width)
