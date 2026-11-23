"""Step 6, contract C2: live connection definitions (plan T4, T5).

Every CDE call goes to a FAKE transport (httpx.MockTransport); the token exchange is a fake function or a fake `cdp`
executable written by the test. No test contacts CDE, Polaris or a control plane. Every value is a test double.
"""
import json
import os
import stat
import sys

import httpx
import pytest

from semantic_pipeline_v2 import connections, iceberg_target
from semantic_pipeline_v2.connections import Connections, SignIn, SignInError, cdp_exchange, run_test
from semantic_pipeline_v2.workspace import review_database
from test_interpreter import pg_bin
from test_physical_mapping import server
from test_workspace import running_server

SECRET = "KEY-SECRET-SENTINEL-4242"
TOKEN = "TOKEN-SENTINEL-777"
MODEL_SENTINEL = "MODEL-KEY-SENTINEL-999"
DSN_SENTINEL = "DSN-PASSWORD-SENTINEL"
FIELDS = {"cde_url": "https://cde.example/dex/api/v1", "cdp_endpoint": "https://console.example",
          "access_key_id": "ak-1", "access_key_secret": SECRET, "iceberg_catalog": "lakehouse",
          "iceberg_namespace": "demo_silver", "polaris_uri": "http://192.0.2.10:32383/api/catalog",
          "cde_polaris_secret_name": "polaris-v2", "cde_polaris_secret_key": "credential", "enabled": "true"}


def store(tmp_path):
    return Connections(tmp_path / "state" / "connections.json")


# ---------------------------------------------------------------- store (T4)

def test_secrets_are_stored_owner_only_and_never_returned(tmp_path):
    s = store(tmp_path)
    view = s.save("iceberg_cde", FIELDS)
    mode = stat.S_IMODE(os.stat(s.path).st_mode)
    assert mode == 0o600
    assert SECRET not in json.dumps(view) and SECRET not in json.dumps(s.listing(True))
    field = view["fields"]["access_key_secret"]
    assert field == {"value": None, "secret": True, "set": True}
    assert view["fields"]["cde_url"] == {"value": FIELDS["cde_url"], "secret": False, "set": True}
    assert view["fields"]["cde_token"] == {"value": None, "secret": True, "set": False}
    assert view["configured"] is True and view["kind"] == "iceberg_cde"
    [postgres, ice] = s.listing(True)["targets"]
    assert postgres["id"] == "postgres" and postgres["configured"] is True and ice["id"] == "iceberg_cde"


def test_an_empty_secret_keeps_the_stored_value_and_null_clears_it(tmp_path):
    s = store(tmp_path)
    s.save("iceberg_cde", FIELDS)
    s.save("iceberg_cde", {"access_key_secret": "", "cde_url": "https://other.example/dex/api/v1"})
    assert s.values("iceberg_cde")["access_key_secret"] == SECRET
    assert s.values("iceberg_cde")["cde_url"] == "https://other.example/dex/api/v1"
    s.save("iceberg_cde", {"access_key_secret": None})
    assert s.values("iceberg_cde")["access_key_secret"] == ""


@pytest.mark.parametrize("fields", [
    {"unknown": "x"}, {"iceberg_namespace": "lakehouse_dataset_x"}, {"iceberg_catalog": "other"},
    {"cde_url": "ftp://x"}, {"cde_url": "https://user:" + SECRET + "@h/x"}, {"enabled": "yes"},
    {"cde_polaris_secret_name": "a b"}, {"access_key_secret": 5},
])
def test_invalid_fields_are_refused_without_echoing_a_value(tmp_path, fields):
    with pytest.raises(ValueError) as error:
        store(tmp_path).save("iceberg_cde", fields)
    assert SECRET not in str(error.value)


def test_postgres_is_read_only_and_an_unknown_target_is_refused(tmp_path):
    with pytest.raises(ValueError):
        store(tmp_path).save("postgres", {})
    with pytest.raises(KeyError):
        store(tmp_path).save("nope", {})


def test_import_copies_the_seven_connection_keys_never_models_or_other_secrets(tmp_path):
    base = tmp_path / "ws"
    (base / "runtime").mkdir(parents=True)
    (base / "runtime" / "settings.json").write_text(json.dumps({
        "connections": {"cde_url": "https://cde.example/dex/api/v1", "cde_ca_bundle": "", "cde_jobs_enabled": "true",
                        "iceberg_catalog": "lakehouse", "iceberg_namespace": "demo_silver",
                        "cde_polaris_secret_name": "polaris-v2", "cde_polaris_secret_key": "credential",
                        "source_dsn": "postgresql://u:" + DSN_SENTINEL + "@h/db"},
        "models": [{"api_key": MODEL_SENTINEL}]}))
    s = store(tmp_path)
    view = s.import_workspace("iceberg_cde", {"V2_ICEBERG_SETTINGS_DIR": str(base)})
    raw = s.path.read_text()
    assert MODEL_SENTINEL not in raw and DSN_SENTINEL not in raw
    assert s.values("iceberg_cde")["enabled"] == "true" and s.values("iceberg_cde")["cde_polaris_secret_name"] == "polaris-v2"
    assert view["fields"]["cde_url"]["value"] == "https://cde.example/dex/api/v1"
    with pytest.raises(ValueError):
        s.import_workspace("iceberg_cde", {})          # no settings directory named


