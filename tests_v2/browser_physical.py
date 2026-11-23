"""Check the Output C (physical mapping) panel against a mocked API boundary at 1440, 980 and 390 px.

The mocked responses are test doubles written by the implementer. The server routes are covered in
test_physical_mapping.py. Run with the miniconda python3: `python3 tests_v2/browser_physical.py [base-url]`.
"""

import sys

from playwright.sync_api import expect, sync_playwright

FINANCE = "financial-services"
SOURCE = f"{FINANCE}:source:cleaned_pdf_1"
LABEL = "works only for document types with a predefined table"


def cell(column, value, status="ok", reason=None):
    return {"column": column, "value": value, "typed": value, "status": status, "reason": reason, "unit": None,
            "sourceId": SOURCE, "sourceRevision": "a" * 64, "chunkId": "c1", "span": {"start": 0, "end": 9},
            "quote": f'xfa:data/STR[1]/SuspiciousTransaction[1]/Transaction[1]/{column}[1] = "{value}"'}


def exercise(base, width):
    errors, posts = [], []
    state = {"rows": [], "offered": True}

    def view():
        return {"sourceId": SOURCE, "table": "STR_Transaction" if state["offered"] else None,
                "writeAvailable": state["offered"], "rows": state["rows"]}

    def physical(route):
        request = route.request
        if request.method == "POST":
            action = request.url.rsplit("/", 1)[1]
            posts.append(action)
            if action == "preview":
                state["rows"] = [{"table": "STR_Transaction", "rowKey": "1", "status": "previewed", "actor": "admin",
                                  "cells": [cell("AmountOriginalCurrency", "SGD"), cell("AmountOriginal", "five", "rejected", "not a decimal number"),
                                            cell("CounterpartyName", "S12****7D")]}]
            else:
                state["rows"] = [{**state["rows"][0], "status": "written"}]
        route.fulfill(json=view())

    def ocr(route):
        if route.request.method == "GET":
            route.fulfill(json={"available": True, "model": "mistral-ocr-latest"})
            return
        route.fulfill(json={"status": "completed", "provider": "local", "model": "xfa-datasets", "evidenceStatus": "saved",
                            "pages": [{"index": 0, "markdown": "Form.Report.Reference = SYNTHETIC"}],
                            "source": {"id": SOURCE, "filename": "customer-form1.pdf", "sha256": "a" * 64},
                            "fields": [], "fieldStatus": "available", "fieldOrigin": "extraction",
                            "evidence": {"sourceId": SOURCE, "observations": [{
                                "id": f"{FINANCE}:observation:x", "quote": "Form.Report.Reference = SYNTHETIC",
                                "citation": {"locator": "embedded form data", "kind": "xfa_fields"}}], "mappings": []}})

    with sync_playwright() as playwright, playwright.chromium.launch() as browser:
        page = browser.new_page(viewport={"width": width, "height": 930})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/api/ocr", ocr)
        page.route("**/api/physical-rows**", physical)
        page.route("**/api/mapped-evidence?*", lambda route: route.fulfill(json={"evidence": None}))
        page.goto(base + f"#{FINANCE}/ingest/cleaned_pdf_1")
        expect(page.locator("#original-title")).to_be_visible()
        page.locator('[data-action="ingest-form"]').click()
        panel = page.locator("[data-physical]")
        expect(panel).to_contain_text("Physical mapping")
        expect(panel).to_contain_text(LABEL)
        expect(panel).to_contain_text("STR_Transaction")
        expect(panel.get_by_role("button", name="Confirm write")).to_be_disabled()  # no preview yet, nothing to confirm
        panel.get_by_role("button", name="Preview rows").click()
        table = panel.get_by_role("table", name="Physical mapping preview")
        expect(table).to_contain_text("AmountOriginal")
        expect(table).to_contain_text("rejected")
        expect(table).to_contain_text("not a decimal number")
        expect(table).to_contain_text("S12****7D")
        expect(table).not_to_contain_text("S1234567D")
        assert posts == ["preview"]  # a preview never writes
        panel.get_by_role("button", name="Confirm write").click()
        expect(table).to_contain_text("written")
        assert posts == ["preview", "confirm"]
        # a document type with no predefined table offers no write action
        state.update(offered=False, rows=[])
        page.reload()
        page.locator('[data-action="ingest-form"]').click()
        expect(panel).to_contain_text("no predefined table")
        expect(panel.get_by_role("button")).to_have_count(0)
        overflow = page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
        assert not overflow, f"horizontal page scroll at {width}px"
        assert not errors, errors
        print(f"Physical mapping panel passed at {width}px.")


if __name__ == "__main__":
    base = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8137/").rstrip("/") + "/"
    for width in (1440, 980, 390):
        exercise(base, width)
