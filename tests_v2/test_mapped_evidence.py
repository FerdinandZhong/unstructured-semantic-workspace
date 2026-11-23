"""Acceptance checks for definition-guided, source-scoped processing evidence."""

from copy import deepcopy
import json

import pytest

from semantic_pipeline_v2.mapped_evidence import MappedEvidence, definition_context, map_pages
from semantic_pipeline_v2.workspace import ROOT, load_workspace


SOURCE_HASH = "a" * 64
MODEL_HASH = "b" * 64
SOURCE = "financial-services:source:invoice"
MODEL = "financial-services:source:model"
RISK = "financial-services:definition:Customers/RiskRating"
AMOUNT_A = "financial-services:definition:Ledger/Amount"
AMOUNT_B = "financial-services:definition:Payments/Amount"


@pytest.fixture
def workspace():
    return {
        "id": "financial-services",
        "sources": [
            {"id": SOURCE, "title": "Invoice", "kind": "pdf", "citation": {"sha256": SOURCE_HASH}},
            {"id": MODEL, "citation": {"sha256": MODEL_HASH}},
        ],
        "knowledge": {
            "structured": {
                "modelSource": MODEL,
                "fields": [
                    {"id": RISK, "name": "Customers/RiskRating", "title": "RiskRating"},
                    {"id": AMOUNT_A, "name": "Ledger/Amount", "title": "Amount"},
                    {"id": AMOUNT_B, "name": "Payments/Amount", "title": "Amount"},
                ],
                "metrics": [],
            },
            "observations": [],
            "mappings": [],
        },
    }


PAGES = [{"index": 6, "markdown": "# Summary\n\nRisk rating: medium\nAmount: 100\nNotes for filing"}]
PRINCIPAL = {"datasets": ["financial-services"], "withheld": []}


def test_mapping_preserves_locations_and_distinguishes_uncertainty(workspace):
    result = map_pages(workspace, SOURCE, SOURCE_HASH, PAGES)
    observations = {item["quote"]: item for item in result["observations"]}
    risk = observations["Risk rating: medium"]
    assert risk["citation"] == {
        "sha256": SOURCE_HASH, "kind": "ocr_markdown", "page": 7,
        "startLine": 3, "endLine": 3,
    }
    matches = {item["observationId"]: item for item in result["mappings"]}
    assert matches[risk["id"]]["targetId"] == RISK
    assert matches[risk["id"]]["status"] == "candidate"
    amount = matches[observations["Amount: 100"]["id"]]
    assert amount["status"] == "ambiguous" and amount["targetId"] is None
    assert set(amount["candidateIds"]) == {AMOUNT_A, AMOUNT_B}
    notes = matches[observations["Notes for filing"]["id"]]
    assert notes["status"] == "unmapped" and notes["candidateIds"] == []
    assert all("rowId" not in item for item in result["mappings"])


def test_lance_reopen_and_scope(workspace, tmp_path):
    store = MappedEvidence(tmp_path)
    first = store.record(workspace, SOURCE, SOURCE_HASH, PAGES)
    assert store.record(workspace, SOURCE, SOURCE_HASH, PAGES) == first
    reopened = MappedEvidence(tmp_path)
    assert reopened.read(workspace, PRINCIPAL, SOURCE) == first
    projected = reopened.project(workspace, PRINCIPAL)
    assert projected["knowledge"]["observations"] == [
        item for item in first["observations"] if item["observationRole"] == "assertion"
    ]
    assert projected["knowledge"]["mappings"] == first["mappings"]
    assert workspace["knowledge"]["observations"] == []
    for principal in ({"datasets": [], "withheld": []},
                      {"datasets": ["financial-services"], "withheld": [SOURCE]}):
        assert reopened.read(workspace, principal, SOURCE) is None
        assert reopened.project(workspace, principal)["knowledge"]["observations"] == []
    later = reopened.record(workspace, SOURCE, SOURCE_HASH, [{"index": 9, "markdown": "Risk rating: low"}])
    assert MappedEvidence(tmp_path).read(workspace, PRINCIPAL, SOURCE) == later
    with pytest.raises(ValueError, match="page"):
        reopened.record(workspace, SOURCE, SOURCE_HASH, PAGES * 2)
    assert MappedEvidence(tmp_path).read(workspace, PRINCIPAL, SOURCE) == later


