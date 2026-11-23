"""Output B built on Output A (P3b): E20 anchored part, E21 residual induction and constrained canonicalisation,
E22 keep by vote.

Output A records, model replies and the embedder are hand-written test doubles, not recordings, except
`test_recorded_*`, which replay real replies recorded on 2026-11-21 offline.
"""

import hashlib
import json
from pathlib import Path
import re

import numpy as np
import pytest
import yaml

from semantic_pipeline_v2 import concept_induction as ci
from semantic_pipeline_v2.core import EmbeddingSpace

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "demo_data/manufacturing/semantics/manufacturing.ossie.yaml"


@pytest.fixture()
def cc():
    from semantic_pipeline_v2 import concept_catalog
    return concept_catalog


class WordEmbedder:
    """Hand-written double: hashed bag of words, 64 dimensions, deterministic."""
    space = EmbeddingSpace("fake-bow", "v1", 64, "test")

    def embed(self, texts):
        out = []
        for text in texts:
            v = np.zeros(64)
            for w in re.findall(r"[a-z]+", text.lower()):
                v[int(hashlib.sha256(w.encode()).hexdigest(), 16) % 64] += 1
            out.append((v / max(np.linalg.norm(v), 1e-9)).tolist())
        return out


class Script:
    """Hand-written replies by task name: a dict of task -> reply (dict, or function of the parsed prompt)."""

    def __init__(self, **replies):
        self.replies, self.calls = replies, []

    def complete(self, system, user):
        data = json.loads(user)
        self.calls.append((data["task"], system, data))
        reply = self.replies.get(data["task"], {})
        reply = reply(data) if callable(reply) else reply
        return reply if isinstance(reply, str) else json.dumps(reply)


# ---------------------------------------------------------------- doubles for Output A and the model

D = "m:definition:x/field/"


def card(name, concept=None, role="measure", unit=None, kind="field", description=None, label=None):
    return {"id": D + name, "kind": kind, "name": name, "label": label or name.replace("_", " ").title(),
            "description": description or f"The {name} recorded by the source system for a business entity.",
            "concept": concept, "role": role, "unit": unit, "synonyms": [], "dataset": "T"}


CARDS = {c["id"]: c for c in (
    card("qty", "ordering", "measure", "units"),
    card("channel", "ordering", "dimension"),
    card("order_fk", "sales_order", "identifier"),
    card("cust_pk", "ordering", "identifier"),
    card("ship_method", None, "dimension", description="The way a shipment is sent to the customer."),
    card("ship_date", None, "time", description="The date on which a shipment leaves the warehouse."),
    card("total", None, "measure", "USD", kind="metric", description="The total amount of all orders in a period."),
)}
STRUCT = [{"id": "ordering", "label": "Ordering", "definition": "A customer asks for goods to be supplied.",
           "synonyms": ["buying"], "broader": None},
          {"id": "sales_order", "label": "Sales order", "definition": "A recorded order for goods.",
           "synonyms": [], "broader": None}]


def rec(chunk, name, relation="states_value_of", *, canonical=None, value=None, unit=None, status="proposed",
        source="s1"):
    ident = D + name if name else None
    return {"chunk_id": chunk, "source_id": source, "definition_id": ident,
            "canonical_definition_id": (D + canonical if canonical else ident), "relation": relation,
            "stated_value": value, "unit": unit, "status": status, "entity_keys": []}


ORDER = {f"c{n}": n for n in range(1, 40)}


def anchored(cc, records, stats=None):
    return cc.anchor_concepts(records, CARDS, STRUCT, ORDER, stats=stats)


def by_id(concepts):
    return {c["id"]: c for c in concepts}


# ---------------------------------------------------------------- E20: the anchored part

def test_e20_a_structured_concept_gets_the_chunks_of_its_bound_fields_and_closematch_from_values(cc):
    """Changed 2026-11-22: stated values alone give closeMatch. 'Ordering' and the field 'Qty' share no name."""
    out = anchored(cc, [rec("c1", "qty", value="71", unit="units"), rec("c2", "qty", value="80 units"),
                        rec("c3", "qty", "is_about")])
    (c,) = out
    assert c["id"] == "ordering" and c["origin"] == "both" and c["label"] == "Ordering"
    assert c["synonyms"] == ["buying"] and c["evidence"] == ["c1", "c2", "c3"]
    assert c["examples"] == ["71", "80 units"] and c["type"] == "measure" and c["unit"] == "units"
    (link,) = c["links"]
    assert link["definition_id"] == D + "qty" and link["predicate"] == "closeMatch" and link["status"] == "proposed"
    assert link["support"] == {"states_value_of": 2, "is_about": 1, "unit_conflict": 0, "chunks": ["c1", "c2", "c3"]}


@pytest.mark.parametrize("chunks,status", [(["c1", "c2"], "proposed"), (["c1"], "candidate")])
def test_e20_is_about_only_gives_relatedmatch_and_two_chunks_are_the_minimum_support(cc, chunks, status):
    (c,) = anchored(cc, [rec(k, "channel", "is_about") for k in chunks])
    (link,) = c["links"]
    assert (link["predicate"], link["status"]) == ("relatedMatch", status)
    assert link["support"]["states_value_of"] == 0 and link["support"]["is_about"] == len(chunks)


def test_e20_one_states_value_of_chunk_is_enough_but_a_minority_is_only_relatedmatch(cc):
    (c,) = anchored(cc, [rec("c1", "qty", value="5", unit="units"), rec("c2", "qty", "is_about"),
                         rec("c3", "qty", "is_about")])
    (link,) = c["links"]
    assert (link["predicate"], link["status"]) == ("relatedMatch", "proposed")


def test_e20_a_stated_unit_that_contradicts_the_field_unit_does_not_count_as_a_stated_value(cc):
    (c,) = anchored(cc, [rec("c1", "qty", value="14", unit="days")])
    (link,) = c["links"]
    assert link["support"] == {"states_value_of": 0, "is_about": 1, "unit_conflict": 1, "chunks": ["c1"]}
    assert (link["predicate"], link["status"]) == ("relatedMatch", "candidate")
    assert c["unit"] is None and c["examples"] == []


