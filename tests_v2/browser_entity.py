"""Mocked browser check of Entity 360 (2026-11-22) at 1440, 1280 and 980 px: the picker, the Jade Logistics report built on opening,
Generate with its progress line, each report section, the charts, the links to Evidence and Concepts, a reader view
and the Manufacturing note. No horizontal scroll and no page error.

Starts its own server on a free port with an empty private state and no provider key. Every /api/entity response is
a test double written here; no provider or shared database is called.
Run with miniconda python3 (Playwright): `python3 tests_v2/browser_entity.py`.
"""
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from browser_form_rows import free_port

ROOT = Path(__file__).resolve().parents[1]
DS = "financial-services"
F = f"{DS}:definition:finance_workbook_demo/field/"
S = f"{DS}:source:"
CUSTOMERS = [{"id": f"customer:CUST{n:05d}", "kind": "customer", "label": f"CUST{n:05d}", "detail": "Corporate · CMB"} for n in range(1, 53)]
JADE = {"id": "entity:jade-logistics", "kind": "document", "label": "Jade Logistics", "detail": "Known from documents only"}


def field(table, column, value):
    return {"definition": F + f"{table}/{column}", "column": column, "value": value}


def item(source, title, chunk, text, how, relation="none", stated=None, **extra):
    return {"chunkId": chunk, "sourceId": S + source, "sourceTitle": title, "text": text, "relation": relation,
            "statedValue": stated, "unit": None, "period": None, "how": how, "howNote": None, "status": "proposed", **extra}


CUSTOMER = {
    "entity": {"id": "customer:CUST00004", "kind": "customer", "label": "CUST00004", "key": "Customers.CustomerID=CUST00004"},
    "businessDate": "2026-11-06",
    "profile": {"rows": True, "source": "SQL", "fields": [field("Customers", c, v) for c, v in (
        ("BusinessDate", "2026-11-06"), ("CustomerID", "CUST00004"), ("CustomerType", "Corporate"), ("IndustryCode", "6810-RealEstate"),
        ("RiskRating", "Medium"), ("CustomerSegment", "CBK"), ("CountryOfOperation", "SG"), ("BranchCode", "BR005"))],
        "rm": [{"RMID": "RM005", "fields": [field("Customer_RM_Relationship", "RMID", "RM005"),
                                            field("Customer_RM_Relationship", "RelationshipType", "Primary RM"),
                                            field("RelationshipManagers", "Title", "Relationship Manager"),
                                            field("RelationshipManagers", "Region", "APAC")]}]},
    "holdings": {"source": "SQL", "accounts": {"count": 2, "businessDate": "2026-11-06", "rows": [
        {"AccountNumber": "MY-CA-00004-0", "ProductType": "CASA", "ProductSubType": "Current", "Currency": "USD", "Status": "Active", "OpenDate": "2022-02-01"},
        {"AccountNumber": "MY-CA-00004-2", "ProductType": "CASA", "ProductSubType": "Savings", "Currency": "HKD", "Status": "Active", "OpenDate": "2022-02-01"}],
        "byType": [{"type": "CASA", "status": "Active", "count": 2}]},
        "transactions": [
            {"table": "CASA_Txn", "definition": F + "CASA_Txn/Amount", "count": 12, "total": 232870.48, "sql": "SELECT 1",
             "currencies": [{"currency": "HKD", "count": 5, "total": 120000.5}, {"currency": "USD", "count": 7, "total": 112869.98}],
             "days": [{"day": d, "count": n, "totals": {"USD": n * 9000.0, "HKD": n * 11000.0}} for d, n in (("2026-11-04", 3), ("2026-11-05", 5), ("2026-11-06", 4))]},
            {"table": "Loan_Txn", "definition": F + "Loan_Txn/Amount", "count": 2, "total": 24000, "sql": "SELECT 1",
             "currencies": [{"currency": "EUR", "count": 2, "total": 24000}],
             "days": [{"day": "2026-11-05", "count": 2, "totals": {"EUR": 24000}}]}]},
    "evidence": {"source": "Output A", "byTable": [{"table": "Customers", "count": 2}, {"table": "No field", "count": 1}], "groups": [
        {"definition": F + "Customers/CustomerSegment", "items": [item("FIN-001", "Banking facility letter", "c4a",
            "Facility letter for customer CUST00004, segment CBK. " + "Long clause text. " * 30, "stated", "states_value_of", "CBK",
            recorded="CBK", result="agree")]},
        {"definition": F + "Customers/RiskRating", "items": [item("FIN-003", "Customer due-diligence review memo", "c4r",
            "The memo rates the customer Low risk.", "inherited", "states_value_of", "Low", recorded="Medium", result="differ")]},
        {"definition": None, "items": [item("FIN-001", "Banking facility letter", "c4b", "Repayments are due monthly.", "derived")]}]},
    "concepts": [{"id": "customer-segment", "label": "Customer segment", "status": "proposed", "type": "attribute",
                  "definition": "The business segment of a customer.", "chunks": ["c4a"],
                  "links": [{"definition": F + "Customers/CustomerSegment", "status": "proposed", "predicate": "exactMatch"}]}],
    "comparisons": [
        {"definition": F + "Customers/CustomerSegment", "chunkId": "c4a", "sourceId": S + "FIN-001", "stated": "CBK", "unit": None, "recorded": "CBK", "result": "agree"},
        {"definition": F + "Customers/RiskRating", "chunkId": "c4r", "sourceId": S + "FIN-003", "stated": "Low", "unit": None, "recorded": "Medium", "result": "differ"}],
    "summary": {"available": True, "label": "Generated summary, for review", "dropped": 1, "sentences": [
        {"text": "The recorded risk rating is Medium; account MY-CA-00004-2 is active.", "cites": ["F5"]},
        {"text": "A facility letter states the segment CBK.", "cites": ["P1", "F6"]}]},
    "citations": {"F5": {"kind": "fact", "text": "Customers › Risk rating: Medium"}, "F6": {"kind": "fact", "text": "Customers › Customer segment: CBK"},
                  "P1": {"kind": "passage", "sourceId": S + "FIN-001", "chunkId": "c4a", "source": "Banking facility letter"}},
    "missing": [], "notes": ["The Customers table holds no customer name. The customer is identified by its Customer ID."], "revision": "abc"}
