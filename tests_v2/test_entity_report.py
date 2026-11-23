"""Entity 360 (2026-11-22): a report on one customer or one document-only entity, computed on request.

Totals come from SQL (the guarded compiler path); passages and links come from Output A, concepts from Output B, each
filtered to what the account may read. The summary is model text that code validates against the cited facts.
The links and the catalog are fixtures injected in place of the server's reads; the SQL runs on the synthetic
projections in `answer_pg`. No provider is contacted: the model is a scripted fake.
"""
import json
from types import SimpleNamespace

import pytest

from semantic_pipeline_v2 import entity_report, workspace_server
from semantic_pipeline_v2.entity_report import compare, validate_summary
from test_workspace import running_server

FIN = "financial-services"
SCHEMA = "v2_facts_financial_services"
F = "financial-services:definition:finance_workbook_demo/field/"
S = "financial-services:source:"
WITHHELD = ("jade-adverse-media", "cleaned_audio", "jade_call")


def key(name, value, how="stated", note=None):
    return {"key": f"{name}={value}", "how": how, **({"note": note} if note else {})}


def link(source, chunk, text, *, definition=None, relation="none", value=None, keys=(), status="proposed"):
    return {"chunk_id": chunk, "source_id": S + source, "source_revision": "r1",
            "span": {"start": 0, "end": len(text), "page": None, "text": text},
            "definition_id": definition and F + definition, "canonical_definition_id": definition and F + definition,
            "relation": relation, "stated_value": value, "unit": None, "period": None,
            "entity_key": "; ".join(k["key"] for k in keys) or "unresolved", "entity_keys": list(keys),
            "interpretation": None, "candidates": [], "scores": {}, "method": "fixture", "model_revision": None,
            "status": status, "actor": "pipeline", "recorded_at": "2026-11-22T00:00:00Z", "notes": {}}


C4 = key("Customers.CustomerID", "CUST00004")
LINKS = [
    # CUST00004: a facility letter that states its segment (the table records another) and its industry
    link("FIN-001", "c4a", "Facility letter for customer CUST00004, segment CBK, industry 6810-RealEstate. NRIC S1234567D.",
         definition="Customers/CustomerSegment", relation="states_value_of", value="CBK", keys=[C4]),
    link("FIN-001", "c4a", "Facility letter for customer CUST00004, segment CBK, industry 6810-RealEstate. NRIC S1234567D.",
         definition="Customers/IndustryCode", relation="states_value_of", value="6810-RealEstate", keys=[C4]),
    link("FIN-001", "c4a", "Facility letter for customer CUST00004, segment CBK, industry 6810-RealEstate. NRIC S1234567D.",
         definition="Customers/RiskRating", relation="states_value_of", value="Low", keys=[C4]),
    link("FIN-001", "c4b", "Repayments are due monthly on account MY-CA-00004-2.",
         keys=[key("Accounts.AccountNumber", "MY-CA-00004-2"), {**C4, "how": "derived", "note": "Accounts -> CustomerID"}]),
    link("FIN-001", "c4c", "Signed for the borrower.", keys=[{**C4, "how": "inherited"}]),
    # a public extraction-only file that (wrongly) carries the key: never attached to a customer
    link("public-singapore-export-review", "pub1", "Exports to Malaysia rose. CUST00004 appears in a caption.",
         definition="Customers/CountryOfOperation", relation="states_value_of", value="MY", keys=[C4]),
    # Jade: the adverse-media report (scoped to Jade in the workspace) and a recorded call that names Jade
    link("jade-adverse-media", "j1", "Adverse Media - JADE LOGISTICS PTE LTD. Party Key: K:CORP0006700. Opaque payment flows of 4,500,000.",
         definition="Customers/CountryOfOperation", relation="is_about", value="SG"),
    link("jade-adverse-media", "j2", "Note for screening: the report is unverified."),
    link("jade_call", "jc1", "This is the relationship manager calling about Jade Logistics PTE Limited about FX hedging."),
    # a call that states a customer ID with no row in the Customers table
    link("cleaned_audio", "a1", "May I have your customer ID, please? Yes, it's CUS48271.",
         definition="CreditCard_Txn/CustomerID", relation="states_value_of", value="CUS48271"),
]
CONCEPTS = [
    {"id": "customer-segment", "label": "Customer segment", "status": "proposed", "definition": "The business segment.",
     "type": "attribute", "evidence": {"chunks": ["c4a"]},
     "links": [{"definition_id": F + "Customers/CustomerSegment", "status": "proposed", "predicate": "exactMatch"}],
     "evidenceDetail": [{"chunkId": "c4a", "sourceId": S + "FIN-001", "quote": "segment CBK"}]},
    {"id": "adverse-media", "label": "Adverse media", "status": "candidate", "definition": "A media allegation.",
     "type": "event", "evidence": {"chunks": ["j1"]}, "links": [],
     "evidenceDetail": [{"chunkId": "j1", "sourceId": S + "jade-adverse-media", "quote": "Opaque payment flows"}]},
    {"id": "unrelated", "label": "Unrelated", "status": "proposed", "definition": "x", "type": "attribute",
     "evidence": {"chunks": ["zz"]}, "links": [], "evidenceDetail": []},
]


