"""Finance synthetic document pack: reproducibility, verbatim planted statements, grounding in the workbook rows."""
import csv
import hashlib
import importlib.util
import json
import re
from collections import Counter
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
PROJ = ROOT / "demo_data/finance/local/finance_workbook"
GEN = ROOT / "demo_data/finance/documents/generate_pack.py"
PACK = ROOT / "demo_data/finance/documents"
MODEL = ROOT / "demo_data/finance/finance.ossie.yaml"

pytestmark = pytest.mark.skipif(not PROJ.is_dir(), reason="finance CSV projections absent (demo_data/finance/local/finance_workbook)")


def _rows(table):
    with open(PROJ / f"{table}.csv", newline="") as handle:
        return list(csv.DictReader(handle))


def _generate(out):
    spec = importlib.util.spec_from_file_location("generate_pack", GEN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.build(PROJ, out)
    return out


@pytest.fixture(scope="module")
def pack(tmp_path_factory):
    out = _generate(tmp_path_factory.mktemp("pack"))
    return json.loads((out / "manifest.json").read_text()), out


def _text(out, doc):
    return (out / doc["path"]).read_text()


def _files(out):
    return {str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.rglob("*")) if p.is_file()}


def test_regeneration_is_byte_identical(tmp_path_factory, pack):
    manifest, out = pack
    again = _generate(tmp_path_factory.mktemp("pack2"))
    assert _files(out) == _files(again)
    for doc in manifest["documents"]:
        assert doc["sha256"] == hashlib.sha256((out / doc["path"]).read_bytes()).hexdigest()


def test_committed_files_match_generator(pack):
    manifest, out = pack
    assert (PACK / "manifest.json").read_bytes() == (out / "manifest.json").read_bytes()
    for doc in manifest["documents"]:
        assert (PACK / doc["path"]).read_bytes() == (out / doc["path"]).read_bytes()


def test_manifest_header_and_size(pack):
    manifest, _ = pack
    assert manifest["synthetic"] is True and manifest["date"] == "2026-11-21" and manifest["seed"] is not None
    assert "not an independent gold set" in manifest["note"]
    assert 10 <= len(manifest["documents"]) <= 12


def test_every_document_is_labelled_and_clean(pack):
    from multimedia_rag.fields import PATTERNS
    manifest, out = pack
    for doc in manifest["documents"]:
        text = _text(out, doc)
        assert text.splitlines()[0].startswith("SYNTHETIC DEMO DOCUMENT")
        assert not PATTERNS["nric"].search(text), doc["id"]
        assert not PATTERNS["email"].search(text) and not PATTERNS["phone_sg"].search(text)
        assert 250 <= len(text.split()) <= 700, (doc["id"], len(text.split()))


def test_sentences_are_verbatim(pack):
    manifest, out = pack
    for doc in manifest["documents"]:
        text = _text(out, doc)
        for st in doc["statements"]:
            assert st["sentence"] in text, (doc["id"], st["sentence"])


def test_definition_ids_exist_in_model(pack):
    manifest, _ = pack
    model = yaml.safe_load(MODEL.read_text())
    fields = {f"{d['name']}/{f['name']}" for d in model["datasets"] for f in d["fields"]}
    metrics = {m["name"] for m in model["metrics"]}
    prefix = f"financial-services:definition:{model['name']}/"
    seen = 0
    for doc in manifest["documents"]:
        for st in doc["statements"]:
            d = st.get("definition")
            if st["kind"] in ("recorded_value", "commitment"):
                assert d, (doc["id"], st["sentence"])
            if d:
                seen += 1
                assert d.startswith(prefix), d
                kind, name = d[len(prefix):].split("/", 1)
                assert (name in fields) if kind == "field" else (name in metrics), d
            else:
                assert st["kind"] in ("no_counterpart", "trap")
    assert seen > 20


def test_recorded_values_equal_workbook_rows(pack):
    manifest, out = pack
    for doc in manifest["documents"]:
        for st in doc["statements"]:
            if st["kind"] != "recorded_value":
                continue
            rec = st["recorded"]
            rows = [r for r in _rows(rec["table"]) if all(r[k] == v for k, v in rec["key"].items())]
            assert rows, (doc["id"], rec)
            values = {r[rec["field"]] for r in rows}
            if rec["field"] == "Amount":
                got = {round(float(v), 2) for v in values}
                assert got == {round(float(rec["value"]), 2)}, (doc["id"], rec)
            else:
                assert values == {rec["value"]}, (doc["id"], rec, values)
            assert rec["surface"] in st["sentence"], (doc["id"], rec)
            assert st["definition"].endswith(f"/field/{rec['table']}/{rec['field']}")


