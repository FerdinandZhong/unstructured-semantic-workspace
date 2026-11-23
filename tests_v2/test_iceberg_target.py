"""Output C, optional Iceberg write target through Cloudera Data Engineering (CDE). User decision of 22 November 2026.

Every CDE call goes to a FAKE transport (httpx.MockTransport). No test opens a connection to CDE or Polaris.
All settings below are test doubles; none is a recorded or live value.
"""
import json

import httpx
import pytest

from semantic_pipeline_v2 import iceberg_target
from semantic_pipeline_v2.iceberg_target import IcebergTarget, load
from semantic_pipeline_v2.physical_mapping import build_rows, row_values
from semantic_pipeline_v2.workspace import load_workspace, review_database
from test_interpreter import pg_bin
from test_physical_mapping import CHUNK, DATASET, FORM_1, act, needs_forms, read_form, server
from test_physical_mapping import template_links
from test_workspace import running_server

TOKEN = "tok-FAKE-cde-123"
MODEL_SENTINEL = "MODEL-KEY-SENTINEL-999"
DSN_SENTINEL = "DSN-PASSWORD-SENTINEL"
SETTINGS = {"cde_url": "https://cde.example/dex/api/v1", "cde_ca_bundle": "", "cde_jobs_enabled": "true",
            "iceberg_catalog": "lakehouse", "iceberg_namespace": "demo_silver",
            "cde_polaris_secret_name": "polaris-v2", "cde_polaris_secret_key": "credential"}


class Cde:
    """Fake CDE 1.5 API. Records the calls; never touches a network."""
    def __init__(self, fail_on=None, http=None, state="running"):
        self.calls, self.fail_on, self.http, self.state = [], fail_on, http, state

    def __call__(self, request):
        self.calls.append((request.method, request.url.path))
        if self.http:
            return httpx.Response(self.http)
        if self.fail_on and request.url.path.endswith(self.fail_on):
            raise httpx.ReadTimeout("timeout with Bearer " + TOKEN)
        if request.url.path.endswith("/run"):
            return httpx.Response(200, json={"id": 77})
        if "/job-runs/" in request.url.path:
            return httpx.Response(200, json={"status": self.state})
        return httpx.Response(201, json={})


def settings_dir(tmp_path, **override):
    connections = {**SETTINGS, "source_dsn": "postgresql://u:" + DSN_SENTINEL + "@h/db", **override}
    (tmp_path / "runtime").mkdir()
    (tmp_path / "runtime" / "settings.json").write_text(json.dumps(
        {"connections": connections, "models": {"chat": {"api_key": MODEL_SENTINEL}}}))
    return tmp_path


def target(cde=None, **override):
    return IcebergTarget({**SETTINGS, **override}, TOKEN, transport=httpx.MockTransport(cde or Cde()))


def rows():
    workspace = load_workspace(DATASET)
    return workspace, build_rows(workspace, template_links(workspace, [CHUNK]))


# ---------------------------------------------------------------- settings

def test_unset_variable_or_missing_file_means_not_configured(tmp_path):
    assert load({}) is None
    assert load({"V2_ICEBERG_SETTINGS_DIR": str(tmp_path)}) is None


def test_settings_load_only_the_listed_connection_keys_never_models(tmp_path):
    found = load({"V2_ICEBERG_SETTINGS_DIR": str(settings_dir(tmp_path)), "CDE_TOKEN": TOKEN})
    assert set(found.settings) == set(iceberg_target.KEYS) and found.problem() is None
    assert MODEL_SENTINEL not in repr(vars(found)) and DSN_SENTINEL not in repr(vars(found))
    info = found.info()
    assert info["available"] is True and TOKEN not in json.dumps(info)
    assert info["targets"] == ["Iceberg lakehouse.demo_silver.ext_str_transaction via Cloudera Data Engineering",
                               "Iceberg lakehouse.demo_silver.ext_str_account_entity via Cloudera Data Engineering"]


def test_a_token_in_the_settings_dot_env_is_used_when_the_process_has_none(tmp_path):
    base = settings_dir(tmp_path)
    (base / ".env").write_text("OTHER=1\nCDE_TOKEN=" + TOKEN + "\n")
    assert load({"V2_ICEBERG_SETTINGS_DIR": str(base)}).problem() is None


def test_problems_are_named_without_values(tmp_path):
    assert "CDE_TOKEN" in IcebergTarget(SETTINGS, "").problem()
    assert "cde_jobs_enabled" in target(cde_jobs_enabled="false").problem()
    assert "lakehouse.demo_silver" in target(iceberg_namespace="lakehouse_dataset_x").problem()
    assert "cde_polaris_secret_name" in target(cde_polaris_secret_name="").problem()
    assert target(cde_polaris_secret_name="").info()["available"] is False


# ---------------------------------------------------------------- the job

