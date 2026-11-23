"""Manufacturing synthetic document pack 2: reproducibility, verbatim planted statements, grounding in the CSV rows."""
import csv
import hashlib
import importlib.util
import json
import re
from collections import Counter
from datetime import date
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CSV_DIR = ROOT / "demo_data/manufacturing/data/csv"
DOCS = ROOT / "demo_data/manufacturing/documents"
GEN = DOCS / "generate_pack2.py"
PACK = DOCS / "pack2"
MODEL = ROOT / "demo_data/manufacturing/semantics/manufacturing.ossie.yaml"
PREFIX = "manufacturing:definition:adventureworks_manufacturing_demo/"

pytestmark = pytest.mark.skipif(not CSV_DIR.is_dir(), reason="manufacturing CSV data absent (demo_data/manufacturing/data/csv)")


@lru_cache(maxsize=None)
def rows(table):
    with open(CSV_DIR / f"{table}.csv", newline="", encoding="utf-8") as handle:
        return tuple(csv.DictReader(handle))


def _generate(out):
    spec = importlib.util.spec_from_file_location("generate_pack2", GEN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.build(CSV_DIR, out)
    return out


@pytest.fixture(scope="module")
def pack(tmp_path_factory):
    out = _generate(tmp_path_factory.mktemp("pack2"))
    return json.loads((out / "manifest.json").read_text()), out


def _text(out, doc):
    return (out / doc["path"]).read_text()


def _files(out):
    return {str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.rglob("*")) if p.is_file()}


def test_regeneration_is_byte_identical(tmp_path_factory, pack):
    manifest, out = pack
    again = _generate(tmp_path_factory.mktemp("pack2b"))
    assert _files(out) == _files(again)
    for doc in manifest["documents"]:
        assert doc["sha256"] == hashlib.sha256((out / doc["path"]).read_bytes()).hexdigest()


def test_committed_files_match_generator(pack):
    manifest, out = pack
    assert (PACK / "manifest.json").read_bytes() == (out / "manifest.json").read_bytes()
    for doc in manifest["documents"]:
        assert (PACK / doc["path"]).read_bytes() == (out / doc["path"]).read_bytes()


def test_existing_contracts_untouched():
    existing = json.loads((DOCS / "manifest.json").read_text())
    assert len(existing["documents"]) == 7


def test_manifest_header_and_size(pack):
    manifest, _ = pack
    assert manifest["synthetic"] is True and manifest["date"] == "2026-11-21" and manifest["seed"] is not None
    assert "not an independent gold set" in manifest["note"]
    assert 12 <= len(manifest["documents"]) <= 14
    assert re.fullmatch(r"MFG-1\d\d", manifest["documents"][0]["id"])


def test_every_document_is_labelled_and_clean(pack):
    from multimedia_rag.fields import PATTERNS
    manifest, out = pack
    for doc in manifest["documents"]:
        text = _text(out, doc)
        assert text.splitlines()[0].startswith("SYNTHETIC DEMO DOCUMENT"), doc["id"]
        assert not PATTERNS["nric"].search(text), doc["id"]
        assert not PATTERNS["email"].search(text) and not PATTERNS["phone_sg"].search(text)
        assert 300 <= len(text.split()) <= 650, (doc["id"], len(text.split()))


def test_sentences_are_verbatim(pack):
    manifest, out = pack
    for doc in manifest["documents"]:
        text = _text(out, doc)
        for st in doc["statements"]:
            assert st["sentence"] in text, (doc["id"], st["sentence"])


def _model():
    model = yaml.safe_load(MODEL.read_text())
    assert model["name"] == "adventureworks_manufacturing_demo"
    fields = {f"{d['name']}/{f['name']}" for d in model["datasets"] for f in d["fields"]}
    return fields, {m["name"] for m in model["metrics"]}


def test_definition_ids_exist_in_model(pack):
    manifest, _ = pack
    fields, metrics = _model()
    for doc in manifest["documents"]:
        for st in doc["statements"]:
            d = st.get("definition")
            if st["kind"] in ("recorded_value", "commitment"):
                assert d, (doc["id"], st["sentence"])
            if d:
                assert d.startswith(PREFIX), d
                kind, name = d[len(PREFIX):].split("/", 1)
                assert (name in fields) if kind == "field" else (name in metrics), d
            else:
                assert st["kind"] in ("no_counterpart", "trap")
            if st["kind"] == "trap" and st["trap"].get("tempts"):
                kind, name = st["trap"]["tempts"][len(PREFIX):].split("/", 1)
                assert (name in fields) if kind == "field" else (name in metrics)


