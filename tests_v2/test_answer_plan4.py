"""Answer path round 4 (2026-11-22), from the per-class failures of the broad development set and the spent third
exam. Each test states one general mechanism with scripted model replies and values computed here from the demo
data; no exam question is encoded (records and documents are picked or written here).

- plan shapes that change no choice are normalised: a kind in words, a range written as an object, "a and b" of two
  comparisons, difference(a, b) == 0, an entity written as a table name or as its value, a name filter on the wrong
  table (to the one field that holds the value, or to an unknown party when none does);
- a date condition no offered period states gets a repair message that names the offered periods;
- on the answer path, a declared path of two many-to-one hops joins (the generic compiler keeps one hop), a bridge table is crossed for a distinct count, an ambiguous path
  is a clarification; a related table's date field filters a field measure;
- a record the cited clause names (an account, a deal) narrows the measure, also when the unnarrowed measure would
  be a clarification; another party's record never does; a record with no rows in the measure's table is clarified;
- a yes/no answer that leaves a stated condition out is a clarification; several conditions must all hold;
- a valid plan whose entity the measure's table cannot reach, or whose document need is a recorded value, is sent
  back once with advice; a plural name matches its singular."""
import csv
import json

import semantic_pipeline_v2.answer_path as ap
from semantic_pipeline_v2.evidence_links import EntityKeys
from semantic_pipeline_v2.metric_sql import ClarificationNeeded, _declared_path, _list_metric_defs
from semantic_pipeline_v2.workspace import load_workspace
from test_answer_plan import ADMIN, FIN, MFG, Script, plan, run
from test_answer_plan2 import rows
from test_answer_plan3 import rows_mfg

import pytest


def ctx_offers(pg, dataset, question):
    ws = load_workspace(dataset)
    ctx = ap._Ctx(question, ws, ADMIN(dataset), pg, None, None, EntityKeys(ws), None, None, None)
    return ctx, ap._offers(ctx)


def validated(pg, dataset, question, reply):
    ctx, offers = ctx_offers(pg, dataset, question)
    return ap.validate_plan(json.dumps(reply), ctx, offers), offers


def read(*values):
    return {"found": [{"need": i, "clause": 1 if v else None, "value": v} for i, v in enumerate(values, 1)],
            "supporting": [], "summary": ""}


# ---------------------------------------------------------------- plan shapes

def test_a_kind_written_in_words_is_its_allowed_kind(answer_pg):
    reply = plan(answer="currency amount per unit", evidence=[{"need": "price cap", "type": "currency amount per unit",
                                                                "party": None, "required": True}])
    checked, _ = validated(answer_pg, MFG, "What price cap does the memo set?", reply)
    assert checked["answer"] == "number" and checked["evidence"][0]["type"] == "number"


def test_a_range_written_as_an_object_or_words_is_a_pair(answer_pg):
    for value in ({"low": 100, "high": 500}, "100 to 500"):
        reply = plan(measure="field/Purchasing_PurchaseOrderDetail/OrderQty", aggregate="count",
                     filters=[{"field": "field/Purchasing_PurchaseOrderDetail/OrderQty", "op": "between", "value": value}])
        checked, _ = validated(answer_pg, MFG, "How many purchase order lines ordered between 100 and 500 units?", reply)
        assert [str(v) for v in checked["filters"][0]["value"]] == ["100", "500"]


def test_two_comparisons_joined_by_and_are_a_list_and_a_zero_difference_is_equality(answer_pg):
    both = plan(measure="field/Purchasing_ProductVendor/StandardPrice", answer="boolean",
                evidence=[{"need": "a", "type": "number", "party": None, "required": True},
                          {"need": "b", "type": "number", "party": None, "required": True}],
                compare={"op": "&&", "left": {"op": "<=", "left": "fact", "right": "V1"},
                         "right": {"op": ">=", "left": "fact", "right": "V2"}})
    checked, _ = validated(answer_pg, MFG, "Is the price within both limits?", both)
    assert isinstance(checked["compare"], list) and [c["op"] for c in checked["compare"]] == ["<=", ">="]
    zero = plan(answer="boolean", evidence=[{"need": "a", "type": "date", "party": None, "required": True},
                                            {"need": "b", "type": "date", "party": None, "required": True}],
                compare={"op": "==", "left": {"op": "difference", "left": "V1", "right": "V2"}, "right": 0})
    checked, _ = validated(answer_pg, MFG, "Do the two notices give the same date?", zero)
    assert checked["compare"] == {"op": "==", "left": "V1", "right": "V2"}


