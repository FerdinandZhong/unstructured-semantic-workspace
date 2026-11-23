"""Answer path round 5 (2026-11-22): safety first. Each test states one general mechanism with scripted model replies
and values computed here from the demo data; documents are written here, no exam question's document is used.

- a commitment (what a party agreed, committed or certified) is a document fact: a database figure never stands in for
  it, and when only a withheld source states it for the party, the reply is a "source withheld" refusal; a readable
  document of another party never stands in either;
- a direction the measure's wording names (credit turnover) binds the one value of the measured table's direction
  column; both directions named is a clarification; a word inside another model name (credit card) binds nothing;
- a period wholly outside the data is refused before a missing parameter is asked for;
- a table the facts schema does not hold (a table filled from documents that has not been written) is not offered to
  the planner, and never reaches the user as a database error;
- a reading that does not copy a value is repaired with an instruction to copy it from the clause; an optional need
  that is still not copied is left out, a required one still fails;
- "booked for" a date reads the snapshot (business) date; "how many <rows of the measured table>" counts rows, never
  sums a quantity; "by how many" is a difference, not a yes/no; two compared values copied from one span are refused."""
import json

import pytest

import semantic_pipeline_v2.answer_path as ap
from semantic_pipeline_v2.evidence_links import EntityKeys
from semantic_pipeline_v2.workspace import load_workspace
from test_answer_plan import ADMIN, FIN, MFG, Script, plan, run
from test_answer_plan2 import rows
from test_answer_plan3 import rows_mfg


class Reader(Script):
    """A Script whose reading replies may be functions of the reading prompt's offered clauses."""

    def complete(self, system, user):
        self.calls.append((system, user))
        queue = self.plans if system == ap.PLAN_SYSTEM else self.reads
        reply = queue.pop(0)
        if callable(reply):
            reply = reply(json.loads(user.split("\nYour reply was rejected")[0])["CLAUSES"])
        return reply if isinstance(reply, str) else json.dumps(reply)


def pick(*words):
    """A reading reply that takes, for need 1, the clause holding all `words`, copying the first word as the value."""
    def reply(clauses):
        n = next((c["n"] for c in clauses if all(w in c["text"] for w in words)), None)
        return {"found": [{"need": 1, "clause": n, "value": words[0] if n else None}], "supporting": [], "summary": ""}
    return reply


RESTRICTED = lambda ds, *withheld: {"id": "r", "role": "restricted", "datasets": [ds], "withheld": list(withheld)}  # noqa: E731

# a second customer's agreement (withheld) and a first customer's agreement (readable), both on one product
STORE_Q = "How many units of the Touring-1000 Yellow, 46 did Camping and Sports Store agree to take in 2024?"
HIDDEN = {"W": "## 1.1 Separate account\nCustomer Camping and Sports Store, CustomerID 29629, has a separate commitment of "
               "40 units of ProductNumber BK-T79Y-46 during calendar year 2024."}
OTHER = {"R": "## 1.1 Account\nCustomer Westside Plaza uses CustomerID 29641 and StoreID 652.\n\n## 2. Commitment\n"
              "The customer commits to order at least 71 units of ProductNumber BK-T79Y-46 from 2024-01-01 through 2024-12-31."}
DATA_PLAN = plan(measure="metric/sales_ordered_units", entities=["E1", "E2"], time=["D1"],
                 time_field="field/Sales_SalesOrderHeader/OrderDate")


# ---------------------------------------------------------------- 1. commitments and withheld sources

def test_a_commitment_only_a_withheld_source_states_is_refused_as_withheld_not_answered_from_the_data(answer_pg):
    result = run(answer_pg, MFG, STORE_Q, Reader([DATA_PLAN]), docs={**HIDDEN, **OTHER}, principal=RESTRICTED(MFG, "W"))
    assert result["status"] == "refused" and result["refusalReason"] == "source withheld", result["reason"]
    assert not result["facts"] and not result["citations"] and result["conclusion"] is None
    assert "40" not in json.dumps(result)


def test_a_commitment_is_read_from_the_party_s_own_agreement_never_from_the_data_or_another_party(answer_pg):
    chat = Reader([DATA_PLAN], [pick("71 units", "commits"), pick("40 units", "Camping")])
    result = run(answer_pg, MFG, STORE_Q, chat, docs={**HIDDEN, **OTHER})
    assert result["status"] == "answered" and result["conclusion"] == "40 units", result["reason"]
    assert not result["facts"] and [c["source"] for c in result["citations"]] == ["W"]


