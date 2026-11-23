"""Mistral audio transcription: request shape, limits, strict reply, and the shared evidence path."""

from hashlib import sha256
import io
import json
import os
from pathlib import Path
from urllib.error import HTTPError

import pytest

from semantic_pipeline_v2 import ocr, transcribe, workspace, workspace_server
from semantic_pipeline_v2.originals import Original
from test_evidence_labeler import FixtureLabeler
from test_workspace import running_server

REPLY = {"model": "voxtral-test", "language": "en", "text": "Hello. ID S1234567Z.", "segments": [
    {"text": "Hello there.", "start": 0.0, "end": 1.5, "speaker_id": "speaker_1", "language": "en"},
    {"text": " My ID is S1234567Z.\n", "start": 1.5, "end": 4.0, "speaker_id": "speaker_2"},
]}


class FakeMistral:
    def __init__(self, result):
        self.result, self.requests = result, []

    def open(self, request, *, timeout):
        self.requests.append((request.full_url, dict(request.header_items()), request.data))
        if isinstance(self.result, Exception):
            raise self.result
        return io.BytesIO(json.dumps(self.result).encode())


def client(result=REPLY, key="fixture-token"):
    transport = FakeMistral(result)
    return transcribe.MistralTranscribe(key, opener=transport), transport


def test_request_shape_and_normalized_segments():
    mistral, transport = client()
    result = mistral.process("call.m4a", "audio/mp4", b"AUDIO", source_id="s")
    (url, headers, data), = transport.requests
    assert url == "https://api.mistral.ai/v1/audio/transcriptions"
    assert headers["Authorization"] == "Bearer fixture-token"
    assert headers["Content-type"].startswith("multipart/form-data; boundary=")
    for part in (b'name="model"', b"voxtral-mini-latest", b'name="diarize"', b'name="timestamp_granularities"',
                 b"segment", b'filename="call.m4a"', b"AUDIO"):
        assert part in data
    assert b'name="language"' not in data     # Mistral rejects language together with timestamps
    page, = result["pages"]
    assert page["markdown"] == "Hello there.\nMy ID is S1234567Z."
    assert page["segments"][1] == {"startSeconds": 1.5, "endSeconds": 4.0, "speaker": "speaker_2", "language": "en"}
    assert result["source"] == {"filename": "call.m4a", "mimeType": "audio/mp4",
                                "sha256": sha256(b"AUDIO").hexdigest(), "id": "s"}
    assert result["provider"] == "mistral" and result["model"] == "voxtral-test"


def test_no_key_means_unavailable_and_no_request():
    mistral, transport = client(key="")
    assert not mistral.available
    with pytest.raises(ocr.OcrError) as error:
        mistral.process("a.mp3", "audio/mpeg", b"x")
    assert error.value.status == 503 and not transport.requests


def test_size_and_type_refusals_before_any_request():
    mistral, transport = client()
    with pytest.raises(ocr.OcrError) as error:
        mistral.process("a.mp3", "audio/mpeg", b"x" * (transcribe.MAX_MEDIA + 1))
    assert error.value.status == 413 and "25 MiB" in str(error.value)
    with pytest.raises(ocr.OcrError) as error:
        mistral.process("a.pdf", "application/pdf", b"x")
    assert error.value.status == 415 and not transport.requests


@pytest.mark.parametrize("reply", [
    {"model": "m", "segments": []}, {"model": "m", "segments": [{"text": "x", "start": 2, "end": 1}]},
    {"model": "m", "segments": [{"text": "x", "start": -1, "end": 1}]},
    {"model": "m", "segments": [{"text": 5, "start": 0, "end": 1}]},
    {"model": "m", "segments": [{"text": "x", "start": 0, "end": float("inf")}]},
    {"model": "m", "segments": [{"text": "  ", "start": 0, "end": 1}]}, ["not", "an", "object"],
])
def test_bad_reply_is_rejected(reply):
    mistral = transcribe.MistralTranscribe("fixture-token", opener=FakeMistral(None))
    mistral._opener.result = reply
    with pytest.raises(ocr.OcrError) as error:
        mistral.process("a.mp3", "audio/mpeg", b"x")
    assert error.value.status == 502


def test_provider_error_is_sanitized_and_never_a_transcript():
    secret = "fixture-token"
    mistral, _ = client(HTTPError("u", 401, f"bad {secret}", {}, io.BytesIO(secret.encode())))
    with pytest.raises(ocr.OcrError) as error:
        mistral.process("a.mp3", "audio/mpeg", b"x")
    assert error.value.status == 502 and secret not in str(error.value)
    mistral, _ = client(OSError(f"down {secret}"))
    with pytest.raises(ocr.OcrError) as error:
        mistral.process("a.mp3", "audio/mpeg", b"x")
    assert error.value.status == 502 and secret not in str(error.value)


