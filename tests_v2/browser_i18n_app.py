"""ZH-T4 (plan 2026-11-23-v2-zh-amp): no fixed English in the React pages when the language is Simplified Chinese.

The language is set to zh-CN. The React pages open in states a user reaches (Ask: an answer, its three views, a
refusal, the write card with a suggested group, a term, the connections panel, the progress panel, a running
request; Evidence and its form rows panel and Approve all; Concepts and its build preview; Model; Entity 360 for Financial Services), with
every server call a test double. The visible text of the page host (text nodes, `title`, `aria-label`, `placeholder`)
is collected and the test fails if a text holds a run of two or more English words, after the text that is data
(titles, names, labels the mocks or the workspace supply; code in `pre` and `code`; quoted passages; the sample
questions, which are questions for the planner) and the product names are taken out.
The allow-list below is data and product names only. Never add fixed UI text to it.
Single English words are printed for review, not failed.
Run with miniconda python3 (Playwright): `python3 tests_v2/browser_i18n_app.py`.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

import browser_ask as A
import browser_entity as E
from browser_form_rows import free_port

ROOT = Path(__file__).resolve().parents[1]
DS = A.DS

# Product and technical names that stay as they are, and the words of identifiers.
PRODUCTS = ["Cloudera", "Cloudera AI", "Cloudera Data Engineering", "Iceberg", "Trino", "Spark", "PostgreSQL", "Polaris", "Mistral",
            "Semantic Hub", "Helios", "CDE", "CDP", "SQL", "CASA", "NRIC", "PDF", "ID", "CA", "JSON", "OK"]
# Data the mocks serve (titles, names, labels, model-written text). Not UI text.
MOCK_DATA = [
    "Q1 date", "Q2 category", "Q3 comparison", "Q4 value", "Q5 clarify", "Q6 form", "Q7 slow", "Q8 refused", "write this", "what does turnover mean",
    "Current account turnover", "savings account", "Credit turnover", "Money paid into an account.", "Sum of amounts.",
    "Turnover means the money paid into an account.", "customer-form3.pdf", "Case status: SAR_FILED / reported",
    "Case status: SAR_FILED", "CASA transaction value: 356152.44", "4 November 2026", "6 November 2026", "SGD, 6 November 2026",
    "Q1 date on 4 November", "Q1 date on 6 November", "Business date", "Customer type", "Concept 0", "Concept 1", "Concept 2", "Concept 3",
    "concept 0", "concept 1", "concept 2", "concept 3", "Private PostgreSQL", "Customer segment", "Customer due-diligence review memo",
    "Banking facility letter", "Jade Logistics adverse-media report", "Jade Logistics", "jade_logistics_rm_call.mp3", "adverse media report",
    "Facility letter for customer CUST00004, segment CBK.", "Long clause text.", "The memo rates the customer Low risk.",
    "Repayments are due monthly.", "Adverse Media - JADE LOGISTICS PTE LTD.", "This is the relationship manager calling about Jade Logistics.",
    "Corporate · CMB", "Known from documents only", "Evidence-only demo entity. The supplied workbook has no reviewed identity link.",
    "The recorded risk rating is Medium; account", "is active.", "A facility letter states the segment CBK.", "Primary RM", "Relationship Manager",
    "Banking facility letter", "A media allegation.", "The business segment of a customer.", "Money paid in.", "Corporate", "Individual",
    "Persons the model holds.", "Transaction ID", "Written at", "Written by", "Source document", "Row in the form", "Question asked",
    "Passage text number", "money in", "source currency", "Business column", "AI hint text from the model."]
# Fixed server sentences the server dictionary does not hold yet. Listed so the gap is visible; remove an entry when
# zh-CN.server.js gains it. Each is server text, so it is not translated by this step.
SERVER_GAPS = [
    "previous period", "next period", "completes the question",                       # answer_detail.py suggestions
    "CASA transaction value needs business date and currency.", "The question could not be planned.",   # refusal and clarification reasons
    "is not set", "Address reachable", "cde.example did not answer.", "No field fits.",                 # connections.py, labeller reasons
    "Written to the local database",                                                    # ask_actions.py label with a count
    "Generated summary, for review", "The model call failed. The rest of the report does not depend on it.",     # entity_report.py
    "The Customers table holds no customer name. The customer is identified by its Customer ID.",
    "No link states that Jade Logistics is a customer of the Customers table. It is shown separately (not linked).",
    "No rows for this entity in the structured tables: it is known from documents only."]

COLLECT = """() => {
  const roots = [document.querySelector('#pf-root'), ...document.querySelectorAll('[data-open-connections]')].filter(Boolean);
  // the library writes edge labels from node ids ("Edge from q to t:CASA_Txn"); they are not page text
  const SKIP = 'script,style,pre,code,blockquote,.pf-quote,[data-sample],.pf-user-bubble,[data-greeting],[data-data],.react-flow__edge';
  const visible = el => { for (let e = el; e && e !== document.body; e = e.parentElement) { const s = getComputedStyle(e); if (s.display === 'none' || s.visibility === 'hidden') return false; } return true; };
  const out = [];
  for (const root of roots) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const node = walker.currentNode, text = node.nodeValue.replace(/\\s+/g, ' ').trim();
      const el = node.parentElement;
      if (!text || !el || el.closest(SKIP) || !visible(el)) continue;
      out.push(text);
    }
    root.querySelectorAll('[title],[aria-label],[placeholder]').forEach(el => { if (el.closest(SKIP) || !visible(el)) return;
      ['title', 'aria-label', 'placeholder'].forEach(name => { const v = el.getAttribute(name); if (v) out.push(v); }); });
  }
  return out;
}"""

DATA_STRINGS = """() => {
  const set = new Set();
  const data = (window.V2_WORKSPACE_DATA?.datasets || []).find(item => item.id === %r);
  const walk = value => { if (typeof value === 'string') { if (value.length >= 3 && value.length < 600) set.add(value); }
    else if (Array.isArray(value)) value.forEach(walk); else if (value && typeof value === 'object') Object.values(value).forEach(walk); };
  walk(data);
  const ids = [...(data?.knowledge?.structured?.fields || []), ...(data?.knowledge?.structured?.metrics || []), ...(data?.knowledge?.structured?.datasets || [])].map(item => item.id);
  ids.forEach(id => { const label = V2Pipeline.definitionLabel(data, id); set.add(label); label.split(' › ').forEach(part => set.add(part)); });
  return [...set];
}""" % DS

WORD = r"[A-Za-z][A-Za-z'’]*"
RUN = re.compile(rf"{WORD}(?:\s+{WORD})+")
SINGLE = re.compile(r"[A-Za-z]{4,}")
PRODUCT_RE = re.compile(r"(?<![A-Za-z])(" + "|".join(sorted(map(re.escape, PRODUCTS), key=len, reverse=True)) + r")(?![A-Za-z])")


class Checker:
    def __init__(self, page):
        self.page, self.failures, self.singles, self.checked = page, [], {}, 0
        self.data = []

    def load_data(self):
        found = self.page.evaluate(DATA_STRINGS)
        self.data = sorted({s for s in [*found, *MOCK_DATA, *SERVER_GAPS, *ZH_DATA] if len(s) >= 3}, key=len, reverse=True)

    def snap(self, label):
        texts = self.page.evaluate(COLLECT)
        assert texts, f"{label}: no text collected"
        for text in texts:
            rest = text
            if text.endswith("…"):
                if any(text[:-1] in item for item in self.data):
                    continue        # a clipped data string (a definition)
                rest = text[:-1].rsplit(" ", 1)[0]       # an axis label clipped inside its last word
            for item in self.data:
                if item in rest:
                    rest = rest.replace(item, " ")
            rest = PRODUCT_RE.sub(" ", rest)
            for run in RUN.findall(rest):
                self.failures.append((label, run, text))
            for word in SINGLE.findall(rest):
                if not RUN.search(rest):
                    self.singles.setdefault(word, label)
        self.checked += len(texts)


def reply_with(question, **extra):
    return A.reply(question, **extra)


# A clarification as the manufacturing data source sends it (24 November 2026): a reason that starts with a value, two
# notes (one per line), question hints that carry the chosen definition, and a choice card with details.
SOLD = "manufacturing:definition:demo/metric/sales_ordered_units"
ZH_CLARIFY = {
    "Q9 period": reply_with("Q9 period", status="clarify", reason="Sales ordered units needs period, and the question does not give it. Name it.",
                            hintKind="question", hintNote="Dates in the data run from 30 May 2022 to 29 June 2025.\nName one product, for example: "
                            "AWC Logo Cap, Adjustable Race and All-Purpose Bike Stand. The data holds 504 of them.",
                            caveats=["The data holds no Product name named 'bike', so the answer is not limited to it."],
                            hints=[{"label": y, "text": f"What were the Sales ordered units in {y}?", "definition": SOLD} for y in ("2025", "2024")]),
    "Q10 choice": reply_with("Q10 choice", status="clarify", reason="2 records of Sales_Store are named 'Friendly Bike Shop'. Give the "
                             "BusinessEntityID of the one you mean.", hintKind="choice",
                             hints=[{"label": "All of these (2 files)", "text": "Write a.pdf and b.pdf to the STR_Transaction table"},
                                    {"label": "a.pdf", "detail": "Rows for STR_Transaction", "text": "Write a.pdf to the STR_Transaction table"},
                                    {"label": "The recorded figure", "text": "What was the limit? Give the recorded figure."},
                                    {"label": "One total, not per group", "text": "What was the value?"}])}
ZH_DATA = ["Sales ordered units", "30 May 2022", "29 June 2025", "AWC Logo Cap", "Adjustable Race", "All-Purpose Bike Stand", "product",
           "Product name", "Sales_Store", "Friendly Bike Shop", "BusinessEntityID", "a.pdf", "STR_Transaction",
           "What were the Sales ordered units in 2025?"]          # the English a clicked choice sent: shown as asked
CARD_TEXT = """card => [...card.querySelectorAll('[data-refusal], [data-caveat], [data-hint-note], [data-hints] h3, [data-hint-button]')]
  .map(el => el.innerText.replace(/\\s+/g, ' ').trim())"""


def run(base):
    errors = []
    state = {"held": [], "ice_ok": False}
    links = [dict(k) for k in A.LINKS]
    links.append({**A.LINKS[0], "chunk_id": "c4", "definition_id": A.TYPE, "status": "corrected", "changed": [], "actor": "admin",
                  "recorded_at": "2026-11-22T10:00:00Z"})
    links.append({**A.LINKS[0], "chunk_id": "c5", "definition_id": None, "relation": "none", "status": "proposed", "notes": {"reason": "No field fits."}})
    chunks = [{"chunk_id": f"c{i}", "source_id": A.FORM, "source_revision": "a" * 64, "order": i - 1, "page": 0, "text": f"Passage text number {i}"} for i in range(1, 6)]
    concepts = [{"id": f"k{i}", "label": f"Concept {i}", "name": f"concept {i}", "status": status, "origin": origin, "synonyms": ["money in"],
                 "definition": "Money paid in." if i == 0 else None,
                 "links": [{"definition_id": A.CASA, "status": "proposed", "predicate": "exactMatch", "reason": "x",
                            "support": {"states_value_of": 2, "is_about": 1}},
                           {"definition_id": A.TYPE, "status": "rejected", "predicate": "closeMatch", "reason": "x", "support": {"is_about": 1}}] if i < 2 else [],
                 "evidence": {"chunks": ["c1"] if i < 3 else []},
                 "evidenceDetail": [{"sourceId": A.FORM, "chunkId": "c1", "quote": "Money paid in." if i == 0 else ""}] if i < 3 else []}
                for i, (status, origin) in enumerate([("proposed", "both"), ("candidate", "document"), ("rejected", "document"), ("corrected", "both")])]
    preview = {"version": "cat-2", "counts": {"concepts": 4, "links": 3, "byStatus": {"proposed": 2, "candidate": 1, "corrected": 1}},
               "incremental": {"new_sources": ["cleaned_pdf_3"], "earlier_concepts": 3, "earlier_concepts_with_new_evidence": 1}}

    def router(route):
        body = json.loads(route.request.post_data)
        text = body["text"]
        if text.lower().startswith("write this"):
            source = {"kind": "answer", "handle": body["context"]["lastAnswer"]}
            out = A.preview_of(source, "iceberg_cde", False)
            out["source"] = source
            out["suggested"] = {"sources": [{"sourceId": A.FORM, "title": "customer-form3.pdf", "rows": 2}],
                                "reason": "Their evidence links fill the same tables, so one job can write them together.",
                                "hint": {"text": "AI hint text from the model.", "label": "AI-written hint, from the evidence links. Check the preview before you confirm."}}
            return route.fulfill(json={"tool": "write", "result": {**out, "warnings": [A.NOT_SUBMITTED]}})
        if text.lower().startswith("what does"):
            return route.fulfill(json={"tool": "semantic", "result": {"term": "turnover", "concepts": [
                {"id": "k0", "label": "Credit turnover", "status": "corrected", "definition": "Money paid into an account.", "synonyms": [], "definitions": [A.CASA]},
                {"id": "k1", "label": "Concept 1", "status": "candidate", "definition": None, "synonyms": [], "definitions": []}],
                "definitions": [{"id": A.CASA, "kind": "metric", "label": "CASA transaction value", "definition": "Sum of amounts."}],
                "passages": [{"chunkId": "c1", "sourceId": A.FORM, "quote": "Turnover means the money paid into an account."}]}})
        if "connections" in text.lower():
            return route.fulfill(json={"tool": "connections", "result": A.connection_targets(state["ice_ok"])})
        if text == "Q7 slow":
            return state["held"].append(route)
        if text == "Q8 refused":
            return route.fulfill(json={"tool": "answer", "result": reply_with(text, status="refused", reason="The question could not be planned.",
                                                                              refusalReason="not planned"), "trace": A.REFUSED})
        if body.get("hint"):         # a clicked choice: the server reads the English it sent and says so (Chinese mode)
            state.setdefault("clicked", []).append(body)
            result = {**A.Q["Q1 date"], "question": text, "questionEnglish": text}
            return route.fulfill(json={"tool": "answer", "result": result, "trace": A.TRACE, "question": text, "questionEnglish": text})
        if text in ZH_CLARIFY:
            return route.fulfill(json={"tool": "answer", "result": ZH_CLARIFY[text], "trace": A.TRACE})
        if text == "Q5 clarify":
            return route.fulfill(json={"tool": "answer", "result": A.Q[text], "trace": A.TRACE})
        route.fulfill(json={"tool": "answer", "result": A.Q.get(text, A.Q["Q1 date"]), "trace": A.TRACE})

    def write(route):
        action = route.request.url.rsplit("/", 1)[1]
        body = json.loads(route.request.post_data)
        if action == "preview":
            out = A.preview_of(body["source"], body.get("target", "postgres"), False)
            return route.fulfill(json=out)
        route.fulfill(json={"status": "written", "postgres": {"written": 2, "found": 2, "expected": 2}, "job": None,
                            "labels": ["Written to the local database: 2 rows", A.NOT_SUBMITTED], "snapshots": [A.snapshot_of(2)],
                            "trace": [A.step("local", "Write to the local database", written=2, found=2, expected=2,
                                             tables=[{"table": "v2_ask.v2_ask_casa_transaction_value", "total": 2, "added": 2}])]})

    def detail(route):
        body = json.loads(route.request.post_data)
        route.fulfill(json=A.DETAIL.get((body["handle"], body["mode"]), {"suggestions": []} if body["mode"] == "next" else {"kind": None}))

    def physical(route):
        rows = [{"table": "STR_Transaction", "rowKey": "1", "status": "previewed",
                 "cells": [{"column": "CaseStatus", "value": "SAR_FILED", "status": "ok", "reason": None},
                           {"column": "Amount", "value": "x", "status": "flagged", "reason": "check"}]}]
        route.fulfill(json={"sourceId": A.FORM, "table": "STR_Transaction", "writeAvailable": True, "tables": ["STR_Transaction"], "rows": rows,
                            "target": "Private PostgreSQL", "fingerprint": "f" * 64,
                            "iceberg": {"available": True, "reason": None, "jobs": 1, "rows": 1, "cells": 2, "approval": "a1", "tables": ["lakehouse.s.t"], "targets": ["Iceberg lakehouse.s.t"]}})

    def approve(route):
        route.fulfill(json={"approved": 2, "revisions": [1, 2], "skipped": {"rejected": 0, "approved": 1}})

    def entity_search(route):
        route.fulfill(json={"results": [E.JADE, *E.CUSTOMERS[:3]], "more": True})

    def entity_report(route):
        body = json.loads(route.request.post_data)
        route.fulfill(json=E.CUSTOMER if body["entity"].startswith("customer:") else E.DOCUMENT)

    with sync_playwright() as playwright, playwright.chromium.launch() as browser:
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.add_init_script("try { localStorage.setItem('v2.lang', 'zh-CN'); } catch (e) {}")
        page.on("pageerror", lambda error: (errors.append(str(error)), print("PAGE ERROR", error)))
        page.route("**/api/answer/detail", detail)
        page.route("**/api/ask", router)
        page.route("**/api/ask/progress?*", lambda route: route.fulfill(json={"steps": A.LIVE, "done": False}))
        page.route("**/api/ask/write/*", write)
        page.route("**/api/connections**", lambda route: route.fulfill(json=A.connection_targets(state["ice_ok"])) if route.request.method == "GET"
                   else route.fulfill(json={"ok": False, "checks": [{"name": "Address reachable", "ok": False, "required": True, "message": "cde.example did not answer."}]}))
        page.route("**/api/physical-rows**", physical)
        page.route("**/api/catalog-correction/approve-all", approve)
        page.route("**/api/catalog-correction", lambda route: route.fulfill(json={"revision": 1}))
        page.route("**/api/catalog/build/preview", lambda route: route.fulfill(json=preview))
        page.route("**/api/evidence-links/export?*", lambda route: route.fulfill(
            body="".join(json.dumps(k) + "\n" for k in links), content_type="application/x-ndjson"))
        page.route("**/api/chunks?*", lambda route: route.fulfill(json={"chunks": chunks}))
        page.route("**/api/catalog?*", lambda route: route.fulfill(json={"version": "cat-1", "concepts": concepts, "editable": True}))
        page.route("**/api/entity/search?*", entity_search)
        page.route("**/api/entity/report", entity_report)

        page.goto(base + f"#{DS}/ask")
        page.reload()
        assert page.evaluate("document.documentElement.lang") == "zh-CN"
        expect(page.locator("[data-greeting]")).to_be_visible(timeout=20000)
        check = Checker(page)
        check.load_data()
        check.snap("ask empty")

        def ask(text):
            page.locator("#pf-question").fill(text)
            page.locator(".pf-composer button[type=submit]").click()
            card = page.locator("[data-answer], [data-tool-card]").last
            expect(card.locator("h2")).to_have_text(text, timeout=20000)
            return card

        # a running request, then the answer
        page.locator("#pf-question").fill("Q7 slow")
        page.locator(".pf-composer button[type=submit]").click()
        expect(page.locator("[data-pending-turn] [data-step]").first).to_be_visible(timeout=20000)
        check.snap("ask running")
        state["held"].pop().fulfill(json={"tool": "answer", "result": A.Q["Q1 date"], "trace": A.TRACE})
        expect(page.locator("[data-answer]")).to_have_count(1, timeout=20000)
        card = page.locator("[data-answer]").last
        card.locator("[data-steps] .pf-linkish").first.click()
        expect(card.locator("[data-step]").first).to_be_visible()
        for step in card.locator("[data-step] summary").all():
            step.click()
        page.wait_for_timeout(400)
        check.snap("ask answer, steps open")
        card.locator("[data-steps] .pf-linkish").nth(1).click()       # side panel
        expect(page.locator("[data-progress]")).to_be_visible()
        check.snap("ask progress panel")
        page.locator("[data-progress] .pf-icon-button").click()
        card.locator(".pf-seg button").nth(1).click()
        expect(card.locator("[data-result-row]")).to_have_count(1)
        card.locator("[data-rows-behind] button").first.click()
        expect(card.locator("[data-rows-caption]")).to_be_visible()
        card.locator(".pf-result .pf-linkish").first.click()
        page.wait_for_timeout(600)
        check.snap("ask table and chart")
        card.locator(".pf-seg button").nth(2).click()
        page.wait_for_timeout(800)
        check.snap("ask path")
        card.locator(".pf-seg button").nth(0).click()
        expect(card.locator("[data-suggestions]")).to_be_visible()
        check.snap("ask answer, suggestions")

        # comparison, a one-value answer, clarification, refusal
        for question in ("Q3 comparison", "Q4 value", "Q5 clarify", "Q8 refused"):
            card = ask(question)
            if question == "Q3 comparison":
                card.locator(".pf-seg button").nth(1).click()
                page.wait_for_timeout(600)
                check.snap("ask comparison chart")
                card.locator(".pf-seg button").nth(0).click()
            if question == "Q4 value":
                card.locator(".pf-seg button").nth(1).click()
                page.wait_for_timeout(600)
                check.snap("ask table without chart")
                card.locator(".pf-seg button").nth(0).click()
            if question == "Q8 refused":
                card.locator("[data-steps] .pf-linkish").first.click()
            check.snap("ask " + question)

        # clarifications in Chinese: no fixed English on the card (reason, caveat, notes, heading, labels, details)
        for question in ZH_CLARIFY:
            card = ask(question)
            shown = page.evaluate(f"({CARD_TEXT})(document.querySelectorAll('[data-answer]')[document.querySelectorAll('[data-answer]').length - 1])")
            assert len(shown) >= 4, shown
            left = []
            for text in shown:
                rest = text
                for item in sorted(ZH_DATA, key=len, reverse=True):
                    rest = rest.replace(item, " ")
                left += RUN.findall(rest)
            assert not left, (question, left, shown)
            check.snap("ask " + question)
        # a click on a choice: the new turn shows the choice's label and what was asked, as "理解为"
        card = page.locator("[data-answer]", has=page.locator("h2", has_text="Q9 period"))
        card.locator("[data-hint-button]").first.click()
        turn = page.locator("[data-answer]").last
        expect(turn.locator("h2")).to_have_text("2025", timeout=20000)
        expect(turn.locator("[data-understood]")).to_have_text("理解为：What were the Sales ordered units in 2025?")
        assert state["clicked"][-1]["definition"] == SOLD and state["clicked"][-1]["hint"] is True, state["clicked"]

        # the term tool, connections, the write card
        ask("what does turnover mean")
        check.snap("ask term")
        page.locator("[data-open-connections]").click()
        expect(page.locator("[data-connections]")).to_be_visible()
        page.locator("[data-connection=iceberg_cde] .pf-actions button").last.click()
        page.wait_for_timeout(600)
        check.snap("ask connections")
        page.locator("[data-connections] .pf-card-head button").click()
        ask("Q1 date")
        ask("write this")
        write_card = page.locator("[data-write]").last
        expect(write_card.locator("[data-suggested]")).to_be_visible(timeout=20000)
        write_card.locator("[data-spark] summary").click()
        check.snap("ask write card")
        write_card.locator("[data-confirm-write]").click()
        expect(write_card.locator("[data-snapshots]")).to_be_visible(timeout=20000)
        check.snap("ask write confirmed")
        page.locator("[data-save-result]")           # present only on the plain answer card
        # a restored exchange: the notice
        page.locator("[data-history-item]").last.click()
        page.wait_for_timeout(800)
        check.snap("ask history opened")
        page.locator(".pf-history-clear").click()
        check.snap("ask history cleared")

        # Evidence
        page.goto(base + f"#{DS}/evidence")
        expect(page.locator(".pf-toolbar")).to_be_visible(timeout=20000)
        page.wait_for_timeout(800)
        check.snap("evidence overall graph")
        page.locator(".pf-toolbar .pf-seg button").nth(1).click()
        check.snap("evidence overall list")
        page.locator(".pf-toolbar select").first.select_option(A.FORM)
        expect(page.locator(".pf-form-rows")).to_be_visible(timeout=20000)
        page.locator(".pf-toolbar .pf-seg button").nth(1).click()
        expect(page.locator("[data-link]").first).to_be_visible(timeout=20000)
        page.locator(".pf-actions button.small").first.click()
        check.snap("evidence link editor")
        page.locator(".pf-editor .pf-actions .secondary").click()
        page.locator(".pf-form-rows .pf-actions button").first.click()     # Preview rows
        page.wait_for_timeout(500)
        check.snap("evidence form rows")
        page.locator(".pf-toolbar .pf-seg button").nth(0).click()
        page.wait_for_timeout(800)
        check.snap("evidence source graph")
        page.locator("[data-approve-all]").click()
        expect(page.locator("dialog[open]")).to_be_visible()
        check.snap("evidence approve all dialog")
        page.locator("[data-approve-confirm]").click()
        expect(page.locator("[data-approve-done]")).to_be_visible(timeout=20000)
        check.snap("evidence approved")

        # Concepts
        page.goto(base + f"#{DS}/concepts")
        expect(page.locator(".pf-split")).to_be_visible(timeout=20000)
        check.snap("concepts list")
        page.locator("[data-build-preview]").click()
        expect(page.locator("[data-build-counts]")).to_be_visible(timeout=20000)
        check.snap("concepts build preview")
        page.locator(".pf-list button").nth(1).click()
        page.locator(".pf-evidence-head").first.click()
        check.snap("concepts detail, evidence open")
        page.locator(".pf-detail .pf-actions button").nth(2).click()
        check.snap("concepts rename")
        page.locator("[data-approve-all]").click()
        check.snap("concepts approve all dialog")
        page.keyboard.press("Escape")
        page.locator(".pf-toolbar input[type=search]").fill("zzzz")
        check.snap("concepts filter, none match")
        page.locator(".pf-toolbar input[type=search]").fill("")
        page.locator(".pf-toolbar select").select_option("candidate")
        check.snap("concepts filter on")
        page.locator(".pf-toolbar select").select_option("all")
        page.locator(".pf-toolbar .pf-seg button").nth(1).click()
        page.wait_for_timeout(800)
        check.snap("concepts graph")

        # Model
        page.goto(base + f"#{DS}/model")
        expect(page.locator(".pf-split.wide")).to_be_visible(timeout=20000)
        page.wait_for_timeout(800)
        check.snap("model")
        page.locator(".pf-table-node").first.click()
        page.locator(".pf-toolbar input[type=checkbox]").check()
        page.wait_for_timeout(500)
        check.snap("model table selected, system fields")

        # Entity 360
        page.goto(base + f"#{DS}/entity")
        page.reload()
        expect(page.locator('[data-report^="customer:"]')).to_be_visible(timeout=20000)
        page.locator("[data-entity-search]").fill("CUST")
        expect(page.locator("[data-results]")).to_be_visible()
        check.snap("entity customer report, picker")
        page.locator(".e360 .pf-link-button").first.click()
        page.locator("[data-entity-search]").fill("zz")
        page.wait_for_timeout(600)
        check.snap("entity picker")
        page.goto(base + f"#{DS}/entity/entity:jade-logistics")
        page.reload()
        expect(page.locator('[data-report="entity:jade-logistics"]')).to_be_visible(timeout=20000)
        check.snap("entity document report")
        assert not errors, errors

    return check


def report(check):
    print(f"Checked {check.checked} texts.")
    if check.singles:
        print("Single English words left (review, not a failure):")
        for word, label in sorted(check.singles.items()):
            print(f"  {word}  ({label})")
    if check.failures:
        seen = set()
        print(f"FAILED: {len(check.failures)} English runs in zh-CN:")
        for label, run_text, text in check.failures:
            if (label, run_text) not in seen:
                seen.add((label, run_text))
                print(f"  [{label}] {run_text!r}   <- {text[:100]!r}")
        raise SystemExit(1)
    print("No fixed English left in the React pages in zh-CN.")


if __name__ == "__main__":
    port, state = free_port(), tempfile.mkdtemp(prefix="v2-i18n-app-")
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
        report(run(url))
    finally:
        process.terminate()
        process.wait(timeout=60)
        shutil.rmtree(state, ignore_errors=True)
