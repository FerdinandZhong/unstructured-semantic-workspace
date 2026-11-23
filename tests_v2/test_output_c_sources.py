"""Output C decisions of 23 November 2026: the Iceberg schema lakehouse.demo_silver with prefixed table names, a recorded
call as an Output C source (long table ext_call_fact keyed by customer ID), and several sources in one write.

No test contacts CDE, Polaris or Trino."""
import json

from semantic_pipeline_v2 import connections, iceberg_target
from semantic_pipeline_v2.ask_actions import Writer
from semantic_pipeline_v2.ask_router import route
from test_ask_actions import form_ctx, form_pg  # noqa: F401  (fixture)
from semantic_pipeline_v2.physical_mapping import template_links
from test_physical_mapping import CHUNK, DATASET

FIELD = f"{DATASET}:definition:finance_workbook_demo/field/CreditCard_Txn/"
SOURCES = [{"id": "a", "title": "customer-form1.pdf", "kind": "pdf"}, {"id": "b", "title": "customer-form2.pdf", "kind": "pdf"},
           {"id": "c", "title": "customer-call.m4a", "kind": "audio"}]


def test_the_authorized_schema_and_the_table_names_follow_the_silver_schema():
    assert iceberg_target.ALLOWED == connections.ALLOWED == ("lakehouse", "demo_silver")
    assert iceberg_target.table_name("STR_AccountEntity") == "ext_str_account_entity"
    assert iceberg_target.table_name("STR_Transaction") == "ext_str_transaction"


def test_the_router_names_several_forms_or_a_recording():
    one = route("Write customer-form2 to a table", {"forms": SOURCES})
    assert one == {"tool": "write", "args": {"source": {"kind": "form", "sourceId": "b"}, "target": "postgres"}}
    two = route("Write customer-form1 and customer-form2 to a table", {"forms": SOURCES})
    assert two["args"]["source"] == {"kind": "form", "sourceId": "a", "sourceIds": ["a", "b"]}
    call = route("Save customer-call to Iceberg", {"forms": SOURCES})
    assert call == {"tool": "write", "args": {"source": {"kind": "call", "sourceId": "c"}, "target": "iceberg_cde"}}


def link(column, relation, value, status, unit=None, chunk="k1", text="May I have your customer ID?"):
    return {"definition_id": FIELD + column, "relation": relation, "stated_value": value, "unit": unit, "period": None,
            "status": status, "chunk_id": chunk, "span": {"text": text, "start": 0, "end": len(text)}}


def call_ctx(pg, links):
    c = form_ctx(pg)
    audio = next(s for s in c.workspace["sources"] if s.get("kind") == "audio")
    c.links = lambda source, *_: links
    return c, audio


def test_a_recording_writes_one_row_per_approved_stated_value_keyed_by_its_customer_id(form_pg):
    links = [link("CustomerID", "states_value_of", "CUS48271", "corrected"),
             link("CustomerID", "states_value_of", "CUS48271", "corrected", chunk="k2"),
             link("Amount", "states_value_of", "879.90", "corrected", unit="SGD", chunk="k3", text="it was 879.90"),
             link("Amount", "states_value_of", "46.80", "proposed", unit="USD", chunk="k2"),          # not approved
             link("TransactionDate", "is_about", None, "corrected", chunk="k2")]                     # states no value
    c, audio = call_ctx(form_pg, links)
    writer, body = Writer(), {"dataset": DATASET, "source": {"kind": "call", "sourceId": audio["id"]}, "target": "iceberg_cde"}
    status, preview = writer.preview(c, body)
    assert status == 200, preview
    [table] = preview["tables"]
    assert table["name"] == "v2_ask.ext_call_fact" and table["icebergName"] == "lakehouse.demo_silver.ext_call_fact"
    assert table["rowCount"] == 1 and [col["name"] for col in table["columns"]][:4] == ["customer_id", "field", "definition", "value"]
    row = table["sample"][0]
    assert (row["customer_id"], row["definition"], row["value"], row["unit"], row["passage"], row["source_file"]) == \
        ("CUS48271", "CreditCard_Txn.Amount", "879.90", "SGD", "it was 879.90", audio["title"])
    assert any("1 stated value" in w and "not approved" in w for w in preview["warnings"])
    assert "MERGE INTO lakehouse.demo_silver.ext_call_fact" in preview["spark"]["script"]
    status, done = writer.confirm(c, {"fingerprint": preview["fingerprint"], "approval": preview["approval"]})
    assert status == 200 and done["postgres"] == {"written": 1, "found": 1, "expected": 1}, done
    assert form_pg.sql("SELECT customer_id || '|' || value FROM v2_ask.ext_call_fact;").strip() == "CUS48271|879.90"
    _, again = writer.preview(c, body)                                        # a replay adds nothing
    _, done = writer.confirm(c, {"fingerprint": again["fingerprint"], "approval": again["approval"]})
    assert done["postgres"] == {"written": 0, "found": 1, "expected": 1}


