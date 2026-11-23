"""Test local OCR boundary and official request shape without provider traffic."""

from base64 import b64encode
from hashlib import sha256
import io
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode

import pytest

from semantic_pipeline_v2 import ocr, workspace, workspace_server
from semantic_pipeline_v2.originals import Original
from test_evidence_labeler import FixtureLabeler
from test_workspace import running_server


class FakeMistral:
    def __init__(self, result):
        self.result = result
        self.requests = []

    def open(self, request, *, timeout):
        self.requests.append(json.loads(request.data))
        assert request.full_url == ocr.OCR_URL and timeout == 60
        if isinstance(self.result, Exception):
            raise self.result
        return io.BytesIO(json.dumps(self.result).encode())


def test_mistral_controls_and_normalized_response():
    transport = FakeMistral({
        "model": "mistral-ocr-test", "pages": [{"index": 3, "markdown": "Example", "footer": "F"}],
        "document_annotation": json.dumps({"findings": [{"label": "Ticker", "value": "Test",
                                                         "definitionId": "local", "quote": "Example",
                                                         "pageIndex": 3}]}),
    })
    result = ocr.MistralOcr("fixture-token", opener=transport).process(
        "test.pdf", "application/pdf", b"%PDF-1.7\nSynthetic", {
            "pages": "3", "tableFormat": "markdown", "includeImageBase64": False,
            "extractHeader": True, "extractFooter": True,
            "annotationPrompt": "Identify the ticker.", "definitionGuided": True,
        }, definitions=[{"id": "local", "definition": "Issuer ticker"}],
    )
    body, = transport.requests
    assert body["document"]["document_url"].startswith("data:application/pdf;base64,")
    assert body["pages"] == [3] and body["table_format"] == "markdown"
    assert body["extract_header"] and body["extract_footer"] and not body["include_image_base64"]
    assert body["document_annotation_format"]["type"] == "json_schema"
    assert "Issuer ticker" in body["document_annotation_prompt"]
    assert result["model"] == "mistral-ocr-test" and result["pages"][0]["index"] == 3
    assert result["annotation"]["findings"][0]["label"] == "Ticker"


@pytest.mark.parametrize(("mime", "payload", "options"), [
    ("image/jpeg", b"wrong type", {}),
    ("application/pdf", b"%PDF-1.7\n/XFA 10 0 R", {}),
    ("application/pdf", b"%PDF-1.7", {"pages": "3-2"}),
    ("application/pdf", b"%PDF-1.7", {"pages": "0-100"}),
    ("application/pdf", b"%PDF-1.7", {"extractFooter": "yes"}),
])
def test_invalid_ocr_requests_do_not_contact_provider(mime, payload, options):
    transport = FakeMistral({})
    with pytest.raises(ocr.OcrError):
        ocr.MistralOcr("fixture-token", opener=transport).process("test.pdf", mime, payload, options)
    assert not transport.requests


