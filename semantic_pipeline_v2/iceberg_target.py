"""Output C, optional Iceberg write target (user decision, 22 November 2026).

Confirmed form rows may also go to Iceberg `lakehouse.demo_silver` through one Cloudera Data Engineering (CDE) Spark
job, with Polaris as the Iceberg REST catalog. The pattern follows the sibling workspace's CDE migration job: preview,
fingerprint binding, one-use approval, a single submit, a read-only status GET and a post-check inside the job.

Settings are read at call time from the step-6 connections store (`connections.py`, 22 November 2026) first; when it
holds no Iceberg settings, IN PLACE from `$V2_ICEBERG_SETTINGS_DIR/runtime/settings.json` (only the `connections` keys
in KEYS) and the CDE bearer token from env `CDE_TOKEN` or `CDE_TOKEN=` in `$V2_ICEBERG_SETTINGS_DIR/.env`.
With neither the target is not configured. A job is always GENERATED for a preview (script, spec and a `cde-cli`
command with credential placeholders); it is SUBMITTED only when the target is configured and the user confirms. The Polaris credential is a CDE workload secret referenced by
name; the job reads it from its mount at run time. No credential is in the job spec, the script or any response.
PySpark runs only inside the CDE job; it is never imported here.
"""

import datetime
import hashlib
import json
import os
import re
import shlex
import ssl
from pathlib import Path

import httpx

from .physical_mapping import declared, row_values
from .semantic_store import _lit

KEYS = ("cde_url", "cde_ca_bundle", "cde_jobs_enabled", "iceberg_catalog", "iceberg_namespace",
        "cde_polaris_secret_name", "cde_polaris_secret_key")
REQUIRED = ("cde_url", "iceberg_catalog", "iceberg_namespace", "cde_polaris_secret_name", "cde_polaris_secret_key")
ALLOWED = ("lakehouse", "demo_silver")         # the only catalog and namespace the user authorized (23 November 2026)
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
SCRIPT = "v2_str_rows.py"
ASK_SCRIPT = "v2_ask_rows.py"
SECRETS_ROOT = "/etc/dex/secrets"              # ponytail: CDE workload-secret mount path, unverified against a live cluster
# Non-secret Polaris endpoints, copied from the sibling's source_migration.POLARIS (its CDE job uses the same catalog).
POLARIS = {"uri": "http://192.0.2.10:32383/api/catalog", "warehouse": "demo_warehouse",
           "oauth2-server-uri": "http://192.0.2.10:32383/api/catalog/v1/oauth/tokens", "scope": "PRINCIPAL_ROLE:service_admin"}
SPARK_TYPE = {"String": "STRING", "Integer": "BIGINT", "Decimal": "DECIMAL(38,6)", "Date": "DATE"}
PROVENANCE = (("v2_source_id", "STRING"), ("v2_source_revision", "STRING"), ("v2_row_key", "STRING"),
              ("v2_written_at", "TIMESTAMP"), ("v2_principal", "STRING"))
STATES = {"starting": "running", "running": "running", "succeeded": "completed", "failed": "failed", "killed": "failed"}
UNKNOWN = "Submission outcome is uncertain. Inspect the job in CDE before any retry."


def from_store(store):
    """The target the connections store defines, or None when it holds no Iceberg settings. The token is fetched
    lazily (pasted token, or the access-key exchange cached in memory)."""
    from .connections import ICEBERG, SIGN_IN, problem
    v = store.values(ICEBERG)
    if not any(v.values()):
        return None
    settings = {k: v.get(k, "") for k in KEYS if k != "cde_jobs_enabled"}
    settings["cde_jobs_enabled"] = v["enabled"]
    polaris = dict(POLARIS)
    if v["polaris_uri"]:
        polaris.update(uri=v["polaris_uri"], **{"oauth2-server-uri": v["polaris_uri"] + "/v1/oauth/tokens"})
    polaris.update({k: v["polaris_" + k] for k in ("warehouse", "scope") if v["polaris_" + k]})
    identity = hashlib.sha256(json.dumps([v["cde_token"], v["cdp_endpoint"], v["access_key_id"],
                                          v["access_key_secret"]]).encode()).hexdigest()
    sign_in = problem(v)
    token = (lambda: SIGN_IN.token(store.values(ICEBERG))) if not (sign_in or "").startswith("No sign-in") else ""
    return IcebergTarget(settings, token, polaris=polaris, identity=identity,
                         extra={"cdp_endpoint": v["cdp_endpoint"]})


