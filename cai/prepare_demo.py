#!/usr/bin/env python3
"""Cloudera AI job: build the demo state with live Mistral calls (needs MISTRAL_API_KEY). Resumable; about 15 minutes.

Runs `semantic_pipeline_v2.prepare_demo` from the app copy into /home/cdsw/.semantic-workspace-state. Sources named in
DEMO_HOLD_OUT (default MFG-114) stay new for the live ingest part of the demo. Stop the application first: the state
has one lock. The key is never printed.
"""

import os
import subprocess
import sys
from pathlib import Path

try:
    ROOT = Path(__file__).resolve().parents[1]
except NameError:   # Cloudera AI runs job scripts where __file__ is undefined
    ROOT = next((p for p in (Path("/home/cdsw"), Path.cwd()) if (p / "cai" / "launch.py").is_file()), Path("/home/cdsw"))
sys.path.insert(0, str(ROOT / "cai"))
import launch  # noqa: E402


def main():
    if not os.environ.get("MISTRAL_API_KEY", "").strip():
        print("ERROR: MISTRAL_API_KEY is not set. Add it to the project's environment variables and rerun this job.",
              file=sys.stderr)
        return 2
    python = launch.VENV / "bin" / "python"
    if not python.is_file() or not launch.APP_DIR.is_dir():
        print("ERROR: run the setup job (cai/setup_environment.py) first.", file=sys.stderr)
        return 3
    pg_bin = launch.probe_pg_bin(python)
    if launch.missing_pg_tools(pg_bin):
        print("ERROR: the PostgreSQL tools are missing. Rerun the setup job.", file=sys.stderr)
        return 3
    command = launch.prepare_command(python, launch.STATE_DIR, launch.hold_outs(os.environ))
    print("Running:", " ".join(command), flush=True)
    return subprocess.run(command, cwd=launch.APP_DIR,
                          env=launch.clean_env(os.environ, V2_PG_BIN=pg_bin)).returncode


if not os.environ.get("SEMANTIC_WORKSPACE_NO_MAIN"):
    code = main()
    if code:
        sys.exit(code)