def _norm(v):
    v = v.strip()
    if re.fullmatch(r"\d{4}-\d\d-\d\d 00:00:00\.000", v):
        return v[:10]
    return v


def _same(a, b):
    a, b = _norm(a), _norm(b)
    try:
        return abs(float(a) - float(b)) < 1e-6
    except ValueError:
        return a == b


def test_recorded_values_equal_rows(pack):
    manifest, _ = pack
    for doc in manifest["documents"]:
        for st in doc["statements"]:
            if st["kind"] != "recorded_value":
                continue
            rec = st["recorded"]
            found = [r for r in rows(rec["table"]) if all(r[k].strip() == str(v) for k, v in rec["key"].items())]
            assert len(found) == 1, (doc["id"], rec)
            assert _same(found[0][rec["field"]], rec["value"]), (doc["id"], rec, found[0][rec["field"]])
            assert rec["surface"] in st["sentence"], (doc["id"], rec)
            assert st["definition"] == f"{PREFIX}field/{rec['table']}/{rec['field']}"
            try:
                assert abs(float(rec["surface"].replace(",", "")) - float(rec["value"])) < 0.005
            except ValueError:
                pass


def _days(a, b):
    return (date.fromisoformat(b[:10]) - date.fromisoformat(a[:10])).days


def _in(v, c):
    return c["from"] <= v[:10] <= c["to"]


def _recompute(c):
    op = c["op"]
    if op.startswith("sales_") or op in ("avg_unit_price", "late_dispatch", "ship_gap_over"):
        heads = {r["SalesOrderID"]: r for r in rows("Sales_SalesOrderHeader")
                 if r["CustomerID"] == str(c["customer"]) and _in(r["OrderDate"], c)}
        if op == "sales_orders":
            return float(len(heads))
        if op == "late_dispatch":
            return float(sum(1 for r in heads.values() if r["ShipDate"][:10] > r["DueDate"][:10]))
        if op == "ship_gap_over":
            return float(sum(1 for r in heads.values() if _days(r["OrderDate"], r["ShipDate"]) > c["days"]))
        lines = [d for d in rows("Sales_SalesOrderDetail")
                 if d["SalesOrderID"] in heads and (c.get("product") is None or d["ProductID"] == str(c["product"]))]
        if op == "sales_units":
            return float(sum(int(d["OrderQty"]) for d in lines))
        if op == "sales_revenue":
            return round(sum(float(d["LineTotal"]) for d in lines), 2)
        return round(sum(float(d["UnitPrice"]) for d in lines) / len(lines), 4)
    if op.startswith("wo_"):
        wos = [w for w in rows("Production_WorkOrder") if w["ProductID"] == str(c["product"]) and _in(w["StartDate"], c)]
        req, scr = sum(int(w["OrderQty"]) for w in wos), sum(int(w["ScrappedQty"]) for w in wos)
        return {"wo_requested": float(req), "wo_scrapped": float(scr), "wo_scrap_rate": round(scr / req, 6),
                "wo_open": float(sum(1 for w in wos if not w["EndDate"].strip()))}[op]
    if op == "inventory":
        return float(sum(int(float(i["Quantity"])) for i in rows("Production_ProductInventory")
                         if i["ProductID"] == str(c["product"]) and (c.get("location") is None or i["LocationID"] == str(c["location"]))))
    if op.startswith("po_"):
        heads = {h["PurchaseOrderID"] for h in rows("Purchasing_PurchaseOrderHeader")
                 if h["VendorID"] == str(c["vendor"]) and _in(h["OrderDate"], c)}
        if op == "po_count":
            return float(len(heads))
        lines = [d for d in rows("Purchasing_PurchaseOrderDetail") if d["PurchaseOrderID"] in heads]
        if op == "po_reject_rate":
            return round(sum(float(d["RejectedQty"]) for d in lines) / sum(float(d["ReceivedQty"]) for d in lines), 6)
        return float(sum(float(d["OrderQty"]) for d in lines))
    if op == "field_value":
        r = [x for x in rows(c["table"]) if all(x[k].strip() == str(v) for k, v in c["key"].items())]
        assert len(r) == 1
        return float(r[0][c["field"]])
    if op == "days_since":
        r = [x for x in rows(c["table"]) if all(x[k].strip() == str(v) for k, v in c["key"].items())]
        assert len(r) == 1
        return float(_days(r[0][c["field"]], c["until"]))
    raise AssertionError(op)


