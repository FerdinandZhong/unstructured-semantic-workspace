"""E11 in a browser, 3 widths, against a real workspace server over a private PostgreSQL seeded with synthetic rows.

Run with miniconda python3 (Playwright): python3 tests_v2/browser_catalog_graph.py
The server runs from the V2 environment through serve_catalog_fixture.py on a free port, and only that process is stopped."""
import json
import os
import subprocess
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PG = os.environ.get("V2_PG_BIN", "")
SCOPE = "manufacturing"
WIDTHS = (1440, 980, 390)
ANSWERED = {"status": "answered", "reason": None, "question": "q", "dataset": SCOPE, "definitions": [], "conclusion": True,
            "facts": [{"label": "Settlement days", "value": 9, "filters": {"AccountNumber": "123456"}, "definition": "d"}],
            "citations": [{"source": "s", "title": "Fixture memo", "chunkId": "c", "span": "Settlement takes at most 9 days.", "role": "primary"}],
            "comparison": {"documentValue": "9", "unit": "days", "factValue": 9, "test": "<=", "holds": True, "source": "c"},
            "summary": "Within the limit.", "entities": [], "notes": [], "checked": True}
REFUSED = {**ANSWERED, "status": "refused", "reason": "The source that answers this question is withheld for this account.",
           "refusalReason": "source withheld", "facts": [], "citations": [], "comparison": None, "conclusion": None}


class Server:
    def __init__(self, seed):
        args = [str(ROOT / ".venv-v2/bin/python"), "-B", str(ROOT / "tests_v2/serve_catalog_fixture.py"), PG] + (["--seed"] if seed else [])
        self.process = subprocess.Popen(args, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.url = self.process.stdout.readline().strip()
        assert self.url.startswith("http://127.0.0.1:") and not self.url.endswith(":8137"), self.url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.process.stdin.close()
        self.process.wait(timeout=30)


def fits(page):
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), "horizontal overflow"


def open_editor(page, base, observation=None):
    if observation is None:
        workspace = page.request.get(base + "/api/workspace").json()
        mapping = next(m for m in workspace["datasets"][0]["knowledge"]["mappings"] if m.get("correctionRevision"))
        observation = mapping["observationId"].split(":")[-1]
    page.goto(f"{base}/#{SCOPE}/knowledge/mappings/{observation}")
    page.reload()
    page.locator("[data-open-mapping]").click()
    return observation


def as_reader(context, page, base):
    page.goto(base + "/")
    result = page.evaluate("""() => fetch('/api/session', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({userId: 'manufacturing_reader', recover: true})}).then(r => r.status)""")
    assert result == 200


