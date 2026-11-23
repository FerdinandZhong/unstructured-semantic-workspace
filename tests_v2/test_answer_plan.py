"""The typed plan of the answer path (2026-11-22). Each test states one general mechanism with scripted model replies:
plan validation and repair, value and entity linking from data, deterministic status, arithmetic and comparison in
code, field aggregates, party ties of evidence and code-attached citations. No exam question is encoded here."""
import json

import pytest

import semantic_pipeline_v2.answer_path as ap
from semantic_pipeline_v2.evidence_links import EntityKeys
from semantic_pipeline_v2.metric_sql import ClarificationNeeded, Filter, query_field
from semantic_pipeline_v2.workspace import load_workspace

ADMIN = lambda ds: {"id": "a", "role": "admin", "datasets": [ds], "withheld": []}  # noqa: E731


def plan(**kw):
    base = {"measure": None, "aggregate": None, "entities": [], "filters": [], "time": [], "group_by": None,
            "answer": "number", "document": None, "evidence": [], "compare": None, "parties": [], "unsupported": None,
            "time_field": None}
    return {**base, **kw}


class Script:
    """Plan replies for the plan prompt, reading replies for the reading prompt, in order."""

    def __init__(self, plans, reads=()):
        self.plans, self.reads, self.calls = list(plans), list(reads), []

    def complete(self, system, user):
        self.calls.append((system, user))
        queue = self.plans if system == ap.PLAN_SYSTEM else self.reads
        reply = queue.pop(0)
        return reply if isinstance(reply, str) else json.dumps(reply)


class Index:
    """A chunk index over a few in-memory documents: {source_id: text}."""

    def __init__(self, docs):
        self.rows = [{"chunk_id": f"c-{s}", "source_id": s, "text": t, "content_hash": "h"} for s, t in docs.items()]

    def search(self, datasets, query, vector, exclude_sources=(), limit=20):
        return {"rows": [r for r in self.rows if r["source_id"] not in exclude_sources], "mode": "keyword"}

    def read(self, dataset, source_id):
        return [r for r in self.rows if r["source_id"] == source_id]


def run(pg, dataset, question, chat, docs=None, principal=None):
    ws = load_workspace(dataset)
    if docs:
        ws = {**ws, "sources": [*ws["sources"], *({"id": s, "title": s, "kind": "synthetic-document"} for s in docs)]}
    return ap.answer(question, ws, principal or ADMIN(dataset), database=pg, chunks=Index(docs or {}),
                     entity_keys=EntityKeys(ws), chat=chat)


FIN, MFG = "financial-services", "manufacturing"
CASA = "metric/casa_transaction_value"


# ---------------------------------------------------------------- plan validation

def test_a_plan_with_an_id_the_model_does_not_hold_is_repaired_once_then_refused(answer_pg):
    bad = plan(measure="metric/deposit_balance")
    chat = Script([bad, bad])
    result = run(answer_pg, FIN, "What was the deposit balance?", chat)
    assert result["status"] == "refused" and result["refusalReason"] == "not planned"     # 2026-11-22: was "model unavailable"
    assert len(chat.calls) == 2 and "rejected" in chat.calls[1][1] and "deposit_balance" in chat.calls[1][1]
    assert not result["facts"] and result["conclusion"] is None


def test_a_repaired_plan_is_used(answer_pg):
    chat = Script([plan(measure="metric/nope"), plan(measure=CASA, filters=[{"field": "field/CASA_Txn/Currency", "value": "SGD"}],
                                                     time=["D1"])])
    result = run(answer_pg, FIN, "What was the CASA transaction value in SGD on 5 November 2026?", chat)
    assert result["status"] == "answered" and result["conclusion"] == 356152.44


def test_no_model_is_a_refusal_never_an_answer(answer_pg):
    result = run(answer_pg, MFG, "How many units are in stock?", None)
    assert result["status"] == "refused" and not result["facts"] and result["conclusion"] is None