def test_a_data_question_about_a_party_a_withheld_source_names_is_still_answered(answer_pg):
    asked = "How many units of the Touring-1000 Yellow, 46 did Camping and Sports Store order in 2024?"
    result = run(answer_pg, MFG, asked, Reader([DATA_PLAN]), docs={**HIDDEN, **OTHER}, principal=RESTRICTED(MFG, "W"))
    assert result["status"] == "answered" and result["facts"], result["reason"]


def test_a_certified_figure_only_a_withheld_finance_source_states_is_refused_as_withheld(answer_pg):
    asked = "What minimum balance did customer CUST00004 certify for its operating account?"
    doc = {"WF": "## 3. Certification\n3.1 Customer CUST00004 certifies a minimum balance of USD 12,000 in account MY-CA-00004-2."}
    reply = plan(measure="field/Accounts/AccountNumber", aggregate="count", entities=["E1"])
    result = run(answer_pg, FIN, asked, Reader([reply]), docs=doc, principal=RESTRICTED(FIN, "WF"))
    assert result["status"] == "refused" and result["refusalReason"] == "source withheld", result["reason"]
    assert not result["facts"] and not result["citations"] and "12,000" not in json.dumps(result)


# ---------------------------------------------------------------- 2. a direction the measure's wording names

def credits(account, direction=None):
    return round(sum(float(r["Amount"]) for r in rows("CASA_Txn")
                     if r["AccountNumber"] == account and direction in (None, r["DebitCredit"])), 2)


def test_credit_turnover_binds_the_credit_value_of_the_direction_column(answer_pg):
    reply = plan(measure="field/CASA_Txn/Amount", aggregate="sum",
                 filters=[{"field": "field/CASA_Txn/AccountNumber", "value": "MY-CA-00004-2"}])
    result = run(answer_pg, FIN, "What was the credit turnover in account MY-CA-00004-2?", Reader([reply]))
    assert result["status"] == "answered" and result["facts"][0]["filters"].get("DebitCredit") == "CR", result["reason"]
    assert result["conclusion"] == credits("MY-CA-00004-2", "CR") != credits("MY-CA-00004-2")


def test_both_directions_named_is_a_clarification_never_a_mixed_figure(answer_pg):
    reply = plan(measure="field/CASA_Txn/Amount", aggregate="sum",
                 filters=[{"field": "field/CASA_Txn/AccountNumber", "value": "MY-CA-00004-2"}])
    result = run(answer_pg, FIN, "What were the debit and credit turnover in account MY-CA-00004-2?", Reader([reply]))
    assert result["status"] == "clarify" and not result["facts"], result["reason"]


def test_a_direction_word_inside_another_model_name_binds_nothing(answer_pg):
    reply = plan(measure="field/CreditCard_Txn/Amount", aggregate="sum", time=["D1"])
    result = run(answer_pg, FIN, "What was the total of credit card transactions booked for 4 November 2026?", Reader([reply]))
    assert result["status"] == "answered" and "DebitCredit" not in result["facts"][0]["filters"], result["reason"]


# ---------------------------------------------------------------- 3. a period outside the data

def test_a_period_outside_the_data_is_refused_before_a_missing_parameter_is_asked_for(answer_pg):
    reply = plan(measure="metric/production_requested_units", time=["D1"], time_field="field/Production_WorkOrder/StartDate")
    result = run(answer_pg, MFG, "How many units were requested on work orders started in 2020?", Reader([reply]))
    assert result["status"] == "refused" and result["refusalReason"] == "period outside the data", result["reason"]


# ---------------------------------------------------------------- 4. tables the facts schema does not hold

def test_a_plan_that_reads_a_table_the_facts_schema_does_not_hold_is_rejected_naming_it(answer_pg):
    ws = load_workspace(FIN)
    ctx = ap._Ctx("q", ws, ADMIN(FIN), answer_pg, None, None, EntityKeys(ws), None, None, None)
    assert "str_transaction" in ctx.empty and "loan_txn" not in ctx.empty
    offers = ap._offers(ctx)
    with pytest.raises(ap.PlanError, match="str_transaction holds no rows"):
        ap.validate_plan(json.dumps(plan(measure="metric/reported_suspicious_transactions")), ctx, offers)
    with pytest.raises(ap.PlanError, match="holds no rows"):
        ap.validate_plan(json.dumps(plan(measure="field/Loan_Txn/TransactionID", aggregate="count",
                                         filters=[{"field": "field/STR_Transaction/StartDate", "value": "x"}])), ctx, offers)