@pytest.mark.parametrize("field_label,synonyms,predicate", [
    ("Ordering", [], "exactMatch"),                      # the field's label is the concept's label
    ("Order quantity", ["buying"], "exactMatch"),        # a field synonym is a concept synonym
    ("Order due date", ["due date"], "closeMatch"),      # values stated, but no shared name: close, not exact
])
def test_e20_exactmatch_needs_stated_values_and_a_name_the_concept_and_the_field_share(cc, field_label, synonyms,
                                                                                      predicate):
    cards = {**CARDS, D + "qty": {**CARDS[D + "qty"], "label": field_label, "synonyms": synonyms}}
    (c,) = cc.anchor_concepts([rec("c1", "qty", value="5", unit="units"), rec("c2", "qty", value="6", unit="units")],
                              cards, STRUCT, ORDER)
    assert c["links"][0]["predicate"] == predicate


def test_e20_a_unit_stated_against_an_identifier_is_not_a_stated_value(cc):
    """'at least 71 units of ProductNumber X' read as a value of the product number: an identifier has no unit."""
    (c,) = anchored(cc, [rec("c1", "cust_pk", value="71", unit="units")])
    (link,) = c["links"]
    assert link["support"]["states_value_of"] == 0 and link["support"]["unit_conflict"] == 1
    assert link["predicate"] == "relatedMatch"


def test_e20_a_value_stated_through_another_concepts_foreign_key_is_closematch(cc):
    out = by_id(anchored(cc, [rec("c1", "order_fk", value="43659", canonical="cust_pk"),
                              rec("c2", "order_fk", value="43660", canonical="cust_pk")]))
    (link,) = out["ordering"]["links"]
    assert link["definition_id"] == D + "cust_pk" and link["predicate"] == "closeMatch"
    assert link["support"]["states_value_of"] == 2


def test_e20_a_field_without_a_concept_gets_a_document_record_from_its_own_label_and_description(cc):
    out = anchored(cc, [rec("c1", "ship_method", value="air"), rec("c2", "ship_date", value="3 May 2026"),
                        rec("c3", "total", value="12,000", unit="USD")])
    got = {c["label"]: c for c in out}
    assert set(got) == {"Ship Method", "Ship Date", "Total"}
    for c in got.values():   # one passage restating a field is not enough for a concept of its own (2026-11-22)
        assert c["origin"] == "document" and c["id"].startswith("doc:") and c["status"] == "candidate"
        assert c["links"][0]["predicate"] == "exactMatch" and c["synonyms"] == []
    assert got["Ship Method"]["definition"] == CARDS[D + "ship_method"]["description"]
    assert (got["Ship Method"]["type"], got["Ship Date"]["type"], got["Total"]["type"]) == (
        "attribute", "event", "measure")
    assert got["Total"]["unit"] == "USD" and got["Ship Method"]["unit"] is None


def test_e20_a_field_restated_by_two_business_passages_is_proposed_but_public_passages_do_not_count(cc):
    records = [rec("c1", "ship_method", value="air"), rec("c2", "ship_method", value="sea")]
    (c,) = anchored(cc, records)
    assert c["status"] == "proposed"
    (c,) = cc.anchor_concepts(records, CARDS, STRUCT, ORDER, excluded={"c2"})
    assert c["status"] == "candidate" and c["evidence"] == ["c1", "c2"]


@pytest.mark.parametrize("status,relation,name", [("rejected", "states_value_of", "qty"),
                                                  ("degraded", "none", None), ("not_processed", "none", None),
                                                  ("proposed", "none", None)])
def test_e20_rejected_degraded_unprocessed_and_empty_records_contribute_nothing(cc, status, relation, name):
    assert anchored(cc, [rec("c1", name, relation, status=status)]) == []


def test_e20_a_human_correction_counts_like_a_link_and_a_rejection_removes_it(cc):
    out = anchored(cc, [rec("c1", "qty", "is_about", status="corrected"), rec("c2", "qty", "is_about"),
                        rec("c3", "qty", status="rejected", value="9")])
    (c,) = out
    assert c["evidence"] == ["c1", "c2"] and c["links"][0]["support"]["is_about"] == 2


def test_e20_the_anchored_part_calls_no_model(cc, mfg_cards):
    """`build_anchored_catalog` with every chunk anchored and nothing to induce (a templated source): the chat double
    records any call, so none may occur."""
    workspace, cards, ident = mfg_cards
    chat = Script()
    chunks = [{"chunk_id": f"c{n}", "text": f"Passage {n}", "title": "Doc", "source": "s1"} for n in (1, 2)]
    records = [rec("c1", None, source="s1") | {"definition_id": ident, "canonical_definition_id": ident,
                                               "relation": "states_value_of", "stated_value": "7"},
               rec("c2", None, source="s1") | {"definition_id": ident, "canonical_definition_id": ident,
                                               "relation": "states_value_of", "stated_value": "8"}]
    catalog = cc.build_anchored_catalog("manufacturing", chunks, records, workspace, chat, WordEmbedder(),
                                        now="2026-11-21T00:00:00Z", templated={"s1"})
    assert chat.calls == [] and catalog["assignment"] == {"c1": [cards[ident]["concept"]],
                                                         "c2": [cards[ident]["concept"]]}


@pytest.fixture(scope="module")
def mfg_cards():
    from semantic_pipeline_v2.definition_cards import load_cards
    from semantic_pipeline_v2.workspace import load_workspace
    workspace = load_workspace("manufacturing")
    cards = load_cards(workspace)
    ident = next(i for i, c in cards.items() if c["kind"] == "field" and c["concept"])
    return workspace, cards, ident


# ---------------------------------------------------------------- E21: residual chunks

def chunk(n, source="s1", text=None):
    return {"chunk_id": f"c{n}", "text": text or f"Passage {n} says something.", "title": "Doc", "source": source}


