"""Two Output A mechanisms (2026-11-22). Report splitting is on by default (`V2_FIELD_PASSAGES=0` turns it off); the interp-v4 prompt is off by default.

`V2_FIELD_PASSAGES=1`: detached OCR tables are read and a label-and-value layout becomes one passage per field.
`V2_INTERPRET_PROMPT=interp-v4`: the decision links commitments, targets and limits as `is_about` (prompts
interp-v4 and shortlist-v3) and an identifier field never keeps a quantity as its stated value.
Unit tests use synthetic text and a scripted chat client (a test double). Identifiers are made up.
"""
import json

import pytest

from multimedia_rag.chunking import ChunkConfig
from semantic_pipeline_v2.chunk_index import chunk_units, units_from_pages
from semantic_pipeline_v2.evidence_links import EntityKeys, chunk_context, interpret_items
from semantic_pipeline_v2.interpreter import (PROMPT_VERSION, SHORTLIST_SYSTEM, SHORTLIST_VERSION, SYSTEM, Interpreter)

from test_candidates import FakeEmbedder, Scripted  # noqa: F401  (fixtures below reuse the scripted client)
from semantic_pipeline_v2.path1 import Path1Mapper
from semantic_pipeline_v2.workspace import load_workspace

# ---------------------------------------------------------------- V2_FIELD_PASSAGES

OCR_PAGE = {"index": 0, "markdown": (
    "# SUSPICIOUS TRANSACTION REPORT\n\nSYNTHETIC TEST DATA\n\n## Account Information\n\n[tbl-1.md](tbl-1.md)\n\n"
    "## Suspicion Narrative\n\nRepeated cash deposits were remitted to a new overseas beneficiary."),
    "tables": [{"id": "tbl-1.md", "content": (
        "|  **Account number** | 0000000000001111  |\n| --- | --- |\n|  **Account type** | Current account  |\n"
        "|  **Account balance** | SGD 8,450.20  |")}]}

LAYOUT_PAGE = {"index": 0, "markdown": (
    "                       SUSPICIOUS TRANSACTION REPORT\n\n Report Information\n\n"
    " STR reference                         SYN-STR-0001\n\n Customer number                       CUST00001\n\n"
    " Account Information\n\n Account type                          Current account\n\n"
    " Account status                        Active\n\n Suspicion Narrative\n\n"
    "Repeated cash deposits below internal review thresholds were consolidated and remitted to a newly added overseas\n"
    "beneficiary.")}

PROSE_PAGE = {"index": 0, "markdown": (
    "# Supply agreement\n\n## 2. Lead time\nThe supplier promises receipt within 14 calendar days.\n\n"
    "| Product | Quantity | Price |\n| --- | --- | --- |\n| Touring-1000 | 71 | 1,200 |\n\n"
    "| Term | Value |\n| --- | --- |\n| Lead time | 14 days |\n\nClosing words of the agreement.")}

XFA_PAGE = {"index": 0, "markdown": 'xfa:data/STR[1]/Header[1]/FormType[1] = "STR"\n'
                                    'xfa:data/STR[1]/Account[1]/AccNum[1] = "SGLO000001"\n'
                                    'xfa:data/STR[1]/Account[1]/AccType[1] = "Current"'}


def chunks(units):
    return chunk_units(units, ChunkConfig(), document_id="doc@" + "a" * 64, name="doc", kind="document")


@pytest.fixture
def fields_on(monkeypatch):
    monkeypatch.setenv("V2_FIELD_PASSAGES", "1")


def test_report_splitting_is_on_by_default(monkeypatch):
    monkeypatch.delenv("V2_FIELD_PASSAGES", raising=False)
    assert any(u["element"] == "field" for u in units_from_pages([OCR_PAGE]))


def test_switch_off_keeps_the_detached_table_placeholder_as_before(monkeypatch):
    monkeypatch.setenv("V2_FIELD_PASSAGES", "0")
    units = units_from_pages([OCR_PAGE])
    assert units == [{"text": OCR_PAGE["markdown"], "page": 0, "element": "text"}]
    assert len(chunks(units)) == 1   # the cause of the one-chunk, no-link report: the values never reach a chunk


