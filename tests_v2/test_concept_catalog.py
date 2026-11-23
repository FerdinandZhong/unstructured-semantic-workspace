"""Pipeline B, step 2: the unified concept catalog (E4, E5, E7).

Model replies and the embedder here are hand-written test doubles, not recordings, except the tests marked
"recorded", which replay real replies recorded on 2026-11-21 offline.
"""

import copy
import hashlib
import json
import os
from pathlib import Path
import re

import numpy as np
import pytest
import yaml

from semantic_pipeline_v2 import concept_induction as ci
from semantic_pipeline_v2.core import EmbeddingSpace

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "demo_data/manufacturing/semantics/manufacturing.ossie.yaml"
SCHEMA = ROOT.parent / "semantic-hub/shared/helios_core/ossie/ossie-schema.json"
CATALOG_REPLIES = Path(__file__).parent / "fixtures" / "catalog_replies"
CATALOG_EMBEDDINGS = Path(__file__).parent / "fixtures" / "catalog_embeddings"


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


def concept(cid, label, definition="A business concept described in the documents of the demo.", **over):
    base = {"id": cid, "label": label, "definition": definition, "synonyms": [], "examples": [], "type": "attribute",
            "unit": None, "grain": None, "evidence": ["c1"], "origin": "document", "status": "proposed"}
    return {**base, **over}


# ---------------------------------------------------------------- canonicalise

def test_canonicalise_merges_a_confirmed_pair(cc):
    items = [concept("doc:purchase-commitment", "Purchase commitment", evidence=["c1"], examples=["at least 71 units"]),
             concept("doc:customer-purchase-commitment", "Customer purchase commitment", evidence=["c2"],
                     synonyms=["order floor"])]
    chat = Script(same=lambda d: {"same": [[d["concepts"][0]["id"], d["concepts"][1]["id"]]]})
    stats = {}
    out = cc.canonicalise(items, chat, WordEmbedder(), ["c1", "c2", "c3"], stats=stats)
    assert len(out) == 1 and out[0]["evidence"] == ["c1", "c2"]
    assert "Customer purchase commitment" in out[0]["synonyms"] or "Purchase commitment" in out[0]["synonyms"]
    assert "order floor" in out[0]["synonyms"] and out[0]["examples"] == ["at least 71 units"]
    assert stats["pairs_proposed"] == 1 and stats["pairs_confirmed"] == 1


def test_canonicalise_skips_unknown_and_unproposed_pairs_and_counts_them(cc):
    items = [concept("doc:a", "Delivery promise", "Promise to deliver goods by a date.", evidence=["c1"]),
             concept("doc:b", "Customer delivery promise", "Promise to deliver goods by a date.", evidence=["c2"]),
             concept("doc:c", "Warranty duration", "Months of cover after delivery of a product.", evidence=["c3"])]
    chat = Script(same=lambda d: {"same": [["zz", "yy"], [d["concepts"][0]["id"], "m3"], ]})
    stats = {}
    out = cc.canonicalise(items, chat, WordEmbedder(), ["c1", "c2", "c3"], stats=stats)
    assert len(out) == 3 and stats["pairs_skipped"] == 2 and stats["pairs_confirmed"] == 0


def test_canonicalise_makes_no_model_call_without_a_proposed_pair(cc):
    items = [concept("doc:a", "Delivery promise", "Promise to deliver goods on a date."),
             concept("doc:b", "Warranty duration", "Months of cover after delivery of a product.", evidence=["c2"])]
    chat = Script()
    out = cc.canonicalise(items, chat, WordEmbedder(), ["c1", "c2"], similarity=0.999)
    assert len(out) == 2 and chat.calls == []


@pytest.mark.parametrize("reply", ["not json", "[]", {"pairs": []}, {"same": "x"}])
def test_canonicalise_malformed_reply_raises_and_substitutes_nothing(cc, reply):
    items = [concept("doc:a", "Delivery promise", evidence=["c1"]),
             concept("doc:b", "Customer delivery promise", evidence=["c2"])]
    with pytest.raises(cc.CatalogError):
        cc.canonicalise(items, Script(same=reply), WordEmbedder(), ["c1", "c2"])


def test_broad_flag_follows_a_stated_evidence_share_and_is_information_only(cc):
    items = [concept("doc:broad", "Party", evidence=["c1", "c2", "c3", "c4"]),
             concept("doc:narrow", "Warranty duration", evidence=["c5"])]
    out = cc.flag_broad(items, total_chunks=10, share=0.3)
    assert [c["broad"] for c in out] == [True, False] and len(out) == 2
    assert [c["broad"] for c in cc.flag_broad(items, total_chunks=10, share=0.5)] == [False, False]


# ---------------------------------------------------------------- merge with structured concepts

STRUCTURED = [
    {"id": "purchase_commitment", "label": "Purchase commitment", "broader": None,
     "definition": "A customer's agreed minimum quantity of product to buy in a period.",
     "synonyms": ["minimum order"]},
    {"id": "warranty", "label": "Warranty", "broader": None,
     "definition": "Cover given by the supplier after delivery for a stated time.", "synonyms": []},
]


def pick(label_to_match):
    def reply(data):
        return {"decisions": [{"id": c["id"], "match": next(
            (k["id"] for k in c["candidates"] if k["label"] == label_to_match.get(c["label"])), None),
            "reason": "same meaning"} for c in data["concepts"]]}
    return reply