def test_e21_every_readable_business_chunk_is_induced_but_templated_and_public_sources_are_not(cc):
    """Changed 2026-11-22: a stated value no longer takes a chunk out of induction. 'The customer commits to order at
    least 71 units of ProductNumber BK-T79Y-46' states a product number and a commitment that no field records."""
    chunks = [chunk(1), chunk(2), chunk(3), chunk(4), chunk(5), chunk(6), chunk(7, source="form"),
              chunk(8, source="public")]
    records = [rec("c1", "qty", value="5", unit="units"),              # states a value: still induced
               rec("c2", "qty", "is_about"),
               rec("c3", None, "none"),
               rec("c4", None, "none", status="degraded"),             # Output A failed; the text is readable
               rec("c5", "qty", "is_about", status="rejected"),
               rec("c6", None, "none", status="not_processed"),
               rec("c7", None, "none", source="form"),                 # a templated source: not induced
               rec("c8", None, "none", source="public")]               # an extraction_only source: not induced
    got, counts = cc.residual_chunks(chunks, records, templated={"form"}, excluded_sources={"public"})
    assert [c["chunk_id"] for c in got] == ["c1", "c2", "c3", "c4", "c5", "c6"]
    assert counts == {"induced": 6, "with_value_link": 1, "degraded": 1, "not_processed": 1, "templated": 1,
                      "extraction_only": 1}


def induce_reply(data, label="Delivery promise"):
    ids = [c["id"] for c in data["chunks"]]
    return {"concepts": [{"label": label, "definition": "A party promises delivery within a stated time.",
                          "synonyms": [], "examples": [], "type": "obligation", "unit": None, "grain": None,
                          "evidence": ids}]}


def test_e21_induction_sees_only_residual_chunks_without_a_model_merge_and_with_the_unit_grain_rule(cc, mfg_cards):
    workspace, cards, ident = mfg_cards
    chunks = [chunk(1, text="Anchored text one."), chunk(2, text="About text two."), chunk(3, text="Free text three."),
              chunk(4, source="form", text="Form field text four.")]
    records = [rec("c1", None) | {"definition_id": ident, "canonical_definition_id": ident, "stated_value": "7"},
               rec("c2", None) | {"definition_id": ident, "canonical_definition_id": ident, "relation": "is_about"},
               rec("c3", None, "none"), rec("c4", None, "none", source="form")]
    chat = Script(induce=induce_reply, same={"same": []}, match={"decisions": []})
    cc.build_anchored_catalog("manufacturing", chunks, records, workspace, chat, WordEmbedder(), templated={"form"},
                              now="2026-11-21T00:00:00Z")
    induced = [data for task, _, data in chat.calls if task == "induce"]
    offered = {c["text"] for data in induced for c in data["chunks"]}
    assert offered == {"Anchored text one.", "About text two.", "Free text three."}
    assert not any(task == "merge" for task, _, _ in chat.calls)           # no model group merge in the residual run
    system = next(s for task, s, _ in chat.calls if task == "induce")
    for words in ("obligation, event, measure, party or attribute", "one unit", "one grain", "one party role",
                  "do not propose a concept that only names such a value"):
        assert words in system


def test_e21_an_is_about_chunk_keeps_a_relatedmatch_to_the_definition_output_a_named(cc, mfg_cards):
    workspace, cards, ident = mfg_cards
    chunks = [chunk(1, text="First about text."), chunk(2, text="Second about text."), chunk(3, text="Free text.")]
    base = {"definition_id": ident, "canonical_definition_id": ident, "relation": "is_about"}
    records = [rec("c1", None) | base, rec("c2", None) | base, rec("c3", None, "none")]
    chat = Script(induce=induce_reply, same={"same": []}, match={"decisions": []})
    catalog = cc.build_anchored_catalog("manufacturing", chunks, records, workspace, chat, WordEmbedder(),
                                        now="2026-11-21T00:00:00Z", sample_seeds=())
    residual = next(r for r in catalog["concepts"] if r["label"] == "Delivery promise")
    (link,) = residual["links"]
    assert link["definition_id"] == ident and link["predicate"] == "relatedMatch"
    assert link["support"] == {"states_value_of": 0, "is_about": 2, "unit_conflict": 0, "chunks": ["c1", "c2"]}
    assert link["status"] == "proposed" and sorted(residual["evidence"]["chunks"]) == ["c1", "c2", "c3"]


def test_e21_a_residual_link_needs_most_of_the_concepts_evidence_to_be_about_that_definition(cc):
    """Induction may give a broad concept many chunks. Two is_about chunks out of six name the definition: candidate."""
    order = {f"c{n}": n for n in range(1, 10)}
    about = {"c1": [D + "qty"], "c2": [D + "qty"], "c3": [D + "channel"]}
    broad = cc.about_links(["c1", "c2", "c3", "c4", "c5", "c6"], about, order)
    assert [(k["definition_id"], k["status"]) for k in broad] == [(D + "qty", "candidate"), (D + "channel", "candidate")]
    focused = cc.about_links(["c1", "c2", "c3"], about, order)
    assert [(k["definition_id"], k["status"]) for k in focused] == [(D + "qty", "proposed"), (D + "channel", "candidate")]


def test_e21_a_residual_concept_merged_into_a_structured_one_adds_chunks_but_no_links(cc, mfg_cards):
    workspace, cards, ident = mfg_cards
    chunks = [chunk(1, text="First free text."), chunk(2, text="Second free text.")]
    base = {"definition_id": ident, "canonical_definition_id": ident, "relation": "is_about"}
    records = [rec("c1", None) | base, rec("c2", None) | base]

    def one(data):
        ids = [c["id"] for c in data["chunks"]]
        return {"concepts": [{"label": "Some promise", "definition": "A promise the documents describe.",
                              "synonyms": [], "examples": [], "type": "obligation", "unit": None, "grain": None,
                              "evidence": ids}]}
    own = next(s["label"] for s in cc.structured_concepts(workspace) if s["id"] == cards[ident]["concept"])
    chat = Script(induce=one, same={"same": []}, match=lambda d: {"decisions": [
        {"id": c["id"], "match": next(k["id"] for k in c["candidates"] if k["label"] != own), "reason": "r"}
        for c in d["concepts"]]})
    catalog = cc.build_anchored_catalog("manufacturing", chunks, records, workspace, chat, WordEmbedder(),
                                        now="2026-11-21T00:00:00Z")
    target = next(r for r in catalog["concepts"] if "Some promise" in r["synonyms"])
    assert target["origin"] == "both" and target["id"] != cards[ident]["concept"]
    assert target["links"] == [] and sorted(target["evidence"]["chunks"]) == ["c1", "c2"]


