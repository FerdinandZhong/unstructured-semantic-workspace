"""Answer path round 3 (2026-11-22), from the per-class failures of the broad development set. Each test states one
general mechanism with scripted model replies and values computed here from the demo data; no exam question is encoded.

- a field written by its column name alone resolves to the measure's table, or to the one related table holding it;
- a date condition written as a filter on a date field is the plan's period on that field;
- a plan with an aggregate and a date field but no measure measures that date field;
- an unknown measure id is repaired with the closest tables and the field-aggregate rule;
- a metric whose only gap is its business date, asked with conditions of the question's own and no date at all, is
  read as the field aggregate it is built from; a metric whose fixed condition the question replaces likewise;
- a per-entity metric or an additive total is scoped by any record or value condition, never by a period alone;
- a row count is reported under the field whose condition selects the rows;
- an identifier or a name no row holds is a refusal (unknown party), not a clarification;
- a period wholly outside the data is refused before a metric's one-date rule is applied;
- the conditions a metric fixes itself are not shown as the question's filters."""
import pytest

import semantic_pipeline_v2.answer_path as ap
from test_answer_plan import FIN, MFG, Script, plan, run
from test_answer_plan2 import rows

SALES = "metric/sales_ordered_units"


def test_a_bare_column_name_and_a_date_filter_become_the_period_on_that_field(answer_pg):
    reply = plan(measure=SALES, entities=["E1"], time_field="OrderDate",
                 filters=[{"field": "OrderDate", "op": "between", "value": ["2024-08-01", "2024-08-31"]}])
    result = run(answer_pg, MFG, "How many units did customer 29641 order from 1 August to 31 August 2024?", Script([reply]))
    assert result["status"] == "answered", result["reason"]
    assert result["facts"][0]["filters"] == {"CustomerID": 29641, "OrderDate": {"from": "2024-08-01", "to": "2024-08-31"}}


def test_an_equality_on_a_date_field_is_the_period_it_names(answer_pg):
    reply = plan(measure="metric/credit_card_transaction_value",
                 filters=[{"field": "field/CreditCard_Txn/TransactionDate", "value": "2026-11-04"},
                          {"field": "field/CreditCard_Txn/Currency", "value": "SGD"}])
    result = run(answer_pg, FIN, "What were SGD credit card transactions worth for the 4th of November, 2026?", Script([reply]))
    assert result["status"] == "answered", result["reason"]
    assert result["facts"][0]["filters"] == {"BusinessDate": "2026-11-04", "Currency": "SGD"}


def test_a_plan_with_an_aggregate_and_a_date_field_but_no_measure_measures_the_date(answer_pg):
    reply = plan(aggregate="value", time_field="field/Sales_SalesOrderHeader/OrderDate", answer="date",
                 filters=[{"field": "field/Sales_SalesOrderHeader/SalesOrderID", "value": 51131}])
    result = run(answer_pg, MFG, "On what date was sales order 51131 placed?", Script([reply]))
    want = next(r for r in rows_mfg("Sales_SalesOrderHeader") if r["SalesOrderID"] == "51131")["OrderDate"][:10]
    assert result["status"] == "answered" and result["conclusion"] == want, result["reason"]
    assert result["facts"][0]["definition"].endswith("field/Sales_SalesOrderHeader/OrderDate")


def test_an_unknown_measure_id_is_repaired_with_the_closest_tables(answer_pg):
    bad = plan(measure="metric/credit_card_transaction_count", filters=[{"field": "field/CreditCard_Txn/Channel", "value": "Mobile"}])
    good = plan(measure="field/CreditCard_Txn/TransactionID", aggregate="count",
                filters=[{"field": "field/CreditCard_Txn/Channel", "value": "Mobile"}])
    chat = Script([bad, good])
    result = run(answer_pg, FIN, "How many credit card transactions went through the mobile channel?", chat)
    repair = chat.calls[1][1]
    assert "CreditCard_Txn" in repair and "aggregate count" in repair
    want = len({r["TransactionID"] for r in rows("CreditCard_Txn") if r["Channel"] == "Mobile"})
    assert result["status"] == "answered" and result["conclusion"] == want, result["reason"]


# ---------------------------------------------------------------- a dated metric asked without a date