def test_commitment_truth_is_recomputed(pack):
    manifest, _ = pack
    met = []
    for doc in manifest["documents"]:
        for st in doc["statements"]:
            if st["kind"] != "commitment":
                continue
            c = st["commitment"]
            actual = _recompute(c["check"])
            assert actual == pytest.approx(c["actual"], abs=1e-6), (doc["id"], st["sentence"], actual, c["actual"])
            ok = actual >= c["threshold"] if c["comparison"] == "ge" else actual <= c["threshold"]
            assert ok == c["met"], (doc["id"], st["sentence"])
            assert c["logic"] and st["stated"]["unit"] and st["stated"]["period"]
            met.append(c["met"])
    assert len(met) >= 24 and met.count(False) >= 8 and met.count(True) >= 8


def test_kind_mix_and_coverage(pack):
    manifest, _ = pack
    fields, metrics = _model()
    total, touched = Counter(), set()
    for doc in manifest["documents"]:
        kinds = Counter(s["kind"] for s in doc["statements"])
        total.update(kinds)
        assert kinds["recorded_value"] >= 2 and len(kinds) >= 3, doc["id"]
        assert doc["customer_keys"] or doc.get("entity_keys")
        touched.update(s["definition"] for s in doc["statements"] if s.get("definition"))
    assert total["commitment"] >= 24 and total["no_counterpart"] >= 12 and total["trap"] >= 12
    assert {PREFIX + "metric/" + m for m in metrics} <= touched
    assert len([t for t in touched if "/field/" in t]) >= 45
    for st in (s for d in manifest["documents"] for s in d["statements"] if s["kind"] == "trap"):
        assert st["trap"]["reason"]


def test_paraphrase_flag(pack):
    manifest, _ = pack
    flagged = 0
    for doc in manifest["documents"]:
        for st in doc["statements"]:
            if st["kind"] in ("recorded_value", "commitment"):
                assert isinstance(st["paraphrase"], bool)
                name = st["definition"].rsplit("/", 1)[1]
                spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name).replace("_", " ").lower()
                if st["paraphrase"]:
                    flagged += 1
                    assert name.lower() not in st["sentence"].lower() and spaced not in st["sentence"].lower(), st["sentence"]
            else:
                assert "paraphrase" not in st
    assert flagged >= 40


def test_entities_are_real_and_spread(pack):
    manifest, out = pack
    custs = {r["CustomerID"] for r in rows("Sales_Customer")}
    vendors = {r["BusinessEntityID"] for r in rows("Purchasing_Vendor")}
    per = Counter()
    for doc in manifest["documents"]:
        for k in doc["customer_keys"]:
            assert k in custs and k in _text(out, doc)
        for k in doc.get("vendor_keys", []):
            assert k in vendors and k in _text(out, doc)
        for k in doc["customer_keys"] + doc.get("vendor_keys", []):
            per[k] += 1
    assert len(per) >= 5 and max(per.values()) >= 3


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


def test_near_identical_documents_for_different_entities(pack):
    manifest, out = pack
    docs = manifest["documents"]
    best = 0
    for i, a in enumerate(docs):
        for b in docs[i + 1:]:
            if (a["customer_keys"], a.get("vendor_keys")) != (b["customer_keys"], b.get("vendor_keys")):
                best = max(best, SequenceMatcher(None, _text(out, a), _text(out, b)).ratio())
    assert best > 0.7


def test_no_sentence_duplicates_existing_contracts(pack):
    manifest, _ = pack
    old = set()
    for p in (DOCS / "markdown").glob("MFG-00*.md"):
        for line in p.read_text().splitlines():
            old.add(re.sub(r"^[#\d. ]+", "", line).strip().lower())
            old.update(s.strip().lower() for s in re.split(r"(?<=\.) ", line))
    old.discard("")
    for doc in manifest["documents"]:
        for st in doc["statements"]:
            assert st["sentence"].strip().lower() not in old, st["sentence"]
