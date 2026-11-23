"""Mocked browser check of the Ask page (2026-11-22) and of "Approve all" on Evidence and Concepts, at 1440, 1280
and 980 px. Also the Progress panel (live steps while a request runs, done and failed steps, the expand) and the table
snapshot after a confirmed write.

Starts its own server on a free port with an empty private state and no provider key. Every /api/answer,
/api/answer/detail, /api/physical-rows, evidence-link, catalog and approve-all response is a test double written here;
no provider or shared database is called.
Run with miniconda python3 (Playwright): `python3 tests_v2/browser_ask.py`.
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
SHOTS = os.environ.get("V2_SHOTS", tempfile.gettempdir())
DS = "financial-services"
DEF = f"{DS}:definition:finance_workbook_demo"
CASA = f"{DEF}/metric/casa_transaction_value"
CUSTOMERS = f"{DEF}/metric/customers_on_business_date"
TYPE = f"{DEF}/field/Customers/CustomerType"
STR_AMOUNT = f"{DEF}/field/STR_Transaction/AmountOriginal"
FORM = f"{DS}:source:cleaned_pdf_3"
SQL = "SELECT SUM(casa_txn.amount) AS metric_value\nFROM v2_facts_financial_services.casa_txn\nWHERE casa_txn.businessdate = '2026-11-05'\nLIMIT 100"


def fact(definition, value, filters, handle, rows=True, tables=("casa_txn",)):
    return {"definition": definition, "label": "x", "value": value, "sql": SQL, "kind": "metric", "filters": filters,
            "detail": {"id": handle, "rows": rows, "tables": list(tables)}}


def reply(question, status="answered", **extra):
    return {"status": status, "reason": None, "question": question, "dataset": DS, "definitions": [], "facts": [],
            "citations": [], "conclusion": None, "summary": None, "comparison": None, "entities": [], "notes": [],
            "plan": {"measure": None, "time_field": None, "evidence": []}, "detail": "r-" + question[:2], **extra}


HINT = "Q5 hint on 6 November 2026 for currency SGD?"
Q = {
    "Q1 date": reply("Q1 date", facts=[fact(CASA, 356152.44, {"BusinessDate": "2026-11-05", "Currency": "SGD"}, "h1")],
                     summary="CASA transaction value: 356152.44", conclusion=356152.44, definitions=[CASA],
                     semantics={"mode": "published", "release": {"version": 3, "catalogVersion": "cat-7"},
                                "concepts": [{"id": "k0", "label": "Current account turnover", "status": "corrected",
                                              "matched": "savings account", "via": "synonym", "definitions": [CASA], "used": True}],
                                "definitionsUsed": [CASA]}),
    "Q2 category": reply("Q2 category", facts=[fact(CUSTOMERS, 12, {"CustomerType": "Corporate"}, "h2", tables=("customers",))],
                         conclusion=12, definitions=[CUSTOMERS],
                         semantics={"mode": "published", "release": None, "concepts": [], "definitionsUsed": [CUSTOMERS]}),
    "Q3 comparison": reply("Q3 comparison", facts=[fact(CASA, 50000, {"BusinessDate": "2026-11-05"}, "h3")], conclusion=True,
                           comparison={"op": ">=", "left": {"from": "fact", "value": 50000}, "right": {"from": "N1", "value": 45000},
                                       "value": True}),
    "Q4 value": reply("Q4 value", facts=[fact(TYPE, "Corporate", {"CustomerID": "CUST00008"}, "h4", rows=False, tables=("customers",))],
                      conclusion="Corporate"),
    "Q5 clarify": reply("Q5 clarify", status="clarify", reason="CASA transaction value needs business date and currency.",
                        hintKind="question", hintNote="Business dates in the data: 4 November 2026, 5 November 2026 and 6 November 2026.",
                        hints=[{"label": "6 November 2026 · SGD", "text": HINT},
                               {"label": "5 November 2026 · SGD", "text": "Q5 hint on 5 November 2026 for currency SGD?"}],
                        plan={"measure": "metric/casa_transaction_value", "time_field": None, "evidence": []}),
    "Q5 plain": reply("Q5 plain", status="clarify", reason="CASA transaction value needs business date and currency.",
                       plan={"measure": "metric/casa_transaction_value", "time_field": None, "evidence": []}),
    "Q6 form": reply("Q6 form", facts=[], conclusion="SAR_FILED", summary="Case status: SAR_FILED",
                     citations=[{"source": FORM, "title": "customer-form3.pdf", "chunkId": "c1",
                                 "span": "Case status: SAR_FILED / reported", "role": "states"}]),
}
DETAIL = {
    ("h1", "rows"): {"columns": [{"name": "TransactionID", "label": "Transaction ID"}, {"name": "Amount", "label": "Amount"}],
                     "rows": [{"TransactionID": "T1", "Amount": 356000.0}, {"TransactionID": "T2", "Amount": 152.44}],
                     "total": 2, "aggregate": "sum", "measure": "Amount"},
    ("h1", "series"): {"kind": "date", "grain": "day", "axis": "Business date", "unit": "source currency",
                       "period": {"from": "2026-11-05", "to": "2026-11-05"},
                       "points": [{"key": "2026-11-04", "value": 300000, "answered": False},
                                  {"key": "2026-11-05", "value": 356152.44, "answered": True},
                                  {"key": "2026-11-06", "value": 310000, "answered": False}]},
    ("h2", "series"): {"kind": "category", "axis": "Customer type", "unit": "count", "value": "Corporate",
                       "points": [{"key": "Corporate", "value": 12, "answered": True}, {"key": "Individual", "value": 39, "answered": False}]},
    ("h4", "series"): {"kind": None},
    ("r-Q1", "next"): {"suggestions": [{"label": "4 November 2026", "question": "Q1 date on 4 November", "why": "previous period"},
                                       {"label": "6 November 2026", "question": "Q1 date on 6 November", "why": "next period"}]},
    ("r-Q5", "next"): {"suggestions": [{"label": "SGD, 6 November 2026", "question": "Q1 date", "why": "completes the question"}]},
}
LINKS = [
    {"source_id": FORM, "chunk_id": "c1", "definition_id": STR_AMOUNT, "relation": "states_value_of", "status": "proposed",
     "stated_value": "500", "unit": "SGD", "period": None, "notes": {}, "entity_keys": [], "span": {"text": "x"}},
    {"source_id": FORM, "chunk_id": "c2", "definition_id": TYPE, "relation": "is_about", "status": "degraded",
     "stated_value": None, "unit": None, "period": None, "notes": {}, "entity_keys": [], "span": {"text": "y"}},
    {"source_id": FORM, "chunk_id": "c3", "definition_id": TYPE, "relation": "is_about", "status": "rejected",
     "stated_value": None, "unit": None, "period": None, "notes": {}, "entity_keys": [], "span": {"text": "z"}},
]
CONCEPTS = [{"id": f"k{i}", "label": f"Concept {i}", "name": f"concept {i}", "status": status, "origin": origin, "synonyms": [],
             "definition": "d", "links": [], "evidence": {"chunks": []}, "evidenceDetail": []}
            for i, (status, origin) in enumerate([("proposed", "both"), ("candidate", "document"), ("rejected", "document"),
                                                  ("corrected", "both")])]


SPARK = {"name": "v2-ask-rows-abc", "script": "# generated PySpark\nrows = [('T1', 356000.0)]\n" + "print(1)\n" * 40,
         "spec": {"name": "v2-ask-rows-abc", "type": "spark", "spark": {"file": "v2_ask_rows.py"}},
         "command": "cde-cli spark submit --vcluster-endpoint $CDE_VCLUSTER_ENDPOINT v2_ask_rows.py", "state": "generated"}
WIDE = {f"business_column_{i}": f"a-long-value-{i}-xxxxxxxx" for i in range(14)}     # a wide sample must not widen the page
NOT_SUBMITTED = "Iceberg: Spark job generated, not submitted (no live connection)"


def step(key, title, state="done", ms=5, start=0, **detail):
    return {"key": key, "title": title, "state": state, "startMs": start, "ms": ms if state not in ("waiting", "skipped", "running") else None,
            "detail": detail}


WHY = "No write, term or connection wording: a question about the data and documents."
TRACE = [step("route", "Route", tool="answer", why=WHY),
         step("understand", "Understand the question", ms=40, start=5, entities=[{"key": "CASA_Txn.Currency=SGD", "how": "stated"}],
              periods=["5 November 2026"], numbers=[], concepts=["Current account turnover"]),
         step("plan", "Plan", ms=2100, start=45, measure="metric/casa_transaction_value", aggregate=None, time=["D1"], needs=[],
              filters=[{"field": "field/CASA_Txn/Currency", "op": "=", "value": "SGD"}], modelCalls=2),
         step("query", "Query the data", ms=80, start=2145, facts=[{"definition": CASA, "value": 356152.44, "tables": ["casa_txn"], "sql": SQL}]),
         step("read", "Read the documents", ms=300, start=2225, retrieved=4, cited=0, documents=[]),
         step("decide", "Check and decide", ms=2, start=2525, status="answered", conclusion=356152.44, comparison=None, refusalReason=None)]
REFUSED = [TRACE[0], TRACE[1],
           step("plan", "Plan", "failed", ms=3000, start=45, modelCalls=2,
                stopped={"status": "refused", "reason": "The question could not be planned.", "guard": "not planned"}),
           step("query", "Query the data", "skipped"), step("read", "Read the documents", "skipped"), step("decide", "Check and decide", "skipped")]
LIVE = [TRACE[0], TRACE[1], step("plan", "Plan", "running", start=45), step("query", "Query the data", "waiting"),
        step("read", "Read the documents", "waiting"), step("decide", "Check and decide", "waiting")]
PREVIEW_TRACE = [step("preview", "Build the preview", tables=[{"name": "v2_ask.v2_ask_casa_transaction_value", "rowCount": 2, "columns": 2}]),
                 step("spark", "Generate the Spark job", name="v2-ask-rows-abc"),
                 step("local", "Write to the local database", "waiting", text="Waits for Confirm write."),
                 step("iceberg", "Submit to Iceberg", "skipped", text=NOT_SUBMITTED)]


def snapshot_of(added):
    return {"target": "postgres", "table": "v2_ask.v2_ask_casa_transaction_value", "total": 2, "added": added,
            "columns": ["transactionid", "amount"], "rows": [{"transactionid": "T1", "amount": 356000.0}, {"transactionid": "T2", "amount": 152.44}],
            "readAt": "2026-11-22T10:00:00+00:00"}


def preview_of(source, target, submits):
    table = "v2_ask.v2_ask_casa_transaction_value" if source["kind"] == "answer" else "STR_Transaction"
    return {"fingerprint": "f" * 64, "approval": "a" * 32,
            "target": {"id": target, "label": {"postgres": "Private PostgreSQL (local)",
                                                "iceberg_cde": "Iceberg through Cloudera Data Engineering"}[target], "submits": submits},
            "tables": [{"name": table, "icebergName": "lakehouse.demo_silver.v2_ask_casa_transaction_value",
                        "columns": [{"name": "transactionid", "type": "String"}, {"name": "amount", "type": "Decimal"}],
                        "rowCount": 2, "sample": [{"transactionid": "T1", "amount": 356000.0, **WIDE}, {"transactionid": "T2", "amount": 152.44, **WIDE}]}],
            "warnings": ["At most 10,000 rows are written."] + ([] if submits else [NOT_SUBMITTED]), "spark": SPARK,
            "trace": PREVIEW_TRACE}


def connection_targets(ice_ok):
    fields = {name: {"value": None, "secret": name in ("access_key_secret", "cde_token"), "set": name == "access_key_secret" and ice_ok}
              for name in ("cde_url", "cdp_endpoint", "access_key_id", "access_key_secret", "cde_token", "iceberg_catalog", "iceberg_namespace")}
    test = {"ok": True, "at": "2026-11-22T10:00:00Z", "checks": [{"name": "Address reachable", "ok": True, "required": True, "message": "The CDE endpoint answered."},
                                                                {"name": "Polaris", "ok": False, "required": False, "message": "Polaris did not answer."}]}
    return {"targets": [{"id": "postgres", "kind": "postgres", "label": "Private PostgreSQL (local)", "configured": True, "fields": {}, "lastTest": None},
                        {"id": "iceberg_cde", "kind": "iceberg_cde", "label": "Iceberg through Cloudera Data Engineering", "configured": ice_ok,
                         "problem": None if ice_ok else "cde_url is not set", "fields": fields, "lastTest": test if ice_ok else None}]}


def run(base, width):
    errors, answers, writes, approvals, asks, previews, conn_posts, langs = [], [], [], [], [], [], [], []
    state = {"form": "previewed", "links": [dict(k) for k in LINKS], "concepts": [dict(c) for c in CONCEPTS], "ice_ok": False,
             "held": [], "confirms": 0, "polls": [], "gone": set(), "details": []}

    def answer(route):
        body = json.loads(route.request.post_data)
        answers.append(body["question"])
        route.fulfill(json=Q.get(body["question"], Q["Q1 date"]))

    def router(route):
        body = json.loads(route.request.post_data)
        asks.append(body)
        langs.append(route.request.headers.get("x-v2-lang"))
        text = body["text"]
        if "QX7" in text:                                               # a Chinese question (plan Z7): the server's English form
            english = "How many units of QX7 are in stock?"
            return route.fulfill(json={"tool": "answer", "result": {**Q["Q1 date"], "question": text, "questionEnglish": english},
                                       "trace": TRACE, "question": text, "questionEnglish": english})
        if text.lower().startswith("write this"):
            source = {"kind": "answer", "handle": body["context"]["lastAnswer"]}
            return route.fulfill(json={"tool": "write", "result": {**preview_of(source, "iceberg_cde", False), "source": source,   # as the server
                "warnings": [NOT_SUBMITTED, "The Iceberg connection is not ready: cde_url is not set. The rows are written to the local database only."]}})
        if text.lower().startswith("write inspection"):                 # a discovered table: proposed from the document's labels
            table = {"name": "v2_ask.ext_doc_inspection_a1b2c3", "icebergName": "lakehouse.demo_silver.ext_doc_inspection_a1b2c3",
                     "discovered": True, "proposed": "doc_inspection_a1b2c3", "rowCount": 1,
                     "columns": [{"name": "site_name", "type": "String", "definition": CASA},
                                 {"name": "inspection_date", "type": "Date", "definition": None},
                                 {"name": "v2_source_id", "type": "String", "definition": None}],
                     "sample": [{"site_name": "Example Plant", "inspection_date": "2026-11-03", "v2_source_id": "s1"}]}
            return route.fulfill(json={"tool": "write", "result": {**preview_of({"kind": "document"}, "postgres", False), "tables": [table],
                "source": {"kind": "document", "sourceId": "s1"},
                "warnings": ["The table is proposed from the document's own fields, not from a declared model table.", NOT_SUBMITTED]}})
        if text.lower().startswith("what does"):
            return route.fulfill(json={"tool": "semantic", "result": {"term": "turnover", "concepts": [
                {"id": "k0", "label": "Credit turnover", "status": "corrected", "definition": "Money paid into an account.", "synonyms": [], "definitions": [CASA]}],
                "definitions": [{"id": CASA, "kind": "metric", "label": "CASA transaction value", "definition": "Sum of amounts."}],
                "passages": [{"chunkId": "c1", "sourceId": FORM, "quote": "Turnover means the money paid into an account."}]}})
        if "connections" in text.lower():
            return route.fulfill(json={"tool": "connections", "result": connection_targets(state["ice_ok"])})
        answers.append(text)
        if text == "Q7 slow":                                          # held until the test has seen the live steps
            return state["held"].append(route)
        if text == "Q9 follow":                                         # a follow-up the server made standalone
            full = "Q1 date on 7 November 2026"
            return route.fulfill(json={"tool": "answer", "result": {**Q["Q1 date"], "question": text, "questionStandalone": full},
                                       "trace": TRACE, "question": text, "questionStandalone": full})
        if text == "Q8 refused":
            return route.fulfill(json={"tool": "answer", "result": reply(text, status="refused", reason="The question could not be planned.",
                                                                          refusalReason="not planned"), "trace": REFUSED})
        route.fulfill(json={"tool": "answer", "result": Q.get(text, Q["Q1 date"]), "trace": TRACE})

    def progress(route):
        state["polls"].append(route.request.url)
        route.fulfill(json={"steps": LIVE, "done": False})

    def write(route):
        action = route.request.url.rsplit("/", 1)[1]
        body = json.loads(route.request.post_data)
        writes.append(action)
        if action == "preview":
            previews.append(body)
            target = body.get("target", "postgres")
            return route.fulfill(json=preview_of(body["source"], target, target == "iceberg_cde" and state["ice_ok"]))
        if action == "confirm":
            submitted = previews[-1].get("target") == "iceberg_cde" and state["ice_ok"]
            state["confirms"] += 1
            added = 2 if state["confirms"] == 1 else 0
            return route.fulfill(json={"status": "submitted" if submitted else "written", "postgres": {"written": 2, "found": 2, "expected": 2},
                                       "job": {"name": "v2-ask-rows-abc", "runId": 77, "status": "submitted", "message": "One Spark job submitted."} if submitted else None,
                                       "labels": ["Written to the local database: 2 rows", "Iceberg: ..." if submitted else NOT_SUBMITTED],
                                       "snapshots": [snapshot_of(added)],
                                       "trace": [step("local", "Write to the local database", written=added, found=2, expected=2,
                                                      tables=[{"table": "v2_ask.v2_ask_casa_transaction_value", "total": 2, "added": added}]),
                                                 step("iceberg", "Submit to Iceberg", "skipped", text=NOT_SUBMITTED)]})
        route.fulfill(json={"name": "v2-ask-rows-abc", "runId": 77, "status": "completed", "message": "Post-check passed."})

    def connections(route):
        if route.request.method == "POST":
            conn_posts.append((route.request.url.split("/api/connections/")[1], json.loads(route.request.post_data)))
            if route.request.url.endswith("/test"):
                return route.fulfill(json={"ok": False, "checks": [{"name": "Address reachable", "ok": False, "required": True, "message": "cde.example did not answer."}]})
            return route.fulfill(json=connection_targets(state["ice_ok"])["targets"][1])
        route.fulfill(json=connection_targets(state["ice_ok"]))

    def detail(route):
        body = json.loads(route.request.post_data)
        assert set(body) == {"dataset", "handle", "mode"}, body          # a handle, never SQL
        state["details"].append(body)
        if body["handle"] in state["gone"]:                                 # the server restarted: the handle is gone
            return route.fulfill(status=404, json={"error": "This answer is no longer held by the server. Ask the question again."})
        route.fulfill(json=DETAIL.get((body["handle"], body["mode"]), {"suggestions": []} if body["mode"] == "next" else {"kind": None}))

    def physical(route):
        if route.request.method == "POST":
            action = route.request.url.rsplit("/", 1)[1]
            writes.append(action)
            if action == "confirm":
                state["form"] = "written"
        rows = [{"table": "STR_Transaction", "rowKey": "1", "status": state["form"],
                 "cells": [{"column": "CaseStatus", "value": "SAR_FILED", "status": "ok", "reason": None}]}]
        route.fulfill(json={"sourceId": FORM, "table": "STR_Transaction", "writeAvailable": True, "tables": ["STR_Transaction"],
                            "rows": rows, "target": "Private PostgreSQL", "fingerprint": "f" * 64, "iceberg": None})

    def approve(route):
        body = json.loads(route.request.post_data)
        approvals.append(body)
        pool = state["links"] if body["kind"] == "evidence_link" else state["concepts"]
        done = 0
        for item in pool:
            key = {"chunkId": item["chunk_id"], "definitionId": item["definition_id"]} if body["kind"] == "evidence_link" else item["id"]
            if key in body["items"] and item["status"] not in ("rejected", "corrected"):
                item["status"], done = "corrected", done + 1
        route.fulfill(json={"approved": done, "revisions": list(range(done)), "skipped": {"rejected": 0, "approved": 0}})

    with sync_playwright() as playwright, playwright.chromium.launch() as browser:
        page = browser.new_page(viewport={"width": width, "height": 900})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/api/answer", answer)
        page.route("**/api/ask", router)
        page.route("**/api/ask/progress?*", progress)
        page.route("**/api/ask/write/*", write)
        page.route("**/api/connections**", connections)
        page.route("**/api/answer/detail", detail)
        page.route("**/api/physical-rows**", physical)
        page.route("**/api/catalog-correction/approve-all", approve)
        page.route("**/api/evidence-links/export?*", lambda route: route.fulfill(
            body="".join(json.dumps(k) + "\n" for k in state["links"]), content_type="application/x-ndjson"))
        page.route("**/api/catalog?*", lambda route: route.fulfill(json={"version": "cat-1", "concepts": state["concepts"], "editable": True}))
        page.goto(base + f"#{DS}/ask")
        page.reload()

        def ask(text, selector="[data-answer]"):
            page.get_by_label("Question").fill(text)
            page.get_by_role("button", name="Ask", exact=True).click()
            card = page.locator(selector).last          # chat order: the newest exchange sits above the composer
            expect(card.locator("h2")).to_have_text(text, timeout=20000)
            return card

        def tab(card, name):
            card.get_by_role("group", name="Answer view").get_by_role("button", name=name).click()

        def open_history():
            expect(page.locator("[data-chat]")).to_be_visible(timeout=20000)        # the page has mounted
            if not page.locator("[data-history]").is_visible():
                page.get_by_role("button", name="Show history", exact=True).click()
            expect(page.locator("[data-history]")).to_be_visible()

        def close_history():
            expect(page.locator("[data-chat]")).to_be_visible(timeout=20000)
            if page.locator("[data-history]").is_visible():
                page.get_by_role("button", name="Hide history", exact=True).click()
            expect(page.locator("[data-history]")).to_have_count(0)

        def apart(label):
            """History, chat column and side panel never intersect and keep at least a 16 px gap."""
            shown = [s for s in ("[data-history]", "[data-chat]", "[data-progress]") if page.locator(s).count() and page.locator(s).is_visible()]
            boxes = [(s, page.locator(s).bounding_box()) for s in shown]
            for i, (sa, a) in enumerate(boxes):
                for sb, b in boxes[i + 1:]:
                    gap = max(b["x"] - a["x"] - a["width"], a["x"] - b["x"] - b["width"], b["y"] - a["y"] - a["height"], a["y"] - b["y"] - b["height"])
                    assert gap >= 15.5, (label, width, sa, a, sb, b)
            assert not page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth"), (label, width)

        def steps_of(card):
            """The inline steps at the top of a reply, expanded."""
            steps = card.locator("[data-steps]").first
            toggle = steps.get_by_role("button", name="Show steps")
            if toggle.count():
                toggle.click()
            expect(steps.locator("[data-step]").first).to_be_visible()
            return steps

        # empty state: a greeting, the composer in the middle, the sample requests under it (no repeating tooltip)
        expect(page.locator("[data-greeting]")).to_contain_text("a question", timeout=20000)
        samples = page.locator("[data-sample]")
        expect(samples).to_have_count(3)
        assert all(samples.nth(i).get_attribute("title") is None for i in range(3))
        composer = page.locator(".pf-composer").bounding_box()
        middle = composer["y"] + composer["height"] / 2
        assert 0.2 * 900 < middle < 0.8 * 900, ("composer in the vertical middle", composer)
        expect(page.locator("[data-chat]")).to_contain_text("Figures come from SQL. Statements come from cited passages.")
        for label, action in (("history open", open_history), ("history closed", close_history)):
            action()
            apart("empty, " + label)
        page.screenshot(path=f"{SHOTS}/chat-mock-empty-{width}.png")

        # Q1: a metric on a business date. Answer is the default view.
        card = ask("Q1 date")
        expect(card.locator("[data-user-bubble]")).to_have_text("Q1 date")
        composer = page.locator(".pf-composer").bounding_box()                       # docked at the bottom
        assert composer["y"] + composer["height"] > 900 - 80, ("composer docked", composer)
        # polish 13: the measure is filtered to exactly one currency, so the conclusion carries it (from the filter, by code)
        expect(card.locator(".pf-conclusion strong")).to_have_text("SGD 356,152.44")
        # inline steps: collapsed to one line when finished, expanded in place
        expect(card.locator("[data-steps-summary]").first).to_have_text("6 steps · 2.5 s")
        panel = steps_of(card)
        expect(panel.locator("[data-step]")).to_have_count(6)
        expect(panel.locator('[data-state="done"]')).to_have_count(6)
        expect(panel.locator('[data-step="plan"] [data-line]')).to_have_text("Measure: CASA transactions › CASA transaction value; repaired once")
        expect(panel.locator('[data-step="plan"] [data-ms]')).to_have_text("2.1 s")
        expect(panel.locator('[data-step="understand"] [data-line]')).to_have_text("1 record, 1 period found; 1 published concept matched")
        expect(panel.locator('[data-step="read"] [data-line]')).to_have_text("4 passages retrieved, 0 cited")
        panel.locator('[data-step="plan"] summary').click()                                  # expand
        expect(panel.locator('[data-step="plan"] [data-step-more]')).to_contain_text("Filter: CASA transactions › Transaction currency = SGD")
        expect(panel.locator('[data-step="plan"] [data-step-more]')).to_contain_text("2 model calls")
        expect(panel.locator('[data-step="plan"] [data-step-more]')).to_contain_text("Period: 5 November 2026")
        panel.locator('[data-step="query"] summary').click()
        expect(panel.locator('[data-step="query"] [data-step-sql]')).to_contain_text("SUM(casa_txn.amount)")
        text = panel.inner_text()
        assert "metric/" not in text and ":definition:" not in text and "CASA_Txn" not in text, text
        page.screenshot(path=f"{SHOTS}/chat-mock-steps-{width}.png")
        # the same steps in a side panel: its own column, pushes the chat, never overlaps; closed by default
        expect(page.locator("[data-progress]")).to_have_count(0)
        card.get_by_role("button", name="Open in side panel").first.click()
        side = page.locator("[data-progress]")
        expect(side.locator("[data-step]")).to_have_count(6)
        expect(side.locator("[data-progress-title]")).to_have_text("Q1 date")
        for label, action in (("history open", open_history), ("history closed", close_history)):
            action()
            if width < 1100 and label == "history open":       # below 1100 px one side area at a time: history closes the panel
                expect(side).to_have_count(0)
            apart("side panel, " + label)
        if not side.count():
            card.get_by_role("button", name="Open in side panel").first.click()
        apart("side panel only")
        page.screenshot(path=f"{SHOTS}/chat-mock-side-{width}.png")
        side.get_by_role("button", name="Close steps").click()
        expect(page.locator("[data-progress]")).to_have_count(0)
        expect(card.get_by_role("button", name="Answer", exact=True)).to_have_attribute("aria-pressed", "true")
        assert asks[-1]["context"] == {"lastAnswer": None, "lastForm": None}, asks[-1]       # one thread through /api/ask
        assert "history" not in asks[-1], asks[-1]                                          # the first turn of a chat session
        semantic = card.locator("[data-semantic]")                                         # C1: visible without a click
        expect(semantic.locator("[data-release]")).to_have_text("Published release 3 · catalog cat-7")
        expect(semantic.locator("[data-concept]")).to_contain_text("Current account turnover")
        expect(semantic.locator("[data-concept]")).to_contain_text("used by the plan")
        assert semantic.locator("[data-concept] a").get_attribute("href").endswith("/concepts/k0")
        expect(semantic.locator("[data-definition]")).to_contain_text("CASA transaction value")
        # C3: save this result as a table: preview, two separate parts, nothing written before the click
        card.locator("[data-save-result]").click()
        writecard = card.locator("[data-write]")
        expect(writecard.locator("[data-write-table]")).to_contain_text("2 rows")
        assert previews[-1] == {"dataset": DS, "source": {"kind": "answer", "handle": "h1"}, "target": "postgres"}, previews
        expect(writecard.locator("[data-target]")).to_contain_text("Private PostgreSQL (local)")
        expect(writecard.get_by_label("Also submit to Iceberg")).to_have_count(0)           # connection not tested
        expect(writecard.locator("[data-iceberg-state]")).to_have_text("Generated, not submitted — no live connection")
        writecard.locator("[data-spark] summary").click()
        assert not page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth"), "write card scroll"
        expect(writecard.locator("[data-script]")).to_contain_text("generated PySpark")
        expect(writecard.locator("[data-command]")).to_contain_text("cde-cli spark submit")
        assert "fingerprint=" + "f" * 64 in writecard.get_by_role("link", name="Download script").get_attribute("href")
        assert "confirm" not in writes
        wsteps = steps_of(writecard)
        expect(wsteps.locator('[data-step="preview"] [data-line]')).to_have_text("2 rows in 1 table")
        expect(wsteps.locator('[data-step="local"]')).to_have_attribute("data-state", "waiting")
        assert page.evaluate("getComputedStyle(document.querySelector('[data-write-table] .pf-rows-wrap')).overflowY") == "auto"
        writecard.locator("[data-confirm-write]").click()
        expect(writecard.locator("[data-local-result]")).to_have_text("Written to the local database: 2 rows · read back 2 of 2")
        # the snapshot: read back from the written table; business names, raw names as tooltips
        snap = writecard.locator("[data-snapshot]")
        expect(snap.locator("[data-snapshot-count]")).to_contain_text("2 new rows; the table holds 2 rows.")
        expect(snap.locator("tbody tr")).to_have_count(2)
        assert snap.locator("th").first.get_attribute("title") == "transactionid"
        expect(snap.locator("[data-snapshot-source]")).to_contain_text("Read from v2_ask.v2_ask_casa_transaction_value in the local database at")
        expect(writecard.locator('[data-part="iceberg"] [data-snapshot]')).to_have_count(0)          # never under Iceberg
        box = snap.locator(".pf-rows-wrap").bounding_box()
        assert box["height"] <= 330, ("the table scrolls in its own box", box)
        wsteps = steps_of(writecard)
        expect(wsteps.locator('[data-step="local"]')).to_have_attribute("data-state", "done")
        expect(wsteps.locator('[data-step="local"] [data-line]')).to_have_text("2 new rows; read back 2 of 2")
        expect(wsteps.locator('[data-step="iceberg"] [data-line]')).to_have_text("Spark job generated, not submitted (no live connection)")
        snap.scroll_into_view_if_needed()
        page.screenshot(path=f"{SHOTS}/chat-mock-write-{width}.png")
        expect(writecard.locator("[data-iceberg-state]")).to_have_text("Generated, not submitted — no live connection")
        assert writes.count("confirm") == 1
        writecard.get_by_role("button", name="Close").click()
        tab(card, "Table & chart")
        row = card.locator("[data-result-row]")
        expect(row).to_contain_text("CASA transaction value")
        expect(row).to_contain_text("Business date = 5 Nov 2026")
        expect(row.locator("[data-value]")).to_have_text("356,152.44")
        card.get_by_role("button", name="SQL").click()
        expect(card.locator(".pf-sql")).to_contain_text("SUM(casa_txn.amount)")
        card.get_by_role("button", name="Show the first 50 rows").click()
        expect(card.locator("[data-rows-caption]")).to_contain_text("All 2 rows")
        expect(card.locator("[data-rows-behind] tbody tr")).to_have_count(2)
        expect(card.locator('[data-chart-kind="date"] [data-bar]')).to_have_count(3)
        expect(card.locator('[data-bar="answered"]')).to_have_count(1)
        expect(card.locator("figcaption")).to_contain_text("not part of the answer")
        tab(card, "Path")
        nodes = card.locator(".react-flow__node")
        expect(nodes.first).to_be_visible(timeout=10000)
        titles = card.locator(".pf-node-title").all_inner_texts()
        assert any("CASA transaction value" in t for t in titles) and any("356,152.44" in t for t in titles), titles
        assert not any("/" in t or ":definition:" in t for t in titles), titles
        # suggestions: generated by code; a chip re-asks
        next_steps = card.locator("[data-suggestions]")
        expect(next_steps).to_contain_text("Generated by code")
        expect(next_steps.locator("[data-suggestion]")).to_have_count(2)
        next_steps.locator("[data-suggestion]").first.click()
        expect(page.locator("[data-answer]").last.locator("h2")).to_have_text("Q1 date on 4 November", timeout=20000)
        assert answers[-1] == "Q1 date on 4 November", answers
        # chat session: the second request carries the first turn, compact, as context for a follow-up
        assert asks[-1]["history"] == [{"text": "Q1 date", "status": "answered", "said": "CASA transaction value: 356152.44"}], asks[-1]
        # the understood form of a turn travels with it: the next follow-up builds on it, not on the typed text
        ask("Q9 follow")
        ask("Q10 next")
        assert asks[-1]["history"][-1] == {"text": "Q9 follow", "understood": "Q1 date on 7 November 2026", "status": "answered",
                                           "said": "CASA transaction value: 356152.44"}, asks[-1]
        assert all("understood" not in turn for turn in asks[-1]["history"][:-1]), asks[-1]

        # Q2: a category chart; Q3: a comparison chart; Q4: a value lookup has no rows and no chart
        card = ask("Q2 category")
        expect(card.locator(".pf-conclusion strong")).to_have_text("12")                  # polish 13: no currency filter, no code
        expect(card.locator("[data-release]")).to_have_text("No published release: answers use the model only")
        tab(card, "Table & chart")
        expect(card.locator('[data-chart-kind="category"] [data-bar]')).to_have_count(2)
        card = ask("Q3 comparison")
        tab(card, "Table & chart")
        expect(card.locator('[data-chart-kind="comparison"] [data-bar]')).to_have_count(2)
        card = ask("Q4 value")
        tab(card, "Table & chart")
        expect(card.locator("[data-no-chart]")).to_be_visible()
        expect(card.locator("[data-chart]")).to_have_count(0)
        expect(card.locator("[data-rows-behind]")).to_have_count(0)

        # Q5: a clarification; its chip completes and re-asks the question; its path names the metric
        card = ask("Q5 clarify")
        tab(card, "Path")
        expect(card.locator(".pf-node-title", has_text="Needs clarification")).to_be_visible(timeout=10000)
        expect(card.locator(".pf-node-title", has_text="CASA transaction value")).to_be_visible()
        # its hints (24 November 2026): the note, one heading, buttons; a keyboard press sends exactly the hint's text
        # as the next turn of this session, flagged as a hint, with the clarification in the history
        tab(card, "Answer")
        card = page.locator("[data-answer]", has=page.locator("h2", has_text="Q5 clarify")).last     # fixed, not "the newest"
        hints = card.locator("[data-hints]")
        expect(hints.locator("[data-hint-note]")).to_contain_text("Business dates in the data")
        expect(hints.locator("h3")).to_have_text("You can ask:")
        expect(hints.locator("[data-hint-button]")).to_have_count(2)
        box = hints.bounding_box()
        assert box["x"] + box["width"] <= page.viewport_size["width"], f"hints overflow at {width}px"
        hints.locator("[data-hint-button]").first.focus()
        page.keyboard.press("Enter")
        expect(page.locator("[data-answer]").last.locator("h2")).to_have_text(HINT, timeout=20000)
        assert asks[-1]["text"] == HINT and asks[-1]["hint"] is True, asks[-1]
        assert asks[-1]["history"][-1]["text"] == "Q5 clarify" and asks[-1]["history"][-1]["status"] == "clarify", asks[-1]
        # a card with hints shows no suggestion that only completes the question (its detail was read, then dropped)
        expect(card.locator("[data-suggestion]")).to_have_count(0)
        assert any(d.get("handle") == "r-Q5" for d in state["details"]), state["details"]
        # a clarification without hints keeps that suggestion; clicking it asks again
        plain = ask("Q5 plain")
        expect(plain.locator("[data-hints]")).to_have_count(0)
        plain.locator("[data-suggestion]").first.click()
        expect(page.locator("[data-answer]").last.locator("h2")).to_have_text("Q1 date", timeout=20000)
        assert "hint" not in asks[-1], asks[-1]                       # a typed or suggested question is not flagged

        # Q6: the operation needs the confirm click
        card = ask("Q6 form")
        operation = card.locator('[data-operation="form"]')
        expect(operation).to_be_visible(timeout=10000)
        confirms = writes.count("confirm")
        operation.click()
        writecard = card.locator("[data-write]")
        expect(writecard).to_contain_text("Load this form's rows")
        assert previews[-1]["source"] == {"kind": "form", "sourceId": FORM}, previews[-1]
        assert writes.count("confirm") == confirms
        writecard.locator("[data-confirm-write]").click()
        expect(writecard.locator("[data-local-result]")).to_contain_text("2 rows")
        expect(writecard.locator("[data-snapshot-count]")).to_contain_text("0 new rows; the table holds 2 rows.")    # a repeat write
        assert writes.count("confirm") == confirms + 1
        writecard.get_by_role("button", name="Close").click()

        # a refused request: the step that refused is failed, the later steps are skipped
        card = ask("Q8 refused")
        expect(card.locator("[data-steps-summary]").first).to_contain_text("stopped at Plan")
        panel = steps_of(card)
        expect(panel.locator('[data-step="plan"]')).to_have_attribute("data-state", "failed")
        expect(panel.locator('[data-step="plan"] [data-line]')).to_have_text("Not answered: The question could not be planned.")
        expect(panel.locator('[data-state="skipped"]')).to_have_count(3)
        # a request still running: the inline steps poll the server's live record, then collapse to one line
        page.get_by_label("Question").fill("Q7 slow")
        page.get_by_role("button", name="Ask", exact=True).click()
        live = page.locator("[data-pending-turn] [data-steps]")
        expect(live.locator('[data-step="plan"]')).to_have_attribute("data-state", "running", timeout=10000)
        expect(live.locator('[data-state="waiting"]')).to_have_count(3)
        expect(live.locator('[data-state="done"]')).to_have_count(2)
        expect(page.locator("[data-pending-turn] [data-user-bubble]")).to_have_text("Q7 slow")
        assert state["polls"] and all("id=" in url and f"dataset={DS}" in url for url in state["polls"]), state["polls"]
        page.screenshot(path=f"{SHOTS}/chat-mock-running-{width}.png")
        state["held"].pop().fulfill(json={"tool": "answer", "result": Q["Q1 date"] | {"question": "Q7 slow"}, "trace": TRACE})
        expect(page.locator("[data-answer]").last.locator("h2")).to_have_text("Q7 slow", timeout=20000)
        expect(page.locator("[data-answer]").last.locator("[data-steps-summary]").first).to_have_text("6 steps · 2.5 s")
        # an older reply opens its own steps in the side panel
        page.locator("[data-answer]", has_text="Q8 refused").get_by_role("button", name="Open in side panel").first.click()
        side = page.locator("[data-progress]")
        expect(side.locator("[data-progress-title]")).to_have_text("Q8 refused")
        expect(side.locator('[data-step="plan"]')).to_have_attribute("data-state", "failed")
        side.get_by_role("button", name="Close steps").click()

        # C4: the router. A write text reaches the same preview; a term gets the semantic card
        ask("Q1 date")
        card = ask("write this to Iceberg", "[data-tool-card]")
        expect(card).to_have_attribute("data-tool-card", "write")
        assert asks[-1]["context"]["lastAnswer"] == "h1", asks[-1]
        expect(card.locator("[data-target]")).to_contain_text("Iceberg through Cloudera Data Engineering")
        expect(card.locator("[data-iceberg-state]")).to_have_text("Generated, not submitted — no live connection")
        expect(card.get_by_text("Save this result as a table", exact=True)).to_have_count(1)   # polish 10: one title, not two
        expect(card).to_contain_text("The Iceberg connection is not ready")
        card.get_by_role("button", name="Cancel").click()
        card = ask("Write inspection_north to a new table", "[data-tool-card]")
        expect(card.get_by_text("Write this document to a new table", exact=True)).to_have_count(1)
        expect(card.locator("[data-proposed-table]")).to_have_text("Proposed table name doc_inspection_a1b2c3")
        expect(card.locator('[data-column="site_name"]')).to_contain_text("CASA transaction value")
        expect(card.locator('[data-column="inspection_date"]')).to_contain_text("Date · no definition yet")
        expect(card.locator('[data-column="v2_source_id"]')).to_have_count(0)                 # system columns are not proposed
        expect(card).to_contain_text("proposed from the document's own fields")
        assert not page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth"), "discovered card scroll"
        card.get_by_role("button", name="Cancel").click()
        card = ask("What does turnover mean?", "[data-tool-card]")
        expect(card).to_have_attribute("data-tool-card", "semantic")
        expect(card).to_contain_text("Credit turnover")
        # polish 12: each passage names its source, as answer citations do
        source = card.locator("[data-passage-source]").first
        expect(source).to_be_visible()
        assert source.inner_text().strip() and ":source:" not in source.inner_text(), source.inner_text()

        # C2: connections. Labels, secrets as set or not set, import, save only what changed, a test line per check
        page.locator("[data-open-connections]").click()
        panel = page.locator("[data-connections]")
        ice = panel.locator('[data-connection="iceberg_cde"]')
        expect(ice).to_contain_text("CDE virtual cluster endpoint")
        expect(ice.locator("[data-secret-state]").first).to_have_text("not set")
        ice.get_by_role("button", name="Import from workspace settings").click()
        expect(ice).to_contain_text("Imported.")
        ice.get_by_label("CDE virtual cluster endpoint").fill("https://cde.example/dex/api/v1")
        ice.get_by_role("button", name="Save").click()
        expect(ice).to_contain_text("Saved.")
        ice.get_by_role("button", name="Test connection").click()
        expect(ice.locator("[data-checks]")).to_contain_text("Failed Address reachable: cde.example did not answer.")
        assert conn_posts == [("iceberg_cde/import", {"from": "workspace"}),
                              ("iceberg_cde", {"fields": {"cde_url": "https://cde.example/dex/api/v1"}}),
                              ("iceberg_cde/test", {})], conn_posts
        # a configured and tested Iceberg connection: the option appears, the job is submitted only on confirm
        state["ice_ok"] = True
        page.locator("[data-open-connections]").click()
        page.locator("[data-open-connections]").click()
        expect(page.locator('[data-connection="iceberg_cde"]')).to_contain_text("test passed")
        page.locator("[data-connections]").get_by_role("button", name="Close").click()
        card = page.locator("[data-answer]").last
        card.locator("[data-save-result]").click()
        writecard = card.locator("[data-write]")
        writecard.get_by_label("Also submit to Iceberg").check()
        expect(writecard.locator("[data-iceberg-state]")).to_have_text("Will be submitted when you confirm")
        assert previews[-1]["target"] == "iceberg_cde"
        writecard.locator("[data-confirm-write]").click()
        expect(writecard.locator("[data-iceberg-state]")).to_have_text("Submitted")
        expect(writecard).to_contain_text("Job v2-ask-rows-abc · run 77")
        writecard.get_by_role("button", name="Check job status").click()
        expect(writecard.locator("[data-iceberg-state]")).to_have_text("Completed")
        writecard.get_by_role("button", name="Close").click()
        page.evaluate("sessionStorage.clear()")
        state["ice_ok"] = False
        overflow = page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
        assert not overflow, f"horizontal page scroll on Ask at {width}px"

        # Connections sits in the page header, beside the title line, not in the chat
        title = page.locator(".pagehead h1").bounding_box()
        button = page.locator("[data-open-connections]").bounding_box()
        assert button["y"] < title["y"] + title["height"] + 40 and button["x"] > title["x"] + 200, (title, button)
        for label, action in (("history open", open_history), ("history closed", close_history)):
            action()
            apart("thread, " + label)

        # Chat sessions: every question so far is one session (one sidebar item) holding all its turns
        open_history()
        hist = page.locator("[data-history]")
        first_turns = page.locator("[data-answer], [data-tool-card]").count()
        expect(hist.locator("[data-history-item]")).to_have_count(1)
        expect(hist.locator("[data-history-item]").first).to_contain_text("Q1 date")
        expect(hist.locator("[data-history-item]").first).to_have_attribute("data-turns", str(first_turns))
        expect(hist.locator("[data-history-item]").first).to_contain_text(f"{first_turns} turns")
        # New chat empties the thread and starts a second session at the next question
        page.locator("[data-history]").get_by_role("button", name="New chat", exact=True).click()
        expect(page.locator("[data-answer]")).to_have_count(0)
        expect(page.locator("[data-tool-card]")).to_have_count(0)
        ask("Q2 category")
        assert "history" not in asks[-1], asks[-1]
        card = ask("write this to Iceberg", "[data-tool-card]")                  # a write preview, left unconfirmed
        assert asks[-1]["history"][0]["text"] == "Q2 category", asks[-1]
        expect(card.locator("[data-confirm-write]")).to_have_count(1)
        open_history()
        expect(hist.locator("[data-history-item]")).to_have_count(2)
        expect(hist.locator("[data-history-item]").first).to_contain_text("Q2 category")
        expect(hist.locator("[data-history-item]").first).to_have_attribute("data-turns", "2")
        # a reload restores the current session; its stored write card is shown as it ended, without Confirm
        page.reload()
        expect(page.locator("[data-answer]").first.locator("h2")).to_have_text("Q2 category", timeout=20000)
        card = page.locator("[data-tool-card]").last
        expect(card.locator("[data-past-write]")).to_be_visible()
        expect(card.locator("[data-confirm-write]")).to_have_count(0)
        expect(card.get_by_role("button", name="Preview again")).to_have_count(1)
        # opening the first session shows its stored turns and runs nothing: no request of any kind
        before = (len(asks), len(answers), len(state["details"]), len(writes), len(previews))
        open_history()
        hist.locator("[data-history-item]", has_text="Q1 date").first.click()
        expect(page.locator("[data-answer], [data-tool-card]")).to_have_count(first_turns, timeout=20000)
        expect(page.locator("[data-answer]").first.locator("h2")).to_have_text("Q1 date")
        stored = page.locator("[data-answer]", has=page.locator("h2", has_text="Q5 clarify")).locator("[data-hints]")
        expect(stored.locator("[data-hint]")).to_have_count(2)              # a stored session shows its hints as text
        expect(stored.locator("[data-hint-button]")).to_have_count(0)
        page.wait_for_timeout(800)
        assert (len(asks), len(answers), len(state["details"]), len(writes), len(previews)) == before, before
        # Preview again is a request only when pressed
        page.locator("[data-answer]").first.get_by_role("button", name="Table & chart").click()
        open_history()
        hist.locator("[data-history-item]", has_text="Q2 category").first.click()
        page.locator("[data-tool-card]").last.get_by_role("button", name="Preview again").click()
        expect(page.locator("[data-tool-card]").last.locator("[data-confirm-write]")).to_have_count(1, timeout=20000)
        assert len(previews) == before[4] + 1, previews
        overflow = page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
        assert not overflow, f"horizontal page scroll on Ask with history at {width}px"
        page.screenshot(path=f"{SHOTS}/chat-mock-history-{width}.png")

        # Approve all on Evidence: two not-yet-reviewed links (the rejected one is left out)
        page.goto(base + f"#{DS}/evidence")
        button = page.locator("[data-approve-all]")
        expect(button).to_have_text("Approve all links · 2", timeout=20000)
        button.click()
        dialog = page.locator("dialog.pf-dialog")
        expect(dialog.locator("[data-approve-count]")).to_contain_text("2")
        expect(dialog.locator("[data-approve-groups]")).to_contain_text("STR transaction 1")
        expect(dialog.locator("[data-approve-flagged]")).to_contain_text("1 of them")
        assert not approvals
        dialog.get_by_role("button", name="Approve 2").click()
        expect(page.locator("[data-approve-done]")).to_have_text("Approved 2.")
        expect(button).to_have_text("Approve all links")
        assert len(approvals) == 1 and len(approvals[0]["items"]) == 2, approvals
        overflow = page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
        assert not overflow, f"horizontal page scroll on Evidence at {width}px"

        # Approve all on Concepts: proposed and candidate only
        page.goto(base + f"#{DS}/concepts")
        button = page.locator("[data-approve-all]")
        expect(button).to_have_text("Approve all concepts · 2", timeout=20000)
        button.click()
        dialog = page.locator("dialog.pf-dialog")
        expect(dialog.locator("[data-approve-groups]")).to_contain_text("anchored 1 · found in documents only 1")
        expect(dialog.locator("[data-approve-flagged]")).to_contain_text("1 of them is flagged candidate")
        dialog.get_by_role("button", name="Cancel").click()
        assert len(approvals) == 1
        button.click()
        page.locator("dialog.pf-dialog").get_by_role("button", name="Approve 2").click()
        expect(page.locator("[data-approve-done]")).to_have_text("Approved 2.")
        assert [c["status"] for c in state["concepts"]] == ["corrected", "corrected", "rejected", "corrected"]

        # polish 1: switching account from the Ask page stays on the Ask page of the same data source
        def switch(user):
            page.locator('[data-action="account"]').click()
            page.locator("#demo-account").select_option(user)
            with page.expect_navigation():
                page.get_by_role("button", name="Switch account").click()
            expect(page.locator('[data-action="account"]')).to_be_visible(timeout=20000)

        page.goto(base + "#manufacturing/ask")
        expect(page.locator("[data-chat]")).to_be_visible(timeout=20000)
        switch("finance_reader")                                  # this account may not open Manufacturing
        expect(page.locator("#dataset-catalog")).to_be_visible(timeout=20000)
        assert page.url.endswith("#datasets"), page.url
        switch("admin")
        page.goto(base + f"#{DS}/ask")
        expect(page.locator("[data-chat]")).to_be_visible(timeout=20000)
        # a reader sees no operation and no approve button
        switch("finance_reader")
        assert page.url.endswith(f"#{DS}/ask"), page.url
        expect(page.locator("[data-chat]")).to_be_visible(timeout=20000)
        # the reader never sees the admin's history or stored answers on the same browser
        open_history()
        expect(page.locator("[data-history-item]")).to_have_count(0)
        expect(page.locator("[data-history]")).not_to_contain_text("Q2 category")
        expect(page.locator("[data-answer]")).to_have_count(0)
        stored = page.evaluate("Object.keys(localStorage).filter(k => k.startsWith('v2-ask-sessions:'))")
        assert stored == ["v2-ask-sessions:admin:" + DS], stored          # keyed by account id and dataset
        expect(page.locator("[data-readonly-note]")).to_have_text("Read-only account")
        close_history()
        page.screenshot(path=f"{SHOTS}/chat-mock-reader-{width}.png")
        state["form"] = "previewed"
        card = ask("Q6 form")
        expect(card.locator("[data-citation]")).to_have_count(1)
        page.wait_for_timeout(800)
        expect(card.locator("[data-operation]")).to_have_count(0)
        card = ask("Q1 date")
        expect(card.locator("[data-semantic]")).to_be_visible()
        expect(card.locator("[data-save-result]")).to_have_count(0)
        expect(card.locator("[data-operation]")).to_have_count(0)
        page.locator("[data-open-connections]").click()
        panel = page.locator("[data-connections]")
        expect(panel.locator('[data-connection="iceberg_cde"]')).to_contain_text("not configured")
        expect(panel.locator("input")).to_have_count(0)
        expect(panel.get_by_role("button", name="Test connection")).to_have_count(0)
        page.goto(base + f"#{DS}/evidence")
        expect(page.get_by_role("group", name="Evidence view")).to_be_visible(timeout=20000)
        expect(page.locator("[data-approve-all]")).to_have_count(0)
        page.goto(base + f"#{DS}/concepts")
        expect(page.get_by_role("group", name="Concept view")).to_be_visible(timeout=20000)
        expect(page.locator("[data-approve-all]")).to_have_count(0)
        page.goto(base + f"#{DS}/ask")
        open_history()
        expect(page.locator("[data-history-item]")).to_have_count(1)            # the reader's own session: two turns
        expect(page.locator("[data-history-item]")).to_have_attribute("data-turns", "2")
        switch("admin")
        assert page.url.endswith(f"#{DS}/ask"), page.url
        open_history()
        expect(page.locator("[data-history-item]")).to_have_count(2)            # the admin's own two sessions
        page.locator("[data-history]").get_by_role("button", name="Clear history", exact=True).click()
        expect(page.locator("[data-history-item]")).to_have_count(0)
        assert page.evaluate("localStorage.getItem('v2-ask-sessions:admin:" + DS + "')") is None
        # Z7 (23 November 2026): in English no /api/ask carries X-V2-Lang; in Chinese each one does, and the English form
        # the server routed shows under the question
        assert langs and not any(langs), langs
        page.evaluate("localStorage.setItem('v2.lang', 'zh-CN')")
        page.reload()
        page.locator("#pf-question").fill("零件 QX7 的库存是多少？")
        page.locator(".pf-composer-bar button[type=submit]").click()
        card = page.locator("[data-answer]").last
        expect(card.locator("[data-understood]")).to_have_text("理解为：How many units of QX7 are in stock?", timeout=20000)
        assert langs[-1] == "zh-CN", langs
        page.evaluate("localStorage.removeItem('v2.lang')")
        assert not errors, errors
        print(f"Ask views, progress panel, snapshot, semantic context, write card, router, connections and Approve all passed at {width}px.")


if __name__ == "__main__":
    port, state = free_port(), tempfile.mkdtemp(prefix="v2-ask-")   # an empty private state of its own
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
