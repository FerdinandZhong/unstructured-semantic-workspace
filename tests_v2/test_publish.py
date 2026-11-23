"""Real publish: preview, confirm, history and download of a versioned Ossie export. Private PostgreSQL only."""

import hashlib
from pathlib import Path
from urllib.parse import urlencode

import yaml

from semantic_pipeline_v2.evidence_links import EvidenceLinks
from semantic_pipeline_v2.mapping_corrections import CorrectionRequest, MappingCorrections
from semantic_pipeline_v2.semantic_store import SemanticStore
from semantic_pipeline_v2.workspace import review_database
from test_catalog_graph import HIDDEN, OPEN, concept, link, pg_bin, session
from test_workspace import running_server

DATASET = "financial-services"
ROOT = Path(__file__).resolve().parents[1]
BODY = {"dataset": DATASET}
FIXTURE_TITLE = "Customer risk rating field alignment"


def seed(database, *, concepts=None):
    store = SemanticStore(database)
    store.import_ossie(DATASET, ROOT / "demo_data/finance/finance.ossie.yaml")
    definition = next(d["id"] for d in store.list_definitions(DATASET) if d["kind"] == "field")
    EvidenceLinks(database).replace(DATASET, OPEN, [link("c-open", OPEN, definition, "Customer NRIC S1234567D owes 9 days")])
    EvidenceLinks(database).replace(DATASET, HIDDEN, [link("c-hid", HIDDEN, definition, "Adverse media")])
    store.write_catalog(DATASET, "cat-1", concepts or [
        concept("open", "Open settlement concept", "document", ["c-open"], definition),
        concept("hidden", "Hidden settlement concept", "document", ["c-hid"], definition),
        {**concept("cand", "Candidate settlement concept", "document", ["c-open"], definition), "status": "candidate"},
        concept("rej", "Rejected settlement concept", "document", ["c-open"], definition)])
    return definition


def correct_concept(call, concept_id, patch):
    assert call("POST", "/api/catalog-correction", {
        "dataset": DATASET, "kind": "concept", "conceptId": concept_id, "patch": patch})[0] == 200


def mapping_correction(database, action="remove", target=None, request_id="r1"):
    request = CorrectionRequest(DATASET, OPEN, f"{OPEN}:obs-1", target, action, "anchor-1", request_id, "NRIC S1234567D")
    MappingCorrections(database).record(request, "anchor-1", "admin")


def download(call, version, headers=None):
    status, raw = call("GET", "/api/publish/release?" + urlencode({**BODY, "version": version}), headers=headers)
    assert status == 200
    return raw.decode()


def publish(call, headers=None):
    status, preview = call("POST", "/api/publish/preview", BODY, headers)
    assert status == 200, preview
    return preview


def test_preview_content_counts_and_exclusions(tmp_path):
    with review_database(tmp_path / "state", pg_bin()) as database, running_server(
            database, evidence_dir=tmp_path / "evidence") as call:
        definition = seed(database)
        correct_concept(call, "rej", {"status": "rejected"})
        mapping_correction(database)
        preview = publish(call)
        assert preview["lint"] == {"ok": True, "errors": []} and preview["nextVersion"] == 1
        assert preview["counts"]["concepts"] == 2 and preview["counts"]["mappingCorrections"] == 1
        assert preview["changes"]["concepts"]["added"] == ["hidden", "open"] and preview["unchanged"] is False
        assert "S1234567D" not in str(preview) and len(preview["fingerprint"]) == 64
        assert preview["catalogVersion"] == "cat-1"
        # nothing written by a preview
        assert call("GET", "/api/publish/history?" + urlencode(BODY))[1] == {"releases": []}
        status, done = call("POST", "/api/publish/confirm", {**BODY, "fingerprint": preview["fingerprint"]})
        assert status == 200 and done["written"] is True and done["version"] == 1
        text = download(call, 1)
        assert hashlib.sha256(text.encode()).hexdigest() == done["contentSha256"]
        assert "S1234567D" not in text and "Candidate settlement concept" not in text and "Rejected settlement concept" not in text
        model = yaml.safe_load(text)
        import json
        data = next(json.loads(e["data"]) for e in model["custom_extensions"] if e["vendor_name"] == "HELIOS")
        ids = {c["id"] for c in data["concepts"]}
        assert {"open", "hidden"} <= ids and not {"cand", "rej"} & ids
        opened = next(c for c in data["concepts"] if c["id"] == "open")
        assert opened["links"][0]["predicate"] == "closeMatch" and opened["links"][0]["definition_id"] == definition
        assert opened["evidence"]["refs"] == [{"chunkId": "c-open", "sourceId": OPEN, "revision": "b" * 64}]
        assert data["mapping_corrections"][0]["action"] == "remove" and data["release"]["catalogVersion"] == "cat-1"
        # base model unchanged
        base = yaml.safe_load((ROOT / "demo_data/finance/finance.ossie.yaml").read_text())
        assert model["datasets"] == base["datasets"] and model["metrics"] == base["metrics"]


