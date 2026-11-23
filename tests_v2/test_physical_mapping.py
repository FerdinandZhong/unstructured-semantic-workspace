"""P2b: Output C, physical mapping (finance only, template-bound). Plan tests E13 and E14.

The expected rows below were written by the implementer by reading the two XFA forms by hand. They are not the
controller's held-out set. Server tests run on the private PostgreSQL and the two pinned forms in
`~/Downloads/cleaned data/`; they skip with a named reason only when that folder is absent.
"""

import json
from pathlib import Path

import pytest

from semantic_pipeline_v2 import workspace_server
from semantic_pipeline_v2.evidence_links import EvidenceLinks
from semantic_pipeline_v2.metric_sql import _LOAD_TRACK_DDL
from semantic_pipeline_v2.evidence_links import overlay
from semantic_pipeline_v2.physical_mapping import PhysicalRows, StalePreview, build_rows, check_cell, template_links
from semantic_pipeline_v2.semantic_store import SemanticStore
from semantic_pipeline_v2.workspace import load_workspace, review_database
from test_evidence_labeler import FixtureLabeler
from test_interpreter import ingest, pg_bin, principal_for
from test_workspace import running_server

DATASET = "financial-services"
FORM_1, FORM_2 = (f"{DATASET}:source:cleaned_pdf_{n}" for n in (1, 2))
JADE = f"{DATASET}:source:jade-adverse-media"
FORMS = Path.home() / "Downloads" / "cleaned data"
FIN_YAML = Path(__file__).resolve().parents[1] / "demo_data/finance/finance.ossie.yaml"
needs_forms = pytest.mark.skipif(not FORMS.is_dir(), reason="the pinned PDFs in ~/Downloads/cleaned data are absent")

# Implementer-written, from the XFA field listing of each form (Transaction[1] and nothing else).
EXPECTED = {
    FORM_1: {"TransactionSeq": "1", "AccountNo": "0", "IsActualTransaction": "Y", "TransactionType": "GENTXN004",
             "InOutTrans": "OUTWARD", "AmountOriginalCurrency": "SGD", "AmountOriginal": "500.00",
             "OverPeriodTime": "N", "StartDate": "2030-09-17", "SourceCountry": "SG", "DestinationCountry": "SG",
             "CounterpartyName": "OTHER", "CounterpartyBankName": "OTHER BANK", "CounterpartyAccountNo": "2345678",
             "IsCounterpartyAddressUnknown": "N", "IsCounterpartyAddressPartial": "N"},
    FORM_2: {"TransactionSeq": "1", "AccountNo": "0", "IsActualTransaction": "Y", "TransactionType": "CASTXN004",
             "AmountOriginalCurrency": "SGD", "AmountOriginal": "123454321.00", "OverPeriodTime": "N",
             "StartDate": "2025-09-03", "IsCounterpartyAddressUnknown": "N", "IsCounterpartyAddressPartial": "N"},
}
TARGET = "v2_facts_financial_services.str_transaction"
SELECT = ("SELECT COALESCE(json_agg(row_to_json(t) ORDER BY t.accountno, t.amountoriginal), '[]') FROM "
          "(SELECT transactionseq::text, accountno, isactualtransaction, transactiontype, inouttrans, "
          "amountoriginalcurrency, amountoriginal::text, overperiodtime, startdate::text, sourcecountry, "
          "destinationcountry, counterpartyname, counterpartybankname, counterpartyaccountno, "
          "iscounterpartyaddressunknown, iscounterpartyaddresspartial FROM " + TARGET + ") t")


def table_rows(database):
    return json.loads(database.sql(SELECT))


def expected_lower(source):
    return {k.lower(): EXPECTED[source].get(k) for k in EXPECTED[FORM_1]}  # a field the form lacks stays NULL


def cells(row):
    return {c["column"]: c for c in row["cells"]}


def server(tmp_path, monkeypatch, role="admin"):
    principal = principal_for(DATASET, role)
    monkeypatch.setattr(workspace_server.WorkspaceRequest, "principal", lambda _: principal)
    monkeypatch.setattr(workspace_server, "resolve_session", lambda *_: principal)
    labeler = FixtureLabeler(lambda anchors, definitions: {"labels": []})
    return principal, dict(evidence_dir=tmp_path / "evidence", evidence_labeler=labeler, chunk_embedder=None,
                           mapping_embedder=None, interpreter=None)