def test_same_meaning_reuses_the_structured_id_and_sets_origin_both(cc):
    items = [concept("doc:minimum-purchase", "Minimum purchase commitment", evidence=["c1"], type="obligation",
                     unit="units", examples=["at least 71 units"]),
             concept("doc:delivery", "Delivery promise", evidence=["c2"], type="obligation")]
    chat = Script(match=pick({"Minimum purchase commitment": "Purchase commitment"}))
    snapshot = copy.deepcopy(STRUCTURED)
    stats = {}
    out = cc.merge_structured(items, STRUCTURED, chat, WordEmbedder(), stats=stats)
    by_id = {c["id"]: c for c in out}
    assert set(by_id) == {"purchase_commitment", "doc:delivery"}
    both = by_id["purchase_commitment"]
    assert both["origin"] == "both" and both["evidence"] == ["c1"] and both["unit"] == "units"
    assert both["label"] == "Purchase commitment" and both["definition"] == STRUCTURED[0]["definition"]
    assert both["synonyms"] == ["minimum order", "Minimum purchase commitment"] and both["examples"] == ["at least 71 units"]
    assert by_id["doc:delivery"]["origin"] == "document"
    assert STRUCTURED == snapshot and stats["merged_both"] == 1


def test_two_document_concepts_for_one_structured_concept_make_one_record(cc):
    items = [concept("doc:a", "Minimum order", evidence=["c1"]), concept("doc:b", "Order floor", evidence=["c2"])]
    chat = Script(match=pick({"Minimum order": "Purchase commitment", "Order floor": "Purchase commitment"}))
    out = cc.merge_structured(items, STRUCTURED, chat, WordEmbedder())
    assert [c["id"] for c in out] == ["purchase_commitment"] and out[0]["evidence"] == ["c1", "c2"]


def test_a_structured_id_that_was_not_offered_is_skipped_and_counted(cc):
    items = [concept("doc:a", "Minimum order", evidence=["c1"])]
    chat = Script(match=lambda d: {"decisions": [{"id": d["concepts"][0]["id"], "match": "warranty_not_offered",
                                                  "reason": "x"}]})
    stats = {}
    out = cc.merge_structured(items, STRUCTURED, chat, WordEmbedder(), stats=stats)
    assert [(c["id"], c["origin"]) for c in out] == [("doc:a", "document")] and stats["decisions_skipped"] == 1


def test_merge_decision_reply_shape_is_checked(cc):
    with pytest.raises(cc.CatalogError):
        cc.merge_structured([concept("doc:a", "Minimum order")], STRUCTURED, Script(match="oops"), WordEmbedder())


# ---------------------------------------------------------------- link

@pytest.fixture(scope="module")
def mfg():
    from semantic_pipeline_v2.workspace import load_workspace
    return load_workspace("manufacturing")


def first_candidate(predicate="closeMatch", only=None):
    def reply(data):
        links = []
        for c in data["concepts"]:
            if only is None or c["label"] in only:
                links.append({"concept": c["id"], "definition": c["candidates"][0]["id"], "predicate": predicate,
                              "reason": "close in meaning"})
        return {"links": links}
    return reply


def test_link_offers_twelve_candidates_and_keeps_valid_links_with_full_ids(cc, mfg):
    items = [concept("doc:ordered-quantity", "Ordered quantity", "Units of a product ordered on a sales order."),
             concept("doc:ghost", "Ghost idea", "Something no definition describes at all in the model.")]
    chat = Script(link=first_candidate("closeMatch", only={"Ordered quantity"}))
    out = cc.link(items, mfg, WordEmbedder(), chat)
    offered = chat.calls[0][2]["concepts"][0]["candidates"]
    assert 12 <= len(offered) <= 12 + cc.MIN_METRIC_CANDIDATES
    link = out[0]["links"][0]
    assert link["predicate"] == "closeMatch" and link["definition_id"].startswith("manufacturing:definition:")
    assert out[1]["links"] == []  # abstaining is valid and visible


def test_link_skips_unoffered_definitions_and_bad_predicates_and_counts(cc, mfg):
    items = [concept("doc:ordered-quantity", "Ordered quantity", "Units of a product ordered on a sales order.")]

    def reply(data):
        c = data["concepts"][0]
        good = c["candidates"][0]["id"]
        return {"links": [
            {"concept": c["id"], "definition": "d999", "predicate": "closeMatch", "reason": "x"},      # not offered
            {"concept": c["id"], "definition": good, "predicate": "sameAs", "reason": "x"},            # bad predicate
            {"concept": "n99", "definition": good, "predicate": "closeMatch", "reason": "x"},          # unknown concept
            {"concept": c["id"], "definition": good, "predicate": "relatedMatch", "reason": "ok"},
            {"concept": c["id"], "definition": good, "predicate": "closeMatch", "reason": "twice"}]}   # duplicate
    stats = {}
    out = cc.link(items, mfg, WordEmbedder(), Script(link=reply), stats=stats)
    assert [(l["predicate"]) for l in out[0]["links"]] == ["relatedMatch"] and stats["links_skipped"] == 4


def test_an_obligation_is_never_linked_with_exactmatch(cc, mfg):
    items = [concept("doc:promise", "Delivery promise", "A promise to deliver by a date.", type="obligation"),
             concept("doc:event", "Shipment", "Goods leave the supplier.", type="event")]
    stats = {}
    keep = lambda d: {"checks": [{"id": x["id"], "predicate": "exactMatch", "mismatch": None, "reason": "ok"}
                                 for x in d["links"]]}
    out = cc.link(items, mfg, WordEmbedder(), Script(link=first_candidate("exactMatch"), verify=keep), stats=stats)
    assert out[0]["links"][0]["predicate"] == "closeMatch" and out[1]["links"][0]["predicate"] == "exactMatch"
    assert stats["exact_capped_for_obligation"] == 1