# ---------------------------------------------------------------- sign-in

class Exchange:
    def __init__(self, fail=False, ttl=3600):
        self.calls, self.fail, self.ttl = 0, fail, ttl

    def __call__(self, endpoint, key_id, secret, ca_bundle):
        self.calls += 1
        if self.fail:
            raise SignInError("cdp refused the key " + secret)
        return TOKEN, 1000 + self.ttl


def test_access_keys_are_exchanged_once_and_cached_in_memory_until_expiry(tmp_path):
    s = store(tmp_path)
    s.save("iceberg_cde", FIELDS)
    exchange, sign_in = Exchange(), SignIn()
    assert sign_in.token(s.values("iceberg_cde"), exchange=exchange, now=1000) == TOKEN
    assert sign_in.token(s.values("iceberg_cde"), exchange=exchange, now=2000) == TOKEN and exchange.calls == 1
    assert sign_in.token(s.values("iceberg_cde"), exchange=exchange, now=1000 + 3600) == TOKEN and exchange.calls == 2
    assert TOKEN not in s.path.read_text()                     # never on disk


def test_a_pasted_token_is_used_without_an_exchange_and_errors_are_scrubbed(tmp_path):
    exchange = Exchange()
    assert SignIn().token({**FIELDS, "cde_token": "pasted"}, exchange=exchange) == "pasted" and exchange.calls == 0
    with pytest.raises(SignInError) as error:
        SignIn().token(FIELDS, exchange=Exchange(fail=True), now=1)
    assert SECRET not in str(error.value)
    with pytest.raises(SignInError):
        SignIn().token({**FIELDS, "access_key_secret": ""}, exchange=exchange)


def fake_cdp(tmp_path, reply, code=0):
    record = tmp_path / "argv.json"
    script = tmp_path / "cdp"
    script.write_text(f"""#!{sys.executable}
import json, os, sys
json.dump({{"argv": sys.argv[1:], "key": os.environ.get("CDP_ACCESS_KEY_ID"),
           "private": os.environ.get("CDP_PRIVATE_KEY"), "home": os.environ.get("HOME"),
           "profile": os.environ.get("CDP_PROFILE")}}, open({str(record)!r}, "w"))
sys.stdout.write({json.dumps(reply)!r})
sys.stderr.write("An error occurred: bad key " + os.environ.get("CDP_PRIVATE_KEY", ""))
sys.exit({code})
""")
    script.chmod(0o700)
    return script, record


def test_the_cdp_exchange_passes_the_key_through_the_environment_never_the_command_line(tmp_path, monkeypatch):
    script, record = fake_cdp(tmp_path, {"token": TOKEN, "expireAt": "2030-01-01T00:00:00.000Z", "endpointUrl": "x"})
    monkeypatch.setenv("V2_CDP_BIN", str(script))
    monkeypatch.setenv("CDP_PROFILE", "someone")
    token, expires = cdp_exchange("https://console.example", "ak-1", SECRET, "")
    seen = json.loads(record.read_text())
    assert token == TOKEN and expires == 1893456000
    assert SECRET not in " ".join(seen["argv"]) and seen["private"] == SECRET and seen["key"] == "ak-1"
    assert seen["profile"] is None and seen["home"] != os.environ.get("HOME")      # no profile, no ~/.cdp
    assert seen["argv"][-4:] == ["iam", "generate-workload-auth-token", "--workload-name", "DE"]
    assert "--endpoint-url" in seen["argv"] and "https://console.example" in seen["argv"]


def test_a_failed_cdp_exchange_reports_without_the_secret(tmp_path, monkeypatch):
    script, _ = fake_cdp(tmp_path, {}, code=255)
    monkeypatch.setenv("V2_CDP_BIN", str(script))
    with pytest.raises(SignInError) as error:
        cdp_exchange("https://console.example", "ak-1", SECRET, "")
    assert SECRET not in str(error.value) and "255" in str(error.value)


# ---------------------------------------------------------------- test (T5)

