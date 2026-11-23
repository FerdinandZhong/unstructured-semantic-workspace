"""P2e part 2: the second Output C table, STR_AccountEntity (AccountInformation/Account and EntityProfile/Entity).

Every expected value below was written by the implementer from the XFA field listing of the two pinned forms
(implementer-written; not the controller's held-out rows). Server tests need `~/Downloads/cleaned data` and skip with
the named reason of `needs_forms` when it is absent.
"""
import json

import pytest

from semantic_pipeline_v2.evidence_links import overlay
from semantic_pipeline_v2.metric_sql import Filter, _LOAD_TRACK_DDL, query_metric
from semantic_pipeline_v2.physical_mapping import build_rows, declared, template_links
from semantic_pipeline_v2.semantic_store import SemanticStore
from semantic_pipeline_v2.workspace import load_workspace, review_database
from test_interpreter import pg_bin
from test_physical_mapping import DATASET, FIN_YAML, FORM_1, FORM_2, act, cells, needs_forms, read_form, server
from test_workspace import running_server

TABLE = "STR_AccountEntity"
TARGET = "v2_facts_financial_services.str_accountentity"
ACC, ENT = "xfa:data/STR[1]/AccountInformation[1]/Account[1]/", "xfa:data/STR[1]/EntityProfile[1]/Entity[1]/"
# what each form states in the direct fields of Account[1] and Entity[1] (dates as stored; nothing else is a cell)
EXPECTED = {
    FORM_1: {"isaccmaintainedwithri": "N", "isaccheldbankornonbank": "B", "bankname": "UNITED OVERSEAS BANK LIMITED",
             "accnum": "123456", "isauthorisedsignatoryunknown": "N", "isbeneficialownerunknown": "N",
             "isrelatedaccount": "Y", "checkentity": "P", "nationality": "SG", "statusofrelationsip": "AC",
             "entityaccurateasat": "2026-08-17", "totalassetsundermgtcurrency": "SGD", "totalassetsundermgt": "30000.00",
             "outstandingloanscurrency": "SGD", "outstandingloans": "5000.00"},
    FORM_2: {"isaccmaintainedwithri": "Y", "accnum": "SGLO000032", "accdate": "2019-12-12", "accbaldrcr": "1",
             "accbalcurr": "SGD", "accbalamount": "1900000.00", "accownership": "SING", "rowaccountstatus": "A",
             "infoaccurasat": "2024-12-12", "isauthorisedsignatoryunknown": "Y", "isbeneficialownerunknown": "Y",
             "isrelatedaccount": "Y", "checkentity": "B", "legalentitytype": "LC", "mainbusinessactivity": "94920",
             "statusofrelationsip": "AC", "entityaccurateasat": "2021-01-12"},
}
COLUMNS = ("isaccmaintainedwithri isaccheldbankornonbank bankname accnum accdate accbaldrcr accbalcurr accbalamount "
           "accownership rowaccountstatus infoaccurasat isauthorisedsignatoryunknown isbeneficialownerunknown "
           "isrelatedaccount checkentity legalentitytype mainbusinessactivity nationality statusofrelationsip "
           "entityaccurateasat totalassetsundermgtcurrency totalassetsundermgt outstandingloanscurrency "
           "outstandingloans").split()
SELECT = ("SELECT COALESCE(json_agg(row_to_json(t) ORDER BY t.accnum), '[]') FROM (SELECT groupseq::text, "
          + ", ".join(c + "::text" for c in COLUMNS) + f" FROM {TARGET}) t")


def stored(database):
    return [{k: v for k, v in row.items() if v is not None and k != "groupseq"} for row in json.loads(database.sql(SELECT))]


def test_the_model_declares_the_second_table_from_the_form_without_personal_identifiers():
    tables = {t["name"]: t for t in declared(load_workspace(DATASET))}
    assert list(tables) == ["STR_Transaction", TABLE]
    table = tables[TABLE]
    assert table["key"] == ["SourceRevision", "GroupSeq"]
    assert table["template"]["row_paths"] == ["xfa:data/STR[1]/AccountInformation[1]/Account",
                                              "xfa:data/STR[1]/EntityProfile[1]/Entity"]
    names = {c["name"].lower() for c in table["columns"]}
    assert set(COLUMNS) <= names
    for left_out in ("idno", "idnotype", "firstname", "lastname", "legalname", "emailaddress", "sex", "contactno",
                     "dobrn_year", "block", "street", "postal"):
        assert left_out not in names


