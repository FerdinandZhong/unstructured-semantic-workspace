"""P6: the held-out exam, run once by the controller. Not collected by the suite (no test_ prefix in the file name).

Wiring follows workspace_server: live chunk embedder, cached mapping embedder, live interpreter with the default
candidate variant, template links merged, masked reply. Every registered source whose text is on disk is ingested.
Run: MISTRAL_API_KEY=... V2_PG_BIN=... pytest tests_v2/exam_heldout_run.py -q -s
Replies are cached in OUT so a crashed run resumes instead of asking again."""
import json
import os
from pathlib import Path

from semantic_pipeline_v2.chunk_index import ChunkIndex, configured_embedder, units_from_pages
from semantic_pipeline_v2.embedding_cache import CachedEmbedder, EmbeddingCache
from semantic_pipeline_v2.evidence_links import EntityKeys, EvidenceLinks, build_links
from semantic_pipeline_v2.extracted_fields import mask_extracted
from semantic_pipeline_v2.interpreter import CachedChat, ChatCache, Interpreter, MistralChat
from semantic_pipeline_v2.path1 import Path1Mapper
from semantic_pipeline_v2.physical_mapping import merge_links, template_links
from semantic_pipeline_v2.workspace import load_workspace
from exam_grader import grade
from test_answer_path import EXAM, ROOT, SPACE, SUP, aliases, ask

OUT = ROOT / os.environ.get("V2_EXAM_OUT", "qualification/exam-heldout-2026-11-21")
EXAM_FILE = os.environ.get("V2_EXAM_FILE", "exam_heldout.jsonl")
DOWNLOADS = "~/Downloads/cleaned data/customer-form{}_clean.pdf"
PATHS = {**{f"cleaned_pdf_{n}": DOWNLOADS.format(n) for n in range(1, 6)}, "jade_call": "demo_data/finance/jade_transcript.json"}
SKIP_KINDS = {"workbook", "structured-model", "mapping-reference"}


def test_heldout_exam_once(answer_pg, tmp_path_factory):
    OUT.mkdir(parents=True, exist_ok=True)
    cache = ChatCache(OUT / "chat")
    chat = CachedChat(cache, MistralChat(os.environ["MISTRAL_API_KEY"].strip()), model="mistral-small-latest")
    embedder = configured_embedder(os.environ)
    mapping = CachedEmbedder(EmbeddingCache(OUT / "embedding-cache"), configured_embedder(os.environ), autosave=True)
    world, links, ingested, absent = {}, EvidenceLinks(answer_pg), [], []
    try:
        for dataset in ("financial-services", "manufacturing"):
            workspace = load_workspace(dataset)
            index, keys = ChunkIndex(tmp_path_factory.mktemp("chunks") / dataset, getattr(embedder, "space", None) or SPACE), EntityKeys(workspace)
            for record in workspace["sources"]:
                key = record["id"].split(":")[-1]
                path = PATHS.get(key) or (record.get("citation") or {}).get("path")
                if record.get("kind") in SKIP_KINDS or not path:
                    continue
                text = SUP.source_text(path)
                if text is None:
                    absent.append(record["id"])
                    continue
                index.ingest(scope=dataset, source_id=record["id"], revision=(record.get("citation") or {}).get("sha256") or "0" * 64,
                             units=units_from_pages([{"markdown": text, "index": 0}]), kind="document", embedder=embedder)
                rows = index.read(dataset, record["id"])
                built = build_links(workspace, rows, Path1Mapper(workspace, mapping), Interpreter(chat), keys,
                                    title=record.get("title"), source_kind=record.get("kind"),
                                    artifact=index.artifact(rows[0]["content_hash"]),
                                    variant="two_step")  # the product default; conftest pins hybrid12 for recordings
                links.replace(dataset, record["id"], merge_links(built, template_links(workspace, rows)))
                ingested.append(record["id"])
                cache.save(model="mistral-small-latest")
            world[dataset] = {"workspace": workspace, "index": index, "keys": keys}
        world["links"] = links
        names, results = aliases(world), []
        names["financial-services:source:jade_call"] = names.get("financial-services:source:jade_call", []) + [PATHS["jade_call"]]  # harness alias found missing on 2026-11-22
        for item in (json.loads(line) for line in (EXAM / EXAM_FILE).read_text(encoding="utf-8").splitlines()):
            try:
                reply = mask_extracted(ask(world, answer_pg, chat, item["dataset"], item["question"],
                                           item.get("principal", "admin"), embedder=embedder))
                graded = grade(item["key"], reply, names)
                results.append({"id": item["id"], "dataset": item["dataset"], "kind": item["kind"], "pass": graded["pass"],
                                "failing": graded["failing"], "expected_status": item["key"]["expected_status"],
                                "status": reply["status"], "reason": reply.get("reason")})
            except Exception as error:  # a crash is a failed question, never a skipped one
                results.append({"id": item["id"], "dataset": item["dataset"], "kind": item["kind"], "pass": False,
                                "failing": [f"error:{type(error).__name__}"], "expected_status": item["key"]["expected_status"],
                                "status": "error", "reason": None})
    finally:
        cache.save(model="mistral-small-latest")
    summary = {d: {"passed": sum(r["pass"] for r in results if r["dataset"] == d),
                   "total": sum(r["dataset"] == d for r in results),
                   "refusals_refused": sum(r["status"] == "refused" for r in results
                                           if r["dataset"] == d and r["kind"] == "no_supported_answer"),
                   "refusals_total": sum(r["kind"] == "no_supported_answer" for r in results if r["dataset"] == d)}
               for d in ("financial-services", "manufacturing")}
    report = {"summary": summary, "live_chat_calls": chat.calls, "ingested": ingested, "absent": absent, "results": results}
    (OUT / "results.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps({"summary": summary, "live_chat_calls": chat.calls, "ingested": len(ingested), "absent": absent}, indent=1))
    for r in results:
        print(r["id"], r["kind"], "PASS" if r["pass"] else f"FAIL {r['failing']}", r["status"])