def test_an_entity_written_as_a_table_name_is_dropped_and_its_value_names_its_id(answer_pg):
    reply = plan(measure="metric/sales_order_count", entities=["Sales_Customer", "29641"])
    checked, offers = validated(answer_pg, MFG, "How many orders has customer 29641 placed?", reply)
    assert checked["entities"] == [next(e["id"] for e in offers["entities"] if e["value"] == "29641")]


def test_a_name_filter_on_the_wrong_table_moves_to_the_field_that_holds_it(answer_pg):
    store = sorted(rows_mfg("Sales_Store"), key=lambda r: r["Name"])[3]["Name"]
    reply = plan(measure="metric/sales_order_count", filters=[{"field": "field/Sales_Customer/Name", "value": store}])
    checked, _ = validated(answer_pg, MFG, f"How many orders has the reseller {store} placed?", reply)
    assert checked["filters"] == [{"field": "field/Sales_Store/Name", "value": store}]


def test_a_name_no_record_holds_is_an_unknown_party_not_an_unplanned_question(answer_pg):
    reply = plan(measure="metric/sales_order_count", entities=["Sales_Customer"],
                 filters={"field": "Sales_Customer.Name", "value": "Quayside Velo Collective"})
    result = run(answer_pg, MFG, "How many orders has the reseller Quayside Velo Collective placed?", Script([reply]))
    assert result["status"] == "refused" and result["refusalReason"] == "unknown party", result["reason"]


def test_a_date_condition_no_offered_period_states_is_repaired_with_the_offered_periods(answer_pg):
    bad = plan(measure="metric/casa_transaction_value", time_field="field/CASA_Txn/TransactionDate",
               filters=[{"field": "field/CASA_Txn/BusinessDate", "op": "between", "value": ["2025-11-09", "2025-11-09"]}])
    chat = Script([bad, bad])
    run(answer_pg, FIN, "What was the CASA transaction value for 5 November 2026?", chat)
    assert "D1" in chat.calls[1][1] and "numbers for between" not in chat.calls[1][1]


# ---------------------------------------------------------------- declared paths

def test_a_condition_two_declared_hops_away_filters_a_field_total(answer_pg):
    vendors = {r["BusinessEntityID"]: r["CreditRating"] for r in rows_mfg("Purchasing_Vendor")}
    heads = {r["PurchaseOrderID"]: r["VendorID"] for r in rows_mfg("Purchasing_PurchaseOrderHeader")}
    want = sum(float(r["OrderQty"]) for r in rows_mfg("Purchasing_PurchaseOrderDetail") if vendors[heads[r["PurchaseOrderID"]]] == "3")
    reply = plan(measure="field/Purchasing_PurchaseOrderDetail/OrderQty", aggregate="sum",
                 filters=[{"field": "field/Purchasing_Vendor/CreditRating", "value": "3"}])
    result = run(answer_pg, MFG, "How many units did we order from vendors with credit rating 3?", Script([reply]))
    assert result["status"] == "answered" and result["conclusion"] == want, result["reason"]
    assert "purchasing_purchaseorderheader" in result["facts"][0]["sql"].lower()


def test_a_distinct_count_crosses_a_bridge_table_and_a_sum_does_not(answer_pg):
    defs = _list_metric_defs(answer_pg, FIN)
    path = _declared_path("Customers", "RelationshipManagers", defs, bridge=True)
    assert [h[1] for h in path] == ["Customer_RM_Relationship", "RelationshipManagers"]
    with pytest.raises(ClarificationNeeded):
        _declared_path("Customers", "RelationshipManagers", defs, bridge=False)
    with pytest.raises(ClarificationNeeded):       # a bridge is crossed, never the end
        _declared_path("Customers", "Customer_RM_Relationship", defs, bridge=True)
    day = "2026-11-05"
    rms = {r["RMID"] for r in rows("RelationshipManagers") if r["BusinessDate"] == day and r["Status"] == "Active"}
    want = len({r["CustomerID"] for r in rows("Customer_RM_Relationship") if r["BusinessDate"] == day and r["RMID"] in rms})
    reply = plan(measure="metric/customers_on_business_date", time=["D1"],
                 filters=[{"field": "field/RelationshipManagers/Status", "value": "Active"}])
    result = run(answer_pg, FIN, "How many customers had an active relationship manager on 5 November 2026?", Script([reply]))
    assert result["status"] == "answered" and result["conclusion"] == want, result["reason"]


