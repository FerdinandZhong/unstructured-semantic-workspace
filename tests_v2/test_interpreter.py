"""P2: interpreter, guards, Output A (evidence links). Plan tests E1 and E2, guards, access, evaluation arm.

Model replies in the server tests come from a scripted client (a test double, like `FixtureLabeler`). The
evaluation test replays recorded live replies from tests_v2/fixtures/interpretations. A prompt that was never
recorded raises; no test supplies a reply for a missing one.
"""

import json
import os
from pathlib import Path
from urllib.parse import urlencode

import pytest

from semantic_pipeline_v2 import workspace_server
from semantic_pipeline_v2.definition_cards import load_cards
from semantic_pipeline_v2.evaluate_mapping import evaluate, interpret_metrics
from semantic_pipeline_v2.evidence_links import (EntityKeys, EvidenceLinks, MAX_LINKED_CHUNKS, build_links,
                                                  max_linked_chunks, overlay, to_jsonl)
from semantic_pipeline_v2.interpreter import (ChatCache,ChatError, ChatMiss, CachedChat,
                                              Interpreter, InterpreterError, PROMPT_VERSION, apply_guards,
                                              prompt_key, validate_reply)
from semantic_pipeline_v2.path1 import Path1Mapper
from semantic_pipeline_v2.workspace import load_workspace, review_database
from test_evidence_labeler import FixtureLabeler
from test_workspace import running_server

FIXTURES = Path(__file__).parent / "fixtures"
DEV = [FIXTURES / "dev" / "path1_dev_finance.jsonl", FIXTURES / "dev" / "path1_dev_manufacturing.jsonl"]
M = "manufacturing:definition:adventureworks_manufacturing_demo/"
F = "financial-services:definition:finance_workbook_demo/"
LEAD = M + "field/Purchasing_ProductVendor/AverageLeadTime"


def reply(**fields):
    """Hand-written reply in the interp-v3 shape: one interpretation and a list with one link."""
    top = fields.pop("interpretation", "The supplier promises receipt within 14 days.")
    link = {"definition_id": LEAD, "relation": "is_about", "stated_value": "14", "unit": "calendar days",
            "period": None, "entity": None, "reason": "A promise about lead time."}
    return json.dumps({"interpretation": top, "links": [{**link, **fields}]})


def no_link(interpretation="Legal boilerplate with no recorded counterpart."):
    return json.dumps({"interpretation": interpretation, "links": []})


class Scripted:
    available = True
    model = "scripted"

    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def complete(self, system, user):
        self.calls.append(user)
        return self.answer(json.loads(user)) if callable(self.answer) else self.answer


def lead_chat(passage_data):
    if "14 calendar days" in passage_data["passage"]:
        return reply()
    return no_link()


def card(ws, ident):
    return load_cards(ws)[ident]


@pytest.fixture(scope="module")
def mfg():
    return load_workspace("manufacturing")


@pytest.fixture(scope="module")
def fin():
    return load_workspace("financial-services")


# ---------------------------------------------------------------- E2: replies are validated, never repaired

OFFERED = {LEAD, M + "field/Purchasing_PurchaseOrderDetail/OrderQty"}


@pytest.mark.parametrize("bad", [
    "not json", "[]", json.dumps({"definition_id": LEAD}),
    reply(definition_id=M + "field/Sales_SalesOrderDetail/UnitPrice"),  # a real definition, but not offered
    reply(relation="exact"), reply(definition_id=None, relation="is_about"), reply(stated_value=14),
    reply(interpretation=None),
    json.dumps({**json.loads(reply()), "extra": 1}), json.dumps({"interpretation": "x", "links": "none"}),
])
def test_malformed_or_out_of_list_replies_are_rejected(bad):
    with pytest.raises(InterpreterError):
        validate_reply(bad, OFFERED)


def test_valid_reply_and_relation_none_with_an_id_is_an_abstention():
    assert validate_reply(reply(), OFFERED)["links"][0]["definition_id"] == LEAD
    assert validate_reply(reply(relation="none"), OFFERED)["links"] == []  # an abstention
    assert validate_reply(no_link(), OFFERED)["links"] == []


def test_recorded_chat_miss_raises_and_key_depends_on_model_and_version(tmp_path):
    cache = ChatCache(tmp_path)
    key = prompt_key("m1", "s", "u")
    assert key != prompt_key("m2", "s", "u") and key != prompt_key("m1", "s", "u2")
    cache.put(key, "stored reply")
    cache.save(model="m1")
    reloaded = ChatCache(tmp_path)
    assert CachedChat(reloaded, model="m1").complete("s", "u") == "stored reply"
    with pytest.raises(ChatMiss):
        CachedChat(reloaded, model="m1").complete("s", "never recorded")
    with pytest.raises(ChatMiss):
        CachedChat(reloaded, model="m2").complete("s", "u")  # another model never recorded this prompt
    manifest = json.loads((tmp_path / "interpretations.json").read_text())
    assert manifest["prompt_version"] == PROMPT_VERSION and manifest["count"] == 1


def test_cached_chat_calls_the_live_client_once_per_prompt(tmp_path):
    class Live:
        model, n = "live", 0

        def complete(self, system, user):
            self.n += 1
            return "r"

    live = Live()
    chat = CachedChat(ChatCache(tmp_path), live)
    assert chat.complete("s", "u") == chat.complete("s", "u") == "r" and live.n == 1 and chat.calls == 1


