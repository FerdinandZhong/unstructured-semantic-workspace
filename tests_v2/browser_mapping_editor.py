"""C6 (T20): one edit surface on the Knowledge canvas, with mocked, permission-scoped responses."""
import json
import mimetypes
from pathlib import Path
from urllib.parse import unquote

from playwright.sync_api import expect, sync_playwright

from browser_knowledge_passages import attach_passages


ASSETS = Path(__file__).resolve().parents[1] / "docs/design/v2-workspace"
BASE = "http://127.0.0.1:8141"
SCOPE = "financial-services"
SOURCE = f"{SCOPE}:source:cleaned_pdf_3"
OBSERVATION = f"{SCOPE}:observation:graph-test-1"
CANVAS = f"{BASE}/#{SCOPE}/knowledge/mappings/graph-test-1"


def mock_workspace(page, role):
    data = attach_passages(json.loads((ASSETS / "workspace-data.js").read_text().split(" = ", 1)[1].rstrip(";\n")))
    data["reviewAvailable"] = True
    knowledge = data["datasets"][0]["knowledge"]
    mapping = next(item for item in knowledge["mappings"] if item["observationId"] == OBSERVATION)
    fields = knowledge["structured"]["fields"]
    target, other = fields[1]["id"], fields[2]["id"]
    original = mapping["targetId"]
    state = {"revision": "rev-0", "stale_once": False, "reviews": {}, "releases": []}
    requests, reviews = [], []

    def serve(route):
        path = route.request.url.split(BASE, 1)[1].split("?", 1)[0]
        query = route.request.url.split("?", 1)[1] if "?" in route.request.url else ""
        if path == "/api/session":
            route.fulfill(json={"principal": {"id": role, "displayName": role,
                                             "role": role, "datasets": [SCOPE]}, "accounts": []})
        elif path == "/api/workspace":
            if role == "admin":
                mapping["correctionRevision"] = state["revision"]
            route.fulfill(json=data)
        elif path == "/api/review" and route.request.method == "GET":
            if "mappingId" not in query:
                route.fulfill(json={"receipts": list(state["reviews"].values())})
            else:
                selected = unquote(query.split("mappingId=", 1)[1])
                route.fulfill(json={"evidenceRevision": "evidence-1", "receipt": state["reviews"].get(selected)})
        elif path == "/api/review":
            request = route.request.post_data_json
            reviews.append(request)
            receipt = {"dataset": SCOPE, "mappingId": request["mappingId"], "decision": request["decision"],
                       "note": request["note"], "evidenceRevision": "evidence-1", "publication": "none"}
            state["reviews"][request["mappingId"]] = receipt
            route.fulfill(json=receipt)
        elif path == "/api/ocr":
            route.fulfill(json={"available": False})
        elif path == "/api/publish/history":
            route.fulfill(json={"releases": state["releases"]})
        elif path == "/api/publish/preview":
            route.fulfill(json={"dataset": SCOPE, "catalogVersion": "cat-1", "modelSha256": "a" * 64, "fingerprint": "f" * 64,
                                "counts": {"concepts": 1, "links": 1, "evidenceRefs": 1, "mappingCorrections": 0},
                                "unchanged": False, "nextVersion": 1,
                                "changes": {"concepts": {"added": ["c1"], "removed": [], "changed": []},
                                            "corrections": {"added": [], "removed": [], "changed": []}},
                                "lint": {"ok": True, "errors": []}})
        elif path == "/api/publish/confirm":
            state["releases"].insert(0, {"version": 1, "publishedAt": "2026-11-22T10:00:00+00:00", "principalId": "admin",
                                         "catalogVersion": "cat-1", "modelSha256": "a" * 64, "contentSha256": "f" * 64,
                                         "counts": {"concepts": 1, "links": 1, "evidenceRefs": 1, "mappingCorrections": 0}})
            route.fulfill(json={"dataset": SCOPE, "version": 1, "written": True, "contentSha256": "f" * 64,
                                "message": "Published as version 1."})
        elif path == "/api/mapping-correction":
            request = route.request.post_data_json
            requests.append(request)
            if state["stale_once"]:
                state["stale_once"] = False
                # Another administrator removed the mapping while the dialog was open.
                state["revision"] = "rev-other"
                mapping.update(correctionAction="remove", originalTargetId=original, targetId=None, candidateIds=[],
                               status="unmapped", correctionNote="Removed in review.",
                               correctionActor="other-admin", correctionAt="2026-11-20T09:15:00+00:00")
                route.fulfill(status=409, json={"error": "Mapping changed. Review the latest revision."})
                return
            assert request["correctionRevision"] == state["revision"], request
            action = request["action"]
            selected = {"replace": target, "remove": None, "restore": original}[action]
            mapping.update(correctionAction=action, originalTargetId=original, targetId=selected,
                           candidateIds=[selected] if selected else [],
                           status={"replace": "corrected", "remove": "unmapped", "restore": "candidate"}[action],
                           correctionNote=request.get("note", ""), correctionActor="admin",
                           correctionAt="2026-11-20T10:30:00+00:00")
            state["revision"] = f"rev-{len(requests)}"
            route.fulfill(json={"correctionRevision": state["revision"]})
        elif path == "/":
            index = (ASSETS / "index.html").read_text().replace(
                "<head>", '<head><meta name="workspace-runtime" content="local">', 1)
            route.fulfill(body=index, content_type="text/html")
        else:
            asset = ASSETS / path.lstrip("/")
            route.fulfill(path=asset, content_type=mimetypes.guess_type(asset)[0]) if asset.is_file() else route.abort()

    page.route(f"{BASE}/**", serve)
    return requests, reviews, state, mapping, target, other