def test_video_without_ffmpeg_is_refused_clearly(monkeypatch):
    mistral, transport = client()
    monkeypatch.setattr(transcribe.shutil, "which", lambda name: None)
    with pytest.raises(ocr.OcrError) as error:
        mistral.process("a.mp4", "video/mp4", b"x")
    assert error.value.status == 415 and "ffmpeg" in str(error.value) and not transport.requests


def test_video_audio_track_is_extracted_and_sent(monkeypatch, tmp_path):
    mistral, transport = client()
    monkeypatch.setattr(transcribe.shutil, "which", lambda name: "/bin/ffmpeg")

    def fake_run(command, **kwargs):
        Path(command[-1]).write_bytes(b"WAVBYTES")
        return type("Done", (), {"returncode": 0})()

    monkeypatch.setattr(transcribe.subprocess, "run", fake_run)
    result = mistral.process("a.mp4", "video/mp4", b"VIDEO", source_id="s")
    assert b"WAVBYTES" in transport.requests[0][2] and b"VIDEO" not in transport.requests[0][2]
    assert result["source"]["sha256"] == sha256(b"VIDEO").hexdigest() and result["source"]["mimeType"] == "video/mp4"


def test_map_pages_keeps_time_ranges_and_chunks_reach_links(tmp_path, monkeypatch):
    pg_bin = os.environ.get("V2_PG_BIN")
    if not pg_bin:
        pytest.skip("set V2_PG_BIN for the link store")
    dataset, source_id = "financial-services", "financial-services:source:cleaned_audio"
    audio = b"fake m4a bytes"
    (tmp_path / "call.m4a").write_bytes(audio)
    original = Original(tmp_path, Path("call.m4a"), sha256(audio).hexdigest(), len(audio))
    monkeypatch.setattr(workspace_server, "load_originals", lambda: {source_id: original})
    principal = {"id": "admin", "role": "admin", "datasets": [dataset], "withheld": []}
    monkeypatch.setattr(workspace_server.WorkspaceRequest, "principal", lambda _: principal)
    monkeypatch.setattr(workspace_server, "resolve_session", lambda *_: principal)
    mistral, transport = client()
    labeler = FixtureLabeler(lambda anchors, definitions: {"labels": []})
    body = {"dataset": dataset, "sourceId": source_id, "options": {}}
    with workspace.review_database(tmp_path / "state", Path(pg_bin)) as database, running_server(
            database, ocr=ocr.MistralOcr(""), transcriber=mistral, evidence_dir=tmp_path / "evidence",
            evidence_labeler=labeler, chunk_embedder=None, mapping_embedder=None, interpreter=None) as call:
        assert call("GET", "/api/ocr")[1]["transcription"] == {"available": True, "model": "voxtral-mini-latest"}
        principal["role"] = "reader"
        assert call("POST", "/api/ocr", body)[0] == 403 and not transport.requests
        principal["role"] = "admin"
        assert call("POST", "/api/ocr", {**body, "options": {"language": "en"}})[0] == 400
        status, result = call("POST", "/api/ocr", body)
        assert status == 200, result
        assert "S1234567Z" not in json.dumps(result)
        observations = result["evidence"]["observations"]
        cites = [o["citation"] for o in observations]
        assert [(c["kind"], c["startSeconds"], c["endSeconds"], c["speaker"]) for c in cites] == [
            ("transcript", 0.0, 1.5, "speaker_1"), ("transcript", 1.5, 4.0, "speaker_2")]
        assert result["chunkIndex"]["rows"] >= 1
        assert result["evidenceLinks"]["status"] == "saved" and result["evidenceLinks"]["rows"] >= 1


def test_http_failure_is_an_error_not_a_sample(tmp_path, monkeypatch):
    dataset, source_id = "financial-services", "financial-services:source:cleaned_audio"
    (tmp_path / "call.m4a").write_bytes(b"x")
    original = Original(tmp_path, Path("call.m4a"), sha256(b"x").hexdigest(), 1)
    monkeypatch.setattr(workspace_server, "load_originals", lambda: {source_id: original})
    principal = {"id": "admin", "role": "admin", "datasets": [dataset], "withheld": []}
    monkeypatch.setattr(workspace_server.WorkspaceRequest, "principal", lambda _: principal)
    monkeypatch.setattr(workspace_server, "resolve_session", lambda *_: principal)
    mistral, _ = client(OSError("down"))
    with running_server(transcriber=mistral, evidence_dir=tmp_path / "evidence") as call:
        status, payload = call("POST", "/api/ocr", {"dataset": dataset, "sourceId": source_id, "options": {}})
        assert status == 502 and "pages" not in payload and "segments" not in json.dumps(payload)
    unavailable = transcribe.MistralTranscribe("")
    with running_server(transcriber=unavailable, evidence_dir=tmp_path / "evidence2") as call:
        assert call("POST", "/api/ocr", {"dataset": dataset, "sourceId": source_id, "options": {}})[0] == 503