def test_unavailable_invalid_output_and_sanitized_provider_error(monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    with pytest.raises(ocr.OcrError, match="unavailable") as missing:
        ocr.configuration().process("local.pdf", "application/pdf", b"%PDF-1.6", {})
    assert missing.value.status == 503
    raw = HTTPError(ocr.OCR_URL, 401, "private provider detail", None, None)
    with pytest.raises(ocr.OcrError, match="credential") as failed:
        ocr.MistralOcr("fixture-token", opener=FakeMistral(raw)).process(
            "local.pdf", "application/pdf", b"%PDF-1.6", {}
        )
    assert "private provider detail" not in str(failed.value) and failed.value.status == 502
    with pytest.raises(ocr.OcrError, match="invalid OCR page"):
        ocr.validated_pages({"model": "ocr", "pages": [{"index": 0, "markdown": "text"}]}, [1])


def test_bad_optional_annotation_preserves_ocr_pages():
    transport = FakeMistral({"model": "test-ocr", "pages": [{"index": 0, "markdown": "returned text"}],
                             "document_annotation": "invalid json"})
    result = ocr.MistralOcr("fixture-token", opener=transport).process(
        "local.pdf", "application/pdf", b"%PDF-1.6", {"annotationPrompt": "Find dates."})
    assert result["pages"] == [{"index": 0, "markdown": "returned text"}]
    assert result["annotationStatus"] == "degraded"


def test_http_native_jade_labels_only_on_admin_run(tmp_path, monkeypatch):
    dataset = "financial-services"
    source = f"{dataset}:source:jade-adverse-media"
    principal = {"id": "admin", "role": "admin", "datasets": [dataset], "withheld": []}
    monkeypatch.setattr(workspace_server.WorkspaceRequest, "principal", lambda _: principal)
    monkeypatch.setattr(workspace_server, "resolve_session", lambda *_: principal)
    labeler = FixtureLabeler(lambda anchors, definitions: {"labels": []})
    body = {"dataset": dataset, "sourceId": source, "mode": "text"}
    query = urlencode({"dataset": dataset, "sourceId": source})
    with running_server(evidence_dir=tmp_path / "evidence", evidence_labeler=labeler) as call:
        assert call("GET", "/api/mapped-evidence?" + query) == (200, {"evidence": None})
        assert labeler.calls == []
        principal["role"] = "reader"
        assert call("POST", "/api/ocr", body)[0] == 403
        assert labeler.calls == []
        principal["role"] = "admin"
        status, article = call("POST", "/api/ocr", body)
        assert status == 200 and article["labeling"]["status"] == "completed"
        assert len(labeler.calls) == 1
        assert "opaque cross-border payment flows" in json.dumps(labeler.calls[-1])
        assert call("POST", "/api/ocr", body)[1]["labeling"] == article["labeling"]
        assert call("GET", "/api/mapped-evidence?" + query)[1]["evidence"]["labeling"] == article["labeling"]
        assert len(labeler.calls) == 1


def test_session_replacement_during_ocr_stops_labeling(tmp_path, monkeypatch):
    dataset = "financial-services"
    principal = {"id": "admin", "role": "admin", "datasets": [dataset], "withheld": []}
    active = principal

    class ReplacingTransport(FakeMistral):
        def open(self, *args, **kwargs):
            nonlocal active
            active = None
            return super().open(*args, **kwargs)

    monkeypatch.setattr(workspace_server.WorkspaceRequest, "principal", lambda _: principal)
    monkeypatch.setattr(workspace_server, "resolve_session", lambda *_: active)
    client = ocr.MistralOcr("fixture-token", opener=ReplacingTransport({"model": "test-ocr", "pages": [
        {"index": 0, "markdown": "Customer risk rating 4"},
    ]}))
    labeler = FixtureLabeler(lambda anchors, definitions: {"labels": []})
    body = {"dataset": dataset, "file": {
        "name": "local.pdf", "mimeType": "application/pdf", "base64": b64encode(b"%PDF-1.7").decode(),
    }}
    with running_server(ocr=client, evidence_dir=tmp_path / "evidence", evidence_labeler=labeler) as call:
        status, response = call("POST", "/api/ocr", body)
        assert status == 401 and response["accounts"]
        assert labeler.calls == []


def test_http_checks_admin_source_original_and_retrieves_mapped_evidence(tmp_path, monkeypatch):
    pg_bin = os.environ.get("V2_PG_BIN")
    if not pg_bin:
        pytest.skip("set V2_PG_BIN for native OCR access integration")
    dataset = "financial-services"
    source_id = "financial-services:source:cleaned_pdf_3"
    filename = "original.pdf"
    payload = b"%PDF-1.7\nSynthetic"
    path = tmp_path / filename
    path.write_bytes(payload)
    original = Original(tmp_path, Path(filename), sha256(payload).hexdigest(), len(payload))
    manufacturing_id = "manufacturing:source:MFG-003"
    xfa_id = f"{dataset}:source:cleaned_pdf_1"
    manufacturing = workspace_server.load_originals()[manufacturing_id]
    monkeypatch.setattr(workspace_server, "load_originals", lambda: {
        source_id: original, manufacturing_id: manufacturing, xfa_id: original,
    })
    monkeypatch.setattr("semantic_pipeline_v2.xfa.form_pages",
                        lambda data: [{"index": 0, "markdown": 'xfa:data/entity = "Example"'}])
    transport = FakeMistral({"model": "test-ocr", "pages": [{
        "index": 0, "markdown": "Customer risk rating 4. ID S1234567Z",
        "tables": [{"id": "tbl-balance", "content": "| Account balance | SGD 50.00 |"}],
    }]})
    client = ocr.MistralOcr("fixture-token", opener=transport)
    labeler = FixtureLabeler(lambda anchors, definitions: {"labels": []})
    body = {"dataset": dataset, "sourceId": source_id, "options": {}}
    query = urlencode({"dataset": dataset, "sourceId": source_id})
    with workspace.review_database(tmp_path / "state", Path(pg_bin)) as database, running_server(
        database, ocr=client, evidence_dir=tmp_path / "evidence", evidence_labeler=labeler,
    ) as call:
        status, connector = call("GET", "/api/ocr")
        assert status == 200 and connector["available"] is True and connector["model"] == "mistral-ocr-latest"
        assert set(connector["transcription"]) == {"available", "model"}
        assert call("POST", "/api/ocr", body, {"Origin": "http://invalid.example"})[0] == 403
        assert call("POST", "/api/ocr", {**body, "dataset": "manufacturing"})[0] == 404
        assert call("POST", "/api/ocr", {**body, "sourceId": xfa_id})[0] == 415
        assert not transport.requests
        status, fields = call("POST", "/api/ocr", {**body, "sourceId": xfa_id, "mode": "xfa-fields"})
        assert status == 200 and fields["provider"] == "local" and fields["evidenceStatus"] == "saved"
        field_citation = fields["evidence"]["observations"][0]["citation"]
        assert field_citation["locator"] == "embedded form data" and "page" not in field_citation
        status, native = call("POST", "/api/ocr", {"dataset": "manufacturing", "sourceId": manufacturing_id,
                                                   "mode": "text", "options": {}})
        assert status == 200 and native["provider"] == "local" and native["evidenceStatus"] == "saved"
        source_citation = native["evidence"]["observations"][0]["citation"]
        assert source_citation["locator"] == "original text" and "page" not in source_citation
        assert not transport.requests
        status, result = call("POST", "/api/ocr", body)
        assert status == 200 and len(transport.requests) == 1
        assert result["source"]["sha256"] == original.digest and result["evidenceStatus"] == "saved"
        assert result["fieldStatus"] == "available"
        assert "S1234567Z" not in json.dumps(result)
        assert any(entry["label"] == "Account balance" and entry["value"] == "SGD 50.00"
                   for entry in result["fields"])
        assert result["documentPages"][0]["order"] == "source_then_detached"
        assert result["tables"] == result["evidence"]["tables"]
        assert result["tables"][0]["rows"] == [["Account balance", "SGD 50.00"]]
        assert call("GET", "/api/mapped-evidence?" + query) == (200, {"evidence": result["evidence"]})
        prepared = call("GET", "/api/workspace")[1]["datasets"][0]
        assert any(obs["sourceId"] == source_id for obs in prepared["knowledge"]["observations"])
        assert "S1234567Z" not in json.dumps(prepared)
        path.write_bytes(payload[:-1] + b"x")
        assert call("POST", "/api/ocr", body)[0] == 409
        local = {"dataset": dataset, "file": {"name": "local.pdf", "mimeType": "application/pdf",
                                             "base64": b64encode(payload).decode()}}
        status, result = call("POST", "/api/ocr", local)
        assert status == 200 and result["evidenceStatus"] == "ephemeral"
        local_id = result["source"]["id"]
        assert local_id == f"{dataset}:source:local-{original.digest}"
        assert result["evidence"]["sourceId"] == local_id and result["evidence"]["observations"]
        assert call("GET", "/api/mapped-evidence?" + urlencode({"dataset": dataset, "sourceId": local_id}))[0] == 404
        assert len(transport.requests) == 2
        status, _, headers = call("POST", "/api/session", {"userId": "finance_reader"}, include_headers=True)
        assert status == 200
        cookie = "; ".join(value.split(";", 1)[0] for name, value in headers if name == "Set-Cookie")
        assert call("POST", "/api/ocr", body, {"Cookie": cookie})[0] == 403
        assert len(transport.requests) == 2
