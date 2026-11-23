"""Answer path round 7 (2026-11-22): the published concept catalog (Output B) in Ask. Contract C1, decision D3, tests
T1 and T3 of docs/plans/2026-11-22-v2-step6-harness.md.

Only approved (`corrected`) concepts of the latest published release count. Their label and synonyms are extra names of
the definitions they link to with exactMatch or closeMatch, offered to the planner beside that definition when the
question states them. A native name of the model wins; one name of two definitions on different tables is a
clarification naming both. Concepts whose every evidence passage is withheld never count. With V2_ASK_CATALOG=off the
reply is today's reply plus `semantics.mode == "off"`. The planner is scripted: it measures a definition only when the
prompt names the question's word beside it, as a model reading the card would."""
import json
from pathlib import Path

import pytest

import semantic_pipeline_v2.answer_path as ap
import semantic_pipeline_v2.concept_catalog as cc
from semantic_pipeline_v2.evidence_links import EntityKeys
from semantic_pipeline_v2.workspace import load_workspace
from test_answer_plan import ADMIN, FIN, plan
from test_answer_plan5 import RESTRICTED

ROOT = Path(__file__).resolve().parents[1]
WS = load_workspace(FIN)
FULL = {f["id"].split(":definition:", 1)[1].split("/", 1)[1]: f["id"]
        for f in WS["knowledge"]["structured"]["fields"] + WS["knowledge"]["structured"]["metrics"]}
CASA_AMOUNT, CARD_AMOUNT, CHANNEL = "field/CASA_Txn/Amount", "field/CreditCard_Txn/Amount", "field/CASA_Txn/Channel"
ASKED = "What were the total takings recorded?"


def concept(cid, label, synonyms, links, status="corrected", chunks=(("c-1", "src-a"),)):
    return {"id": cid, "label": label, "definition": f"{label}.", "synonyms": list(synonyms), "examples": [],
            "origin": "document", "type": "measure", "unit": None, "grain": None, "status": status, "actor": "pipeline-b",
            "time": None, "broad": False, "level": "coarse", "parent": None,
            "links": [{"definition_id": FULL[d], "predicate": p, "status": "proposed", "reason": "test"} for d, p in links],
            "evidence": {"chunks": [c for c, _ in chunks], "refs": [{"chunkId": c, "sourceId": s, "revision": "r"} for c, s in chunks]}}


TAKINGS = concept("takings", "Deposit takings", ["takings", "till receipts"], [(CASA_AMOUNT, "exactMatch")])


class Releases:
    """`publish.Releases` over in-memory release documents: [(version, text)], oldest first."""

    def __init__(self, *concept_sets):
        base = (ROOT / "demo_data/finance/finance.ossie.yaml").read_text()
        self.docs = [(n, cc.export_ossie(base, {"concepts": s})) for n, s in enumerate(concept_sets, 1)]

    def history(self, dataset):
        return [{"version": n, "catalogVersion": f"cat-{n}", "contentSha256": f"sha-{n}", "sourceIds": []}
                for n, _ in reversed(self.docs)]

    def document(self, dataset, version):
        return dict(self.docs)[version]


def named(user, word):
    """The definition ids the plan prompt names `word` beside: a metric synonym, or a field label's 'also called'."""
    card = json.loads(user.split("\nYour reply was rejected")[0])
    ids = [m["id"] for m in card["metrics"] if word in [s.lower() for s in m["synonyms"]]]
    ids += [row[0] for t in card["tables"] for row in t["fields"]
            if "also called: " in row[1] and word in row[1].lower().split("also called: ", 1)[1]]
    return sorted(ids)


class Planner:
    """Measures the one definition the prompt names `word` beside (sum, or count for a channel); several named: the
    first; none named: says the data does not hold `word`."""

    def __init__(self, word, extra=None):
        self.word, self.extra, self.calls = word, extra or {}, []

    def complete(self, system, user):
        self.calls.append((system, user))
        ids = named(user, self.word)
        if not ids:
            return json.dumps(plan(unsupported=self.word))
        return json.dumps(plan(measure=ids[0], aggregate="sum", **self.extra))


