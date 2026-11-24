"""Clarifications in Simplified Chinese, a chain of two choices, and examples for a customer (24 November 2026).

1. Every fixed sentence, note, label and detail of a clarification reads in Chinese through the real `i18n.js` and its
   dictionaries (run by node, no browser): a sentence that starts with a value (a definition label, a count) is a
   pattern when its key holds at least 24 literal characters; a short key such as "{count} rows" never is. Notes are
   sent one per line, so each is matched alone.
2. A choice carries the definition it chose. A clicked choice plans with that definition fixed, and the choices of the
   next clarification are built fresh from the definition's name and the chosen values, never appended to the text.
3. A value recorded per customer, in a table without a name column: "Name the customer you mean." and a note with
   examples (identifiers, so only an account that reads them sees the note).
Every model is a FAKE. Data values stay as written.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import semantic_pipeline_v2.answer_path as ap
from semantic_pipeline_v2 import ask_actions, identifier_mask, ui_lang
from semantic_pipeline_v2.evidence_links import EntityKeys
from semantic_pipeline_v2.workspace import load_workspace
from test_answer_plan import ADMIN, CASA, FIN, MFG, Index, Script, plan, run
from test_ask_history import served  # noqa: F401  (a fixture)

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "docs/design/v2-workspace"
NODE = shutil.which("node")
HARNESS = r"""
const fs = require('fs'), vm = require('vm');
const root = process.argv[1], payload = JSON.parse(process.argv[2]);
const ctx = {localStorage: {getItem: () => 'zh-CN', setItem() {}}, document: {documentElement: {}}, location: {reload() {}}, Intl};
ctx.window = ctx;
vm.createContext(ctx);
const files = ['i18n.js', ...fs.readdirSync(root + '/i18n').filter(n => n.endsWith('.js')).sort().map(n => 'i18n/' + n)];
for (const file of files) vm.runInContext(fs.readFileSync(root + '/' + file, 'utf8'), ctx);
if (payload.extra) ctx.V2I18n.register('zh-CN', payload.extra);
process.stdout.write(JSON.stringify(payload.texts.map(text => ctx.V2I18n.server(text))));
"""
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def zh(texts, extra=None):
    """V2I18n.server(text) in zh-CN for each text, by the page's own i18n.js and dictionaries."""
    out = subprocess.run([NODE, "-e", HARNESS, str(WEB), json.dumps({"texts": list(texts), "extra": extra})],
                         capture_output=True, text=True, timeout=60, check=True)
    return json.loads(out.stdout)


def card_texts(result):
    """What a clarification card shows from the server: the reason, each note line, each label and detail."""
    out = [result["reason"], *(result.get("hintNote") or "").split("\n"), *result.get("caveats", [])]
    out += [h[k] for h in result.get("hints") or () for k in ("label", "detail") if h.get(k)]
    return [t for t in out if t]


def english_left(texts, data):
    """Runs of two or more English words left after the data values are taken out."""
    left = []
    for text in texts:
        for value in sorted(data, key=len, reverse=True):
            text = text.replace(value, " ")
        left += re.findall(r"[A-Za-z][A-Za-z']*(?:[ ,]+[A-Za-z][A-Za-z']*)+", text)
    return left


# ---------------------------------------------------------------- 1. the matcher

@needs_node
def test_a_key_that_starts_with_a_value_is_a_pattern_only_with_enough_literal_text():
    extra = {"{count} rows": "{count} 行", "{label} by {axis}": "按{axis}的{label}", "{a} is short here": "短{a}",
             "{label} needs a long fixed tail of words here.": "{label} 需要长尾。"}
    out = zh(["The answer covers 4 rows", "Cost by month", "x is short here", "Revenue needs a long fixed tail of words here.",
              "Revenue needs a long fixed tail of words here. And more.", "{count} rows"], extra)
    assert out[0] == "The answer covers 4 rows"          # short leading keys never act as patterns
    assert out[1] == "Cost by month" and out[2] == "x is short here"
    assert out[3] == "Revenue 需要长尾。"                # 24 or more literal characters: a pattern
    assert out[4] == "Revenue needs a long fixed tail of words here. And more."     # anchored at both ends
    assert out[5] == "{count} 行"                        # the exact key still translates


@needs_node
def test_the_most_specific_key_wins():
    sent = ["Sales line revenue needs period, and the question does not give it. Name it.",
            "Inventory units needs product, and the question does not give it. Name it."]
    out = zh(sent)
    assert "期间" in out[0] and "period" not in out[0], out
    assert out[1].startswith("Inventory units ") and "product" in out[1] and "question" not in out[1], out


# ---------------------------------------------------------------- 1. the live cases read in Chinese