# ---------------------------------------------------------------- value and entity linking from data

def test_a_value_is_linked_to_the_column_values_ignoring_case_and_an_unknown_value_is_a_clarification(answer_pg):
    asked = "What was the CASA transaction value on 5 November 2026?"
    ok = run(answer_pg, FIN, asked, Script([plan(measure=CASA, time=["D1"], filters=[{"field": "field/CASA_Txn/Currency", "value": "sgd"}])]))
    assert ok["facts"][0]["filters"] == {"BusinessDate": "2026-11-05", "Currency": "SGD"}
    unknown = run(answer_pg, FIN, asked, Script([plan(measure=CASA, time=["D1"],
                                                      filters=[{"field": "field/CASA_Txn/Currency", "value": "Singapore dollars"}])]))
    assert unknown["status"] == "clarify" and "SGD" in unknown["reason"] and not unknown["facts"]


def test_the_planner_sees_the_values_of_a_small_column_so_it_can_name_one(answer_pg):
    ws = load_workspace(FIN)
    ctx = ap._Ctx("q", ws, ADMIN(FIN), answer_pg, None, None, EntityKeys(ws), None, None, None)
    rows = {r[0]: r for t in ap._card(ctx)["tables"] for r in t["fields"]}
    assert "SGD" in rows["field/CASA_Txn/Currency"][3]                 # a dimension lists its values
    assert len(rows["field/Customers/CustomerID"]) == 3                 # an identifier never does


def test_a_name_value_becomes_the_key_of_its_one_row(answer_pg):
    chat = Script([plan(measure="metric/inventory_units", filters=[{"field": "field/Production_Product/Name", "value": "touring-1000 yellow, 46"}])])
    result = run(answer_pg, MFG, "How many units of that bike are in stock?", chat)
    assert result["status"] == "answered" and result["facts"][0]["filters"] == {"ProductID": 954} and result["conclusion"] == 148


def test_a_name_matches_by_its_words_in_any_order(answer_pg):
    ws = load_workspace(MFG)
    ctx = ap._Ctx("What lead time applies to the panniers, large touring?", ws, ADMIN(MFG), answer_pg, None, None,
                  EntityKeys(ws), None, None, None)
    found, _ = ap._entities(ctx)
    assert any(e["table"] == "Production_Product" and e["how"] == "named" and e["value"] for e in found)


def test_a_record_named_by_an_identifier_and_its_owner_filters_once(answer_pg):
    # an order line identifies its order; the order is implied by the line, not repeated as a filter
    chat = Script([plan(measure="field/Sales_SalesOrderDetail/OrderQty", aggregate="value", entities=["E1"],
                        filters=[{"field": "field/Sales_SalesOrderDetail/SalesOrderDetailID", "value": "36824"}])])
    result = run(answer_pg, MFG, "What quantity is on line 36824 of sales order 51131?", chat)
    assert result["status"] == "answered" and result["facts"][0]["filters"] == {"SalesOrderDetailID": 36824}
    assert result["conclusion"] == 16


def test_an_ambiguous_name_is_a_clarification_not_a_default(answer_pg):
    ws = load_workspace(MFG)
    ctx = ap._Ctx("q", ws, ADMIN(MFG), answer_pg, None, None, EntityKeys(ws), None, None, None)
    offers = {"entities": [{"id": "E1", "table": "Production_Product", "column": "ProductID", "value": None, "how": "named",
                            "text": "Twin", "ambiguous": ["1", "2"]}], "dates": {}, "years": {}, "numbers": {}}
    with pytest.raises(ClarificationNeeded, match="Give the ProductID"):
        ap._facts(ctx, plan(measure="metric/inventory_units", entities=["E1"]), offers)


# ---------------------------------------------------------------- deterministic status