def test_detached_ocr_table_becomes_one_passage_per_field_with_its_section(fields_on):
    units = units_from_pages([OCR_PAGE])
    fields = [u["text"] for u in units if u["element"] == "field"]
    assert fields == ["Account number: 0000000000001111", "Account type: Current account",
                      "Account balance: SGD 8,450.20"]
    found = chunks(units)
    by_text = {c["text"]: c for c in found}
    assert {"Account type: Current account", "Account balance: SGD 8,450.20"} <= set(by_text)
    assert by_text["Account type: Current account"]["strategy"] == "whole_field"
    assert any("Repeated cash deposits" in c["text"] for c in found)
    artifact = "\n\n".join(u["text"] for u in units)
    chunk = by_text["Account type: Current account"]
    context = chunk_context("STR", {"start_offset": chunk["start"]}, artifact)
    assert "Section: Account Information" in context


def test_layout_text_pairs_labels_with_values_on_their_own_line(fields_on):
    units = units_from_pages([LAYOUT_PAGE])
    fields = [u["text"] for u in units if u["element"] == "field"]
    assert fields == ["STR reference: SYN-STR-0001", "Customer number: CUST00001", "Account type: Current account",
                      "Account status: Active"]     # never "Current account Account status"
    texts = [u["text"] for u in units if u["element"] == "text"]
    assert "## Report Information" in texts and "## Account Information" in texts
    assert any(t.startswith("## Suspicion Narrative") and "beneficiary." in t for t in texts)


@pytest.mark.parametrize("page", [PROSE_PAGE, XFA_PAGE], ids=["prose", "xfa"])
def test_prose_and_xfa_chunking_is_unchanged_with_the_switch_on(monkeypatch, page):
    monkeypatch.setenv("V2_FIELD_PASSAGES", "0")
    before = units_from_pages([page])
    monkeypatch.setenv("V2_FIELD_PASSAGES", "1")
    assert units_from_pages([page]) == before
    assert [c["text"] for c in chunks(units_from_pages([page]))] == [c["text"] for c in chunks(before)]


# ---------------------------------------------------------------- V2_INTERPRET_PROMPT=interp-v4

M = "manufacturing:definition:adventureworks_manufacturing_demo/"
PRODUCT_NUMBER, ORDERED = M + "field/Production_Product/ProductNumber", M + "metric/sales_ordered_units"
PROMISE = "The customer commits to order at least 71 units of ProductNumber BK-XXXX-00 from 2024-01-01 through 2024-12-31."


@pytest.fixture(scope="module")
def mfg():
    return load_workspace("manufacturing")


@pytest.fixture(scope="module")
def mapper(mfg):
    return Path1Mapper(mfg, FakeEmbedder())


def decide(mfg, mapper, chat, prompt=None):
    """The whole catalog is offered in step one, so the scripted shortlist is always valid."""
    item = {"text": PROMISE, "context": "Document: Framework agreement\nSection: 2. Minimum purchase commitment",
            "source_kind": "synthetic-contract"}
    return interpret_items(mapper, Interpreter(chat, prompt=prompt), EntityKeys(mfg, "/nonexistent"), [item], variant="two_step", whole_max=1000)[0]


def link(ident, value, unit, relation="states_value_of"):
    return {"definition_id": ident, "relation": relation, "stated_value": value, "unit": unit, "period": None,
            "entity": None, "reason": "scripted"}


def test_default_prompts_are_unchanged(monkeypatch):
    monkeypatch.delenv("V2_INTERPRET_PROMPT", raising=False)
    interpreter = Interpreter(Scripted())
    assert interpreter.version == PROMPT_VERSION == "interp-v3" and interpreter.shortlist_version == SHORTLIST_VERSION
    system, _ = interpreter.prompt("p", "c", [])
    assert system == SYSTEM
    assert interpreter.systems == (SYSTEM, SHORTLIST_SYSTEM)