class ScriptedChat:
    """A fake model. It reads the facts it was given and writes one valid sentence, one with an invented number and
    one that cites an id it was never given. `fail` makes every call raise as a provider failure would."""

    def __init__(self, fail=False):
        self.fail, self.prompts = fail, []

    def complete(self, system, user):
        self.prompts.append(user)
        if self.fail:
            from semantic_pipeline_v2.interpreter import ChatError
            raise ChatError("The provider returned HTTP 503.")
        body = json.loads(user)
        facts = {f["id"]: f["text"] for f in body["facts"]}
        sentences = []
        risk = next((i for i, t in facts.items() if "Risk rating" in t), None)
        if risk:
            value = facts[risk].rsplit(": ", 1)[1]
            sentences += [{"text": f"The recorded risk rating is {value}.", "cites": [risk]},
                          {"text": "The customer made 999 transactions last year.", "cites": [risk]},
                          {"text": f"The risk rating is {value}.", "cites": ["F999"]}]
        for p in body["passages"]:
            sentences.append({"text": "A document describes the entity.", "cites": [p["id"]]})
            break
        return json.dumps({"sentences": sentences})


@pytest.fixture
def serve(answer_pg, monkeypatch):
    """(call, chat, login): the workspace server on `answer_pg` with the fixture links and catalog."""
    monkeypatch.setattr(workspace_server.WorkspaceRequest, "visible_links",
                        lambda self, workspace, principal, source_id=None: [dict(k) for k in LINKS])
    monkeypatch.setattr(workspace_server.WorkspaceRequest, "catalog_rows",
                        lambda self, workspace, principal: ("cat-1", [dict(c) for c in CONCEPTS], [dict(k) for k in LINKS]))
    chat = ScriptedChat()
    entity_report.CACHE.clear()
    options = dict(ocr=SimpleNamespace(available=False), transcriber=SimpleNamespace(available=False),
                   chunk_embedder=None, mapping_embedder=None, interpreter=SimpleNamespace(chat=chat), catalog_chat=None,
                   iceberg=None)
    with running_server(answer_pg, **options) as call:
        def login(user):
            _, _, headers = call("POST", "/api/session", {"userId": user, "recover": True}, {"Cookie": ""},
                                 include_headers=True)      # a second session: the admin one stays valid
            cookie = "; ".join(v.split(";", 1)[0] for n, v in headers if n == "Set-Cookie")
            return lambda method, path, body=None: call(method, path, body, {"Cookie": cookie})
        yield call, chat, login


def report(call, entity):
    return call("POST", "/api/entity/report", {"dataset": FIN, "entity": entity})


def sql(pg, query):
    return [line.split("|") for line in pg.sql(query).splitlines() if line]


# ---------------------------------------------------------------- search

