#!/usr/bin/env python3
"""Cloudera AI application: start the Semantic Workspace server from the app copy.

Cloudera AI variables read: CDSW_APP_PORT (the port; the server binds 127.0.0.1) and CDSW_DOMAIN (with the
application subdomain it gives the public host added to V2_ALLOWED_HOSTS). Untested on a real workspace.
Without the PostgreSQL tools the server still starts, read-only, without review, evidence or answers.
"""

import os
import subprocess
import sys
from pathlib import Path

try:
    ROOT = Path(__file__).resolve().parents[1]
except NameError:   # Cloudera AI runs application scripts where __file__ is undefined
    ROOT = next((p for p in (Path("/home/cdsw"), Path.cwd()) if (p / "cai" / "launch.py").is_file()), Path("/home/cdsw"))
sys.path.insert(0, str(ROOT / "cai"))
import launch  # noqa: E402


def main():
    python = launch.VENV / "bin" / "python"
    if not python.is_file() or not launch.APP_DIR.is_dir():
        print("ERROR: run the setup job (cai/setup_environment.py) first.", file=sys.stderr)
        return 3
    pg_bin = launch.probe_pg_bin(python)
    command = launch.server_command(python, launch.app_port(os.environ), launch.STATE_DIR)
    if launch.missing_pg_tools(pg_bin):
        print("WARNING: PostgreSQL tools missing; starting without state. Rerun the setup job.", flush=True)
        command = command[:command.index("--state-dir")]
        pg_bin = None
    env = launch.clean_env(os.environ, V2_PG_BIN=pg_bin, V2_ALLOWED_HOSTS=launch.allowed_hosts(os.environ))
    print("Allowed public hosts:", env["V2_ALLOWED_HOSTS"] or "(none)", flush=True)
    print("Running:", " ".join(command), flush=True)
    # subprocess.run, not os.execv: the Cloudera AI engine treats process replacement as a crash.
    return subprocess.run(command, cwd=launch.APP_DIR, env=env).returncode


if not os.environ.get("SEMANTIC_WORKSPACE_NO_MAIN"):
    code = main()
    if code:
        sys.exit(code)