def read_form(call, source):
    status, result = call("POST", "/api/ocr", {"dataset": DATASET, "sourceId": source, "mode": "xfa-fields", "options": {}})
    assert status == 200 and result["evidenceLinks"]["status"] == "saved"
    return result


def act(call, action, source):
    return call("POST", f"/api/physical-rows/{action}", {"dataset": DATASET, "sourceId": source})


# ---------------------------------------------------------------- E13

@needs_forms
def test_e13_preview_then_confirm_writes_the_expected_rows_with_provenance_and_talk_to_data_answers(tmp_path, monkeypatch):
    _, options = server(tmp_path, monkeypatch)
    with review_database(tmp_path / "state", pg_bin()) as database, running_server(database, **options) as call:
        SemanticStore(database).import_ossie(DATASET, FIN_YAML)
        database.sql(_LOAD_TRACK_DDL)  # the projection tracker a deployed database already has; this table has no CSV batch
        for source in (FORM_1, FORM_2):
            read_form(call, source)
            # deterministic template links, marked as such, never as a model interpretation
            links = call("GET", f"/api/evidence-links?dataset={DATASET}&sourceId={source}")[1]["links"]
            template = [k for k in links if k["method"].startswith("template:")]
            assert template and all(k["relation"] == "states_value_of" and k["model_revision"] is None for k in template)
        # preview: rows are shown, nothing is written to the target table
        status, preview = act(call, "preview", FORM_1)
        assert status == 200 and preview["table"] == "STR_Transaction" and preview["writeAvailable"] is True
        assert [r["status"] for r in preview["rows"]] == ["previewed", "previewed"]   # P2e: STR_Transaction, then STR_AccountEntity
        assert {c: v["value"] for c, v in cells(preview["rows"][0]).items() if v["status"] == "ok"} == {
            k: v for k, v in EXPECTED[FORM_1].items() if k not in ("TransactionSeq", "StartDate")} | {"StartDate": "17/09/2030"}
        assert database.sql(f"SELECT to_regclass('{TARGET}') IS NULL") == "t"  # nothing written, no table yet
        # confirm writes it, each cell with source, revision, chunk id and quoted span
        status, written = act(call, "confirm", FORM_1)
        assert status == 200 and [r["status"] for r in written["rows"]] == ["written", "written"]
        for cell in written["rows"][0]["cells"]:
            assert cell["status"] == "ok" and cell["chunkId"] and cell["quote"] and cell["sourceId"] == FORM_1
            assert cell["sourceRevision"] and cell["column"] in cell["quote"]
        assert table_rows(database) == [expected_lower(FORM_1)]
        # the second form, then the same revision again adds no row
        read_form(call, FORM_2)
        assert act(call, "preview", FORM_2)[0] == 200 and act(call, "confirm", FORM_2)[0] == 200
        read_form(call, FORM_1)
        assert act(call, "preview", FORM_1)[1]["rows"][0]["status"] == "written"
        assert act(call, "confirm", FORM_1)[0] == 200
        assert table_rows(database) == sorted([expected_lower(FORM_1), expected_lower(FORM_2)],
                                              key=lambda r: (r["accountno"], r["amountoriginal"]))
        # Talk to Data answers from the new table through the declared metric
        status, answer = call("POST", "/api/talk-to-data", {"dataset": DATASET, "mode": "query",
                              "question": "Count the reported suspicious transactions."})
        assert status == 200 and answer["status"] == "grounded" and answer["facts"][0]["value"] == 2
        assert answer["facts"][0]["definitionId"].endswith("metric/reported_suspicious_transactions")
        status, answer = call("POST", "/api/talk-to-data", {"dataset": DATASET, "mode": "query",
                              "question": "What is the total reported suspicious transaction value in SGD?"})
        assert status == 200 and answer["facts"][0]["value"] == 123454821


# ---------------------------------------------------------------- E14

