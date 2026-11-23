"""Exercise source-bound definition labeling with a local model fixture."""

import json

from semantic_pipeline_v2 import evidence_labeler
from semantic_pipeline_v2.mapped_evidence import map_pages
from semantic_pipeline_v2.workspace import JADE, ROOT, load_workspace


class FixtureLabeler:
    model = "fixture"
    available = True

    def __init__(self, response):
        self.response = response
        self.calls = []

    def label(self, anchors, definitions):
        self.calls.append((anchors, definitions))
        return self.response(anchors, definitions)


def positive(anchors, definitions):
    row = next(row for row in anchors if "due date" in row["text"].lower())
    definition = next(item for item in definitions if item["title"] == "Sales_SalesOrderHeader/DueDate")
    return {"labels": [{"anchor": row["anchor"], "definitionId": definition["id"], "quote": row["text"]}]}


def prepared(text):
    workspace = load_workspace("manufacturing")
    source = next(item for item in workspace["sources"] if item["id"].endswith(":MFG-003"))
    return workspace, map_pages(workspace, source["id"], source["citation"]["sha256"],
                                [{"index": 0, "markdown": text}], kind="native_text")


def test_native_label_and_replay_remain_pinned_to_exact_anchor():
    text = "# Purchase order\nOrder due date: 2026-11-30"
    workspace, extracted = prepared(text)
    fixture = FixtureLabeler(positive)
    result = evidence_labeler.label_result(workspace, extracted, fixture)
    assert result["labeling"]["status"] == "completed"
    assert result["labeling"]["accepted"] == 1
    mapping, = result["mappings"]
    assert mapping["agentLabel"]["quote"] == "Order due date: 2026-11-30"
    assert mapping["agentLabel"]["definitionId"].endswith("/DueDate")
    assert "agent_exact_quote" in mapping["basis"]
    assert mapping["status"] == "mapped"
    assert mapping["candidateIds"] == [mapping["targetId"]] == [mapping["agentLabel"]["definitionId"]]
    observation = next(item for item in result["observations"] if item["id"] == mapping["observationId"])
    assert observation["citation"]["locator"] == "original text"
    assert observation["citation"]["startLine"] == 2

    _, replay = prepared(text)
    evidence_labeler.label_result(workspace, replay, fixture, previous=result)
    assert len(fixture.calls) == 1
    assert replay["mappings"] == result["mappings"]

    _, moved = prepared(text.replace("\n", "\n\n"))
    evidence_labeler.label_result(workspace, moved, fixture, previous=result)
    assert len(fixture.calls) == 2
    assert moved["mappings"][0]["observationId"] != mapping["observationId"]


def test_actual_jade_passage_cannot_establish_workbook_rating_or_identity():
    workspace = load_workspace("financial-services")
    source = next(item for item in workspace["sources"] if item["kind"] == "adverse-media")
    result = map_pages(workspace, source["id"], source["citation"]["sha256"], [
        {"index": 0, "markdown": (ROOT / JADE).read_text()},
    ], kind="native_text")
    fields = workspace["knowledge"]["structured"]["fields"]
    rating = next(item for item in fields if item["name"] == "Customers/RiskRating")
    country = next(item for item in fields if item["name"] == "Customers/CountryOfOperation")

    def ungrounded(anchors, definitions):
        return {"labels": [
            {"anchor": 0, "definitionId": rating["id"], "quote": anchors[0]["text"]},
            {"anchor": 1, "definitionId": "another-dataset:definition:RiskRating", "quote": anchors[1]["text"]},
            {"anchor": 2, "definitionId": country["id"], "quote": "operating in a fabricated country"},
        ]}

    fixture = FixtureLabeler(ungrounded)
    evidence_labeler.label_result(workspace, result, fixture)
    assert result["labeling"]["accepted"] == 0
    assert result["labeling"]["rejected"] == 3
    assert "opaque cross-border payment flows" in json.dumps(fixture.calls)
    assert all("agentLabel" not in item for item in result["mappings"])


def test_provider_failure_keeps_local_results_and_is_retried():
    text = "Order due date: 2026-11-30"
    workspace, result = prepared(text)
    baseline = result["mappings"].copy()

    def failed(anchors, definitions):
        raise TimeoutError("private detail")

    fixture = FixtureLabeler(failed)
    evidence_labeler.label_result(workspace, result, fixture)
    assert result["labeling"]["status"] == "degraded"
    assert result["labeling"]["reason"] == "provider_failed"
    assert result["mappings"] == baseline
    assert "private detail" not in json.dumps(result)

    _, retry = prepared(text)
    recovered = FixtureLabeler(positive)
    evidence_labeler.label_result(workspace, retry, recovered, previous=result)
    assert recovered.calls and retry["labeling"]["accepted"] == 1


def test_bounded_labeling_omits_masked_identifiers_and_keeps_all_local_observations():
    text = "Party identifier: S1234567D\n" + "\n".join(f"Line {index}: sample" for index in range(90))
    workspace, result = prepared(text)
    fixture = FixtureLabeler(lambda anchors, definitions: {"labels": []})
    evidence_labeler.label_result(workspace, result, fixture)
    assert len(fixture.calls) == evidence_labeler.MAX_BATCHES
    assert all(len(anchors) == evidence_labeler.BATCH_SIZE
               and len(definitions) <= evidence_labeler.MAX_DEFINITIONS for anchors, definitions in fixture.calls)
    assert "S1234567D" not in json.dumps(fixture.calls)
    assert result["labeling"]["status"] == "partial"
    assert result["labeling"]["considered"] < len(result["observations"])
    assert len(result["observations"]) == 91