def test_a_plain_dated_metric_with_conditions_of_its_own_and_no_date_is_its_field_aggregate(answer_pg):
    corporate = {r["CustomerID"] for r in rows("Customers") if r["CustomerType"] == "Corporate"}
    want = len({r["TransactionID"] for r in rows("CASA_Txn") if r["CustomerID"] in corporate})
    reply = plan(measure="metric/casa_transaction_count", filters=[{"field": "field/Customers/CustomerType", "value": "Corporate"}])
    result = run(answer_pg, FIN, "How many CASA transactions were made by corporate customers?", Script([reply]))
    assert result["status"] == "answered" and result["conclusion"] == want > 0, result["reason"]
    assert result["facts"][0]["definition"].endswith("field/CASA_Txn/TransactionID")
    assert result["facts"][0]["filters"] == {"CustomerType": "Corporate", "aggregate": "count"}


def test_a_dated_metric_asked_bare_or_for_its_own_condition_still_needs_its_date(answer_pg):
    bare = run(answer_pg, FIN, "How many CASA transactions were recorded?", Script([plan(measure="metric/casa_transaction_count")]))
    assert bare["status"] == "clarify" and "business date" in bare["reason"]
    own = run(answer_pg, FIN, "How many large SGD CASA transactions were recorded?", Script([plan(
        measure="metric/large_casa_transaction_count", filters=[{"field": "field/CASA_Txn/Currency", "value": "SGD"}])]))
    assert own["status"] == "clarify" and "business date" in own["reason"]


def test_a_threshold_that_replaces_a_metrics_fixed_condition_is_a_field_count(answer_pg):
    want = sum(1 for r in rows("CASA_Txn") if r["Currency"] == "SGD" and float(r["Amount"]) > 45000)
    reply = plan(measure="metric/large_casa_transaction_count", filters=[
        {"field": "field/CASA_Txn/Currency", "value": "SGD"}, {"field": "field/CASA_Txn/Amount", "op": "gt", "value": 45000}])
    result = run(answer_pg, FIN, "How many SGD CASA transactions were above 45,000?", Script([reply]))
    assert result["status"] == "answered" and result["conclusion"] == want > 0, result["reason"]
    assert result["facts"][0]["definition"].endswith("field/CASA_Txn/Amount")
    assert result["facts"][0]["filters"] == {"Currency": "SGD", "Amount": {"gt": 45000}, "aggregate": "count"}


def test_a_metric_still_needs_a_required_value_it_lacks_when_the_date_is_given(answer_pg):
    reply = plan(measure="metric/credit_card_transaction_value", time=["D1"])
    result = run(answer_pg, FIN, "What were credit card transactions worth on 4 November 2026?", Script([reply]))
    assert result["status"] == "clarify" and "currency" in result["reason"]


# ---------------------------------------------------------------- scope

def test_a_per_entity_metric_is_scoped_by_a_related_condition_but_not_by_a_period(answer_pg):
    at = run(answer_pg, MFG, "How many units of stock sit in the Finished Goods Storage location?", Script([plan(
        measure="metric/inventory_units", filters=[{"field": "field/Production_Location/Name", "value": "Finished Goods Storage"}])]))
    assert at["status"] == "answered" and at["conclusion"] > 0, at["reason"]
    dated = run(answer_pg, MFG, "How many units were scrapped on work orders started in 2024?", Script([plan(
        measure="metric/production_scrapped_units", time=["D1"])]))
    assert dated["status"] == "clarify" and "product" in dated["reason"]


# ---------------------------------------------------------------- reporting a count

def test_a_row_count_is_reported_under_the_field_whose_condition_selects_the_rows(answer_pg):
    want = sum(1 for r in rows_mfg("Production_Product") if r["Color"] == "Red")
    reply = plan(measure="field/Production_Product/ProductID", aggregate="count",
                 filters=[{"field": "field/Production_Product/Color", "value": "Red"}])
    result = run(answer_pg, MFG, "How many products are red?", Script([reply]))
    assert result["conclusion"] == want > 0 and result["facts"][0]["definition"].endswith("field/Production_Product/Color")
    assert result["facts"][0]["filters"] == {"Color": "Red", "aggregate": "count"}
    dated = run(answer_pg, FIN, "How many investment transactions are dated from 4 to 5 November 2026?", Script([plan(
        measure="field/Investment_Txn/TransactionID", aggregate="count", time=["D1"], time_field="field/Investment_Txn/TransactionDate")]))
    assert dated["facts"][0]["definition"].endswith("field/Investment_Txn/TransactionDate"), dated["reason"]


# ---------------------------------------------------------------- unknown records and the data's range