# ---------------------------------------------------------------- E21: constrained canonicalisation

def rc(cid, label, ctype="obligation", unit=None, defs=(), evidence=("c1",), definition=None):
    return {"id": cid, "label": label, "definition": definition or f"{label}: a business concept in the documents.",
            "synonyms": [], "examples": [], "type": ctype, "unit": unit, "grain": None, "defs": list(defs),
            "evidence": list(evidence), "origin": "document", "status": "proposed"}


def confirm_all(data):
    return {"same": [list(p) for p in data["pairs"]]}


@pytest.mark.parametrize("a,b", [(dict(ctype="obligation"), dict(ctype="measure")),
                                 (dict(defs=[D + "qty"]), dict(defs=[D + "channel"]))])
def test_e21_a_different_type_label_or_about_definition_is_asked_not_refused(cc, a, b):
    """Changed 2026-11-22: the induction model applies the type loosely, and Output A's about-links describe the
    chunks, not the concept. Neither refuses a pair; the dedup model sees the type and decides."""
    x, y = rc("doc:a", "Delivery promise", **a), rc("doc:b", "Delivery promise terms", **b, evidence=("c2",))
    assert cc.signature_conflict(x, y) is None
    chat = Script(same=confirm_all)
    out = cc.canonicalise_constrained([x, y], chat, WordEmbedder(), ["c1", "c2"], stats={}, fates={})
    assert len(out) == 1 and [t for t, _, _ in chat.calls] == ["same"]


def test_e21_each_concepts_nearest_neighbours_are_offered_even_below_the_similarity_threshold(cc):
    class Fixed:   # hand-written double: cosine 0.88 between the first two texts, 0.0 to the third
        space = WordEmbedder.space

        def embed(self, texts):
            rows = {0: [1.0, 0.0, 0.0], 1: [0.88, 0.475, 0.0], 2: [0.0, 0.0, 1.0]}
            return [rows[n] for n in range(len(texts))]
    a = rc("doc:a", "Transaction limit covenant", evidence=("c1",))
    b = rc("doc:b", "Maximum single payment cap", evidence=("c2",))
    c = rc("doc:c", "Fee schedule", evidence=("c3",))
    chat = Script(same={"same": []})
    cc.canonicalise_constrained([a, b, c], chat, Fixed(), ["c1", "c2", "c3"], stats={}, fates={})
    (call,) = chat.calls
    labels = {x["id"]: x["label"] for x in call[2]["concepts"]}
    assert [sorted(labels[i] for i in p) for p in call[2]["pairs"]] == [
        ["Maximum single payment cap", "Transaction limit covenant"]]


def test_e21_an_earlier_concept_heads_its_group_and_two_earlier_concepts_are_never_paired(cc):
    old1 = rc("doc:old", "Delivery promise", evidence=("c1",))
    old2 = rc("doc:old2", "Delivery promise terms", evidence=("c2",))
    new = rc("doc:new", "Delivery promise", evidence=("c3", "c4"))
    chat = Script(same=confirm_all)
    out = cc.canonicalise_constrained([old1, old2, new], chat, WordEmbedder(), ["c1", "c2", "c3", "c4"], stats={},
                                      fates={}, keep={"doc:old", "doc:old2"})
    ids = {c["id"]: c["evidence"] for c in out}
    assert ids == {"doc:old": ["c1", "c3", "c4"], "doc:old2": ["c2"]}
    asked = [frozenset(p) for _, _, data in chat.calls for p in data["pairs"]]
    assert len(asked) == 1     # old2 and new only; old and new share a label and are confirmed without the model


@pytest.mark.parametrize("a,b,reason", [
    (dict(unit="days"), dict(unit="hours"), "unit"),
])
def test_e21_signatures_that_conflict_are_never_merged_and_never_asked(cc, a, b, reason):
    x, y = rc("doc:a", "Delivery promise", **a), rc("doc:b", "Delivery promise terms", **b, evidence=("c2",))
    assert cc.signature_conflict(x, y) == reason
    chat, stats = Script(same=confirm_all), {}
    out = cc.canonicalise_constrained([x, y], chat, WordEmbedder(), ["c1", "c2"], stats=stats, fates={})
    assert len(out) == 2 and chat.calls == []
    assert stats["merges_refused_by_signature"] == 1


def test_e21_two_party_roles_conflict_and_an_unstated_role_does_not(cc):
    supplier, customer = rc("doc:a", "Supplier delivery promise"), rc("doc:b", "Customer delivery promise")
    assert cc.signature_conflict(supplier, customer) == "role"
    assert cc.signature_conflict(supplier, rc("doc:c", "Delivery promise")) is None
    assert cc.signature_conflict(rc("doc:d", "Delivery promise", unit="days"), rc("doc:e", "Delivery promise")) is None
    assert cc.signature_conflict(rc("doc:f", "Delivery", defs=[D + "qty"]), rc("doc:g", "Delivery")) is None


def test_e21_concepts_that_differ_only_in_wording_are_merged_when_the_model_confirms(cc):
    x = rc("doc:a", "Purchase commitment", evidence=("c1",))
    y = rc("doc:b", "Customer purchase commitment", evidence=("c2",))
    chat, stats, fates = Script(same=confirm_all), {}, {}
    (out,) = cc.canonicalise_constrained([x, y], chat, WordEmbedder(), ["c1", "c2"], stats=stats, fates=fates)
    assert out["evidence"] == ["c1", "c2"] and stats["merges_refused_by_signature"] == 0
    assert list(fates.values())[0]["fate"] == "merged" and chat.calls[0][0] == "same"