def ask(pg, question, chat, catalog, principal=None):
    return ap.answer(question, WS, principal or ADMIN(FIN), database=pg, chunks=None, entity_keys=EntityKeys(WS), chat=chat,
                     catalog=catalog)


@pytest.fixture
def published(monkeypatch):
    monkeypatch.setenv("V2_ASK_CATALOG", "published")


def visible_all(source_id):
    return True


# ---------------------------------------------------------------- T1: an approved published synonym is used

def test_an_approved_published_synonym_resolves_to_its_definition_and_is_answered(answer_pg, published):
    catalog = ap.published_catalog(Releases([TAKINGS]), FIN, visible_all)
    assert catalog["release"] == {"version": 1, "catalogVersion": "cat-1"}
    result = ask(answer_pg, ASKED, Planner("takings"), catalog)
    assert result["status"] == "answered", result.get("reason")
    assert [f["definition"] for f in result["facts"]] == [FULL[CASA_AMOUNT]]
    semantics = result["semantics"]
    assert semantics["mode"] == "published" and semantics["release"] == {"version": 1, "catalogVersion": "cat-1"}
    assert semantics["concepts"] == [{"id": "takings", "label": "Deposit takings", "status": "corrected", "matched": "takings",
                                      "via": "synonym", "definitions": [FULL[CASA_AMOUNT]], "used": True}]
    assert semantics["definitionsUsed"] == [FULL[CASA_AMOUNT]]


def test_a_proposed_concept_is_not_used(answer_pg, published):
    catalog = ap.published_catalog(Releases([{**TAKINGS, "status": "proposed"}]), FIN, visible_all)
    chat = Planner("takings")
    result = ask(answer_pg, ASKED, chat, catalog)
    assert result["status"] == "refused" and result["refusalReason"] == "no supported answer"
    assert result["semantics"]["concepts"] == [] and result["semantics"]["definitionsUsed"] == []
    assert not named(chat.calls[0][1], "takings")


def test_an_approved_concept_only_in_an_older_release_or_in_no_release_is_not_used(answer_pg, published):
    other = concept("float", "Float", ["float"], [(CARD_AMOUNT, "exactMatch")])
    for releases in (Releases([TAKINGS], [other]), Releases()):
        catalog = ap.published_catalog(releases, FIN, visible_all)
        result = ask(answer_pg, ASKED, Planner("takings"), catalog)
        assert result["status"] == "refused" and result["semantics"]["concepts"] == []
    assert ap.published_catalog(Releases(), FIN, visible_all) == {"release": None, "concepts": []}


def test_a_link_weaker_than_close_match_contributes_no_name(answer_pg, published):
    broad = concept("takings", "Takings", ["takings"], [(CASA_AMOUNT, "relatedMatch")])
    result = ask(answer_pg, ASKED, Planner("takings"), ap.published_catalog(Releases([broad]), FIN, visible_all))
    assert result["status"] == "refused"


# ---------------------------------------------------------------- ambiguity and native names

def test_a_synonym_of_two_definitions_on_different_tables_is_a_clarification_naming_both(answer_pg, published):
    two = concept("takings", "Takings", ["takings"], [(CASA_AMOUNT, "exactMatch"), (CARD_AMOUNT, "exactMatch")])
    chat = Planner("takings")
    result = ask(answer_pg, ASKED, chat, ap.published_catalog(Releases([two]), FIN, visible_all))
    assert named(chat.calls[0][1], "takings") == [CASA_AMOUNT, CARD_AMOUNT]
    assert result["status"] == "clarify", result.get("reason")
    assert "CASA_Txn" in result["reason"] and "CreditCard_Txn" in result["reason"] and "takings" in result["reason"]
    assert not result["facts"] and result["conclusion"] is None
    assert result["semantics"]["concepts"][0]["definitions"] == sorted([FULL[CASA_AMOUNT], FULL[CARD_AMOUNT]])