DOCUMENT = {
    "entity": {"id": JADE["id"], "kind": "document", "label": "Jade Logistics",
               "description": "Evidence-only demo entity. The supplied workbook has no reviewed identity link."},
    "businessDate": None, "profile": {"rows": False, "fields": [], "rm": [], "source": "SQL"}, "holdings": None,
    "evidence": {"source": "Output A", "byTable": [{"table": "No field", "count": 2}], "groups": [{"definition": None, "items": [
        item("jade-adverse-media", "Jade Logistics adverse-media report", "j1", "Adverse Media - JADE LOGISTICS PTE LTD.", "scoped"),
        item("jade_call", "jade_logistics_rm_call.mp3", "jc1", "This is the relationship manager calling about Jade Logistics.", "named")]}]},
    "concepts": [{"id": "adverse-media", "label": "adverse media report", "status": "candidate", "type": "document", "definition": "A media allegation.",
                  "chunks": ["j1"], "links": []}],
    "comparisons": [], "summary": {"available": False, "label": "Generated summary, for review", "sentences": [], "dropped": 0,
                                   "reason": "The model call failed. The rest of the report does not depend on it."},
    "citations": {}, "missing": ["No rows for this entity in the structured tables: it is known from documents only."],
    "notes": ["No link states that Jade Logistics is a customer of the Customers table. It is shown separately (not linked)."], "revision": "def"}
WITHHELD = {**DOCUMENT, "evidence": {"source": "Output A", "byTable": [], "groups": []}, "concepts": [],
            "missing": DOCUMENT["missing"] + ["No document passage this account may read is linked to this entity.",
                                              "No concept of the catalog draws on this entity's passages."],
            "notes": DOCUMENT["notes"] + ["Sources withheld for this account are left out of this report."],
            "summary": {**DOCUMENT["summary"], "reason": "There are no facts or passages to summarize."}}


def fits(page):
    assert page.evaluate("document.documentElement.scrollWidth - innerWidth") <= 0, "horizontal scroll"


