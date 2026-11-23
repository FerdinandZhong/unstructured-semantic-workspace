"""Shared pure helpers for the Cloudera AI launch scripts (setup, prepare demo, start app). No side effects on import.

Layout on Cloudera AI (all under /home/cdsw, the only folder jobs and the application share):
  repository   /home/cdsw                              (the AMP checkout)
  app copy     /home/cdsw/.semantic-workspace-app      the code and data the jobs and the app run from
  venv         /home/cdsw/.venv-semantic-workspace     requirements-v2.lock + pgserver
  state        /home/cdsw/.semantic-workspace-state    private PostgreSQL, evidence, catalog

The app copy exists because the V2 state guard refuses a state folder inside the repository, and the repository on
Cloudera AI is /home/cdsw itself. # ponytail: a full copy per setup run (~100 MB); rerun setup after a git pull.
"""

import os
import shutil
from pathlib import Path

HOME = Path("/home/cdsw")
VENV = Path(os.environ.get("SEMANTIC_WORKSPACE_VENV") or HOME / ".venv-semantic-workspace")
APP_DIR = Path(os.environ.get("SEMANTIC_WORKSPACE_APP_DIR") or HOME / ".semantic-workspace-app")
STATE_DIR = Path(os.environ.get("SEMANTIC_WORKSPACE_STATE_DIR") or HOME / ".semantic-workspace-state")
PGSERVER = "pgserver==0.1.4"           # PostgreSQL 16 wheels; no root or Docker needed (same pin as the V1 app)
PG_TOOLS = ("initdb", "pg_ctl", "psql")
SUBDOMAIN = "semantic-workspace"       # must match `subdomain` in .project-metadata.yaml
DEFAULT_PORT = 8137
COPY_SKIP = {".git", "__pycache__", ".pytest_cache", "node_modules", ".DS_Store"}
PG_PROBE = "import pathlib, pgserver; print(pathlib.Path(pgserver.__file__).parent / 'pginstall' / 'bin')"
IMPORT_CHECK = """
import semantic_pipeline_v2.workspace_server, semantic_pipeline_v2.prepare_demo
from multimedia_rag.chunking import ChunkConfig
import lancedb, numpy, httpx, yaml, jsonschema, sqlglot
from semantic_pipeline_v2.workspace import DATASETS, load_workspace
print({key: len(load_workspace(key)["sources"]) for key in DATASETS})
"""


def clean_env(environ, **extra):
    """The child environment: injected Python paths removed so the venv's packages cannot be shadowed."""
    env = {k: v for k, v in environ.items() if k not in {"PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE"}}
    env.update({k: str(v) for k, v in extra.items() if v is not None})
    return env


def app_port(environ):
    """Cloudera AI gives the application its port in CDSW_APP_PORT."""
    return int(environ.get("CDSW_APP_PORT") or DEFAULT_PORT)


def allowed_hosts(environ, subdomain=SUBDOMAIN):
    """V2_ALLOWED_HOSTS for the server: hosts already listed, plus `<subdomain>.<CDSW_DOMAIN>` when the domain is known.
    # ponytail: assumes the public app host is subdomain + CDSW_DOMAIN; untested on a real workspace."""
    hosts = [h.strip() for h in environ.get("V2_ALLOWED_HOSTS", "").split(",") if h.strip()]
    domain = environ.get("CDSW_DOMAIN", "").strip().strip(".")
    if domain:
        public = f"{environ.get('SEMANTIC_WORKSPACE_SUBDOMAIN') or subdomain}.{domain}"
        if public not in hosts:
            hosts.append(public)
    return ",".join(hosts)


def module_command(python, module, *args):
    """`-s` drops user site-packages; the app copy is the working directory, so `-m` finds the package."""
    return [str(python), "-s", "-m", module, *map(str, args)]


def check_command(python):
    """Post-install check: imports and both workspaces load from the app copy (offline)."""
    return [str(python), "-s", "-c", IMPORT_CHECK]


def server_command(python, port, state_dir):
    return module_command(python, "semantic_pipeline_v2.workspace_server",
                          "--port", port, "--state-dir", state_dir, "--import-models")


def prepare_command(python, state_dir, hold_out=()):
    extra = [arg for source in hold_out for arg in ("--hold-out", source)]
    return module_command(python, "semantic_pipeline_v2.prepare_demo", "--state-dir", state_dir, *extra)


def hold_outs(environ):
    """Sources the prepare job leaves new for the live ingest part of the demo (comma-separated)."""
    raw = environ.get("DEMO_HOLD_OUT", "MFG-114")
    return [s.strip() for s in raw.split(",") if s.strip()]


def missing_pg_tools(bin_dir):
    bin_dir = Path(bin_dir) if bin_dir else None
    return [t for t in PG_TOOLS if bin_dir is None or not (bin_dir / t).is_file() or not os.access(bin_dir / t, os.X_OK)]


def copy_app(root, target):
    """Refresh the app copy from the repository's visible top-level entries (hidden folders such as caches, venvs and
    the copy itself are skipped). Files only the copy holds, such as downloaded data, are kept."""
    root, target = Path(root).resolve(), Path(target).resolve()
    if target == root or target in root.parents:
        raise ValueError("The app copy must not contain the repository")
    target.mkdir(parents=True, exist_ok=True)
    ignore = shutil.ignore_patterns(*COPY_SKIP)
    for entry in sorted(root.iterdir()):
        if entry.name.startswith(".") or entry.is_symlink():
            continue
        if entry.is_dir():
            shutil.copytree(entry, target / entry.name, ignore=ignore, dirs_exist_ok=True)
        else:
            shutil.copy2(entry, target / entry.name)
    return target


def probe_pg_bin(python):
    """The `pginstall/bin` folder of the pgserver package in the venv (this is where V2_PG_BIN comes from), or None."""
    import subprocess
    try:
        out = subprocess.run([str(python), "-I", "-c", PG_PROBE], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return Path(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else None
