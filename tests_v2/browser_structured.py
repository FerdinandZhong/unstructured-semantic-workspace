"""Check scoped structured table browsing and workbook selection in Chromium."""

import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
PORTABLE = (ROOT / "docs/design/v2-workspace/index.html").as_uri()
FINANCE = "financial-services"
MANUFACTURING = "manufacturing"


def visit(page, base, dataset, detail=""):
    suffix = "/" + detail if detail else ""
    page.goto(f"{base}#{dataset}/ingest{suffix}")
    expect(page.locator("#ingest-title")).to_be_visible()


def check_selection_order(page, base):
    visit(page, base, FINANCE)
    expect(page.locator(".file-library")).to_be_visible()
    page.evaluate("""() => {
      const read = File.prototype.arrayBuffer;
      const digest = crypto.subtle.digest.bind(crypto.subtle);
      window.finishedTestDigests = 0;
      crypto.subtle.digest = async (...args) => {
        const value = await digest(...args);
        setTimeout(() => { window.finishedTestDigests++; }, 0);
        return value;
      };
      File.prototype.arrayBuffer = function () {
        if (!this.name.startsWith('slow-')) return read.call(this);
        return new Promise(resolve => { window.releaseTestWorkbook = () => resolve(read.call(this)); });
      };
    }""")
    picker = page.locator("#workspace-file")
    delayed = {"name": "slow-input.xlsx", "mimeType": "application/octet-stream", "buffer": b"Delayed workbook digest"}
    earlier_file = {"name": "earlier.txt", "mimeType": "text/plain", "buffer": b"Earlier mixed batch"}
    picker.set_input_files([delayed, earlier_file])
    page.wait_for_function("typeof window.releaseTestWorkbook === 'function'")
    picker.set_input_files({"name": "later.txt", "mimeType": "text/plain", "buffer": b"Later ordinary batch"})
    expect(page.locator(".file-added .file-open")).to_have_text("later.txt")
    page.evaluate("window.releaseTestWorkbook(); window.releaseTestWorkbook = null")
    page.wait_for_function("window.finishedTestDigests === 1")
    expect(page.locator(".file-library")).to_be_visible()
    expect(page.get_by_role("link", name="earlier.txt")).to_be_visible()
    expect(page.get_by_role("link", name="later.txt")).to_be_visible()

    picker.set_input_files(delayed)
    page.wait_for_function("typeof window.releaseTestWorkbook === 'function'")
    page.get_by_role("link", name="later.txt").click()
    expect(page.locator(".original-text")).to_have_text("Later ordinary batch")
    page.evaluate("window.releaseTestWorkbook(); window.releaseTestWorkbook = null")
    page.wait_for_function("window.finishedTestDigests === 2")
    expect(page.locator(".original-text")).to_have_text("Later ordinary batch")


def exercise(base, workbook):
    with sync_playwright() as tools:
        with tools.chromium.launch() as browser:
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            for width in (1440, 980, 390):
                page.set_viewport_size({"width": width, "height": 930})
                visit(page, base, FINANCE)
                expect(page.locator(".file-library")).to_be_visible()
                # The workbook simulates the structured database. It is never listed as an ingested file (user, 2026-11-22).
                expect(page.get_by_role("link", name="MockUp Data.xlsx")).to_have_count(0)
                expect(page.locator(".file-library")).not_to_contain_text("MockUp")
                page.get_by_role("link", name="Structured data").click()
                expect(page.locator(".structured-summary")).to_be_visible()
                expect(page.get_by_role("table", name="Structured tables")).to_have_count(0)
                page.get_by_role("link", name="Inspect table schema").click()
                expect(page.get_by_role("table", name="Structured tables")).to_be_visible()
                expect(page.get_by_role("heading", name="Customers", exact=True)).to_be_visible()
                expect(page.locator(".structured-metrics")).to_contain_text("Customers on business date")
                expect(page.locator(".structured-metrics")).to_contain_text("Distinct customers in period")
                expect(page.locator(".structured-detail")).to_contain_text("SourceBatch, BusinessDate, CustomerID")
                expect(page.locator("#original-content")).to_have_count(0)
                page.get_by_role("link", name="Accounts", exact=True).click()
                expect(page.get_by_role("table", name="Table fields")).to_contain_text("AccountNumber")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")

                page.locator("#workspace-choice").select_option(MANUFACTURING)
                expect(page.locator(".breadcrumb")).to_contain_text("Manufacturing")
                page.get_by_role("link", name="Inspect table schema").click()
                expect(page.get_by_role("link", name="Sales_SalesOrderDetail", exact=True)).to_be_visible()
                expect(page.get_by_role("link", name="Customers", exact=True)).to_have_count(0)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")

            visit(page, base, "finance", "workbook")
            expect(page.locator(".structured-workspace")).to_be_visible()
            expect(page.locator("#original-content")).to_have_count(0)
            if workbook:
                page.locator("#workspace-file").set_input_files(workbook)
                expect(page.locator(".structured-messages")).to_contain_text("matches the prepared demo workbook")
                if base != PORTABLE:
                    expect(page.locator(".structured-intro")).to_contain_text("11 of 11 prepared tables have synthetic rows.")
                    page.get_by_role("link", name="Inspect table schema").click()
                    assert page.get_by_role("table", name="Structured tables").get_by_role("row").nth(1).get_by_role("cell").nth(1).inner_text().isdigit()

            visit(page, base, FINANCE)
            expect(page.locator(".file-library")).to_be_visible()
            page.locator("#workspace-file").set_input_files([
                {"name": "MockUp Data.xlsx", "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "buffer": b"An unmatched workbook"},
                {"name": "mixed.txt", "mimeType": "text/plain", "buffer": b"Document from the same batch"},
            ])
            expect(page.locator(".structured-messages")).to_contain_text("has no verified import for this dataset")
            expect(page.locator(".structured-messages")).to_contain_text("other selected files")
            page.get_by_role("button", name="Documents", exact=True).click()
            expect(page.get_by_role("link", name="MockUp Data.xlsx")).to_have_count(0)
            page.get_by_role("link", name="mixed.txt", exact=True).click()
            expect(page.locator(".original-text")).to_have_text("Document from the same batch")
            check_selection_order(page, base)
            assert not errors, errors
            print("Structured data verified at 1440, 980 and 390 pixels.")


if __name__ == "__main__":
    exercise(sys.argv[1] if len(sys.argv) > 1 else PORTABLE, Path(sys.argv[2]) if len(sys.argv) > 2 else None)
