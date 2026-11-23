"""Discovered table (23 November 2026): a label-and-value document with no declared template is written to a NEW wide
table whose schema code proposes from the document's own labels. One table per label set; a later file with the same
labels appends a row. No model call, no lakehouse, no CDE: the Spark payload and the Trino statements are only built.

Every value below is made up by the implementer."""
import json
import re

from semantic_pipeline_v2 import trino_target
from semantic_pipeline_v2.ask_actions import Writer
from semantic_pipeline_v2.ask_router import route
from semantic_pipeline_v2.physical_mapping import template_links
from test_ask_actions import form_ctx, form_pg  # noqa: F401  (fixture)
from test_physical_mapping import DATASET
from test_physical_printed import chunks, field_report

PAIRS = [("## Site", None), ("Inspection date", "3 November 2026"), ("Site name", "Example Plant North"),
         ("Inspector count", "4"), ("## Costs", None), ("Repair estimate", "SGD 1,250.50"), ("Labour hours", "12.5"),
         ("Next review", "sometime in December"), ("## Contact", None), ("Name", "Example Contact"),
         ("## Supplier", None), ("Name", "Example Supplier Ltd")]
NOTES = "## Notes\nThe pump was inspected and found worn.\n\n**Important:** All values are synthetic."


def report(pairs=PAIRS, title="# Equipment Inspection Report", **kw):
    """Field passages as ingest stores them: headings as text, one `Label: value` chunk per field, then prose."""
    return chunks([title + "\n\nSYNTHETIC TEST DATA"] + [label if value is None else f"{label}: {value}" for label, value in pairs]
                  + [NOTES], **kw)


def other(pairs=PAIRS, value=lambda v: v):
    return [(label, value(v) if v else v) for label, v in pairs]


def setup(pg, made, extra_links=()):
    c = form_ctx(pg)
    pdfs = [s for s in c.workspace["sources"] if s.get("kind") == "pdf"][:3]
    docs = {pdfs[i]["id"]: build(pdfs[i]["id"]) for i, build in enumerate(made)}
    c.chunks = lambda source, workspace: docs.get(source, [])
    c.links = lambda source, *_: template_links(c.workspace, docs.get(source, [])) + [k for k in extra_links if k["source_id"] == source]
    return c, [p["id"] for p in pdfs], docs


def body(ids, kind="document", **kw):
    return {"dataset": DATASET, "source": {"kind": kind, "sourceId": ids[0], **({"sourceIds": ids} if len(ids) > 1 else {})}, **kw}


def columns(table):
    return {c["name"]: c["type"] for c in table["columns"]}


def test_the_schema_is_proposed_from_the_labels_with_types_a_section_prefix_on_a_clash_and_system_columns(form_pg):
    c, ids, _ = setup(form_pg, [lambda s: report(source=s, revision="1" * 64)])
    status, out = Writer().preview(c, body(ids[:1]))
    assert status == 200, out
    [table] = out["tables"]
    assert re.fullmatch(r"v2_ask\.ext_doc_equipment_inspection_rep_[0-9a-f]{6}", table["name"]), table["name"]
    assert table["proposed"] == table["name"].split(".")[1].removeprefix("ext_") and table["discovered"] is True
    assert table["icebergName"] == "lakehouse.demo_silver." + table["name"].split(".")[1]
    assert columns(table) == {
        "inspection_date": "Date", "site_name": "String", "inspector_count": "Integer", "repair_estimate": "Decimal",
        "repair_estimate_currency": "String", "labour_hours": "Decimal", "next_review": "String",
        "contact_name": "String", "supplier_name": "String",            # the same label in two sections: prefixed
        "v2_source_id": "String", "v2_source_revision": "String", "v2_row_key": "String",
        "v2_written_at": "Timestamp", "v2_principal": "String"}          # no column for the narrative's "Important"
    assert table["rowCount"] == 1
    row = table["sample"][0]
    assert {k: row[k] for k in ("inspection_date", "inspector_count", "repair_estimate", "repair_estimate_currency",
                                "labour_hours", "next_review", "contact_name", "v2_source_id", "v2_row_key")} == {
        "inspection_date": "2026-11-03", "inspector_count": "4", "repair_estimate": "1250.50",
        "repair_estimate_currency": "SGD", "labour_hours": "12.5", "next_review": "sometime in December",
        "contact_name": "Example Contact", "v2_source_id": ids[0], "v2_row_key": "1"}
    assert any("proposed from the document's own fields" in w for w in out["warnings"])