@pytest.mark.parametrize("dataset, question, reply", [
    (FIN, "What risk rating does customer CUST99999 carry on 6 November 2026?",
     plan(measure="field/Customers/RiskRating", aggregate="value", time=["D1"],
          filters=[{"field": "field/Customers/CustomerID", "value": "CUST99999"}])),
    (MFG, "What is the credit rating of the vendor Harbour Cycle Supply?",
     plan(measure="field/Purchasing_Vendor/CreditRating", aggregate="value",
          filters=[{"field": "field/Purchasing_Vendor/Name", "value": "Harbour Cycle Supply"}])),
])
def test_an_identifier_or_name_no_row_holds_is_an_unknown_party(answer_pg, dataset, question, reply):
    result = run(answer_pg, dataset, question, Script([reply]))
    assert result["status"] == "refused" and result["refusalReason"] == "unknown party", result["reason"]
    assert not result["facts"] and result["conclusion"] is None


def test_a_period_outside_the_data_is_refused_before_the_one_date_rule(answer_pg):
    result = run(answer_pg, FIN, "How many accounts were active between 1 July and 30 September 2026?",
                 Script([plan(measure="metric/active_accounts", time=["D1"])]))
    assert result["status"] == "refused" and result["refusalReason"] == "period outside the data", result["reason"]


def test_a_condition_the_metric_fixes_is_not_shown_as_a_filter(answer_pg):
    result = run(answer_pg, FIN, "How many customers carried a High risk rating on 6 November 2026?",
                 Script([plan(measure="metric/high_risk_customers", time=["D1"])]))
    assert result["status"] == "answered" and result["facts"][0]["filters"] == {"BusinessDate": "2026-11-06"}