def test_search_returns_at_most_twenty_with_the_jade_sample_first(serve):
    call, _, _ = serve
    status, out = call("GET", f"/api/entity/search?dataset={FIN}&q=")
    assert status == 200 and len(out["results"]) <= 20 and out["results"][0]["id"] == entity_report.SAMPLE
    status, out = call("GET", f"/api/entity/search?dataset={FIN}&q=CUST")
    assert status == 200 and len(out["results"]) == 20 and out["more"] is True
    assert all(r["kind"] == "customer" and "CUST" in r["id"] for r in out["results"])
    status, out = call("GET", f"/api/entity/search?dataset={FIN}&q=cust00004")
    assert [r["id"] for r in out["results"]] == ["customer:CUST00004"]
    status, out = call("GET", f"/api/entity/search?dataset={FIN}&q=jade")
    assert [r["id"] for r in out["results"]] == ["entity:jade-logistics"]


def test_search_scopes_document_entities_to_what_the_account_may_read(serve):
    call, _, login = serve
    assert [r["id"] for r in call("GET", f"/api/entity/search?dataset={FIN}&q=CUS48271")[1]["results"]] == ["stated:CUS48271"]
    restricted = login("finance_restricted")
    status, out = restricted("GET", f"/api/entity/search?dataset={FIN}&q=CUS48271")
    assert status == 200 and out["results"] == []
    manufacturing = login("manufacturing_reader")
    assert manufacturing("GET", f"/api/entity/search?dataset={FIN}&q=")[0] == 403
    assert call("GET", "/api/entity/search?dataset=manufacturing&q=")[0] == 404
    assert call("GET", f"/api/entity/search?dataset={FIN}")[0] == 400


# ---------------------------------------------------------------- a customer with rows

def test_customer_report_totals_equal_direct_sql(serve, answer_pg):
    call, _, _ = serve
    status, out = report(call, "customer:CUST00004")
    assert status == 200 and out["entity"]["kind"] == "customer" and out["profile"]["rows"] is True
    latest = answer_pg.sql(f"SELECT max(businessdate) FROM {SCHEMA}.customers;")
    assert out["businessDate"] == latest
    [[risk, segment]] = sql(answer_pg, f"SELECT riskrating, customersegment FROM {SCHEMA}.customers "
                                       f"WHERE customerid = 'CUST00004' AND businessdate = '{latest}';")
    fields = {f["column"]: f["value"] for f in out["profile"]["fields"]}
    assert fields["RiskRating"] == risk and fields["CustomerSegment"] == segment
    expected = {}
    for table in ("casa_txn", "creditcard_txn", "investment_txn", "loan_txn", "treasury_fx_txn", "treasury_lc_txn"):
        for currency, n, total in sql(answer_pg, f"SELECT currency, count(DISTINCT transactionid), sum(amount) "
                                                f"FROM {SCHEMA}.{table} WHERE customerid = 'CUST00004' GROUP BY 1;"):
            expected[table, currency] = (int(n), round(float(total), 2))
    got = {(t["table"].lower(), c["currency"]): (c["count"], round(float(c["total"]), 2))
           for t in out["holdings"]["transactions"] for c in t["currencies"]}
    assert got == expected and expected
    for t in out["holdings"]["transactions"]:
        [[n]] = sql(answer_pg, f"SELECT count(DISTINCT transactionid) FROM {SCHEMA}.{t['table'].lower()} WHERE customerid = 'CUST00004';")
        assert t["count"] == int(n) and sum(d["count"] for d in t["days"]) == int(n)
    [[accounts]] = sql(answer_pg, f"SELECT count(DISTINCT accountnumber) FROM {SCHEMA}.accounts "
                                  f"WHERE customerid = 'CUST00004' AND businessdate = (SELECT max(businessdate) FROM {SCHEMA}.accounts);")
    assert out["holdings"]["accounts"]["count"] == int(accounts) == len(out["holdings"]["accounts"]["rows"])
    assert out["profile"]["rm"] and out["profile"]["rm"][0]["RMID"].startswith("RM")