def trio(cc, third, confirmed):
    a = rc("doc:a", "Delivery promise", evidence=("c1",))
    b = rc("doc:b", "Delivery promise terms", evidence=("c2", "c3"))
    c = rc("doc:c", third, evidence=("c4",))
    alias = {}

    def reply(data):
        names = {x["id"]: x["label"] for x in data["concepts"]}
        return {"same": [list(p) for p in data["pairs"]
                         if frozenset(names[i] for i in p) in confirmed]}
    out = cc.canonicalise_constrained([a, b, c], Script(same=reply), WordEmbedder(), ["c1", "c2", "c3", "c4"],
                                      stats={}, fates={})
    return {frozenset(x["evidence"]) for x in out}


@pytest.mark.parametrize("third", ["Promise terms", "Delivery promise terms extra"])
def test_e21_a_chain_of_confirmed_pairs_is_not_a_group(cc, third):
    """A-B and B-C are confirmed; A-C is not offered or not confirmed. The group needs every pair."""
    confirmed = {frozenset(("Delivery promise", "Delivery promise terms")),
                 frozenset(("Delivery promise terms", third))}
    groups = trio(cc, third, confirmed)
    assert groups == {frozenset(("c1", "c2", "c3")), frozenset(("c4",))}


def test_e21_a_complete_group_is_merged_into_one_concept(cc):
    third = "Delivery promise terms extra"
    confirmed = {frozenset(("Delivery promise", "Delivery promise terms")),
                 frozenset(("Delivery promise terms", third)), frozenset(("Delivery promise", third))}
    assert trio(cc, third, confirmed) == {frozenset(("c1", "c2", "c3", "c4"))}


def test_e21_every_fate_is_recorded_and_the_counts_add_up(cc, mfg_cards):
    workspace, cards, ident = mfg_cards
    chunks = [chunk(1, text="Free text one."), chunk(2, text="Free text two."), chunk(3, text="Free text three.")]
    records = [rec(f"c{n}", None, "none") for n in (1, 2, 3)]

    def two(data):
        ids = [c["id"] for c in data["chunks"]]
        mk = lambda label, e: {"label": label, "definition": f"{label} in the documents.", "synonyms": [],  # noqa: E731
                               "examples": [], "type": "obligation", "unit": None, "grain": None, "evidence": [e]}
        return {"concepts": [mk("Purchase commitment", ids[0]), mk("Customer purchase commitment", ids[1])]}
    chat = Script(induce=two, same=confirm_all, match={"decisions": []})
    catalog = cc.build_anchored_catalog("manufacturing", chunks, records, workspace, chat, WordEmbedder(),
                                        now="2026-11-21T00:00:00Z", sample_seeds=())
    fates, rows = catalog["stats"]["fates"], catalog["fates"]
    assert catalog["stats"]["concepts_total"] == len(rows) == fates["kept"] + fates["merged"] + fates["dropped"] == 2
    assert fates == {"kept": 1, "merged": 1, "dropped": 0}


# ---------------------------------------------------------------- E22: keep by vote

def test_e22_vote_counts_the_runs_that_find_a_concept_with_the_stability_matcher(cc):
    full = [rc("doc:a", "Delivery promise", evidence=("c1", "c2")),
            rc("doc:b", "Warranty term", evidence=("c3", "c4"), definition="How long the goods are covered.")]
    run1 = [rc("doc:x", "Delivery promise", evidence=("c1", "c2")),
            rc("doc:y", "Other", evidence=("c3",), definition="Something unrelated here.")]
    run2 = [rc("doc:z", "Delivery promise", evidence=("c1",))]
    got = cc.vote(full, full, [(run1, {"c1", "c2", "c3", "c4"}), (run2, {"c1", "c2"})])
    assert got == {"doc:a": 3, "doc:b": 1}


def test_e22_a_broad_concept_is_found_by_a_narrower_one_on_its_chunks_and_a_renamed_one_by_its_definition(cc):
    """Changed 2026-11-22. A six-passage concept and a run's one-passage concept on one of them overlap by the smaller
    set, not by Jaccard. A run that names the same clause differently is matched by the definition words."""
    broad = rc("doc:a", "Banking facility agreement", evidence=("c1", "c2", "c3", "c4", "c5", "c6"),
               definition="The agreement under which the bank makes a credit facility available.")
    renamed = rc("doc:b", "Lead-time promise", evidence=("c7",),
                 definition="The supplier promises receipt within 14 calendar days after order acceptance.")
    run = [rc("doc:x", "Banking facility limit", evidence=("c2",), definition="Maximum amount of the facility."),
           rc("doc:y", "Delivery promise", evidence=("c7",),
              definition="The supplier promises receipt within 14 calendar days after acceptance of the order.")]
    keep = {f"c{n}" for n in range(1, 8)}
    assert cc.vote([broad, renamed], [broad, renamed], [(run, keep)]) == {"doc:a": 2, "doc:b": 2}
    other = [rc("doc:z", "Fee schedule", evidence=("c7",), definition="Charges for account services and cards.")]
    assert cc.vote([renamed], [renamed], [(other, keep)]) == {"doc:b": 1}


def test_e22_a_merged_concept_is_found_when_any_member_is_found(cc):
    raw = [rc("doc:a", "Delivery promise", evidence=("c1",)), rc("doc:b", "Shipping pledge", evidence=("c2",))]
    fates = {"doc:b": {"fate": "merged", "into": "doc:a"}}
    run = [rc("doc:y", "Shipping pledge", evidence=("c2",))]
    assert cc.vote([raw[0]], raw, [(run, {"c1", "c2"})], fates=fates) == {"doc:a": 2}


def flaky(data):
    """Always finds 'Stable promise'; finds 'Full run only' only when it is offered all ten residual chunks."""
    ids = [c["id"] for c in data["chunks"]]
    mk = lambda label: {"label": label, "definition": f"{label} as the documents describe it.", "synonyms": [],  # noqa: E731
                        "examples": [], "type": "obligation", "unit": None, "grain": None, "evidence": ids}
    return {"concepts": [mk("Stable promise")] + ([mk("Full run only")] if len(ids) == 10 else [])}


