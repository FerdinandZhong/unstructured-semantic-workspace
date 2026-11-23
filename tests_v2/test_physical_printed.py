"""Output C, second template kind (23 November 2026): a PRINTED label-and-value report (the one-page suspicious
transaction report) fills the same declared tables as the XFA form. One report is one row per table.

All values below are made up by the implementer. The chunk shapes follow what ingest stores for printed reports:
one `Label: value` passage per field (V2_FIELD_PASSAGES), or a whole OCR pipe table whose label cells are not bold."""
import pytest

from semantic_pipeline_v2.ask_actions import Writer
from semantic_pipeline_v2.physical_mapping import build_rows, row_values, template_links
from semantic_pipeline_v2.workspace import load_workspace
from test_ask_actions import form_ctx, form_pg  # noqa: F401  (fixture)
from test_physical_mapping import DATASET

PAIRS = [("STR reference", "SYN-STR-9001"), ("Form version", "Compatibility copy, PDF 1.4"),
         ("Submission date", "8 November 2026"), ("Reporting institution", "Example Bank Ltd"),
         ("Customer number", "TESTCUST-1"), ("Account number", "TESTACC-0001"), ("Account type", "Current account"),
         ("Account status", "Active"), ("Account balance", "SGD 1,234.50"), ("Account holder", "Test Person"),
         ("Transaction period", "1 October 2026 to 31 October 2026"), ("Transaction type", "Domestic transfer"),
         ("Total amount involved", "SGD 12,000.00"), ("Counterparty", "Example Trading Ltd"),
         ("Source / destination", "Singapore / Malaysia"), ("Primary indicator", "Rapid movement of funds")]
TITLE = "# SUSPICIOUS TRANSACTION REPORT\n\nSYNTHETIC TEST DATA - NOT FOR REGULATORY SUBMISSION\n\n## Report Information"
NARRATIVE = "## Suspicion Narrative\nIncoming transfers were moved on quickly.\n\n**Important:** All values are synthetic."


def chunks(texts, source="p", revision="d" * 64):
    out, start = [], 0
    for n, text in enumerate(texts):
        out.append({"chunk_id": f"{source}-{n}", "source_id": source, "source_revision": revision, "start_offset": start,
                    "end_offset": start + len(text), "page": 0, "text": text})
        start += len(text) + 2
    return out


def field_report(pairs=PAIRS, **kw):
    return chunks([TITLE] + [f"{label}: {value}" for label, value in pairs] + [NARRATIVE], **kw)


def table_report(pairs=PAIRS, **kw):
    table = "\n".join([f"|  {pairs[0][0]} | {pairs[0][1]}  |", "| --- | --- |"]
                      + [f"|  {label} | {value}  |" for label, value in pairs[1:]])
    return chunks([TITLE, table, NARRATIVE], **kw)


FORM_TEXT = "\n".join([
    'xfa:data/STR[1]/Header[1]/FormType[1] = "STR"',
    'xfa:data/STR[1]/SuspiciousTransaction[1]/Transaction[1]/AmountOriginal[1] = "5.00"',
    'xfa:data/STR[1]/SuspiciousTransaction[1]/Transaction[1]/AmountOriginalCurrency[1] = "SGD"',
    'xfa:data/STR[1]/SuspiciousTransaction[1]/Transaction[1]/AccountNo[1] = "77"',
    'xfa:data/STR[1]/AccountInformation[1]/Account[1]/AccNum[1] = "A1"'])


def form(source="f", revision="e" * 64):
    return chunks([FORM_TEXT], source=source, revision=revision)