def load(environ=os.environ, store=None):
    """The configured target: from the connections store first, else from V2_ICEBERG_SETTINGS_DIR; None when neither."""
    if store is not None:
        found = from_store(store)
        if found is not None:
            return found
    base = environ.get("V2_ICEBERG_SETTINGS_DIR")
    if not base:
        return None
    try:
        connections = json.loads((Path(base) / "runtime" / "settings.json").read_text()).get("connections") or {}
    except (OSError, ValueError, AttributeError):
        return None
    settings = {k: str(connections.get(k) or "").strip() for k in KEYS}   # nothing else is kept
    token = environ.get("CDE_TOKEN", "")
    if not token:
        try:
            token = next((line.split("=", 1)[1].strip().strip("'\"") for line in (Path(base) / ".env").read_text().splitlines()
                          if line.startswith("CDE_TOKEN=")), "")
        except OSError:
            token = ""
    return IcebergTarget(settings, token)


def table_name(name):
    """STR_AccountEntity -> ext_str_account_entity: the silver schema's prefix for data taken from outside sources."""
    return "ext_" + re.sub(r"(?<=[a-z])(?=[A-Z])", "_", name).lower()


def scrub(text, *secrets):
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


class _Failure(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class IcebergTarget:
    def __init__(self, settings, token, transport=None, polaris=None, identity=None, extra=None):
        """`token` is the bearer token or a callable that returns it (raising on a failed sign-in)."""
        self.settings = {k: settings.get(k, "") for k in KEYS}
        self.token, self.transport, self.polaris = token, transport, polaris or dict(POLARIS)
        self.identity = identity or hashlib.sha256((token if isinstance(token, str) else "").encode()).hexdigest()
        self.extra, self._issued = extra or {}, ""

    @classmethod
    def generator(cls, base=None):
        """A target used only to GENERATE the job when no live target is configured: the authorized catalog and
        namespace, placeholder secret names, no token. `problem()` says why it cannot submit."""
        settings = dict(base.settings) if base else {}
        settings.update(iceberg_catalog=ALLOWED[0], iceberg_namespace=ALLOWED[1])
        for key, placeholder in (("cde_polaris_secret_name", "polaris-credential"), ("cde_polaris_secret_key", "credential")):
            settings[key] = settings.get(key) or placeholder
        return cls(settings, "", polaris=base.polaris if base else None, extra=base.extra if base else None)

    def problem(self):
        """Why the target cannot be written, by setting name only; None when it can."""
        s = self.settings
        missing = [k for k in REQUIRED if not s[k]]
        if missing:
            return "Iceberg settings are incomplete: " + ", ".join(missing)
        if (s["iceberg_catalog"], s["iceberg_namespace"]) != ALLOWED:
            return "Only Iceberg lakehouse.demo_silver is authorized for these rows"
        if not (NAME.match(s["cde_polaris_secret_name"]) and NAME.match(s["cde_polaris_secret_key"])):
            return "cde_polaris_secret_name or cde_polaris_secret_key is not a plain name"
        if s["cde_jobs_enabled"].lower() != "true":
            return "CDE job submission is disabled (cde_jobs_enabled)"
        if not self.token:
            return "No CDE sign-in: set a token or an access key in the connections panel (or CDE_TOKEN)"
        return None

    def tables(self, workspace=None):
        names = [t["name"] for t in declared(workspace)] if workspace else ["STR_Transaction", "STR_AccountEntity"]
        return [f"{ALLOWED[0]}.{ALLOWED[1]}.{table_name(n)}" for n in names]

    def info(self, workspace=None):
        problem = self.problem()
        return {"available": problem is None, "reason": problem, "jobs": 1, "tables": self.tables(workspace),
                "targets": [f"Iceberg {t} via Cloudera Data Engineering" for t in self.tables(workspace)]}

    def binding(self):
        """Non-secret settings plus a digest of the sign-in identity: a settings change invalidates a preview."""
        return json.dumps([self.settings[k] for k in KEYS] + [self.polaris, self.identity])

    # ------------------------------------------------------------ job

    def build(self, workspace, rows, *, source_id, actor, fingerprint):
        """(script, CDE job spec, payload) for the rows a confirm wrote. Deterministic SQL; MERGE inserts missing keys only."""
        catalog, namespace = self.settings["iceberg_catalog"], self.settings["iceberg_namespace"]
        written_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        job = "v2-str-rows-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S") + "-" + fingerprint[:8]
        tables = []
        for table in declared(workspace):
            selected = [r for r in rows if r["table"] == table["name"] and r["status"] in ("previewed", "written")]
            if not selected:
                continue
            target = f"{catalog}.{namespace}.{table_name(table['name'])}"
            columns = [(c["name"].lower(), SPARK_TYPE[c["datatype"]]) for c in table["columns"]] + list(PROVENANCE)
            view = "incoming_" + table_name(table["name"])
            data = []
            for row in selected:
                values = {k.lower(): None if v is None else str(v) for k, v in row_values(workspace, row).items()}
                values.update(v2_source_id=row.get("sourceId", source_id), v2_source_revision=row["cells"][0]["sourceRevision"],
                              v2_row_key=row["rowKey"], v2_written_at=written_at, v2_principal=actor)
                data.append([values.get(name) for name, _ in columns])
            key = ("t.v2_source_id = s.v2_source_id AND t.v2_source_revision = s.v2_source_revision"
                   " AND t.v2_row_key = s.v2_row_key")
            tables.append({
                "columns": [list(c) for c in columns], "keys": ["v2_source_id", "v2_source_revision", "v2_row_key"], "nullSafe": False,
                "target": target, "view": view, "expected": len(data), "rows": data,
                "schema": ", ".join(f"{name} STRING" for name, _ in columns),
                "ddl": f"CREATE TABLE IF NOT EXISTS {target} ({', '.join(f'{n} {t}' for n, t in columns)}) USING iceberg",
                "merge": (f"MERGE INTO {target} t USING (SELECT {', '.join(f'CAST({n} AS {t}) AS {n}' for n, t in columns)}"
                          f" FROM {view}) s ON {key} WHEN NOT MATCHED THEN INSERT *"),
                "check": f"SELECT count(*) FROM {target} t JOIN {view} s ON {key}"})
        return self.package(job, fingerprint, tables, SCRIPT)

    def build_answer(self, table, columns, rows, *, question, actor, fingerprint, keys=None):
        """(script, spec, payload) writing an answer result to `<catalog>.<namespace>.<table>`. `columns` are
        [(name, model datatype)] of the business columns; `rows` lists of values in that order. MERGE inserts a row only
        when no row has the same business values and question (null-safe), so a replay adds nothing. With `keys` (a
        discovered document table) there is no question column and only those columns decide a replay."""
        catalog, namespace = self.settings["iceberg_catalog"], self.settings["iceberg_namespace"]
        now = datetime.datetime.now(datetime.timezone.utc)
        job = "v2-ask-rows-" + now.strftime("%Y%m%d%H%M%S") + "-" + fingerprint[:8]
        target = f"{catalog}.{namespace}.{table}"
        asked = [] if keys else [question]
        typed = [(n, SPARK_TYPE.get(t, "STRING")) for n, t in columns] + [("v2_question", "STRING")] * len(asked) + [
            ("v2_written_at", "TIMESTAMP"), ("v2_principal", "STRING")]
        written_at = now.strftime("%Y-%m-%d %H:%M:%S")
        data = [[None if v is None else str(v) for v in row] + asked + [written_at, actor] for row in rows]
        keys = keys or [n for n, _ in typed[:-2]]
        key = " AND ".join(f"t.{n} <=> s.{n}" for n in keys)
        view = "incoming_" + table
        tables = [{"columns": [list(c) for c in typed], "keys": keys, "nullSafe": True,
                   "target": target, "view": view, "expected": len(data), "rows": data,
                   "schema": ", ".join(f"{n} STRING" for n, _ in typed),
                   "ddl": f"CREATE TABLE IF NOT EXISTS {target} ({', '.join(f'{n} {t}' for n, t in typed)}) USING iceberg",
                   "merge": (f"MERGE INTO {target} t USING (SELECT {', '.join(f'CAST({n} AS {t}) AS {n}' for n, t in typed)}"
                             f" FROM {view}) s ON {key} WHEN NOT MATCHED THEN INSERT *"),
                   "check": f"SELECT count(*) FROM {target} t WHERE EXISTS (SELECT 1 FROM {view} s WHERE {key})"}]
        return self.package(job, fingerprint, tables, ASK_SCRIPT)

    def package(self, job, fingerprint, tables, script_name):
        """(script, CDE job spec, payload). No credential is in any of them: the Polaris credential is a CDE workload
        secret mounted at run time (or, with `cde-cli`, the `$POLARIS_OAUTH_CREDENTIAL` the user's shell holds)."""
        catalog = self.settings["iceberg_catalog"]
        payload = {"job": job, "fingerprint": fingerprint, "namespace": f"{catalog}.{self.settings['iceberg_namespace']}",
                   "tables": tables}
        script = _SCRIPT.format(payload=json.dumps(json.dumps(payload)), catalog=json.dumps(catalog))
        c = "spark.sql.catalog." + catalog
        conf = {"spark.sql.extensions": "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
                c: "org.apache.iceberg.spark.SparkCatalog", c + ".catalog-impl": "org.apache.iceberg.rest.RESTCatalog",
                c + ".uri": self.polaris["uri"], c + ".warehouse": self.polaris["warehouse"], c + ".rest.auth.type": "oauth2",
                c + ".oauth2-server-uri": self.polaris["oauth2-server-uri"], c + ".scope": self.polaris["scope"],
                c + ".io-impl": "org.apache.iceberg.aws.s3.S3FileIO", c + ".rest.access-delegation": "vended-credentials",
                # the three below are in the user's working `cde spark submit` command (2026-11-22)
                "cde.iceberg.enabled": "true", c + ".client.region": "us-east-1",
                c + ".header.X-Iceberg-Access-Delegation": "vended-credentials"}
        name, key = self.settings["cde_polaris_secret_name"], self.settings["cde_polaris_secret_key"]
        spec = {"name": job, "type": "spark", "mounts": [{"resourceName": job}], "workloadCredentials": [name],
                "spark": {"file": script_name, "args": ["--secret-file", f"{SECRETS_ROOT}/{name}/{key}"], "conf": conf}}
        return script, spec, payload

    def command(self, spec):
        """The equivalent `cde-cli spark submit` line, credentials as shell variables, never values."""
        conf = spec["spark"]["conf"]
        catalog = self.settings["iceberg_catalog"]
        endpoint = shlex.quote(self.settings["cde_url"]) if self.settings["cde_url"] else '"$CDE_VCLUSTER_ENDPOINT"'
        control = shlex.quote(self.extra["cdp_endpoint"]) if self.extra.get("cdp_endpoint") else '"$CDP_ENDPOINT"'
        lines = [f"cde-cli --vcluster-endpoint {endpoint}", f"--cdp-endpoint {control}",
                 '--access-key-id "$CDE_ACCESS_KEY_ID" --access-key-secret "$CDE_ACCESS_KEY_SECRET"',
                 f"spark submit {spec['spark']['file']}"]
        lines += ["--conf " + shlex.quote(f"{k}={v}") for k, v in conf.items()]
        lines.append(f'--conf spark.sql.catalog.{catalog}.credential="$POLARIS_OAUTH_CREDENTIAL"')
        return " \\\n  ".join(lines)

    # ------------------------------------------------------------ CDE calls (CDE 1.5 /dex/api/v1, as the sibling)

    def _bearer(self):
        if callable(self.token):
            from .connections import SignInError
            try:
                self._issued = self.token()
            except SignInError as error:
                raise _Failure(str(error), 401) from None
            return self._issued
        return self.token

    def _client(self):
        ca = self.settings["cde_ca_bundle"]
        verify = True if self.transport is not None or not ca else ssl.create_default_context(cafile=ca)
        return httpx.Client(base_url=self.settings["cde_url"].rstrip("/"), verify=verify, timeout=30,
                            transport=self.transport, headers={"Authorization": "Bearer " + self._bearer()})

    def _send(self, client, method, path, **kw):
        try:
            response = client.request(method, path, **kw)
        except httpx.HTTPError as error:
            raise _Failure(f"CDE request failed ({type(error).__name__}).") from None   # no URL, header or body text
        if not 200 <= response.status_code < 300:
            raise _Failure(f"CDE returned HTTP {response.status_code}.", response.status_code)
        return response

    def _secret(self):
        return self.token if isinstance(self.token, str) else self._issued

    def submit(self, script, spec):
        """Make each CDE call once. No retry: an uncertain outcome is reported, never resubmitted."""
        name, step = spec["name"], 0
        try:
            with self._client() as client:
                self._send(client, "POST", "/resources", json={"name": name, "type": "files"})
                step = 1
                file = spec["spark"]["file"]
                self._send(client, "PUT", f"/resources/{name}/{file}", files={"file": (file, script.encode())})
                self._send(client, "POST", "/jobs", json=spec)
                try:
                    run_id = int(self._send(client, "POST", f"/jobs/{name}/run").json()["id"])
                except (ValueError, KeyError, TypeError):
                    raise _Failure("CDE run response was not understood.") from None
        except (_Failure, OSError, ssl.SSLError) as error:
            text = str(error) if isinstance(error, _Failure) else f"CDE client setup failed ({type(error).__name__})."
            if step == 0 and isinstance(error, _Failure) and error.status:
                message = f"CDE rejected the first call ({text}) Nothing was created."
                return {"status": "failed", "jobName": name, "message": scrub(message, self._secret())}
            return {"status": "unknown", "jobName": name, "message": scrub(f"{text} {UNKNOWN}", self._secret())}
        return {"status": "submitted", "runId": run_id, "jobName": name,
                "message": "One Spark job submitted to Cloudera Data Engineering."}

    def status(self, run_id):
        """One read-only GET. Returns running, completed, failed or unknown."""
        try:
            with self._client() as client:
                state = str(self._send(client, "GET", f"/job-runs/{int(run_id)}").json()["status"]).lower()
        except (_Failure, ValueError, KeyError, TypeError, OSError):
            return "unknown"
        return STATES.get(state, "unknown")