def run(width, browser):
    with Server(seed=True) as server:
        base = server.url
        context = browser.new_context(viewport={"width": width, "height": 900})
        page = context.new_page()
        page.goto(base + "/")
        observation = open_editor(page, base)
        dialogs = page.locator("dialog[open]")
        expect(dialogs).to_have_count(1)
        expect(dialogs.get_by_role("heading", name="Mapping", exact=True)).to_be_visible()
        evidence, concept = (page.locator(f'[data-editor-section="{name}"]') for name in ("evidence-link", "concept"))
        expect(evidence.locator("[data-link]")).to_be_visible()
        expect(concept.locator("[data-concept]")).to_be_visible()
        expect(page.locator("dialog")).to_have_count(1)
        # edit the evidence link relation and stated value
        evidence.locator("[data-relation]").select_option("is_about")
        evidence.locator("[data-value]").fill("12")
        evidence.locator("[data-save-link]").click()
        expect(evidence.locator("[data-link-status]")).to_contain_text("Saved · revision")
        # edit the concept label and one link: change a predicate, remove the other
        concept.locator("[data-label]").fill("Renamed settlement")
        concept.locator('[data-concept-link="0"] [data-predicate]').select_option("broadMatch")
        concept.locator('[data-toggle-link="1"]').click()
        expect(concept.locator('[data-concept-link="1"]')).to_contain_text("(removed)")
        concept.locator("[data-save-concept]").click()
        expect(concept.locator("[data-concept-notice]")).to_contain_text("Saved · revision")
        fits(page)
        # reload: the change is still there, from the database
        page.reload()
        page.locator("[data-open-mapping]").click()
        evidence, concept = (page.locator(f'[data-editor-section="{name}"]') for name in ("evidence-link", "concept"))
        expect(evidence.locator("[data-relation]")).to_have_value("is_about")
        expect(evidence.locator("[data-value]")).to_have_value("12")
        expect(evidence.locator("[data-link-status]")).to_contain_text("Corrected by admin")
        expect(concept.locator("[data-label]")).to_have_value("Renamed settlement")
        expect(concept.locator('[data-concept-link="0"] [data-predicate]')).to_have_value("broadMatch")
        expect(concept.locator('[data-concept-link="1"]')).to_contain_text("(removed)")
        page.keyboard.press("Escape")
        # the graph reads /api/graph (database rows)
        with page.expect_response(lambda r: r.url.endswith(f"/api/graph?dataset={SCOPE}")) as seen:
            page.goto(f"{base}/#{SCOPE}/knowledge/ontology")
        graph = seen.value.json()
        assert any(n["id"] == "concept:settlement-days" and n["label"] == "Renamed settlement" for n in graph["nodes"])
        assert {"source", "chunk", "concept", "definition"} <= {n["kind"] for n in graph["nodes"]}
        expect(page.locator("#graph-definitions")).to_contain_text("Renamed settlement")
        expect(page.locator("[data-graph-empty]")).to_have_count(0)
        expect(page.get_by_role("button", name="Build concept catalog")).to_be_visible()   # admin
        page.get_by_role("button", name="Build concept catalog").click()   # fixture server has no chat client: a clear refusal
        expect(page.locator("#catalog-build [role=alert]")).to_contain_text("No chat client")
        fits(page)
        # Talk to Data: structured answer block (the route is mocked: no model call)
        replies = iter([ANSWERED, REFUSED])
        page.route("**/api/answer", lambda route: route.fulfill(json=next(replies)))
        page.goto(f"{base}/#{SCOPE}/knowledge/talk")
        for question in ("Is settlement within the limit?", "What is in the withheld memo?"):
            page.locator("#talk-question").fill(question)
            page.get_by_role("button", name="Ask a question").click()
            expect(page.locator(".talk-status")).not_to_contain_text("Checking")
        # the structured answer is requested when the response cites passages, or on the button under the answer
        for done in (1, 2):
            page.locator("[data-load-structured]").first.click()
            expect(page.locator("[data-structured-answer]")).to_have_count(done)
        expect(page.locator(".talk-turn")).to_have_count(2)
        blocks = page.locator("[data-structured-answer]")
        expect(blocks).to_have_count(2)
        expect(blocks.first).to_contain_text("SQL facts")
        expect(blocks.first).to_contain_text("AccountNumber = 123456")
        expect(blocks.first).to_contain_text("Settlement takes at most 9 days.")
        expect(blocks.first.locator("[data-comparison]")).to_contain_text("holds")
        expect(blocks.last.locator("[data-refusal]")).to_contain_text("withheld for this account")
        fits(page)
        # the publish step is real: preview, confirm, history. No fixture title and no simulation wording.
        page.goto(f"{base}/#{SCOPE}/review")
        expect(page.get_by_text("No release published yet.")).to_be_visible()
        body = page.locator("#workspace-main")
        expect(body).not_to_contain_text("Customer risk rating field alignment")
        expect(body).not_to_contain_text("imulation")
        expect(page.get_by_role("button", name="Confirm publish")).to_have_count(0)
        page.get_by_role("button", name="Preview release").click()
        preview = page.locator("[data-release-preview]")
        expect(preview).to_contain_text("Preview of version 1", timeout=30000)   # the export parses the whole model
        expect(preview).to_contain_text("passes the Ossie lint")
        expect(preview).to_contain_text("1 concepts")
        fits(page)
        page.get_by_role("button", name="Confirm publish").click()
        expect(page.get_by_text("Published as version 1.")).to_be_visible(timeout=30000)
        expect(page.get_by_role("link", name="Download YAML")).to_have_count(1)
        expect(page.locator(".receipt-mark")).to_have_text("1")
        released = page.request.get(f"{base}/api/publish/release?dataset={SCOPE}&version=1").text()
        assert "settlement-days" in released and "Renamed settlement" in released
        fits(page)
        # a reader sees no controls
        reader_context = browser.new_context(viewport={"width": width, "height": 900})
        reader = reader_context.new_page()
        as_reader(reader_context, reader, base)
        open_editor(reader, base, observation)
        expect(reader.locator("dialog[open]")).to_have_count(1)
        expect(reader.locator("[data-editor-section], [data-save-link], [data-save-concept], .mapping-form")).to_have_count(0)
        fits(reader)
        reader.goto(f"{base}/#{SCOPE}/knowledge/ontology")
        expect(reader.locator("#graph-definitions")).to_be_visible()
        expect(reader.locator("#catalog-build, [data-build-preview]")).to_have_count(0)
        context.close(), reader_context.close()
    with Server(seed=False) as server:  # nothing ingested: an honest empty state, no fixture graph
        context = browser.new_context(viewport={"width": width, "height": 900})
        page = context.new_page()
        page.goto(server.url + "/")
        with page.expect_response(lambda r: "/api/graph" in r.url) as seen:
            page.goto(f"{server.url}/#{SCOPE}/knowledge/ontology")
        assert seen.value.json()["empty"] is True
        expect(page.locator("[data-graph-empty]")).to_be_visible()
        expect(page.locator("#graph-canvas")).to_have_count(0)
        expect(page.get_by_role("button", name="Build concept catalog")).to_be_visible()
        fits(page)
        context.close()
    print(f"{width}px ok")


if __name__ == "__main__":
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for width in WIDTHS:
            run(width, browser)
        browser.close()