def test_second_look_weakens_to_closematch_and_removes_only_for_a_stated_mismatch(cc, mfg):
    items = [concept(f"doc:e{n}", f"Event {n}", "Something that happens.", type="event") for n in range(5)]
    verdicts = iter([("exactMatch", None), ("narrowMatch", None), (None, None), (None, "unit")])  # fifth: no verdict

    def verify(data):
        out = []
        for x in data["links"][:4]:
            predicate, mismatch = next(verdicts)
            out.append({"id": x["id"], "predicate": predicate, "mismatch": mismatch, "reason": "r"})
        return {"checks": out}
    chat, stats = Script(link=first_candidate("exactMatch"), verify=verify), {}
    out = cc.link(items, mfg, WordEmbedder(), chat, stats=stats)
    assert [[l["predicate"] for l in c["links"]] for c in out] == [
        ["exactMatch"], ["narrowMatch"], ["closeMatch"], [], ["closeMatch"]]
    assert (stats["exact_weakened"], stats["exact_removed"], stats["exact_unverified"]) == (2, 1, 1)
    system = chat.calls[-1][1]
    assert "same unit" in system and "mismatch" in system and "closeMatch" in system


def test_no_exactmatch_means_no_second_look(cc, mfg):
    chat = Script(link=first_candidate("relatedMatch"))
    cc.link([concept("doc:a", "Ordered quantity")], mfg, WordEmbedder(), chat)
    assert [c[0] for c in chat.calls] == ["link"]


def test_an_unusable_second_look_reply_raises(cc, mfg):
    with pytest.raises(cc.CatalogError):
        cc.link([concept("doc:a", "Ordered quantity", type="event")], mfg, WordEmbedder(),
                Script(link=first_candidate("exactMatch"), verify="nope"))


def test_link_candidates_include_metrics_and_the_query_uses_a_verbatim_example(cc, mfg):
    chat = Script(link={"links": []})
    cc.link([concept("doc:a", "Ordered quantity", "Units of a product ordered.", examples=["ordered 71 units"])],
            mfg, WordEmbedder(), chat)
    row = chat.calls[0][2]["concepts"][0]
    kinds = [k["kind"] for k in row["candidates"]]
    assert kinds.count("metric") >= cc.MIN_METRIC_CANDIDATES and 12 <= len(kinds) <= 12 + cc.MIN_METRIC_CANDIDATES
    assert row["examples"] == ["ordered 71 units"]
    assert "ordered 71 units" in cc.link_text(concept("doc:a", "X", examples=["ordered 71 units"]))


def test_link_reply_that_is_not_a_links_object_raises(cc, mfg):
    with pytest.raises(cc.CatalogError):
        cc.link([concept("doc:a", "Ordered quantity")], mfg, WordEmbedder(), Script(link={"x": 1}))


def test_link_prompt_states_the_general_rules(cc, mfg):
    chat = Script(link={"links": []})
    cc.link([concept("doc:a", "Ordered quantity")], mfg, WordEmbedder(), chat)
    system = chat.calls[0][1]
    for word in ("exactMatch", "closeMatch", "broadMatch", "narrowMatch", "relatedMatch", "unit", "grain"):
        assert word in system
    assert "obligation" in system and "never exactMatch" in system and "empty list" in system
    assert "reference number" in system and "identifier" in system and "category" in system


def test_broad_concepts_are_linked_like_any_other(cc, mfg):
    chat = Script(link=first_candidate("closeMatch"))
    out = cc.link([concept("doc:party", "Party", broad=True, type="party")], mfg, WordEmbedder(), chat)
    assert len(chat.calls) == 1 and out[0]["links"][0]["predicate"] == "closeMatch"


# ---------------------------------------------------------------- assign

CHUNKS = [{"chunk_id": f"c{i}", "text": f"Passage {i} about something.", "title": "Doc", "source": "s"}
          for i in range(1, 5)]


def test_assign_uses_induction_evidence_first_then_the_model_for_the_rest(cc):
    items = [concept("doc:a", "Alpha", evidence=["c1"]), concept("doc:b", "Beta", evidence=["c1", "c2"])]

    def reply(data):
        ids = {k["label"]: k["id"] for k in data["concepts"]}
        return {"assignments": [{"chunk": data["chunks"][0]["id"], "concepts": [ids["Alpha"]]},
                                {"chunk": data["chunks"][1]["id"], "concepts": []},
                                {"chunk": "zz", "concepts": [ids["Beta"]]}]}        # unknown chunk: skipped
    chat = Script(assign=reply)
    stats = {}
    out, assignment = cc.assign(items, CHUNKS, chat, stats=stats)
    assert [c["id"] for c in chat.calls[0][2]["concepts"]] != [] and len(chat.calls[0][2]["chunks"]) == 2
    assert assignment == {"c1": ["doc:a", "doc:b"], "c2": ["doc:b"], "c3": ["doc:a"], "c4": ["unassigned"]}
    assert out[0]["evidence"] == ["c1", "c3"] and stats["assignments_skipped"] == 1
    assert stats["chunks_unassigned"] == 1


def test_assign_makes_no_call_when_every_chunk_has_evidence(cc):
    items = [concept("doc:a", "Alpha", evidence=["c1", "c2", "c3", "c4"])]
    chat = Script()
    _, assignment = cc.assign(items, CHUNKS, chat)
    assert chat.calls == [] and set(assignment) == {"c1", "c2", "c3", "c4"}


def test_assign_never_substitutes_for_a_bad_reply(cc):
    with pytest.raises(cc.CatalogError):
        cc.assign([concept("doc:a", "Alpha", evidence=["c1"])], CHUNKS, Script(assign="not json"))