# ---------------------------------------------------------------- guards

def test_allegation_can_never_state_a_recorded_value(fin):
    rating = card(fin, F + "field/Customers/RiskRating")
    r = {"interpretation": "x", **json.loads(reply(definition_id=F + "field/Customers/RiskRating",
                                                   relation="states_value_of", stated_value="High", unit=None))["links"][0]}
    media = apply_guards(r, "Risk rating: High", rating, source_kind="adverse-media", constrained=True)
    assert media["definition_id"] is None and media["relation"] == "none"
    claim = apply_guards(r, "The firm was allegedly a High risk customer.", rating, constrained=True)
    assert claim["definition_id"] is None
    other = apply_guards({**r, "definition_id": F + "field/Customers/CustomerSegment"},
                         "Reportedly the segment is High", card(fin, F + "field/Customers/CustomerSegment"))
    assert other["relation"] == "is_about" and "allegation_is_not_a_recorded_value" in other["guards"]
    kept = apply_guards(r, "Risk rating: High", rating, constrained=True)
    assert kept["relation"] == "states_value_of" and kept["guards"] == []


def test_contradictory_unit_downgrades_and_matching_unit_stays(mfg):
    lead = card(mfg, LEAD)  # unit: days
    base = {"interpretation": "x", "definition_id": LEAD, "relation": "states_value_of", "stated_value": "14",
            "unit": "SGD", "period": None, "entity": None, "reason": "r"}
    bad = apply_guards(base, "Lead time is 14 SGD.", lead)
    assert bad["relation"] == "is_about" and "stated_unit_contradicts_definition" in bad["guards"]
    good = apply_guards({**base, "unit": "calendar days"}, "Lead time is 14 calendar days.", lead)
    assert good["relation"] == "states_value_of" and good["guards"] == []


def test_stated_value_must_appear_in_the_passage(mfg):
    lead = card(mfg, LEAD)
    base = {"interpretation": "x", "definition_id": LEAD, "relation": "states_value_of", "stated_value": "21",
            "unit": "days", "period": None, "entity": None, "reason": "r"}
    out = apply_guards(base, "Receipt within 14 days.", lead)
    assert out["stated_value"] is None and "stated_value_not_in_passage" in out["guards"]
    assert out["relation"] == "is_about"  # no value left to state
    assert apply_guards({**base, "stated_value": "1,000"}, "ordered 1000 days", lead)["stated_value"] == "1,000"


# ---------------------------------------------------------------- entity keys

def test_entity_key_needs_an_exact_identifier(tmp_path, fin):
    (tmp_path / "Customers.csv").write_text("SourceBatch,BusinessDate,CustomerID\nb,2026-11-04,CUST00013\n")
    keys = EntityKeys(fin, tmp_path)
    assert keys.resolve("Customers", "Customer number\nCUST00013") == "Customers.CustomerID=CUST00013"
    assert keys.resolve("Customers", "CUST00099 is unknown") == "unresolved"
    assert keys.resolve("Customers", "Jade Logistics Pte Ltd") == "unresolved"  # a name never resolves
    assert keys.resolve("Customers", "CUST000130") == "unresolved"  # a prefix match is not exact


def test_numeric_key_needs_its_column_label(tmp_path, mfg):
    (tmp_path / "Purchasing_ProductVendor.csv").write_text("ProductID,BusinessEntityID\n842,1518\n")
    keys = EntityKeys(mfg, tmp_path)
    assert keys.resolve("Purchasing_ProductVendor", "ProductID 842 within 14 days") == (
        "Purchasing_ProductVendor.ProductID=842")
    assert keys.resolve("Purchasing_ProductVendor", "about 842 pieces") == "unresolved"


# ---------------------------------------------------------------- build_links degradation (E2)

def chunk(text, n=0):
    return {"chunk_id": f"c{n}", "source_id": "manufacturing:source:MFG-006", "source_revision": "a" * 64,
            "start_offset": 0, "end_offset": len(text), "page": None, "text": text, "speaker": None}


def run(mfg, interpreter, text="Average lead time: the supplier promises receipt within 14 calendar days.", **options):
    mapper = Path1Mapper(mfg, None)  # hybrid_context without an embedder: lexical candidates on the full passage
    return build_links(mfg, [chunk(text)], mapper, interpreter, EntityKeys(mfg, "/nonexistent"),
                       title="Supply agreement", **options)


def test_selection_carries_value_unit_and_is_about(mfg):
    [link] = run(mfg, Interpreter(Scripted(lead_chat)))
    assert link["definition_id"] == LEAD and link["relation"] == "is_about" and link["status"] == "proposed"
    assert link["stated_value"] == "14" and link["unit"] == "calendar days" and link["entity_key"] == "unresolved"
    assert LEAD in link["candidates"] and len(link["candidates"]) <= 12 and set(link["scores"]) == set(link["candidates"])
    assert link["method"].startswith("interpret:" + PROMPT_VERSION) and link["model_revision"] == "scripted"


def test_no_counterpart_is_stored_without_a_selection(mfg):
    [link] = run(mfg, Interpreter(Scripted(lead_chat)), "Average lead time is not discussed in this clause.")
    assert link["definition_id"] is None and link["relation"] == "none" and link["status"] == "proposed"
    assert link["candidates"] and link["interpretation"]


