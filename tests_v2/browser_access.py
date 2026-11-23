"""Verify local demo account isolation in Chromium against the V2 server."""
import sys

from playwright.sync_api import expect, sync_playwright


FINANCE = "financial-services"
FACTORY = "manufacturing"
WIDTHS = (1440, 980, 390)
ROLES = {
    "admin": (FINANCE, FACTORY),
    "finance_reader": (FINANCE,),
    "finance_restricted": (FINANCE,),
    "manufacturing_reader": (FACTORY,),
    "manufacturing_restricted": (FACTORY,),
}
PROTECTED = {
    FINANCE: ("jade", "jade-adverse-media", "reported-payment-concern", "(UEN 202386951H) has been named in"),
    FACTORY: ("amendment", "MFG-003", "amended-delivery", "changes from 2024-06-10 to 2024-06-17"),
}
TITLES = {FINANCE: "Financial Services", FACTORY: "Manufacturing"}


def switch(page, user):
    # 2026-11-23: a switch now stays on the current step when the new account may open it; from the catalog it stays there
    page.evaluate("location.hash = '#datasets'")
    expect(page.locator("#dataset-catalog")).to_be_visible()
    page.locator('[data-action="account"]').click()
    chooser = page.locator("#demo-account")
    expect(chooser.locator("option")).to_have_count(5)
    chooser.select_option(user)
    with page.expect_navigation():
        page.get_by_role("button", name="Switch account").click()
    expect(page.locator("#dataset-catalog")).to_be_visible(timeout=20000)      # a large state takes about 5 s to load
    assert session(page)["id"] == user


def session(page):
    response = page.request.get("/api/session")
    assert response.ok
    return response.json()["principal"]


def scoped_text_checks(page, user, dataset):
    other = FACTORY if dataset == FINANCE else FINANCE
    response = page.request.get("/api/workspace")
    assert response.ok
    assert [item["id"] for item in response.json()["datasets"]] == [dataset]
    protected = PROTECTED[dataset][3]
    for target in ("/api/workspace", "/workspace-data.js", "/workspace-fixtures.js"):
        delivery = page.request.get(target)
        assert delivery.ok, (user, target, delivery.status)
        body = delivery.text()
        assert TITLES[other] not in body, (user, target)
        if user.endswith("_restricted"):
            assert protected.lower() not in body.lower(), (user, target, protected)
            if dataset == FACTORY:
                assert "promises delivery by 17 Jun 2024" not in body, (user, target)


def check_reader(page, base, user, dataset):
    other = FACTORY if dataset == FINANCE else FINANCE
    detail, source_key, observation, protected = PROTECTED[dataset]
    restricted = user.endswith("_restricted")
    scoped_text_checks(page, user, dataset)
    page.goto(base + f"#{other}/ingest")
    expect(page.locator("#workspace-main")).to_contain_text("Access unavailable")
    expect(page.locator(".file-library")).to_have_count(0)
    page.goto(base + f"#{dataset}/ingest")
    expect(page.locator(".file-library")).to_be_visible()
    expect(page.locator("#workspace-file, #upload-prompt")).to_have_count(0)
    expect(page.locator('nav a[href$="/review"]')).to_have_count(0)
    expect(page.locator("#workspace-choice option")).to_have_count(1)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")

    page.goto(base + f"#{dataset}/ingest/{detail}/{observation}")
    expect(page.locator("#original-title")).to_be_visible()
    expect(page.locator('[data-ingest-tab="configure"], [data-action="ingest-run"], [data-action="ingest-prepare"]')).to_have_count(0)
    scoped_id = f"{dataset}:source:{source_key}"
    original = page.request.get("/api/original", params={"dataset": dataset, "sourceId": scoped_id})
    if restricted:
        assert original.status == 403
        expect(page.locator("#original-content")).to_contain_text("cannot view the original")
        expect(page.locator(".side-panel")).not_to_contain_text(protected)
        expect(page.locator("#original-download a")).to_have_count(0)
    else:
        assert original.ok
        expect(page.locator(".original-text")).to_be_visible()
        expect(page.locator(".side-panel blockquote")).to_contain_text(protected)

    page.goto(base + f"#{dataset}/knowledge")
    if restricted:
        # 2026-11-23: on a state with many readable sources the restricted reader also sees nodes, so "empty" was
        # state-dependent. The invariant is that nothing of the withheld source shows.
        expect(page.locator("#workspace-main")).not_to_contain_text(protected)
    else:
        expect(page.locator(".canvas-node").first).to_be_visible()
    page.get_by_role("link", name="Ontology", exact=True).click()
    expect(page.locator(".canvas-node").first).to_be_visible()
    page.goto(base + f"#{dataset}/review")
    expect(page.locator("#workspace-main")).to_contain_text("Access unavailable")
    expect(page.locator("[data-action=approve], [data-action=publish]")).to_have_count(0)
    # 2026-11-23: the default report is now a table customer, so the Jade report is opened by its address
    page.goto(base + f"#{dataset}/entity" + ("/entity:jade-logistics" if dataset == FINANCE else ""))
    page.reload()
    expect(page.get_by_role("heading", name="Entity 360")).to_be_visible()
    if dataset == FINANCE:          # Entity 360 is built for Financial Services only
        report = page.locator('[data-report="entity:jade-logistics"]')
        expect(report).to_be_visible(timeout=90000)
        if restricted:
            expect(report.locator("[data-notes]")).to_contain_text("withheld for this account")
            expect(report.locator("[data-evidence-item]")).to_have_count(0)
    else:
        expect(page.locator("[data-entity-unavailable]")).to_be_visible()
    if restricted:
        expect(page.locator("main")).not_to_contain_text(protected)