LIVE = []        # (case, English, Chinese) for the report


def chinese(case, result, data):
    texts = card_texts(result)
    out = zh(texts) if NODE else texts
    LIVE.extend((case, a, b) for a, b in zip(texts, out))
    assert not english_left(out, data), list(zip(texts, out))
    return out


@needs_node
def test_a_missing_period_reads_in_chinese(answer_pg):
    result = run(answer_pg, MFG, "What was the sales line revenue?", Script([plan(measure="metric/sales_line_revenue")]))
    out = chinese("period", result, ["Sales line revenue", "May", "June"] + [h["label"] for h in result["hints"]])
    assert out[0] == "Sales line revenue 需要期间，但问题中没有给出。请指明。", out[0]


@needs_node
def test_records_with_one_name_read_in_chinese(answer_pg):
    ws = load_workspace(MFG)
    asked = "How many orders did Friendly Bike Shop place?"
    ctx = ap._Ctx(asked, ws, ADMIN(MFG), answer_pg, None, None, EntityKeys(ws), None, None, None)
    found, _ = ap._entities(ctx)
    named = next(e for e in found if e.get("ambiguous"))
    result = run(answer_pg, MFG, asked, Script([plan(measure="metric/sales_order_count", entities=[f"E{found.index(named) + 1}"])]))
    assert result["reason"].startswith("2 records of Sales_Store are named 'Friendly Bike Shop'.")
    out = chinese("records", result, ["Sales_Store", "Friendly Bike Shop", "BusinessEntityID"] + [h["label"] for h in result["hints"]])
    assert "Give the" not in out[0] and "BusinessEntityID" in out[0]


@needs_node
def test_a_large_set_note_and_two_notes_read_in_chinese(answer_pg):
    product = run(answer_pg, MFG, "How many units are in stock?", Script([plan(measure="metric/inventory_units")]))
    names = product["hintNote"].split(": ", 1)[1].split(". The data")[0]
    chinese("product note", product, ["Inventory units", "product", *re.split(r", | and ", names)])
    two = run(answer_pg, FIN, "How much money moved through the accounts?", Script([plan(measure=CASA)]))
    assert two["hintNote"].count("\n") >= 1          # one note per line, so each is matched alone
    chinese("two notes", two, ["CASA transaction value", "business date", "currency", "November", "HKD", "EUR", "USD", "JPY", "SGD"])


@needs_node
def test_fixed_labels_and_details_read_in_chinese():
    forms = [{"id": f"s{n}", "title": f"customer-form{n}_clean.pdf", "kind": "pdf"} for n in (3, 4)]
    hints = ask_actions._write_hints({"words": "x", "concepts": [], "files": forms, "tables": [{"name": "STR_Transaction", "files": forms}]})
    figures = ap._hints(type("C", (), {"question": "What was the limit?"})(), "figures", {})
    texts = [h[k] for h in hints["hints"] + figures["hints"] for k in ("label", "detail") if h.get(k)] + [ap.ONE_TOTAL]
    out = zh(texts)
    LIVE.extend(("labels", a, b) for a, b in zip(texts, out))
    assert not english_left(out, ["customer-form3.pdf", "customer-form4.pdf", "STR_Transaction"]), out


# ---------------------------------------------------------------- 2. a chain of two choices

BAD = plan(measure="metric/sales_ordered_units", filters=[{"field": "field/Production_Product/ProductCategoryID", "value": "1"}])


def ask(pg, question, chat, definition=None):
    ws = load_workspace(MFG)
    return ap.answer(question, ws, ADMIN(MFG), database=pg, chunks=Index({}), entity_keys=EntityKeys(ws), chat=chat,
                     definition=definition)


def test_a_chain_of_two_choices_builds_each_text_fresh_and_ends_answered(answer_pg):
    first = ask(answer_pg, "How many bikes did we sell?", Script([BAD, BAD]))
    assert first["refusalReason"] == "not planned"
    [choice] = first["hints"]
    assert choice["text"] == "What were the Sales ordered units?" and choice["definition"].endswith("metric/sales_ordered_units")
    # the planner wanders to another measure: the chosen definition is planned all the same
    second = ask(answer_pg, choice["text"], Script([plan(measure="metric/sales_line_revenue")]), choice["definition"])
    assert second["status"] == "clarify" and second["reason"].startswith("Sales ordered units needs period"), second["reason"]
    years = [h["label"] for h in second["hints"]]
    assert [h["text"] for h in second["hints"]] == [f"What were the Sales ordered units in {y}?" for y in years]
    assert all(h["definition"] == choice["definition"] for h in second["hints"])
    third = ask(answer_pg, second["hints"][0]["text"], Script([plan(measure="metric/sales_line_revenue", time=["D1"])]),
                second["hints"][0]["definition"])
    assert third["status"] == "answered", third.get("reason")
    assert third["facts"][0]["definition"] == choice["definition"] and years[0] in json.dumps(third["facts"][0]["filters"])


