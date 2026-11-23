"""Step 6, contract C2: live connection definitions, stored server-side (plan D4).

The store is `<state-dir>/connections.json`, owner-only (0600), written atomically. A GET never returns a secret value,
only whether it is set. A secret left empty on save keeps its stored value; `null` clears it. `import` copies the seven
non-model `connections` keys of the data operations workspace settings; nothing else of that file is kept.

Sign-in to the CDE jobs API: a pasted bearer token (`cde_token`), or an access key pair exchanged at the control plane
(`cdp_endpoint`) for a workload token with the installed `cdp` CLI:
`cdp --endpoint-url <cdp_endpoint> --form-factor private iam generate-workload-auth-token --workload-name DE`.
The key travels in the subprocess ENVIRONMENT (CDP_ACCESS_KEY_ID, CDP_PRIVATE_KEY) with an empty temporary HOME, so no
key is on a command line and no ~/.cdp profile is read. The token is cached in memory until shortly before it expires
and is never written to disk. The exchange has not been run against the private control plane (no key was available).
"""

import datetime
import hashlib
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import time
from pathlib import Path
from threading import Lock, RLock
from urllib.parse import urlsplit

import httpx

ICEBERG = "iceberg_cde"
POSTGRES = "postgres"
FIELDS = ("cde_url", "cde_ca_bundle", "cdp_endpoint", "access_key_id", "access_key_secret", "cde_token",
          "iceberg_catalog", "iceberg_namespace", "polaris_uri", "polaris_warehouse", "polaris_scope",
          "cde_polaris_secret_name", "cde_polaris_secret_key", "enabled")
SECRETS = {"access_key_secret", "cde_token"}
URLS = {"cde_url", "cdp_endpoint", "polaris_uri"}
ALLOWED = ("lakehouse", "demo_silver")       # the only catalog and namespace the user authorized
IMPORT = {"cde_url": "cde_url", "cde_ca_bundle": "cde_ca_bundle", "cde_jobs_enabled": "enabled",
          "iceberg_catalog": "iceberg_catalog", "iceberg_namespace": "iceberg_namespace",
          "cde_polaris_secret_name": "cde_polaris_secret_name", "cde_polaris_secret_key": "cde_polaris_secret_key"}
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
LABELS = {POSTGRES: "Private PostgreSQL (local)", ICEBERG: "Iceberg through Cloudera Data Engineering"}
POLARIS_NOTE = ("Polaris did not answer from this machine. The Spark job runs inside the cluster and may still reach "
                "it; this check is for information only.")


class SignInError(Exception):
    """A sign-in failure. The text never holds a key, a secret or a token."""


def scrub(text, *secrets):
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


def _url(name, value):
    p = urlsplit(value)
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password or p.query or p.fragment:
        raise ValueError(f"{name} must be an http(s) address without credentials, query or fragment")
    return value.rstrip("/")


def problem(values):
    """Why the Iceberg target cannot be submitted to, by field name only; None when it can."""
    missing = [k for k in ("cde_url", "iceberg_catalog", "iceberg_namespace", "cde_polaris_secret_name",
                           "cde_polaris_secret_key") if not values.get(k)]
    if missing:
        return "Iceberg settings are incomplete: " + ", ".join(missing)
    if (values["iceberg_catalog"], values["iceberg_namespace"]) != ALLOWED:
        return "Only Iceberg lakehouse.demo_silver is authorized"
    if values.get("enabled") != "true":
        return "Job submission is disabled (enabled)"
    if not values.get("cde_token") and not (values.get("cdp_endpoint") and values.get("access_key_id")
                                            and values.get("access_key_secret")):
        return "No sign-in: set cde_token, or cdp_endpoint with access_key_id and access_key_secret"
    return None


