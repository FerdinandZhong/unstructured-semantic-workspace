"""A concept's `examples` are phrases copied word for word from its evidence chunks, with no record of which chunk.
Found on 22 November 2026 on the rebuilt demo state: a restricted reader's catalog carried a sentence of a withheld
contract in `examples`. When any evidence chunk of a concept is withheld, its examples are not shown."""
from semantic_pipeline_v2.catalog_api import scope_concepts

SOURCE = {"c1": "ds:source:open", "c2": "ds:source:withheld"}


def concept(chunks):
    return {"id": "k", "examples": ["changes from 2024-06-10 to 2024-06-17"], "links": [], "evidence": {"chunks": chunks}}


def test_examples_are_hidden_when_part_of_the_evidence_is_withheld():
    out = scope_concepts([concept(["c1", "c2"])], SOURCE, lambda s: s.endswith(":open"))
    assert out[0]["evidence"]["chunks"] == ["c1"] and out[0]["examples"] == []


def test_examples_stay_when_every_evidence_chunk_is_readable():
    out = scope_concepts([concept(["c1", "c2"])], SOURCE, lambda s: True)
    assert out[0]["examples"] == ["changes from 2024-06-10 to 2024-06-17"]


def induced(chunks):
    return {"id": "k2", "origin": "document", "definition": "Delivery moved from 2024-06-10 to 2024-06-17.",
            "synonyms": ["delivery slip"], "examples": [], "links": [], "evidence": {"chunks": chunks}}


def test_generated_meaning_and_synonyms_are_hidden_when_part_of_the_evidence_is_withheld():
    """A concept found in documents only has a meaning and synonyms written from all its passages, withheld ones too."""
    out = scope_concepts([induced(["c1", "c2"])], SOURCE, lambda s: s.endswith(":open"))[0]
    assert out["synonyms"] == [] and "2024-06-17" not in out["definition"] and out["definition"]
    full = scope_concepts([induced(["c1", "c2"])], SOURCE, lambda s: True)[0]
    assert full["synonyms"] == ["delivery slip"] and "2024-06-17" in full["definition"]


def test_a_model_anchored_meaning_stays_because_it_comes_from_the_model():
    anchored = {**induced(["c1", "c2"]), "origin": "both", "definition": "The promised delivery date of an order."}
    out = scope_concepts([anchored], SOURCE, lambda s: s.endswith(":open"))[0]
    assert out["definition"] == "The promised delivery date of an order." and out["synonyms"] == []
