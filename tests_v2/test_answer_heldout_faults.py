"""General faults the held-out exam exposed (2026-11-21). The held-out set is spent: these are development tests.
Each test states a rule of the answer path, never a question of the exam."""
import json

import pytest

import semantic_pipeline_v2.answer_path as ap
from semantic_pipeline_v2._pilot_postgres import PilotDatabaseError
from semantic_pipeline_v2.evidence_links import EntityKeys
from semantic_pipeline_v2.metric_sql import (ClarificationNeeded, MetricQueryError, _refuse_breakdown, _run_sql,
                                             query_metric)
from semantic_pipeline_v2.workspace import load_workspace

from test_answer_plan import Script, plan

# 2026-11-22: the judge and its reply-shape tests (_validated, _judge) were replaced by the typed plan. Plan validation
# and repair are tested in test_answer_plan.py; the rules below now run through scripted plans.


# ---------------------------------------------------------------- a database error is a refusal, never a crash

def test_a_database_error_becomes_a_metric_query_error():
    class Broken:
        def sql(self, script):
            raise PilotDatabaseError('psql failed: ERROR:  relation "x" does not exist')
    with pytest.raises(MetricQueryError, match="does not exist"):
        _run_sql(Broken(), "SELECT 1")


# ---------------------------------------------------------------- compiler rules

def test_by_how_many_asks_for_a_difference_not_a_breakdown():
    _refuse_breakdown("By how many units did its orders fall short?")
    _refuse_breakdown("By how much did the value grow?")
    with pytest.raises(ClarificationNeeded):
        _refuse_breakdown("units ordered by product")


def test_a_metric_that_needs_a_date_asks_for_it_instead_of_returning_nothing(answer_pg):
    ws = load_workspace("financial-services")
    with pytest.raises(ClarificationNeeded, match="[Dd]ate"):
        query_metric(ws, "How many customers do we have?", answer_pg)


def _answer(answer_pg, question, chat, dataset="financial-services"):
    ws = load_workspace(dataset)
    prin = {"id": "a", "role": "admin", "datasets": [dataset], "withheld": []}
    return ap.answer(question, ws, prin, database=answer_pg, entity_keys=EntityKeys(ws), chat=chat)


CUSTOMERS = "metric/customers_on_business_date"


def test_a_value_the_plan_names_filters_a_metric_that_allows_that_dimension(answer_pg):
    asked = "How many corporate customers were on the books on 6 November 2026?"
    result = _answer(answer_pg, asked, Script([plan(measure=CUSTOMERS, time=["D1"],
                                                    filters=[{"field": "field/Customers/CustomerType", "value": "Corporate"}])]))
    assert result["status"] == "answered" and result["facts"][0]["value"] == 12 and result["conclusion"] == 12
    assert result["facts"][0]["filters"] == {"BusinessDate": "2026-11-06", "CustomerType": "Corporate"}
    assert any(d.endswith("field/Customers/CustomerType") for d in result["definitions"])
    total = _answer(answer_pg, "How many customers were on the books on 6 November 2026?", Script([plan(measure=CUSTOMERS, time=["D1"])]))
    assert total["facts"][0]["value"] > 12 and "CustomerType" not in total["facts"][0]["filters"]


def test_a_stated_customer_filters_a_metric_on_its_own_table(answer_pg):
    asked = ("Suspicious transaction report SYN-STR-0013 lists one account for customer CUST00013. "
             "How many active accounts does the bank record for that customer on 6 November 2026?")
    result = _answer(answer_pg, asked, Script([plan(measure="metric/active_accounts", entities=["E1"], time=["D1"])]))
    assert result["facts"][0]["definition"].endswith("metric/active_accounts") and result["facts"][0]["value"] == 3, result["reason"]


def test_a_clarification_names_the_measure_the_question_lacks_a_parameter_for(answer_pg):
    result = _answer(answer_pg, "How many customers do we have?", Script([plan(measure=CUSTOMERS)]))
    assert result["status"] == "clarify" and "business date" in result["reason"]
    assert result["definitions"] == ["financial-services:definition:finance_workbook_demo/metric/customers_on_business_date"]
    assert not result["facts"] and not result["citations"] and result["conclusion"] is None


class _Index:
    def __init__(self, text):
        self.row = {"chunk_id": "c1", "source_id": "s1", "text": text, "content_hash": "h"}

    def search(self, datasets, query, vector, exclude_sources=(), limit=20):
        return {"rows": [self.row], "mode": "keyword"}

    def read(self, dataset, source_id):
        return [self.row]


def test_a_sentence_in_a_script_the_question_does_not_use_is_offered_from_a_retrieved_chunk():
    from types import SimpleNamespace
    english = "".join(f"The payment date {n} was asked about by the partner.\n\n" for n in range(14))
    text = english + "请在10月31日之前汇出美元货款。\n\nThank you."
    ctx = SimpleNamespace(question="By what date was the payment asked?", chunks=_Index(text), embedder=None, notes=[],
                          principal={"withheld": []}, links=None, dataset="d", ek=SimpleNamespace(keys={}), ws={"sources": [{"id": "s1", "title": "Call"}]})
    clauses, _ = ap._clauses(ctx, ctx.question, [])
    assert any("10月31日" in c["text"] for c in clauses)


# ---------------------------------------------------------------- Output A: a form field link keeps the chunk's entity keys

def test_a_template_link_keeps_the_entity_keys_the_model_record_of_its_chunk_found():
    from semantic_pipeline_v2.physical_mapping import merge_links
    keys = [{"key": "Customers.CustomerID=CUST00003", "how": "stated"}]
    model = [{"chunk_id": "c1", "definition_id": None, "relation": "none", "entity_key": "Customers.CustomerID=CUST00003",
              "entity_keys": keys}]
    template = [{"chunk_id": "c1", "definition_id": "d", "relation": "states_value_of", "entity_key": "unresolved",
                 "entity_keys": []}, {"chunk_id": "c2", "definition_id": "d", "relation": "states_value_of",
                                      "entity_key": "unresolved", "entity_keys": []}]
    merged = merge_links(model, template)
    assert [(k["chunk_id"], k["entity_keys"]) for k in merged] == [("c1", keys), ("c2", [])]
    assert merged[0]["entity_key"] == "Customers.CustomerID=CUST00003" and merged[1]["entity_key"] == "unresolved"


def test_a_sentence_that_holds_the_identifier_the_question_gives_outranks_sentences_that_only_share_words():
    from types import SimpleNamespace
    words_only = "".join(f"The customer is recorded as a corporate or individual customer in segment {n}.\n\n" for n in range(14))
    text = words_only + 'form/Identification[1]/IdNo[1] = "CUST00003"\n\nOther text here.'
    ctx = SimpleNamespace(question="Is CUST00003 recorded as corporate or individual?", chunks=_Index(text), embedder=None,
                          notes=[], principal={"withheld": []}, links=None, dataset="d", ek=SimpleNamespace(keys={}, row=lambda *a: None),
                          ws={"sources": [{"id": "s1", "title": "Form"}]})
    key = {"table": "Customers", "column": "CustomerID", "value": "CUST00003", "how": "stated"}
    clauses, _ = ap._clauses(ctx, ctx.question, [key])
    assert "CUST00003" in clauses[0]["text"]