def test_a_value_that_fails_its_type_stays_text_and_a_leading_zero_is_not_a_number(form_pg):
    bad = other(value=lambda v: {"3 November 2026": "1 October 2026 to 31 October 2026", "SGD 1,250.50": "SGD a lot",
                                 "4": "0042"}.get(v, v))
    c, ids, _ = setup(form_pg, [lambda s: report(bad, source=s)])
    _, out = Writer().preview(c, body(ids[:1]))
    types = columns(out["tables"][0])
    assert (types["inspection_date"], types["repair_estimate"], types["inspector_count"]) == ("String", "String", "String")
    assert "repair_estimate_currency" not in types
    row = out["tables"][0]["sample"][0]
    assert (row["inspection_date"], row["repair_estimate"], row["inspector_count"]) == \
        ("1 October 2026 to 31 October 2026", "SGD a lot", "0042")


def test_the_same_label_set_gives_the_same_table_and_another_set_or_layout_a_different_one(form_pg):
    c, ids, _ = setup(form_pg, [lambda s: report(source=s, revision="1" * 64),
                                lambda s: report(other(value=lambda v: v + "9" if v[-1].isdigit() else v), source=s, revision="2" * 64),
                                lambda s: report(PAIRS[:-3], source=s, revision="3" * 64)])     # the same title, fewer labels
    names = [Writer().preview(c, body([i]))[1]["tables"][0]["name"] for i in ids]
    assert names[0] == names[1] and names[2] != names[0] and names[2].startswith("v2_ask.ext_doc_equipment_inspection_rep_")


def test_two_files_with_the_same_labels_write_one_table_with_two_rows_and_the_group_is_suggested(form_pg):
    c, ids, _ = setup(form_pg, [lambda s: report(source=s, revision="1" * 64),
                                lambda s: report(source=s, revision="2" * 64),
                                lambda s: report(PAIRS[:4], title="# Another Report", source=s)])
    status, one = Writer().preview(c, body(ids[:1]))
    assert status == 200 and [s["sourceId"] for s in one["suggested"]["sources"]] == [ids[1]], one["suggested"]
    status, both = Writer().preview(c, body(ids[:2]))
    assert status == 200, both
    [table] = both["tables"]
    assert table["rowCount"] == 2 and sorted(r["v2_source_id"] for r in table["sample"]) == sorted(ids[:2])
    assert both["suggested"]["sources"] == []
    status, out = Writer().preview(c, body([ids[0], ids[2]]))
    assert status == 409 and "different labelled fields" in out["error"]


def test_an_existing_table_with_the_same_columns_appends_and_with_other_columns_is_refused(form_pg):
    c, ids, _ = setup(form_pg, [lambda s: report(source=s, revision="1" * 64), lambda s: report(source=s, revision="2" * 64),
                                lambda s: report(PAIRS[:4], source=s, revision="3" * 64)])
    writer = Writer()
    _, first = writer.preview(c, body(ids[:1]))
    status, done = writer.confirm(c, {"fingerprint": first["fingerprint"], "approval": first["approval"]})
    assert status == 200 and done["postgres"] == {"written": 1, "found": 1, "expected": 1}, done
    _, second = writer.preview(c, body(ids[1:2]))
    assert second["tables"][0]["name"] == first["tables"][0]["name"]
    _, done = writer.confirm(c, {"fingerprint": second["fingerprint"], "approval": second["approval"]})
    assert done["postgres"]["written"] == 1 and done["snapshots"][0]["total"] == 2
    _, again = writer.preview(c, body(ids[1:2]))                     # a replay adds nothing
    _, done = writer.confirm(c, {"fingerprint": again["fingerprint"], "approval": again["approval"]})
    assert done["postgres"] == {"written": 0, "found": 1, "expected": 1}
    name = first["tables"][0]["proposed"]
    status, out = writer.preview(c, body(ids[2:3], table=name))      # another layout into that table
    assert status == 409 and "already exists with other columns" in out["error"]
    assert "labour_hours" in out["error"] and "supplier_name" in out["error"]


def test_the_trino_statements_create_the_table_once_and_insert_only_missing_keys(form_pg):
    c, ids, _ = setup(form_pg, [lambda s: report(source=s)])
    _, out = Writer().preview(c, body(ids[:1], target="iceberg_cde"))
    payload = json.loads(json.loads(out["spark"]["script"].split("PAYLOAD = json.loads(", 1)[1].split(")\nCATALOG", 1)[0]))
    [table] = payload["tables"]
    assert table["keys"] == ["v2_source_id", "v2_source_revision", "v2_row_key"] and "v2_question" not in json.dumps(table["columns"])
    create, insert, check = trino_target.statements(table)
    target = out["tables"][0]["icebergName"]
    assert create.startswith(f"CREATE TABLE IF NOT EXISTS {target} (") and "inspection_date date" in create
    assert "repair_estimate decimal(38,6)" in create and f"WHERE NOT EXISTS (SELECT 1 FROM {target} t" in insert
    assert "coalesce(CAST(t.v2_source_id AS varchar), chr(0))" in insert and "site_name AS" not in insert.split("WHERE", 1)[1]