_SCRIPT = '''"""V2: write confirmed rows to Iceberg. Generated by the V2 workspace. No credential is in this file.
The Polaris credential is read at run time from the CDE workload secret mounted at --secret-file, or from the
spark.sql.catalog.<catalog>.credential setting the submitter passes. The rows are exactly the previewed rows."""
import argparse
import json
import sys

from pyspark.sql import SparkSession

PAYLOAD = json.loads({payload})
CATALOG = {catalog}


def retarget(table, namespace):
    """The table with its statements pointed at another <catalog>.<schema>, when one is given."""
    if not namespace:
        return table
    return {{key: value.replace(PAYLOAD["namespace"] + ".", namespace + ".") if isinstance(value, str) else value
            for key, value in table.items()}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--secret-file")   # CDE job: the mounted workload secret; cde-cli: the credential is a --conf
    parser.add_argument("--target-namespace")   # your own <catalog>.<schema>; default: the previewed one
    args = parser.parse_args()
    catalog = args.target_namespace.split(".")[0] if args.target_namespace else CATALOG
    builder = SparkSession.builder.appName(PAYLOAD["job"])
    if args.secret_file:
        with open(args.secret_file) as handle:
            builder = builder.config("spark.sql.catalog." + catalog + ".credential", handle.read().strip())
    spark = builder.getOrCreate()
    results = []
    for table in (retarget(item, args.target_namespace) for item in PAYLOAD["tables"]):
        spark.sql(table["ddl"])
        spark.createDataFrame(table["rows"], table["schema"]).createOrReplaceTempView(table["view"])
        spark.sql(table["merge"])
        found = spark.sql(table["check"]).first()[0]
        results.append({{"table": table["target"], "expected": table["expected"], "found": found}})
    print(json.dumps({{"job": PAYLOAD["job"], "fingerprint": PAYLOAD["fingerprint"], "post_check": results}}))
    if any(r["found"] != r["expected"] for r in results):
        sys.exit("Post-check failed: the Iceberg row count differs from the confirmed rows.")


if __name__ == "__main__":
    main()
'''