def _txns(table, account, period, credit=None, over=None):
    seen, out = set(), []
    for r in _rows(table):
        if r["AccountNumber"] != account or r["TransactionID"] in seen:
            continue
        if not period[0] <= r["TransactionDate"] <= period[1]:
            continue
        if credit is not None and r["DebitCredit"] != credit:
            continue
        if over is not None and float(r["Amount"]) <= over:
            continue
        seen.add(r["TransactionID"])
        out.append(float(r["Amount"]))
    return out


def _recompute(check):
    if check["op"] == "status_all_active":
        rows = [r for r in _rows("Accounts") if r["AccountNumber"] == check["account"]]
        return float(all(r["Status"] == "Active" for r in rows) and bool(rows))
    vals = _txns(check["table"], check["account"], check["period"], check.get("debit_credit"), check.get("over"))
    return round(sum(vals), 2) if check["op"] == "sum" else float(len(vals))


def test_commitment_truth_is_recomputed(pack):
    manifest, _ = pack
    met = []
    for doc in manifest["documents"]:
        for st in doc["statements"]:
            if st["kind"] != "commitment":
                continue
            c = st["commitment"]
            actual = _recompute(c["check"])
            assert actual == pytest.approx(c["actual"]), (doc["id"], st["sentence"])
            ok = actual >= c["threshold"] if c["comparison"] == "ge" else actual <= c["threshold"]
            assert ok == c["met"], (doc["id"], st["sentence"])
            assert c["logic"] and st["stated"]["unit"] and st["stated"]["period"]
            met.append(c["met"])
    assert len(met) >= 10 and 3 <= met.count(False) <= len(met) - 3


def test_kind_mix(pack):
    manifest, _ = pack
    total = Counter()
    for doc in manifest["documents"]:
        kinds = Counter(s["kind"] for s in doc["statements"])
        total.update(kinds)
        assert kinds["recorded_value"] >= 2, doc["id"]
        assert len(kinds) >= 2, doc["id"]
        assert {"customer_keys"} <= doc.keys() and doc["customer_keys"]
    assert total["commitment"] >= 10 and total["no_counterpart"] >= 6 and total["trap"] >= 6
    for st in (s for d in manifest["documents"] for s in d["statements"] if s["kind"] == "trap"):
        assert st["trap"]["reason"]


def test_customers_are_workbook_rows_and_spread(pack):
    manifest, out = pack
    ids = {r["CustomerID"] for r in _rows("Customers")}
    per = Counter()
    for doc in manifest["documents"]:
        assert set(doc["customer_keys"]) <= ids
        text = _text(out, doc)
        for key in doc["customer_keys"]:
            assert key in text
            per[key] += 1
    assert 4 <= len(per) <= 6 and max(per.values()) >= 3


def test_amendment_names_original_and_changes_one_term(pack):
    manifest, out = pack
    docs = {d["id"]: d for d in manifest["documents"]}
    amend = [d for d in docs.values() if d.get("amends")]
    assert len(amend) == 1
    a = amend[0]
    assert a["amends"] in docs and a["amends"] in _text(out, a)
    old = {s["sentence"]: s for s in docs[a["amends"]]["statements"]}
    changed = [s for s in a["statements"] if s.get("supersedes")]
    assert len(changed) == 1 and changed[0]["supersedes"] in old
    assert changed[0]["commitment"]["threshold"] != old[changed[0]["supersedes"]]["commitment"]["threshold"]


def test_similar_wording_across_customers(pack):
    manifest, out = pack
    texts = {d["id"]: _text(out, d) for d in manifest["documents"]}
    from difflib import SequenceMatcher
    best = max(SequenceMatcher(None, texts[a], texts[b]).ratio()
               for a in texts for b in texts if a < b and set(next(d for d in manifest["documents"] if d["id"] == a)["customer_keys"])
               != set(next(d for d in manifest["documents"] if d["id"] == b)["customer_keys"]))
    assert best > 0.6