def test_assign_writes_catalog_columns_to_the_chunk_index(cc, tmp_path):
    from semantic_pipeline_v2.chunk_index import ChunkIndex
    from semantic_pipeline_v2.embedding_cache import DEFAULT_SPACE
    index = ChunkIndex(tmp_path / "idx", DEFAULT_SPACE)
    index.ingest(scope="w", source_id="s", revision="a" * 64, kind="text", name="Doc",
                 units=[{"text": "Alpha passage about delivery.", "page": 0, "element": "text"},
                        {"text": "Beta passage about warranty.", "page": 1, "element": "table"}])
    rows = index.read("w", "s")
    chunks = [{"chunk_id": f"x{n}", "text": r["text"], "title": "Doc", "source": "s"} for n, r in enumerate(rows)]
    stats = cc.write_to_index(index, "w", chunks, {"x0": ["doc:a", "doc:b"], "x1": ["unassigned"]}, "cat-1")
    got = {r["text"]: (r["catalog_entry_id"], r["catalog_version"]) for r in index.read("w", "s")}
    assert got[chunks[0]["text"]] == ("doc:a|doc:b", "cat-1") and got[chunks[1]["text"]] == ("unassigned", "cat-1")
    assert stats == {"rows_written": 2, "chunks_unmatched": 0}


# ---------------------------------------------------------------- E4: build the catalog

def build(cc, mfg, chunks, induced, **scripts):
    scripts.setdefault("children", {"concepts": []})
    chat = Script(**scripts)
    return cc.build_catalog("manufacturing", chunks, induced, mfg, chat, WordEmbedder(), now="2026-11-21T00:00:00Z"), chat


def manufacturing_induced():
    return [concept("doc:minimum-purchase", "Minimum purchase commitment", "A customer promises to order a minimum "
                    "quantity of units each year.", type="obligation", unit="units", evidence=["c1"]),
            concept("doc:delivery-promise", "Delivery promise", "A supplier promises delivery within a number of days.",
                    type="obligation", evidence=["c2"]),
            concept("doc:party", "Party", "Any person or organisation named in a contract.", type="party",
                    evidence=["c1", "c2", "c3"])]


def match_first(label):
    """Hand-written decision: the first offered candidate for the concept with this label, abstain for the rest."""
    return lambda d: {"decisions": [{"id": c["id"], "match": c["candidates"][0]["id"] if c["label"] == label else None,
                                     "reason": "r"} for c in d["concepts"]]}


def test_e4_catalog_records_have_the_contract_fields_and_every_chunk_is_assigned(cc, mfg):
    structured_ids = {c["id"] for c in cc.structured_concepts(mfg)}
    catalog, chat = build(
        cc, mfg, CHUNKS, manufacturing_induced(),
        same={"same": []},
        match=match_first("Delivery promise"),
        link=first_candidate("closeMatch", only={"Minimum purchase commitment"}),
        assign=lambda d: {"assignments": [{"chunk": c["id"], "concepts": []} for c in d["chunks"]]})
    records = catalog["concepts"]
    contract = {"id", "label", "definition", "synonyms", "examples", "origin", "type", "unit", "grain", "links",
                "evidence", "status", "actor", "time", "broad", "level", "parent"}
    assert all(set(r) == contract for r in records)
    assert all(r["status"] == "proposed" and r["time"] == "2026-11-21T00:00:00Z" for r in records)
    assert {r["origin"] for r in records} == {"document", "both"}
    assert all(r["evidence"]["catalog_version"] == catalog["version"] for r in records)
    assert all(r["level"] == "coarse" and r["parent"] is None for r in records)
    both = [r for r in records if r["origin"] == "both"]
    assert len(both) == 1 and both[0]["id"] in structured_ids and both[0]["evidence"]["chunks"] == ["c2"]
    assert not any(r["id"] in structured_ids for r in records if r["origin"] == "document")
    assert set(catalog["assignment"]) == {c["chunk_id"] for c in CHUNKS}
    assert catalog["assignment"]["c4"] == ["unassigned"]
    assert catalog["stats"]["chunks_assigned"] == 3 and catalog["stats"]["chunks_unassigned"] == 1
    assert catalog["stats"]["concepts_induced"] == 3


def test_e4_version_is_stable_and_changes_with_the_content(cc, mfg):
    scripts = dict(same={"same": []}, match={"decisions": []}, link={"links": []}, assign={"assignments": []},
                   children={"concepts": []})
    a, _ = build(cc, mfg, CHUNKS, manufacturing_induced(), **scripts)
    b, _ = build(cc, mfg, CHUNKS, manufacturing_induced(), **scripts)
    changed = manufacturing_induced()
    changed[0]["definition"] = "Quite another meaning."
    c, _ = build(cc, mfg, CHUNKS, changed, **scripts)
    assert a["version"] == b["version"] != c["version"]


# ---------------------------------------------------------------- E5: export and re-import

@pytest.fixture()
def exported(cc, mfg):
    induced = manufacturing_induced()
    catalog, _ = build(
        cc, mfg, CHUNKS, induced, same={"same": []}, match=match_first("Delivery promise"),
        link=first_candidate("relatedMatch", only={"Minimum purchase commitment", "Delivery promise"}),
        assign={"assignments": []})
    return catalog, cc.export_ossie(MODEL.read_text(), catalog)


def test_e5_schema_lint_identity_and_reimport(cc, exported):
    import jsonschema
    catalog, text = exported
    model = yaml.safe_load(text)
    jsonschema.Draft202012Validator(json.loads(SCHEMA.read_text())).validate(model)
    from semantic_pipeline_v2.semantic_store import _project_lint
    assert _project_lint()(model) == []
    report = cc.verify_export(MODEL.read_text(), text, catalog)
    assert report == {"schema": True, "lint": [], "structured_identical": True, "reimport_equal": True}
    again = cc.catalog_from_ossie(text)
    assert sorted(r["id"] for r in again) == sorted(r["id"] for r in catalog["concepts"])
    assert {r["id"]: r for r in again} == {r["id"]: r for r in catalog["concepts"]}


