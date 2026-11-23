"""Answer path round 8 (2026-11-22): the conclusion of a document answer is the value of the need the question asks
for. Each test states one general mechanism with scripted model replies; documents are written here.

- what a party must take, pay or deliver (an obligation, like a commitment) is a document fact: a recorded figure the
  plan also measured never becomes the conclusion;
- with a recorded figure and a document value and no comparison, a conclusion the reading's own summary does not state
  (while it states the document value) is a clarification, never a figure;
- with several needs and no comparison, the conclusion is the value of the asked kind: a currency code is not an
  amount, a date need listed first is not the asked number; the planner lists the asked need first, so the first need
  of the asked kind is the primary one (which value of an amended term is asked stays the reading's choice);
- a difference or comparison the question asks for still computes its conclusion, and a one-need lookup is unchanged."""
import pytest

from test_answer_plan import FIN, MFG, plan, run
from test_answer_plan3 import rows_mfg
from test_answer_plan5 import Reader

AGREEMENT = {
    "agreement": "## 3. Commitment\n3.1 The buyer commits to take at least 25 units across all products during 2024.\n"
                 "Entity scope: customer:29837",
    "amendment": "## 2. Amendment\nDate: 2024-07-01\n2.1 Clause 3.1 of the agreement is replaced: the buyer commits to take at least 40 units "
                 "across all products during 2024, in place of 25 units.\nEntity scope: customer:29837",
}
NEW_TOTAL = {"need": "total units required for 2024 under the amended agreement", "type": "number", "party": "E1",
             "required": True}
MEASURED = dict(measure="metric/sales_ordered_units", entities=["E1"], time=["D2"],
                time_field="field/Sales_SalesOrderHeader/OrderDate")


def clause(clauses, *words):
    return next(c["n"] for c in clauses if all(w in c["text"] for w in words))


def amended(summary):
    """A reading that copies the amended total from the amending clause, with the given summary."""
    def reply(clauses):
        return {"found": [{"need": 1, "clause": clause(clauses, "in place of"), "value": "40 units"}],
                "supporting": [], "summary": summary}
    return reply


def ordered_29837_2024():
    orders = {r["SalesOrderID"] for r in rows_mfg("Sales_SalesOrderHeader")
              if r["CustomerID"] == "29837" and r["OrderDate"][:4] == "2024"}
    return sum(int(r["OrderQty"]) for r in rows_mfg("Sales_SalesOrderDetail") if r["SalesOrderID"] in orders)


# ---------------------------------------------------------------- (a) the 34-vs-40 shape

def test_an_obligation_is_read_from_the_amending_clause_and_a_recorded_figure_never_becomes_the_conclusion(answer_pg):
    asked = ("Customer 29837's supply agreement was amended in July 2024. How many units across all products must it now "
             "take during 2024?")
    # the live plan's shape: a recorded measure beside two needs, the first of them a date
    needs = [{"need": "amendment date in July 2024", "type": "date", "party": "E1", "required": True}, NEW_TOTAL]

    def read(clauses):
        return {"found": [{"need": 1, "clause": clause(clauses, "Date:"), "value": "2024-07-01"},
                          {"need": 2, "clause": clause(clauses, "in place of"), "value": "40 units"}], "supporting": [],
                "summary": "The amendment dated 2024-07-01 raised the 2024 commitment from 25 units to 40 units."}
    chat = Reader([plan(**MEASURED, evidence=needs, document="the agreement")], [read])
    result = run(answer_pg, MFG, asked, chat, AGREEMENT)
    assert result["status"] == "answered" and result["conclusion"] == "40 units", (result["status"], result.get("conclusion"))
    assert not result["facts"] and result["citations"][0]["source"] == "amendment"


def test_a_conclusion_the_summary_contradicts_with_the_document_value_is_a_clarification(answer_pg):
    asked = "Customer 29837's supply agreement was amended in July 2024. What unit total across all products does it set for 2024?"
    recorded = ordered_29837_2024()
    assert recorded not in (25, 40)
    chat = Reader([plan(**MEASURED, evidence=[NEW_TOTAL], document="the amendment")],
                  [amended("The amendment sets the 2024 total at 40 units across all products, in place of 25 units.")])
    result = run(answer_pg, MFG, asked, chat, AGREEMENT)
    assert result["status"] == "clarify" and result["conclusion"] is None, (result["status"], result.get("conclusion"))
    assert str(recorded) not in str(result["reason"])


def test_the_first_need_of_the_asked_kind_is_the_conclusion_and_an_original_value_asked_first_stays_it(answer_pg):
    # the planner lists the asked need first: a question about the original term names it first (replay dw-fin-046)
    needs = [{"need": "the original 2024 minimum", "type": "number", "party": "E1", "required": True}, NEW_TOTAL]

    def both(clauses):
        return {"found": [{"need": 1, "clause": clause(clauses, "commits to take at least 25"), "value": "25 units"},
                          {"need": 2, "clause": clause(clauses, "in place of"), "value": "40 units"}],
                "supporting": [], "summary": "The agreement set 25 units, which the amendment replaced with 40 units."}
    asked = "Before its amendment, what 2024 unit total across all products did customer 29837's supply agreement set?"
    result = run(answer_pg, MFG, asked, Reader([plan(evidence=needs, document="the agreement")], [both]), AGREEMENT)
    assert result["status"] == "answered" and result["conclusion"] == "25 units", result.get("reason")


# ---------------------------------------------------------------- (b) the "SGD"-only shape

REPORT = {"report": "## Suspicious transaction report\nReport reference: SYN-TEST-0091\n| Total amount involved | SGD 3,400.00 |"}
ASKED_TOTAL = "What total amount does suspicious transaction report SYN-TEST-0091 state was involved?"