@pytest.mark.parametrize("answer", [
    "not json", reply(definition_id=M + "field/Sales_SalesOrderDetail/UnitPrice"),
    lambda data: (_ for _ in ()).throw(ChatError("down")),
])
def test_interpreter_failure_degrades_and_keeps_candidates(mfg, answer):
    [link] = run(mfg, Interpreter(Scripted(answer)))
    assert link["status"] == "degraded" and link["definition_id"] is None and link["relation"] == "none"
    assert link["candidates"] and link["notes"]["degraded"]
    with pytest.raises((InterpreterError, ChatError)):
        run(mfg, Interpreter(Scripted(answer)), strict=True)


def test_no_interpreter_degrades_and_a_cache_miss_raises(mfg, tmp_path):
    [link] = run(mfg, None)
    assert link["status"] == "degraded" and link["method"] == "retrieval_only" and link["candidates"]
    with pytest.raises(ChatMiss):
        run(mfg, Interpreter(CachedChat(ChatCache(tmp_path), model="m")))  # never degraded into an answer


# ---------------------------------------------------------------- E1 and access: server

def principal_for(dataset, role="admin", withheld=()):
    return {"id": role, "role": role, "datasets": [dataset], "withheld": list(withheld)}


def pg_bin():
    value = os.environ.get("V2_PG_BIN")
    if not value:
        pytest.skip("set V2_PG_BIN for private PostgreSQL integration tests")
    return Path(value)


def ingest(call, dataset, source):
    return call("POST", "/api/ocr", {"dataset": dataset, "sourceId": source, "mode": "text"})


def test_e1_ingest_stores_links_with_every_contract_field_and_routes_gate_access(tmp_path, monkeypatch):
    dataset, source = "manufacturing", "manufacturing:source:MFG-006"
    principal = principal_for(dataset)
    monkeypatch.setattr(workspace_server.WorkspaceRequest, "principal", lambda _: principal)
    monkeypatch.setattr(workspace_server, "resolve_session", lambda *_: principal)
    labeler = FixtureLabeler(lambda anchors, definitions: {"labels": []})
    query = urlencode({"dataset": dataset, "sourceId": source})
    with review_database(tmp_path / "state", pg_bin()) as database, running_server(
            database, evidence_dir=tmp_path / "evidence", evidence_labeler=labeler, chunk_embedder=None,
            mapping_embedder=None, interpreter=Interpreter(Scripted(lead_chat))) as call:
        assert call("GET", "/api/evidence-links?" + query) == (200, {"links": []})
        status, result = ingest(call, dataset, source)
        assert status == 200 and result["evidenceLinks"]["status"] == "saved" and result["evidenceLinks"]["rows"] >= 1
        assert result["evidenceLinks"]["selected"] == 1 and result["evidenceLinks"]["degraded"] == 0
        status, read = call("GET", "/api/evidence-links?" + query)
        assert status == 200
        links = read["links"]
        assert links and all({"chunk_id", "source_id", "source_revision", "span", "definition_id", "relation",
                              "stated_value", "unit", "period", "entity_key", "interpretation", "candidates",
                              "scores", "method", "model_revision", "status", "actor", "recorded_at"} <= set(k)
                             for k in links)
        [lead] = [k for k in links if k["definition_id"]]
        assert lead["definition_id"] == LEAD and lead["relation"] == "is_about"
        assert lead["stated_value"] == "14" and lead["unit"] == "calendar days" and "14 calendar days" in lead["span"]["text"]
        assert lead["source_id"] == source and lead["source_revision"] and lead["recorded_at"]
        _, chunks = call("GET", "/api/chunks?" + query)
        assert LEAD in {label for c in chunks["chunks"] for label in c["labels"]}  # chunk labels keep selected ids
        # re-ingest is idempotent
        ingest(call, dataset, source)
        assert call("GET", "/api/evidence-links?" + query)[1]["links"][0]["chunk_id"] == links[0]["chunk_id"]
        assert len(call("GET", "/api/evidence-links?" + query)[1]["links"]) == len(links)
        # export of every link of a dataset
        status, body = call("GET", "/api/evidence-links/export?dataset=" + dataset)
        assert status == 200
        exported = [json.loads(line) for line in body.decode().splitlines()]
        assert [k["chunk_id"] for k in exported] == [k["chunk_id"] for k in links]
        # access: same gate as /api/chunks
        principal["withheld"] = [source]
        assert call("GET", "/api/evidence-links?" + query)[0] == 403
        principal["withheld"] = []
        principal["datasets"] = []
        assert call("GET", "/api/evidence-links?" + query)[0] == 403
        assert call("GET", "/api/evidence-links/export?dataset=" + dataset)[0] == 403
        assert call("GET", "/api/evidence-links?dataset=" + dataset)[0] == 400


def test_degraded_ingest_without_an_interpreter_says_so(tmp_path, monkeypatch):
    dataset, source = "manufacturing", "manufacturing:source:MFG-006"
    principal = principal_for(dataset)
    monkeypatch.setattr(workspace_server.WorkspaceRequest, "principal", lambda _: principal)
    monkeypatch.setattr(workspace_server, "resolve_session", lambda *_: principal)
    labeler = FixtureLabeler(lambda anchors, definitions: {"labels": []})
    with review_database(tmp_path / "state", pg_bin()) as database, running_server(
            database, evidence_dir=tmp_path / "evidence", evidence_labeler=labeler, chunk_embedder=None,
            mapping_embedder=None, interpreter=None) as call:
        status, result = ingest(call, dataset, source)
        assert status == 200 and result["evidenceLinks"]["status"] == "saved"
        assert result["evidenceLinks"]["degraded"] == result["evidenceLinks"]["rows"] >= 1
        assert result["evidenceLinks"]["selected"] == 0
        links = call("GET", "/api/evidence-links?" + urlencode({"dataset": dataset, "sourceId": source}))[1]["links"]
        assert all(k["status"] == "degraded" and k["definition_id"] is None and k["candidates"] for k in links)