def test_to_a_table_keeps_a_declared_match_and_falls_back_only_with_three_fields(form_pg):
    c, ids, _ = setup(form_pg, [lambda s: field_report(source=s), lambda s: report(source=s),
                                lambda s: chunks(["# Short Note", "Reference: X-1", "Owner: Example Owner"], source=s)])
    status, declared = Writer().preview(c, body(ids[:1], kind="form"))
    assert status == 200 and [t["name"].split(".")[-1] for t in declared["tables"]] == ["str_transaction", "str_accountentity"]
    assert not any(t.get("discovered") for t in declared["tables"])
    assert not any("own fields" in w for w in declared["warnings"])
    status, fallback = Writer().preview(c, body(ids[1:2], kind="form"))
    assert status == 200 and fallback["tables"][0]["discovered"] is True
    assert any(w.startswith("No declared model table matches this document.") for w in fallback["warnings"])
    status, out = Writer().preview(c, body(ids[2:3], kind="form"))
    assert status == 404 and out["error"] == "This document has no rows of a predefined table"
    status, out = Writer().preview(c, body(ids[2:3]))
    assert status == 404 and "fewer than three labelled fields" in out["error"]


def test_a_reader_is_refused(form_pg):
    c, ids, _ = setup(form_pg, [lambda s: report(source=s)])
    c.principal = {**c.principal, "role": "reader"}
    assert Writer().preview(c, body(ids[:1]))[0] == 403


def test_a_definition_id_is_recorded_only_on_a_column_whose_label_a_link_quotes(form_pg):
    site = "Site name: Example Plant North"
    holder = {}

    def make(s):
        holder[s] = report(source=s)
        return holder[s]
    c, ids, docs = setup(form_pg, [make])
    chunk = next(k for k in docs[ids[0]] if k["text"] == site)
    narrative = docs[ids[0]][-1]
    link = lambda ch, definition, quote: {"source_id": ids[0], "chunk_id": ch["chunk_id"], "relation": "states_value_of",
                                          "definition_id": definition, "stated_value": "x", "status": "proposed",
                                          "notes": {"quote": quote}, "span": {"text": ch["text"], "start": 0, "end": 1}}
    extra = [link(chunk, "fs:definition:m/field/Site/SiteName", site),
             link(narrative, "fs:definition:m/field/Site/Remark", "The pump was inspected")]
    c.links = lambda source, *_: extra if source == ids[0] else []
    _, out = Writer().preview(c, body(ids[:1]))
    defined = {col["name"]: col.get("definition") for col in out["tables"][0]["columns"] if col.get("definition")}
    assert defined == {"site_name": "fs:definition:m/field/Site/SiteName"}


SOURCES = [{"id": "a", "title": "inspection_north.pdf", "kind": "pdf"}, {"id": "b", "title": "inspection_south.pdf", "kind": "pdf"}]


def test_the_router_chooses_the_discovered_table_for_new_table_phrases():
    for text in ("Write inspection_north to a new table", "Create a new table from inspection_north",
                 "Make a table from inspection_north's fields"):
        assert route(text, {"forms": SOURCES}) == {"tool": "write", "args": {
            "source": {"kind": "document", "sourceId": "a"}, "target": "postgres"}}, text
    assert route("Write inspection_north to a table", {"forms": SOURCES})["args"]["source"] == {"kind": "form", "sourceId": "a"}
    assert route("Write it to a new table", {"forms": SOURCES, "lastForm": "b"})["args"]["source"] == {"kind": "document", "sourceId": "b"}
    assert route("store the answer in a new table", {"forms": SOURCES, "lastAnswer": "h1"})["args"]["source"] == \
        {"kind": "answer", "handle": "h1"}


def test_an_identifier_stays_text_even_when_it_is_all_digits():
    """An account number is not a quantity: as a number it loses leading zeros and escapes the identifier mask."""
    from semantic_pipeline_v2.ask_actions import _infer
    for label in ("Account number", "Customer no", "Customer ID", "STR reference", "Postal code"):
        assert _infer("4111111111111111", label)[0] == "String", label
    assert _infer("4", "Inspector count")[0] == "Integer"
    assert _infer("4", "Number of inspectors")[0] == "Integer"
