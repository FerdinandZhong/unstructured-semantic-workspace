"""Start the V2 workspace server on a free port over a private PostgreSQL seeded with synthetic links and a catalog.
Run with the V2 environment: prints the URL, then serves until stopped. Used by browser_catalog_graph.py."""

import sys
import tempfile
from pathlib import Path
from threading import Thread

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from semantic_pipeline_v2.evidence_links import EvidenceLinks
from semantic_pipeline_v2.semantic_store import SemanticStore
from semantic_pipeline_v2.workspace import review_database
from semantic_pipeline_v2.workspace_server import create_server

DATASET = "manufacturing"


def seed(database, workspace):
    store = SemanticStore(database)
    store.import_ossie(DATASET, Path(__file__).resolve().parents[1] / "demo_data/manufacturing/semantics/manufacturing.ossie.yaml")
    knowledge = workspace["knowledge"]
    definition = knowledge["structured"]["fields"][0]["id"]
    chunks = []
    quotes = {o["id"]: o["quote"] for o in knowledge["observations"]}
    for source in dict.fromkeys(m["sourceId"] for m in knowledge["mappings"] if m.get("observationId") in quotes):
        quote = next(quotes[m["observationId"]] for m in knowledge["mappings"]
                     if m["sourceId"] == source and m.get("observationId") in quotes)
        chunk = "chunk-" + source.rsplit(":", 1)[-1]
        chunks.append(chunk)
        EvidenceLinks(database).replace(DATASET, source, [{
            "chunk_id": chunk, "source_id": source, "source_revision": "b" * 64,
            "span": {"start": 0, "end": len(quote), "page": None, "text": quote}, "definition_id": definition,
            "relation": "states_value_of", "stated_value": "9", "unit": "days", "period": None,
            "entity_key": "unresolved", "interpretation": "synthetic", "candidates": [definition], "scores": {},
            "method": "fixture", "model_revision": None, "status": "proposed", "actor": "pipeline", "notes": {}}])
    store.write_catalog(DATASET, "cat-fixture", [{
        "id": "settlement-days", "label": "Settlement days", "definition": "Number of days taken to settle a trade.", "synonyms": [],
        "examples": [], "origin": "document", "type": "measure", "unit": "days", "grain": None,
        "links": [{"definition_id": definition, "predicate": "closeMatch", "status": "proposed", "reason": "synthetic",
                   "support": {"chunks": chunks}},
                  {"definition_id": knowledge["structured"]["fields"][1]["id"], "predicate": "relatedMatch",
                   "status": "proposed", "reason": "synthetic", "support": {"chunks": chunks}}],
        "evidence": {"catalog_version": "cat-fixture", "chunks": chunks}, "status": "proposed", "actor": "pipeline-b"}])


def main():
    from semantic_pipeline_v2.workspace import load_workspace
    with tempfile.TemporaryDirectory() as tmp, review_database(Path(tmp) / "state", Path(sys.argv[1])) as database:
        with create_server(0, database, evidence_dir=Path(tmp) / "evidence", chunk_embedder=None, mapping_embedder=None,
                           interpreter=None) as server:
            if "--seed" in sys.argv:
                seed(database, load_workspace(DATASET))
            print(f"http://127.0.0.1:{server.server_port}", flush=True)
            Thread(target=server.serve_forever, daemon=True).start()
            sys.stdin.read()  # stops when the browser script closes the pipe


main()