def test_a_date_of_a_related_table_filters_a_field_measure(answer_pg):
    line = next(r for r in rows_mfg("Purchasing_PurchaseOrderDetail") if r["PurchaseOrderDetailID"] == "100")
    head = next(r for r in rows_mfg("Purchasing_PurchaseOrderHeader") if r["PurchaseOrderID"] == line["PurchaseOrderID"])
    day = head["OrderDate"][:10]
    reply = plan(measure="field/Purchasing_PurchaseOrderDetail/ReceivedQty", aggregate="value", time=["D1"],
                 time_field="field/Purchasing_PurchaseOrderHeader/OrderDate",
                 filters=[{"field": "field/Purchasing_PurchaseOrderDetail/PurchaseOrderDetailID", "value": "100"}])
    result = run(answer_pg, MFG, f"How many units were received on purchase order line 100 ordered on {day}?", Script([reply]))
    assert result["status"] == "answered" and float(result["conclusion"]) == float(line["ReceivedQty"]), result["reason"]


# ---------------------------------------------------------------- the record a document names

def two_account_customer():
    accounts = {}
    for r in rows("CASA_Txn"):
        accounts.setdefault(r["CustomerID"], {}).setdefault(r["AccountNumber"], []).append(r)
    customer = sorted(c for c, a in accounts.items() if len(a) == 2 and len({x["Currency"] for v in a.values() for x in v}) == 2)[2]
    return customer, accounts[customer]


def test_the_account_a_cited_clause_names_narrows_the_total(answer_pg):
    customer, accounts = two_account_customer()
    account = sorted(accounts)[1]
    want = round(sum(float(r["Amount"]) for r in accounts[account] if r["DebitCredit"] == "CR"), 2)
    doc = {"memo": f"The review memo expects credit turnover in account {account} of at least 1,000 during the review period."}
    reply = plan(measure="field/CASA_Txn/Amount", aggregate="sum", entities=["E1"], time=["D1"],
                 time_field="field/CASA_Txn/TransactionDate", filters=[{"field": "field/CASA_Txn/DebitCredit", "value": "CR"}],
                 evidence=[{"need": "expected credit turnover", "type": "number", "party": "E1", "required": True}],
                 compare={"op": "difference", "left": "fact", "right": "V1"})
    result = run(answer_pg, FIN, f"By how much does customer {customer}'s recorded credit turnover for 4 to 6 November 2026 "
                 "differ from what the review memo expects?", Script([reply], [read("1,000")]), docs=doc)
    assert result["status"] == "answered", result["reason"]
    fact = result["facts"][0]
    assert fact["filters"] == {"AccountNumber": account, "DebitCredit": "CR", "aggregate": "sum",
                               "TransactionDate": {"from": "2026-11-04", "to": "2026-11-06"}}
    assert abs(fact["value"] - want) < 0.01 and abs(result["conclusion"] - abs(want - 1000)) < 0.01


def test_a_clarification_about_several_rows_is_settled_by_the_record_the_document_names(answer_pg):
    fx = rows("Treasury_FX_Txn")
    customer = sorted({r["CustomerID"] for r in fx})[1]
    deal = sorted((r for r in fx if r["CustomerID"] == customer), key=lambda r: r["TransactionID"])[0]
    doc = {"sheet": f"The term sheet confirms deal {deal['TransactionID']}, booked on {deal['TransactionDate'][:10]}."}
    reply = plan(measure="field/Treasury_FX_Txn/TransactionDate", aggregate="value", entities=["E1"], answer="boolean",
                 evidence=[{"need": "booking date of the deal", "type": "date", "party": "E1", "required": True}],
                 compare={"op": "==", "left": "fact", "right": "V1"})
    result = run(answer_pg, FIN, f"Customer {customer}'s term sheet names one deal by its booking date. Does the bank's "
                 "record carry the deal on that date?", Script([reply, reply], [read(deal["TransactionDate"][:10])]), docs=doc)
    assert result["status"] == "answered" and result["conclusion"] is True, result["reason"]
    assert result["facts"][0]["filters"] == {"TransactionID": deal["TransactionID"]}


