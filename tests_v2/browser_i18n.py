"""ZH-T2 and ZH-T6 (plan 2026-11-23-v2-zh-amp): the language switch and the fallbacks, in the offline preview.

English is the default and `<html lang="en">`. Pressing 中文 gives zh-CN, a shell label reads Chinese, and the choice
survives a reload; pressing English restores English. `t()` with an unknown key returns the key; an unknown server
sentence stays English; a known code and a `{placeholder}` sentence are translated.
Run with miniconda python3: `python3 tests_v2/browser_i18n.py`.
"""
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ENTRY = (Path(__file__).resolve().parents[1] / "docs/design/v2-workspace/index.html").as_uri() + "#manufacturing/ingest"


def run(width):
    errors = []
    with sync_playwright() as playwright, playwright.chromium.launch() as browser:
        page = browser.new_page(viewport={"width": width, "height": 900})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(ENTRY)
        crumb = page.locator('#top a[href="#datasets"]')
        expect(crumb).to_have_text("Data sources")
        assert page.evaluate("document.documentElement.lang") == "en"
        switch = page.locator("[data-lang-switch]")
        expect(switch.get_by_role("button", name="English")).to_have_attribute("aria-pressed", "true")

        switch.get_by_role("button", name="中文").click()
        expect(crumb).to_have_text("数据源")
        assert page.evaluate("document.documentElement.lang") == "zh-CN"
        page.reload()
        expect(crumb).to_have_text("数据源")
        assert page.evaluate("document.documentElement.lang") == "zh-CN"
        expect(page.locator("[data-lang-switch]").get_by_role("button", name="中文")).to_have_attribute("aria-pressed", "true")

        # ZH-T6: fallbacks, and the code-keyed lookup of server text
        out = page.evaluate("""() => [t('no such key'), t('Rows: {n}', {n: V2I18n.number(12000)}),
          V2I18n.server('An unknown server sentence.'), V2I18n.server('source withheld'), V2I18n.server(null),
          V2I18n.server('Table v2_ask.orders already exists with other columns. Name another table.')]""")
        assert out[0] == "no such key", out
        assert out[1] == "Rows: 12,000", out
        assert out[2] == "An unknown server sentence.", out
        assert out[3] and out[3] != "source withheld", out
        assert out[4] is None, out
        assert "v2_ask.orders" in out[5] and "already exists" not in out[5], out
        # server(): exact keys first; a key is a pattern only with at least 8 literal characters and a literal start, so
        # a short key such as "{count} rows" or "{label} by {axis}" never rewrites another sentence (23 November 2026)
        out = page.evaluate("""() => {
          V2I18n.register('zh-CN', {'{count} rows': '{count} 行', '{label} by {axis}': '按{axis}的{label}', 'Rows: {n}': '行数：{n}',
                                    'Sales by region': '按地区的销售额'});
          return [V2I18n.server('The answer covers 4 rows'), V2I18n.server('Sales by region'), V2I18n.server('Cost by month'),
                  V2I18n.server('Rows: 9'), V2I18n.server('Written to the local database: 7 rows'),
                  V2I18n.server('nearest in the data'), V2I18n.server('{count} rows')];
        }""")
        assert out[0] == "The answer covers 4 rows", out          # no leading-placeholder pattern
        assert out[1] == "按地区的销售额", out                       # an exact key still wins
        assert out[2] == "Cost by month" and out[3] == "Rows: 9", out   # too little literal text: not a pattern
        assert out[4] == "已写入本地数据库：7 行" and out[5] != "nearest in the data", out
        assert out[6] == "{count} 行", out                             # the exact key itself still translates

        page.locator("[data-lang-switch]").get_by_role("button", name="English").click()
        expect(crumb).to_have_text("Data sources")
        assert page.evaluate("document.documentElement.lang") == "en"
        assert page.evaluate("V2I18n.server('source withheld')") == "source withheld"
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"horizontal scroll at {width}px"
        assert not errors, errors
        print(f"Language switch passed at {width}px.")


if __name__ == "__main__":
    for width in (1440, 980, 390):
        run(width)