def test_customer_report_links_evidence_concepts_and_comparison(serve, answer_pg):
    call, _, _ = serve
    out = report(call, "customer:CUST00004")[1]
    [[segment, industry]] = sql(answer_pg, f"SELECT customersegment, industrycode FROM {SCHEMA}.customers WHERE customerid = "
                                           f"'CUST00004' AND businessdate = (SELECT max(businessdate) FROM {SCHEMA}.customers);")
    found = {c["definition"].rsplit("/", 1)[1]: c for c in out["comparisons"]}
    assert set(found) == {"CustomerSegment", "IndustryCode", "RiskRating"}
    assert found["RiskRating"]["recorded"] == "Medium" and found["RiskRating"]["result"] == "differ"
    for column, recorded, stated in (("CustomerSegment", segment, "CBK"), ("IndustryCode", industry, "6810-RealEstate")):
        assert found[column]["recorded"] == recorded and found[column]["stated"] == stated
        assert found[column]["result"] == ("agree" if recorded.casefold() == stated.casefold() else "differ")
    chunks = {i["chunkId"]: i for g in out["evidence"]["groups"] for i in g["items"]}
    assert set(chunks) == {"c4a", "c4b", "c4c"}                   # never the public extraction-only file
    assert chunks["c4b"]["how"] == "derived" and chunks["c4c"]["how"] == "inherited" and chunks["c4a"]["how"] == "stated"
    assert "S1234567D" not in json.dumps(out)                    # NRIC masked
    assert {c["id"] for c in out["concepts"]} == {"customer-segment"}
    by_table = {row["table"]: row["count"] for row in out["evidence"]["byTable"]}
    assert by_table == {"Customers": 3, "No field": 2}
    assert out["summary"]["available"] and out["summary"]["label"] == "Generated summary, for review"
    texts = [s["text"] for s in out["summary"]["sentences"]]
    assert any("risk rating" in t for t in texts) and not any("999" in t for t in texts) and out["summary"]["dropped"] == 2
    assert all(s["cites"] for s in out["summary"]["sentences"])


def test_a_second_request_is_served_from_the_cache(serve):
    call, chat, _ = serve
    report(call, "customer:CUST00004")
    report(call, "customer:CUST00004")
    assert len(chat.prompts) == 1


# ---------------------------------------------------------------- document-only, unknown

def test_jade_is_document_only_and_says_so(serve):
    call, _, _ = serve
    status, out = report(call, "entity:jade-logistics")
    assert status == 200 and out["entity"]["kind"] == "document" and out["profile"]["rows"] is False
    assert out["holdings"] is None and out["comparisons"] == []
    assert any("known from documents only" in m for m in out["missing"])
    assert any("not linked" in n for n in out["notes"])
    hows = {i["chunkId"]: i["how"] for g in out["evidence"]["groups"] for i in g["items"]}
    assert hows == {"j1": "scoped", "j2": "scoped", "jc1": "named"}
    j1 = next(i for g in out["evidence"]["groups"] for i in g["items"] if i["chunkId"] == "j1")
    assert j1["relation"] == "is_about" and j1["statedValue"] is None      # "is about" states no value
    assert "demo" not in out["entity"]["description"].lower() and "sample" not in json.dumps(out).lower()
    assert {c["id"] for c in out["concepts"]} == {"adverse-media"}


def test_a_stated_id_and_a_similar_customer_id_stay_separate(serve):
    call, _, login = serve
    stated = report(call, "stated:CUS48271")[1]          # the call states CUS48271; the Customers table holds CUS048271
    assert stated["entity"]["kind"] == "document" and any("CUS048271" in n and "not linked" in n for n in stated["notes"])
    assert {i["chunkId"] for g in stated["evidence"]["groups"] for i in g["items"]} == {"a1"}
    customer = report(call, "customer:CUS048271")[1]
    assert any("CUS48271" in n and "not linked" in n for n in customer["notes"])
    assert customer["evidence"]["groups"] == []
    restricted = login("finance_restricted")
    assert "CUS48271" not in json.dumps(restricted("POST", "/api/entity/report", {"dataset": FIN, "entity": "customer:CUS048271"})[1])


def test_unknown_entities_are_404(serve):
    call, _, _ = serve
    for entity in ("customer:CUST99999", "entity:nobody", "stated:NOPE1", "CUST00004", "customer:'; DROP TABLE x"):
        assert report(call, entity)[0] == 404, entity
    assert call("POST", "/api/entity/report", {"dataset": FIN})[0] == 400


# ---------------------------------------------------------------- restricted reader