@pytest.mark.parametrize("currency_type", ["text", "number"])
def test_a_currency_need_is_never_the_conclusion_of_an_amount_question(answer_pg, currency_type):
    needs = [{"need": "currency of the total amount", "type": currency_type, "party": None, "required": True},
             {"need": "total amount involved", "type": "number", "party": None, "required": True}]

    def split(clauses):
        n = clause(clauses, "Total amount involved")
        return {"found": [{"need": 1, "clause": n, "value": "SGD"}, {"need": 2, "clause": n, "value": "3,400.00"}],
                "supporting": [], "summary": "The report states the total amount involved was SGD 3,400.00."}
    result = run(answer_pg, FIN, ASKED_TOTAL, Reader([plan(evidence=needs)], [split]), REPORT)
    assert result["status"] == "answered" and result["conclusion"] == "3,400.00", (result["status"], result.get("conclusion"))


def test_an_amount_question_whose_only_value_holds_no_number_is_not_answered(answer_pg):
    needs = [{"need": "currency of the total amount", "type": "text", "party": None, "required": True},
             {"need": "total amount involved", "type": "number", "party": None, "required": False}]

    def currency(clauses):
        return {"found": [{"need": 1, "clause": clause(clauses, "Total amount involved"), "value": "SGD"},
                          {"need": 2, "clause": None, "value": None}], "supporting": [], "summary": ""}
    result = run(answer_pg, FIN, ASKED_TOTAL, Reader([plan(evidence=needs)], [currency]), REPORT)
    assert result["status"] != "answered" and result["conclusion"] is None


# ---------------------------------------------------------------- (c) comparisons still compute

def test_a_difference_the_question_asks_for_is_still_computed(answer_pg):
    product = rows_mfg("Production_Product")[200]
    days = int(product["DaysToManufacture"])
    doc = {"S": f"Specification for ProductID {product['ProductID']}.\n\n## 3. Target\n3.1 The build time will be cut to no more "
                f"than {days + 2} days."}
    reply = plan(measure="field/Production_Product/DaysToManufacture", aggregate="value",
                 filters=[{"field": "field/Production_Product/ProductID", "value": product["ProductID"]}],
                 evidence=[{"need": "target build time", "type": "number", "party": None, "required": True}],
                 compare={"op": "difference", "left": "V1", "right": "fact"})

    def target(clauses):
        return {"found": [{"need": 1, "clause": clause(clauses, "build time"), "value": f"{days + 2} days"}],
                "supporting": [], "summary": f"The target build time is {days + 2} days."}
    result = run(answer_pg, MFG, f"By how many days is the build time of product {product['ProductID']} below the target?",
                 Reader([reply], [target]), docs=doc)
    assert result["status"] == "answered" and result["conclusion"] == 2, result["reason"]


def test_a_comparison_of_a_recorded_figure_with_a_commitment_is_still_computed(answer_pg):
    recorded = ordered_29837_2024()
    reply = plan(**{**MEASURED, "time": ["D1"]}, answer="boolean", evidence=[NEW_TOTAL], compare={"op": ">=", "left": "fact", "right": "V1"})
    result = run(answer_pg, MFG, "Did customer 29837 order at least its amended 2024 commitment during 2024?",
                 Reader([reply], [amended("The amended commitment is 40 units.")]), AGREEMENT)
    assert result["status"] == "answered" and result["conclusion"] is (recorded >= 40), result["reason"]


# ---------------------------------------------------------------- (d) a one-need lookup is unchanged

def test_a_single_need_lookup_is_unchanged(answer_pg):
    def total(clauses):
        return {"found": [{"need": 1, "clause": clause(clauses, "Total amount involved"), "value": "SGD 3,400.00"}],
                "supporting": [], "summary": "The report states SGD 3,400.00 was involved."}
    need = {"need": "total amount involved", "type": "number", "party": None, "required": True}
    result = run(answer_pg, FIN, ASKED_TOTAL, Reader([plan(evidence=[need])], [total]), REPORT)
    assert result["status"] == "answered" and result["conclusion"] == "SGD 3,400.00"


def test_a_recorded_value_the_summary_states_beside_a_document_value_stays_the_conclusion(answer_pg):
    need = {"need": "the type the report states", "type": "text", "party": None, "required": False}
    docs = {"report": "Customer number CUST00008 appears in this report about a corporate customer."}

    def stated(clauses):
        return {"found": [{"need": 1, "clause": clause(clauses, "CUST00008"), "value": "corporate"}], "supporting": [],
                "summary": "The bank records customer CUST00008 as Corporate on 6 November 2026."}
    chat = Reader([plan(measure="field/Customers/CustomerType", aggregate="value", entities=["E1"], time=["D1"],
                        evidence=[need], answer="choice")], [stated])
    result = run(answer_pg, FIN, "Is customer CUST00008 corporate or individual on 6 November 2026?", chat, docs)
    assert result["status"] == "answered" and result["conclusion"] == "Corporate"


def test_a_number_written_as_a_word_still_answers_a_number_question(answer_pg):
    docs = {"guide": "## 4. Oil\n4.2 Apply four drops of oil to the chain after each wash."}

    def drops(clauses):
        return {"found": [{"need": 1, "clause": clause(clauses, "drops"), "value": "four drops"}], "supporting": [],
                "summary": "Apply four drops of oil."}
    need = {"need": "drops of oil to apply", "type": "text", "party": None, "required": True}
    result = run(answer_pg, MFG, "How many drops of oil does the guide say to apply to the chain?",
                 Reader([plan(evidence=[need])], [drops]), docs)
    assert result["status"] == "answered" and result["conclusion"] == "four drops"
