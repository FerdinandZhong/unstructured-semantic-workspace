"""Layout checks of the six pipeline steps on a real server, at desktop widths 1440, 1280 and 980 pixels.

User decision of 22 November 2026: this V2 UI targets desktop browsers only; no 390-pixel checks.

The earlier scripts checked that elements exist. These check that the layout is right: no overlap between a list
and its detail panel, toggle buttons in one row, canvas nodes spread over more than one column, real <button>
controls, no horizontal scroll, and no static sample text on live pages.

Run with miniconda python3 (Playwright) and a prepared state (whole corpus ingested, catalogs built):
    python3 tests_v2/browser_pipeline_layout.py /tmp/prep/state1
The state is copied to a temporary folder; the server runs from .venv-v2 on a free port with no provider key, so no
model call can happen. Only that server process is stopped."""
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PG = os.environ.get("V2_PG_BIN", "")
STEPS = ["Sources", "Model", "Evidence", "Concepts", "Publish", "Ask"]
WIDTHS = (1440, 1280, 980)
SLOW = 60000   # the dataset-wide evidence export takes several seconds on the full corpus


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    assert port not in (8137, 8291)
    return port


class Server:
    def __init__(self, state):
        self.dir = tempfile.mkdtemp(prefix="v2-layout-")
        shutil.copytree(state, self.dir + "/state", ignore=shutil.ignore_patterns("review.lock", "postmaster.pid"))
        self.port = free_port()
        env = {k: v for k, v in os.environ.items() if k != "MISTRAL_API_KEY"} | {"V2_PG_BIN": PG}
        self.process = subprocess.Popen([str(ROOT / ".venv-v2/bin/python"), "-B", "-m", "semantic_pipeline_v2.workspace_server",
                                         "--port", str(self.port), "--state-dir", self.dir + "/state"],
                                        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.url = f"http://127.0.0.1:{self.port}/"
        for _ in range(90):
            try:
                urllib.request.urlopen(self.url + "pipeline.js", timeout=2)
                return
            except OSError:
                time.sleep(1)
        raise RuntimeError("server did not start")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.process.terminate()
        self.process.wait(timeout=60)
        shutil.rmtree(self.dir, ignore_errors=True)


def box(locator):
    value = locator.bounding_box()
    assert value, locator
    return value


def apart(a, b):
    """True when two boxes do not overlap."""
    return a["x"] + a["width"] <= b["x"] + 1 or b["x"] + b["width"] <= a["x"] + 1 or \
        a["y"] + a["height"] <= b["y"] + 1 or b["y"] + b["height"] <= a["y"] + 1


def fits(page):
    assert page.evaluate("document.documentElement.scrollWidth - innerWidth") <= 0, "horizontal scroll"


def one_row(locator):
    middles = [box(locator.nth(i))["y"] + box(locator.nth(i))["height"] / 2 for i in range(locator.count())]
    assert max(middles) - min(middles) <= 4, f"buttons stagger over rows {middles}"
    tags = {locator.nth(i).evaluate("node => node.tagName") for i in range(locator.count())}
    assert tags == {"BUTTON"}, tags


def spread(page, selector):
    nodes = page.locator(selector)
    expect(nodes.first).to_be_visible(timeout=SLOW)
    xs = {round(box(nodes.nth(i))["x"]) for i in range(nodes.count())}
    assert nodes.count() < 2 or len(xs) > 1, "all canvas nodes share one x coordinate"
    return nodes.count()


def go(page, url):
    page.goto(url)
    expect(page.locator("main h1")).to_be_visible()


def check(page, base, width):
    wide = width > 860
    ds = base + "#financial-services/"
    go(page, ds + "ingest")
    nav = page.locator('nav[aria-label="Workflow"] a.nav-item')
    expect(nav.locator(".nav-count").filter(has_text="6")).to_have_count(1)
    titles = [text.split("\n")[0].strip() for text in nav.locator(".nav-text").all_inner_texts()]
    assert titles[:6] == STEPS and titles[6:] == ["Entity 360"], titles
    expect(page.locator("[data-step-state]").first).to_be_visible(timeout=SLOW)

    # Sources: one list for every kind, with data label and result counts.
    headers = page.locator(".file-table thead th").all_inner_texts()
    assert headers == ["Name", "Kind", "Data", "State", "Result"], headers
    expect(page.locator("[data-pending]")).to_have_count(0, timeout=SLOW)
    expect(page.locator(".file-table a[href*='/evidence/']").first).to_be_visible()
    fits(page)

    # Model: one card per table, spread by the layout, edges drawn, system fields hidden until asked.
    go(page, ds + "model")
    assert spread(page, ".react-flow__node") >= 10
    assert page.locator(".react-flow__edge").count() > 0
    page.locator(".react-flow__node", has_text="Accounts").first.click()
    expect(page.locator(".pf-table-fields").first).to_be_visible()
    expect(page.locator(".pf-table-fields").first).not_to_contain_text("Source batch")
    page.get_by_label("Show system fields").check()
    expect(page.locator(".pf-table-fields").first).to_contain_text("Source batch")
    if wide:
        assert apart(box(page.locator(".pf-canvas")), box(page.locator(".pf-side")))
    fits(page)

    # Evidence: passage left, links right; business names; toggle buttons in one row; graph grouped by table.
    go(page, ds + "evidence/FIN-002")
    # Graph is the default view (user, 2026-11-22); the reading view is one click away.
    expect(page.locator(".react-flow__node").first).to_be_visible(timeout=SLOW)
    page.get_by_role("button", name="Reading", exact=True).click()
    rows = page.locator(".pf-row")
    expect(rows.first).to_be_visible(timeout=SLOW)
    one_row(page.locator(".pf-seg button"))
    expect(page.locator(".pf-target").filter(has_text="›").first).to_be_visible()
    for i in range(rows.count()):
        passage, links = box(rows.nth(i).locator(".pf-passage")), box(rows.nth(i).locator(".pf-links"))
        assert apart(passage, links), "passage and links overlap"
        assert (passage["x"] < links["x"]) if wide else (passage["y"] < links["y"])
    assert page.locator(".pf-chip").first.evaluate("node => node.tagName") == "BUTTON"
    page.get_by_role("button", name="Graph", exact=True).click()
    spread(page, ".react-flow__node")
    labels = page.locator(".react-flow__node").all_inner_texts()
    assert not any("/" in text for text in labels), labels      # tables, never one node per column id
    fits(page)
    # Overall: every source against the tables, then the per-source list.
    go(page, ds + "evidence")
    expect(page.locator('.react-flow__node [data-node-kind="table"]').first).to_be_visible(timeout=SLOW)
    assert page.locator('.react-flow__node [data-node-kind="source"]').count() > 1
    page.get_by_role("button", name="List", exact=True).click()
    expect(page.get_by_role("table", name="Links by source")).to_be_visible()
    fits(page)

    # Concepts: list and detail side by side without overlap; Build catalog is a real button.
    go(page, ds + "concepts")
    detail = page.locator(".pf-detail")
    expect(detail).to_be_visible(timeout=SLOW)
    assert apart(box(page.locator(".pf-list")), box(detail)), "concept list overlaps the detail panel"
    assert page.locator("[data-build-preview]").evaluate("node => node.tagName") == "BUTTON"
    one_row(detail.locator(".pf-actions").first.locator("button"))
    one_row(page.locator(".pf-seg button"))
    page.get_by_role("button", name="Graph", exact=True).click()
    spread(page, ".react-flow__node")
    assert page.locator(".react-flow__edge").count() > 0
    fits(page)

    # Publish and Entity 360: live state only, no simulated receipt or static revision rows.
    go(page, ds + "review")
    expect(page.locator(".panel").filter(has_text="Publication history")).to_be_visible()
    assert "Simulation" not in page.locator("main").inner_text()
    go(page, ds + "entity")
    expect(page.locator("[data-entity-search]")).to_be_visible()   # Entity 360 is live (2026-11-22), not a later note
    assert page.locator('[data-action="receipt"]').count() == 0 and "Revision history" not in page.locator("main").inner_text()
    go(page, ds + "ask")
    expect(page.locator("#pf-question")).to_be_visible()
    fits(page)


def check_reader(context, base):
    page = context.new_page()
    page.goto(base)
    status = page.evaluate("""() => fetch('/api/session', {method:'POST', headers:{'Content-Type':'application/json'},
                     body: JSON.stringify({userId:'finance_reader', recover:true})}).then(r => r.status)""")
    assert status == 200, status
    page.goto(base + "#financial-services/evidence/FIN-002")
    page.reload()   # a hash-only goto keeps the page that started with the replaced admin session
    page.get_by_role("button", name="Reading", exact=True).click(timeout=SLOW)
    expect(page.locator(".pf-row").first).to_be_visible(timeout=SLOW)
    assert page.locator(".pf-link button", has_text="Edit").count() == 0
    assert page.locator('nav a[href$="/review"]').count() == 0
    go(page, base + "#financial-services/concepts")
    expect(page.locator(".pf-detail")).to_be_visible(timeout=SLOW)
    assert page.get_by_role("button", name="Approve").count() == 0 and page.locator("[data-build-preview]").count() == 0
    expect(page.locator(".pf-detail")).to_contain_text("Read-only account")
    page.close()


if __name__ == "__main__":
    state = sys.argv[1] if len(sys.argv) > 1 else "/tmp/prep/state1"
    with Server(state) as server, sync_playwright() as playwright:
        with playwright.chromium.launch() as browser:
            for width in WIDTHS:
                context = browser.new_context(viewport={"width": width, "height": 900})
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                check(page, server.url, width)
                assert not errors, errors
                context.close()
                print(f"Pipeline layout passed at {width}px.")
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            check_reader(context, server.url)
            context.close()
            print("Reader view passed.")