def test_rejected_link_correction_is_excluded(tmp_path):
    with review_database(tmp_path / "state", pg_bin()) as database, running_server(
            database, evidence_dir=tmp_path / "evidence") as call:
        definition = seed(database)
        assert call("POST", "/api/catalog-correction", {
            "dataset": DATASET, "kind": "concept_link", "conceptId": "open", "definitionId": definition,
            "patch": {"status": "rejected"}})[0] == 200
        done = call("POST", "/api/publish/confirm", {**BODY, "fingerprint": publish(call)["fingerprint"]})[1]
        text = download(call, done["version"])
        data = next(__import__("json").loads(e["data"]) for e in yaml.safe_load(text)["custom_extensions"]
                    if e["vendor_name"] == "HELIOS")
        assert next(c for c in data["concepts"] if c["id"] == "open")["links"] == []


def test_stale_preview_reader_and_empty(tmp_path):
    with review_database(tmp_path / "state", pg_bin()) as database, running_server(
            database, evidence_dir=tmp_path / "evidence") as call:
        # empty: no catalog and no corrections
        SemanticStore(database).import_ossie(DATASET, ROOT / "demo_data/finance/finance.ossie.yaml")
        status, body = call("POST", "/api/publish/preview", BODY)
        assert status == 422 and "Nothing to publish" in body["error"]
        assert call("POST", "/api/publish/confirm", {**BODY, "fingerprint": "0" * 64})[0] == 422
        seed(database)
        preview = publish(call)
        correct_concept(call, "open", {"label": "Changed after preview"})
        status, body = call("POST", "/api/publish/confirm", {**BODY, "fingerprint": preview["fingerprint"]})
        assert status == 409 and "Preview again" in body["error"]
        assert call("GET", "/api/publish/history?" + urlencode(BODY))[1] == {"releases": []}
        assert call("POST", "/api/publish/confirm", {**BODY, "fingerprint": "nope"})[0] == 409
        reader = session(call, "finance_reader")
        assert call("POST", "/api/publish/preview", BODY, reader)[0] == 403
        assert call("GET", "/api/publish/history?" + urlencode(BODY), headers=reader)[0] == 200
        assert call("POST", "/api/publish/confirm", {**BODY, "fingerprint": preview["fingerprint"]}, reader)[0] == 403


def test_idempotent_confirm_and_version_increment(tmp_path):
    with review_database(tmp_path / "state", pg_bin()) as database, running_server(
            database, evidence_dir=tmp_path / "evidence") as call:
        seed(database)
        first = publish(call)
        one = call("POST", "/api/publish/confirm", {**BODY, "fingerprint": first["fingerprint"]})[1]
        two = call("POST", "/api/publish/confirm", {**BODY, "fingerprint": first["fingerprint"]})[1]
        assert one["written"] is True and two["written"] is False and two["version"] == 1
        assert "unchanged" in two["message"].lower()
        again = publish(call)
        assert again["unchanged"] is True and again["fingerprint"] == first["fingerprint"]
        correct_concept(call, "open", {"label": "Renamed"})
        changed = publish(call)
        assert changed["nextVersion"] == 2 and changed["changes"]["concepts"]["changed"] == ["open"]
        three = call("POST", "/api/publish/confirm", {**BODY, "fingerprint": changed["fingerprint"]})[1]
        assert three["version"] == 2 and three["written"] is True
        releases = call("GET", "/api/publish/history?" + urlencode(BODY))[1]["releases"]
        assert [r["version"] for r in releases] == [2, 1]
        top = releases[0]
        assert top["principalId"] == "admin" and top["catalogVersion"] == "cat-1" and top["publishedAt"]
        assert top["contentSha256"] == three["contentSha256"] and len(top["modelSha256"]) == 64
        assert top["counts"]["concepts"] == 3
        assert call("GET", "/api/publish/release?" + urlencode({**BODY, "version": 9}))[0] == 404
        assert call("GET", "/api/publish/history?dataset=nope")[0] == 403


def test_broken_export_blocks_confirm(tmp_path):
    with review_database(tmp_path / "state", pg_bin()) as database, running_server(
            database, evidence_dir=tmp_path / "evidence") as call:
        seed(database, concepts=[concept("bad", "Bad settlement concept", "document", ["c-open"], "financial-services:definition:nope/field/X/Y")])
        preview = publish(call)
        assert preview["lint"]["ok"] is False and "unknown definition" in preview["lint"]["errors"][0]
        status, body = call("POST", "/api/publish/confirm", {**BODY, "fingerprint": preview["fingerprint"]})
        assert status == 422 and "lint" in body["error"].lower()
        assert call("GET", "/api/publish/history?" + urlencode(BODY))[1] == {"releases": []}