def test_restricted_reader_gets_nothing_from_withheld_sources(serve):
    call, chat, login = serve
    restricted = login("finance_restricted")
    status, out = restricted("POST", "/api/entity/report", {"dataset": FIN, "entity": "entity:jade-logistics"})
    assert status == 200
    text = json.dumps(out)
    for word in ("JADE LOGISTICS PTE", "CORP0006700", "Opaque", "4,500,000", "unverified", "FX hedging",
                 "Adverse media", "adverse-media report", "jade_call", "jade-adverse-media", "cleaned_audio", "CUS48271"):
        assert word not in text, word
    assert out["evidence"]["groups"] == [] and out["concepts"] == [] and out["evidence"]["byTable"] == []
    for prompt in chat.prompts:
        assert "Opaque" not in prompt and "FX hedging" not in prompt
    assert restricted("POST", "/api/entity/report", {"dataset": FIN, "entity": "stated:CUS48271"})[0] == 404
    status, c4 = restricted("POST", "/api/entity/report", {"dataset": FIN, "entity": "customer:CUST00004"})
    assert status == 200 and {i["chunkId"] for g in c4["evidence"]["groups"] for i in g["items"]} == {"c4a", "c4b", "c4c"}


# ---------------------------------------------------------------- summary and comparison, in code

def test_model_failure_leaves_the_rest_of_the_report(serve):
    call, chat, _ = serve
    chat.fail = True
    status, out = report(call, "customer:CUST00004")
    assert status == 200 and out["summary"]["available"] is False and out["summary"]["sentences"] == []
    assert out["profile"]["rows"] and out["holdings"]["transactions"] and out["evidence"]["groups"]


def test_validation_drops_uncited_numbers_and_names():
    facts = {"F1": "Customers › Risk rating: Medium", "F2": "CASA transactions › count: 12"}
    kept, dropped = validate_summary([
        {"text": "The recorded risk rating is Medium.", "cites": ["F1"]},
        {"text": "There are 12 CASA transactions.", "cites": ["F2"]},
        {"text": "There are 13 CASA transactions.", "cites": ["F2"]},
        {"text": "The risk rating is High.", "cites": ["F1"]},
        {"text": "The risk rating is Medium.", "cites": []},
        {"text": "It is Medium.", "cites": ["F7"]},
        {"text": "Account MY-CA-00004-2 is open.", "cites": ["F1"]},
    ], facts)
    assert [s["text"] for s in kept] == ["The recorded risk rating is Medium.", "There are 12 CASA transactions."]
    assert dropped == 5


def test_compare_is_computed_by_code():
    assert compare("CBK", "Priority") == "differ"
    assert compare(" 6810-realestate ", "6810-RealEstate") == "agree"
    assert compare("EUR 60,000", "60000.0") == "agree"
    assert compare("60,000.5", "60000") == "differ"
    assert compare("3rd November 2026", "2026-11-03") == "agree"
    assert compare("almost 900", "879.90") == "not comparable"
    assert compare("CBK", None) == "not comparable"


def test_validation_removes_ids_the_model_put_in_the_text():
    kept, dropped = validate_summary([{"text": "The recorded risk rating is Medium (F1).", "cites": ["F1"]}],
                                     {"F1": "Customers › Risk rating: Medium"})
    assert kept == [{"text": "The recorded risk rating is Medium.", "cites": ["F1"]}] and dropped == 0


def test_fact_text_uses_business_table_names():
    from semantic_pipeline_v2.entity_report import _table_label
    assert [_table_label(t) for t in ("CASA_Txn", "CreditCard_Txn", "Treasury_LC_Txn")] == \
        ["CASA transactions", "Credit card transactions", "Treasury LC transactions"]


def test_a_state_without_the_customer_field_is_refused_not_crashed():
    """2026-11-23: on a state whose definitions are not imported, the lookup raised ValueError and dropped the connection."""
    import pytest
    from semantic_pipeline_v2 import entity_report

    class NoField:
        def guarded(self, *args, **kwargs):
            raise ValueError("Customers/CustomerID is not a field of the model")

    with pytest.raises(entity_report._Refusal) as refused:
        entity_report._customer_ids(NoField(), None)
    assert refused.value.status == 503 and "not loaded" in str(refused.value)
