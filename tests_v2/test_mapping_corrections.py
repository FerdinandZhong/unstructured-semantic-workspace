"""Exercise local correction edits with scoped sessions and dedicated PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import os
from pathlib import Path
from urllib.parse import urlencode

import pytest

from semantic_pipeline_v2.mapping_corrections import CorrectionContext, overlay_mappings
from semantic_pipeline_v2.mapped_evidence import MappedEvidence
from semantic_pipeline_v2.workspace import load_workspace, review_database
from test_workspace import running_server


DATASET = "manufacturing"
SOURCE = f"{DATASET}:source:MFG-003"
PATH = "/api/mapping-correction"


def mapping(snapshot, observation_id):
    return next(item for item in snapshot if item.get("observationId") == observation_id)


def test_source_and_model_revisions_bind_exact_observation():
    workspace = load_workspace(DATASET)
    principal = {"datasets": [DATASET], "withheld": []}
    knowledge = workspace["knowledge"]
    selected = knowledge["mappings"][0]
    observation = next(item for item in knowledge["observations"] if item["id"] == selected["observationId"])
    context = CorrectionContext(workspace, principal, {})
    anchor = context.anchor(selected, observation)
    assert anchor
    assert anchor != context.anchor(selected, {**observation, "quote": "Reprocessed observation"})
    assert context.anchor(selected, dict(observation, sourceId=SOURCE + "-unrelated")) is None
    assert context.anchor(selected, dict(observation, citation={"sha256": "1" * 64})) is None

    modified = deepcopy(workspace)
    model = next(item for item in modified["sources"] if item["id"] == knowledge["structured"]["modelSource"])
    model["citation"]["sha256"] = "2" * 64
    changed = CorrectionContext(modified, principal, {})
    assert anchor != changed.anchor(selected, observation)
    withheld = CorrectionContext(workspace, {**principal, "withheld": [model["id"]]}, {})
    assert withheld.anchor(selected, observation) is None


def test_correction_keeps_original_ambiguity_for_editor():
    workspace = load_workspace(DATASET)
    principal = {"datasets": [DATASET], "withheld": []}
    knowledge = workspace["knowledge"]
    base = knowledge["mappings"][0]
    observation = next(item for item in knowledge["observations"] if item["id"] == base["observationId"])
    candidates = [field["id"] for field in knowledge["structured"]["fields"][:2]]
    ambiguous = {**base, "status": "ambiguous", "targetId": None, "candidateIds": candidates}
    context = CorrectionContext(workspace, principal, {})
    anchor = context.anchor(ambiguous, observation)
    [projected] = overlay_mappings(context, [ambiguous], [observation], {
        anchor: {"action": "replace", "targetId": candidates[0], "correctionRevision": "1" * 64},
    }, editable=True)
    assert projected["status"] == "corrected"
    assert projected["originalStatus"] == "ambiguous"
    assert projected["originalTargetId"] is None
    assert projected["originalCandidateIds"] == candidates


def test_http_correction_overlay_replay_and_restart(tmp_path):
    pg_bin = os.environ.get("V2_PG_BIN")
    if not pg_bin:
        pytest.skip("set V2_PG_BIN for native mapping correction integration")
    state = tmp_path / "workspace-state"
    workspace = load_workspace(DATASET)
    original = next(item for item in workspace["sources"] if item["id"] == SOURCE)
    source_sha = original["citation"]["sha256"]
    store = MappedEvidence(tmp_path / "extraction-state")
    extracted = store.record(workspace, SOURCE, source_sha, [
        {"index": 0, "markdown": "Order quantity: 12\nWarranty limit: 3 years"},
    ])
    baseline = extracted["mappings"][0]
    observation_id = baseline["observationId"]
    admin = {"datasets": [DATASET], "withheld": []}
    evidence = "/api/mapped-evidence?" + urlencode({"dataset": DATASET, "sourceId": SOURCE})

    def projected(call, *, headers=None, source=observation_id):
        status, response = call("GET", "/api/workspace", headers=headers)
        assert status == 200
        catalog = next(dataset for dataset in response["datasets"] if dataset["id"] == DATASET)
        return mapping(catalog["knowledge"]["mappings"], source), catalog["knowledge"]["structured"]

    with review_database(state, Path(pg_bin)) as database, running_server(database, evidence=store) as call:
        current, model = projected(call)
        target = next(item["id"] for item in model["fields"] if item["id"] != baseline["targetId"])
        request = {"dataset": DATASET, "sourceId": SOURCE, "observationId": observation_id,
                   "action": "replace", "targetId": target, "correctionRevision": current["correctionRevision"],
                   "requestId": "replace-1"}
        assert call("POST", PATH, {**request, "targetId": "financial-services:definition:foreign"})[0] == 400
        assert call("POST", PATH, {**request, "observationId": "fabricated-observation"})[0] == 409
        assert call("POST", PATH, {**request, "sourceId": "financial-services:source:jade-adverse-media"})[0] == 403
        assert call("POST", PATH, request, {"Origin": "https://foreign.example"})[0] == 403
        status, response = call("POST", PATH, request)
        assert status == 200
        replaced, _ = projected(call)
        revision = response["correctionRevision"]
        assert replaced["correctionRevision"] == revision != current["correctionRevision"]
        assert replaced["candidateIds"] == [target]
        assert replaced["targetId"] == target
        assert replaced["originalTargetId"] == baseline["targetId"]
        assert replaced["correctionAction"] == "replace"
        assert mapping(call("GET", evidence)[1]["evidence"]["mappings"], observation_id) == replaced
        assert call("POST", PATH, request) == (200, response)
        assert call("POST", PATH, {**request, "requestId": "stale"})[0] == 409

        remove = {**request, "action": "remove", "targetId": None, "correctionRevision": revision}
        with ThreadPoolExecutor(2) as workers:
            attempts = list(workers.map(
                lambda key: call("POST", PATH, {**remove, "requestId": key}), ["remove-1", "remove-2"]
            ))
        assert sorted(result[0] for result in attempts) == [200, 409]
        removed, _ = projected(call)
        assert removed["targetId"] is None and removed["candidateIds"] == []
        assert removed["correctionAction"] == "remove"
        assert call("POST", PATH, request) == (200, response)
        assert projected(call)[0] == removed

        restore = {**remove, "action": "restore", "requestId": "restore-1",
                   "correctionRevision": removed["correctionRevision"]}
        assert call("POST", PATH, restore)[0] == 200
        restored, _ = projected(call)
        assert restored["correctionAction"] == "restore"
        assert restored["targetId"] == baseline["targetId"]
        assert restored["candidateIds"] == baseline["candidateIds"]
        assert mapping(store.read(workspace, admin, SOURCE)["mappings"], observation_id) == baseline

        _, _, headers = call("POST", "/api/session", {"userId": "manufacturing_restricted"}, include_headers=True)
        cookie = "; ".join(value.split(";", 1)[0] for name, value in headers if name == "Set-Cookie")
        restricted = {"Cookie": cookie}
        assert call("POST", PATH, request, restricted)[0] == 403
        assert call("GET", evidence, headers=restricted)[0] == 403

    with review_database(state, Path(pg_bin)) as database, running_server(database, evidence=store) as call:
        assert projected(call)[0] == restored
        assert database.sql("SELECT count(*) FROM v2_mapping_corrections;") == "3"