def test_a_plan_on_a_table_with_no_relation_is_repaired_and_never_shows_a_database_error(answer_pg):
    asked = "How many loan transactions were booked for 6 November 2026?"
    bad = plan(measure="metric/reported_suspicious_transactions", time=["D1"])
    good = plan(measure="field/Loan_Txn/TransactionID", aggregate="count", time=["D1"])
    chat = Reader([bad, good])
    result = run(answer_pg, FIN, asked, chat)
    assert "the question names: Loan_Txn" in chat.calls[1][1]
    assert result["status"] == "answered" and result["conclusion"] == sum(r["BusinessDate"] == "2026-11-06" for r in rows("Loan_Txn"))
    with_need = plan(measure="metric/reported_suspicious_transactions", time=["D1"], document="Suspicious transaction report",
                     evidence=[{"need": "number of transactions reported", "type": "number", "party": None, "required": True}])
    chat = Reader([with_need, good])     # a document need beside it does not turn a data count into a document read
    assert run(answer_pg, FIN, asked, chat)["conclusion"] == result["conclusion"] and "holds no rows" in chat.calls[1][1]
    failed = run(answer_pg, FIN, asked, Reader([bad, bad]))
    assert failed["status"] == "refused" and "does not exist" not in failed["reason"] and "psql" not in failed["reason"]


# ---------------------------------------------------------------- 5. a value that is not copied

DOC_NOTICE = {"N": "## 3. Term\n3.5 Either party may end this agreement with 90 days of written notice."}
TWO_NEEDS = plan(answer="text", evidence=[{"need": "notice period", "type": "text", "party": None, "required": True},
                                          {"need": "whether it is the same for both", "type": "text", "party": None, "required": False}])


def reading(second):
    return lambda clauses: {"found": [{"need": 1, "clause": clauses[0]["n"], "value": "90 days"},
                                      {"need": 2, "clause": clauses[0]["n"], "value": second}], "supporting": [], "summary": ""}


def test_a_value_not_copied_is_repaired_with_an_instruction_to_copy_it_and_an_optional_need_is_then_left_out(answer_pg):
    chat = Reader([TWO_NEEDS], [reading("the same"), reading("the same")])
    result = run(answer_pg, MFG, "How much written notice must either party give to end the agreement?", chat, docs=DOC_NOTICE)
    assert "copy" in chat.calls[-1][1].split("Your reply was rejected")[1].lower()
    assert result["status"] == "answered" and result["conclusion"] == "90 days", result["reason"]


def test_a_required_value_not_copied_twice_is_still_not_read(answer_pg):
    one = plan(answer="text", evidence=[{"need": "notice period", "type": "text", "party": None, "required": True}])
    bad = lambda clauses: {"found": [{"need": 1, "clause": clauses[0]["n"], "value": "three months"}], "supporting": [], "summary": ""}  # noqa: E731
    result = run(answer_pg, MFG, "How much written notice must either party give?", Reader([one], [bad, bad]), docs=DOC_NOTICE)
    assert result["status"] == "refused" and result["refusalReason"] == "not read"


# ---------------------------------------------------------------- 6. wrong figures

def test_booked_for_a_date_reads_the_snapshot_date(answer_pg):
    reply = plan(measure="field/Loan_Txn/Amount", aggregate="sum", time=["D1"], time_field="field/Loan_Txn/TransactionDate",
                 filters=[{"field": "field/Loan_Txn/Currency", "value": "SGD"}])
    result = run(answer_pg, FIN, "What was the total value of loan transactions in SGD booked for 5 November 2026?", Reader([reply]))
    want = round(sum(float(r["Amount"]) for r in rows("Loan_Txn") if r["BusinessDate"] == "2026-11-05" and r["Currency"] == "SGD"), 2)
    assert result["status"] == "answered" and result["facts"][0]["filters"].get("BusinessDate") == "2026-11-05", result["reason"]
    assert result["conclusion"] == want