def test_links_route_and_export_mask_nric(tmp_path, monkeypatch):
    dataset, source = "financial-services", "financial-services:source:jade-adverse-media"
    principal = principal_for(dataset)
    monkeypatch.setattr(workspace_server.WorkspaceRequest, "principal", lambda _: principal)
    monkeypatch.setattr(workspace_server, "resolve_session", lambda *_: principal)
    link = {"chunk_id": "c1", "source_id": source, "source_revision": "b" * 64,
            "span": {"start": 0, "end": 9, "page": None, "text": "NRIC S1234567D"}, "definition_id": None,
            "relation": "none", "stated_value": None, "unit": None, "period": None, "entity_key": "unresolved",
            "interpretation": "NRIC S1234567D appears.", "candidates": ["a"], "scores": {"a": {}},
            "method": "m", "model_revision": None, "status": "degraded", "actor": "pipeline", "notes": {}}
    with review_database(tmp_path / "state", pg_bin()) as database, running_server(
            database, evidence_dir=tmp_path / "evidence") as call:
        store = EvidenceLinks(database)
        store.replace(dataset, source, [link])
        [stored] = store.read(dataset, source)
        assert "S1234567D" in stored["span"]["text"]  # stored exactly; masked at the response boundary
        query = urlencode({"dataset": dataset, "sourceId": source})
        status, read = call("GET", "/api/evidence-links?" + query)
        assert status == 200 and "S1234567D" not in json.dumps(read) and read["links"][0]["chunk_id"] == "c1"
        status, body = call("GET", "/api/evidence-links/export?dataset=" + dataset)
        assert status == 200 and b"S1234567D" not in body and json.loads(body.decode().splitlines()[0])["chunk_id"] == "c1"
        principal["withheld"] = [source]  # an export never carries a source the account cannot see
        assert call("GET", "/api/evidence-links/export?dataset=" + dataset) == (200, b"")
        assert json.loads(to_jsonl([stored]).splitlines()[0])["chunk_id"] == "c1"
        store.replace(dataset, source, [])
        assert store.read(dataset, source) == []


def test_correction_overlays_status():
    link = {"span": {"text": "Receipt within 14 days of PO acceptance."}, "definition_id": LEAD, "relation": "is_about",
            "stated_value": "14", "unit": "days", "period": None, "entity_key": "unresolved", "status": "proposed",
            "actor": "pipeline", "notes": {}}
    obs = [{"id": "o1", "quote": "Receipt within 14 days"}]
    replaced = {"observationId": "o1", "correctionAction": "replace", "targetId": M + "field/Purchasing_PurchaseOrderDetail/OrderQty",
                "originalTargetId": LEAD, "correctionActor": "admin", "correctionAt": "2026-11-20T00:00:00Z"}
    [corrected] = overlay([link], [replaced], obs)
    assert corrected["status"] == "corrected" and corrected["definition_id"].endswith("OrderQty")
    assert corrected["actor"] == "admin" and corrected["stated_value"] is None
    assert corrected["notes"]["originalDefinitionId"] == LEAD
    [rejected] = overlay([link], [{**replaced, "correctionAction": "remove", "targetId": None}], obs)
    assert rejected["status"] == "rejected" and rejected["definition_id"] == LEAD
    [unrelated] = overlay([link], [{**replaced, "correctionAction": "remove", "targetId": None, "originalTargetId": "other"}], obs)
    assert unrelated["status"] == "proposed"
    assert overlay([link], [], obs) == [link]


# ---------------------------------------------------------------- evaluation arm

def test_interpret_arm_runs_offline_on_the_dev_set_from_recorded_replies():
    report = evaluate(DEV, "interpret", FIXTURES / "embeddings", chat_cache_dir=FIXTURES / "interpretations")
    assert report["arm"] == "interpret" and report["overall"]["n"] == 29
    assert report["model"] and report["prompt_version"] == PROMPT_VERSION and report["settings"]["k"] == 12
    for scope in (report["overall"], *report["datasets"].values()):
        for key in ("n", "recall_at_12", "selection_precision", "recall", "abstention_accuracy", "false_link_rate",
                    "relation_accuracy", "stated_value_accuracy", "entity_accuracy", "categories",
                    "canonical_precision", "canonical_recall"):
            assert key in scope
    assert report["overall"]["degraded"] == 0


# ---------------------------------------------------------------- P2c: E15 canonical definition

FK = M + "field/Sales_SalesOrderHeader/CustomerID"
PK = M + "field/Sales_Customer/CustomerID"


def test_e15_canonical_follows_declared_relationship_to_the_primary_key(mfg, fin):
    keys = EntityKeys(mfg, "/nonexistent")
    assert keys.canonical_id(FK) == PK and keys.canonical_id(PK) == PK  # the key field is its own canonical id
    assert keys.canonical_id(LEAD) == LEAD  # no relationship: its own canonical id
    assert keys.canonical_id(M + "metric/sales_ordered_units") == M + "metric/sales_ordered_units"
    assert EntityKeys(fin, "/nonexistent").canonical_id(F + "field/Accounts/CustomerID") == F + "field/Customers/CustomerID"


