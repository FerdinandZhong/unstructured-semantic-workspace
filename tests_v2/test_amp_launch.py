"""AMP launch helpers (plan 2026-11-23-v2-zh-amp, A2, AMP-T2): pure functions only; nothing is installed or started."""
import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# The source tree keeps the launchers in amp/cai; the exported repository has them in cai/.
PATH = next(p for p in (ROOT / "amp" / "cai" / "launch.py", ROOT / "cai" / "launch.py") if p.is_file())
spec = importlib.util.spec_from_file_location("amp_launch", PATH)
launch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launch)


def test_injected_python_paths_are_removed_and_extras_added():
    env = launch.clean_env({"PATH": "/bin", "PYTHONPATH": "/x", "PYTHONHOME": "/y", "PYTHONUSERBASE": "/z"},
                           V2_PG_BIN=Path("/pg/bin"), SKIPPED=None)
    assert env == {"PATH": "/bin", "V2_PG_BIN": "/pg/bin"}


def test_port_comes_from_cdsw_app_port():
    assert launch.app_port({"CDSW_APP_PORT": "8090"}) == 8090
    assert launch.app_port({}) == launch.DEFAULT_PORT


@pytest.mark.parametrize("environ, expected", [
    ({}, ""),
    ({"CDSW_DOMAIN": "ml-1234.example.cloudera.site"}, "semantic-workspace.ml-1234.example.cloudera.site"),
    ({"CDSW_DOMAIN": "d.example.org.", "V2_ALLOWED_HOSTS": " a.example.org ,"}, "a.example.org,semantic-workspace.d.example.org"),
    ({"CDSW_DOMAIN": "d.example.org", "SEMANTIC_WORKSPACE_SUBDOMAIN": "sw2"}, "sw2.d.example.org"),
    ({"CDSW_DOMAIN": "d.example.org", "V2_ALLOWED_HOSTS": "semantic-workspace.d.example.org"}, "semantic-workspace.d.example.org"),
])
def test_allowed_hosts(environ, expected):
    assert launch.allowed_hosts(environ) == expected


def test_subdomain_matches_the_manifest():
    manifest = next(p for p in (ROOT / "amp" / "project-metadata.yaml", ROOT / ".project-metadata.yaml") if p.is_file())
    assert f"subdomain: {launch.SUBDOMAIN}\n" in manifest.read_text()


def test_command_lines():
    assert launch.server_command("/v/bin/python", 8090, "/s") == [
        "/v/bin/python", "-s", "-m", "semantic_pipeline_v2.workspace_server",
        "--port", "8090", "--state-dir", "/s", "--import-models"]
    assert launch.prepare_command("/v/bin/python", "/s", ["MFG-114", "x"]) == [
        "/v/bin/python", "-s", "-m", "semantic_pipeline_v2.prepare_demo", "--state-dir", "/s",
        "--hold-out", "MFG-114", "--hold-out", "x"]
    assert launch.check_command("/p")[:3] == ["/p", "-s", "-c"]


def test_hold_outs():
    assert launch.hold_outs({}) == ["MFG-114"]
    assert launch.hold_outs({"DEMO_HOLD_OUT": ""}) == []
    assert launch.hold_outs({"DEMO_HOLD_OUT": "a, b"}) == ["a", "b"]


def test_missing_pg_tools(tmp_path):
    assert launch.missing_pg_tools(None) == list(launch.PG_TOOLS)
    for name in ("initdb", "psql"):
        (tmp_path / name).write_text("")
        os.chmod(tmp_path / name, 0o755)
    assert launch.missing_pg_tools(tmp_path) == ["pg_ctl"]


def test_copy_app_skips_hidden_entries_and_keeps_downloaded_data(tmp_path):
    repo, app = tmp_path / "repo", tmp_path / "repo" / ".semantic-workspace-app"
    (repo / "pkg" / "__pycache__").mkdir(parents=True)
    (repo / "pkg" / "a.py").write_text("1")
    (repo / "pkg" / "__pycache__" / "a.pyc").write_text("x")
    (repo / ".venv-x").mkdir()
    (repo / "top.txt").write_text("t")
    launch.copy_app(repo, app)
    (app / "pkg" / "downloaded.csv").write_text("d")
    (repo / "pkg" / "a.py").write_text("2")
    launch.copy_app(repo, app)
    assert sorted(p.relative_to(app).as_posix() for p in app.rglob("*")) == [
        "pkg", "pkg/a.py", "pkg/downloaded.csv", "top.txt"]
    assert (app / "pkg" / "a.py").read_text() == "2"
    with pytest.raises(ValueError):
        launch.copy_app(repo, repo)