def test_e5_document_and_structured_concepts_share_one_block(cc, exported):
    catalog, text = exported
    model = yaml.safe_load(text)
    blocks = [json.loads(e["data"]) for e in model["custom_extensions"]]
    ids = [c["id"] for b in blocks for c in b.get("concepts", [])]
    original = [c["id"] for c in cc.structured_from_model(yaml.safe_load(MODEL.read_text()))]
    assert len(ids) == len(set(ids)) and set(original) <= set(ids)
    assert {r["id"] for r in catalog["concepts"] if r["origin"] == "document"} <= set(ids)
    only_structured = [c for b in blocks for c in b["concepts"] if "origin" not in c]
    assert [c["id"] for c in only_structured if c["id"] in original] != []


def test_e5_a_changed_structured_definition_is_detected(cc, exported):
    catalog, text = exported
    model = yaml.safe_load(text)
    model["metrics"][0]["description"] += " changed"
    assert cc.verify_export(MODEL.read_text(), yaml.safe_dump(model, sort_keys=False, width=100), catalog)[
        "structured_identical"] is False


def test_e5_lint_rejects_a_link_to_an_unknown_definition_or_predicate(cc, exported):
    from semantic_pipeline_v2.semantic_store import _project_lint
    _, text = exported
    model = yaml.safe_load(text)
    top = next(e for e in model["custom_extensions"] if "concepts" in json.loads(e["data"]))
    data = json.loads(top["data"])
    doc = next(c for c in data["concepts"] if c.get("origin") == "document" and c["links"])
    doc["links"][0]["definition_id"] = "manufacturing:definition:adventureworks_manufacturing_demo/metric/nope"
    top["data"] = json.dumps(data, sort_keys=True)
    assert any("unknown definition" in e for e in _project_lint()(model))
    doc["links"][0].update(definition_id=doc["links"][0]["definition_id"], predicate="sameAs")
    top["data"] = json.dumps(data, sort_keys=True)
    assert any("predicate" in e for e in _project_lint()(model))


def test_lint_still_requires_a_binding_for_a_structured_concept():
    from semantic_pipeline_v2.semantic_store import _project_lint
    model = yaml.safe_load(MODEL.read_text())
    top = next(e for e in model["custom_extensions"] if "concepts" in json.loads(e["data"]))
    data = json.loads(top["data"])
    data["concepts"].append({"id": "orphan", "label": "Orphan", "definition": "A concept that nothing is bound to.",
                             "synonyms": [], "broader": None})
    top["data"] = json.dumps(data, sort_keys=True)
    assert any("orphan" in e and "bound to no field" in e for e in _project_lint()(model))


# ---------------------------------------------------------------- store

@pytest.fixture()
def db(tmp_path):
    pg_bin = os.environ.get("V2_PG_BIN")
    if not pg_bin:
        pytest.skip("set V2_PG_BIN to run store tests")
    from semantic_pipeline_v2._pilot_postgres import PilotPostgres
    with PilotPostgres.start(Path(pg_bin)) as database:
        yield database


def test_store_writes_the_catalog_add_only_and_reads_it_back(cc, mfg, db, exported):
    from semantic_pipeline_v2.semantic_store import SemanticStore
    catalog, _ = exported
    store = SemanticStore(db)
    store.write_catalog("manufacturing", catalog["version"], catalog["concepts"])
    store.write_catalog("manufacturing", catalog["version"], catalog["concepts"])  # idempotent
    SemanticStore(db)  # idempotent DDL
    got = store.read_catalog("manufacturing", catalog["version"])
    assert {r["id"]: r for r in got} == {r["id"]: r for r in catalog["concepts"]}
    assert store.read_catalog("manufacturing", "other") == []


# ---------------------------------------------------------------- corpus: text-layer PDFs

def test_finance_chunks_now_include_the_three_text_layer_pdfs_masked_and_hash_checked():
    skipped = []
    chunks = ci.dataset_chunks("finance", skipped)
    titles = {c["title"] for c in chunks}
    assert {"customer-form3.pdf", "customer-form4.pdf", "customer-form5.pdf"} <= titles
    assert not [s for s in skipped if "cleaned_pdf_3" in s or "cleaned_pdf_4" in s or "cleaned_pdf_5" in s]
    assert not any(re.search(r"\b[STFGM]\d{7}[A-Z]\b", c["text"]) for c in chunks)
    assert len({c["chunk_id"] for c in chunks}) == len(chunks) and chunks == ci.dataset_chunks("finance")


def test_a_changed_text_layer_pdf_is_reported_not_skipped(monkeypatch, tmp_path):
    real = ci.load_originals()
    broken = {k: v.__class__(tmp_path, v.relative, v.digest, v.size) if k.endswith("cleaned_pdf_4") else v
              for k, v in real.items()}
    monkeypatch.setattr(ci, "load_originals", lambda: broken)
    with pytest.raises(ci.InductionError, match="cleaned_pdf_4"):
        ci.dataset_chunks("finance")


def test_concept_counts_are_reported_with_and_without_form_chunks(cc):
    items = [concept("doc:a", "Alpha", evidence=["f1", "t1"]), concept("doc:b", "Beta", evidence=["f1"]),
             concept("doc:c", "Gamma", evidence=["t2"])]
    assert cc.counts_without(items, {"f1"}) == {"with_form_chunks": 3, "without_form_chunks": 2,
                                                "only_form_evidence": 1}


# ---------------------------------------------------------------- recorded: real replies replay offline

