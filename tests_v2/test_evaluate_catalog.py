"""Scorer for Output B (E6). The gold, anchors and catalog below are hand-written fixtures in the documented format,
not the held-out set."""

import json

import pytest

D1 = "manufacturing:definition:m/metric/ordered_units"
D2 = "manufacturing:definition:m/metric/delivery_days"
D3 = "manufacturing:definition:m/field/Sales/Region"


@pytest.fixture()
def ev():
    from semantic_pipeline_v2 import evaluate_catalog
    return evaluate_catalog


def link(definition, predicate):
    return {"definition_id": definition, "predicate": predicate}


def row(gid, label, definition, links, anchor, dataset="manufacturing"):
    return {"id": gid, "dataset": dataset, "concept_label": label, "concept_definition": definition,
            "expected_links": [{"definition": d, "predicate": p} for d, p in links], "supporting_anchor_ids": [anchor]}


GOLD = [
    row("g1", "Minimum purchase commitment", "A customer promise to order at least a minimum quantity of units each "
        "year.", [(D1, "relatedMatch")], "a1"),
    row("g2", "Delivery promise", "A supplier promise to deliver within a stated number of days.",
        [(D2, "closeMatch"), (D3, "broadMatch")], "a2"),
    row("g3", "Warranty period", "Months of cover after delivery.", [], "a3"),            # anchor text not in any chunk
    row("g4", "Acceptance window", "Time allowed to accept delivered goods.", [], "a4"),
    row("g5", "Payment terms", "When and how the buyer pays an invoice.", [], "a5"),     # only the broad concept holds a5
    row("g6", "Contract party", "A person or organisation named in a contract.", [], "a6"),
    row("x1", "Other dataset", "Not scored here.", [], "a1", dataset="financial-services"),
]
ANCHORS = [{"id": a, "dataset": "manufacturing", "passage": p} for a, p in [
    ("a1", "order at least 71 units"), ("a2", "deliver within 14 days"), ("a3", "twelve months of guarantee"),
    ("a4", "accepts within 5 days"), ("a5", "within 5 days of arrival"), ("a6", "the customer commits")]]
EVERY = {"c1": "The customer commits to order at least 71 units each year.",
         "c2": "The supplier shall deliver within 14 days of the order.",
         "c3": "Goods carry twelve months of warranty cover.",
         "c4": "Buyer accepts within 5 days of arrival."}
CATALOG = {"dataset": "manufacturing", "chunks": EVERY, "concepts": [
    {"id": "doc:min", "label": "Purchase minimum", "definition": "A customer's promise of a minimum order quantity "
     "of units per year.", "synonyms": [], "evidence": {"chunks": ["c1"]}, "links": [link(D1, "closeMatch")]},
    {"id": "doc:del", "label": "Delivery promise", "definition": "A supplier promises delivery within days.",
     "synonyms": [], "evidence": {"chunks": ["c2"]}, "links": [link(D2, "closeMatch"), link(D1, "relatedMatch")]},
    {"id": "doc:war", "label": "Warranty", "definition": "Months of cover after delivery.",
     "synonyms": ["warranty period"], "evidence": {"chunks": ["c3"]}, "links": []},
    {"id": "doc:acc", "label": "Acceptance", "definition": "Period in which the buyer accepts delivered goods.",
     "synonyms": [], "evidence": {"chunks": ["c4"]}, "links": [link(D3, "relatedMatch")]},
    # the absorbing concept: broad, its evidence holds every anchor, its meaning matches only one gold concept
    {"id": "doc:party", "label": "Party", "definition": "Any person or organisation named in a contract.",
     "synonyms": [], "evidence": {"chunks": ["c1", "c2", "c3", "c4"]}, "links": []}]}


