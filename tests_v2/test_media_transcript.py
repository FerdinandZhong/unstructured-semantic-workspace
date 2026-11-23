"""The exact synthetic archive cannot be used for other media or unauthorized readers."""

from hashlib import sha256
import json

import pytest

from semantic_pipeline_v2.access import project_workspace
from semantic_pipeline_v2.media_transcript import AUDIO_PATH, SOURCE_ID, archived_transcript
from semantic_pipeline_v2.workspace import FINANCE, ROOT, load_workspace


def test_verified_audio_and_transcript_pins_reject_substitution(tmp_path):
    fixture_path = tmp_path / "archive.json"
    known = b"Synthetic fixture bytes"
    digest = sha256(known).hexdigest()
    audio = tmp_path / AUDIO_PATH
    audio.parent.mkdir(parents=True)
    audio.write_bytes(known)
    asset = {"id": "jade_call", "root": "v1", "kind": "synthetic_audio", "scope": "jade_demo",
             "path": AUDIO_PATH, "sha256": digest, "bytes": len(known)}
    quote = "Verbatim conversation."
    fixture = {
        "version": 1, "sourceId": SOURCE_ID, "synthetic": True, "audioSha256": digest,
        "archive": {"textSha256": sha256(quote.encode()).hexdigest()}, "text": quote,
        "spans": [{"start": 0, "end": len(quote), "startSeconds": 1.1, "endSeconds": 2.9,
                   "speaker": "speaker_1", "language": "en"}],
    }
    fixture_path.write_text(json.dumps(fixture))
    row, = archived_transcript(fixture_path, tmp_path, asset)
    assert row["title"] == row["quote"] == quote
    assert row["citation"]["startSeconds"] == 1.1 and row["citation"]["speaker"] == "speaker_1"
    assert archived_transcript(fixture_path, tmp_path, {**asset, "id": "cleaned_audio"}) == []
    fixture_path.write_text(json.dumps({**fixture, "text": "Edited conversation."}))
    assert archived_transcript(fixture_path, tmp_path, asset) == []
    fixture_path.write_text(json.dumps(fixture))
    audio.write_bytes(b"Changed")
    assert archived_transcript(fixture_path, tmp_path, asset) == []


def test_actual_jade_archive_scope_and_exact_segment_citations():
    fixture = json.loads((ROOT / FINANCE / "jade_transcript.json").read_text())
    workspace = load_workspace("financial-services")
    source = next(item for item in workspace["sources"] if item["id"] == SOURCE_ID)
    if source["transcriptStatus"] != "available":
        pytest.skip("the exact local V1 synthetic audio original is absent")
    observations = [item for item in workspace["knowledge"]["observations"] if item["sourceId"] == SOURCE_ID]
    assert len(observations) == source["transcriptSegmentCount"] == 22
    for item, span in zip(observations, fixture["spans"], strict=True):
        citation = item["citation"]
        assert citation["sha256"] == fixture["audioSha256"]
        assert citation["kind"] == "transcript"
        assert item["quote"] == fixture["text"][span["start"]:span["end"]]
        assert item["title"] == item["quote"].strip()
        for field in ("startSeconds", "endSeconds", "speaker", "language"):
            assert citation[field] == span[field]
    assert {item["citation"]["language"] for item in observations} == {"en", "zh"}
    payload = {"datasets": [workspace], "reviewAvailable": False}
    reader = {"datasets": ["financial-services"], "withheld": [], "role": "reader"}
    allowed = project_workspace(payload, reader)["datasets"][0]
    assert len([row for row in allowed["knowledge"]["observations"] if row["sourceId"] == SOURCE_ID]) == 22
    restricted = project_workspace(payload, {**reader, "withheld": [SOURCE_ID]})["datasets"][0]
    assert all(row["sourceId"] != SOURCE_ID for row in restricted["knowledge"]["observations"])
    restricted_source = next(item for item in restricted["sources"] if item["id"] == SOURCE_ID)
    assert restricted_source["examples"] == [] and "transcriptSegmentCount" not in restricted_source
    assert all(item["sourceId"] != SOURCE_ID for item in load_workspace(
        "financial-services", local_projections=False,
    )["knowledge"]["observations"])