def test_withheld_source_never_leaks(tmp_path):
    with review_database(tmp_path / "state", pg_bin()) as database, running_server(
            database, evidence_dir=tmp_path / "evidence") as call:
        seed(database)
        full = publish(call)
        assert "hidden" in full["changes"]["concepts"]["added"]
        assert call("POST", "/api/publish/confirm", {**BODY, "fingerprint": full["fingerprint"]})[1]["version"] == 1
        # the admin now loses the hidden source
        database.sql(f"INSERT INTO v2_demo_restrictions VALUES ('admin', '{HIDDEN}');")
        narrow = publish(call)
        assert "hidden" not in str(narrow).lower() and "c-hid" not in str(narrow)
        assert narrow["changes"]["concepts"]["added"] == ["open", "rej"]   # release 1 holds hidden content: not a baseline
        done = call("POST", "/api/publish/confirm", {**BODY, "fingerprint": narrow["fingerprint"]})[1]
        assert done["version"] == 2
        assert [r["version"] for r in call("GET", "/api/publish/history?" + urlencode(BODY))[1]["releases"]] == [2]
        assert call("GET", "/api/publish/release?" + urlencode({**BODY, "version": 1}))[0] == 403
        text = download(call, 2)
        assert "Hidden settlement" not in text and "c-hid" not in text and HIDDEN not in text


def test_review_page_has_no_fixture_title_or_simulation():
    page = (ROOT / "docs/design/v2-workspace/review.js").read_text()
    assert FIXTURE_TITLE not in page and "simulation" not in page.lower() and "proposal.title" not in page
    assert "'/api/publish/' + action" in page and "/api/publish/history" in page   # the DOM run is browser_catalog_graph.py


def correct_link(call, chunk, definition, patch):
    assert call("POST", "/api/catalog-correction", {
        "dataset": DATASET, "kind": "evidence_link", "chunkId": chunk, "definitionId": definition, "patch": patch})[0] == 200


def helios(text):
    import json
    return next(json.loads(e["data"]) for e in yaml.safe_load(text)["custom_extensions"] if e["vendor_name"] == "HELIOS")


def test_evidence_link_corrections_are_previewed_and_released_and_versioned(tmp_path):
    with review_database(tmp_path / "state", pg_bin()) as database, running_server(
            database, evidence_dir=tmp_path / "evidence") as call:
        definition = seed(database)
        first = publish(call)
        assert first["counts"]["evidenceLinkCorrections"] == 0 and first["evidenceLinkCorrections"] == []
        correct_link(call, "c-open", definition, {"relation": "is_about", "statedValue": "NRIC S1234567D"})
        preview = publish(call)
        assert preview["counts"]["evidenceLinkCorrections"] == 1 and preview["fingerprint"] != first["fingerprint"]
        [row] = preview["evidenceLinkCorrections"]
        assert row["chunkId"] == "c-open" and row["sourceId"] == OPEN and row["definitionId"] == definition
        assert row["patch"]["relation"] == "is_about" and row["actor"] == "admin" and row["revision"]
        assert "S1234567D" not in str(preview) and preview["changes"]["evidenceLinks"]["added"] == [f"c-open | {definition}"]
        assert call("GET", "/api/publish/history?" + urlencode(BODY))[1] == {"releases": []}   # a preview writes nothing
        done = call("POST", "/api/publish/confirm", {**BODY, "fingerprint": preview["fingerprint"]})[1]
        data = helios(download(call, done["version"]))
        assert data["evidence_link_corrections"] == [row] and data["release"]["counts"]["evidenceLinkCorrections"] == 1
        # a later correction of the same link is a new release version
        correct_link(call, "c-open", definition, {"status": "rejected"})
        again = publish(call)
        assert again["changes"]["evidenceLinks"]["changed"] == [f"c-open | {definition}"] and again["nextVersion"] == 2
        two = call("POST", "/api/publish/confirm", {**BODY, "fingerprint": again["fingerprint"]})[1]
        [latest] = helios(download(call, two["version"]))["evidence_link_corrections"]
        assert latest["patch"]["relation"] == "is_about" and latest["patch"]["status"] == "rejected" and latest["revision"] > row["revision"]


def test_evidence_link_correction_of_a_withheld_source_is_not_published(tmp_path):
    with review_database(tmp_path / "state", pg_bin()) as database, running_server(
            database, evidence_dir=tmp_path / "evidence") as call:
        definition = seed(database)
        correct_link(call, "c-hid", definition, {"relation": "is_about"})
        assert publish(call)["counts"]["evidenceLinkCorrections"] == 1
        database.sql(f"INSERT INTO v2_demo_restrictions VALUES ('admin', '{HIDDEN}');")
        narrow = publish(call)
        assert narrow["counts"]["evidenceLinkCorrections"] == 0 and "c-hid" not in str(narrow)