def test_a_metric_without_its_required_parameters_is_a_clarification_that_names_them(answer_pg):
    result = run(answer_pg, FIN, "What was the total value of CASA transactions?", Script([plan(measure=CASA)]))
    assert result["status"] == "clarify" and "business date" in result["reason"] and "currency" in result["reason"]
    assert result["definitions"] and result["definitions"][0].endswith(CASA) and not result["facts"]


def test_an_additive_total_over_dated_rows_needs_a_period_but_a_count_does_not(answer_pg):
    total = run(answer_pg, MFG, "How many units were ordered?", Script([plan(measure="metric/sales_ordered_units")]))
    assert total["status"] == "clarify" and "period" in total["reason"]
    count = run(answer_pg, MFG, "How many sales orders shipped late?", Script([plan(measure="metric/late_dispatch_orders")]))
    assert count["status"] == "answered" and count["conclusion"] == 0
    stock = run(answer_pg, MFG, "How many units of product 954 are in stock?", Script([plan(measure="metric/inventory_units", entities=["E1"])]))
    assert stock["status"] == "answered"          # no time field: a snapshot total, not a sum over history
    # 2026-11-22: stock with no product is now a clarification (scope_required, test_answer_plan2.py)


def test_a_party_no_record_identifies_is_a_refusal_for_a_recorded_value(answer_pg):
    chat = Script([plan(measure="field/Customers/RiskRating", aggregate="value", parties=["Harbour Bridge Traders"], answer="choice")])
    result = run(answer_pg, FIN, "What is the recorded risk rating of Harbour Bridge Traders?", chat)
    assert result["status"] == "refused" and result["refusalReason"] == "unknown party" and not result["facts"]


def test_a_plan_that_names_an_unsupported_measure_is_refused(answer_pg):
    result = run(answer_pg, MFG, "What was the margin?", Script([plan(unsupported="a profit margin")]))
    assert result["status"] == "refused" and "profit margin" in result["reason"] and result["refusalReason"] == "no supported answer"


def test_a_yes_no_question_without_a_computable_comparison_is_refused(answer_pg):
    chat = Script([plan(measure="field/Sales_SalesOrderHeader/ShipDate", aggregate="value", entities=["E1"], answer="boolean")])
    result = run(answer_pg, MFG, "Was sales order 51131 accepted?", chat)
    assert result["status"] == "refused" and result["conclusion"] is None and not result["facts"]


def test_a_required_document_value_no_clause_states_is_a_refusal_that_names_it(answer_pg):
    docs = {"doc-a": "The supplier ships goods by road."}
    need = {"need": "the acceptance date of the order", "type": "date", "party": None, "required": True}
    chat = Script([plan(evidence=[need], answer="date")], [{"found": [{"need": 1, "clause": None, "value": None}], "supporting": [], "summary": ""}])
    result = run(answer_pg, MFG, "When were the goods shipped by road accepted?", chat, docs)
    assert result["status"] == "refused" and "acceptance date" in result["reason"] and not result["citations"]


# ---------------------------------------------------------------- arithmetic and comparison in code

def test_compute_differences_orders_and_coverage():
    assert ap.compute("difference", 40, 21) == 19
    assert ap.compute(">=", 51, 71) is False and ap.compute(">=", 71, 71) is True
    assert ap.compute("<=", "2024-06-05", "2024-06-17") is True
    assert ap.compute("==", "16", 16) is True
    assert ap.compute("covers", ("2026-11-04", "2026-11-06"), ("2026-10-01", "2026-10-31")) is False
    assert ap.compute("covers", ("2026-01-01", "2026-12-31"), ("2026-10-01", "2026-10-31")) is True
    # 2026-11-22 round 3: a difference of two days is the number of days between them (test_answer_plan3.py)
    assert ap.compute("difference", "2024-01-01", "2024-01-02") == 1
    for bad in (("<", "2024-06-05", 3), ("difference", "2024-01-01", 3), (">", "abc", "def")):
        with pytest.raises(TypeError):
            ap.compute(*bad)