def no_overflow(page, modal):
    assert modal.evaluate("(node) => node.scrollWidth <= node.clientWidth")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert modal.evaluate("(node) => node.getBoundingClientRect().bottom <= innerHeight")


def admin_smoke(page):
    requests, reviews, state, mapping, target, other = mock_workspace(page, "admin")
    page.goto(CANVAS)
    opener = page.get_by_role("button", name="Edit mapping")
    modal = page.get_by_role("dialog", name="Mapping editor")

    # One action opens one dialog. Mapping, note and decision share it.
    opener.click()
    expect(modal.get_by_role("table")).to_contain_text("Moderate")
    expect(modal.get_by_role("combobox", name="Assertion")).to_be_focused()
    expect(modal.get_by_role("combobox", name="Correction")).to_be_visible()
    expect(modal.get_by_role("textbox", name="Note")).to_be_visible()
    expect(modal.get_by_role("combobox", name="Evidence decision")).to_be_visible()
    expect(modal.get_by_role("textbox", name="Decision rationale")).to_be_visible()
    expect(page.get_by_role("button", name="Open evidence decision")).to_have_count(0)
    assert page.locator("dialog[open]").count() == 1
    no_overflow(page, modal)
    page.keyboard.press("Escape")
    expect(modal).to_be_hidden()
    expect(opener).to_be_focused()

    # Double-click on a canvas node opens the same editor on the selection.
    # Selecting the document starts at its first passage, which may be read-only in this fixture.
    page.locator("#graph-canvas .canvas-node").first.dblclick()
    expect(page.locator("dialog[open]")).to_have_count(1)
    expect(page.locator("dialog[open] .mapping-editor-evidence")).to_be_visible()
    page.keyboard.press("Escape")
    page.reload()
    expect(opener).to_be_visible()

    # Correction with a note. The last change shows actor and time.
    opener.click()
    modal.get_by_role("combobox", name="Correction").select_option("replace")
    modal.get_by_role("combobox", name="New definition").select_option(target)
    modal.get_by_role("textbox", name="Note").fill("Scale confirmed with the data owner.")
    modal.get_by_role("button", name="Save changes").click()
    expect(modal).to_be_hidden()
    expect(opener).to_be_focused()
    assert requests[-1] == {"dataset": SCOPE, "sourceId": SOURCE, "observationId": OBSERVATION,
                            "action": "replace", "targetId": target, "correctionRevision": "rev-0",
                            "note": "Scale confirmed with the data owner.", "requestId": requests[-1]["requestId"]}
    assert not reviews
    opener.click()
    expect(modal).to_contain_text("Scale confirmed with the data owner.")
    expect(modal).to_contain_text("admin")
    expect(modal).to_contain_text("2026-11-20")

    # Decision is optional and saved from the same dialog, for any mapping.
    expect(modal.get_by_role("button", name="Save changes")).to_be_disabled()
    modal.get_by_role("combobox", name="Evidence decision").select_option("request_evidence")
    expect(modal.get_by_role("button", name="Save changes")).to_be_disabled()
    modal.get_by_role("textbox", name="Decision rationale").fill("Need the receipt.")
    modal.get_by_role("button", name="Save changes").click()
    expect(modal).to_be_hidden()
    assert len(requests) == 1 and reviews == [{"dataset": SCOPE, "mappingId": mapping["id"],
                                               "decision": "request_evidence", "note": "Need the receipt.",
                                               "evidenceRevision": "evidence-1"}]
    opener.click()
    expect(modal).to_contain_text("Need the receipt.")
    expect(modal.get_by_role("textbox", name="Decision rationale")).to_have_count(0)
    page.keyboard.press("Escape")

    # A stale save keeps the choice and note, shows what changed and can be re-applied.
    state["stale_once"] = True
    opener.click()
    modal.get_by_role("combobox", name="Correction").select_option("replace")
    modal.get_by_role("combobox", name="New definition").select_option(other)
    modal.get_by_role("textbox", name="Note").fill("My note survives.")
    modal.get_by_role("button", name="Save changes").click()
    alert = modal.get_by_role("alert")
    expect(alert).to_contain_text("changed")
    expect(alert).to_contain_text("other-admin")
    expect(modal.get_by_role("combobox", name="Correction")).to_have_value("replace")
    expect(modal.get_by_role("combobox", name="New definition")).to_have_value(other)
    expect(modal.get_by_role("textbox", name="Note")).to_have_value("My note survives.")
    expect(modal.get_by_role("link", name="Reload workspace")).to_have_count(0)
    expect(modal.get_by_role("button", name="Save changes")).to_be_enabled()
    no_overflow(page, modal)
    modal.get_by_role("button", name="Save changes").click()
    expect(modal).to_be_hidden()
    assert requests[-1]["correctionRevision"] == "rev-other" and requests[-1]["note"] == "My note survives."

    # Review stays a read-only summary. Publication needs no decision and asks for confirmation.
    page.goto(f"{BASE}/#{SCOPE}/review")
    expect(page.locator("#workspace-main").get_by_text("Need the receipt.")).to_be_visible()
    expect(page.get_by_role("link", name="Open Evidence")).to_be_visible()   # was "Open Knowledge canvas"; Evidence is now the step
    expect(page.get_by_role("button", name="Save decision")).to_have_count(0)
    expect(page.get_by_role("textbox")).to_have_count(0)
    state["reviews"].clear()
    page.reload()
    publish = page.get_by_role("button", name="Preview release")
    expect(publish).to_be_enabled()
    expect(page.get_by_text("No release published yet.")).to_be_visible()
    publish.click()
    expect(page.locator("[data-release-preview]")).to_contain_text("Preview of version 1")
    page.get_by_role("button", name="Confirm publish").click()
    expect(page.get_by_text("Published as version 1.")).to_be_visible()
    expect(page.get_by_role("link", name="Download YAML")).to_be_visible()
    expect(page.locator("#workspace-main")).not_to_contain_text("imulation")
    # The definition-mapping editor is no longer a step; it stays reachable by its link.
    page.goto(f"{BASE}/#{SCOPE}/knowledge")
    expect(page.get_by_text("Supporting evidence", exact=True)).to_be_visible()
    expect(page.locator("[data-open-mapping]")).to_be_visible()