EXPECTED = {
    "STR_Transaction": {"AccountNo": ("ok", "TESTACC-0001", None), "AmountOriginalCurrency": ("ok", "SGD", None),
                        "AmountOriginal": ("ok", "12000.00", "SGD"), "StartDate": ("ok", "2026-10-01", None),
                        "SourceCountry": ("ok", "SG", None), "DestinationCountry": ("ok", "MY", None),
                        "CounterpartyName": ("ok", "Example Trading Ltd", None)},
    "STR_AccountEntity": {"AccNum": ("ok", "TESTACC-0001", None), "AccBalCurr": ("ok", "SGD", None),
                          "AccBalAmount": ("ok", "1234.50", "SGD")},
}


def by_table(rows):
    return {r["table"]: {c["column"]: (c["status"], c["typed"], c["unit"]) for c in r["cells"]} for r in rows}


@pytest.mark.parametrize("report", [field_report, table_report])
def test_a_printed_report_gives_one_typed_row_per_declared_table(report):
    workspace = load_workspace(DATASET)
    report_chunks = report()
    links = template_links(workspace, report_chunks)
    assert links and all(k["relation"] == "states_value_of" and k["status"] == "proposed" and k["method"].startswith("template:")
                         and k["model_revision"] is None and set(k["notes"]) == {"table", "column", "row", "quote"}
                         and k["notes"]["row"] == 1 and k["notes"]["quote"] in k["span"]["text"] for k in links)
    rows = build_rows(workspace, links)
    assert [(r["table"], r["rowKey"], r["status"]) for r in rows] == [("STR_AccountEntity", "1", "previewed"),
                                                                       ("STR_Transaction", "1", "previewed")]
    assert by_table(rows) == EXPECTED
    for row in rows:
        for cell in row["cells"]:      # each cell quotes the printed passage it came from
            assert cell["quote"] and cell["chunkId"] and cell["sourceId"] == "p"
    values = {r["table"]: row_values(workspace, r) for r in rows}
    assert values["STR_Transaction"]["TransactionSeq"] == "1" and values["STR_AccountEntity"]["GroupSeq"] == "1"


def test_a_label_with_no_declared_column_gives_no_cell():
    workspace = load_workspace(DATASET)
    only = [p for p in PAIRS if p[0] in ("STR reference", "Customer number", "Account holder", "Submission date",
                                         "Primary indicator", "Form version", "Account number")]
    rows = build_rows(workspace, template_links(workspace, field_report(only)))
    assert by_table(rows) == {"STR_AccountEntity": {"AccNum": ("ok", "TESTACC-0001", None)},
                              "STR_Transaction": {"AccountNo": ("ok", "TESTACC-0001", None)}}


def test_a_value_that_fails_its_column_type_is_rejected_like_a_form_value_and_not_written():
    workspace = load_workspace(DATASET)
    bad = dict(PAIRS) | {"Total amount involved": "SGD a lot", "Transaction period": "sometime in October",
                         "Account balance": "1,234.50"}             # no currency stated for the balance
    rows = build_rows(workspace, template_links(workspace, field_report(list(bad.items()))))
    cells = {r["table"]: {c["column"]: c for c in r["cells"]} for r in rows}
    tx, acc = cells["STR_Transaction"], cells["STR_AccountEntity"]
    assert (tx["AmountOriginal"]["status"], tx["AmountOriginal"]["reason"]) == ("rejected", "not a decimal number")
    assert (tx["StartDate"]["status"], tx["StartDate"]["reason"]) == ("rejected", "not a dd/mm/yyyy date")
    assert (acc["AccBalAmount"]["status"], acc["AccBalAmount"]["reason"]) == \
        ("rejected", "no valid ISO currency stated for this amount")
    values = {r["table"]: row_values(workspace, r) for r in rows}
    assert "AmountOriginal" not in values["STR_Transaction"] and "StartDate" not in values["STR_Transaction"]
    assert "AccBalAmount" not in values["STR_AccountEntity"]


