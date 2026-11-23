"""Answer path round 6 (2026-11-22): a record identifier the question states (a report, contract, order or account
reference) binds the documents a value may be read from. The cited value's source must state that identifier in at
least one readable passage; a neighbouring document about another record never stands in. With report splitting a
report is many passages, so the identifier may stand in another passage of the same source. Documents are written here;
no exam question's document is used."""
import json

import semantic_pipeline_v2.answer_path as ap
from semantic_pipeline_v2.evidence_links import EntityKeys
from semantic_pipeline_v2.workspace import load_workspace
from test_answer_plan import ADMIN, FIN, plan
from test_answer_plan5 import RESTRICTED, Reader, pick


class Passages:
    """A chunk index whose sources are several passages: {source_id: [passage, ...]}. Search leaves out passages that
    hold `unseen` (retrieval that misses a source's identifier passage)."""

    def __init__(self, docs, unseen=None):
        self.rows = [{"chunk_id": f"c-{s}-{i}", "source_id": s, "text": t, "content_hash": "h"}
                     for s, parts in docs.items() for i, t in enumerate(parts)]
        self.unseen = unseen

    def search(self, datasets, query, vector, exclude_sources=(), limit=20):
        return {"rows": [r for r in self.rows if r["source_id"] not in exclude_sources
                         and not (self.unseen and self.unseen in r["text"])], "mode": "keyword"}

    def read(self, dataset, source_id):
        return [r for r in self.rows if r["source_id"] == source_id]


def run(pg, question, chat, docs, principal=None, unseen=None):
    ws = load_workspace(FIN)
    ws = {**ws, "sources": [*ws["sources"], *({"id": s, "title": s, "kind": "synthetic-document"} for s in docs)]}
    return ap.answer(question, ws, principal or ADMIN(FIN), database=pg, chunks=Passages(docs, unseen),
                     entity_keys=EntityKeys(ws), chat=chat)


# report 0013 (another record) and report 0008 (the asked record), each split into an identifier and a value passage
OTHER = {"N": ["## Suspicious transaction report\nReport reference: SYN-STR-0013\nCounterparty: Harbour Line Trading",
               "Total amount involved: SGD 156,750.00",
               "Amount flagged on first review: SGD 12,000.00"]}
OWN = {"A": ["## Suspicious transaction report\nReport reference: syn str 0008\nCounterparty: Kestrel Metals",
             "Total amount involved: SGD 22,400.00"]}
TOTAL = {"need": "total amount the report states was involved", "type": "number", "party": None, "required": True}
ASKED = "What total amount does suspicious transaction report SYN-STR-0008 state was involved?"


def test_a_stated_identifier_no_readable_document_states_is_refused_not_answered_from_a_neighbour(answer_pg):
    result = run(answer_pg, ASKED, Reader([plan(evidence=[TOTAL])], [pick("SGD 156,750.00")] * 2), OTHER)
    assert result["status"] == "refused" and result["refusalReason"] == "no supported answer", result.get("reason")
    assert "SYN-STR-0008" in result["reason"]
    assert not result["facts"] and not result["citations"] and result["conclusion"] is None
    assert "156,750" not in json.dumps(result)


def test_the_counterparty_of_a_report_no_readable_document_states_is_refused(answer_pg):
    need = {"need": "counterparty named on the report", "type": "text", "party": None, "required": True}
    asked = "Who is the counterparty on suspicious transaction report SYN-STR-0008?"
    result = run(answer_pg, asked, Reader([plan(answer="text", evidence=[need])], [pick("Harbour Line Trading")] * 2), OTHER)
    assert result["status"] == "refused" and not result["citations"] and "Harbour" not in json.dumps(result)


def test_the_value_is_answered_from_another_passage_of_the_source_that_states_the_identifier(answer_pg):
    for unseen in (None, "Report reference"):     # the identifier passage retrieved, or only read from the source
        chat = Reader([plan(evidence=[TOTAL])], [pick("SGD 22,400.00")])
        result = run(answer_pg, ASKED, chat, {**OTHER, **OWN}, unseen=unseen)
        assert result["status"] == "answered" and result["conclusion"] == "SGD 22,400.00", (unseen, result.get("reason"))
        assert {c["source"] for c in result["citations"]} == {"A"}


def test_a_question_with_no_identifier_is_unaffected(answer_pg):
    asked = "What total amount does the suspicious transaction report state was involved?"
    result = run(answer_pg, asked, Reader([plan(evidence=[TOTAL])], [pick("SGD 156,750.00")]), OTHER)
    assert result["status"] == "answered" and result["conclusion"] == "SGD 156,750.00", result.get("reason")


def test_the_report_only_a_withheld_source_states_is_refused_as_withheld(answer_pg):
    result = run(answer_pg, ASKED, Reader([plan(evidence=[TOTAL])], [pick("SGD 156,750.00")] * 2), {**OTHER, **OWN},
                 principal=RESTRICTED(FIN, "A"))
    assert result["status"] == "refused" and result["refusalReason"] == "source withheld", result.get("reason")
    assert not result["citations"] and "22,400" not in json.dumps(result) and "156,750" not in json.dumps(result)


TWO = "Is the total amount of report SYN-STR-0008 greater than that of report SYN-STR-0013?"
BOTH = plan(answer="boolean", evidence=[{**TOTAL, "need": "total amount of report SYN-STR-0008"},
                                        {**TOTAL, "need": "total amount of report SYN-STR-0013"}],
            compare={"op": ">", "left": "V1", "right": "V2"})


def two(first, second):
    def reply(clauses):
        n = [next((c["n"] for c in clauses if w in c["text"]), None) for w in (first, second)]
        return {"found": [{"need": i, "clause": k, "value": w if k else None}
                          for i, (k, w) in enumerate(zip(n, (first, second)), 1)], "supporting": [], "summary": ""}
    return reply


def test_two_identifiers_answer_only_when_each_cited_source_states_its_identifier(answer_pg):
    result = run(answer_pg, TWO, Reader([BOTH], [two("SGD 22,400.00", "SGD 156,750.00")]), {**OTHER, **OWN})
    assert result["status"] == "answered" and result["conclusion"] is False, result.get("reason")
    assert {c["source"] for c in result["citations"]} == {"A", "N"}
    # only report 0013 is readable: both values read from it are not an answer about report 0008
    alone = run(answer_pg, TWO, Reader([BOTH], [two("SGD 12,000.00", "SGD 156,750.00")] * 2), OTHER)
    assert alone["status"] == "refused" and "SYN-STR-0008" in alone["reason"], alone.get("reason")
    assert not alone["citations"] and alone["conclusion"] is None and not alone["facts"]