class Cde:
    def __init__(self, status=200, down=False):
        self.calls, self.status, self.down = [], status, down

    def __call__(self, request):
        self.calls.append((request.method, request.url.path, request.headers.get("Authorization")))
        if self.down:
            raise httpx.ConnectError("refused " + SECRET)
        if request.headers.get("Authorization") is None:
            return httpx.Response(401)
        return httpx.Response(self.status, json={"jobs": []})


def checks_of(result):
    return {c["name"]: c for c in result["checks"]}


def test_checks_report_each_step_plainly_and_only_read(tmp_path):
    cde = Cde()
    result = run_test(FIELDS, transport=httpx.MockTransport(cde), probe=lambda host, port: False,
                      sign_in=lambda values: TOKEN)
    found = checks_of(result)
    assert list(found) == ["Jobs API address", "Sign-in", "Catalog and namespace", "Polaris address"]
    assert found["Jobs API address"]["ok"] and found["Sign-in"]["ok"] and found["Catalog and namespace"]["ok"]
    assert found["Polaris address"]["ok"] is False and "inside the cluster" in found["Polaris address"]["message"]
    assert result["ok"] is True                                   # Polaris from a laptop is informational
    assert {m for m, _, _ in cde.calls} == {"GET"} and all("/jobs" in p for _, p, _ in cde.calls)
    assert TOKEN not in json.dumps(result) and SECRET not in json.dumps(result)


def test_an_unreachable_address_fails_only_its_checks(tmp_path):
    cde = Cde(down=True)
    result = run_test(FIELDS, transport=httpx.MockTransport(cde), probe=lambda host, port: True,
                      sign_in=lambda values: TOKEN)
    found = checks_of(result)
    assert result["ok"] is False and found["Jobs API address"]["ok"] is False
    assert found["Catalog and namespace"]["ok"] and found["Polaris address"]["ok"]
    assert SECRET not in json.dumps(result)


def test_a_refused_sign_in_fails_the_sign_in_check(tmp_path):
    def refuse(values):
        raise SignInError("Sign-in by access key failed")
    result = run_test(FIELDS, transport=httpx.MockTransport(Cde()), probe=lambda h, p: True, sign_in=refuse)
    assert checks_of(result)["Sign-in"]["ok"] is False and result["ok"] is False
    result = run_test(FIELDS, transport=httpx.MockTransport(Cde(status=401)), probe=lambda h, p: True,
                      sign_in=lambda values: TOKEN)
    assert checks_of(result)["Sign-in"]["ok"] is False and "401" in checks_of(result)["Sign-in"]["message"]


# ---------------------------------------------------------------- the Iceberg target reads the store first

def test_iceberg_target_reads_the_store_first_then_the_environment(tmp_path):
    s = store(tmp_path)
    assert iceberg_target.load({}, store=s) is None                              # nothing anywhere
    s.save("iceberg_cde", FIELDS)
    ice = iceberg_target.load({}, store=s)
    assert ice.settings["cde_url"] == FIELDS["cde_url"] and ice.settings["cde_jobs_enabled"] == "true"
    assert ice.polaris["uri"] == FIELDS["polaris_uri"] and ice.polaris["oauth2-server-uri"].startswith(FIELDS["polaris_uri"])
    assert ice.problem() is None and SECRET not in ice.binding()
    s.save("iceberg_cde", {"access_key_secret": None, "access_key_id": ""})
    assert "sign-in" in iceberg_target.load({}, store=s).problem().lower()


# ---------------------------------------------------------------- routes (T4)

def test_routes_hide_secrets_and_a_reader_gets_403_on_save_test_and_import(tmp_path, monkeypatch):
    principal, options = server(tmp_path, monkeypatch)
    options["iceberg"] = lambda: None
    with review_database(tmp_path / "pg", pg_bin()) as database, running_server(database, **options) as call:
        status, saved = call("POST", "/api/connections/iceberg_cde", {"fields": FIELDS})
        assert status == 200 and SECRET not in json.dumps(saved) and saved["fields"]["access_key_secret"]["set"] is True
        status, listing = call("GET", "/api/connections")
        assert status == 200 and [t["id"] for t in listing["targets"]] == ["postgres", "iceberg_cde"]
        assert SECRET not in json.dumps(listing)
        assert call("POST", "/api/connections/iceberg_cde", {"fields": {"bogus": SECRET}})[0] == 400
        assert SECRET not in json.dumps(call("POST", "/api/connections/iceberg_cde", {"fields": {"bogus": SECRET}})[1])
        principal["role"] = "reader"
        assert call("GET", "/api/connections")[0] == 200
        for path, body in (("/api/connections/iceberg_cde", {"fields": {}}), ("/api/connections/iceberg_cde/test", {}),
                           ("/api/connections/iceberg_cde/import", {"from": "workspace"})):
            assert call("POST", path, body)[0] == 403
        assert os.stat(tmp_path / "connections.json").st_mode & 0o077 == 0