def test_a_form_still_gives_exactly_its_rows_and_no_printed_link():
    workspace = load_workspace(DATASET)
    links = template_links(workspace, form())
    assert {k["method"] for k in links} == {"template:xfa-form"}
    assert by_table(build_rows(workspace, links)) == {
        "STR_AccountEntity": {"AccNum": ("ok", "A1", None)},
        "STR_Transaction": {"AccountNo": ("ok", "77", None), "AmountOriginalCurrency": ("ok", "SGD", None),
                            "AmountOriginal": ("ok", "5.00", "SGD")}}


def test_a_document_with_neither_layout_still_gets_the_404(form_pg):
    c = form_ctx(form_pg)
    untitled = chunks(["Account number: TESTACC-0001", "Account balance: SGD 1.00", "Counterparty: X Ltd"])  # no report title
    for text in (chunks(["Customer risk rating 4"]), untitled):
        c.links = lambda source, *_, text=text: template_links(c.workspace, text)
        status, out = Writer().preview(c, {"dataset": DATASET, "source": {"kind": "form", "sourceId": "p"}})
        assert status == 404 and out["error"] == "This document has no rows of a predefined table"


def _pdf_ctx(pg):
    c = form_ctx(pg)
    first, second, third = [s for s in c.workspace["sources"] if s.get("kind") == "pdf"][:3]
    made = {first["id"]: form(first["id"]), second["id"]: field_report(source=second["id"])}
    c.links = lambda source, *_: template_links(c.workspace, made[source]) if source in made else []
    return c, first, second, third


def test_a_form_and_a_printed_report_write_rows_of_both_into_the_same_tables(form_pg):
    c, first, second, _ = _pdf_ctx(form_pg)
    status, both = Writer().preview(c, {"dataset": DATASET, "target": "postgres", "source": {
        "kind": "form", "sourceId": first["id"], "sourceIds": [first["id"], second["id"]]}})
    assert status == 200, both
    assert [(t["name"].split(".")[-1], t["rowCount"]) for t in both["tables"]] == [("str_transaction", 2),
                                                                                  ("str_accountentity", 2)]
    amounts = sorted(r.get("AmountOriginal") for t in both["tables"] if t["name"].endswith("str_transaction") for r in t["sample"])
    assert amounts == ["12000.00", "5.00"]


def test_the_suggested_group_offers_a_printed_report_next_to_a_form_and_the_reverse(form_pg):
    c, first, second, third = _pdf_ctx(form_pg)
    for chosen, other in ((first, second), (second, first)):
        status, out = Writer().preview(c, {"dataset": DATASET, "source": {"kind": "form", "sourceId": chosen["id"]}})
        assert status == 200, out
        assert [s["sourceId"] for s in out["suggested"]["sources"]] == [other["id"]]


def test_relink_adds_the_template_links_of_a_stored_report_keeps_its_model_links_and_is_idempotent(form_pg):
    from semantic_pipeline_v2.evidence_links import EvidenceLinks, _record
    from semantic_pipeline_v2.physical_mapping import relink

    class Index:                                   # the stored chunks, as ChunkIndex.read returns them
        labels = []
        read = staticmethod(lambda dataset, source: field_report())
        add_labels = staticmethod(lambda chunk, ids: Index.labels.append((chunk, ids)))
    workspace, store = load_workspace(DATASET), EvidenceLinks(form_pg)
    narrative = field_report()[-1]
    model = _record(narrative, dict(definition_id=None, canonical_definition_id=None, relation="none", stated_value=None,
                                    unit=None, period=None, entity_key="unresolved", entity_keys=[], interpretation=None,
                                    candidates=[], scores={}, method="interpreter", model_revision="m1", status="proposed",
                                    notes={}))
    store.replace(DATASET, "p", [model])
    assert relink(workspace, store, Index, "p") == 10 and relink(workspace, store, Index, "p") == 10
    stored = store.read(DATASET, "p")
    assert [k["method"] for k in stored].count("interpreter") == 1 and len(stored) == 11
    assert by_table(build_rows(workspace, stored)) == EXPECTED and len(Index.labels) == 20
