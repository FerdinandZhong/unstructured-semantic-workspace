"""Mocked browser check of FormRows (Output C) with the "Also write to Iceberg" choice, at 1440, 1280 and 980 px.

Starts its own server on a free port with no state, no provider key and no Iceberg settings. Every /api/physical-rows
response is a test double written by the implementer; no CDE or Polaris call can happen.
Run with miniconda python3 (Playwright): `python3 tests_v2/browser_form_rows.py`.
"""
import json
import os
import socket
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
DS = "financial-services"
SRC = f"{DS}:source:cleaned_pdf_1"
REV = "a" * 64


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    assert port not in (8113, 8137, 8173, 8291)
    return port


def cell(column, value):
    return {"column": column, "value": value, "typed": value, "status": "ok", "reason": None, "unit": None,
            "sourceId": SRC, "sourceRevision": REV, "chunkId": "c1", "span": {"start": 0, "end": 9}, "quote": column}


LINK = {"source_id": SRC, "source_revision": REV, "chunk_id": "c1",
        "definition_id": f"{DS}:definition:x/field/STR_Transaction/AmountOriginal", "canonical_definition_id": None,
        "relation": "states_value_of", "stated_value": "500.00", "unit": "SGD", "period": None, "entity_key": "unresolved",
        "entity_keys": [], "interpretation": None, "candidates": [], "scores": {}, "method": "template:STR",
        "model_revision": None, "status": "proposed",
        "notes": {"table": "STR_Transaction", "column": "AmountOriginal", "row": 1, "quote": "q"},
        "span": {"start": 0, "end": 14, "text": "AmountOriginal"}}
CHUNKS = {"chunks": [{"chunk_id": "c1", "source_id": SRC, "source_revision": REV, "start_offset": 0, "end_offset": 14,
                      "page": 0, "text": "AmountOriginal"}]}


def run(base, width):
    posts, errors = [], []
    state = {"status": "none", "job": None}
    ice = {"available": True, "reason": None, "jobs": 1,
           "tables": ["lakehouse.demo_silver.ext_str_transaction", "lakehouse.demo_silver.ext_str_account_entity"],
           "targets": ["Iceberg lakehouse.demo_silver.ext_str_transaction via Cloudera Data Engineering",
                       "Iceberg lakehouse.demo_silver.ext_str_account_entity via Cloudera Data Engineering"]}

    def view(approval=None):
        rows = [] if state["status"] == "none" else [
            {"table": "STR_Transaction", "rowKey": "1", "status": state["status"], "cells": [cell("AmountOriginal", "500.00")]}]
        return {"sourceId": SRC, "table": "STR_Transaction", "writeAvailable": True, "tables": ["STR_Transaction"],
                "rows": rows, "target": "Private PostgreSQL", "fingerprint": "f" * 64,
                "iceberg": {**ice, "rows": len(rows), "cells": 3 * len(rows), "job": state["job"],
                            **({"approval": approval} if approval else {})}}

    def physical(route):
        request = route.request
        if request.method == "POST":
            action, body = request.url.rsplit("/", 1)[1], json.loads(request.post_data)
            posts.append((action, body))
            if action == "preview":
                state["status"] = "previewed"
                return route.fulfill(json=view("a1"))
            if action == "confirm":
                state["status"] = "written"
                if body.get("iceberg"):
                    state["job"] = {"jobName": "v2-str-rows-x", "runId": 77, "status": "submitted",
                                    "message": "One Spark job submitted."}
            if action == "iceberg-status":
                state["job"] = {**state["job"], "status": "completed", "message": "Post-check passed."}
        route.fulfill(json=view())

    with sync_playwright() as playwright, playwright.chromium.launch() as browser:
        page = browser.new_page(viewport={"width": width, "height": 900})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/api/physical-rows**", physical)
        page.route("**/api/evidence-links/export?*",
                   lambda route: route.fulfill(body=json.dumps(LINK) + "\n", content_type="application/x-ndjson"))
        page.route("**/api/chunks?*", lambda route: route.fulfill(json=CHUNKS))
        page.goto(base + f"#{DS}/evidence/cleaned_pdf_1")
        page.reload()
        reading = page.get_by_role("button", name="Reading")
        expect(reading.first).to_be_visible(timeout=20000)
        reading.first.click()
        panel = page.get_by_label("Table rows from this form")
        expect(panel).to_be_visible(timeout=20000)
        expect(panel).to_contain_text("Target: Private PostgreSQL")
        expect(panel).to_contain_text("lakehouse.demo_silver.ext_str_transaction via Cloudera Data Engineering")
        expect(panel.get_by_label("Also write to Iceberg")).to_have_count(0)          # no preview, no approval
        panel.get_by_role("button", name="Preview rows").click()
        choice = panel.get_by_label("Also write to Iceberg")
        expect(choice).not_to_be_checked()                                             # default off
        expect(panel).to_contain_text("1 rows, 3 cells, 1 Spark job.")
        choice.check()
        panel.get_by_role("button", name="Confirm write").click()
        expect(panel).to_contain_text("Iceberg job v2-str-rows-x (run 77): submitted.")
        assert posts[-1] == ("confirm", {"dataset": DS, "sourceId": SRC, "fingerprint": "f" * 64,
                                         "iceberg": True, "approval": "a1"}), posts
        panel.get_by_role("button", name="Check job status").click()
        expect(panel).to_contain_text("completed")
        panel.get_by_role("button", name="Preview rows").click()
        expect(panel.get_by_label("Also write to Iceberg")).not_to_be_checked()      # off again after a confirm
        panel.get_by_role("button", name="Confirm write").click()
        expect(panel).to_contain_text("completed")
        assert "iceberg" not in posts[-1][1], posts[-1]                               # a plain confirm names no Iceberg
        overflow = page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
        assert not overflow, f"horizontal page scroll at {width}px"
        assert not errors, errors
        print(f"FormRows Iceberg choice passed at {width}px.")


if __name__ == "__main__":
    port, state = free_port(), tempfile.mkdtemp(prefix="v2-form-rows-")   # an empty private state of its own
    env = {k: v for k, v in os.environ.items() if k not in ("MISTRAL_API_KEY", "V2_ICEBERG_SETTINGS_DIR", "CDE_TOKEN")}
    env["V2_PG_BIN"] = env.get("V2_PG_BIN", "")
    process = subprocess.Popen([str(ROOT / ".venv-v2/bin/python"), "-B", "-m", "semantic_pipeline_v2.workspace_server",
                                "--port", str(port), "--state-dir", state + "/state"],
                               cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        url = f"http://127.0.0.1:{port}/"
        for _ in range(60):
            try:
                urllib.request.urlopen(url + "pipeline.js", timeout=2)
                break
            except OSError:
                time.sleep(1)
        for width in (1440, 1280, 980):
            run(url, width)
    finally:
        process.terminate()
        process.wait(timeout=60)
        shutil.rmtree(state, ignore_errors=True)