def test_e15_link_keeps_the_selected_id_adds_the_canonical_id_and_resolves_on_the_canonical_table(mfg, tmp_path):
    (tmp_path / "Sales_Customer.csv").write_text("CustomerID,PersonID\n11000,13531\n")
    chat = Scripted(reply(definition_id=FK, relation="states_value_of", stated_value="11000", unit=None))
    mapper = Path1Mapper(mfg, None)
    text = "Customer ID 11000 placed order SO43659 for 500 bikes."
    [link] = build_links(mfg, [chunk(text)], mapper, Interpreter(chat), EntityKeys(mfg, tmp_path), title="Order")
    assert link["definition_id"] == FK and link["canonical_definition_id"] == PK
    assert link["entity_key"] == "Sales_Customer.CustomerID=11000"


def test_e15_canonical_metrics_sit_beside_the_strict_ones():
    gold = {"expected": [PK], "expected_canonical": [PK], "relation": "exact"}
    pred = {"decision": "selected", "selected": FK, "canonical": PK, "relation": "states_value_of",
            "candidates": [{"id": FK, "retrievalRank": 1}], "degraded": False, "stated_value": None,
            "unit": None, "period": None, "entity_key": "unresolved"}
    out = interpret_metrics([(gold, pred)])
    assert out["selection_precision"] == 0.0 and out["recall"] == 0.0  # strict numbers do not change
    assert out["canonical_precision"] == 1.0 and out["canonical_recall"] == 1.0


# ---------------------------------------------------------------- P2c: E16 several links per chunk

OPQ = M + "field/Purchasing_PurchaseOrderDetail/OrderQty"


def item(ident, **fields):
    return {"definition_id": ident, "relation": "is_about", "stated_value": None, "unit": None, "period": None,
            "entity": None, "reason": "r", **fields}


def multi(*items, interpretation="Two facts."):
    return json.dumps({"interpretation": interpretation, "links": list(items)})


def test_e16_bad_items_are_dropped_and_noted_good_ones_kept():
    bad_id = item(M + "field/Sales_SalesOrderDetail/UnitPrice")  # real, but not offered
    out = validate_reply(multi(item(LEAD, stated_value="14"), bad_id, item(OPQ, relation="exact")), OFFERED)
    assert [k["definition_id"] for k in out["links"]] == [LEAD] and len(out["dropped"]) == 2
    out = validate_reply(multi(item(LEAD), item(LEAD, reason="again"), item(OPQ)), OFFERED)
    assert [k["definition_id"] for k in out["links"]] == [LEAD, OPQ] and "duplicate" in " ".join(out["dropped"])
    with pytest.raises(InterpreterError):  # nothing usable at all
        validate_reply(multi(bad_id, item(OPQ, relation="exact")), OFFERED)


def test_e16_at_most_three_links_per_chunk():
    ids = [LEAD, OPQ, FK, PK]
    out = validate_reply(multi(*[item(i) for i in ids]), set(ids))
    assert [k["definition_id"] for k in out["links"]] == ids[:3] and "limit" in " ".join(out["dropped"])


def test_e16_a_chunk_with_two_facts_has_two_links_and_no_counterpart_has_one_record(mfg):
    chat = Scripted(multi(item(LEAD, stated_value="14", unit="days", relation="states_value_of"),
                          item(OPQ, stated_value="500", relation="states_value_of")))
    text = "Average lead time is 14 days and ordered quantity is 500 units."
    links = run(mfg, Interpreter(chat), text)
    ids = [k["definition_id"] for k in links]
    assert len(links) <= 3 and len(set(ids)) == len(ids)
    assert {k["chunk_id"] for k in links} == {"c0"} and ids == [LEAD, OPQ]
    [none] = run(mfg, Interpreter(Scripted(no_link())), "Nothing in the list applies here.")
    assert none["definition_id"] is None and none["relation"] == "none" and none["status"] == "proposed"


def test_e16_store_keeps_several_links_per_chunk_and_migrates_an_old_table(tmp_path):
    base = {"chunk_id": "c1", "source_id": "s", "source_revision": "b" * 64,
            "span": {"start": 0, "end": 9, "page": None, "text": "t"}, "relation": "is_about", "stated_value": None,
            "unit": None, "period": None, "entity_key": "unresolved", "interpretation": "i", "candidates": ["a"],
            "scores": {"a": {}}, "method": "m", "model_revision": None, "status": "proposed", "actor": "pipeline",
            "notes": {}}
    two = [{**base, "definition_id": LEAD, "canonical_definition_id": LEAD},
           {**base, "definition_id": OPQ, "canonical_definition_id": OPQ}]
    with review_database(tmp_path / "state", pg_bin()) as database:
        database.sql("""CREATE TABLE v2_evidence_links (
            link_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, dataset text NOT NULL, chunk_id text NOT NULL,
            source_id text NOT NULL, source_revision text NOT NULL, span jsonb NOT NULL, definition_id text,
            relation text NOT NULL CHECK (relation IN ('states_value_of', 'is_about', 'none')), stated_value text,
            unit text, period text, entity_key text NOT NULL DEFAULT 'unresolved', interpretation text,
            candidates jsonb NOT NULL, scores jsonb NOT NULL, method text NOT NULL, model_revision text,
            status text NOT NULL CHECK (status IN ('proposed', 'degraded', 'corrected', 'rejected')),
            actor text NOT NULL, notes jsonb NOT NULL DEFAULT '{}'::jsonb, recorded_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (dataset, chunk_id));""")  # the P2 table, before this change
        EvidenceLinks(database)
        store = EvidenceLinks(database)  # a second start is a no-op
        store.replace("d", "s", two)
        got = store.read("d", "s")
        assert [k["definition_id"] for k in got] == [LEAD, OPQ] and got[0]["canonical_definition_id"] == LEAD
        skipped = {**base, "chunk_id": "c2", "definition_id": None, "relation": "none", "status": "not_processed"}
        store.replace("d", "s", [*two, skipped])
        assert [k["status"] for k in store.read("d", "s")][-1] == "not_processed"
        with pytest.raises(Exception):  # the same definition twice in one chunk is refused
            store.replace("d", "s", [two[0], two[0]])