def test_a_native_name_wins_over_a_concept_synonym(answer_pg, published):
    # "transaction channel" is the label of field/CASA_Txn/Channel; a concept that calls it the amount is not offered
    wrong = concept("channel-amount", "Channel amount", ["transaction channel"], [(CASA_AMOUNT, "exactMatch")])
    question = "Which transaction channel was used most?"
    chat = Planner("transaction channel")
    result = ask(answer_pg, question, chat, ap.published_catalog(Releases([wrong]), FIN, visible_all))
    assert not named(chat.calls[0][1], "transaction channel")
    off = Planner("transaction channel")
    ask(answer_pg, question, off, None)
    assert chat.calls[0][1] == off.calls[0][1]          # the prompt is today's prompt
    [listed] = result["semantics"]["concepts"]
    assert listed["matched"] == "transaction channel" and listed["used"] is False


# ---------------------------------------------------------------- T3: scoping to the principal

def test_a_concept_whose_every_evidence_passage_is_withheld_is_not_used_or_named(answer_pg, published):
    hidden = concept("takings", "Takings", ["takings"], [(CASA_AMOUNT, "exactMatch")], chunks=(("c-1", "W"), ("c-2", "W")))
    visible = lambda s: s != "W"   # noqa: E731
    catalog = ap.published_catalog(Releases([hidden]), FIN, visible)
    assert catalog["concepts"] == []
    chat = Planner("takings")
    result = ask(answer_pg, ASKED, chat, catalog, RESTRICTED(FIN, "W"))
    assert result["status"] == "refused" and "takings" not in json.dumps(result["semantics"])
    assert not named(chat.calls[0][1], "takings")
    # Changed by the controller on 2026-11-22: one withheld passage is enough to leave the concept out, because its
    # label and synonyms are written from all its passages (the rule `/api/catalog` follows since P8f). It was: kept.
    mixed = concept("takings", "Takings", ["takings"], [(CASA_AMOUNT, "exactMatch")], chunks=(("c-1", "W"), ("c-2", "R")))
    assert ap.published_catalog(Releases([mixed]), FIN, visible)["concepts"] == []
    readable = concept("takings", "Takings", ["takings"], [(CASA_AMOUNT, "exactMatch")], chunks=(("c-1", "R"), ("c-2", "R")))
    assert [c["id"] for c in ap.published_catalog(Releases([readable]), FIN, visible)["concepts"]] == ["takings"]


# ---------------------------------------------------------------- the switch

def test_off_gives_todays_reply_plus_mode_off(answer_pg, monkeypatch):
    monkeypatch.setenv("V2_ASK_CATALOG", "published")
    catalog = ap.published_catalog(Releases([TAKINGS]), FIN, visible_all)
    monkeypatch.setenv("V2_ASK_CATALOG", "off")
    assert ap.published_catalog(Releases([TAKINGS]), FIN, visible_all) is None
    with_catalog, without = Planner("takings"), Planner("takings")
    a = ask(answer_pg, ASKED, with_catalog, catalog)
    b = ask(answer_pg, ASKED, without, None)
    assert a["semantics"] == {"mode": "off", "release": None, "concepts": [], "definitionsUsed": []}
    assert a == b and [u for _, u in with_catalog.calls] == [u for _, u in without.calls]
    assert a["status"] == "refused"
    monkeypatch.delenv("V2_ASK_CATALOG")         # the shipped default
    assert ask(answer_pg, ASKED, Planner("takings"), catalog)["semantics"]["mode"] == ap.catalog_mode()


# ---------------------------------------------------------------- the report

def test_semantics_lists_each_matched_concept_and_marks_used(answer_pg, published):
    touch = concept("touchpoint", "Touchpoint", ["touchpoint"], [(CHANNEL, "closeMatch")])
    catalog = ap.published_catalog(Releases([TAKINGS, touch]), FIN, visible_all)
    result = ask(answer_pg, "What were the total takings recorded at each touchpoint?", Planner("takings"), catalog)
    assert result["status"] == "answered", result.get("reason")
    by = {c["id"]: c for c in result["semantics"]["concepts"]}
    assert by["takings"]["used"] is True and by["touchpoint"]["used"] is False
    assert by["touchpoint"]["matched"] == "touchpoint" and by["touchpoint"]["definitions"] == [FULL[CHANNEL]]
    # matched through its label
    labelled = concept("till", "Till receipts", [], [(CASA_AMOUNT, "exactMatch")])
    result = ask(answer_pg, "What were the total till receipts?", Planner("till receipts"),
                 ap.published_catalog(Releases([labelled]), FIN, visible_all))
    assert result["semantics"]["concepts"][0]["via"] == "label" and result["semantics"]["concepts"][0]["used"] is True


