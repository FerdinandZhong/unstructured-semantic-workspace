#!/usr/bin/env python3
"""Cloudera AI job: create the venv, refresh the app copy, fetch the manufacturing tables, check everything. Safe to rerun.

  venv      /home/cdsw/.venv-semantic-workspace  <- requirements-v2.lock + pgserver (uv, else python -m venv + pip)
  app copy  /home/cdsw/.semantic-workspace-app   <- this repository (see cai/launch.py for why a copy)
  tables    demo_data/manufacturing/data/csv     <- scripts/prepare_data.py: pinned Microsoft AdventureWorks (MIT), curl
No provider is called. Exit code 1 on any failure.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

try:
    ROOT = Path(__file__).resolve().parents[1]
except NameError:   # Cloudera AI runs job scripts where __file__ is undefined
    ROOT = next((p for p in (Path("/home/cdsw"), Path.cwd()) if (p / "cai" / "launch.py").is_file()), Path("/home/cdsw"))
sys.path.insert(0, str(ROOT / "cai"))
import launch  # noqa: E402


def run(cmd, **kwargs):
    cmd = [str(c) for c in cmd]
    print("Running:", " ".join(cmd), flush=True)
    return subprocess.run(cmd, env=launch.clean_env(os.environ), **kwargs).returncode == 0


def uv():
    found = shutil.which("uv")
    if not found and run([sys.executable, "-I", "-m", "pip", "install", "--quiet", "uv"]):
        found = shutil.which("uv") or str(Path(sys.executable).with_name("uv"))
    return found if found and Path(found).exists() else ""


def main():
    if sys.version_info[:2] != (3, 12):
        print(f"ERROR: requirements-v2.lock is pinned for Python 3.12; this runtime is "
              f"{sys.version_info[0]}.{sys.version_info[1]}. Select a Python 3.12 runtime.", file=sys.stderr)
        return 1
    tool, venv = uv(), launch.VENV
    python = venv / "bin" / "python"
    if not python.is_file():
        created = run([tool, "venv", "--python", sys.executable, venv] if tool else [sys.executable, "-I", "-m", "venv", venv])
        if not created:
            print("ERROR: could not create the venv", file=sys.stderr)
            return 1
    packages = ["-r", ROOT / "requirements-v2.lock", launch.PGSERVER]
    if not run([tool, "pip", "install", "--python", python, *packages] if tool
               else [python, "-I", "-m", "pip", "install", *packages]):
        print("ERROR: package installation failed", file=sys.stderr)
        return 1
    app = launch.copy_app(ROOT, launch.APP_DIR)
    print(f"App copy refreshed: {app}", flush=True)
    tables = app / "demo_data/manufacturing/data/csv"
    if not any(tables.glob("*.csv")) and not run([python, "-I", "demo_data/manufacturing/scripts/prepare_data.py"], cwd=app):
        print("ERROR: the manufacturing tables could not be prepared (needs curl and access to raw.githubusercontent.com)",
              file=sys.stderr)
        return 1
    pg_bin = launch.probe_pg_bin(python)
    missing = launch.missing_pg_tools(pg_bin)
    if missing:
        print(f"ERROR: PostgreSQL tools missing from the pgserver package ({pg_bin}): {', '.join(missing)}", file=sys.stderr)
        return 1
    print(f"PostgreSQL tools found (V2_PG_BIN is set from the pgserver package at each start): {pg_bin}", flush=True)
    if not run(launch.check_command(python), cwd=app):
        print("ERROR: the post-install import and data check failed", file=sys.stderr)
        return 1
    print("Environment setup complete.", flush=True)
    return 0


# Cloudera AI does not set __name__ == "__main__"; tests set SEMANTIC_WORKSPACE_NO_MAIN=1 to import without running.
if not os.environ.get("SEMANTIC_WORKSPACE_NO_MAIN"):
    code = main()
    if code:
        sys.exit(code)