# ---------------------------------------------------------------- P2c: E17 chunk limit

def test_e17_chunks_beyond_the_limit_get_a_not_processed_record(mfg):
    chat = Scripted(lead_chat)
    mapper = Path1Mapper(mfg, None)
    chunks = [chunk(f"Average lead time: 14 calendar days, clause {n}.", n) for n in range(5)]
    links = build_links(mfg, chunks, mapper, Interpreter(chat), EntityKeys(mfg, "/nonexistent"), limit=3)
    assert [k["chunk_id"] for k in links] == [f"c{n}" for n in range(5)]  # no chunk without a record
    assert [k["status"] for k in links[3:]] == ["not_processed"] * 2 and len(chat.calls) == 3
    skipped = links[4]
    assert skipped["relation"] == "none" and skipped["definition_id"] is None and skipped["span"]["text"]
    assert all(k["status"] != "not_processed" for k in links[:3])


def test_e17_limit_is_configurable_and_the_default_covers_the_demo_sources(monkeypatch):
    monkeypatch.delenv("V2_MAX_LINKED_CHUNKS", raising=False)
    assert max_linked_chunks() == MAX_LINKED_CHUNKS >= 200
    monkeypatch.setenv("V2_MAX_LINKED_CHUNKS", "80")
    assert max_linked_chunks() == 80
    monkeypatch.setenv("V2_MAX_LINKED_CHUNKS", "junk")
    assert max_linked_chunks() == MAX_LINKED_CHUNKS
    root = Path(__file__).resolve().parents[1] / "demo_data"
    longest = max(len([ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()])
                  for p in [*(root / "manufacturing" / "documents" / "markdown").glob("*.md"), *(root / "preload").glob("*.md")])
    assert longest < MAX_LINKED_CHUNKS  # a chunk holds at least one non-empty line


# ---------------------------------------------------------------- P2c: E18 stated value, unit, period, entity

def test_e18_unit_and_period_are_separate_from_the_stated_value(mfg):
    lead = card(mfg, LEAD)
    base = {"interpretation": "x", "definition_id": LEAD, "relation": "is_about", "stated_value": "14 calendar days",
            "unit": "calendar days", "period": "Q3 2026", "entity": None, "reason": "r"}
    out = apply_guards(base, "Lead time is 14 calendar days in Q3 2026.", lead)
    assert out["stated_value"] == "14" and out["unit"] == "calendar days" and out["period"] == "Q3 2026"
    assert "unit_split_from_value" in out["guards"]
    out = apply_guards({**base, "stated_value": "SGD 12,000", "unit": "SGD", "period": "Q4"},
                       "Amount SGD 12,000 is due in Q3 2026.", lead)
    assert out["stated_value"] == "12,000" and out["period"] is None and "period_not_in_passage" in out["guards"]


def test_e18_entity_comes_from_the_passage_or_its_context_and_resolves_on_the_canonical_table(mfg, tmp_path):
    (tmp_path / "Sales_Customer.csv").write_text("CustomerID,PersonID\n11000,13531\n")
    keys = EntityKeys(mfg, tmp_path)
    selected = Interpreter(Scripted(reply(definition_id=FK, relation="states_value_of", stated_value="500", unit="units")))
    mapper = Path1Mapper(mfg, None)
    from_context = build_links(mfg, [chunk("Customer ID of the sales order: 500 units.")], mapper, selected, keys,
                               title="Customer ID 11000 order confirmation")
    from_name = build_links(mfg, [chunk("Customer ID of the sales order: 500 units.")], mapper, selected, keys,
                            title="Contoso Bikes order confirmation")
    for [link], want in ((from_context, "Sales_Customer.CustomerID=11000"), (from_name, "unresolved")):
        assert link["definition_id"] == FK and link["entity_key"] == want


# ---------------------------------------------------------------- P2c follow-up: key fields and canonical limit

VENDOR = M + "field/Purchasing_Vendor/BusinessEntityID"
CUSTOMER_KEY_TEXT = "Customer ID in scope: customer:11000"


def key_link(mfg, tmp_path, text, *, ident=PK, values="11000", **fields):
    (tmp_path / "Sales_Customer.csv").write_text("CustomerID,PersonID\n" + "".join(f"{v},1\n" for v in values.split()))
    chat = Scripted(reply(definition_id=ident, relation=fields.pop("relation", "is_about"), stated_value=None,
                          unit=None, **fields))
    [link] = build_links(mfg, [chunk(text)], Path1Mapper(mfg, None), Interpreter(chat),
                         EntityKeys(mfg, tmp_path), title="Scope")
    return link


