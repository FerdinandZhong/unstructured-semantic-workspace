"""Run the source-card and shared-stage regression with Playwright Chromium."""

from pathlib import Path

from playwright.sync_api import expect, sync_playwright


ENTRY = (Path(__file__).resolve().parents[1] / "docs/design/v2-workspace/index.html").as_uri()
# Six pipeline steps, then Entity 360 under "Customer view" (Financial Services). Knowledge stays reachable by link, not as a step.
STAGES = ["Sources", "Model", "Evidence", "Concepts", "Publish", "Ask", "Entity 360"]
ANSWERS = {
    "financial-services": "no recorded risk rating",
    "manufacturing": "Delivery remains unknown",
}


def check_workspace(page):
    for dataset, qualifier in ANSWERS.items():
        page.goto(ENTRY)
        expect(page.locator(".workspace-card")).to_have_count(2)
        page.locator(f'[data-dataset="{dataset}"] .open-workspace').click()
        expect(page).to_have_url(f"{ENTRY}#{dataset}/ingest")
        expect(page.locator("main h1")).to_have_text("Sources")
        expect(page.locator(".file-library")).to_be_visible()
        expect(page.locator(".ingest-split")).to_have_count(0)
        expect(page.locator('nav[aria-label="Workflow"] .nav-text')).to_have_text(STAGES)
        for title in STAGES[1:]:
            page.locator('nav[aria-label="Workflow"] a').filter(has_text=title).click()
            expect(page.locator("main h1")).to_have_text(title)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.get_by_role("button", name="Show cited answer").click()
        expect(page.locator(".answer")).to_contain_text(qualifier)
        citations = page.locator(".answer > .citations a")
        expect(citations).to_have_count(2)
        citations.first.click()
        expect(page.locator(".side-panel blockquote")).to_be_visible()

        page.goto(f"{ENTRY}#{dataset}/knowledge/ontology")
        other = next(key for key in ANSWERS if key != dataset)
        page.locator("#workspace-choice").select_option(other)
        expect(page).to_have_url(f"{ENTRY}#{other}/knowledge/ontology")
        expect(page.locator("main h1")).to_have_text("Knowledge")


if __name__ == "__main__":
    with sync_playwright() as playwright:
        with playwright.chromium.launch() as browser:
            for width in (1440, 980, 390):
                page = browser.new_page(viewport={"width": width, "height": 900})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                check_workspace(page)
                assert not errors, errors
                page.close()
                print(f"Both datasets passed at {width}px.")
