"""The React pages ship as a vendored bundle: served by the allowlist, loaded by the page, and listed in the NOTICE."""
import json
import re
from pathlib import Path

from semantic_pipeline_v2.workspace_server import ASSETS

UI = Path(__file__).resolve().parents[1] / "docs/design/v2-workspace"


def test_bundle_is_served_and_loaded_without_a_cdn():
    page = (UI / "index.html").read_text()
    for name in ("pipeline.js", "pipeline.css"):
        assert name in ASSETS and (UI / name).is_file() and name in page
    assert not re.search(r"""(?:src|href)=["']https?:""", page), "the demo must run with no network access"
    assert "https://" not in (UI / "pipeline.css").read_text()


def test_notice_names_every_bundled_package_with_version_and_licence():
    notice = (UI / "pipeline.NOTICE.txt").read_text()
    lock = json.loads((UI / "app/package-lock.json").read_text())["packages"]
    for path, item in lock.items():
        if path.startswith("node_modules/") and not item.get("dev") and "esbuild" not in path and "/@types/" not in path \
                and not path.endswith(("js-tokens", "loose-envify")):
            name = path.removeprefix("node_modules/")
            assert f"- {name} {item['version']} {item['license']}" in notice, name


def test_navigation_is_the_six_pipeline_steps_in_order():
    code = (UI / "workspace.js").read_text()
    block = code[code.index("const pipelineSteps = ["):code.index("];", code.index("const pipelineSteps = ["))]
    titles = re.findall(r"\['\w+', '([^']+)', '\w+'\]", block)
    assert titles == ["Sources", "Model", "Evidence", "Concepts", "Publish", "Ask"]