def vote_catalog(cc, mfg_cards):
    workspace, *_ = mfg_cards
    chunks = [chunk(n, text=f"Free passage number {n}.") for n in range(1, 11)]
    records = [rec(f"c{n}", None, "none") for n in range(1, 11)]
    chat = Script(induce=flaky, same={"same": []}, match={"decisions": []})
    return cc.build_anchored_catalog("manufacturing", chunks, records, workspace, chat, WordEmbedder(),
                                     now="2026-11-21T00:00:00Z")


def test_e22_found_in_two_runs_is_proposed_found_once_is_candidate_and_the_shares_are_reported(cc, mfg_cards):
    catalog = vote_catalog(cc, mfg_cards)
    status = {r["label"]: r["status"] for r in catalog["concepts"]}
    assert status == {"Stable promise": "proposed", "Full run only": "candidate"}
    assert catalog["votes"] == {r["id"]: n for r, n in zip(catalog["concepts"], (4, 1))}
    stats = catalog["stats"]
    assert stats["residual_concepts"] == {"proposed": 1, "candidate": 1, "share_proposed": 0.5, "share_candidate": 0.5}
    assert stats["vote_runs"] == 4


def test_e22_a_candidate_stays_in_the_catalog_and_the_store_but_is_not_exported(cc, mfg_cards, tmp_path):
    catalog = vote_catalog(cc, mfg_cards)
    assert any(r["status"] == "candidate" for r in catalog["concepts"])
    shown = cc.exportable(catalog)
    assert [r["label"] for r in shown["concepts"]] == ["Stable promise"]
    text = cc.export_ossie(MODEL.read_text(), shown)
    assert "Full run only" not in text and "Stable promise" in text
    assert cc.verify_export(MODEL.read_text(), text, shown) == {
        "schema": True, "lint": [], "structured_identical": True, "reimport_equal": True}
    import os
    if os.environ.get("V2_PG_BIN"):
        from semantic_pipeline_v2._pilot_postgres import PilotPostgres
        from semantic_pipeline_v2.semantic_store import SemanticStore
        with PilotPostgres.start(Path(os.environ["V2_PG_BIN"])) as database:
            store = SemanticStore(database)
            store.write_catalog("manufacturing", catalog["version"], catalog["concepts"])
            got = {r["label"]: r["status"] for r in store.read_catalog("manufacturing", catalog["version"])}
            assert got == {"Stable promise": "proposed", "Full run only": "candidate"}


# ---------------------------------------------------------------- quantified single-clause promise (hand-written doubles)

def about(chunk, name, value=None, unit=None, period=None):
    return rec(chunk, name, "is_about", value=value, unit=unit) | {"period": period}


def test_quantified_single_clause_is_proposed_and_names_its_rule(cc):
    for kw in ({"unit": "units"}, {"period": "per month"}):
        (c,) = anchored(cc, [about("c1", "qty", value="500", **kw)])
        (link,) = c["links"]
        assert (link["predicate"], link["status"], link["rule"]) == ("relatedMatch", "proposed", "quantified_single_clause")


@pytest.mark.parametrize("record", [about("c1", "qty"), about("c1", "qty", value="500"), about("c1", "qty", unit="units")])
def test_unquantified_single_clause_stays_candidate(cc, record):
    (link,) = anchored(cc, [record])[0]["links"]
    assert link["status"] == "candidate" and link["rule"] is None


def test_two_unquantified_clauses_are_proposed_as_before_and_the_rule_is_named(cc):
    (link,) = anchored(cc, [about("c1", "qty"), about("c2", "qty")])[0]["links"]
    assert link["status"] == "proposed" and link["rule"] == "min_about"


def test_residual_concept_gets_a_proposed_link_from_its_own_quantified_clause_but_a_broad_one_does_not_from_unquantified(cc):
    order = {f"c{n}": n for n in range(1, 10)}
    about_map = {"c1": [D + "qty"], "c2": [D + "channel"]}
    broad = cc.about_links(["c1", "c2", "c3", "c4", "c5", "c6"], about_map, order)
    assert all(k["status"] == "candidate" and k["rule"] is None for k in broad)
    own = cc.about_links(["c1", "c3", "c4", "c5", "c6"], about_map, order, quantified={("c1", D + "qty")})
    assert {k["definition_id"]: (k["status"], k["rule"]) for k in own} == {D + "qty": ("proposed", "quantified_single_clause")}
    focused = cc.about_links(["c1", "c2", "c3"], {"c1": [D + "qty"], "c2": [D + "qty"], "c3": [D + "qty"]}, order)
    assert focused[0]["status"] == "proposed" and focused[0]["rule"] == "more_than_half"


def test_quantified_is_about_pairs_come_from_output_a_records(cc):
    pairs = cc.quantified_about([about("c1", "qty", value="5", unit="units"), about("c2", "qty", value="5"),
                                 rec("c3", "qty", value="5", unit="units")], CARDS)
    assert pairs == {("c1", D + "qty")}


def test_the_newly_proposed_link_is_exported(cc, mfg_cards):
    workspace, cards, ident = mfg_cards
    catalog = {"dataset": "manufacturing", "version": "v", "concepts": [
        {"id": "doc:x", "label": "X thing", "definition": "A thing the documents describe at length.", "synonyms": [],
         "examples": [], "origin": "document", "type": "attribute", "unit": None, "grain": None,
         "links": cc.about_links(["c1"], {"c1": [ident]}, ORDER, quantified={("c1", ident)}),
         "evidence": {"catalog_version": "v", "chunks": []}, "status": "proposed", "actor": "a", "time": "t",
         "broad": False, "level": "coarse", "parent": None}]}
    (record,) = cc.exportable(catalog)["concepts"]
    assert [l["predicate"] for l in record["links"]] == ["relatedMatch"]