def test_job_spec_names_catalog_namespace_tables_and_references_the_secret_by_name_only():
    workspace, built = rows()
    script, spec, payload = target().build(workspace, built, source_id="s", actor="admin", fingerprint="f" * 64)
    conf = spec["spark"]["conf"]
    assert conf["spark.sql.catalog.lakehouse"] == "org.apache.iceberg.spark.SparkCatalog"
    assert conf["spark.sql.catalog.lakehouse.catalog-impl"] == "org.apache.iceberg.rest.RESTCatalog"
    assert not any(k.endswith((".credential", ".token")) for k in conf)
    # settings of the user's working job command (22 November 2026)
    assert conf["cde.iceberg.enabled"] == "true"
    assert conf["spark.sql.catalog.lakehouse.client.region"] == "us-east-1"
    assert conf["spark.sql.catalog.lakehouse.header.X-Iceberg-Access-Delegation"] == "vended-credentials"
    assert spec["workloadCredentials"] == ["polaris-v2"]
    assert spec["spark"]["args"] == ["--secret-file", "/etc/dex/secrets/polaris-v2/credential"]
    assert spec["mounts"] == [{"resourceName": spec["name"]}] and spec["spark"]["file"] == iceberg_target.SCRIPT
    [table] = payload["tables"]
    assert table["target"] == "lakehouse.demo_silver.ext_str_transaction"
    text = script + json.dumps(spec)
    assert TOKEN not in text and "Bearer" not in text
    compile(script, iceberg_target.SCRIPT, "exec")                      # valid Python; pyspark runs only on CDE


def test_ddl_adds_provenance_and_merge_inserts_only_missing_row_keys_so_a_replay_changes_nothing():
    workspace, built = rows()
    _, _, payload = target().build(workspace, built, source_id="s", actor="admin", fingerprint="f" * 64)
    [table] = payload["tables"]
    assert table["ddl"].startswith("CREATE TABLE IF NOT EXISTS lakehouse.demo_silver.ext_str_transaction (")
    for column in ("v2_source_id STRING", "v2_source_revision STRING", "v2_row_key STRING", "v2_written_at TIMESTAMP",
                   "v2_principal STRING", "amountoriginal DECIMAL(38,6)", "transactionseq BIGINT"):
        assert column in table["ddl"]
    merge = table["merge"]
    assert merge.startswith("MERGE INTO lakehouse.demo_silver.ext_str_transaction t USING")
    assert ("ON t.v2_source_id = s.v2_source_id AND t.v2_source_revision = s.v2_source_revision"
            " AND t.v2_row_key = s.v2_row_key") in merge
    assert merge.endswith("WHEN NOT MATCHED THEN INSERT *") and "WHEN MATCHED" not in merge
    assert table["check"].startswith("SELECT count(*) FROM lakehouse.demo_silver.ext_str_transaction t JOIN")
    # the rows are exactly the values PostgreSQL receives, plus provenance
    [row] = built
    names = [n.split()[0] for n in table["schema"].split(", ")]
    record = dict(zip(names, table["rows"][0]))
    expected = {k.lower(): str(v) for k, v in row_values(workspace, row).items()}
    assert {k: v for k, v in record.items() if not k.startswith("v2_") and v is not None} == expected
    assert record["v2_source_id"] == "s" and record["v2_row_key"] == "1" and record["v2_principal"] == "admin"
    assert table["expected"] == 1


# ---------------------------------------------------------------- submission (fake transport)

def test_submit_makes_four_calls_once_and_status_maps_the_cde_state():
    workspace, built = rows()
    cde = Cde(state="succeeded")
    ice = target(cde)
    script, spec, _ = ice.build(workspace, built, source_id="s", actor="admin", fingerprint="f" * 64)
    out = ice.submit(script, spec)
    name = spec["name"]
    assert out == {"status": "submitted", "runId": 77, "jobName": name,
                   "message": "One Spark job submitted to Cloudera Data Engineering."}
    assert cde.calls == [("POST", "/dex/api/v1/resources"), ("PUT", f"/dex/api/v1/resources/{name}/{iceberg_target.SCRIPT}"),
                         ("POST", "/dex/api/v1/jobs"), ("POST", f"/dex/api/v1/jobs/{name}/run")]
    assert ice.status(77) == "completed"


def test_failures_are_reported_plainly_with_secrets_scrubbed():
    workspace, built = rows()
    for cde, status in ((Cde(http=500), "failed"), (Cde(fail_on="/run"), "unknown")):
        ice = target(cde)
        out = ice.submit(*ice.build(workspace, built, source_id="s", actor="a", fingerprint="f" * 64)[:2])
        assert out["status"] == status and TOKEN not in json.dumps(out) and "Bearer" not in json.dumps(out)
        assert out["jobName"] and "runId" not in out
    assert "Nothing was created" in target(Cde(http=500)).submit(
        *target().build(workspace, built, source_id="s", actor="a", fingerprint="f" * 64)[:2])["message"]
    assert iceberg_target.scrub("x " + TOKEN + " y", TOKEN) == "x *** y"


