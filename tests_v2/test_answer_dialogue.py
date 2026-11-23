"""A transcript states a figure in the turn that answers a question ("Could you confirm the exact amount? $879.90 in
Sing dollars."). The answer turn shares no word with a user's question, so it is offered to the reader only as one
clause with the question turn before it (22 November 2026: the recorded call's amount, merchant, date and customer
ID were refused because only the question turns were offered)."""
from semantic_pipeline_v2.answer_path import _windows

CALL = """Could you tell me the name of the merchant exactly as it appears by your credit card statement?

It says Nova Digital Marketplace.

Thank you.

What's the exact transaction date as shown on your statement?

3Rd November 2026.

Could you confirm the exact amount and currency?

$879.90 in Sing dollars.

Of course, sir.

May I have your customer ID, please?

Yes, it's CUS48271.
"""


def test_a_question_turn_and_the_turn_that_answers_it_form_one_clause():
    found = {w for w, _ in _windows(CALL)}
    assert "Could you confirm the exact amount and currency? $879.90 in Sing dollars." in found
    assert "What's the exact transaction date as shown on your statement? 3Rd November 2026." in found
    assert ("Could you tell me the name of the merchant exactly as it appears by your credit card statement? "
            "It says Nova Digital Marketplace.") in found
    assert "May I have your customer ID, please? Yes, it's CUS48271." in found
    # each turn is still a clause of its own; a statement is never joined to the line after it
    assert "$879.90 in Sing dollars." in found and "It says Nova Digital Marketplace." in found
    assert not any(w.startswith("Thank you.") and len(w) > len("Thank you.") for w in found)


CALL_ID, NOTE_ID = "fs:source:call", "fs:source:note"
NOTE = "\n".join(f"3.{n} The Customer shall make at least {n} credit card transactions during 4 November 2026 to "
                 "6 November 2026." for n in range(1, 16))
QUESTION = ("On the recorded credit card call of 6 November 2026, what amount did the customer dispute? "
            "The amount the customer disputed in the credit card call")
AMOUNT = "Could you confirm the exact amount and currency? $879.90 in Sing dollars."


class _Chunks:
    def __init__(self, rows):
        self.rows = rows

    def search(self, datasets, query, vector, *, exclude_sources=(), limit=20):
        return {"rows": [r for r in self.rows if r["source_id"] not in exclude_sources][:limit], "mode": "keyword"}

    def read(self, dataset, source_id):
        return [r for r in self.rows if r["source_id"] == source_id]


def _ctx(withheld=()):
    from types import SimpleNamespace
    rows = [*({"chunk_id": f"n{i}", "source_id": NOTE_ID, "text": "\n".join(NOTE.split("\n")[i::3])} for i in range(3)),
            {"chunk_id": "c1", "source_id": CALL_ID, "text": CALL + "\nThank you for calling ABC Bank Credit Card Services.\n"}]
    keys = SimpleNamespace(keys={}, values=lambda t, c: ())
    return SimpleNamespace(question=QUESTION, dataset="fs", principal={"withheld": list(withheld)}, chunks=_Chunks(rows),
                           links=None, ek=keys, embedder=None, notes=[],
                           ws={"sources": [{"id": CALL_ID, "title": "customer-call.m4a"},
                                           {"id": NOTE_ID, "title": "Card programme note"}]})


def test_the_document_the_plan_names_offers_its_clauses_that_state_a_figure():
    from semantic_pipeline_v2.answer_path import MAX_CLAUSES, _clauses
    plain, _ = _clauses(_ctx(), QUESTION, [])
    assert len(plain) == MAX_CLAUSES and AMOUNT not in [c["text"] for c in plain]   # crowded out by the note's clauses
    named, _ = _clauses(_ctx(), QUESTION, [], document=CALL_ID)
    texts = [c["text"] for c in named]
    assert texts[:MAX_CLAUSES] == [c["text"] for c in plain]       # the ranked clauses stay as they were
    assert AMOUNT in texts and "May I have your customer ID, please? Yes, it's CUS48271." in texts
    assert all(c["source_id"] == CALL_ID for c in named[MAX_CLAUSES:])
    # a withheld document offers nothing, also when the plan names it
    hidden, best = _clauses(_ctx(withheld=[CALL_ID]), QUESTION, [], document=CALL_ID)
    assert all(c["source_id"] != CALL_ID for c in hidden) and best["source_id"] == CALL_ID


def test_a_question_line_is_not_joined_to_a_long_passage_after_it():
    text = "Why does it matter?\n\n" + "The bank reviews every disputed charge under its policy, and the outcome " \
           "depends on the account status and the merchant reply within the stated period of review."
    assert not any(w.startswith("Why does it matter? ") for w, _ in _windows(text))


def test_an_optional_side_need_never_answers_in_place_of_the_required_need():
    # live plan of 22 November 2026: answer "text", a required amount (number) and an optional currency (text); the
    # conclusion was "Sing dollars" once in three asks
    from semantic_pipeline_v2.answer_path import _primary
    plan = {"answer": "text", "evidence": [{"need": "the amount disputed", "type": "number", "required": True},
                                           {"need": "the currency of the amount", "type": "text", "required": False}]}
    found = [{"value": "$879.90"}, {"value": "Sing dollars"}]
    assert _primary(plan, found)["value"] == "$879.90"
    assert _primary(plan, [None, {"value": "Sing dollars"}])["value"] == "Sing dollars"   # nothing required was found
    both = {"answer": "number", "evidence": [{"need": "currency", "type": "text", "required": True},
                                             {"need": "total", "type": "number", "required": True}]}
    assert _primary(both, [{"value": "SGD"}, {"value": "3,400.00"}])["value"] == "3,400.00"   # round 8 stays