def test_e22_candidate_links_of_an_exported_concept_are_not_exported(cc, mfg_cards):
    workspace, cards, ident = mfg_cards
    catalog = {"dataset": "manufacturing", "version": "v", "concepts": [
        {"id": "doc:x", "label": "X thing", "definition": "A thing the documents describe at length.", "synonyms": [],
         "examples": [], "origin": "document", "type": "attribute", "unit": None, "grain": None,
         "links": [{"definition_id": ident, "predicate": "relatedMatch", "reason": "r", "status": "candidate"},
                   {"definition_id": ident, "predicate": "exactMatch", "reason": "r", "status": "proposed"}],
         "evidence": {"catalog_version": "v", "chunks": []}, "status": "proposed", "actor": "a", "time": "t",
         "broad": False, "level": "coarse", "parent": None}]}
    (record,) = cc.exportable(catalog)["concepts"]
    assert [l["predicate"] for l in record["links"]] == ["exactMatch"]
    assert len(catalog["concepts"][0]["links"]) == 2                       # the catalog itself is unchanged


# ---------------------------------------------------------------- helpers of the induction step

def test_induce_can_skip_the_model_merge_and_append_a_prompt_addendum():
    class Chat:
        def __init__(self):
            self.systems = []

        def complete(self, system, user):
            data = json.loads(user)
            self.systems.append((data["task"], system))
            ids = [c["id"] for c in data["chunks"]]
            mk = lambda label: {"label": label, "definition": f"{label} as described.", "synonyms": [],  # noqa: E731
                                "examples": [], "type": "obligation", "unit": None, "grain": None, "evidence": ids}
            return json.dumps({"concepts": [mk("Alpha promise"), mk("Beta pledge")]})
    chunks = [{"chunk_id": "c1", "title": "T", "text": "Text."}]
    chat = Chat()
    out = ci.induce(chunks, chat, "coarse", extra=" EXTRA-RULE", merge=False)
    assert len(out) == 2 and [t for t, _ in chat.systems] == ["induce"] and chat.systems[0][1].endswith(" EXTRA-RULE")


def test_matched_ids_uses_the_stability_matcher():
    a = [rc("doc:a", "Delivery promise", evidence=("c1", "c2")), rc("doc:b", "Warranty", evidence=("c3",))]
    b = [rc("doc:x", "Delivery promise", evidence=("c1",))]
    assert ci.matched_ids(a, b, {"c1", "c2", "c3"}) == {"doc:a"}


# ---------------------------------------------------------------- recorded replies, offline

def test_recorded_the_anchored_catalog_of_manufacturing_replays_offline_and_exports_clean(cc):
    result = cc.run_anchored("manufacturing", offline=True)
    catalog = result["catalog"]
    assert result["verify"] == {"schema": True, "lint": [], "structured_identical": True, "reimport_equal": True}
    assert catalog["concepts"] and all(r["status"] in ("proposed", "candidate") for r in catalog["concepts"])
    exported = {r["id"] for r in cc.catalog_from_ossie(result["export"])}
    assert exported == {r["id"] for r in catalog["concepts"] if r["status"] == "proposed"}
    stats = catalog["stats"]
    assert stats["concepts_total"] == sum(stats["fates"].values())
    assert set(catalog["assignment"]) == {c["chunk_id"] for c in result["_chunks"]}
    assert result["live_calls"] == {"chat": 0, "new_vectors": 0, "interpreter": 0}


# ---------------------------------------------------------------- one bad reply must not abort the build (2026-11-22)

def test_a_bad_assign_reply_fails_that_batch_and_is_counted(cc):
    stats = {}
    concepts = [{"id": "a", "label": "A", "definition": "d", "evidence": []}]
    out, assignment = cc.assign(concepts, [chunk(1), chunk(2)], Script(assign="not json"), stats=stats)
    assert assignment == {"c1": ["unassigned"], "c2": ["unassigned"]}
    assert stats["failed_items"] == [{"task": "assign", "reason": "reply is not JSON"}]


def test_a_bad_dedup_reply_confirms_no_pair_and_the_build_goes_on(cc):
    stats = {}
    a = {"id": "x", "label": "Delivery time", "definition": "d", "synonyms": [], "evidence": ["c1"], "type": "measure",
         "unit": None, "grain": None, "examples": []}
    b = {**a, "id": "y", "label": "Delivery time limit", "evidence": ["c2"]}
    out = cc.canonicalise_constrained([a, b], Script(same={"wrong": 1}), WordEmbedder(), ["c1", "c2"], stats=stats)
    assert len(out) == 2 and stats["failed_items"][0]["task"] == "same"


def test_a_build_reports_zero_failures_and_fails_loudly_above_the_share(cc, mfg_cards):
    workspace, cards, ident = mfg_cards
    chunks = [chunk(3, text="Free text three.")]
    records = [rec("c3", None, "none")]
    ok = cc.build_anchored_catalog("manufacturing", chunks, records, workspace,
                                   Script(induce=induce_reply, same={"same": []}, match={"decisions": []}),
                                   WordEmbedder(), now="2026-11-22T00:00:00Z")
    assert ok["stats"]["failed"]["count"] == 0 and ok["stats"]["failed"]["units"] > 0
    with pytest.raises(ci.InductionError, match="failed"):
        cc.build_anchored_catalog("manufacturing", chunks, records, workspace, Script(induce="not json"),
                                  WordEmbedder(), now="2026-11-22T00:00:00Z")


# ---------------------------------------------------------------- incremental update (task 5, 2026-11-22)

def _two_sources(mfg_cards):
    """Source s1 (two chunks) and source s2 (one chunk). Each chunk states a value of one bound field; c2 and c3 also
    carry free text. Hand-written doubles."""
    workspace, cards, ident = mfg_cards
    value = {"definition_id": ident, "canonical_definition_id": ident, "relation": "states_value_of"}
    chunks = [chunk(1, "s1", "Customer 29641 orders 16 units."), chunk(2, "s1", "The supplier promises delivery in 14 days."),
              chunk(3, "s2", "Customer 29629 must accept goods in writing within ten days.")]
    records = [rec("c1", None, source="s1") | value | {"stated_value": "16"},
               rec("c2", None, source="s1") | value | {"stated_value": "17"},
               rec("c3", None, source="s2") | value | {"stated_value": "18"}]
    return workspace, chunks, records


