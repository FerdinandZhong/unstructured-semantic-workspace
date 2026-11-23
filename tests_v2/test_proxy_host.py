"""Behind the Cloudera AI application proxy the browser addresses the app by its public host, not by loopback.

`V2_ALLOWED_HOSTS` names those hosts. Every other host is still refused, and the same-origin rules still hold."""
import pytest

from semantic_pipeline_v2 import workspace_server as ws


class _Request:
    local_request = ws.WorkspaceRequest.local_request

    def __init__(self, headers, port=8100):
        self.headers, self.sent = headers, None
        self.server = type("S", (), {"server_port": port})()

    def send_json(self, status, body):
        self.sent = status


APP = "workspace.ml-1234.example.cloudera.site"


def test_loopback_is_accepted_and_an_unknown_host_is_refused(monkeypatch):
    monkeypatch.delenv("V2_ALLOWED_HOSTS", raising=False)
    assert _Request({"Host": "127.0.0.1:8100"}).local_request()
    refused = _Request({"Host": APP})
    assert not refused.local_request() and refused.sent == 403


def test_a_listed_public_host_is_accepted_with_its_own_origin(monkeypatch):
    monkeypatch.setenv("V2_ALLOWED_HOSTS", f" {APP} , other.example.org")
    assert _Request({"Host": APP}).local_request()
    assert _Request({"Host": APP, "Origin": f"https://{APP}"}).local_request(mutating=True)
    assert _Request({"Host": "other.example.org", "Origin": "http://other.example.org"}).local_request(mutating=True)


@pytest.mark.parametrize("headers", [
    {"Host": "evil.example.org"},                                         # not listed
    {"Host": APP, "Origin": "https://evil.example.org"},                  # another site's page
    {"Host": APP, "Sec-Fetch-Site": "cross-site"},
    {"Host": "127.0.0.1:8100", "Origin": "https://127.0.0.1:8100"},      # loopback stays http only
])
def test_other_hosts_and_other_origins_are_still_refused(monkeypatch, headers):
    monkeypatch.setenv("V2_ALLOWED_HOSTS", APP)
    request = _Request(headers)
    assert not request.local_request(mutating="Origin" in headers) and request.sent == 403


def test_a_write_without_an_origin_is_refused_on_a_public_host(monkeypatch):
    monkeypatch.setenv("V2_ALLOWED_HOSTS", APP)
    assert not _Request({"Host": APP}).local_request(mutating=True)


def test_the_port_defaults_to_the_application_port(monkeypatch):
    monkeypatch.setenv("CDSW_APP_PORT", "8100")
    assert ws.default_port() == 8100
    monkeypatch.delenv("CDSW_APP_PORT")
    assert ws.default_port() == 8137