def check_tab_reload(context, page, base):
    page.goto(base + f"#{FINANCE}/ingest")
    page.locator("#workspace-file").set_input_files(
        {"name": "admin-only-tab.txt", "mimeType": "text/plain", "buffer": b"Private tab fixture"}
    )
    expect(page.get_by_role("link", name="admin-only-tab.txt")).to_be_visible()
    page.goto(base + f"#{FINANCE}/ingest/jade/reported-payment-concern")
    expect(page.locator(".original-text")).to_be_visible()
    other_tab = context.new_page()
    other_tab.goto(base + f"#{FACTORY}/ingest/amendment/amended-delivery")
    expect(other_tab.locator(".original-text")).to_be_visible()
    switch(page, "finance_restricted")
    expect(other_tab.locator("#workspace-main")).to_contain_text("Access unavailable")
    expect(other_tab.locator(".original-text")).to_have_count(0)
    page.go_back()
    expect(page.locator('[data-action="account"]')).to_have_text(session(page)["displayName"])
    expect(page.locator(".original-text")).to_have_count(0)
    page.goto(base + f"#{FINANCE}/ingest")
    expect(page.locator(".file-library")).to_be_visible()
    expect(page.get_by_role("link", name="admin-only-tab.txt")).to_have_count(0)
    other_tab.close()

    context.add_cookies([{"name":"v2_demo_session", "value":"invalid", "url":base, "httpOnly":True}])
    page.reload()
    expect(page.get_by_role("heading", name="Workspace unavailable")).to_be_visible()
    expect(page.locator(".workspace-card")).to_have_count(0)
    assert page.request.get("/workspace-fixtures.js").status == 401
    page.get_by_role("button", name="Choose demo account").click()
    chooser = page.locator("#demo-account")
    expect(chooser).to_have_value("")
    chooser.select_option("manufacturing_reader")
    page.get_by_role("button", name="Switch account").click()
    expect(page.locator(".workspace-card")).to_have_count(1)
    expect(page.locator(".workspace-card")).to_have_attribute("data-dataset", FACTORY)
    assert session(page)["id"] == "manufacturing_reader"


def exercise(base):
    with sync_playwright() as tools:
        with tools.chromium.launch() as browser:
            for width in WIDTHS:
                with browser.new_context(base_url=base, viewport={"width":width, "height":930}) as context:
                    page = context.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(base)
                    expect(page.locator(".workspace-card")).to_have_count(2)
                    assert session(page)["id"] == "admin"
                    for dataset in ROLES["admin"]:
                        page.goto(base + f"#{dataset}/ingest")
                        expect(page.locator("#workspace-file")).to_have_count(1)
                        page.goto(base + f"#{dataset}/review")
                        expect(page.locator(".review-grid")).to_be_visible()
                    for user, allowed in ROLES.items():
                        if user == "admin":
                            continue
                        switch(page, user)
                        expect(page.locator(".workspace-card")).to_have_count(1)
                        expect(page.locator(".workspace-card")).to_have_attribute("data-dataset", allowed[0])
                        check_reader(page, base, user, allowed[0])
                    switch(page, "admin")
                    expect(page.locator(".workspace-card")).to_have_count(2)
                    check_tab_reload(context, page, base)
                    assert not errors, errors
                    print(f"Five demo accounts verified at {width}px.")


if __name__ == "__main__":
    expect.set_options(timeout=20000)      # 2026-11-23: a large state takes about 5 s to load a page
    endpoint = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8137/").rstrip("/") + "/"
    exercise(endpoint)