def test_old_or_invalid_source_revision_never_projects(workspace, tmp_path):
    store = MappedEvidence(tmp_path)
    store.record(workspace, SOURCE, SOURCE_HASH, PAGES)
    revised = deepcopy(workspace)
    revised["sources"][0]["citation"]["sha256"] = "c" * 64
    assert store.read(revised, PRINCIPAL, SOURCE) is None
    revised["sources"][0]["citation"]["sha256"] = SOURCE_HASH
    revised["sources"][1]["citation"]["sha256"] = "d" * 64
    assert store.read(revised, PRINCIPAL, SOURCE) is None
    with pytest.raises(ValueError, match="Source"):
        store.record(workspace, "manufacturing:source:invoice", SOURCE_HASH, PAGES)
    with pytest.raises(ValueError, match="Source"):
        store.record(workspace, SOURCE, "e" * 64, PAGES)
    with pytest.raises(ValueError, match="page"):
        store.record(workspace, SOURCE, SOURCE_HASH, PAGES * 2)


def test_form_data_never_claims_a_physical_page(workspace, tmp_path):
    workspace["sources"][0].pop("citation")
    store = MappedEvidence(tmp_path, source_pins={SOURCE: SOURCE_HASH})
    result = store.record(
        workspace, SOURCE, SOURCE_HASH, [{"index": 0, "markdown": 'Risk rating: "medium"'}],
        kind="xfa_fields",
    )
    assert result["observations"][0]["citation"] == {
        "sha256": SOURCE_HASH, "kind": "xfa_fields", "locator": "embedded form data",
        "startLine": 1, "endLine": 1,
    }
    assert store.read(workspace, PRINCIPAL, SOURCE) == result
    model_withheld = {**PRINCIPAL, "withheld": [MODEL]}
    assert store.read(workspace, model_withheld, SOURCE) is None


def test_definition_context_prioritizes_hint_and_stays_metadata_only(workspace):
    context = definition_context(workspace, "Please review the risk rating.")
    assert context[0]["id"] == RISK
    assert all(set(item) == {"id", "title", "definition"} for item in context)
    assert {item["id"] for item in context} == {RISK, AMOUNT_A, AMOUNT_B}


def test_guided_proposals_require_offered_definition_and_unique_exact_quote(workspace):
    pages = [
        {"index": 4, "markdown": "Unexpected value: medium\nRepeating note"},
        {"index": 8, "markdown": "Repeating note\nRisk rating: low"},
    ]
    proposals = [
        {"definitionId": RISK, "pageIndex": 4, "quote": "Unexpected value: medium"},
        {"definitionId": RISK, "pageIndex": 4, "quote": "Repeating note"},
        {"definitionId": RISK, "pageIndex": 8, "quote": "Invented text"},
        {"definitionId": AMOUNT_A, "pageIndex": 8, "quote": "Risk rating: low"},
        {"definitionId": RISK, "pageIndex": 4, "quote": "Risk rating: low"},
    ]
    result = map_pages(workspace, SOURCE, SOURCE_HASH, pages, proposals=proposals,
                       offered_definitions=[{"id": RISK}])
    mapping_by_id = {item["observationId"]: item for item in result["mappings"]}
    for item in result["observations"]:
        mapping = mapping_by_id[item["id"]]
        if item["quote"] == "Unexpected value: medium":
            assert mapping["targetId"] == RISK
            assert mapping["basis"] == ["provider_exact_quote"]
        elif item["quote"] == "Repeating note":
            assert mapping["candidateIds"] == []
        else:
            assert mapping["targetId"] == RISK
            assert mapping["basis"] == ["definition_label"]


def test_prepared_manufacturing_source_maps_actual_product_label():
    workspace = load_workspace("manufacturing")
    source_id = "manufacturing:source:MFG-001"
    source = next(item for item in workspace["sources"] if item["id"] == source_id)
    citation = source["citation"]
    original = (ROOT / citation["path"]).read_text()
    result = map_pages(workspace, source_id, citation["sha256"],
                       [{"index": 0, "markdown": original}], kind="native_text")
    product = "manufacturing:definition:adventureworks_manufacturing_demo/field/Production_Product/ProductNumber"
    matched = next(mapping for mapping in result["mappings"] if mapping["targetId"] == product)
    observation = next(item for item in result["observations"] if item["id"] == matched["observationId"])
    line = observation["citation"]["startLine"]
    assert "ProductNumber" in observation["quote"]
    assert observation["quote"] == original.splitlines()[line - 1]
    assert observation["citation"]["locator"] == "original text"
    assert "page" not in observation["citation"]