@needs_forms
def test_e14_reader_gets_403_untemplated_type_offers_no_write_and_bad_or_unlinked_values_are_not_written(tmp_path, monkeypatch):
    principal, options = server(tmp_path, monkeypatch)
    with review_database(tmp_path / "state", pg_bin()) as database, running_server(database, **options) as call:
        read_form(call, FORM_1)
        # an untemplated document type: no table, no write action
        assert ingest(call, DATASET, JADE)[0] == 200
        status, none = act(call, "preview", JADE)
        assert status == 200 and none["writeAvailable"] is False and none["table"] is None and none["rows"] == []
        assert act(call, "confirm", JADE)[0] == 404
        # a bad value (wrong type) and a field with no link
        store, links = EvidenceLinks(database), None
        links = store.read(DATASET, FORM_1)
        for link in links:
            if link["method"].startswith("template:") and link["definition_id"].endswith("/AmountOriginal"):
                link["stated_value"] = "five hundred"
            if link["method"].startswith("template:") and link["definition_id"].endswith("/StartDate"):
                link["definition_id"] = None
                link["relation"] = "none"
        store.replace(DATASET, FORM_1, [{**k, "scores": k["scores"], "notes": k["notes"]} for k in links])
        status, preview = act(call, "preview", FORM_1)
        row = cells(preview["rows"][0])
        assert row["AmountOriginal"]["status"] == "rejected" and "five hundred" == row["AmountOriginal"]["value"]
        assert row["AmountOriginal"]["reason"]
        assert "StartDate" not in row
        assert act(call, "confirm", FORM_1)[0] == 200
        [stored] = table_rows(database)
        assert stored["amountoriginal"] is None and stored["startdate"] is None and stored["accountno"] == "0"
        assert [r["status"] for r in act(call, "confirm", FORM_1)[1]["rows"]] == ["written", "written"]
        # a reader may not preview or confirm
        principal["role"] = "reader"
        assert act(call, "confirm", FORM_1)[0] == 403 and act(call, "preview", FORM_1)[0] == 403
        assert len(table_rows(database)) == 1


# ---------------------------------------------------------------- unit

def test_check_cell_type_and_unit_rules():
    assert check_cell("Decimal", "500.00", currency="SGD") == ("500.00", None)
    assert check_cell("Decimal", "five", currency="SGD")[0] is None
    assert check_cell("Decimal", "500.00", currency=None, needs_currency=True)[0] is None  # no unit, no write
    assert check_cell("Decimal", "500.00", currency="sg", needs_currency=True)[0] is None
    assert check_cell("Date", "17/09/2030") == ("2030-09-17", None)
    assert check_cell("Date", "31/02/2030")[0] is None and check_cell("Date", "2030-09-17")[0] is None
    assert check_cell("Integer", "12") == ("12", None) and check_cell("Integer", "1.5")[0] is None
    assert check_cell("String", "S1234567D") == ("S12****7D", None)  # NRIC is masked in the stored value


def test_template_links_come_only_from_declared_field_names_and_leave_other_forms_alone():
    workspace = load_workspace(DATASET)
    base = "xfa:data/STR[1]/SuspiciousTransaction[1]/Transaction[1]/"
    form = ('xfa:data/STR[1]/Header[1]/FormType[1] = "STR"\n' + base + 'AmountOriginal[1] = "5.00"\n'
            + base + 'AmountOriginalCurrency[1] = "SGD"\n' + base + 'NotAColumn[1] = "x"\n'
            'xfa:data/STR[1]/Other[1]/AmountOriginal[1] = "9"')
    chunk = {"chunk_id": "c1", "source_id": "s", "source_revision": "a" * 64, "start_offset": 0, "end_offset": len(form),
             "page": 0, "text": form}
    links = template_links(workspace, [chunk])
    assert sorted(k["definition_id"].rsplit("/", 1)[1] for k in links) == ["AmountOriginal", "AmountOriginalCurrency"]
    assert all(k["method"].startswith("template:") and k["relation"] == "states_value_of" and k["span"]["text"] == form
               for k in links)
    [amount] = [k for k in links if k["definition_id"].endswith("/AmountOriginal")]
    assert amount["stated_value"] == "5.00" and amount["unit"] == "SGD" and amount["notes"]["row"] == 1
    assert template_links(workspace, [{**chunk, "text": form.replace('"STR"', '"XYZ"')}]) == []  # not this form type
    assert template_links(workspace, [{**chunk, "text": "Customer risk rating 4"}]) == []


# ---------------------------------------------------------------- editor corrections reach Output C
# Hand-written form text and corrections (test doubles, not recordings). Corrections go through the real `overlay`.

BASE = "xfa:data/STR[1]/SuspiciousTransaction[1]/Transaction[1]/"
FORM = ('xfa:data/STR[1]/Header[1]/FormType[1] = "STR"\n' + BASE + 'AmountOriginal[1] = "5.00"\n'
        + BASE + 'AmountOriginalCurrency[1] = "SGD"\n' + BASE + 'AccountNo[1] = "77"')
