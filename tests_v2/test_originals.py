"""Check original HTTP bytes and isolation with no provider."""

from contextlib import contextmanager
from hashlib import sha256
from http.client import HTTPConnection
from pathlib import Path
import json
import os
from threading import Thread
from urllib.parse import urlencode

import pytest

from semantic_pipeline_v2.originals import Original, byte_range, load_originals, original_info
from semantic_pipeline_v2.workspace import review_database
from semantic_pipeline_v2.workspace_server import create_server


@pytest.fixture(scope="module")
def database(tmp_path_factory):
    pg_bin = os.environ.get("V2_PG_BIN")
    if not pg_bin:
        pytest.skip("set V2_PG_BIN for authenticated loopback originals")
    with review_database(tmp_path_factory.mktemp("original-tests") / "state", Path(pg_bin)) as instance:
        yield instance


@contextmanager
def original_server(database):
    with create_server(0, database) as server:
        worker = Thread(target=server.serve_forever)
        worker.start()
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            connection.request("GET", "/")
            response = connection.getresponse()
            cookie = "; ".join(value.split(";", 1)[0] for name, value in response.getheaders()
                               if name == "Set-Cookie")
            response.read()
        finally:
            connection.close()

        def request(endpoint, dataset, source_id, headers=None):
            connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            query = urlencode({"dataset": dataset, "sourceId": source_id})
            try:
                connection.request("GET", f"/api/{endpoint}?{query}",
                                   headers={"Cookie": cookie, **(headers or {})})
                response = connection.getresponse()
                body = response.read()
                if response.getheader("Content-Type", "").startswith("application/json"):
                    body = json.loads(body)
                return response.status, dict(response.getheaders()), body
            finally:
                connection.close()

        try:
            yield server, request
        finally:
            server.shutdown()
            worker.join()


def test_original_prepared_markdown_and_scope(database):
    original_id = "manufacturing:source:MFG-003"
    with original_server(database) as (server, request):
        original = server.originals[original_id]
        expected = (original.root / original.relative).read_bytes()
        status, headers, detail = request("original-info", "manufacturing", original_id)
        assert status == 200 and detail["canPreview"] is True and detail["status"] == "available"
        assert detail["filename"] == "MFG-003.md" and detail["bytes"] == len(expected)
        assert detail["originalUrl"].startswith("/api/original?")
        assert str(original.root) not in json.dumps(detail)
        status, headers, data = request("original", "manufacturing", original_id)
        assert status == 200 and data == expected
        assert headers["Content-Type"] == "text/plain; charset=utf-8"
        assert headers["Cross-Origin-Resource-Policy"] == "same-origin"
        assert request("original", "financial-services", original_id)[0] == 404
        assert request("original", "manufacturing", "../../../etc/passwd")[0] == 404
        assert request("original", "manufacturing", original_id, {"Sec-Fetch-Site": "cross-site"})[0] == 403
        assert request("original", "manufacturing", original_id, {"Origin": "http://example.invalid"})[0] == 403
        model = "manufacturing:source:manufacturing-model"
        assert request("original-info", "manufacturing", model)[2]["status"] == "unsupported"


def test_workbook_original_is_not_registered():
    dataset = "financial-services"
    workbook_id = f"{dataset}:source:workbook"
    assert workbook_id not in load_originals()
    assert original_info(dataset, workbook_id, None, "unsupported") == {
        "status": "unsupported", "filename": None, "mimeType": None,
        "bytes": None, "canPreview": False, "originalUrl": None,
    }


def test_original_audio_ranges_and_office_download(tmp_path, database):
    dataset = "financial-services"
    audio_id = f"{dataset}:source:cleaned_audio"
    office_id = f"{dataset}:source:finance-model"
    audio = b"x" * (300 * 1024) + b"0123456789"
    with original_server(database) as (server, request):
        workbook_id = f"{dataset}:source:workbook"
        assert workbook_id not in server.originals
        assert request("original", dataset, workbook_id)[0] == 404
        assert request("original-info", dataset, workbook_id)[2] == {
            "status": "unsupported", "filename": None, "mimeType": None,
            "bytes": None, "canPreview": False, "originalUrl": None,
        }
        for ident, filename, data in [(audio_id, "abc.m4a", audio), (office_id, "input.xlsx", b"xlsx")]:
            (tmp_path / filename).write_bytes(data)
            server.originals[ident] = Original(tmp_path, Path(filename), sha256(data).hexdigest(), len(data))
        status, headers, segment = request("original", dataset, audio_id, {"Range": "bytes=-10"})
        assert status == 206 and segment == b"0123456789"
        assert headers["Content-Range"] == f"bytes {len(audio)-10}-{len(audio)-1}/{len(audio)}"
        assert headers["Accept-Ranges"] == "bytes" and headers["Content-Type"] == "audio/mp4"
        status, headers, body = request("original", dataset, audio_id, {"Range": f"bytes={len(audio)}-"})
        assert status == 416 and headers["Content-Range"] == f"bytes */{len(audio)}" and not body
        status, headers, body = request("original", dataset, office_id)
        assert status == 200 and body == b"xlsx"
        assert headers["Content-Disposition"].startswith("attachment;")
        assert request("original-info", dataset, office_id)[2]["canPreview"] is False


def test_missing_changed_and_symlink_never_stream(tmp_path, database):
    dataset, ident = "manufacturing", "manufacturing:source:MFG-003"
    path = tmp_path / "input.md"
    expected = b"expected"
    with original_server(database) as (server, request):
        server.originals[ident] = Original(tmp_path, Path("input.md"),
                                          sha256(expected).hexdigest(), len(expected))
        assert request("original-info", dataset, ident)[2]["status"] == "missing"
        path.write_bytes(b"modified")
        assert request("original-info", dataset, ident)[2]["status"] == "changed"
        assert request("original", dataset, ident)[0] == 409
        path.unlink()
        real = tmp_path / "private-target.md"
        real.write_bytes(expected)
        path.symlink_to(real)
        assert request("original", dataset, ident)[0] == 404


@pytest.mark.parametrize(("header", "expected"), [
    ("bytes=0-2", (0, 2)), ("bytes=3-", (3, 9)), ("bytes=5-30", (5, 9)), ("bytes=-100", (0, 9)),
])
def test_valid_byte_ranges(header, expected):
    assert byte_range(header, 10) == expected


@pytest.mark.parametrize("header", ["bytes=10-", "bytes=-0", "bytes=1-2,3-4", "bytes=-", "bytes=8-3", "items=0-2"])
def test_invalid_byte_ranges(header):
    with pytest.raises(ValueError):
        byte_range(header, 10)