def test_fields_and_candidates_include_separated_tables_and_page_edges(workspace):
    pages = [{
        "index": 2,
        "markdown": "Email support@example.org\n[tbl-a](tbl-a.md)",
        "header": "1 January 2026",
        "footer": "Call +65 6234 5678",
        "tables": [
            {"id": "tbl-a", "content": "| Customer Risk Rating | USD 1,250.00 |"},
            {"id": "tbl-a", "content": "| Customer Risk Rating | USD 1,250.00 |"},
            {"id": "tbl-b", "format": "html", "content": (
                "<table><tr><td>Amount</td><td>SGD 50.00</td></tr></table>"
                "<script>fake@example.org</script>"
            )},
        ],
    }]
    result = map_pages(workspace, SOURCE, SOURCE_HASH, pages)
    assert result["fieldStatus"] == "available"
    fields = {entry["value"]: entry["source"] for entry in result["fields"]}
    assert list(fields) == ["support@example.org", "1 January 2026", "+65 6234 5678", "USD 1,250.00", "SGD 50.00"]
    assert len(result["fields"]) == len(fields)
    assert fields["USD 1,250.00"] == {
        "kind": "ocr_table", "locator": "table tbl-a", "tableId": "tbl-a",
        "page": 3, "startLine": 1, "endLine": 1,
    }
    assert fields["1 January 2026"]["locator"] == "page header"
    assert fields["+65 6234 5678"]["locator"] == "page footer"
    assert [entry["label"] for entry in result["fields"] if entry["type"] == "amount"] == [
        "Customer Risk Rating", "Amount",
    ]
    table_observations = [item for item in result["observations"] if item["citation"]["kind"] == "ocr_table"]
    assert len(table_observations) == 2
    assert not any(item["quote"] == "[tbl-a](tbl-a.md)" for item in result["observations"])
    risk = table_observations[0]
    assert any(item["observationId"] == risk["id"] and item["targetId"] == RISK for item in result["mappings"])


def test_native_text_and_xfa_fields_keep_logical_line_locators(workspace):
    for kind, label in (("native_text", "original text"), ("xfa_fields", "embedded form data")):
        result = map_pages(workspace, SOURCE, SOURCE_HASH, [
            {"index": 0, "markdown": "Document\nNumber S1234567Z and total SGD 50.00"},
        ], kind=kind)
        first, second = result["fields"]
        assert first["value"] == "S12****7Z" and second["value"] == "SGD 50.00"
        assert first["source"] == {"kind": kind, "locator": label, "startLine": 2, "endLine": 2}


def test_legacy_evidence_stays_readable_without_invented_field_results(workspace, tmp_path):
    store = MappedEvidence(tmp_path)
    result = store.record(workspace, SOURCE, SOURCE_HASH, PAGES)
    key = store._visible_key(workspace, PRINCIPAL, SOURCE)
    for name in ("fields", "fieldStatus", "fieldOrigin"):
        result.pop(name)
    store._table.merge_insert("key").when_matched_update_all().execute([
        {"key": key, "payload": json.dumps(result)}
    ])
    loaded = store.read(workspace, PRINCIPAL, SOURCE)
    assert loaded["fields"] == [] and loaded["fieldStatus"] == "unavailable"
    assert loaded["observations"] == result["observations"]


def test_context_ranks_specific_labels_and_keeps_headings_and_fences_out_of_mapping(workspace):
    pages = [{
        "index": 1,
        "markdown": "# Payments Summary\nAmount: 100\n```text\nRisk rating: hidden\n```\nRisk rating: medium",
        "header": "Internal report",
        "footer": "Page 2 of 2",
    }]
    result = map_pages(workspace, SOURCE, SOURCE_HASH, pages)
    by_quote = {item["quote"]: item for item in result["observations"]}
    mappings = {item["observationId"]: item for item in result["mappings"]}
    amount = by_quote["Amount: 100"]
    assert amount["title"] == "Amount"
    assert "Payments Summary" in amount["context"]
    assert mappings[amount["id"]]["targetId"] == AMOUNT_B
    assert mappings[by_quote["Risk rating: medium"]["id"]]["targetId"] == RISK
    for quote in ("# Payments Summary", "Risk rating: hidden", "Internal report", "Page 2 of 2"):
        item = by_quote[quote]
        assert item["observationRole"] == "context" and item["id"] not in mappings
    assert result["sourcePages"][0]["text"] == pages[0]["markdown"]