def test_a_recording_needs_exactly_one_approved_customer_id_and_a_recording(form_pg):
    two = [link("CustomerID", "states_value_of", "CUS48271", "corrected"), link("CustomerID", "states_value_of", "CUS99999", "corrected"),
           link("Amount", "states_value_of", "879.90", "corrected")]
    c, audio = call_ctx(form_pg, two)
    status, out = Writer().preview(c, {"dataset": DATASET, "source": {"kind": "call", "sourceId": audio["id"]}})
    assert status == 409 and "2 customer IDs" in out["error"]
    c, audio = call_ctx(form_pg, [link("Amount", "states_value_of", "879.90", "corrected")])
    status, out = Writer().preview(c, {"dataset": DATASET, "source": {"kind": "call", "sourceId": audio["id"]}})
    assert status == 409 and "0 customer IDs" in out["error"]
    pdf = next(s for s in c.workspace["sources"] if s.get("kind") == "pdf")
    status, out = Writer().preview(c, {"dataset": DATASET, "source": {"kind": "call", "sourceId": pdf["id"]}})
    assert status == 404 and "not a recording" in out["error"]


def test_several_forms_are_previewed_and_written_together_in_one_job(form_pg):
    c, writer = form_ctx(form_pg), Writer()
    # two different documents: each has its own revision, as two real forms do
    c.links = lambda source, *_: template_links(c.workspace, [{**CHUNK, "source_id": source, "source_revision": ("b" if source == "s1" else "c") * 64}])
    _, single = writer.preview(c, {"dataset": DATASET, "source": {"kind": "form", "sourceId": "s1"}, "target": "iceberg_cde"})
    status, both = writer.preview(c, {"dataset": DATASET, "source": {"kind": "form", "sourceId": "s1", "sourceIds": ["s1", "s2"]},
                                      "target": "iceberg_cde"})
    assert status == 200, both
    assert [t["name"] for t in both["tables"]] == [t["name"] for t in single["tables"]]          # the tables are created once
    assert [t["rowCount"] for t in both["tables"]] == [2 * t["rowCount"] for t in single["tables"]]
    payload = json.loads(json.loads(both["spark"]["script"].split("PAYLOAD = json.loads(", 1)[1].split(")\nCATALOG", 1)[0]))
    assert len(payload["tables"]) == len(single["tables"])
    sources = {tuple(row)[[n.split()[0] for n in t["schema"].split(", ")].index("v2_source_id")] for t in payload["tables"] for row in t["rows"]}
    assert sources == {"s1", "s2"}
    status, done = writer.confirm(c, {"fingerprint": both["fingerprint"], "approval": both["approval"]})
    assert status == 200 and done["postgres"]["written"] == done["postgres"]["expected"] == sum(t["rowCount"] for t in both["tables"])


def test_the_preview_suggests_the_other_sources_whose_evidence_fills_the_same_tables(form_pg):
    """23 November 2026: a hint for grouping. It is derived by code from the evidence links; no model is asked."""
    c, writer = form_ctx(form_pg), Writer()
    first, second, third = [s for s in c.workspace["sources"] if s.get("kind") == "pdf"][:3]
    revision = {first["id"]: "b" * 64, second["id"]: "c" * 64}
    c.links = lambda source, *_: template_links(c.workspace, [{**CHUNK, "source_id": source, "source_revision": revision[source]}]) \
        if source in revision else []                                             # the third PDF fills no table
    status, one = writer.preview(c, {"dataset": DATASET, "source": {"kind": "form", "sourceId": first["id"]}})
    assert status == 200, one
    assert one["suggested"]["sources"] == [{"sourceId": second["id"], "title": second["title"],
                                            "rows": sum(t["rowCount"] for t in one["tables"])}]
    assert "same tables" in one["suggested"]["reason"] and third["id"] not in json.dumps(one["suggested"])
    _, both = writer.preview(c, {"dataset": DATASET, "source": {"kind": "form", "sourceId": first["id"],
                                                                  "sourceIds": [first["id"], second["id"]]}})
    assert both["suggested"]["sources"] == []


