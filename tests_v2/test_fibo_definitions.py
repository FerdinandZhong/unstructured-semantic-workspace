"""P2e part 3: FIBO definitions are attributed secondary definitions, and lint refuses a loose FIBO reference."""
import copy
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "demo_data"))
import enrich_ossie  # noqa: E402

MODEL = ROOT / "demo_data/finance/finance.ossie.yaml"
MAPPED = {"customer", "account", "product", "currency", "relationship_manager"}   # implementer-listed: the only mappings
LEFT_OUT = {"fx_transaction", "letter_of_credit_transaction", "credit_card_transaction", "loan_transaction",
            "transaction", "branch", "industry"}


def model():
    return yaml.safe_load(MODEL.read_text())


def test_only_clearly_matching_concepts_carry_a_complete_attributed_fibo_reference():
    top = enrich_ossie.ext(model())
    with_fibo = {c["id"]: c for c in top["concepts"] if "fibo" in c}
    assert set(with_fibo) == MAPPED and not LEFT_OUT & set(with_fibo)
    for concept in with_fibo.values():
        ref = concept["fibo"]
        assert ref["iri"].startswith("https://spec.edmcouncil.org/fibo/ontology/")
        assert ref["licence"] == "MIT" and ref["licence_url"].endswith("edmcouncil/fibo/blob/master/LICENSE")
        assert ref["definition"] and ref["label"] and ref["fetched"] == "2026-11-21"
        assert ref["definition"] != concept["definition"]          # the model's own definition is kept, not replaced
    assert top["attributions"][0]["id"] == "fibo" and top["attributions"][0]["licence"] == "MIT"
    assert enrich_ossie.lint(model()) == []


def test_lint_requires_an_iri_and_a_licence_note_on_a_fibo_reference():
    broken = copy.deepcopy(model())
    data = enrich_ossie.ext(broken)
    account = next(c for c in data["concepts"] if c["id"] == "account")
    account["fibo"] = {"label": "account", "definition": "x" * 30}
    enrich_ossie._set_ext(broken, data)
    errors = enrich_ossie.lint(broken)
    assert "concept account: fibo reference needs a FIBO IRI" in errors
    assert "concept account: fibo reference has no licence" in errors
    assert "concept account: fibo reference has no licence_url" in errors
    assert "concept account: fibo reference has no attribution" in errors