def test_recorded_replies_rebuild_the_manufacturing_catalog_offline(cc, mfg):
    """E4 and E7 on real replies recorded on 2026-11-21 (live run, `mistral-small-latest`, `mistral-embed`)."""
    if not (CATALOG_REPLIES / "inductions.json.gz").exists():
        pytest.fail("no recorded catalog replies under tests_v2/fixtures/catalog_replies")
    result = cc.run_dataset("manufacturing", "coarse", offline=True)
    catalog = result["catalog"]
    chunks = {c["chunk_id"] for c in ci.dataset_chunks("manufacturing")}
    assert catalog["concepts"] and set(catalog["assignment"]) == chunks
    assert all(v for v in catalog["assignment"].values())
    for r in catalog["concepts"]:
        assert r["status"] == "proposed" and r["origin"] in ("document", "both")
        assert set(r["evidence"]["chunks"]) <= chunks and r["evidence"]["catalog_version"] == catalog["version"]
        assert all(l["predicate"] in cc.PREDICATES for l in r["links"])
    assert result["verify"] == {"schema": True, "lint": [], "structured_identical": True, "reimport_equal": True}
    for level in ("coarse", "child"):  # E7 per level: single-run concepts are flagged
        assert 0.0 <= result["stability"][level]["agreement"] <= 1.0 and "only_in_a" in result["stability"][level]
    fates = catalog["stats"]["fates"]
    assert catalog["stats"]["concepts_total"] == fates["kept"] + fates["merged"] + fates["dropped"]
    assert {r["level"] for r in catalog["concepts"]} <= {"coarse", "child"}


# ---------------------------------------------------------------- children (second level)

def child(label, evidence, **over):
    base = {"label": label, "definition": f"The {label} that the passage states explicitly.", "synonyms": [],
            "examples": [], "type": "attribute", "unit": None, "grain": None, "evidence": evidence}
    return {**base, **over}


CH = [{"chunk_id": f"c{i}", "text": f"Passage {i}: the customer number is stated and the order quantity is 5 units.",
       "title": "Doc", "source": "s"} for i in range(1, 4)]


def asked_chunk_ids(data):
    return [c["id"] for c in data["chunks"]]


def test_children_have_a_parent_a_level_and_evidence_inside_the_parents_chunks(cc):
    parent = concept("doc:customer", "Customer", evidence=["c1", "c2"], type="party")
    chat = Script(children=lambda d: {"concepts": [
        child("Customer number", [asked_chunk_ids(d)[0]], examples=["customer number"]),
        child("Order quantity", asked_chunk_ids(d), examples=["order quantity is 5 units", "not in the text"])]})
    out = cc.add_children([parent], CH, chat)
    assert [c["id"] for c in out][0] == "doc:customer" and len(out) == 3
    kids = out[1:]
    assert all(k["level"] == "child" and k["parent"] == "doc:customer" and k["origin"] == "document" for k in kids)
    assert kids[0]["evidence"] == ["c1"] and kids[1]["evidence"] == ["c1", "c2"] and "c3" not in kids[1]["evidence"]
    assert kids[1]["examples"] == ["order quantity is 5 units"]  # a non-verbatim example is removed
    assert out[0]["level"] == "coarse" and out[0]["parent"] is None and len({c["id"] for c in out}) == 3
    assert chat.calls[0][2]["concept"]["label"] == "Customer"


def test_invalid_children_are_skipped_and_counted_and_a_reply_of_the_wrong_shape_raises(cc):
    parent = concept("doc:customer", "Customer", evidence=["c1"])
    bad = {"label": "no other keys"}
    stats, dropped = {}, []
    out = cc.add_children([parent], CH, Script(children={"concepts": [child("X", ["c99"]), bad]}), stats=stats)
    assert len(out) == 1 and stats["children_skipped_invalid"] == 2    # an invalid item is skipped and counted
    with pytest.raises(cc.CatalogError):
        cc.add_children([parent], CH, Script(children={"concepts": "x"}))
    stats, dropped = {}, []
    out = cc.add_children([parent], CH, Script(children={"concepts": [child("Ghost", [])]}), stats=stats,
                          dropped=dropped)
    assert len(out) == 1 and stats["children_dropped_no_evidence"] == 1 and dropped == ["Ghost"]
    with pytest.raises(cc.CatalogError):
        cc.add_children([parent], CH, Script(children="not json"))


def test_a_concept_without_evidence_chunks_gets_no_children_call(cc):
    chat = Script()
    out = cc.add_children([concept("doc:a", "Alpha", evidence=[])], CH, chat)
    assert len(out) == 1 and chat.calls == []


def test_a_child_that_only_restates_its_parent_is_dropped_by_canonicalise(cc):
    parent = concept("doc:order-quantity", "Order quantity", "Units ordered on an order.", evidence=["c1", "c2"],
                     level="coarse", parent=None)
    restate = concept("doc:oq", "Quantity ordered", "The units that are ordered.", evidence=["c1"], level="child",
                      parent="doc:order-quantity")
    other = concept("doc:cn", "Customer number", "Identifier of a customer.", evidence=["c2"], level="child",
                    parent="doc:order-quantity")
    fates = {}
    chat = Script(same=lambda d: {"same": [[p[0], p[1]] for p in d["pairs"]
                                           if {x["label"] for x in d["concepts"] if x["id"] in p} ==
                                           {"Order quantity", "Quantity ordered"}]})
    out = cc.canonicalise([parent, restate, other], chat, WordEmbedder(), ["c1", "c2"], fates=fates)
    assert [c["id"] for c in out] == ["doc:order-quantity", "doc:cn"]
    assert fates["doc:oq"]["fate"] == "merged" and fates["doc:oq"]["into"] == "doc:order-quantity"
    assert out[1]["parent"] == "doc:order-quantity" and "Quantity ordered" in out[0]["synonyms"]


def test_a_parent_child_pair_is_always_offered_to_the_model(cc):
    parent = concept("doc:a", "Alpha thing", "Zzz qqq.", evidence=["c1"], level="coarse", parent=None)
    kid = concept("doc:b", "Wholly other words", "Mmm kkk.", evidence=["c1"], level="child", parent="doc:a")
    chat = Script(same={"same": []})
    cc.canonicalise([parent, kid], chat, WordEmbedder(), ["c1"], similarity=0.999)
    assert len(chat.calls) == 1 and len(chat.calls[0][2]["pairs"]) == 1