def by_text(data):
    """Induction double: one concept per chunk, named after the chunk's text."""
    names = {"Customer 29641 orders 16 units.": "Order quantity statement",
             "The supplier promises delivery in 14 days.": "Supplier delivery promise",
             "Customer 29629 must accept goods in writing within ten days.": "Written acceptance requirement"}
    return {"concepts": [{"label": names[c["text"]], "definition": f"{names[c['text']]}: {c['text']}", "synonyms": [],
                          "examples": [], "type": "obligation", "unit": None, "grain": None, "evidence": [c["id"]]}
                         for c in data["chunks"]]}


def test_incremental_update_equals_a_full_rebuild_in_the_anchored_part_and_keeps_earlier_ids(cc, mfg_cards):
    workspace, chunks, records = _two_sources(mfg_cards)
    chat = lambda: Script(induce=by_text, same={"same": []}, match={"decisions": []})  # noqa: E731
    first = cc.build_anchored_catalog("manufacturing", chunks[:2], records[:2], workspace, chat(), WordEmbedder(),
                                      now="2026-11-22T00:00:00Z")
    full = cc.build_anchored_catalog("manufacturing", chunks, records, workspace, chat(), WordEmbedder(),
                                     now="2026-11-22T00:00:00Z")
    calls = chat()
    nxt = cc.update_anchored_catalog("manufacturing", first["concepts"], chunks, records, ["s2"], workspace, calls,
                                     WordEmbedder(), now="2026-11-22T00:00:00Z")
    anchored = lambda cat: {r["id"]: (r["links"], r["status"]) for r in cat["concepts"] if r["id"] in cat["anchored_ids"]}  # noqa: E731
    assert anchored(nxt) == anchored(full)
    old = {r["id"]: r for r in first["concepts"] if r["id"] not in first["anchored_ids"]}
    new = {r["id"]: r for r in nxt["concepts"]}
    assert set(old) <= set(new) and all(new[i]["label"] == old[i]["label"] for i in old)
    induced = [c["text"] for task, _, data in calls.calls if task == "induce" for c in data["chunks"]]
    assert set(induced) == {chunks[2]["text"]}                  # only the new source is induced
    assert "Written acceptance requirement" in {r["label"] for r in nxt["concepts"]}
    assert nxt["version"] != first["version"] and nxt["stats"]["incremental"]["new_sources"] == ["s2"]
    assert set(nxt["assignment"]) == {"c1", "c2", "c3"}


def test_new_sources_since_names_added_sources_and_asks_for_a_full_rebuild_otherwise(cc):
    chunks = [chunk(1, "s1"), chunk(2, "s2")]
    old = [{"id": "doc:a", "evidence": {"catalog_version": "v", "chunks": ["c1"]}}]
    assert cc.new_sources_since(old, chunks) == ["s2"]
    assert cc.new_sources_since(None, chunks) is None                       # no earlier version
    assert cc.new_sources_since(old, chunks[:1]) is None                    # nothing new
    gone = [{"id": "doc:a", "evidence": {"catalog_version": "v", "chunks": ["c9"]}}]
    assert cc.new_sources_since(gone, chunks) is None                       # an earlier chunk is gone


def test_build_from_links_updates_incrementally_when_a_confirmed_version_exists(cc, mfg_cards):
    workspace, chunks, records = _two_sources(mfg_cards)
    chat = Script(induce=by_text, same={"same": []}, match={"decisions": []})
    first = cc.build_from_links("manufacturing", chunks[:2], records[:2], chat, WordEmbedder(), now="2026-11-22T00:00:00Z")
    assert "incremental" not in first["stats"]
    nxt = cc.build_from_links("manufacturing", chunks, records, chat, WordEmbedder(), now="2026-11-22T00:00:00Z",
                              previous=first["concepts"])
    assert nxt["stats"]["incremental"]["new_sources"] == ["s2"]


# ---------------------------------------------------------------- bounded retries (2026-11-22)

def test_the_embedder_repeats_a_dropped_batch_a_bounded_number_of_times(monkeypatch):
    from semantic_pipeline_v2 import chunk_index
    monkeypatch.setattr(chunk_index.time, "sleep", lambda s: None)

    class Client:   # hand-written double of the SDK client
        def __init__(self, fail):
            self.fail, self.calls = fail, 0
            self.embeddings = self

        def create(self, model, inputs):
            self.calls += 1
            if self.calls <= self.fail:
                raise ConnectionError("Server disconnected without sending a response.")
            return type("R", (), {"data": [type("D", (), {"index": i, "embedding": [1.0]})() for i in range(len(inputs))]})()
    ok = Client(2)
    assert chunk_index.MistralEmbedder("", client=ok).embed(["a", "b"]) == [[1.0], [1.0]] and ok.calls == 3
    down = Client(99)
    with pytest.raises(ConnectionError):
        chunk_index.MistralEmbedder("", client=down).embed(["a"])
    assert down.calls == len(chunk_index.EMBED_BACKOFF) + 1


def test_retrying_abandons_a_stalled_attempt_and_asks_again(cc, monkeypatch):
    import threading
    monkeypatch.setattr(cc.time, "sleep", lambda s: None)
    release = threading.Event()

    class Stalls:
        model, calls = "m", 0

        def complete(self, system, user):
            self.calls += 1
            if self.calls == 1:
                release.wait(5)          # the first attempt hangs past the deadline
            return "ok"
    live = Stalls()
    assert cc.Retrying(live, deadline=0.2).complete("s", "u") == "ok" and live.calls == 2
    release.set()


def test_run_anchored_wraps_the_induction_and_interpreter_clients_once(cc, monkeypatch):
    seen = {}

    def fake_output_a(dataset, workspace, embedder, interpreter, extra_docs=()):
        seen["interpreter"] = interpreter.chat.live
        return [], [], set(), {}, []
    monkeypatch.setattr(cc, "output_a_records", fake_output_a)
    monkeypatch.setattr(cc, "build_anchored_catalog", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("stop")))

    class Live:
        model, available = "m", True
    with pytest.raises(RuntimeError, match="stop"):
        cc.run_anchored("manufacturing", live_chat=Live(), live_interpreter=Live())
    assert isinstance(seen["interpreter"], cc.Retrying) and not isinstance(seen["interpreter"].live, cc.Retrying)