def test_another_partys_record_in_the_clause_never_narrows(answer_pg):
    fx = rows("Treasury_FX_Txn")
    one, other = sorted({r["CustomerID"] for r in fx})[1:3]
    deal = next(r for r in fx if r["CustomerID"] == other)
    doc = {"sheet": f"The term sheet confirms deal {deal['TransactionID']}, booked on {deal['TransactionDate'][:10]}."}
    reply = plan(measure="field/Treasury_FX_Txn/TransactionDate", aggregate="value", entities=["E1"], answer="boolean",
                 evidence=[{"need": "booking date of the deal", "type": "date", "party": None, "required": True}],
                 compare={"op": "==", "left": "fact", "right": "V1"})
    result = run(answer_pg, FIN, f"Customer {one}'s term sheet names one deal by its booking date. Does the bank's record "
                 "carry the deal on that date?", Script([reply, reply], [read(deal["TransactionDate"][:10])] * 2), docs=doc)
    assert result["status"] == "clarify" and not result["facts"], result["reason"]


def test_a_named_record_with_no_rows_in_the_measures_table_is_a_clarification(answer_pg):
    loan = next(r for r in rows("Loan_Txn"))
    doc = {"notice": f"The notice expects at least 3 transactions on account {loan['AccountNumber']} during the period."}
    reply = plan(measure="field/CASA_Txn/TransactionID", aggregate="count", entities=["E1"], answer="boolean",
                 evidence=[{"need": "minimum number of transactions", "type": "number", "party": "E1", "required": True}],
                 compare={"op": ">=", "left": "fact", "right": "V1"})
    result = run(answer_pg, FIN, f"Did customer {loan['CustomerID']}'s account reach the number of transactions its notice "
                 "expects?", Script([reply], [read("3 transactions")]), docs=doc)
    assert result["status"] == "clarify" and "no rows" in result["reason"] and not result["facts"]


# ---------------------------------------------------------------- conditions

def test_a_yes_no_answer_that_leaves_a_stated_condition_out_is_a_clarification(answer_pg):
    vendor = rows_mfg("Purchasing_ProductVendor")[0]
    doc = {"agreement": f"The supplier undertakes a lead time of at most 30 days and a price of at most 99.00 per unit."}
    reply = plan(measure="field/Purchasing_ProductVendor/StandardPrice", aggregate="value", answer="boolean",
                 filters=[{"field": "field/Purchasing_ProductVendor/ProductID", "value": vendor["ProductID"]},
                          {"field": "field/Purchasing_ProductVendor/BusinessEntityID", "value": vendor["BusinessEntityID"]}],
                 evidence=[{"need": "price ceiling", "type": "number", "party": None, "required": True},
                           {"need": "lead time", "type": "period", "party": None, "required": True}],
                 compare={"op": "<=", "left": "fact", "right": "V1"})
    result = run(answer_pg, MFG, "Do the recorded supply terms meet both the price and the lead time of the agreement?",
                 Script([reply], [read("99.00", "30 days")]), docs=doc)
    assert result["status"] == "clarify" and "lead time" in result["reason"] and result["conclusion"] is None


def test_several_conditions_must_all_hold_and_a_length_of_time_is_a_number(answer_pg):
    vendor = rows_mfg("Purchasing_ProductVendor")[0]
    price = float(vendor["StandardPrice"])
    doc = {"agreement": f"The supplier undertakes a lead time of at most 30 days and a price of at most {price + 1:.2f} per unit."}
    reply = plan(measure="field/Purchasing_ProductVendor/StandardPrice", aggregate="value", answer="boolean",
                 filters=[{"field": "field/Purchasing_ProductVendor/ProductID", "value": vendor["ProductID"]},
                          {"field": "field/Purchasing_ProductVendor/BusinessEntityID", "value": vendor["BusinessEntityID"]}],
                 evidence=[{"need": "price ceiling", "type": "number", "party": None, "required": True},
                           {"need": "lead time", "type": "period", "party": None, "required": True}],
                 compare=[{"op": "<=", "left": "fact", "right": "V1"}, {"op": "<", "left": "V2", "right": "fact"}])
    result = run(answer_pg, MFG, "Do the recorded supply terms meet both the price and the lead time of the agreement?",
                 Script([reply], [read(f"{price + 1:.2f}", "30 days")]), docs=doc)
    assert result["status"] == "answered", result["reason"]
    assert result["conclusion"] is (price <= price + 1 and 30 < price)


# ---------------------------------------------------------------- advice once

def test_an_entity_the_measures_table_cannot_reach_is_sent_back_once(answer_pg):
    vendor = rows_mfg("Purchasing_Vendor")[5]
    first = plan(measure="metric/sales_order_count", entities=["E1"])
    second = plan(measure="field/Purchasing_PurchaseOrderHeader/PurchaseOrderID", aggregate="count", entities=["E1"])
    chat = Script([first, second])
    result = run(answer_pg, MFG, f"How many purchase orders went to {vendor['Name']}?", chat)
    assert len(chat.calls) == 2 and "cannot filter" in chat.calls[1][1]
    want = sum(r["VendorID"] == vendor["BusinessEntityID"] for r in rows_mfg("Purchasing_PurchaseOrderHeader"))
    assert result["status"] == "answered" and result["conclusion"] == want, result["reason"]