def test_matching_uses_anchor_candidates_then_similarity_with_a_minimum(ev):
    r = ev.score(GOLD, ANCHORS, CATALOG)
    assert r["gold_concepts"] == 6 and r["covered"] == 4 and r["coverage"] == pytest.approx(0.667, abs=1e-3)
    assert r["unmatched"] == ["g3", "g5"]                         # g3: no anchor text; g5: only the broad concept
    assert r["matched_concept"] == {"g1": "doc:min", "g2": "doc:del", "g4": "doc:acc", "g6": "doc:party"}
    assert "anchor" in r["matching_rule"] and "minimum" in r["matching_rule"]
    assert r["similarity_minimum"] == {"label_words": 0.5, "definition_words": 0.25, "embedding": 0.8}


def test_a_broad_concept_does_not_absorb_unrelated_gold_concepts(ev):
    r = ev.score(GOLD, ANCHORS, CATALOG)
    assert "g5" in r["unmatched"] and "g1" in r["matched_concept"] and r["matched_concept"]["g1"] != "doc:party"


def test_anchor_free_coverage_is_reported_separately(ev):
    r = ev.score(GOLD, ANCHORS, CATALOG)
    free = r["coverage_by_similarity_alone"]
    assert free["covered"] == 5 and free["coverage"] == pytest.approx(0.833, abs=1e-3) and free["unmatched"] == ["g5"]


def test_link_scores_per_predicate_and_agnostic(ev):
    r = ev.score(GOLD, ANCHORS, CATALOG)
    agnostic = r["links"]["predicate_agnostic"]
    # gold: D1, D2, D3. Predicted on matched concepts: D1, D2, D1, D3. Right definitions: D1, D2.
    assert (agnostic["gold"], agnostic["predicted"], agnostic["correct"]) == (3, 4, 2)
    assert agnostic["precision"] == pytest.approx(0.5) and agnostic["recall"] == pytest.approx(2 / 3, abs=1e-3)
    close = r["links"]["by_predicate"]["closeMatch"]
    assert (close["gold"], close["predicted"], close["correct"]) == (1, 2, 1)
    related = r["links"]["by_predicate"]["relatedMatch"]
    assert (related["gold"], related["predicted"], related["correct"]) == (1, 2, 0)
    broad = r["links"]["by_predicate"]["broadMatch"]
    assert broad["predicted"] == 0 and broad["precision"] is None and broad["recall"] == 0.0


def test_unlinked_share_of_no_counterpart_concepts(ev):
    r = ev.score(GOLD, ANCHORS, CATALOG)["no_counterpart"]
    assert r == {"gold": 4, "covered": 2, "kept_unlinked": 1, "share_of_covered": 0.5, "unmatched": 2}


def test_an_embedder_can_rescue_a_match_that_words_miss_and_is_optional(ev):
    def embed(texts):   # hand-written double: the payment gold text and the party concept text point the same way
        return [[1.0, 0.0] if t.startswith(("Payment terms", "Party")) else [0.0, 1.0] for t in texts]
    without = ev.score(GOLD, ANCHORS, CATALOG)
    with_embed = ev.score(GOLD, ANCHORS, CATALOG, embed=embed)
    assert "g5" in without["unmatched"] and with_embed["matched_concept"]["g5"] == "doc:party"
    assert with_embed["embedding"] == "used" and without["embedding"] == "not used"


def test_other_dataset_rows_are_ignored_and_empty_catalog_scores_zero(ev):
    r = ev.score(GOLD, ANCHORS, {**CATALOG, "concepts": []})
    assert r["gold_concepts"] == 6 and r["covered"] == 0 and r["links"]["predicate_agnostic"]["recall"] == 0.0
    assert r["links"]["predicate_agnostic"]["precision"] is None


def test_cli_reads_files_and_prints_json(ev, tmp_path, capsys):
    paths = {}
    for name, rows in (("gold", GOLD), ("anchors", ANCHORS)):
        paths[name] = tmp_path / f"{name}.jsonl"
        paths[name].write_text("\n".join(json.dumps(r) for r in rows))
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps(CATALOG))
    ev.main(["--gold", str(paths["gold"]), "--catalog", str(catalog), "--anchors", str(paths["anchors"])])
    assert json.loads(capsys.readouterr().out)["covered"] == 4