# ---------------------------------------------------------------- server: preview, confirm, admin, default off

def ice_server(tmp_path, monkeypatch, cde, **override):
    principal, options = server(tmp_path, monkeypatch)
    options["iceberg"] = lambda: target(cde, **override)
    return principal, options


def confirm(call, preview, **extra):
    return call("POST", "/api/physical-rows/confirm", {"dataset": DATASET, "sourceId": FORM_1,
                                                       "fingerprint": preview["fingerprint"], **extra})


@needs_forms
def test_without_a_target_the_preview_names_private_postgresql_only(tmp_path, monkeypatch):
    _, options = server(tmp_path, monkeypatch)
    options["iceberg"] = lambda: None
    with review_database(tmp_path / "state", pg_bin()) as database, running_server(database, **options) as call:
        read_form(call, FORM_1)
        status, preview = act(call, "preview", FORM_1)
        assert status == 200 and preview["target"] == "Private PostgreSQL" and preview["iceberg"] is None
        assert preview["fingerprint"]
        assert confirm(call, preview, iceberg=True)[0] == 409       # no target, no Iceberg write
        assert act(call, "confirm", FORM_1)[0] == 200


@needs_forms
def test_preview_states_the_iceberg_target_and_confirm_writes_postgres_and_submits_one_job_once(tmp_path, monkeypatch):
    cde = Cde(state="succeeded")
    _, options = ice_server(tmp_path, monkeypatch, cde)
    with review_database(tmp_path / "state", pg_bin()) as database, running_server(database, **options) as call:
        read_form(call, FORM_1)
        status, preview = act(call, "preview", FORM_1)
        ice = preview["iceberg"]
        assert status == 200 and ice["available"] is True and ice["jobs"] == 1 and ice["approval"]
        assert ice["targets"][0] == "Iceberg lakehouse.demo_silver.ext_str_transaction via Cloudera Data Engineering"
        assert ice["tables"] == ["lakehouse.demo_silver.ext_str_transaction", "lakehouse.demo_silver.ext_str_account_entity"]
        assert ice["rows"] == 2 and ice["cells"] > ice["rows"] and TOKEN not in json.dumps(preview)
        # default off: a plain confirm writes PostgreSQL only and calls no CDE endpoint
        # (checked on a second form below); here a wrong fingerprint is refused before anything is written
        assert confirm(call, {"fingerprint": "0" * 64}, iceberg=True, approval=ice["approval"])[0] == 409
        assert cde.calls == [] and [r["status"] for r in act(call, "preview", FORM_1)[1]["rows"]] == ["previewed"] * 2
        status, preview = act(call, "preview", FORM_1)
        status, done = confirm(call, preview, iceberg=True, approval=preview["iceberg"]["approval"])
        assert status == 200 and [r["status"] for r in done["rows"]] == ["written", "written"]
        job = done["iceberg"]["job"]
        assert job["status"] == "submitted" and job["runId"] == 77 and len(cde.calls) == 4
        # the approval is one use
        again = confirm(call, preview, iceberg=True, approval=preview["iceberg"]["approval"])
        assert again[0] == 409 and len(cde.calls) == 4
        # status is read once from CDE and recorded
        status, polled = act(call, "iceberg-status", FORM_1)
        assert status == 200 and polled["iceberg"]["job"]["status"] == "completed" and cde.calls[-1][0] == "GET"
        assert act(call, "preview", FORM_1)[1]["iceberg"]["job"]["status"] == "completed"


@needs_forms
def test_plain_confirm_never_calls_cde_and_a_cde_failure_keeps_postgres_and_reports_the_job(tmp_path, monkeypatch):
    cde = Cde(http=500)
    principal, options = ice_server(tmp_path, monkeypatch, cde)
    with review_database(tmp_path / "state", pg_bin()) as database, running_server(database, **options) as call:
        read_form(call, FORM_1)
        status, preview = act(call, "preview", FORM_1)
        assert act(call, "confirm", FORM_1)[0] == 200 and cde.calls == []           # default off
        status, preview = act(call, "preview", FORM_1)                             # rows already written: may still go to Iceberg
        status, done = confirm(call, preview, iceberg=True, approval=preview["iceberg"]["approval"])
        assert status == 200 and [r["status"] for r in done["rows"]] == ["written", "written"]
        job = done["iceberg"]["job"]
        assert job["status"] == "failed" and job["jobName"] and "HTTP 500" in job["message"] and TOKEN not in json.dumps(done)
        # only an admin may confirm or poll
        principal["role"] = "reader"
        assert confirm(call, preview, iceberg=True, approval="x")[0] == 403
        assert act(call, "iceberg-status", FORM_1)[0] == 403
        assert len(cde.calls) == 1
