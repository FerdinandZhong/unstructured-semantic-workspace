"""ZH-T4 (plan 2026-11-23-v2-zh-amp): in zh-CN, the plain-JS pages show no run of English words in fixed UI text.

Offline preview (file://, no server, no provider). For every page the plain JS renders, the visible text nodes are
collected. The language switch (its names are shown in their own language) is skipped. Text that comes from data (any string in the fixtures or the prepared data, or any DOM text inside an
input) is not checked. A node fails when, after data strings and the allow-list are removed, two or more English
words in a row remain. The allow-list holds product names, file names and code words only: never fixed UI text.
Run with miniconda python3: `python3 tests_v2/browser_i18n_shell.py`.
"""
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = (Path(__file__).resolve().parents[1] / "docs/design/v2-workspace/index.html").as_uri()
ALLOW = ["Cloudera AI", "Cloudera", "Semantic Hub", "Helios", "Iceberg", "Trino", "Spark", "Mistral", "PostgreSQL",
         "Google AlloyDB", "Ossie", "Customer 360", "Entity 360", "Financial Services", "Polaris", "OCR", "XFA", "PDF",
         "SQL", "YAML", "JSON", "Lakehouse Engine"]
PAGES = [
    "#datasets",
    "#manufacturing/ingest", "#financial-services/ingest", "#manufacturing/ingest/structured",
    "#manufacturing/knowledge", "#manufacturing/knowledge/ontology", "#manufacturing/knowledge/mappings",
    "#manufacturing/knowledge/talk", "#financial-services/knowledge/talk",
    "#manufacturing/review", "#financial-services/review",
    "#manufacturing/entity", "#financial-services/entity", "#financial-services/evidence",
]
COLLECT = """([allow]) => {
  const data = [];
  const walk = value => { if (typeof value === 'string') { if (value.length > 2) data.push(value, value.replaceAll('-', ' ')); }   // the file table shows a kind with spaces
    else if (value && typeof value === 'object') Object.values(value).forEach(walk); };
  try { walk(workspaceDatasets); } catch {}
  walk(window.V2_WORKSPACE_DATA);
  data.sort((a, b) => b.length - a.length);
  const bad = [], seen = new Set();
  const nodes = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const attrs = [];
  for (const el of document.body.querySelectorAll('[title],[aria-label],[placeholder],[alt]'))
    for (const name of ['title', 'aria-label', 'placeholder', 'alt']) if (el.getAttribute(name)) attrs.push([el, el.getAttribute(name), name]);
  const items = [];
  while (nodes.nextNode()) {
    const node = nodes.currentNode, el = node.parentElement;
    if (!el || el.closest('script,style,svg,textarea,[hidden],[data-lang-switch]') || !node.nodeValue.trim()) continue;
    items.push([el, node.nodeValue.trim(), 'text']);
  }
  for (const [el, value, kind] of items.concat(attrs)) {
    if (el.closest('[hidden]') && kind === 'text') continue;
    let text = value;
    for (const item of data) if (text.includes(item)) text = text.split(item).join(' ');
    for (const word of allow) text = text.split(word).join(' ');
    text = text.replace(/\\b[\\w.-]*[_./\\\\#\\d][\\w.-]*\\b/g, ' ');           // ids, file names, numbers
    const run = text.match(/\\b[A-Za-z][A-Za-z'-]+(?:[ \\u00a0]+[A-Za-z][A-Za-z'-]+)+\\b/);
    const single = text.trim().match(/^[A-Z][a-z]{2,}$/);                   // one capitalised English word alone
    if ((run || single) && !seen.has(kind + value)) { seen.add(kind + value); bad.push(`${kind}: ${value.slice(0, 110)}`); }
  }
  return bad;
}"""


def run():
    failures = {}
    errors = []
    with sync_playwright() as playwright, playwright.chromium.launch() as browser:
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.add_init_script("localStorage.setItem('v2.lang', 'zh-CN')")
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(BASE + "#datasets")
        sources = page.evaluate("Object.fromEntries(Object.entries(workspaceDatasets).map(([id, d]) => [id, d.sources.slice(0, 3).map(s => s.id)]))")
        pages = list(PAGES) + [f"#{ds}/ingest/{sid}" for ds, ids in sources.items() for sid in ids]
        for fragment in pages:
            page.goto(BASE + fragment)
            page.reload()
            page.wait_for_function("document.querySelector('#workspace-main')?.children.length > 0")
            page.wait_for_timeout(250)
            bad = page.evaluate(COLLECT, [ALLOW])
            if bad:
                failures[fragment] = bad
        page.goto(BASE + "#datasets")
        page.reload()
        page.locator(".source-details").first.click()      # the opened details of a card
        page.fill("#dataset-search", "zzzz")
        bad = page.evaluate(COLLECT, [ALLOW])
        if bad:
            failures["#datasets (filtered)"] = bad
    for fragment, bad in failures.items():
        print(fragment)
        for line in bad:
            print("   ", line)
    assert not errors, errors
    print(f"{len(pages)} pages checked; {sum(map(len, failures.values()))} English texts in {len(failures)} pages.")
    return not failures


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