def test_a_condition_the_chosen_definition_cannot_take_is_said_not_dropped(answer_pg):
    first = ask(answer_pg, "How many bikes did we sell?", Script([BAD, BAD]))
    choice = first["hints"][0]
    wander = plan(measure="metric/sales_ordered_units", filters=[{"field": "field/Production_Product/Name", "value": "bike"}])
    second = ask(answer_pg, choice["text"], Script([wander]), choice["definition"])
    assert second["status"] == "clarify" and second["reason"].startswith("Sales ordered units needs period"), second["reason"]
    assert second["caveats"] == ["The data holds no Product name named 'bike', so the answer is not limited to it."] \
        or second["caveats"][0].startswith("The data holds no "), second.get("caveats")
    assert all(h["text"].startswith("What were the Sales ordered units in ") for h in second["hints"])


def test_without_a_chosen_definition_nothing_changes(answer_pg):
    plain = ask(answer_pg, "What was the sales line revenue?", Script([plan(measure="metric/sales_line_revenue")]))
    assert all(h["text"].startswith("What was the sales line revenue in ") and "definition" not in h for h in plain["hints"])
    unknown = ask(answer_pg, "What was the sales line revenue?", Script([plan(measure="metric/sales_line_revenue")]),
                  "manufacturing:definition:x/metric/not_in_the_model")
    assert [h["text"] for h in unknown["hints"]] == [h["text"] for h in plain["hints"]]       # an unknown id is ignored


def test_the_definition_of_a_clicked_choice_reaches_the_planner(served):  # noqa: F811
    ask_, chat, _ = served
    chat.script = Script([BAD, BAD])
    definition = ask_("How many bikes did we sell?")["result"]["hints"][0]["definition"]
    chat.script = Script([plan(measure="metric/sales_line_revenue", time=["D1"])])
    out = ask_("What were the Sales ordered units in 2024?", hint=True, definition=definition)
    assert out["result"]["status"] == "answered", out["result"].get("reason")
    assert out["result"]["facts"][0]["definition"] == definition


def test_a_clicked_choice_in_chinese_says_what_was_asked(served):  # noqa: F811
    ask_, chat, _ = served
    chat.script = Script([plan(measure="metric/sales_line_revenue", time=["D1"])])
    out = ask_("What was the sales line revenue in 2024?", hint=True, lang=ui_lang.ZH)
    assert not chat.of(ui_lang.REWRITE_SYSTEM) and not chat.of(ui_lang.STANDALONE_SYSTEM)
    assert out["questionEnglish"] == "What was the sales line revenue in 2024?"
    assert out["result"]["status"] == "answered", out["result"].get("reason")
    chat.script = Script([plan(measure="metric/sales_line_revenue", time=["D1"])])
    english = ask_("What was the sales line revenue in 2024?", hint=True)
    assert "questionEnglish" not in english             # the English page is unchanged


def test_an_invalid_definition_field_is_refused(served):  # noqa: F811
    ask_, _, _ = served
    ask_("x", hint=True, definition=["no"], status=400)


# ---------------------------------------------------------------- 3. a value recorded per customer

RISK = "What is the customer's risk rating?"


@needs_node
def test_a_value_recorded_per_customer_names_the_customer_and_gives_examples(answer_pg):
    result = run(answer_pg, FIN, RISK, Script([plan(measure="field/Customers/RiskRating", aggregate="value")]))
    assert result["status"] == "clarify"
    assert result["reason"].endswith("is recorded per row. Name the customer you mean."), result["reason"]
    assert re.fullmatch(r"Name one customer, for example: \S+, \S+ and \S+\. The data holds \d+ of them\.", result["hintNote"]), result["hintNote"]
    ids = re.findall(r"CUS\w+", result["hintNote"])
    chinese("customer", result, ["Risk rating", "customer", *ids])
    reader = type("R", (), {"masked": staticmethod(lambda v: identifier_mask.mask(v, {"customer": set(ids)}))})()
    assert "hintNote" not in ask_actions._shown_hints(reader, dict(result))          # identifiers: never for a reader
    admin = type("A", (), {"masked": staticmethod(lambda v: v)})()
    assert ask_actions._shown_hints(admin, dict(result))["hintNote"] == result["hintNote"]


def test_report_lines():
    """Prints what the card shows in Chinese (for the record); asserts nothing new."""
    for case, english, chinese_text in LIVE:
        print(f"[{case}] {english}\n    -> {chinese_text}")