# ---------------------------------------------------------------- narrowing found by the T2 replay (2026-11-22)

def test_a_concept_about_a_whole_record_gives_no_name_to_its_identifier(answer_pg, published):
    # "Sales order" -> SalesOrderNumber offered "orders" beside an identifier; counts of orders then read the identifier
    whole = concept("holding", "Holding", ["holdings"], [("field/Accounts/AccountNumber", "closeMatch")])
    chat = Planner("holdings")
    result = ask(answer_pg, "How many holdings were open?", chat, ap.published_catalog(Releases([whole]), FIN, visible_all))
    assert not named(chat.calls[0][1], "holdings") and result["semantics"]["concepts"][0]["definitions"] == []


def test_a_shorter_name_inside_a_longer_concept_name_is_not_offered(answer_pg, published):
    # "orders" of "purchase orders" is not the sales order concept's "orders"
    takings = concept("takings", "Takings", ["takings"], [(CASA_AMOUNT, "exactMatch")])
    longer = concept("card-takings", "Card takings", [], [])
    chat = Planner("takings")
    result = ask(answer_pg, "What were the total card takings?", chat,
                 ap.published_catalog(Releases([takings, longer]), FIN, visible_all))
    assert not named(chat.calls[0][1], "takings") and result["status"] == "refused"


def test_a_concept_word_inside_a_record_name_the_question_states_is_not_offered(answer_pg, published):
    mws = load_workspace("manufacturing")
    full = next(f["id"] for f in mws["knowledge"]["structured"]["fields"] if f["id"].endswith("field/Production_Product/Color"))
    goods = {**concept("goods", "Goods", ["goods"], []), "links": [{"definition_id": full, "predicate": "closeMatch",
                                                                   "status": "proposed"}]}

    class Base(Releases):
        def __init__(self, *sets):
            base = (ROOT / "demo_data/manufacturing/semantics/manufacturing.ossie.yaml").read_text()
            self.docs = [(n, cc.export_ossie(base, {"concepts": s})) for n, s in enumerate(sets, 1)]
    chat = Planner("goods")
    result = ap.answer("How many orders did Versatile Sporting Goods Company place?", mws, ADMIN("manufacturing"),
                       database=answer_pg, chunks=None, entity_keys=EntityKeys(mws), chat=chat,
                       catalog=ap.published_catalog(Base([goods]), "manufacturing", visible_all))
    assert not named(chat.calls[0][1], "goods")
    assert result["semantics"]["concepts"][0]["matched"] == "goods"


def test_a_concept_naming_several_values_of_one_record_gives_no_name(answer_pg, published):
    # "Suspicious transaction" closeMatch to nine STR_Transaction fields distracted the planner (ex-fin-14)
    record = concept("casa-line", "Deposit line", ["deposit line"], [(CASA_AMOUNT, "closeMatch"), (CHANNEL, "closeMatch")])
    chat = Planner("deposit line")
    result = ask(answer_pg, "What was the total deposit line?", chat, ap.published_catalog(Releases([record]), FIN, visible_all))
    assert not named(chat.calls[0][1], "deposit line") and result["semantics"]["concepts"][0]["definitions"] == []
    # an exactMatch among them is the one the name stands for
    exact = concept("casa-line", "Deposit line", ["deposit line"], [(CASA_AMOUNT, "exactMatch"), (CHANNEL, "closeMatch")])
    chat = Planner("deposit line")
    result = ask(answer_pg, "What was the total deposit line?", chat, ap.published_catalog(Releases([exact]), FIN, visible_all))
    assert named(chat.calls[0][1], "deposit line") == [CASA_AMOUNT] and result["status"] == "answered"