class Connections:
    def __init__(self, path):
        self.path, self.lock, self.last_tests = Path(path), RLock(), {}

    def _read(self):
        try:
            data = json.loads(self.path.read_text())
        except FileNotFoundError:
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def values(self, target):
        """Every field of a target, secrets included. Server-side use only."""
        stored = self._read().get(target) or {}
        return {k: str(stored.get(k) or "") for k in FIELDS}

    def view(self, target, postgres_configured=False):
        if target == POSTGRES:
            return {"id": POSTGRES, "kind": POSTGRES, "label": LABELS[POSTGRES], "configured": bool(postgres_configured),
                    "fields": {}, "lastTest": self.last_tests.get(POSTGRES)}
        if target != ICEBERG:
            raise KeyError(target)
        v = self.values(ICEBERG)
        fields = {k: {"value": None if k in SECRETS else (v[k] or None), "secret": k in SECRETS, "set": bool(v[k])}
                  for k in FIELDS}
        return {"id": ICEBERG, "kind": ICEBERG, "label": LABELS[ICEBERG], "configured": problem(v) is None,
                "problem": problem(v), "fields": fields, "lastTest": self.last_tests.get(ICEBERG)}

    def listing(self, postgres_configured):
        return {"targets": [self.view(POSTGRES, postgres_configured), self.view(ICEBERG)]}

    def save(self, target, fields):
        if target == POSTGRES:
            raise ValueError("The local PostgreSQL connection is managed by the server and is read-only")
        if target != ICEBERG:
            raise KeyError(target)
        if not isinstance(fields, dict) or set(fields) - set(FIELDS):
            raise ValueError("Unknown connection field: " + ", ".join(sorted(set(fields) - set(FIELDS)))[:200]
                             if isinstance(fields, dict) else "Require a fields object")
        clean = {}
        for key, value in fields.items():
            if value is None:
                if key in SECRETS:
                    clean[key] = ""          # null clears a secret
                continue
            if not isinstance(value, str) or len(value) > 6000:
                raise ValueError(f"{key} must be a text of up to 6,000 characters")
            value = value.strip()
            if key in SECRETS and not value:
                continue                     # empty keeps the stored secret
            if value and key in URLS:
                value = _url(key, value)
            if key == "enabled" and value not in ("true", "false", ""):
                raise ValueError("enabled must be true or false")
            if key == "iceberg_catalog" and value not in ("", ALLOWED[0]):
                raise ValueError("Only catalog lakehouse is authorized")
            if key == "iceberg_namespace" and value not in ("", ALLOWED[1]):
                raise ValueError("Only namespace demo_silver is authorized")
            if key in ("cde_polaris_secret_name", "cde_polaris_secret_key") and value and not NAME.match(value):
                raise ValueError(f"{key} must be a plain name")
            clean[key] = value
        with self.lock:
            data = self._read()
            data[ICEBERG] = {**(data.get(ICEBERG) or {}), **clean}
            self._write(data)
        SIGN_IN.forget()
        return self.view(ICEBERG)

    def import_workspace(self, target, environ=os.environ):
        """Copy the seven non-model connection keys of `$V2_ICEBERG_SETTINGS_DIR/runtime/settings.json`."""
        base = environ.get("V2_ICEBERG_SETTINGS_DIR")
        if not base:
            raise ValueError("V2_ICEBERG_SETTINGS_DIR is not set for the server")
        try:
            found = json.loads((Path(base) / "runtime" / "settings.json").read_text()).get("connections") or {}
        except (OSError, ValueError, AttributeError):
            raise ValueError("The workspace settings file could not be read") from None
        return self.save(target, {mine: str(found.get(theirs) or "") for theirs, mine in IMPORT.items()
                                  if found.get(theirs) not in (None, "")})

    def record_test(self, target, result):
        self.last_tests[target] = {**result, "at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}
        return self.last_tests[target]


# ---------------------------------------------------------------- sign-in

def cdp_exchange(endpoint, key_id, secret, ca_bundle, timeout=60):
    """(token, expires epoch) from `cdp iam generate-workload-auth-token --workload-name DE`. Unverified live."""
    exe = os.environ.get("V2_CDP_BIN") or shutil.which("cdp")
    if not exe:
        raise SignInError("The cdp command is not installed on the server; paste a CDE token instead")
    command = [exe, "--endpoint-url", endpoint, "--form-factor", "private", "--output", "json"]
    if ca_bundle:
        command += ["--ca-bundle", ca_bundle]
    command += ["iam", "generate-workload-auth-token", "--workload-name", "DE"]
    with tempfile.TemporaryDirectory(prefix="v2-cdp-") as home:   # empty HOME: no ~/.cdp profile or config
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": home, "LANG": "C.UTF-8",
               "CDP_ACCESS_KEY_ID": key_id, "CDP_PRIVATE_KEY": secret}
        try:
            done = subprocess.run(command, env=env, capture_output=True, text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            raise SignInError("Sign-in by access key timed out") from None
        except OSError as error:
            raise SignInError(f"The cdp command could not run ({type(error).__name__})") from None
    if done.returncode != 0:
        detail = next((line for line in done.stderr.splitlines() if line.strip()), "")[:200]
        raise SignInError(scrub(f"Sign-in by access key failed (cdp exit {done.returncode}): {detail}", secret, key_id))
    try:
        reply = json.loads(done.stdout)
        token = reply["token"]
        expires = datetime.datetime.fromisoformat(reply["expireAt"].replace("Z", "+00:00")).timestamp()
    except (ValueError, KeyError, TypeError, AttributeError):
        raise SignInError("The control plane reply had no token") from None
    return token, int(expires)


class SignIn:
    """The CDE bearer token. ponytail: one in-memory cache per process; a restart signs in again."""

    def __init__(self):
        self._cache, self._lock = {}, Lock()

    def forget(self):
        with self._lock:
            self._cache.clear()

    def token(self, values, exchange=None, now=None):
        if values.get("cde_token"):
            return values["cde_token"]
        endpoint, key_id, secret = values.get("cdp_endpoint"), values.get("access_key_id"), values.get("access_key_secret")
        if not (endpoint and key_id and secret):
            raise SignInError("No sign-in: set cde_token, or cdp_endpoint with access_key_id and access_key_secret")
        now = time.time() if now is None else now
        key = (endpoint, key_id, hashlib.sha256(secret.encode()).hexdigest())
        with self._lock:
            held = self._cache.get(key)
            if held and held[1] - 60 > now:
                return held[0]
        try:
            token, expires = (exchange or cdp_exchange)(endpoint, key_id, secret, values.get("cde_ca_bundle") or "")
        except SignInError as error:
            raise SignInError(scrub(str(error), secret)) from None
        with self._lock:
            self._cache[key] = (token, expires)
        return token


SIGN_IN = SignIn()


# ---------------------------------------------------------------- read-only test

def _probe(host, port):
    try:
        with socket.create_connection((host, port), timeout=3):
            return True
    except OSError:
        return False


def run_test(values, *, transport=None, probe=_probe, sign_in=None):
    """{"ok", "checks": [{name, ok, message, required}]}. Read-only GETs; nothing is created or changed."""
    checks = []

    def check(name, ok, message, required=True):
        checks.append({"name": name, "ok": bool(ok), "message": message, "required": required})

    ca, url = values.get("cde_ca_bundle"), values.get("cde_url")
    token = None
    try:
        token = (sign_in or SIGN_IN.token)(values)
        signed = None
    except SignInError as error:
        signed = str(error)
    secrets = (values.get("access_key_secret"), values.get("cde_token"), token)
    if not url:
        check("Jobs API address", False, "cde_url is not set")
        check("Sign-in", False, "Not tried: cde_url is not set")
    else:
        try:
            verify = True if transport is not None or not ca else ssl.create_default_context(cafile=ca)
            with httpx.Client(base_url=url, verify=verify, timeout=10, transport=transport) as client:
                client.get("/jobs", params={"limit": 1})      # any HTTP reply proves the address and TLS
                check("Jobs API address", True, "The jobs API answered over TLS.")
                if signed:
                    check("Sign-in", False, scrub(signed, *secrets))
                else:
                    reply = client.get("/jobs", params={"limit": 1}, headers={"Authorization": "Bearer " + token})
                    ok = 200 <= reply.status_code < 300
                    check("Sign-in", ok, "Signed in: the jobs list was read." if ok
                          else f"Sign-in refused: the jobs API returned HTTP {reply.status_code}.")
        except (httpx.HTTPError, OSError, ssl.SSLError) as error:
            check("Jobs API address", False, f"The jobs API did not answer ({type(error).__name__}).")
            check("Sign-in", False, "Not tried: the jobs API did not answer.")
    allowed = (values.get("iceberg_catalog"), values.get("iceberg_namespace")) == ALLOWED
    check("Catalog and namespace", allowed, "lakehouse.demo_silver is the authorized target." if allowed
          else "Only lakehouse.demo_silver is authorized.")
    polaris = urlsplit(values.get("polaris_uri") or "http://192.0.2.10:32383/api/catalog")
    reached = polaris.hostname and probe(polaris.hostname, polaris.port or (443 if polaris.scheme == "https" else 80))
    check("Polaris address", reached, "Polaris answered from this machine." if reached else POLARIS_NOTE, required=False)
    return {"ok": all(c["ok"] for c in checks if c["required"]), "checks": checks}