def rows_mfg(table):
    import csv
    with open(f"demo_data/manufacturing/data/csv/{table}.csv", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def test_a_compare_operand_that_is_not_a_name_is_repaired_not_a_crash(answer_pg):
    bad = plan(measure="metric/sales_order_count", entities=["E1"], time=["D1"], answer="boolean",
               compare={"op": "==", "left": {"measure": "metric/late_dispatch_orders"}, "right": "N1"})
    chat = Script([bad, bad])
    result = run(answer_pg, MFG, "Did customer 29641 place 3 sales orders in 2024?", chat)
    assert result["status"] == "refused" and result["refusalReason"] == "not planned" and "compare.left" in result["plannerError"]


# ---------------------------------------------------------------- document values

def test_a_difference_of_two_dates_is_a_number_of_days():
    assert ap.compute("difference", "2024-07-15", "2024-07-01") == 14
    assert ap.compute("difference", "2024-07-01", "2024-07-15") == 14


def test_a_number_or_date_reworded_by_the_reader_is_kept_as_the_clause_writes_it(answer_pg):
    need = {"need": "the cap on large transactions", "type": "number", "party": None, "required": True}
    docs = {"letter": "The account may hold no more than 3 transactions above 50,000 in a month."}
    reworded = {"found": [{"need": 1, "clause": 1, "value": "3 transactions over 50,000"}], "supporting": [], "summary": ""}
    chat = Script([plan(evidence=[need])], [reworded])
    result = run(answer_pg, MFG, "What cap applies to large transactions on the account?", chat, docs)
    assert result["status"] == "answered" and result["conclusion"] == "3", result["reason"]
    assert result["citations"][0]["span"] == docs["letter"]
    invented = {"found": [{"need": 1, "clause": 1, "value": "4 transactions"}], "supporting": [], "summary": ""}
    refused = run(answer_pg, MFG, "What cap applies to large transactions on the account?",
                  Script([plan(evidence=[need])], [invented, invented]), docs)
    assert refused["status"] == "refused" and "not copied exactly" in refused["reason"]


def test_a_compare_operand_naming_the_measure_is_the_fact(answer_pg):
    reply = plan(aggregate="value", entities=["E1"], answer="boolean",
                 compare={"op": "<", "left": "field/Sales_SalesOrderHeader.OrderDate", "right": "D1"})
    result = run(answer_pg, MFG, "Is the order date of sales order 51131 earlier than 1 January 2030?", Script([reply]))
    assert result["status"] == "answered" and result["conclusion"] is True, result["reason"]
    assert result["facts"][0]["definition"].endswith("field/Sales_SalesOrderHeader/OrderDate")


def test_a_reworded_date_is_the_clause_words_for_that_one_date():
    clause = "The promised delivery moves from 1 July 2024 to 15 July 2024."
    assert ap._as_written("date", "July 15, 2024", clause) == "15 July 2024"
    assert ap._as_written("date", "July 20, 2024", clause) == "July 20, 2024"      # not in the clause: left to be rejected


# ---------------------------------------------------------------- the answer model client (V2_ANSWER_MODEL)

class _Opener:
    """A transport that records the request body and returns one canned reply."""

    def __init__(self, content):
        self.content, self.sent = content, []

    def open(self, request, timeout):
        import io
        import json as _json
        self.sent.append((_json.loads(request.data), timeout))
        body = _json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": self.content}}]}).encode()
        return type("R", (io.BytesIO,), {"__enter__": lambda s: s, "__exit__": lambda s, *a: None})(body)


def test_a_reasoning_answer_model_reply_of_thinking_and_text_blocks_gives_its_text():
    from semantic_pipeline_v2.interpreter import MistralChat
    opener = _Opener([{"type": "thinking", "thinking": [{"type": "text", "text": "hm"}]}, {"type": "text", "text": '{"a": 1}'}])
    chat = MistralChat("fixture-token", opener=opener).with_model("mistral-large-latest")
    assert chat.complete("s", "u") == '{"a": 1}'
    body, timeout = opener.sent[0]
    assert body["model"] == "mistral-large-latest" and body["max_tokens"] >= 6000 and timeout >= 180


def test_the_default_client_keeps_its_budget_and_plain_text_replies():
    from semantic_pipeline_v2.interpreter import MistralChat
    opener = _Opener('{"a": 1}')
    assert MistralChat("fixture-token", opener=opener).complete("s", "u") == '{"a": 1}'
    assert opener.sent[0][0]["max_tokens"] == 900 and opener.sent[0][1] == 40


# ---------------------------------------------------------------- round 2 (2026-11-22): values and plan shapes

def test_a_value_named_by_its_declared_label_links_to_its_code(answer_pg):
    want = sum(1 for r in rows_mfg("Purchasing_PurchaseOrderHeader") if r["Status"] == "4")
    reply = plan(measure="field/Purchasing_PurchaseOrderHeader/PurchaseOrderID", aggregate="count",
                 filters=[{"field": "field/Purchasing_PurchaseOrderHeader/Status", "value": "complete"}])
    result = run(answer_pg, MFG, "How many purchase orders are complete?", Script([reply]))
    assert result["status"] == "answered" and result["conclusion"] == want > 0, result["reason"]
    assert result["facts"][0]["filters"] == {"Status": 4, "aggregate": "count"}


def test_the_planner_sees_the_labels_of_coded_values(answer_pg):
    from semantic_pipeline_v2.evidence_links import EntityKeys
    from semantic_pipeline_v2.workspace import load_workspace
    ws = load_workspace(MFG)
    ctx = ap._Ctx("q", ws, {"id": "a", "role": "admin", "datasets": [MFG], "withheld": []}, answer_pg, None, None,
                  EntityKeys(ws), None, None, None)
    rows_ = {r[0]: r for t in ap._card(ctx)["tables"] for r in t["fields"]}
    assert {"4": "Complete"}.items() <= rows_["field/Purchasing_PurchaseOrderHeader/Status"][-1].items()


def test_an_aggregate_with_filters_and_no_measure_counts_the_filtered_table(answer_pg):
    want = sum(1 for r in rows_mfg("Production_Product") if r["Color"] == "Red")
    reply = plan(aggregate="count", filters=[{"field": "field/Production_Product/Color", "value": "Red"}])
    result = run(answer_pg, MFG, "How many products are red?", Script([reply]))
    assert result["status"] == "answered" and result["conclusion"] == want, result["reason"]
    assert result["facts"][0]["definition"].endswith("field/Production_Product/Color")


def test_a_value_no_value_of_its_field_but_of_one_related_field_moves_there(answer_pg):
    people = {r["CustomerID"] for r in rows("Customers") if r["CustomerType"] == "Individual"}
    want = len({r["TransactionID"] for r in rows("CASA_Txn") if r["CustomerID"] in people})
    reply = plan(measure="field/CASA_Txn/TransactionID", aggregate="count",
                 filters=[{"field": "field/CASA_Txn/CustomerID", "value": "Individual"}])
    result = run(answer_pg, FIN, "How many CASA transactions were made by individual customers?", Script([reply]))
    assert result["status"] == "answered" and result["conclusion"] == want, result["reason"]
    assert result["facts"][0]["filters"] == {"CustomerType": "Individual", "aggregate": "count"}
    # "Corporate" is both a customer type and a customer segment: a clarification naming both, never a default
    both = plan(measure="field/CASA_Txn/TransactionID", aggregate="count",
                filters=[{"field": "field/CASA_Txn/CustomerID", "value": "Corporate"}])
    asked = run(answer_pg, FIN, "How many CASA transactions were made by corporate customers?", Script([both]))
    assert asked["status"] == "clarify" and "CustomerType" in asked["reason"] and "CustomerSegment" in asked["reason"]


def test_an_unsupported_note_that_names_the_measured_field_is_dropped(answer_pg):
    reply = plan(measure="field/Sales_SalesOrderHeader/Freight", aggregate="sum", time=["D1"],
                 time_field="field/Sales_SalesOrderHeader/OrderDate", unsupported="Freight is not recorded on sales orders")
    result = run(answer_pg, MFG, "What was the total freight on sales orders placed in May 2024?", Script([reply]))
    assert result["status"] == "answered" and result["conclusion"] > 0, result["reason"]
    other = plan(measure="field/Sales_SalesOrderHeader/Freight", aggregate="sum", unsupported="a customer satisfaction score")
    assert run(answer_pg, MFG, "What was the satisfaction score?", Script([other]))["status"] == "refused"


def test_a_metric_per_business_date_asked_over_a_range_of_an_event_table_is_its_field_aggregate(answer_pg):
    txns = rows("CASA_Txn")
    account = next(r["AccountNumber"] for r in txns)
    want = round(sum(float(r["Amount"]) for r in txns if r["AccountNumber"] == account and r["DebitCredit"] == "CR"
                     and "2026-11-04" <= r["TransactionDate"][:10] <= "2026-11-06"), 2)
    reply = plan(measure="metric/casa_transaction_value", entities=["E1"], time=["D1"], time_field="field/CASA_Txn/TransactionDate",
                 filters=[{"field": "field/CASA_Txn/DebitCredit", "value": "CR"}])
    result = run(answer_pg, FIN, f"What credits did account {account} take in from 4 to 6 November 2026?", Script([reply]))
    assert result["status"] == "answered" and result["conclusion"] == pytest.approx(want), result["reason"]
    assert result["facts"][0]["definition"].endswith("field/CASA_Txn/Amount")
    assert result["facts"][0]["filters"]["TransactionDate"] == {"from": "2026-11-04", "to": "2026-11-06"}
    # the metric requires one currency: rows of several currencies are a clarification, never a mixed total
    mixed = plan(measure="metric/casa_transaction_value", time=["D1"], time_field="field/CASA_Txn/TransactionDate",
                 filters=[{"field": "field/CASA_Txn/DebitCredit", "value": "CR"}])
    asked = run(answer_pg, FIN, "What credits came in from 4 to 6 November 2026?", Script([mixed]))
    assert asked["status"] == "clarify" and "currency" in asked["reason"].lower() and not asked["facts"]


def test_an_unsupported_note_beside_a_document_need_lets_the_documents_answer(answer_pg):
    need = {"need": "how often scrap figures are reviewed", "type": "text", "party": None, "required": True}
    docs = {"memo": "The plant manager reviews scrap figures every Monday."}
    found = {"found": [{"need": 1, "clause": 1, "value": "every Monday"}], "supporting": [], "summary": ""}
    reply = plan(evidence=[need], answer="text", unsupported="a review frequency is no metric or field")
    result = run(answer_pg, MFG, "How often does the plant manager review scrap figures?", Script([reply], [found]), docs)
    assert result["status"] == "answered" and result["conclusion"] == "every Monday", result["reason"]
    empty = {"found": [{"need": 1, "clause": None, "value": None}], "supporting": [], "summary": ""}
    refused = run(answer_pg, MFG, "How often does the plant manager review scrap figures?", Script([reply], [empty]), docs)
    assert refused["status"] == "refused" and "review frequency" in refused["reason"]


def test_a_boolean_answer_kind_on_a_question_that_is_not_yes_or_no_takes_the_value(answer_pg):
    need = {"need": "how often scrap figures are reviewed", "type": "text", "party": None, "required": True}
    docs = {"memo": "The plant manager reviews scrap figures every Monday."}
    found = {"found": [{"need": 1, "clause": 1, "value": "every Monday"}], "supporting": [], "summary": ""}
    result = run(answer_pg, MFG, "How often does the plant manager review scrap figures?",
                 Script([plan(evidence=[need], answer="boolean")], [found]), docs)
    assert result["status"] == "answered" and result["conclusion"] == "every Monday", result["reason"]
    yes_no = run(answer_pg, MFG, "Does the plant manager review scrap figures?",
                 Script([plan(evidence=[need], answer="boolean")], [found]), docs)
    assert yes_no["status"] == "refused" and yes_no["conclusion"] is None


# ---------------------------------------------------------------- round 3 (2026-11-22): plan spellings

def test_type_names_of_numbers_texts_and_dates_are_read_as_the_allowed_types(answer_pg):
    need = {"need": "the fee", "type": "Decimal", "party": None, "required": True}
    docs = {"fees": "Each outgoing payment costs 25 dollars."}
    found = {"found": [{"need": 1, "clause": 1, "value": "25 dollars"}], "supporting": [], "summary": ""}
    result = run(answer_pg, MFG, "What does each outgoing payment cost?", Script([plan(evidence=[need], answer="decimal")], [found]), docs)
    assert result["status"] == "answered" and result["conclusion"] == "25 dollars", result["reason"]
    assert result["plan"]["evidence"][0]["type"] == "number" and result["plan"]["answer"] == "number"


def test_date_bounds_and_offered_ids_written_as_filter_values_become_the_period_and_the_value(answer_pg):
    reply = plan(measure="metric/sales_order_count", time=["D1"], filters=[
        {"field": "Sales_SalesOrderHeader.CustomerID", "value": "N1"},
        {"field": "Sales_SalesOrderHeader.OrderDate", "op": "between", "value": ["D1", "D1"]},
        {"field": "Sales_SalesOrderHeader/OrderDate", "op": "gte", "value": "2024-01-01"}])
    result = run(answer_pg, MFG, "How many sales orders did buyer number 29641 place in 2024?", Script([reply]))
    assert result["status"] == "answered", result["reason"]
    assert result["facts"][0]["filters"] == {"CustomerID": 29641, "OrderDate": {"from": "2024-01-01", "to": "2024-12-31"}}


def test_a_foreign_key_value_no_owner_row_holds_is_an_unknown_party_and_one_with_no_rows_is_zero(answer_pg):
    unknown = plan(measure="metric/sales_order_count", time=["D1"],
                   filters=[{"field": "field/Sales_SalesOrderHeader/CustomerID", "value": "99999"}])
    result = run(answer_pg, MFG, "How many sales orders did buyer 99999 place in 2024?", Script([unknown]))
    assert result["status"] == "refused" and result["refusalReason"] == "unknown party", result["reason"]
    customers = {r["CustomerID"] for r in rows_mfg("Sales_Customer")}
    ordering = {r["CustomerID"] for r in rows_mfg("Sales_SalesOrderHeader")}
    idle = sorted(customers - ordering)[0]
    none = plan(measure="metric/sales_order_count", filters=[{"field": "field/Sales_SalesOrderHeader/CustomerID", "value": idle}])
    zero = run(answer_pg, MFG, f"How many sales orders did buyer {idle} place?", Script([none]))
    assert zero["status"] == "answered" and zero["conclusion"] == 0, zero["reason"]


# ---------------------------------------------------------------- withheld amendments (2026-11-22, safety)

def test_a_withheld_clause_that_amends_the_readable_value_for_the_same_record_is_a_withheld_refusal(answer_pg):
    need = {"need": "the promised delivery date", "type": "date", "party": None, "required": True}
    docs = {"schedule": "The promised delivery date for order 51131 is 2024-06-10.",
            "amendment": "The promised delivery date for order 51131 changes from 2024-06-10 to 2024-06-17.",
            "memo": "The warehouse closes at 18:00 on Fridays."}
    found = {"found": [{"need": 1, "clause": 1, "value": "2024-06-10"}], "supporting": [], "summary": ""}
    question = "What promised delivery date applies to order 51131?"
    reader = {"id": "r", "role": "reader", "datasets": [MFG], "withheld": ["amendment"]}
    result = run(answer_pg, MFG, question, Script([plan(evidence=[need], answer="date")], [found]), docs, principal=reader)
    assert result["status"] == "refused" and result["refusalReason"] == "source withheld", result["reason"]
    assert not result["citations"] and result["conclusion"] is None and "17" not in result["reason"]
    other = {"id": "r", "role": "reader", "datasets": [MFG], "withheld": ["memo"]}   # a withheld clause that changes nothing
    kept = run(answer_pg, MFG, question, Script([plan(evidence=[need], answer="date")], [found]), docs, principal=other)
    assert kept["status"] == "answered", kept["reason"]