def test_how_many_rows_of_the_measured_table_counts_rows_and_never_sums_a_quantity(answer_pg):
    reply = plan(measure="metric/production_scrapped_units", filters=[{"field": "field/Production_WorkOrder/ScrappedQty", "op": ">", "value": 100}])
    result = run(answer_pg, MFG, "How many work orders scrapped more than 100 units?", Reader([reply]))
    assert result["status"] == "answered" and result["conclusion"] == sum(float(r["ScrappedQty"]) > 100 for r in rows_mfg("Production_WorkOrder"))
    units = run(answer_pg, MFG, "How many units did work orders scrap above 100 each?", Reader([reply]))
    assert units["conclusion"] == sum(float(r["ScrappedQty"]) for r in rows_mfg("Production_WorkOrder") if float(r["ScrappedQty"]) > 100)


def test_by_how_many_is_a_difference_not_a_yes_or_no(answer_pg):
    product = rows_mfg("Production_Product")[200]
    days = int(product["DaysToManufacture"])
    doc = {"S": f"Specification for ProductID {product['ProductID']}.\n\n## 3. Target\n3.1 The build time will be cut to no more "
                f"than {days + 2} days."}
    reply = plan(measure="field/Production_Product/DaysToManufacture", aggregate="value",
                 filters=[{"field": "field/Production_Product/ProductID", "value": product["ProductID"]}],
                 evidence=[{"need": "target build time", "type": "number", "party": None, "required": True}],
                 compare={"op": "<", "left": "fact", "right": "V1"})
    result = run(answer_pg, MFG, f"By how many days is the build time of product {product['ProductID']} below the target?",
                 Reader([reply], [pick(f"{days + 2} days")]), docs=doc)
    assert result["status"] == "answered" and result["conclusion"] == 2, result["reason"]


def test_two_compared_values_copied_from_one_span_are_not_compared(answer_pg):
    doc = {"T": "## 3. Limits\n3.5 The Customer shall not book more than 2 foreign exchange transactions above USD 40,000."}
    reply = plan(answer="boolean", evidence=[{"need": "the cap", "type": "number", "party": None, "required": True},
                                             {"need": "the number booked", "type": "number", "party": None, "required": True}],
                 compare={"op": "<=", "left": "V2", "right": "V1"})
    same = lambda clauses: {"found": [{"need": i, "clause": clauses[0]["n"], "value": "2 foreign exchange transactions"}  # noqa: E731
                                      for i in (1, 2)], "supporting": [], "summary": ""}
    result = run(answer_pg, FIN, "Did the customer stay within the cap on large foreign exchange transactions?",
                 Reader([reply], [same]), docs=doc)
    assert result["status"] != "answered" and result["conclusion"] is None


def test_the_largest_single_value_is_the_maximum_of_the_column_a_total_adds(answer_pg):
    reply = plan(measure="metric/sales_ordered_units")
    result = run(answer_pg, MFG, "What is the largest quantity ordered on a single sales order line?", Reader([reply]))
    assert result["status"] == "answered" and result["conclusion"] == max(int(r["OrderQty"]) for r in rows_mfg("Sales_SalesOrderDetail"))
    total = run(answer_pg, MFG, "What is the largest quantity ordered?", Reader([reply]))
    assert total["status"] == "clarify"        # no single row named: the total still needs its period


def test_a_promise_that_only_sets_the_context_leaves_a_recorded_value_question_to_the_data(answer_pg):
    asked = ("Camping and Sports Store committed to take the Touring-1000 Yellow, 46 in 2024. How many units of it did "
             "the system record as ordered by that store in 2024?")
    result = run(answer_pg, MFG, asked, Reader([DATA_PLAN]), docs={**HIDDEN, **OTHER})
    assert result["status"] == "answered" and result["facts"], result["reason"]


def test_a_document_question_whose_plan_names_an_empty_table_is_answered_from_the_document(answer_pg):
    need = {"need": "total amount the report states", "type": "number", "party": None, "required": True}
    docs = {"report": "Report SYN-TEST-0077: total amount involved SGD 3,400.00 in this report."}
    chat = Reader([plan(measure="metric/reported_suspicious_transaction_value", evidence=[need],
                        filters=[{"field": "field/STR_Transaction/TransactionSeq", "value": "SYN-TEST-0077"}])],
                  [{"found": [{"need": 1, "clause": 1, "value": "SGD 3,400.00"}], "supporting": [], "summary": ""}])
    result = run(answer_pg, FIN, "What total amount does report SYN-TEST-0077 state?", chat, docs)
    assert result["status"] == "answered" and not result["facts"] and result["conclusion"] == "SGD 3,400.00"
    assert len(chat.calls) == 2         # one plan, one reading: the empty table cost no repair
