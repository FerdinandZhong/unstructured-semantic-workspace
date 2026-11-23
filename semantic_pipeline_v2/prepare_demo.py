"""Prepare a V2 state directory before a live demo: ingest the registered sources, then build the catalog.

Every step is a request to the same local server routes the Ingest and Catalog pages use (`/api/ocr`,
`/api/catalog/build/preview` and `/confirm`), so there is no second implementation. The server runs in this
process on a free port. Calls go to the live providers unless the caller passes fakes.

    python -m semantic_pipeline_v2.prepare_demo --state-dir DIR [--dataset D ...] [--hold-out SOURCE ...]

Resumable: a source already stored at its pinned revision is skipped, and so is a catalog built from the
current links. Exit code 1 when anything failed. Output never carries passage text or the key.
"""

import argparse
from contextlib import ExitStack
from hashlib import sha256
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import sys
from threading import Thread
import time

from .originals import checked_original
from .workspace import DATASETS, ROOT, review_database
from . import workspace_server

MANIFEST = "prepared-manifest.json"
ATTEMPTS, DELAYS = 3, (5, 15)   # bounded retries of one driven request
READERS = {"application/pdf": "ocr", "image/png": "ocr", "image/jpeg": "ocr"}


class Counted:
    """Count calls and seconds of a provider object's public methods; attributes pass through."""

    def __init__(self, target, name, stats):
        self._target, self._name, self._stats = target, name, stats

    def __getattr__(self, attr):
        value = getattr(self._target, attr)
        if attr.startswith("_") or not callable(value):
            return value

        def timed(*args, **kwargs):
            start = time.monotonic()
            try:
                return value(*args, **kwargs)
            finally:
                row = self._stats.setdefault(self._name, [0, 0.0])
                row[0] += 1
                row[1] += time.monotonic() - start
        return timed


def reader_mode(record, original):
    """The `mode` the Ingest page sends for this source, or None when no reader exists."""
    mime = original.content_type[0].split(";", 1)[0]
    if record.get("status") == "xfa_render_required":
        return "xfa-fields"
    if mime == "text/plain":
        return "text"
    if mime.startswith(("audio/", "video/")) or mime in READERS:
        return "ocr"
    return None


class Client:
    def __init__(self, server):
        self.port, self.cookie = server.server_port, ""
        _, _, headers = self.call("POST", "/api/session", {"userId": "admin", "recover": True}, include_headers=True)
        self.cookie = "; ".join(v.split(";", 1)[0] for n, v in headers if n == "Set-Cookie")

    def call(self, method, path, body=None, *, include_headers=False):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=1800)
        try:
            conn.request(method, path, json.dumps(body) if body is not None else None, {
                "Origin": f"http://127.0.0.1:{self.port}", "Content-Type": "application/json", "Cookie": self.cookie})
            response = conn.getresponse()
            data = response.read()
            if response.getheader("Content-Type", "").startswith("application/json"):
                data = json.loads(data)
            return (response.status, data, response.getheaders()) if include_headers else (response.status, data)
        finally:
            conn.close()


def scrub(text):
    key = os.environ.get("MISTRAL_API_KEY", "").strip()
    text = str(text).replace(key, "***") if key else str(text)
    return " ".join(text.split())[:160]


def code_hash():
    digest = sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        digest.update(path.name.encode() + path.read_bytes())
    return digest.hexdigest()


def with_retries(client, method, path, body, judge, sleep):
    """Repeat a request on transient trouble. `judge(status, data)` returns None when good, else (retryable, reason)."""
    reason = "no attempt"
    for attempt in range(ATTEMPTS):
        try:
            status, data = client.call(method, path, body)
        except (OSError, ValueError) as error:   # timeout, reset
            status, data = 0, {"error": type(error).__name__}
        verdict = judge(status, data)
        if verdict is None:
            return status, data, None
        retryable, reason = verdict
        if not retryable or attempt == ATTEMPTS - 1:
            break
        sleep(DELAYS[attempt])
    return status, data, reason


def judge_ingest(status, data, kinds=lambda: ()):
    """An unusable model reply (InterpreterError) is stored as a degraded link and is not retried: the same prompt
    answers the same way. Provider trouble (ChatError, no interpreter) is retried, then a failure."""
    if status != 200:
        error = data.get("error", "") if isinstance(data, dict) else ""
        return (status in (0, 408, 429) or status >= 500), f"HTTP {status} {scrub(error)}"
    links, chunks = data.get("evidenceLinks", {}), data.get("chunkIndex", {})
    if data.get("evidenceStatus") != "saved":
        return True, "evidence not saved"
    if chunks.get("status") != "saved" or links.get("status") != "saved":
        return True, f"chunk index {chunks.get('status')}, links {links.get('status')}"
    bad = [k for k in kinds() if k != "InterpreterError"]
    if links.get("degraded") and bad:
        return True, f"{links['degraded']} link(s) degraded (interpreter did not answer) [{', '.join(sorted(set(bad)))}]"
    return None