def test_canonical_stops_at_a_single_column_primary_key_of_its_own_table(mfg):
    keys = EntityKeys(mfg, "/nonexistent")
    assert keys.canonical_id(VENDOR) == VENDOR  # the whole primary key of its own table
    assert keys.canonical_id(PK) == PK and keys.canonical_id(FK) == PK  # a non-key foreign key still follows


def test_key_field_gets_its_value_from_the_passage(mfg, tmp_path):
    link = key_link(mfg, tmp_path, CUSTOMER_KEY_TEXT)
    assert link["definition_id"] == PK and link["relation"] == "states_value_of" and link["stated_value"] == "11000"
    assert link["entity_key"] == "Sales_Customer.CustomerID=11000" and "key_value_in_passage" in link["notes"]["guards"]


def test_key_rule_needs_exactly_one_existing_value(mfg, tmp_path):
    unknown = key_link(mfg, tmp_path, "Customer ID in scope: customer:99999")
    assert unknown["relation"] == "is_about" and unknown["stated_value"] is None and unknown["entity_key"] == "unresolved"
    two = key_link(mfg, tmp_path, "Customer ID in scope: customer:11000 and customer:11001", values="11000 11001")
    assert two["relation"] == "is_about" and two["stated_value"] is None
    # the chunk still states both identifiers (P2d); only the link's value rule needs exactly one
    assert two["entity_key"] == "Sales_Customer.CustomerID=11000; Sales_Customer.CustomerID=11001"


def test_key_rule_does_not_apply_to_a_non_key_field_or_override_an_allegation(mfg, tmp_path):
    other = key_link(mfg, tmp_path, "Average lead time for customer:11000 is long", ident=LEAD)
    assert other["stated_value"] is None and "key_value_in_passage" not in other["notes"]["guards"]
    claim = key_link(mfg, tmp_path, "Customer ID allegedly in scope: customer:11000", relation="states_value_of")
    assert claim["relation"] == "is_about" and "allegation_is_not_a_recorded_value" in claim["notes"]["guards"]
    assert claim["stated_value"] is None and "key_value_in_passage" not in claim["notes"]["guards"]


# ---------------------------------------------------------------- P2d: E19 entity keys per chunk

CUST, ORDER = "Sales_Customer.CustomerID", "Sales_SalesOrderHeader.SalesOrderID"


def data_dir(tmp_path):
    (tmp_path / "Sales_Customer.csv").write_text("CustomerID,PersonID,StoreID\n11000,1,2\n11001,3,4\n")
    (tmp_path / "Sales_SalesOrderHeader.csv").write_text("SalesOrderID,CustomerID\n51131,11000\n51132,11001\n")
    return tmp_path


def keyed(mfg, tmp_path, texts, **options):
    """Records for chunks of one source, every reply an abstention (the keys do not depend on the model)."""
    chunks = [chunk(t, n) for n, t in enumerate(texts)]
    return build_links(mfg, chunks, Path1Mapper(mfg, None), Interpreter(Scripted(no_link())),
                       EntityKeys(mfg, data_dir(tmp_path)), title=options.pop("title", "Scope"), **options)


def how(link):
    return {k["key"]: k["how"] for k in link["entity_keys"]}


def test_e19_labelled_forms_resolve_and_bare_numbers_and_names_do_not(mfg, tmp_path):
    for text in ("Entity scope: customer:11000", "Customer ID 11000 is in scope", "customer 11000 asked", "Customer ID: 11000"):
        [link] = keyed(mfg, tmp_path, [text])
        assert how(link) == {CUST + "=11000": "stated"}, text
    for text in ("Ordered 11000 units", "Contoso Bikes Pte Ltd asked", "order 99999 is unknown"):
        [link] = keyed(mfg, tmp_path, [text])
        assert link["entity_keys"] == [] and link["entity_key"] == "unresolved", text


def test_e19_a_record_without_a_definition_still_carries_its_keys_and_the_text_column_matches(mfg, tmp_path):
    [link] = keyed(mfg, tmp_path, ["customer:11000"])
    assert link["definition_id"] is None and link["relation"] == "none"
    assert link["entity_key"] == CUST + "=11000"


def test_e19_an_order_gives_its_customer_and_a_stated_key_is_not_derived_twice(mfg, tmp_path):
    [link] = keyed(mfg, tmp_path, ["Sales Order ID 51131 was shipped late"])
    assert how(link) == {ORDER + "=51131": "stated", CUST + "=11000": "derived"}
    derived = next(k for k in link["entity_keys"] if k["how"] == "derived")
    assert "CustomerID" in derived["note"] and "51131" in derived["note"]
    [both] = keyed(mfg, tmp_path, ["Sales Order ID 51131 for customer:11000"])
    assert how(both) == {ORDER + "=51131": "stated", CUST + "=11000": "stated"}


def test_e19_an_account_gives_its_customer(fin, tmp_path):
    (tmp_path / "Accounts.csv").write_text("SourceBatch,BusinessDate,AccountNumber,CustomerID\nb,2026-11-04,1001234,CUST00013\n")
    (tmp_path / "Customers.csv").write_text("SourceBatch,BusinessDate,CustomerID\nb,2026-11-04,CUST00013\n")
    c = {**chunk("Account Number 1001234 was opened in 2024."), "source_id": "financial-services:source:x"}
    [link] = build_links(fin, [c], Path1Mapper(fin, None), Interpreter(Scripted(no_link())), EntityKeys(fin, tmp_path))
    assert how(link) == {"Accounts.AccountNumber=1001234": "stated", "Customers.CustomerID=CUST00013": "derived"}
    [mixed] = build_links(fin, [{**c, "text": "Customer CUST00013 is a long-standing client."}], Path1Mapper(fin, None),
                          Interpreter(Scripted(no_link())), EntityKeys(fin, tmp_path))
    assert how(mixed) == {"Customers.CustomerID=CUST00013": "stated"}  # a mixed id needs no label


