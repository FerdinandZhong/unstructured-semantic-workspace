"""A write may name ONE declared table ("... to the transaction table"). User request of 23 November 2026: the
account table of the lakehouse refused inserts, and the transaction rows had to be written on their own."""
from semantic_pipeline_v2.ask_actions import Writer
from semantic_pipeline_v2.ask_router import route
from test_ask_actions import form_pg  # noqa: F401  (fixture)
from test_physical_mapping import DATASET
from test_physical_printed import _pdf_ctx

FORMS = [{"id": "s1", "title": "customer-form3.pdf", "kind": "pdf"}, {"id": "s2", "title": "customer-form4.pdf", "kind": "pdf"},
         {"id": "a1", "title": "customer-call.m4a", "kind": "audio"}]


def source(text):
    return route(text, {"forms": FORMS})["args"]["source"]


def test_the_router_reads_the_one_table_a_request_names():
    assert source("Write customer-form3 and customer-form4 to the transaction table") == {
        "kind": "form", "sourceId": "s1", "sourceIds": ["s1", "s2"], "tableLike": "transaction"}
    assert source("Write customer-form3 to the STR_Transaction table")["tableLike"] == "str_transaction"
    assert source("Save customer-form3 into the account entity table in Iceberg")["tableLike"] == "account entity"
    for text in ("Write customer-form3 to a table", "Write customer-form3 to the table", "Save customer-form3 to the Iceberg table",
                 "Write customer-form3 to the lakehouse table", "Write customer-form3 to a new table",
                 "Save customer-call to the call table"):
        assert "tableLike" not in source(text), text


def test_one_named_table_is_previewed_and_written_alone(form_pg):
    c, first, second, _ = _pdf_ctx(form_pg)
    ids = {"sourceId": first["id"], "sourceIds": [first["id"], second["id"]]}
    writer = Writer()
    status, out = writer.preview(c, {"dataset": DATASET, "source": {"kind": "form", **ids, "tableLike": "transaction"}})
    assert status == 200, out
    assert [(t["name"].split(".")[-1], t["rowCount"]) for t in out["tables"]] == [("str_transaction", 2)]
    assert any("Only STR_Transaction is written" in w for w in out["warnings"])
    status, done = writer.confirm(c, {"fingerprint": out["fingerprint"], "approval": out["approval"]})
    assert status == 200 and [s["table"].split(".")[-1] for s in done["snapshots"]] == ["str_transaction"], done
    status, both = writer.preview(c, {"dataset": DATASET, "source": {"kind": "form", **ids}})
    assert [t["name"].split(".")[-1] for t in both["tables"]] == ["str_transaction", "str_accountentity"]   # unchanged
    assert both["fingerprint"] != out["fingerprint"]


def test_a_table_name_that_matches_no_declared_table_is_refused_by_name(form_pg):
    c, first, _, _ = _pdf_ctx(form_pg)
    status, out = Writer().preview(c, {"dataset": DATASET, "source": {"kind": "form", "sourceId": first["id"], "tableLike": "payments"}})
    assert status == 400 and "payments" in out["error"] and "STR_Transaction" in out["error"], out


def test_the_users_own_wording_is_a_write_to_the_one_table():
    """Reported on 23 November 2026: no article before the table name, and words between the files and the table."""
    typed = "Write customer-form3, customer-form4 extracted transactions to transaction table"
    assert route(typed, {"forms": FORMS}) == {"tool": "write", "args": {"target": "postgres", "source": {
        "kind": "form", "sourceId": "s1", "sourceIds": ["s1", "s2"], "tableLike": "transaction"}}}
    for text, like in (("Write the rows of customer-form3 into transaction table", "transaction"),
                       ("Load customer-form3 and customer-form4 transactions into the transactions table", "transactions"),
                       ("Save customer-form3 to account entity table", "account entity"),
                       ("Write customer-form3 to the right transaction table", "right transaction")):
        assert source(text)["tableLike"] == like, text
    for text in ("Write customer-form3 to Iceberg as a table", "Put customer-form3 in a table", "Write customer-form3 to a new table"):
        assert "tableLike" not in source(text), text
    for text in ("Write a short summary of customer-form3", "What does customer-form3 say about the transaction table?",
                 "Show me the transactions of customer-form3"):
        assert route(text, {"forms": FORMS})["tool"] == "answer", text


def test_a_table_is_matched_by_any_word_that_names_exactly_one_declared_table(form_pg):
    c, first, _, _ = _pdf_ctx(form_pg)
    for like, table in (("transaction", "str_transaction"), ("transactions", "str_transaction"), ("right transaction", "str_transaction"),
                        ("account entity", "str_accountentity"), ("str_transaction", "str_transaction")):
        status, out = Writer().preview(c, {"dataset": DATASET, "source": {"kind": "form", "sourceId": first["id"], "tableLike": like}})
        assert status == 200 and [t["name"].split(".")[-1] for t in out["tables"]] == [table], (like, out)
