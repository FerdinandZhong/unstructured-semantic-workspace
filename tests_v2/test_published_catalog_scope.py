"""Found by the paper review on 22 November 2026: `/api/catalog` hides a concept's synonyms when part of its evidence
is withheld (P8f), but answers read the release directly and kept such a concept, with its synonyms and label, for a
restricted reader. A concept with any withheld evidence passage is now left out of answer-time matching."""
from semantic_pipeline_v2 import answer_path, concept_catalog


class Releases:
    def history(self, dataset):
        return [{"version": 1, "catalogVersion": "cat-x"}]

    def document(self, dataset, version):
        return "release-text"


def concepts():
    refs = [{"chunkId": "c1", "sourceId": "ds:source:open"}, {"chunkId": "c2", "sourceId": "ds:source:withheld"}]
    return [{"id": "mixed", "label": "Delivery slip", "status": "corrected", "evidence": {"chunks": ["c1", "c2"], "refs": refs}},
            {"id": "open", "label": "Facility", "status": "corrected", "evidence": {"chunks": ["c1"], "refs": refs[:1]}},
            {"id": "model-only", "label": "Account", "status": "corrected", "evidence": {"chunks": [], "refs": []}}]


def test_a_concept_with_any_withheld_evidence_is_left_out_for_that_reader(monkeypatch):
    monkeypatch.setenv("V2_ASK_CATALOG", "published")
    monkeypatch.setattr(concept_catalog, "catalog_from_ossie", lambda text: concepts())
    answer_path._RELEASES.clear()
    restricted = answer_path.published_catalog(Releases(), "ds", lambda source: source.endswith(":open"))
    assert [c["id"] for c in restricted["concepts"]] == ["open", "model-only"]
    answer_path._RELEASES.clear()
    everyone = answer_path.published_catalog(Releases(), "ds", lambda source: True)
    assert [c["id"] for c in everyone["concepts"]] == ["mixed", "open", "model-only"]