def test_a_number_the_question_states_is_an_operand_and_the_result_is_traceable(answer_pg):
    need = {"need": "the committed quantity of customer 29641", "type": "number", "party": "E1", "required": True}
    docs = {"agreement": "Customer 29641 commits to order at least 71 units in 2024.", "other": "Customer 11000 commits to 9 units."}
    chat = Script([plan(measure="metric/sales_ordered_units", entities=["E1", "E2"], time=["D1"], evidence=[need],
                        compare={"op": "difference", "left": "N1", "right": "fact"})],
                  [{"found": [{"need": 1, "clause": 1, "value": "71 units"}], "supporting": [], "summary": ""}])
    question = "Customer 29641 committed to 71 units of the Touring-1000 Yellow, 46 in 2024. By how many units did its orders fall short?"
    result = run(answer_pg, MFG, question, chat, docs)
    assert result["status"] == "answered" and result["conclusion"] == 71 - 51
    assert result["comparison"]["left"] == {"from": "N1", "value": 71} and result["comparison"]["right"]["from"] == "fact"
    assert result["citations"][0]["source"] == "agreement"         # the clause that carries the operand is attached


def test_a_clause_about_another_party_is_rejected_and_the_need_then_fails(answer_pg):
    need = {"need": "the committed quantity", "type": "number", "party": "E1", "required": True}
    docs = {"other": "Customer 11000 commits to order at least 9 units in 2024."}
    wrong = {"found": [{"need": 1, "clause": 1, "value": "9 units"}], "supporting": [], "summary": ""}
    chat = Script([plan(measure="metric/sales_ordered_units", entities=["E1"], time=["D1"], evidence=[need],
                        compare={"op": ">=", "left": "fact", "right": "V1"}, answer="boolean")], [wrong, wrong])
    result = run(answer_pg, MFG, "Did customer 29641 order its committed minimum in 2024?", chat, docs)
    # 2026-11-22: a clause another party's document holds, given twice, means no document of the party states it
    assert result["status"] == "refused" and "No document of" in result["reason"] and len(chat.calls) == 3


def test_a_value_is_typed_only_when_code_computes_with_it(answer_pg):
    need = {"need": "detection date on the form", "type": "date", "party": None, "required": True}
    docs = {"form": 'DetectionDate[1] = "01/01/2026" was entered on the detection form.'}
    chat = Script([plan(evidence=[need], answer="date")],
                  [{"found": [{"need": 1, "clause": 1, "value": "01/01/2026"}], "supporting": [], "summary": ""}])
    result = run(answer_pg, FIN, "What detection date was entered on the detection form?", chat, docs)
    assert result["status"] == "answered" and result["conclusion"] == "01/01/2026"      # quoted, not parsed
    assert ap._parse("date", "6 November 2026") == "2026-11-06"
    with pytest.raises(ap.PlanError):
        ap._parse("date", "01/01/2026")          # an operand code must compare is never guessed (day or month first)


def test_a_transport_failure_is_retried_once_and_then_refused(answer_pg):
    from semantic_pipeline_v2.interpreter import ChatError

    class Flaky:
        def __init__(self, failures):
            self.failures, self.calls = failures, 0

        def complete(self, system, user):
            self.calls += 1
            if self.calls <= self.failures:
                raise ChatError("The provider did not return a usable reply (URLError).")
            return json.dumps(plan(unsupported="a profit margin"))
    once = Flaky(1)
    assert run(answer_pg, MFG, "What was the margin?", once)["reason"].endswith("profit margin.") and once.calls == 2
    twice = Flaky(2)
    result = run(answer_pg, MFG, "What was the margin?", twice)
    assert result["refusalReason"] == "model unavailable" and twice.calls == 2


