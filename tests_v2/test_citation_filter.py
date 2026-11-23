"""Citations (2026-11-22, replaces the P5 value-overlap post-filter `supported_citations`): code attaches the clause that
carries an operand or a found value first, then the model's supporting clauses; duplicates are dropped and the list is
capped. Every citation stays verbatim in a stored chunk."""

from semantic_pipeline_v2.answer_path import MAX_CITATIONS
from test_answer_plan import FIN, Script, plan, run

DOCS = {f"doc-{i}": f"Report clause {i}: the reported total amount for this case is SGD {i},000.00 in the report." for i in range(7)}


def test_the_value_clause_comes_first_duplicates_are_dropped_and_the_list_is_capped(answer_pg):
    need = {"need": "the reported total amount", "type": "number", "party": None, "required": True}
    chat = Script([plan(evidence=[need], answer="text")],
                  [{"found": [{"need": 1, "clause": 3, "value": "SGD 2,000.00"}], "supporting": [3, 1, 2, 4, 5, 6, 7], "summary": ""}])
    result = run(answer_pg, FIN, "What reported total amount does the report state for this case?", chat, DOCS)
    assert result["status"] == "answered" and result["conclusion"] == "SGD 2,000.00"
    spans = [c["span"] for c in result["citations"]]
    assert "SGD 2,000.00" in spans[0] and len(spans) == len(set(spans)) == MAX_CITATIONS


def test_a_supporting_clause_that_is_not_in_its_chunk_is_dropped(answer_pg, monkeypatch):
    import semantic_pipeline_v2.answer_path as ap
    real = ap._clauses

    def forged(*args, **kwargs):
        found, hidden = real(*args, **kwargs)
        return [found[0], {**found[1], "text": "An invented sentence the document never held."}, *found[2:]], hidden
    monkeypatch.setattr(ap, "_clauses", forged)
    need = {"need": "the reported total amount", "type": "number", "party": None, "required": True}
    chat = Script([plan(evidence=[need], answer="text")],
                  [{"found": [{"need": 1, "clause": 1, "value": "SGD"}], "supporting": [2], "summary": ""}])
    result = run(answer_pg, FIN, "What reported total amount does the report state for this case?", chat, DOCS)
    assert result["status"] == "answered" and all("invented" not in c["span"] for c in result["citations"])