def prepare(state_dir, pg_bin, *, datasets=None, hold_out=(), only=(), say=print, sleep=time.sleep, **server_options):
    """Prepare `state_dir`. Returns {"failed", "exit", "manifest"}. `server_options` pass to `create_server` (fakes)."""
    started, state_dir = time.monotonic(), Path(state_dir)
    datasets = list(datasets or DATASETS)
    stats, failed = {}, 0
    manifest_path = state_dir / MANIFEST
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, ValueError):
        manifest = {}
    manifest.setdefault("datasets", {})

    def matches(dataset, wanted, source_id):
        key = source_id.split(":source:", 1)[1]
        return any(w in (source_id, key) for w in wanted)

    with ExitStack() as stack:
        database = stack.enter_context(review_database(state_dir, pg_bin))
        workspace_server.import_models(database, say=say)
        workspace_server.load_facts(database, say=say)
        server = stack.enter_context(workspace_server.create_server(
            0, database, evidence_dir=state_dir / "mapped-evidence", **server_options))
        for name in ("ocr", "transcriber", "chunk_embedder", "evidence_labeler", "catalog_chat"):
            if getattr(server, name) is not None:
                setattr(server, name, Counted(getattr(server, name), name, stats))
        if server.interpreter is not None:
            server.interpreter.chat = Counted(server.interpreter.chat, "interpreter", stats)
        if getattr(server.mapping_embedder, "live", None) is not None:
            server.mapping_embedder.live = Counted(server.mapping_embedder.live, "mapping_embedder", stats)
        worker = Thread(target=server.serve_forever)
        worker.start()
        stack.callback(worker.join)
        stack.callback(server.shutdown)
        client = Client(server)
        totals = {"ingested": 0, "skipped": 0, "already": 0, "held_out": 0}

        for dataset in datasets:
            workspace = server.workspaces[dataset]
            entry = manifest["datasets"].setdefault(dataset, {"sources": {}})
            entry.update(skipped={}, failed={}, heldOut=[])
            for record in workspace["sources"]:
                sid, key = record["id"], record["id"].split(":source:", 1)[1]
                if only and not matches(dataset, only, sid):
                    continue
                if matches(dataset, hold_out, sid):
                    say(f"[{dataset}] {key}: held out (stays new for the live part)")
                    entry["heldOut"].append(sid)
                    totals["held_out"] += 1
                    continue
                original = server.originals.get(sid)
                if original is None:
                    entry["skipped"][sid] = "no original file registered (structured model or reference)"
                else:
                    with checked_original(original) as (status, _, _):
                        pass
                    mode = reader_mode(record, original) if status == "available" else None
                    if status != "available":
                        entry["skipped"][sid] = f"file absent or changed ({status})"
                    elif mode is None:
                        entry["skipped"][sid] = f"no reader for {original.content_type[0].split(';')[0]}"
                if sid in entry["skipped"]:
                    say(f"[{dataset}] {key}: skipped, {entry['skipped'][sid]}"
                        .replace("file absent or changed", "file absent or changed"))
                    totals["skipped"] += 1
                    continue
                rows = server.chunk_index.read(dataset, sid)
                links = server.evidence_links.read(dataset, sid)
                if rows and rows[0]["source_revision"] == original.digest and links and all(
                        k["status"] != "degraded" or k["notes"].get("degraded") == "InterpreterError" for k in links):
                    entry["sources"].setdefault(sid, {"revision": original.digest, "chunks": len(rows),
                                                      "links": len(links), "mode": mode, "seconds": None})
                    say(f"[{dataset}] {key}: already ingested at this revision, skipped")
                    totals["already"] += 1
                    continue
                t0 = time.monotonic()
                body = {"dataset": dataset, "sourceId": sid, "mode": mode}
                if mode == "ocr" and not original.content_type[0].startswith(("audio/", "video/")):
                    body["options"] = {"includeImageBase64": True, "extractHeader": False, "extractFooter": False,
                                       "tableFormat": "markdown", "definitionGuided": True}   # Ingest page defaults
                def kinds(dataset=dataset, sid=sid):
                    return [str(k["notes"].get("degraded")) for k in server.evidence_links.read(dataset, sid)
                            if k["status"] == "degraded"]

                status, data, reason = with_retries(client, "POST", "/api/ocr", body,
                                                    lambda s, d: judge_ingest(s, d, kinds), sleep)
                seconds = round(time.monotonic() - t0, 1)
                if reason:
                    failed += 1
                    entry["failed"][sid] = reason
                    say(f"[{dataset}] {key}: FAILED after {seconds}s: {reason}")
                    continue
                el, ci = data["evidenceLinks"], data["chunkIndex"]
                entry["sources"][sid] = {"revision": original.digest, "chunks": ci["rows"], "links": el["rows"],
                                         "selected": el["selected"], "degraded": el["degraded"], "mode": mode, "seconds": seconds}
                totals["ingested"] += 1
                say(f"[{dataset}] {key}: ingested ({mode}) chunks={ci['rows']} links={el['rows']} "
                    f"selected={el['selected']} degraded={el['degraded']} {seconds}s")
                manifest_path.write_text(json.dumps(manifest, indent=1))

            # catalog
            links = server.evidence_links.read(dataset)
            inputs = sorted({(k["source_id"], k["source_revision"]) for k in links})
            inputs = [list(i) for i in inputs]
            held = entry.get("catalog", {})
            if not inputs:
                say(f"[{dataset}] catalog: skipped, nothing ingested")
            elif held.get("inputs") == inputs and server.semantic.latest_catalog_version(dataset) == held.get("version"):
                say(f"[{dataset}] catalog: already built ({held['version']}), skipped")
            else:
                t0 = time.monotonic()
                judge = lambda s, d: None if s == 200 else (s in (0, 502), f"HTTP {s} {scrub(d.get('error', '') if isinstance(d, dict) else '')}")
                _, preview, reason = with_retries(client, "POST", "/api/catalog/build/preview", {"dataset": dataset}, judge, sleep)
                if not reason:
                    _, done, reason = with_retries(client, "POST", "/api/catalog/build/confirm",
                                                   {"dataset": dataset, "version": preview["version"]}, judge, sleep)
                seconds = round(time.monotonic() - t0, 1)
                if reason:
                    failed += 1
                    entry["catalogFailed"] = reason
                    say(f"[{dataset}] catalog: FAILED after {seconds}s: {reason}")
                else:
                    entry.pop("catalogFailed", None)
                    entry["catalog"] = {"version": preview["version"], "inputs": inputs,
                                        "concepts": preview["counts"]["concepts"], "links": preview["counts"]["links"]}
                    say(f"[{dataset}] catalog: built and confirmed {preview['version']} "
                        f"concepts={preview['counts']['concepts']} {seconds}s")
            manifest_path.write_text(json.dumps(manifest, indent=1))

        models = {}
        for dataset in datasets:
            structured = server.workspaces[dataset]["knowledge"]["structured"]
            model = next(s for s in server.workspaces[dataset]["sources"] if s["id"] == structured["modelSource"])
            models[dataset] = sha256((ROOT / model["citation"]["path"]).read_bytes()).hexdigest()

    elapsed = round(time.monotonic() - started, 1)
    complete = failed == 0 and all(
        "catalog" in manifest["datasets"][d] and not manifest["datasets"][d]["heldOut"] for d in datasets)
    manifest.update(preparedAt=time.strftime("%Y-%m-%dT%H:%M:%S%z"), codeHash=code_hash(), models=models,
                    complete=complete, elapsedSeconds=elapsed,
                    calls={k: v[0] for k, v in sorted(stats.items())},
                    callSeconds={k: round(v[1], 1) for k, v in sorted(stats.items())})
    manifest_path.write_text(json.dumps(manifest, indent=1))
    say(f"summary: ingested {totals['ingested']}, already done {totals['already']}, skipped {totals['skipped']}, "
        f"held out {totals['held_out']}, failed {failed}")
    say("live calls: " + (", ".join(f"{k} {v[0]} ({v[1]:.0f}s)" for k, v in sorted(stats.items())) or "none"))
    say(f"elapsed {elapsed}s; manifest {manifest_path}; complete={complete}")
    return {"failed": failed, "exit": 1 if failed else 0, "manifest": manifest}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--state-dir", type=Path, required=True, help="absolute state root outside the repository")
    parser.add_argument("--pg-bin", type=Path, default=os.environ.get("V2_PG_BIN"))
    parser.add_argument("--dataset", action="append", choices=DATASETS)
    parser.add_argument("--hold-out", action="append", default=[], metavar="SOURCE_ID")
    parser.add_argument("--source", action="append", default=[], help="only these sources (full id or key)")
    args = parser.parse_args(argv)
    result = prepare(args.state_dir, args.pg_bin, datasets=args.dataset, hold_out=args.hold_out, only=args.source,
                     say=lambda m: print(m, flush=True))
    sys.exit(result["exit"])


if __name__ == "__main__":
    main()