def test_a_value_not_copied_from_its_clause_is_rejected(answer_pg):
    need = {"need": "warranty length", "type": "text", "party": None, "required": True}
    docs = {"warranty": "The warranty lasts 24 months from acceptance."}
    invented = {"found": [{"need": 1, "clause": 1, "value": "36 months"}], "supporting": [], "summary": ""}
    chat = Script([plan(evidence=[need], answer="text")], [invented, invented])
    result = run(answer_pg, MFG, "How long does the warranty last from acceptance?", chat, docs)
    assert result["status"] == "refused" and not result["citations"] and result["conclusion"] is None
    assert len(chat.calls) == 3 and "not copied exactly" in result["reason"]
    good = Script([plan(evidence=[need], answer="text")], [{"found": [{"need": 1, "clause": 1, "value": "24 months"}],
                                                            "supporting": [], "summary": "It lasts 36 months."}])
    result = run(answer_pg, MFG, "How long does the warranty last from acceptance?", good, docs)
    assert result["status"] == "answered", result["reason"]
    assert result["conclusion"] == "24 months" and "36" not in result["summary"]


# ---------------------------------------------------------------- field aggregates

def test_field_aggregates_return_one_value_each(answer_pg):
    ws = load_workspace(FIN)
    date = "financial-services:definition:finance_workbook_demo/field/CASA_Txn/TransactionDate"
    low, high = (query_field(ws, date, [], answer_pg, aggregate=a)["value"] for a in ("min", "max"))
    assert low <= high and low.startswith("2026-11")
    amount = "financial-services:definition:finance_workbook_demo/field/CASA_Txn/Amount"
    # 2026-11-22: CASA events never repeat across snapshot dates (checked in the data), so a sum over all dates is allowed;
    # a table that holds the same records on every snapshot date still needs one date
    assert query_field(ws, amount, [Filter(field="Currency", value="SGD")], answer_pg, aggregate="sum")["value"] > 356152.44
    with pytest.raises(ClarificationNeeded, match="one date"):
        query_field(ws, "financial-services:definition:finance_workbook_demo/field/Accounts/AccountNumber", [], answer_pg, aggregate="count")
    total = query_field(ws, amount, [Filter(field="Currency", value="SGD")], answer_pg, ("2026-11-05",), aggregate="sum")
    assert total["value"] == pytest.approx(356152.44)
    count = query_field(ws, "financial-services:definition:finance_workbook_demo/field/CASA_Txn/Currency", [], answer_pg,
                        ("2026-11-05",), aggregate="count")
    assert isinstance(count["value"], int) and count["value"] >= 1


def test_a_range_aggregate_answers_a_coverage_question_with_aggregate_facts(answer_pg):
    need = {"need": "the period the report covers", "type": "period", "party": None, "required": True}
    docs = {"report": "Transaction period 1 October 2026 to 31 October 2026"}
    chat = Script([plan(measure="field/CASA_Txn/TransactionDate", aggregate="range", evidence=[need], answer="boolean",
                        compare={"op": "covers", "left": "fact", "right": "V1"})],
                  [{"found": [{"need": 1, "clause": 1, "value": "1 October 2026 to 31 October 2026"}], "supporting": [], "summary": ""}])
    result = run(answer_pg, FIN, "Does the CASA data cover the period the report describes?", chat, docs)
    assert result["status"] == "answered" and result["conclusion"] is False
    assert [f["filters"] for f in result["facts"]] == [{"aggregate": "min"}, {"aggregate": "max"}]


def test_distinctive_words_of_a_name_match_one_row_and_shared_words_stay_ambiguous(answer_pg):
    ws = load_workspace(MFG)
    keys = EntityKeys(ws)

    def named(question):
        ctx = ap._Ctx(question, ws, ADMIN(MFG), answer_pg, None, None, keys, None, None, None)
        return [e for e in ap._entities(ctx)[0] if e["how"] == "named" and e["table"] == "Purchasing_Vendor"]
    [one] = named("What lead time does Trek Center promise?")             # only one vendor name holds both words
    assert one["value"] and not one.get("ambiguous")
    frames = [e for e in ap._entities(ap._Ctx("How many Touring Frame Yellow parts are in stock?", ws, ADMIN(MFG), answer_pg,
                                              None, None, keys, None, None, None))[0] if e["table"] == "Production_Product"]
    assert frames and all(e.get("ambiguous") and e["value"] is None for e in frames)   # several products: never a default
    common = ap._entities(ap._Ctx("How many Touring Yellow bikes are in stock?", ws, ADMIN(MFG), answer_pg, None, None, keys,
                                  None, None, None))[0]
    assert not any(e["table"] == "Production_Product" for e in common)       # words most names share identify nothing