def test_a_document_need_that_is_a_recorded_value_is_sent_back_once_and_a_kept_choice_is_used(answer_pg):
    reply = plan(answer="boolean", evidence=[{"need": "the date in the notice", "type": "date", "party": None, "required": True},
                                             {"need": "the date recorded in the system", "type": "date", "party": None,
                                              "required": True}],
                 compare={"op": "==", "left": "V1", "right": "V2"})
    chat = Script([reply, reply], [read(None, None)])
    run(answer_pg, MFG, "Does the order record show the date the notice gives?", chat, docs={"n": "A notice without dates."})
    plans = [c for c in chat.calls if c[0] == ap.PLAN_SYSTEM]
    assert len(plans) == 2 and "value the data records" in plans[1][1]


def test_a_plural_name_matches_its_singular(answer_pg):
    sub = next(r for r in rows_mfg("Production_ProductSubcategory") if r["Name"].endswith("Bikes"))
    words = sub["Name"].lower()[:-1]          # "touring bike"
    ctx, offers = ctx_offers(answer_pg, MFG, f"How many {words} units did customers order in 2023?")
    assert any(e["table"] == "Production_ProductSubcategory" and e["value"] == sub["ProductSubcategoryID"]
               for e in offers["entities"])


def test_a_plural_never_widens_a_partial_name_run(answer_pg):
    stores = [r["Name"] for r in rows_mfg("Sales_Store")]
    word = next(w for w in ("Wholesale", "Cycle", "Bike") if sum(w.lower() + "s" not in n.lower() and w.lower() in n.lower()
                                                                  for n in stores) > 1)
    ctx, offers = ctx_offers(answer_pg, MFG, f"How many orders has Harbourview {word}s placed?")
    assert not any(e["table"] == "Sales_Store" for e in offers["entities"])


def test_a_joined_filter_whose_column_name_several_tables_hold_keeps_its_table(answer_pg):
    reply = plan(measure="metric/customers_on_business_date", time=["D1"],
                 filters=[{"field": "field/RelationshipManagers/Status", "value": "Active"}])
    result = run(answer_pg, FIN, "How many customers had an active relationship manager on 5 November 2026?", Script([reply]))
    assert result["facts"][0]["filters"] == {"RelationshipManagers.Status": "Active", "BusinessDate": "2026-11-05"}


def test_a_key_written_as_a_description_is_the_record_the_document_names(answer_pg):
    heads = rows_mfg("Sales_SalesOrderHeader")
    customer = next(c for c in sorted({r["CustomerID"] for r in heads}) if sum(r["CustomerID"] == c for r in heads) > 2)
    order = next(r for r in heads if r["CustomerID"] == customer)
    day = order["OrderDate"][:10]
    doc = {"notice": f"The shipment notice confirms that sales order {order['SalesOrderID']} was placed on {day}."}
    reply = plan(measure="field/Sales_SalesOrderHeader/OrderDate", aggregate="value", entities=["E1"], answer="boolean",
                 filters=[{"field": "field/Sales_SalesOrderHeader/SalesOrderID", "value": "the order in the shipment notice"}],
                 evidence=[{"need": "order date in the notice", "type": "date", "party": "E1", "required": True}],
                 compare={"op": "==", "left": "fact", "right": "V1"})
    result = run(answer_pg, MFG, f"The shipment notice to customer {customer} gives the date of one order. Is it the "
                 "date the order carries?", Script([reply, reply], [read(day)]), docs=doc)
    assert result["status"] == "answered" and result["conclusion"] is True, result["reason"]
    assert result["facts"][0]["filters"] == {"SalesOrderID": int(order["SalesOrderID"])}



def test_an_invalid_reply_to_advice_falls_back_to_the_valid_plan(answer_pg):
    vendor = rows_mfg("Purchasing_Vendor")[5]
    first = plan(measure="metric/sales_order_count", entities=["E1"])
    chat = Script([first, plan(measure="metric/nope")])
    result = run(answer_pg, MFG, f"How many purchase orders went to {vendor['Name']}?", chat)
    assert len(chat.calls) == 2 and result.get("refusalReason") != "not planned"
    assert result["status"] == "clarify" and not result["facts"], result["reason"]
