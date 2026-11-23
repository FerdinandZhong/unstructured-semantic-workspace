"""Step 6, contract C4: the Ask router (plan T9). Routing is by code rules; no model decides the tool."""

import pytest

from semantic_pipeline_v2.ask_router import route

FORMS = [{"id": "fs:source:cleaned_pdf_1", "title": "customer-form1.pdf"},
         {"id": "fs:source:cleaned_pdf_2", "title": "customer-form2.pdf"}]


def ctx(**extra):
    return {"lastAnswer": None, "forms": FORMS, **extra}


@pytest.mark.parametrize("text,target", [
    ("write this to Iceberg", "iceberg_cde"),
    ("Save this result as a table", "postgres"),
    ("export these rows into the lakehouse", "iceberg_cde"),
    ("store the answer in a new table", "postgres"),
])
def test_write_verbs_with_a_lakehouse_or_table_word_route_to_write_for_the_last_answer(text, target):
    out = route(text, ctx(lastAnswer="h1"))
    assert out == {"tool": "write", "args": {"source": {"kind": "answer", "handle": "h1"}, "target": target}}


def test_a_named_form_wins_over_the_last_answer():
    out = route("load the suspicious transaction report from customer-form1 into the lakehouse", ctx(lastAnswer="h1"))
    assert out == {"tool": "write", "args": {"source": {"kind": "form", "sourceId": "fs:source:cleaned_pdf_1"},
                                             "target": "iceberg_cde"}}


def test_a_recently_cited_form_is_used_when_no_answer_is_held():
    out = route("write it to iceberg", ctx(lastForm="fs:source:cleaned_pdf_2"))
    assert out["args"]["source"] == {"kind": "form", "sourceId": "fs:source:cleaned_pdf_2"}


def test_write_without_anything_to_write_still_routes_to_write_with_no_source():
    assert route("write this to Iceberg", ctx()) == {"tool": "write", "args": {"source": None, "target": "iceberg_cde"}}


@pytest.mark.parametrize("text,term", [
    ("What does facility mean?", "facility"),
    ("what is meant by beneficial owner", "beneficial owner"),
    ("Define risk rating", "risk rating"),
    ("meaning of STR", "STR"),
])
def test_definition_questions_route_to_semantic_with_the_term(text, term):
    assert route(text, ctx()) == {"tool": "semantic", "args": {"term": term}}


@pytest.mark.parametrize("text", ["show the connections", "Which target is configured?", "test the connection"])
def test_connection_words_route_to_connections(text):
    assert route(text, ctx()) == {"tool": "connections", "args": {}}


@pytest.mark.parametrize("text", [
    "How many corporate customers were there on 6 November 2026?",
    "Which table holds the transactions?",            # a question about a table is not a write
    "How many rows does the load table hold?",
    "",
    "hello",
])
def test_an_unclear_or_ordinary_text_goes_to_answer(text):
    assert route(text, ctx()) == {"tool": "answer", "args": {"question": text.strip()}}


def test_polite_and_create_phrasings_reach_the_write_tool():
    """Seen on the live router on 22 November 2026: these three went to the answer tool."""
    from semantic_pipeline_v2.ask_router import route
    context = {"lastAnswer": "h1"}
    for text in ("can you write this to Iceberg?", "could you please save this result as a table",
                 "create a new table from this result", "please make a table of these rows in the lakehouse"):
        assert route(text, context)["tool"] == "write", text
    assert route("can you write this to Iceberg?", context)["args"]["target"] == "iceberg_cde"
    assert route("create a new table from this result", context)["args"]["target"] == "postgres"
    # a real question that merely mentions a table or a write is still a question
    for text in ("can you tell me how many orders were placed in 2023?", "which table holds the order date?",
                 "how many rows were written to the orders table?"):
        assert route(text, context)["tool"] == "answer", text