def run(base, width):
    errors, searches, reports, held = [], [], [], []
    state = {"hold": False, "reader": False}

    def search(route):
        q = route.request.url.split("q=", 1)[1] if "q=" in route.request.url else ""
        searches.append(q)
        found = [r for r in [JADE, *CUSTOMERS] if q.lower() in r["label"].lower()]
        route.fulfill(json={"results": found[:20], "more": len(found) > 20})

    def report(route):
        body = json.loads(route.request.post_data)
        reports.append(body)
        if state["hold"]:
            return held.append(route)
        out = CUSTOMER if body["entity"].startswith("customer:") else WITHHELD if state["reader"] else DOCUMENT
        route.fulfill(json=out)

    with sync_playwright() as playwright, playwright.chromium.launch() as browser:
        page = browser.new_page(viewport={"width": width, "height": 900})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/api/entity/search?*", search)
        page.route("**/api/entity/report", report)
        state["hold"] = True
        page.goto(base + f"#{DS}/entity")
        page.reload()
        nav = page.locator("#rail nav")
        expect(nav.locator(".nav-group")).to_have_text("Customer view")
        assert [nav.locator(".nav-count").nth(i).inner_text() for i in range(6)] == ["1", "2", "3", "4", "5", "6"]

        # on opening, the default customer report builds by itself, with a plain loading line
        expect(page.locator("[data-progress]")).to_be_visible(timeout=20000)
        for _ in range(50):
            if held:
                break
            page.wait_for_timeout(100)
        # 2026-11-23: the default is a customer the tables hold and a cleaned customer file states
        assert [r["entity"] for r in reports] == ["customer:CUST00007"]
        state["hold"] = False
        held.pop().fulfill(json=CUSTOMER)
        expect(page.locator('[data-report^="customer:"]')).to_be_visible()
        reports.clear()
        page.goto(base + f"#{DS}/entity/entity:jade-logistics")      # a named entity still opens by its address
        page.reload()
        report_box = page.locator('[data-report="entity:jade-logistics"]')
        expect(report_box).to_be_visible()
        expect(page.locator("[data-entity-search]")).to_have_value("")
        expect(page.locator("[data-generate]")).to_be_visible()
        assert page.locator("[data-selected]").count() == 0
        assert "sample" not in page.locator("main").inner_text().lower()
        assert "Selected" not in page.locator("main").inner_text()
        expect(report_box).to_contain_text("Known from documents only")
        expect(report_box.locator("[data-notes]")).to_contain_text("not linked")
        expect(report_box.locator("[data-summary-unavailable]")).to_contain_text("Summary unavailable")
        expect(report_box.locator("[data-missing]")).to_contain_text("known from documents only")
        expect(report_box.locator('[data-section="holdings"]')).to_have_count(0)
        expect(report_box.locator('[data-section="comparisons"]')).to_have_count(0)
        expect(report_box.locator("[data-evidence-item]")).to_have_count(2)
        expect(report_box.locator("[data-evidence-item]").first).to_contain_text("document scoped to this entity")
        fits(page)

        # the picker: at most 20 matches, narrowed by typing
        page.locator("[data-entity-search]").fill("CUST")
        expect(page.locator("[data-results] [data-entity]")).to_have_count(20)
        page.locator("[data-entity-search]").fill("CUST00004")
        expect(page.locator("[data-results] [data-entity]")).to_have_count(1)
        fits(page)

        # a customer with rows: every section, the charts, the links
        page.locator('[data-entity="customer:CUST00004"]').click()
        page.locator("[data-generate]").click()
        box = page.locator('[data-report="customer:CUST00004"]')
        expect(box).to_be_visible()
        for section in ("summary", "profile", "holdings", "comparisons", "evidence", "concepts", "missing"):
            expect(box.locator(f'[data-section="{section}"]')).to_be_visible()
        expect(box.locator("[data-summary] li")).to_have_count(2)
        # polish 8: account numbers are restricted; masked to the last four characters and never in the DOM
        expect(box.locator("[data-accounts] tbody tr").first).to_contain_text("•••• 04-0")
        expect(box.locator("[data-summary]")).to_contain_text("account •••• 04-2 is active")
        assert "MY-CA-00004" not in page.content(), "a full account number is in the DOM"
        # polish 7: plain source names, never "Output A/B/C"
        assert not any(word in page.locator("main").inner_text() for word in ("Output A", "Output B", "Output C"))
        expect(box.locator('[data-section="evidence"]')).to_contain_text("Source: evidence links")
        expect(box.locator('[data-section="concepts"]')).to_contain_text("Source: concept catalog")
        expect(box.locator('[data-section="profile"]')).to_contain_text("Risk rating")
        expect(box.locator('[data-section="profile"]')).to_contain_text("Medium")
        assert "RiskRating" not in box.locator('[data-section="profile"]').inner_text()     # business names, not raw columns
        expect(box.locator("[data-transactions] tbody tr")).to_have_count(3)
        expect(box.locator("[data-transactions]")).to_contain_text("120,000.5 HKD")
        charts = box.locator("[data-chart]")
        expect(charts).to_have_count(4)
        for i in range(4):
            assert charts.nth(i).locator("rect").count() > 0, f"chart {i} is empty"
            assert charts.nth(i).bounding_box()["width"] > 300
        holdings = box.locator('[data-section="holdings"]')
        holdings.get_by_role("group", name="Measure").get_by_role("button", name="Amount USD").click()
        expect(holdings.locator("[data-chart]").nth(1)).to_have_attribute("aria-label", "Amount USD by Transaction date, CASA transactions")
        holdings.get_by_role("group", name="Transaction table").get_by_role("button", name="Loan").click()
        expect(holdings.locator("[data-chart]").nth(1).locator("rect")).to_have_count(1)
        expect(box.locator("[data-comparisons] tbody tr")).to_have_count(2)
        expect(box.locator("[data-comparisons]")).to_contain_text("differ")
        expect(box.locator("[data-group]")).to_have_count(3)
        expect(box.locator("[data-group]").first.locator("h4")).to_have_text("Customers › Customer segment")
        passage = box.locator("[data-open-passage]").first
        assert passage.get_attribute("href") == f"#{DS}/evidence/FIN-001/c4a"
        concept = box.locator("[data-open-concept]").first
        assert concept.get_attribute("href") == f"#{DS}/concepts/customer-segment"
        fits(page)
        passage.click()
        expect(page.locator("main h1")).to_have_text("Evidence")
        page.go_back()
        expect(page.locator("main h1")).to_have_text("Entity 360")
        page.goto(base + f"#{DS}/concepts/customer-segment")
        expect(page.locator("main h1")).to_have_text("Concepts")

        # a restricted reader: the same page, a report with nothing from the withheld sources
        page.request.post(base + "api/session", data={"userId": "finance_restricted"}, headers={"Origin": base.rstrip("/")})
        state["reader"] = True
        page.goto(base + f"#{DS}/entity/entity:jade-logistics")
        page.reload()
        box = page.locator('[data-report="entity:jade-logistics"]')
        expect(box).to_be_visible()
        expect(box.locator("[data-evidence-item]")).to_have_count(0)
        assert "JADE LOGISTICS PTE" not in box.inner_text()
        expect(box.locator("[data-notes]")).to_contain_text("withheld for this account")
        fits(page)

        # a server without the routes answers 404 "Asset unavailable": the page says so plainly, never the raw text
        page.unroute("**/api/entity/report")
        page.route("**/api/entity/report", lambda route: route.fulfill(status=404, json={"error": "Asset unavailable"}))
        page.reload()
        expect(page.locator("[data-entity-error]")).to_contain_text("not available on this server", timeout=20000)
        assert "Asset unavailable" not in page.locator("main").inner_text()

        # Manufacturing: a short note, no customer view in the navigation
        page.request.post(base + "api/session", data={"userId": "admin"}, headers={"Origin": base.rstrip("/")})
        page.goto(base + "#manufacturing/entity")
        page.reload()
        expect(page.locator("[data-entity-unavailable]")).to_be_visible()
        expect(page.locator("#rail .nav-group")).to_have_count(0)
        fits(page)
        assert not errors, errors
        page.screenshot(path=os.environ.get("E360_SHOT_DIR", tempfile.gettempdir()) + f"/e360-mock-{width}.png", full_page=False)
        print(f"Entity 360 picker, default customer report, Jade by address, report sections, charts, links, reader and Manufacturing note passed at {width}px.")


if __name__ == "__main__":
    port, state = free_port(), tempfile.mkdtemp(prefix="v2-e360-")   # an empty private state of its own
    env = {k: v for k, v in os.environ.items() if k not in ("MISTRAL_API_KEY", "V2_ICEBERG_SETTINGS_DIR", "CDE_TOKEN")}
    env["V2_PG_BIN"] = env.get("V2_PG_BIN", "")
    process = subprocess.Popen([str(ROOT / ".venv-v2/bin/python"), "-B", "-m", "semantic_pipeline_v2.workspace_server",
                                "--port", str(port), "--state-dir", state + "/state"],
                               cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        url = f"http://127.0.0.1:{port}/"
        for _ in range(60):
            try:
                urllib.request.urlopen(url + "pipeline.js", timeout=2)
                break
            except OSError:
                time.sleep(1)
        for width in (1440, 1280, 980):
            run(url, width)
    finally:
        process.terminate()
        process.wait(timeout=60)
        shutil.rmtree(state, ignore_errors=True)