CHUNK = {"chunk_id": "c1", "source_id": "s", "source_revision": "a" * 64, "start_offset": 0, "end_offset": len(FORM),
         "page": 0, "text": FORM}


def amount_def(workspace, name="AmountOriginal"):
    return next(k["definition_id"] for k in template_links(workspace, [CHUNK]) if k["definition_id"].endswith("/" + name))


def correct(workspace, action, name="AmountOriginal", target=None):
    links = template_links(workspace, [CHUNK])
    fix = {"observationId": "o1", "correctionAction": action, "originalTargetId": amount_def(workspace, name),
           "targetId": target, "correctionActor": "reviewer-1", "correctionAt": "2026-11-21T00:00:00Z"}
    return links, overlay(links, [fix], [{"id": "o1", "quote": f'{name}[1] = '}])


def cell_map(links, workspace):
    [row] = build_rows(workspace, links)
    return {c["column"]: c for c in row["cells"]}


def test_uncorrected_template_links_build_the_same_rows_as_before():
    workspace = load_workspace(DATASET)
    cells_ = cell_map(template_links(workspace, [CHUNK]), workspace)
    assert {c: v["status"] for c, v in cells_.items()} == {"AmountOriginal": "ok", "AmountOriginalCurrency": "ok", "AccountNo": "ok"}
    assert all("provenance" not in v and "actor" not in v for v in cells_.values())
    assert cells_["AmountOriginal"]["value"] == "5.00" and cells_["AmountOriginal"]["unit"] == "SGD"


def test_a_rejected_link_gives_no_cell_and_the_preview_says_a_reviewer_removed_it():
    workspace = load_workspace(DATASET)
    _, corrected = correct(workspace, "remove")
    cells_ = cell_map(corrected, workspace)
    assert cells_["AmountOriginal"]["status"] == "removed" and cells_["AmountOriginal"]["reason"] == "removed by a reviewer"
    assert cells_["AmountOriginal"]["typed"] is None and cells_["AccountNo"]["status"] == "ok"
    [row] = build_rows(workspace, corrected)
    assert not [c for c in row["cells"] if c["column"] == "AmountOriginal" and c["status"] == "ok"]


def test_a_link_remapped_to_a_definition_outside_the_table_gives_no_cell():
    workspace = load_workspace(DATASET)
    _, corrected = correct(workspace, "replace", target="financial-services:definition:elsewhere/field/Other/Thing")
    assert "AmountOriginal" not in cell_map(corrected, workspace)


def test_a_corrected_value_is_used_type_checked_again_and_credited_to_the_actor():
    workspace = load_workspace(DATASET)
    links = template_links(workspace, [CHUNK])
    # the correction contract for a value: the link stays states_value_of, status corrected, with the new stated value
    fixed = [{**k, "stated_value": "6.50", "status": "corrected", "actor": "reviewer-1"}
             if k["definition_id"].endswith("/AmountOriginal") else k for k in links]
    cell = cell_map(fixed, workspace)["AmountOriginal"]
    assert cell["value"] == "6.50" and cell["typed"] == "6.50" and cell["status"] == "ok"
    assert cell["provenance"] == "corrected" and cell["actor"] == "reviewer-1"
    bad = [{**k, "stated_value": "six"} if k["status"] == "corrected" else k for k in fixed]
    cell = cell_map(bad, workspace)["AmountOriginal"]
    assert cell["status"] == "rejected" and cell["provenance"] == "corrected"


def test_a_correction_made_after_a_preview_makes_confirm_refuse_until_a_new_preview(tmp_path):
    workspace = load_workspace(DATASET)
    original, corrected = correct(workspace, "remove")
    with review_database(tmp_path / "state", pg_bin()) as database:
        database.sql(_LOAD_TRACK_DDL)
        rows = PhysicalRows(database)
        rows.preview(workspace, "s", original, "admin")
        with pytest.raises(StalePreview):
            rows.confirm(workspace, "s", "admin", links=corrected)
        assert [r["status"] for r in rows.read(DATASET, "s")] == ["previewed"]   # nothing was written
        rows.preview(workspace, "s", corrected, "admin")
        done = rows.confirm(workspace, "s", "admin", links=corrected)
        assert [r["status"] for r in done] == ["written"]
        assert "AmountOriginal" not in {c["column"] for c in done[0]["cells"] if c["status"] == "ok"}