def test_format_slips_in_a_plan_are_normalized_without_changing_a_choice(answer_pg):
    reply = plan(measure="metric/active_accounts", entities=["E1"], answer=None, time=[],
                 filters=[{"field": "field/Accounts.Status", "op": "=", "value": "Active"},
                          {"field": "field/Accounts/BusinessDate", "value": "D1"}])
    result = run(answer_pg, FIN, "How many active accounts does customer CUST00013 have on 6 November 2026?", Script([reply]))
    assert result["status"] == "answered" and result["conclusion"] == 3
    assert result["facts"][0]["filters"] == {"CustomerID": "CUST00013", "BusinessDate": "2026-11-06"}   # Status is the metric's own


def test_a_document_need_no_computation_uses_does_not_block_a_data_answer(answer_pg):
    need = {"need": "the type the report states", "type": "text", "party": None, "required": True}
    chat = Script([plan(measure="field/Customers/CustomerType", aggregate="value", entities=["E1"], time=["D1"], evidence=[need],
                        answer="choice")], [{"found": [{"need": 1, "clause": None, "value": None}], "supporting": [], "summary": ""}])
    result = run(answer_pg, FIN, "Is customer CUST00008 corporate or individual on 6 November 2026, as the report says?", chat,
                 {"report": "Customer number CUST00008 appears in this report about the customer."})
    assert result["status"] == "answered" and result["conclusion"] == "Corporate"


def test_a_number_after_a_word_that_also_labels_keys_stays_an_offered_number(answer_pg):
    ws = load_workspace(MFG)
    ctx = ap._Ctx("The customer committed to order 40 units in 2024.", ws, ADMIN(MFG), answer_pg, None, None, EntityKeys(ws),
                  None, None, None)
    offers = ap._offers(ctx)
    assert "40" in offers["numbers"].values()


def test_a_document_answer_stands_when_a_side_need_is_not_found(answer_pg):
    needs = [{"need": "warranty length", "type": "text", "party": None, "required": True},
             {"need": "the start date of the warranty", "type": "date", "party": None, "required": True}]
    docs = {"warranty": "The synthetic warranty lasts 24 months from documented customer acceptance."}
    chat = Script([plan(evidence=needs, answer="text")],
                  [{"found": [{"need": 1, "clause": 1, "value": "24 months"}, {"need": 2, "clause": None, "value": None}],
                    "supporting": [], "summary": ""}])
    result = run(answer_pg, MFG, "How long does the synthetic warranty last from acceptance?", chat, docs)
    assert result["status"] == "answered" and result["conclusion"] == "24 months"


def test_a_document_reference_no_table_holds_does_not_scope_the_data(answer_pg):
    need = {"need": "total amount the report states", "type": "number", "party": None, "required": True}
    docs = {"report": "Report SYN-TEST-0042: total amount involved SGD 1,250.00 in this report."}
    chat = Script([plan(measure="metric/reported_suspicious_transaction_value", evidence=[need],
                        filters=[{"field": "field/STR_Transaction/TransactionSeq", "value": "SYN-TEST-0042"}])],
                  [{"found": [{"need": 1, "clause": 1, "value": "SGD 1,250.00"}], "supporting": [], "summary": ""}])
    result = run(answer_pg, FIN, "What total amount does report SYN-TEST-0042 state?", chat, docs)
    assert result["status"] == "answered" and not result["facts"] and result["conclusion"] == "SGD 1,250.00"
