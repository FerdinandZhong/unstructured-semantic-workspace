"""Mocked browser check of the rehearsal UI fixes (23 November 2026) at 1440, 1280 and 980 px.

2 a withheld source shows only "Source withheld"; 3 a long passage is shown in full; 4 an approved link reads "Approved",
a changed one "Corrected"; 5 every relationship line on the Model canvas can be clicked where it is drawn, and opens the
relationship; 6 the publish preview names concepts and fields, raw ids only as tooltips; 9 a count of one is singular;
11 the Publish step reads "Changes to publish" until the latest release holds the current state.

Starts its own server on a free port with an empty private state and no provider key; evidence links, chunks, the
catalog and the publish routes are test doubles written here. Run with miniconda python3: `python3 tests_v2/browser_polish.py`.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from browser_form_rows import free_port

ROOT = Path(__file__).resolve().parents[1]
DS = "financial-services"
DEF = f"{DS}:definition:finance_workbook_demo/field/"
FORM = f"{DS}:source:FIN-007"
SHOTS = os.environ.get("V2_SHOTS", tempfile.gettempdir())
LONG = ("The corporate card programme applies to all cards issued under this agreement. " * 14
        + "The cash advance fee is $879.90 in Sing dollars. THE FOLLOWING SENTENCE MUST BE VISIBLE.")


def link(chunk, definition, status, **extra):
    return {"source_id": FORM, "chunk_id": chunk, "definition_id": DEF + definition, "relation": "states_value_of",
            "status": status, "stated_value": "5000", "unit": "USD", "period": None, "notes": {}, "entity_keys": [],
            "span": {"text": "x"}, "actor": "admin", "recorded_at": "2026-11-22T10:00:00", **extra}


LINKS = [link("c1", "CreditCard_Txn/Amount", "corrected", changed=[]),
         link("c2", "CreditCard_Txn/Amount", "corrected", changed=["definition_id"]),
         link("c2", "CreditCard_Txn/Currency", "proposed")]
CHUNKS = [{"chunk_id": "c1", "order": 0, "page": "None", "text": LONG},
          {"chunk_id": "c2", "order": 1, "page": "None", "text": "The cash advance limit is USD 5,000 per transaction."}]
PREVIEW = {"dataset": DS, "catalogVersion": "cat-9", "modelSha256": "m", "fingerprint": "f" * 64, "nextVersion": 2,
           "counts": {"concepts": 1, "links": 1, "evidenceRefs": 1, "mappingCorrections": 0, "evidenceLinkCorrections": 1},
           "changes": {"concepts": {"added": ["doc:credit-card-programme"], "removed": [], "changed": []},
                       "corrections": {"added": [], "removed": [], "changed": []},
                       "evidenceLinks": {"added": ["3b7801e1c0ffee | " + DEF + "CreditCard_Txn/Amount"], "removed": [], "changed": []}},
           "lint": {"ok": True, "errors": []}, "evidenceLinkCorrections": []}
CATALOG = {"version": "cat-9", "editable": True, "concepts": [
    {"id": "doc:credit-card-programme", "label": "Credit card programme", "name": "credit card programme", "status": "corrected",
     "origin": "document", "synonyms": [], "definition": "d", "links": [], "evidence": {"chunks": []}, "evidenceDetail": []}]}


def run(base, width):
    errors, state = [], {"unchanged": False}
    with sync_playwright() as playwright, playwright.chromium.launch() as browser:
        page = browser.new_page(viewport={"width": width, "height": 900})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/api/evidence-links/export?*", lambda route: route.fulfill(
            body="".join(json.dumps(k) + "\n" for k in LINKS), content_type="application/x-ndjson"))
        page.route("**/api/chunks?*", lambda route: route.fulfill(json={"chunks": CHUNKS}))
        page.route("**/api/catalog?*", lambda route: route.fulfill(json=CATALOG))
        page.route("**/api/publish/history?*", lambda route: route.fulfill(json={"releases": [
            {"version": 1, "publishedAt": "2026-11-22T09:00:00", "principalId": "admin", "catalogVersion": "cat-8",
             "contentSha256": "a" * 64, "counts": {"concepts": 1, "mappingCorrections": 0, "evidenceLinkCorrections": 0}}]}))
        page.route("**/api/publish/preview", lambda route: route.fulfill(json={**PREVIEW, "unchanged": state["unchanged"]}))

        def text():
            return page.locator("main").inner_text() + page.locator("#rail").inner_text()

        def singular():
            found = re.findall(r"\b1 (metrics|links|sources|datasets|tables|fields|relationships|concepts|passages|forms|rows|definitions)\b", text())
            assert not found, (width, found)

        # 11: the Publish step reads "Changes to publish" while the latest release lacks the current state
        page.goto(base + f"#{DS}/evidence/FIN-007/c1")
        rail = page.locator('#rail [data-step="review"] [data-step-state]')
        expect(rail).to_have_text("Changes to publish", timeout=20000)

        # 3: the long passage is shown in full; 4: approved versus corrected
        passage = page.locator("#chunk-c1 .pf-passage p")
        expect(passage).to_contain_text("THE FOLLOWING SENTENCE MUST BE VISIBLE.", timeout=20000)
        assert passage.evaluate("p => p.scrollHeight <= p.clientHeight + 1"), "the passage is clipped"
        expect(page.locator("#chunk-c1 [data-link]").first).to_contain_text("Approved")
        expect(page.locator("#chunk-c1 [data-link]").first).not_to_contain_text("Corrected")
        expect(page.locator("#chunk-c2 [data-link]").first).to_contain_text("Corrected")
        singular()
        page.screenshot(path=f"{SHOTS}/polish-evidence-{width}.png")
        page.get_by_role("group", name="Evidence view").get_by_role("button", name="Graph").click()
        expect(page.locator(".react-flow__node").first).to_be_visible(timeout=10000)
        singular()

        # 5: every relationship line can be clicked where it is drawn, and opens the relationship
        page.goto(base + f"#{DS}/model")
        expect(page.locator(".react-flow__edge").first).to_be_attached(timeout=20000)
        page.wait_for_timeout(600)
        singular()
        edges = page.locator(".react-flow__edge")
        for i in range(edges.count()):
            hit = edges.nth(i).evaluate("""g => {
                const path = g.querySelector('path.react-flow__edge-path'), m = path.getScreenCTM(), total = path.getTotalLength();
                const out = {covered: [], own: 0, id: g.getAttribute('data-testid')};
                for (let t = 0.15; t <= 0.851; t += 0.05) {
                    const p = path.getPointAtLength(total * t);
                    const at = document.elementFromPoint(p.x * m.a + p.y * m.c + m.e, p.x * m.b + p.y * m.d + m.f);
                    if (at && at.closest('.react-flow__node')) out.covered.push(Math.round(t * 100));
                    if (at && at.closest('.react-flow__edge') === g) out.own += 1;
                }
                return out;
            }""")
            # the line runs around the tables, never behind one, and part of it selects this relationship
            assert not hit["covered"] and hit["own"] > 0, ("a relationship line is hidden or not selectable", width, hit)
        wanted = page.locator('.react-flow__edge[data-testid="rf__edge-CreditCard_Txn>Customers"]')
        point = wanted.evaluate("""g => { const path = g.querySelector('path.react-flow__edge-path');
            const p = path.getPointAtLength(path.getTotalLength() * 0.5), m = path.getScreenCTM();
            return {x: p.x * m.a + p.y * m.c + m.e, y: p.x * m.b + p.y * m.d + m.f}; }""")
        page.mouse.click(point["x"], point["y"])
        side = page.locator("[data-relationship]")
        expect(side).to_contain_text("Credit card transactions → Customers")
        side.get_by_role("button", name="Open Customers").click()
        expect(page.locator(".pf-side h2")).to_have_text("Customers")
        page.screenshot(path=f"{SHOTS}/polish-model-{width}.png")

        # 6: the publish preview names concepts and fields; raw ids only as tooltips, and nothing overflows
        page.goto(base + f"#{DS}/review")
        page.get_by_role("button", name="Preview release").click()
        preview = page.locator("[data-release-preview]")
        expect(preview).to_contain_text("Credit card programme", timeout=20000)
        expect(preview).to_contain_text("Credit card transactions › Card transaction amount")
        shown = preview.inner_text()
        assert ":definition:" not in shown and "doc:" not in shown and "3b7801e1" not in shown, shown
        assert preview.locator('[title*=":definition:"]').count() == 1
        assert preview.evaluate("e => e.scrollWidth <= e.clientWidth + 1"), "the preview overflows"
        page.screenshot(path=f"{SHOTS}/polish-publish-{width}.png")

        # 11: once the latest release holds the current state the step reads Done
        state["unchanged"] = True
        page.reload()
        expect(page.locator('#rail [data-step="review"] [data-step-state]')).to_have_text("Done", timeout=20000)

        # 2 and 9: a restricted reader sees "Source withheld" only, and one data source in the singular
        page.request.post(base + "api/session", data={"userId": "finance_restricted"}, headers={"Origin": base.rstrip("/")})
        page.goto(base + "#datasets")
        page.reload()
        expect(page.locator("#dataset-count")).to_have_text("1 dataset", timeout=20000)
        page.goto(base + f"#{DS}/ingest")
        row = page.locator("[data-file-row]", has_text="Source withheld").first
        expect(row).to_be_visible(timeout=20000)
        page.wait_for_timeout(800)
        assert "Not processed" not in row.inner_text() and "Checking" not in row.inner_text(), row.inner_text()
        page.screenshot(path=f"{SHOTS}/polish-sources-{width}.png")
        page.request.post(base + "api/session", data={"userId": "admin"}, headers={"Origin": base.rstrip("/")})
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        assert not errors, errors
        print(f"Rehearsal UI fixes passed at {width}px.")


if __name__ == "__main__":
    port, state = free_port(), tempfile.mkdtemp(prefix="v2-polish-")
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
        for width in [int(w) for w in sys.argv[1:]] or (1440, 1280, 980):
            run(url, width)
    finally:
        process.terminate()
        process.wait(timeout=60)
        shutil.rmtree(state, ignore_errors=True)