def reader_smoke(page):
    requests, reviews, *_ = mock_workspace(page, "reader")
    page.goto(CANVAS)
    expect(page.get_by_role("button", name="Edit mapping")).to_have_count(0)
    page.get_by_role("button", name="Open passage").click()
    modal = page.get_by_role("dialog", name="Source passage")
    expect(modal.get_by_role("table")).to_contain_text("Moderate")
    expect(modal).to_contain_text("Current mapping")
    for control in ("combobox", "textbox", "button"):
        expect(modal.get_by_role(control, name="Save changes")).to_have_count(0)
    expect(modal.get_by_role("combobox")).to_have_count(0)
    expect(modal.get_by_role("textbox")).to_have_count(0)
    expect(modal.get_by_role("button", name="Save changes")).to_have_count(0)
    page.keyboard.press("Escape")
    expect(modal).to_be_hidden()
    expect(page.get_by_role("button", name="Open passage")).to_be_focused()
    page.locator("#graph-canvas .canvas-node").first.dblclick()
    expect(page.locator("dialog[open]")).to_have_count(1)
    expect(page.locator("dialog[open]").get_by_role("button", name="Save changes")).to_have_count(0)
    page.keyboard.press("Escape")
    assert not requests and not reviews


if __name__ == "__main__":
    with sync_playwright() as runtime, runtime.chromium.launch() as browser:
        for width in (1440, 980, 390):
            for smoke in (admin_smoke, reader_smoke):
                page = browser.new_page(viewport={"width": width, "height": 900})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                smoke(page)
                assert not errors, errors
                page.close()
            print(f"One edit surface passed at {width}px.")