def test_a_recording_suggests_only_other_recordings_that_can_be_written(form_pg):
    c = form_ctx(form_pg)
    audio = [s for s in c.workspace["sources"] if s.get("kind") == "audio"]
    assert len(audio) >= 2
    ready = [link("CustomerID", "states_value_of", "CUS48271", "corrected"), link("Amount", "states_value_of", "879.90", "corrected")]
    c.links = lambda source, *_: ready if source in (audio[0]["id"], audio[1]["id"]) else []
    _, out = Writer().preview(c, {"dataset": DATASET, "source": {"kind": "call", "sourceId": audio[0]["id"]}})
    assert [s["sourceId"] for s in out["suggested"]["sources"]] == [audio[1]["id"]]
    c.links = lambda source, *_: ready if source == audio[0]["id"] else [link("Amount", "states_value_of", "1", "corrected")]
    _, out = Writer().preview(c, {"dataset": DATASET, "source": {"kind": "call", "sourceId": audio[0]["id"]}})
    assert out["suggested"]["sources"] == []                                      # the other recording has no approved customer ID


class _Chat:
    """A model stand-in: records what it is sent and returns one reply."""
    def __init__(self, reply):
        self.reply, self.sent = reply, []

    def complete(self, system, user):
        self.sent.append((system, user))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def _two_forms(pg, chat):
    c = form_ctx(pg)
    first, second = [s for s in c.workspace["sources"] if s.get("kind") == "pdf"][:2]
    revision = {first["id"]: "b" * 64, second["id"]: "c" * 64}
    c.links = lambda source, *_: template_links(c.workspace, [{**CHUNK, "source_id": source, "source_revision": revision[source]}]) \
        if source in revision else []
    c.chat = chat
    return c, first, second


def test_the_suggested_group_carries_a_model_written_hint_built_from_the_evidence(form_pg):
    """23 November 2026: the user wants the hint written by the model. Code still decides the group; the model only
    words why, from the file names, the tables and the fields the links fill."""
    chat = _Chat('{"reason": "Both files are suspicious transaction reports that fill the same account and transaction fields."}')
    c, first, second = _two_forms(form_pg, chat)
    _, out = Writer().preview(c, {"dataset": DATASET, "source": {"kind": "form", "sourceId": first["id"]}})
    assert [s["sourceId"] for s in out["suggested"]["sources"]] == [second["id"]]
    assert out["suggested"]["hint"] == {"text": "Both files are suspicious transaction reports that fill the same account and transaction fields.",
                                        "label": "AI-written hint, from the evidence links. Check the preview before you confirm."}
    [(system, user)] = chat.sent
    sent = json.loads(user)
    assert sent["chosen"] == [first["title"]] and sent["suggested"] == [second["title"]]
    assert [t["table"] for t in sent["tables"]] and all(t["fields"] for t in sent["tables"])
    assert "b" * 64 not in user and all(set(t) == {"table", "fields"} for t in sent["tables"])       # names only: no values, no revisions


def test_no_hint_without_a_suggestion_a_model_or_a_usable_reply(form_pg):
    chat = _Chat('{"reason": "x"}')
    c, first, second = _two_forms(form_pg, chat)
    _, both = Writer().preview(c, {"dataset": DATASET, "source": {"kind": "form", "sourceId": first["id"], "sourceIds": [first["id"], second["id"]]}})
    assert both["suggested"]["sources"] == [] and "hint" not in both["suggested"] and chat.sent == []     # nothing to suggest: no call
    for bad in (_Chat(RuntimeError("provider down")), _Chat("not json"), _Chat('{"reason": ""}'), _Chat('{"reason": "' + "long " * 80 + '"}'), None):
        c, first, second = _two_forms(form_pg, bad)
        status, out = Writer().preview(c, {"dataset": DATASET, "source": {"kind": "form", "sourceId": first["id"]}})
        assert status == 200 and len(out["suggested"]["sources"]) == 1 and "hint" not in out["suggested"]     # the group stands