def test_e19_the_single_subject_of_a_document_is_inherited(mfg, tmp_path):
    links = keyed(mfg, tmp_path, ["Customer ID 11000 framework agreement", "Payment is due in 30 days.",
                                  "Customer ID 11000 amends the schedule.", "A name, Contoso, appears here."])
    assert [how(k) for k in links] == [{CUST + "=11000": "stated"}, {CUST + "=11000": "inherited"},
                                       {CUST + "=11000": "stated"}, {CUST + "=11000": "inherited"}]
    assert links[1]["entity_key"] == CUST + "=11000"
    # an order of that customer gives its customer (derived), so the chunk keeps one customer key
    links = keyed(mfg, tmp_path, ["Customer ID 11000 framework agreement", "Sales Order ID 51131 is covered."])
    assert how(links[1]) == {ORDER + "=51131": "stated", CUST + "=11000": "derived"}
    assert how(links[0]) == {CUST + "=11000": "stated", ORDER + "=51131": "inherited"}  # one order key in the document: per table


def test_e19_two_subjects_in_a_document_inherit_nothing(mfg, tmp_path):
    links = keyed(mfg, tmp_path, ["Customer ID 11000 framework agreement", "Payment is due in 30 days.",
                                  "customer:11001 also signs."])
    assert [k["entity_keys"] for k in links][1] == [] and links[1]["entity_key"] == "unresolved"


def test_e19_chunks_past_the_limit_also_carry_keys(mfg, tmp_path):
    links = keyed(mfg, tmp_path, ["customer:11000 header", "late clause", "Sales order 51131"], limit=1)
    assert [k["status"] for k in links] == ["proposed", "not_processed", "not_processed"]
    assert how(links[1])[CUST + "=11000"] == "inherited" and how(links[2])[ORDER + "=51131"] == "stated"


def test_e19_value_scorer_compares_identifiers_not_text():
    gold = {"expected": [], "relation": "none", "entity": "customer:29641"}
    pred = {"decision": "abstain", "selected": None, "relation": "none", "candidates": [], "degraded": False,
            "stated_value": None, "unit": None, "period": None, "entity_key": "Sales_Customer.CustomerID=29641",
            "entity_keys": [{"key": "Sales_Customer.CustomerID=29641", "how": "stated"}]}
    out = interpret_metrics([(gold, pred), ({**gold, "entity": "CUST1"}, pred), ({**gold, "entity": None}, {**pred, "entity_keys": [], "entity_key": "unresolved"})])
    assert out["entity_value_accuracy"] == round(2 / 3, 4) and out["entity_accuracy"] == round(1 / 3, 4)
    assert interpret_metrics([({**gold, "entity": "K:CORP1"}, {**pred, "entity_keys": [{"key": "Customers.CustomerID=CORP1", "how": "stated"}]})])["entity_value_accuracy"] == 1.0


def test_e19_store_round_trips_entity_keys_and_the_overlay_keeps_them(tmp_path):
    keys = [{"key": CUST + "=11000", "how": "inherited"}]
    base = {"chunk_id": "c1", "source_id": "s", "source_revision": "b" * 64,
            "span": {"start": 0, "end": 9, "page": None, "text": "t"}, "relation": "none", "stated_value": None,
            "unit": None, "period": None, "entity_key": CUST + "=11000", "entity_keys": keys, "interpretation": "i",
            "candidates": ["a"], "scores": {"a": {}}, "method": "m", "model_revision": None, "status": "proposed",
            "actor": "pipeline", "notes": {}, "definition_id": None, "canonical_definition_id": None}
    with review_database(tmp_path / "state", pg_bin()) as database:
        database.sql("""CREATE TABLE v2_evidence_links (
            link_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, dataset text NOT NULL, chunk_id text NOT NULL,
            source_id text NOT NULL, source_revision text NOT NULL, span jsonb NOT NULL, definition_id text,
            canonical_definition_id text, relation text NOT NULL, stated_value text, unit text, period text,
            entity_key text NOT NULL DEFAULT 'unresolved', interpretation text, candidates jsonb NOT NULL,
            scores jsonb NOT NULL, method text NOT NULL, model_revision text, status text NOT NULL,
            actor text NOT NULL, notes jsonb NOT NULL DEFAULT '{}'::jsonb, recorded_at timestamptz NOT NULL DEFAULT now());""")
        EvidenceLinks(database)  # migrates the P2c table
        store = EvidenceLinks(database)
        store.replace("d", "s", [base])
        assert store.read("d", "s")[0]["entity_keys"] == keys
    link = {**base, "span": {"text": "Receipt within 14 days"}, "definition_id": LEAD, "relation": "is_about"}
    fix = {"observationId": "o1", "correctionAction": "replace", "targetId": OPQ, "originalTargetId": LEAD}
    [out] = overlay([link], [fix], [{"id": "o1", "quote": "Receipt within 14 days"}])
    assert out["entity_keys"] == keys and out["status"] == "corrected"