def test_canonicalise_never_merges_concepts_with_different_stated_units(cc):
    a = concept("doc:a", "Delivery promise", "Promise to deliver goods.", unit="calendar days", evidence=["c1"])
    b = concept("doc:b", "Customer delivery promise", "Promise to deliver goods.", unit="months", evidence=["c2"])
    chat, stats = Script(same=lambda d: {"same": d["pairs"]}), {}
    out = cc.canonicalise([a, b], chat, WordEmbedder(), ["c1", "c2"], stats=stats)
    assert len(out) == 2 and stats["pairs_blocked_unit"] == 1 and chat.calls == []


def test_the_dedup_prompt_names_unit_and_party_role(cc):
    chat = Script(same={"same": []})
    cc.canonicalise([concept("doc:a", "Delivery promise", evidence=["c1"]),
                     concept("doc:b", "Customer delivery promise", evidence=["c2"])], chat, WordEmbedder(), ["c1", "c2"])
    system = chat.calls[0][1]
    assert "unit" in system and "party" in system and "supplier" in system


# ---------------------------------------------------------------- merge: no fold of different meaning, synonyms, fates

def test_two_document_concepts_with_different_units_are_not_folded_into_one_structured_concept(cc):
    items = [concept("doc:a", "Promised date", evidence=["c1"], unit=None),
             concept("doc:b", "Supplier lead time", evidence=["c2"], unit="calendar days"),
             concept("doc:c", "Promised days", evidence=["c3"], unit="months")]
    chat = Script(match=pick({"Promised date": "Warranty", "Supplier lead time": "Warranty",
                              "Promised days": "Warranty"}))
    fates, stats = {}, {}
    out = cc.merge_structured(items, STRUCTURED, chat, WordEmbedder(), fates=fates, stats=stats)
    both = [c for c in out if c["origin"] == "both"]
    assert len(both) == 1 and both[0]["unit"] == "calendar days"
    assert {c["id"] for c in out if c["origin"] == "document"} == {"doc:c"}
    assert stats["merge_blocked_unit"] == 1
    assert fates["doc:a"]["into"] == both[0]["id"] and fates["doc:b"]["into"] == both[0]["id"]
    assert "doc:c" not in fates


def test_a_both_record_keeps_every_merged_label_synonym_and_all_evidence(cc):
    items = [concept("doc:a", "Minimum order", evidence=["c1"], synonyms=["floor"]),
             concept("doc:b", "Order floor", evidence=["c2"]),
             concept("doc:c", "purchase commitment", evidence=["c3"])]   # equals the structured label: not repeated
    chat = Script(match=pick({"Minimum order": "Purchase commitment", "Order floor": "Purchase commitment",
                              "purchase commitment": "Purchase commitment"}))
    out = cc.merge_structured(items, STRUCTURED, chat, WordEmbedder())
    both = out[0]
    assert both["synonyms"] == ["minimum order", "floor", "Order floor"]
    assert both["evidence"] == ["c1", "c2", "c3"]


def test_every_induced_concept_has_a_fate_and_the_counts_add_up(cc, mfg):
    induced = manufacturing_induced() + [concept("doc:dup", "Delivery promise terms", "A supplier promises delivery "
                                                 "within a number of days.", type="obligation", evidence=["c3"])]
    catalog, _ = build(
        cc, mfg, CHUNKS, induced, children=lambda d: {"concepts": (
            [child("Delivery date", [asked_chunk_ids(d)[0]]), child("Ghost", [])]
            if d["concept"]["label"] == "Delivery promise" else [])},
        same=lambda d: {"same": [p for p in d["pairs"] if {x["label"] for x in d["concepts"] if x["id"] in p}
                                 == {"Delivery promise", "Delivery promise terms"}]},
        match=match_first("Delivery date"), link={"links": []}, assign={"assignments": []})
    stats, fates = catalog["stats"], catalog["fates"]
    assert stats["concepts_total"] == len(fates) == stats["concepts_induced"] + stats["children_induced"] \
        + stats["children_dropped_no_evidence"]
    assert stats["fates"] == {"kept": sum(f["fate"] == "kept" for f in fates),
                              "merged": sum(f["fate"] == "merged" for f in fates),
                              "dropped": sum(f["fate"] == "dropped" for f in fates)}
    assert stats["concepts_total"] == sum(stats["fates"].values())
    assert any(f["fate"] == "dropped" and f["label"] == "Ghost" and f["reason"] for f in fates)
    merged = [f for f in fates if f["fate"] == "merged"]
    assert merged and all(f["into"] for f in merged)
    kept_ids = {f["id"] for f in fates if f["fate"] == "kept"}
    assert kept_ids == {r["id"] for r in catalog["concepts"] if r["origin"] == "document"}
    ids = {r["id"] for r in catalog["concepts"]}
    assert all(f["into"] in ids for f in merged)    # a chain always ends at a record of the catalog


def test_the_export_round_trip_keeps_document_synonyms_of_a_both_record(cc, mfg):
    catalog, _ = build(cc, mfg, CHUNKS, manufacturing_induced(), same={"same": []},
                       match=match_first("Delivery promise"), link={"links": []}, assign={"assignments": []})
    text = cc.export_ossie(MODEL.read_text(), catalog)
    both = [r for r in catalog["concepts"] if r["origin"] == "both"][0]
    assert "Delivery promise" in both["synonyms"]
    assert cc.verify_export(MODEL.read_text(), text, catalog) == {
        "schema": True, "lint": [], "structured_identical": True, "reimport_equal": True}


# ---------------------------------------------------------------- per-level stability (E7)