def test_template_links_read_both_groups_skip_nested_groups_and_empty_fields():
    workspace = load_workspace(DATASET)
    form = "\n".join(['xfa:data/STR[1]/Header[1]/FormType[1] = "STR"', ACC + 'AccNum[1] = "A1"',
                      ACC + 'AccBalAmount[1] = "9.50"', ACC + 'AccBalCurr[1] = "SGD"', ACC + 'BankName[1] = ""',
                      ACC + 'AccType[1]/Savings[1] = "Y"', ENT + 'Nationality[1] = "SG"',
                      ENT + 'Identification[1]/IdNo[1] = "S1234567D"', ENT + 'FirstName[1] = "X"'])
    chunk = {"chunk_id": "c", "source_id": "s", "source_revision": "b" * 64, "start_offset": 0, "end_offset": len(form),
             "page": 0, "text": form}
    links = [k for k in template_links(workspace, [chunk]) if k["notes"]["table"] == TABLE]
    assert sorted(k["notes"]["column"] for k in links) == ["AccBalAmount", "AccBalCurr", "AccNum", "Nationality"]
    [row] = [r for r in build_rows(workspace, links)]
    got = {c["column"]: (c["status"], c["typed"], c["unit"]) for c in row["cells"]}
    assert got == {"AccNum": ("ok", "A1", None), "AccBalAmount": ("ok", "9.50", "SGD"), "AccBalCurr": ("ok", "SGD", None),
                   "Nationality": ("ok", "SG", None)}   # no BankName cell: the form field is empty
    assert row["table"] == TABLE and row["rowKey"] == "1"


def test_a_group_position_beyond_the_first_is_its_own_row():
    workspace = load_workspace(DATASET)
    form = "\n".join(['xfa:data/STR[1]/Header[1]/FormType[1] = "STR"',
                      'xfa:data/STR[1]/AccountInformation[1]/Account[1]/AccNum[1] = "A1"',
                      'xfa:data/STR[1]/AccountInformation[1]/Account[2]/AccNum[1] = "A2"'])
    chunk = {"chunk_id": "c", "source_id": "s", "source_revision": "b" * 64, "start_offset": 0, "end_offset": len(form),
             "page": 0, "text": form}
    rows = build_rows(workspace, template_links(workspace, [chunk]))
    assert [(r["table"], r["rowKey"]) for r in rows] == [(TABLE, "1"), (TABLE, "2")]


@needs_forms
def test_preview_and_confirm_write_both_tables_for_both_forms_and_a_join_question_answers(tmp_path, monkeypatch):
    _, options = server(tmp_path, monkeypatch)
    with review_database(tmp_path / "state", pg_bin()) as database, running_server(database, **options) as call:
        SemanticStore(database).import_ossie(DATASET, FIN_YAML)
        database.sql(_LOAD_TRACK_DDL)
        for source in (FORM_1, FORM_2):
            read_form(call, source)
            status, preview = act(call, "preview", source)
            assert status == 200 and preview["tables"] == ["STR_Transaction", TABLE]
            [row] = [r for r in preview["rows"] if r["table"] == TABLE]
            assert row["status"] == "previewed" and row["rowKey"] == "1"
            # The admin reads the account number as written (user decision, 2026-11-23, evening); other accounts read it
            # masked (tests_v2/test_identifier_mask.py). Only an admin may preview a write.
            assert {c["column"].lower(): c["typed"] for c in row["cells"] if c["status"] == "ok"} == EXPECTED[source]
            if source == FORM_1:
                assert database.sql(f"SELECT to_regclass('{TARGET}') IS NULL") == "t"  # nothing written before confirm
            assert act(call, "confirm", source)[0] == 200
        assert stored(database) == [EXPECTED[FORM_1], EXPECTED[FORM_2]]      # ordered by account number
        # one Talk to Data question uses both tables: the transaction total, filtered by a column of the account table
        workspace, question = load_workspace(DATASET), "What is the total reported suspicious transaction value in SGD?"
        for flag, total in (("Y", 123454321), ("N", 500)):
            fact = query_metric(workspace, question, database, bind=lambda m, d, v=flag: [
                Filter(field=f"{TABLE}.IsAccMaintainedWithRI", op="=", value=v)])
            assert fact["value"] == total and "JOIN" in fact["sql"] and "str_accountentity" in fact["sql"]
        # confirming again adds no row
        assert act(call, "confirm", FORM_1)[0] == 200 and len(stored(database)) == 2