def test_legacy_mixed_guidance_reprojects_without_falsifying_or_rewriting_evidence(workspace, tmp_path):
    store = MappedEvidence(tmp_path)
    saved = store.record(workspace, SOURCE, SOURCE_HASH, PAGES)
    risk = next(item for item in saved["observations"] if item["quote"] == "Risk rating: medium")
    anchor = next(item for item in saved["mappings"] if item["observationId"] == risk["id"])
    anchor.pop("guidedCandidateIds")
    anchor.update(candidateIds=[RISK, AMOUNT_A], basis=["definition_label", "provider_exact_quote"],
                  targetId=None, status="ambiguous")
    raw = json.dumps(saved)
    key = store._visible_key(workspace, PRINCIPAL, SOURCE)
    store._table.merge_insert("key").when_matched_update_all().execute([{"key": key, "payload": raw}])
    loaded = store.read(workspace, PRINCIPAL, SOURCE)
    updated = next(item for item in loaded["mappings"] if item["observationId"] == risk["id"])
    assert updated["status"] == "ambiguous" and updated["targetId"] is None
    assert updated["basis"] == ["definition_label", "historical_mixed"]
    assert {RISK, AMOUNT_A} == set(updated["candidateIds"])
    assert store._table.search().where(f"key = '{key}'").limit(1).to_list()[0]["payload"] == raw


def test_shared_catalog_scopes_reference_lookup_and_handles_native_xfa_labels():
    from semantic_pipeline_v2.semantic_retrieval import DefinitionCatalog, label_query

    finance = load_workspace("financial-services")
    manufacturing = load_workspace("manufacturing")
    matches = DefinitionCatalog(finance).search("Show me customer risk rating")
    assert matches and matches[0]["id"].endswith("/Customers/RiskRating")
    assert all(item["id"].startswith("financial-services:definition:") for item in matches)
    assert DefinitionCatalog(manufacturing).search("consumer credit risk rating") == []
    assert DefinitionCatalog(finance).search("volcanic eruption satellites") == []
    label, context = label_query('xfa:data/customer/account[4]/AccountNumber = "value"', "xfa_fields")
    assert label == "AccountNumber" and "account" in context


@pytest.mark.parametrize("layout", ["inline", "detached"])
def test_ocr_form_first_rows_are_evidence_even_with_markdown_divider(workspace, layout):
    first_rows = ("STR reference | Demo123", "Account number | 00123",
                  "Transaction period | 1 Jan 2026 to 2 Feb 2026")
    tables = [
        f"| {first} |\n| --- | --- |\n| Follow-up note | pending |"
        for first in first_rows
    ]
    if layout == "inline":
        page = {"index": 0, "markdown": "\n\n".join(tables)}
    else:
        page = {"index": 0, "markdown": "# Form", "tables": [
            {"id": f"tbl-{index}", "content": text} for index, text in enumerate(tables)
        ]}
    result = map_pages(workspace, SOURCE, SOURCE_HASH, [page])
    notes = {item["title"]: item for item in result["observations"] if item["observationRole"] == "assertion"}
    for name in ("STR reference", "Account number", "Transaction period"):
        assert name in notes and notes[name]["citation"]["page"] == 1


@pytest.mark.parametrize("divider", ["| --- | --- | --- | --- |\n", ""])
def test_wide_table_labels_and_native_prose_are_distinguished(workspace, divider):
    cells = "| Date | Category | Description | Amount |\n" + divider + "| 12 Mar 2026 | Card | Payment | SGD 20 |"
    result = map_pages(workspace, SOURCE, SOURCE_HASH, [{"index": 0, "markdown": cells}])
    assert [item["observationRole"] for item in result["observations"]] == ["context", "assertion"]
    prose = "We confirm delivery under the Supply Contract Details"
    result = map_pages(workspace, SOURCE, SOURCE_HASH, [{"index": 0, "markdown": prose}], kind="native_text")
    assert result["observations"][0]["observationRole"] == "assertion"
    assert result["observations"][0]["quote"] == prose


def test_catalog_uses_token_postings_without_iterating_all_definitions(workspace):
    from semantic_pipeline_v2.semantic_retrieval import DefinitionCatalog

    class NoCatalogScan(list):
        def __iter__(self):
            raise AssertionError("search must use token postings")

    catalog = DefinitionCatalog(workspace)
    catalog.entries = NoCatalogScan(catalog.entries)
    assert catalog.search("Risk rating")[0]["id"] == RISK
    assert [item["id"] for item in catalog.search("Amount")] == [AMOUNT_A, AMOUNT_B]
    assert catalog.search("Amount", context="Payments Summary")[0]["id"] == AMOUNT_B
    assert catalog.search("Volcanic mechanisms") == []

    other = DefinitionCatalog(load_workspace("manufacturing"))
    other.entries = NoCatalogScan(other.entries)
    result = other.search("Production Product ProductNumber")
    assert result[0]["id"].endswith("/Production_Product/ProductNumber")
    assert all(item["id"].startswith("manufacturing:definition:") for item in result)
    assert other.search("consumer credit risk rating") == []
