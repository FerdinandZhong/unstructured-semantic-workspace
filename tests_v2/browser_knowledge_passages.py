"""Exercise grouped graph passages without provider calls."""
import sys

from playwright.sync_api import expect, sync_playwright


FINANCE = "financial-services"
MANUFACTURING = "manufacturing"


def source(dataset, key):
    return f"{dataset}:source:{key}"


def attach_passages(data):
    """Use modeled definitions and sources with deterministic passage content."""
    for workspace in data["datasets"]:
        dataset = workspace["id"]
        fields = workspace["knowledge"]["structured"]["fields"]
        specs = (
            ("cleaned_pdf_3", "table", "Budget and currency", "Budget  SGD 250.75",
             [["Budget", "SGD 250.75"], ["Currency", "SGD"]], [0, 1]),
            ("cleaned_pdf_3", "table", "Review flags", "Risk  Moderate", [["Risk", "Moderate"]], [0]),
            ("jade-adverse-media", "article", "Article context", "First paragraph.\n\nSecond paragraph.", None, [1]),
            ("jade_call", "transcript", "Call discussion", "Adviser: What was reported?\nCustomer: The dates are uncertain.", None, []),
            ("cleaned_pdf_4", "table", "Withheld fixture", "Do not disclose this fixture.", [["Secret", "Protected"]], [1]),
        ) if dataset == FINANCE else (
            ("MFG-002", "table", "Order line", "Ordered quantity  16", [["Ordered quantity", "16"]], [0]),
        )
        observations, mappings, passages = [], [], []
        for index, (key, kind, title, text, rows, targets) in enumerate(specs):
            source_id = source(dataset, key)
            observation_id = f"{dataset}:observation:graph-test-{index}"
            citation = {"page": index + 1, "startLine": 2, "endLine": 3}
            observations.append({"id": observation_id, "title": title, "sourceId": source_id,
                                 "quote": text, "citation": citation})
            passages.append({"id": f"{dataset}:passage:graph-test-{index}", "sourceId": source_id,
                             "kind": kind, "title": title, "text": text, "rows": rows,
                             "observationIds": [observation_id], "citation": citation})
            for target in targets:
                mappings.append({"id": f"{dataset}:mapping:graph-test-{index}-{target}",
                                 "observationId": observation_id, "targetId": fields[target]["id"],
                                 "status": "ambiguous" if target else "candidate"})
        workspace["knowledge"].update(observations=observations, mappings=mappings, passages=passages)
        for item in workspace["sources"]:
            if item["id"] == source(FINANCE, "cleaned_pdf_4"):
                item["access"] = "withheld"
    return data


def smoke(browser, url, width):
    page = browser.new_page(viewport={"width": width, "height": 930})
    page_errors = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.route("**/api/workspace", lambda route: route.fulfill(json=attach_passages(route.fetch().json())))
    page.goto(url + f"#{FINANCE}/knowledge/mappings/graph-test-1")
    scope = page.locator("#graph-filters")
    source_picker = scope.get_by_role("combobox", name="Source file")
    type_picker = scope.get_by_role("combobox", name="Content type")
    status_picker = scope.get_by_role("combobox", name="Mapping status")
    search = scope.get_by_role("searchbox", name="Find passages")
    details = page.get_by_role("complementary").locator("#graph-detail")
    catalog = page.locator("#graph-definitions")
    expect(source_picker).to_have_value(source(FINANCE, "cleaned_pdf_3"))
    expect(source_picker).not_to_contain_text("customer-form4.pdf")
    expect(details.get_by_text("Moderate")).to_be_visible()
    expect(catalog.get_by_role("button")).to_have_count(3)
    expect(catalog.get_by_role("button", name="customer-form3.pdf 2 supporting passages")).to_have_count(1)
    expect(details.get_by_role("link", name="Inspect cited source")).to_have_attribute(
        "href", f"#{FINANCE}/ingest/cleaned_pdf_3/graph-test-1")
    details.get_by_role("button", name="Previous").click()
    expect(details.get_by_role("table", name="Budget and currency")).to_contain_text("SGD 250.75")
    # Selecting a definition supported by only one group narrows the inspector.
    catalog.get_by_role("button").last.click()
    expect(details.get_by_role("table", name="Budget and currency")).to_be_visible()
    expect(details.get_by_role("button", name="Next")).to_have_count(0)
    status_picker.select_option("ambiguous")
    expect(catalog.get_by_role("button")).to_have_count(2)
    status_picker.select_option("all")
    search.fill("Moderate")
    expect(details.get_by_role("table", name="Review flags")).to_be_visible()
    search.fill("not in the source")
    expect(catalog.get_by_role("button")).to_have_count(0)
    search.fill("Second paragraph")
    expect(source_picker).to_have_value(source(FINANCE, "jade-adverse-media"))
    search.fill("")
    type_picker.select_option("article")
    expect(details.get_by_text("First paragraph.")).to_be_visible()
    expect(details.locator(".graph-passage-text")).to_have_text("First paragraph.\n\nSecond paragraph.", use_inner_text=True)
    type_picker.select_option("transcript")
    expect(details.get_by_text("Customer: The dates are uncertain.")).to_be_visible()
    expect(catalog.get_by_role("button")).to_have_count(1)
    expect(page.locator("#workspace-main")).not_to_contain_text("Do not disclose this fixture.")

    page.locator("#workspace-choice").select_option(MANUFACTURING)
    expect(page).to_have_url(url + f"#{MANUFACTURING}/knowledge/mappings")
    page.get_by_text("Supporting evidence", exact=True).click()
    expect(page.locator("#graph-detail").get_by_role("cell", name="16")).to_be_visible()
    expect(page.locator("#workspace-main")).not_to_contain_text("Call discussion")
    assert not page_errors, page_errors
    page.close()
    print(f"Grouped document, definition, table, article, transcript and scope passed at {width}px.")


if __name__ == "__main__":
    base = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8137/").rstrip("/") + "/"
    with sync_playwright() as runtime, runtime.chromium.launch() as browser:
        for pixels in (1440, 980, 390):
            smoke(browser, base, pixels)