_DDL = """
    CREATE TABLE IF NOT EXISTS v2_iceberg_jobs (
        job_name text PRIMARY KEY, dataset text NOT NULL, source_id text NOT NULL, fingerprint text NOT NULL,
        run_id bigint, status text NOT NULL, message text NOT NULL, actor text NOT NULL,
        recorded_at timestamptz NOT NULL DEFAULT now());
"""


class IcebergJobs:
    """The submitted jobs, in the private PostgreSQL. Names, ids and status only."""
    def __init__(self, database):
        self.database = database
        database.sql(_DDL)

    def save(self, dataset, source_id, fingerprint, actor, job):
        run = str(int(job["runId"])) if job.get("runId") is not None else "NULL"
        self.database.sql(
            "INSERT INTO v2_iceberg_jobs (job_name, dataset, source_id, fingerprint, run_id, status, message, actor) VALUES ("
            f"{_lit(job['jobName'])}, {_lit(dataset)}, {_lit(source_id)}, {_lit(fingerprint)}, {run}, {_lit(job['status'])},"
            f" {_lit(job['message'])}, {_lit(actor)}) ON CONFLICT (job_name) DO UPDATE SET status = EXCLUDED.status,"
            " message = EXCLUDED.message, recorded_at = now();")

    def by_name(self, name):
        out = self.database.sql(
            "SELECT COALESCE((SELECT jsonb_build_object('jobName', job_name, 'runId', run_id, 'status', status,"
            " 'message', message, 'actor', actor, 'fingerprint', fingerprint, 'dataset', dataset, 'sourceId', source_id)"
            f" FROM v2_iceberg_jobs WHERE job_name = {_lit(name)}), 'null');")
        return json.loads(out)

    def latest(self, dataset, source_id):
        out = self.database.sql(
            "SELECT COALESCE((SELECT jsonb_build_object('jobName', job_name, 'runId', run_id, 'status', status,"
            " 'message', message, 'actor', actor, 'fingerprint', fingerprint, 'recordedAt', recorded_at) FROM v2_iceberg_jobs"
            f" WHERE dataset = {_lit(dataset)} AND source_id = {_lit(source_id)} ORDER BY recorded_at DESC LIMIT 1), 'null');")
        return json.loads(out)