def test_stability_is_reported_per_level(cc):
    big = [{"chunk_id": f"c{i}", "text": f"The customer number {i} is stated. The order quantity is {i} units.",
            "title": "Doc", "source": "s"} for i in range(1, 11)]

    def chat_for(data):
        return {"concepts": [concept_reply("Customer", [c["id"] for c in data["chunks"]])]}

    def concept_reply(label, ev):
        return {"label": label, "definition": f"The {label} of the documents.", "synonyms": [], "examples": [],
                "type": "party", "unit": None, "grain": None, "evidence": ev}

    def induce(d):
        return chat_for(d)

    def kids(d):
        return {"concepts": [concept_reply("Customer number", [c["id"] for c in d["chunks"]])]}
    chat = Script(induce=induce, children=kids, merge={"groups": []})
    report = cc.level_stability(big, chat, "coarse")
    assert set(report) == {"coarse", "child", "seeds", "shared_chunks"}
    assert report["coarse"]["agreement"] == 1.0 and report["child"]["matched"] >= 1


# ---------------------------------------------------------------- extra documents

def test_extra_docs_are_chunked_masked_and_must_live_under_demo_data(cc, tmp_path):
    inside = ROOT / "demo_data" / "preload" / "sar1536_SAR-AML202311000007_ahmad-bin-osman.md"
    chunks = cc.extra_doc_chunks([inside])
    assert chunks and all(c["source"] == "extra:" + inside.relative_to(ROOT).as_posix() for c in chunks)
    assert not any(re.search(r"\b[STFGM]\d{7}[A-Z]\b", c["text"]) for c in chunks)
    assert chunks == cc.extra_doc_chunks([str(inside)])
    outside = tmp_path / "x.md"
    outside.write_text("S1234567A is a masked id.")
    with pytest.raises(cc.CatalogError):
        cc.extra_doc_chunks([outside])
    with pytest.raises(cc.CatalogError):
        cc.extra_doc_chunks([ROOT / "demo_data" / "does-not-exist.md"])
    with pytest.raises(cc.CatalogError):
        cc.extra_doc_chunks([ROOT / "demo_data" / "preload" / ".." / ".." / "PLAN.md"])


def test_the_cli_accepts_a_list_of_extra_docs(cc):
    args = cc.build_parser().parse_args(["--dataset", "manufacturing", "--extra-docs", "a.md", "b.md"])
    assert args.extra_docs == [Path("a.md"), Path("b.md")]


# ---------------------------------------------------------------- added with the decision-model change (written after the code)

def test_equal_normalised_labels_merge_without_a_model_call_and_the_coarse_concept_stays_head(cc):
    kid = concept("doc:k", "Customer identifiers", "Id of a customer.", evidence=["c1", "c2", "c3"], level="child",
                  parent="doc:p")
    first = concept("doc:c", "Customer identifier", "Id of a customer.", evidence=["c1"], level="coarse", parent=None)
    other = concept("doc:o", "Warranty", "Months of cover after delivery.", evidence=["c4"], level="coarse", parent=None)
    fates, stats, chat = {}, {}, Script()
    out = cc.canonicalise([kid, first, other], chat, WordEmbedder(), ["c1", "c2", "c3", "c4"], fates=fates, stats=stats)
    assert chat.calls == [] and stats["same_label_merged"] == 1
    assert [c["id"] for c in out] == ["doc:c", "doc:o"] and out[0]["level"] == "coarse"
    assert out[0]["evidence"] == ["c1", "c2", "c3"] and fates["doc:k"]["into"] == "doc:c"
    assert "same normalised label" in fates["doc:k"]["reason"]


def test_the_match_prompt_shows_which_definitions_record_each_structured_concept(cc, mfg):
    chat = Script(match={"decisions": []})
    cards = {"x": {"concept": STRUCTURED[0]["id"], "label": "Ordered units", "unit": "units"}}
    cc.merge_structured([concept("doc:a", "Minimum order")], STRUCTURED, chat, WordEmbedder(), cards=cards)
    cands = chat.calls[0][2]["concepts"][0]["candidates"]
    assert {c["label"]: c["recorded_by"] for c in cands}["Purchase commitment"] == ["Ordered units (units)"]
    assert "recorded_by" in chat.calls[0][1] and "unit" in chat.calls[0][1]


def test_a_live_failure_is_retried_with_the_same_prompt_then_raised(cc, tmp_path, monkeypatch):
    monkeypatch.setattr(cc.time, "sleep", lambda s: None)
    class Flaky:   # hand-written double
        def __init__(self, fail):
            self.fail, self.seen = fail, []

        def complete(self, system, user):
            self.seen.append((system, user))
            if len(self.seen) <= self.fail:
                raise cc.ci.ChatError("down")
            return json.dumps({"same": []})
    cache = cc.RepliesCache(tmp_path)
    ok = cc.CatalogChat(cache, Flaky(2), "m")
    assert ok.complete("s", "u") == '{"same": []}' and ok.calls == 1
    assert len(ok.live.seen) == 3 and len({x for x in ok.live.seen}) == 1
    with pytest.raises(cc.ci.ChatError):
        cc.CatalogChat(cc.RepliesCache(tmp_path / "b"), Flaky(99), "m").complete("s", "u")


def test_the_dedup_and_match_views_carry_unit_and_verbatim_examples(cc):
    a = concept("doc:a", "Delivery promise", unit="calendar days", examples=["within 14 calendar days"], evidence=["c1"])
    b = concept("doc:b", "Customer delivery promise", evidence=["c2"])
    chat = Script(same={"same": []})
    cc.canonicalise([a, b], chat, WordEmbedder(), ["c1", "c2"])
    row = {x["label"]: x for x in chat.calls[0][2]["concepts"]}["Delivery promise"]
    assert row["unit"] == "calendar days" and row["examples"] == ["within 14 calendar days"]