def test_switch_selects_interp_v4_and_shortlist_v3(monkeypatch):
    monkeypatch.setenv("V2_INTERPRET_PROMPT", "interp-v4")
    interpreter = Interpreter(Scripted())
    assert interpreter.version == "interp-v4" and interpreter.shortlist_version == "shortlist-v3"
    decision, shortlist = interpreter.systems
    assert decision.startswith(SYSTEM.split("- stated_value")[0]) and decision != SYSTEM   # v3 word for word plus one rule
    assert "commitment" in shortlist and shortlist != SHORTLIST_SYSTEM
    assert interpreter.prompt(PROMISE, "", [])[0] == decision                      # commitment wording: v4
    assert interpreter.prompt("Effective date: 2024-01-01", "", [])[0] == SYSTEM   # any other passage keeps v3
    with pytest.raises(ValueError):
        Interpreter(Scripted(), prompt="interp-v9")


def test_v4_never_keeps_a_promised_quantity_as_the_value_of_an_identifier_field(mfg, mapper, monkeypatch):
    monkeypatch.delenv("V2_INTERPRET_PROMPT", raising=False)
    reply = json.dumps({"interpretation": "A minimum order commitment.",
                        "links": [link(PRODUCT_NUMBER, "71", "units"), link(ORDERED, "71", "units", "is_about")]})
    old = decide(mfg, mapper, Scripted([PRODUCT_NUMBER, ORDERED], reply))
    assert old[0]["stated_value"] == "71" and old[0]["relation"] == "states_value_of"   # the v3 behaviour, kept
    new = decide(mfg, mapper, Scripted([PRODUCT_NUMBER, ORDERED], reply), prompt="interp-v4")
    product, ordered = new
    assert product["definition_id"] == PRODUCT_NUMBER and product["stated_value"] is None
    assert product["relation"] == "is_about" and "identifier_value_not_held" in product["notes"]["guards"]
    assert ordered["relation"] == "is_about" and ordered["stated_value"] == "71" and ordered["unit"] == "units"
    assert new[0]["method"].startswith("interpret:interp-v4+") and new[0]["notes"]["step1"]["prompt"] == "shortlist-v3"


def test_v4_keeps_an_identifier_value_the_column_holds(mfg, mapper, tmp_path):
    (tmp_path / "Production_Product.csv").write_text("ProductID,ProductNumber\n1,BK-XXXX-00\n")
    reply = json.dumps({"interpretation": "The product the commitment covers.",
                        "links": [link(PRODUCT_NUMBER, "BK-XXXX-00", None)]})
    item = {"text": PROMISE, "context": "", "source_kind": None}
    [kept] = interpret_items(mapper, Interpreter(Scripted([PRODUCT_NUMBER], reply), prompt="interp-v4"),
                             EntityKeys(mfg, tmp_path), [item], variant="two_step", whole_max=1000)[0]
    assert kept["stated_value"] == "BK-XXXX-00" and kept["relation"] == "states_value_of"


def test_a_heading_only_chunk_gets_a_record_without_a_call_when_fields_are_on(mfg, mapper, monkeypatch):
    from semantic_pipeline_v2.evidence_links import build_links
    rows = [{"chunk_id": f"c{n}", "source_id": "s", "source_revision": "r", "start_offset": 0, "end_offset": 1, "page": 0,
             "text": text} for n, text in enumerate(("## Account Information", "Account type: Current account"))]
    keys = EntityKeys(mfg, "/nonexistent")
    monkeypatch.setenv("V2_FIELD_PASSAGES", "0")
    chat = Scripted([])
    assert [r["method"] for r in build_links(mfg, rows, mapper, Interpreter(chat), keys, variant="two_step")
            ].count("heading_only") == 0 and len(chat.users) == 2      # off: every chunk is asked, as before
    monkeypatch.setenv("V2_FIELD_PASSAGES", "1")
    chat = Scripted([])
    links = build_links(mfg, rows, mapper, Interpreter(chat), keys, variant="two_step")
    assert [r["method"] for r in links][0] == "heading_only" and len(links) == 2 and len(chat.users) == 1


# ---------------------------------------------------------------- one EntityKeys per dataset (first load of Evidence and Concepts)

def test_entity_keys_are_built_once_per_dataset(mfg):
    from types import SimpleNamespace
    from semantic_pipeline_v2.workspace_server import entity_keys_of
    server = SimpleNamespace(entity_keys={})
    assert entity_keys_of(server, mfg) is entity_keys_of(server, mfg)   # it parsed the model YAML on every call before
