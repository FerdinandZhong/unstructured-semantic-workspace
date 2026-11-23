"""Exercise the hosted Talk to Data UI with intercepted, local-only responses."""
import sys

from playwright.sync_api import expect, sync_playwright


FINANCE = "financial-services"
MANUFACTURING = "manufacturing"


def answer(request):
    scope = request["dataset"]
    mode = request["mode"]
    if request["question"] == "Find a moon base":
        return {"status": "unsupported", "summary": "No relevant definition.",
                "gaps": ["Identify a field available in this dataset."]}
    source, passage = ("jade-adverse-media", "reported-payment-concern") if scope == FINANCE else ("MFG-002", "order-line")
    result = {
        "status": "grounded",
        "summary": f"Grounded in {scope}.",
        "definitions": [{"title": "Business measure", "definition": "Measures at the source grain."}],
        "facts": [{"label": "Reported measure", "value": "16", "source": "Prepared example"}],
        "evidence": [{"id": f"{scope}:observation:{passage}", "title": "Original passage",
                      "sourceId": f"{scope}:source:{source}", "quote": "Scoped example evidence.",
                      "citation": {"page": 1, "startLine": 12, "endLine": 13}},
                     {"id": "unexpected", "title": "Out-of-scope citation", "quote": "Do not show this passage.",
                      "sourceId": f"{MANUFACTURING if scope == FINANCE else FINANCE}:source:other",
                      "citation": {"page": 1}}],
    }
    if mode == "proposal":
        result.update({
            "proposal": {"title": "Example view", "table": "orders", "fields": [{"name": "order_id"}],
                         "grain": "One row per order", "limitations": ["Confirm the source binding."]},
            "sparkDraft": {"language": "pyspark", "source": {"path": "demo_data/orders.csv", "modelRevision": "demo-revision"},
                           "target": {"description": "Select an approved target."},
                           "code": "result = spark.table('orders')\n# <img src='/never-load-spark'>"},
        })
    return result


def smoke(browser, base, width):
    page = browser.new_page(viewport={"width": width, "height": 950})
    requests = []
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: errors.append(request.url) if "never-load-spark" in request.url else None)
    fail_next = False

    def intercept(route):
        nonlocal fail_next
        if route.request.method == "GET":
            route.fulfill(json={"mistralAvailable": True})
        else:
            request = route.request.post_data_json
            requests.append(request)
            if fail_next:
                fail_next = False
                route.fulfill(status=503, json={"error": "Test connection unavailable."})
            else:
                route.fulfill(json=answer(request))

    page.route("**/api/talk-to-data", intercept)
    page.goto(base + f"#{FINANCE}/knowledge")
    page.get_by_role("navigation", name="Knowledge views").get_by_role("link", name="Talk to Data").click()
    expect(page.get_by_role("link", name="Talk to Data")).to_have_attribute("aria-current", "page")
    entry = page.get_by_role("textbox", name="Question or product requirements")
    log = page.get_by_role("log", name="Conversation")
    interpreter = page.get_by_role("checkbox", name="Use Mistral to interpret this request")
    expect(interpreter).to_be_visible()
    expect(interpreter).not_to_be_checked()
    entry.fill("What definitions and evidence are available?")
    page.get_by_role("button", name="Ask a question").click()
    expect(log.get_by_text("Grounded in financial-services.")).to_be_visible()
    assert requests[-1]["interpretation"] == "offline"
    expect(log.get_by_role("link", name="Open cited source · Page 1 · Lines 12–13")).to_be_visible()
    expect(log.get_by_text("Do not show this passage.")).to_have_count(0)

    interpreter.check()
    entry.fill("Draft a product at order grain.")
    page.get_by_role("button", name="Draft data product and Spark").click()
    expect(log.get_by_role("heading", name="Proposed data product")).to_be_visible()
    expect(log.get_by_label("PySpark draft")).to_contain_text("spark.table('orders')")
    expect(log.get_by_text("demo_data/orders.csv")).to_be_visible()
    expect(log.get_by_text("demo-revision")).to_be_visible()
    expect(interpreter).not_to_be_checked()
    assert requests[-1]["mode"] == "proposal"
    assert requests[-1]["interpretation"] == "mistral"  # the response is mocked
    assert requests[-1]["history"][0]["role"] == "user"

    page.locator("#workspace-choice").select_option(MANUFACTURING)
    expect(page).to_have_url(f"{base}#{MANUFACTURING}/knowledge/talk")
    expect(log.get_by_text("Grounded in financial-services.")).to_have_count(0)
    fail_next = True
    entry.fill("Find a moon base")
    page.get_by_role("button", name="Ask a question").click()
    expect(page.get_by_role("status").get_by_text("Test connection unavailable.")).to_be_visible()
    expect(entry).to_have_value("Find a moon base")
    page.get_by_role("button", name="Ask a question").click()
    expect(log.get_by_role("heading", name="Missing inputs")).to_be_visible()
    assert requests[-1]["dataset"] == MANUFACTURING
    assert not requests[-1]["history"]
    assert not errors, errors
    page.close()
    print(f"Talk to Data conversation, scope, Spark, errors, and provider choice passed at {width}px.")


if __name__ == "__main__":
    base_url = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8137/").rstrip("/") + "/"
    with sync_playwright() as runtime, runtime.chromium.launch() as chromium:
        for pixels in (1440, 980, 390):
            smoke(chromium, base_url, pixels)
