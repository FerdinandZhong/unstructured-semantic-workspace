"""Serve the local V2 draft and optional private PostgreSQL review actions."""

import argparse
from contextlib import ExitStack
from hashlib import sha256
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import mimetypes
import os
from pathlib import Path
import re
import secrets
import signal
from threading import RLock
import time
from urllib.parse import parse_qs, quote, urlencode, urlsplit

log = logging.getLogger(__name__)

from . import identifier_mask
from .trino_target import LABEL as TRINO_LABEL, from_env as trino_from_env
from ._pilot_postgres import PilotDatabaseError
from .access import (
    ACCOUNTS, COOKIE, SESSION_SECONDS, demo_accounts, initialize_access, new_session, project_fixtures,
    project_workspace, resolve_session, session_payload,
)
from .originals import BLOCK_SIZE, byte_range, checked_original, load_originals, original_info
from .ocr import MAX_DOCUMENT, MAX_REQUEST, OcrError, configuration, document_input, validated_pages
from .ocr_tables import document_tables, restore_document_tables
from .transcribe import MAX_MEDIA, configuration as transcription_configuration
from .chunk_index import ChunkIndex, configured_embedder, units_from_observations, units_from_pages
from .chunk_index import visible as chunk_visible
from .core import EmbeddingSpace
from .embedding_cache import CachedEmbedder, EmbeddingCache
from .path1 import Path1Mapper
from .extracted_fields import field_values, mask_extracted
from .evidence_labeler import configured_labeler, label_result
from .evidence_links import EntityKeys, EvidenceLinks, build_links, overlay, to_jsonl
from .interpreter import Interpreter, configured_chat
from . import ui_lang
from .physical_mapping import (
    PhysicalRows, StalePreview, build_rows, declared, fingerprint, merge_links, row_values, template_links)
from .iceberg_target import ALLOWED as ICEBERG_ALLOWED, IcebergJobs, IcebergTarget, load as load_iceberg, table_name as iceberg_table
from .mapping_corrections import (
    CorrectionConflict, CorrectionContext, CorrectionRequest, MappingCorrections, overlay_mappings,
)
from .answer_path import answer
from .answer_detail import Details, respond as detail_reply
from .catalog_api import (
    CatalogCorrections, PATCHES, apply_concepts, apply_links, correction_key, graph as build_graph_view,
    scope_concepts, validate_patch,
)
from .semantic_store import SemanticStore
from .publish import EmptyRelease, Releases, build_release, diff, link_corrections
from .talk_to_data import TalkError, TalkRequest, configured_interpretation, respond
from .workspace import (
    DATASETS, EXPORT, ROOT, STAGES, ReviewConflict,
    get_review, list_reviews, load_workspace, record_review, review_database, review_revision,
)


ASSETS = {
    "index.html", "workspace.js", "workspace-fixtures.js", "workspace-data.js", "styles.css",
    "catalog.js", "catalog.css", "ingest.js", "knowledge.js", "review.js", "entity.js",
    "talk-to-data.js", "talk-to-data.css",
    "helios-canvas.js", "helios-canvas.css", "v1-style.css", "v1-graph-canvas.js",
    "v1-graph-canvas.css", "cloudera-logo.png", "cloudera-favicon.ico",
    "pipeline.js", "pipeline.css",   # the built React pages (docs/design/v2-workspace/app)
    # Chinese UI text (plan 2026-11-23-v2-zh-amp): list each new i18n/ dictionary here and in index.html
    "i18n.js", "i18n/zh-CN.pipeline.js", "i18n/zh-CN.server.js", "i18n/zh-CN.shell.js",
}
STATIC = (ROOT / EXPORT).parent
RUNTIME_MARKER = b'<meta name="workspace-runtime" content="local">'
MAX_BODY = 4096
JOB_STATES = {"running": "The Spark job is running in Cloudera Data Engineering.",
              "completed": "The Spark job finished. Its post-check found every confirmed row in Iceberg.",
              "failed": "The Spark job failed or was killed in Cloudera Data Engineering. Inspect its logs there.",
              "unknown": "The job status could not be read from Cloudera Data Engineering."}
MAX_TALK_BODY = 64 * 1024
MAX_APPROVE_BODY = 512 * 1024    # an approve-all lists every item it approves
VISITED_COOKIE = "v2_demo_visited"


def entity_keys_of(server, workspace):
    """One EntityKeys per dataset. `setdefault(id, EntityKeys(...))` built (and parsed the model YAML of) a new one per call."""
    if workspace["id"] not in server.entity_keys:
        server.entity_keys[workspace["id"]] = EntityKeys(workspace)
    return server.entity_keys[workspace["id"]]


class WorkspaceServer(ThreadingHTTPServer):
    daemon_threads = False


class WorkspaceRequest(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(6)
        super().setup()

    def response_headers(self, status, length, content_type, headers=()):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()

    def reply(self, status, body, content_type, headers=()):
        self.response_headers(status, len(body), content_type, headers)
        self.wfile.write(body)

    def reads_identifiers(self):
        """True for an admin session. No reply is sent here; any doubt (no store, no session, an error) means False."""
        try:
            store = getattr(self.server, "review_store", None)
            principal = resolve_session(store, self.session_token()) if store is not None else None
            return bool(principal) and principal.get("role") == "admin"
        except Exception:
            return False

    def masked(self, value):
        """`value` without restricted identifiers. The customer view keeps customer and manager IDs: it is searched by them.
        An admin reads the real values (user decision of 23 November 2026): the admin checks a preview before a write."""
        if self.reads_identifiers():
            return value
        found = identifier_mask.current(self.server, lambda ws: entity_keys_of(self.server, ws))
        keep = ("customer", "manager") if urlsplit(self.path).path.startswith("/api/entity") else ()
        return identifier_mask.mask(value, found, keep)

    def send_json(self, status, value, headers=()):
        self.reply(status, json.dumps(self.masked(value)).encode(), "application/json; charset=utf-8", headers)

    def local_request(self, *, mutating=False):
        host = self.headers.get("Host")
        port = self.server.server_port
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if port == 80:
            allowed.update(("127.0.0.1", "localhost"))
        # Behind an application proxy (Cloudera AI) the browser uses the app's public host. The deployment names it.
        public = {h.strip() for h in os.environ.get("V2_ALLOWED_HOSTS", "").split(",") if h.strip()}
        origins = {f"http://{host}"} | ({f"https://{host}"} if host in public else set())
        origin = self.headers.get("Origin")
        if (host not in allowed | public or self.headers.get("Sec-Fetch-Site") in {"cross-site", "same-site"}
                or (origin is not None and origin not in origins)
                or (mutating and origin is None)):
            self.send_json(403, {"error": "Same-origin loopback request required"})
            return False
        return True

    def session_token(self):
        raw = self.headers.get("Cookie", "")
        occurrences = re.findall(rf"(?:^|;)\s*{COOKIE}(?=\s|=|;|$)", raw)
        if not occurrences:
            return None
        if len(occurrences) != 1:
            return ""
        cookie = SimpleCookie()
        try:
            cookie.load(raw)
        except CookieError:
            return ""
        return cookie[COOKIE].value if COOKIE in cookie else ""

    def principal(self):
        if self.server.review_store is None:
            self.send_json(503, {"error": "Private PostgreSQL access is unavailable"})
            return None
        try:
            principal = resolve_session(self.server.review_store, self.session_token())
        except PilotDatabaseError:
            self.send_json(503, {"error": "Private PostgreSQL access is unavailable"})
            return None
        if principal is None:
            self.send_json(401, {
                "error": "Demo session is missing, replaced or expired",
                "accounts": demo_accounts(),
            })
        return principal

    @staticmethod
    def session_cookies(token):
        attributes = "Path=/; HttpOnly; SameSite=Strict"
        return [
            ("Set-Cookie", f"{COOKIE}={token}; {attributes}; Max-Age={SESSION_SECONDS}"),
            ("Set-Cookie", f"{VISITED_COOKIE}=1; {attributes}; Max-Age=31536000"),
        ]

    def do_GET(self):
        if not self.local_request():
            return
        path = urlsplit(self.path)
        if path.path.startswith(("/api/ask", "/api/connections")):   # step 6 harness (ask_actions.py)
            from . import ask_actions
            return ask_actions.handle(self, "GET")
        if path.path.startswith("/api/entity/"):   # Entity 360 (entity_report.py)
            from . import entity_report
            return entity_report.handle(self, "GET")
        if path.path == "/api/talk-to-data":
            if self.principal() is not None:
                self.send_json(200, {"mistralAvailable": self.server.talk_interpretation.available})
            return
        if path.path in {"/api/session", "/api/workspace", "/api/ocr", "/workspace-data.js", "/workspace-fixtures.js"}:
            principal = self.principal()
            if principal is not None:
                self.scoped_response(path.path, principal)
            return
        if path.path in ("/api/original-info", "/api/original"):
            self.original(path.query, metadata_only=path.path.endswith("-info"))
            return
        if path.path == "/api/mapped-evidence":
            self.mapped_evidence(path.query)
            return
        if path.path == "/api/chunks":
            self.chunks(path.query)
            return
        if path.path == "/api/physical-rows":
            self.physical_rows(None, path.query)
            return
        if path.path in ("/api/catalog", "/api/graph"):
            self.catalog_view(path.query, graph=path.path == "/api/graph")
            return
        if path.path in ("/api/evidence-links", "/api/evidence-links/export"):
            self.evidence_links(path.query, export=path.path.endswith("/export"))
            return
        if path.path in ("/api/publish/history", "/api/publish/release"):
            self.publish_read(path.path.rsplit("/", 1)[1], path.query)
            return
        if path.path == "/api/review":
            try:
                query = parse_qs(path.query, strict_parsing=True, max_num_fields=2)
                if set(query) not in ({"dataset"}, {"dataset", "mappingId"}) or any(len(values) != 1 for values in query.values()):
                    raise ValueError("Require dataset and optional mappingId")
                self.review(query["dataset"][0], query.get("mappingId", [None])[0])
            except ValueError as error:
                self.send_json(400, {"error": str(error)})
            return
        name = "index.html" if path.path == "/" else path.path.removeprefix("/")
        asset = STATIC / name
        if name not in ASSETS or asset.is_symlink() or not asset.is_file():
            self.send_json(404, {"error": "Asset unavailable"})
            return
        payload = asset.read_bytes()
        headers = []
        if name == "index.html":
            if RUNTIME_MARKER not in payload:
                payload = payload.replace(b"</head>", RUNTIME_MARKER + b"</head>", 1)
            visited = re.search(rf"(?:^|;)\s*{VISITED_COOKIE}(?=\s|=|;|$)", self.headers.get("Cookie", ""))
            if self.session_token() is None and not visited and self.server.review_store is not None:
                try:
                    token = new_session(self.server.review_store, "admin")
                    if token:
                        headers.extend(self.session_cookies(token))
                except PilotDatabaseError:
                    pass
        self.reply(200, payload, mimetypes.guess_type(name)[0] or "application/octet-stream", headers)

    def scoped_response(self, path, principal):
        if path == "/api/session":
            try:
                self.send_json(200, session_payload(self.server.review_store, principal))
            except PilotDatabaseError:
                self.send_json(503, {"error": "Private PostgreSQL access is unavailable"})
        elif path == "/api/ocr":
            if principal["role"] != "admin":
                self.send_json(403, {"error": "OCR requires an admin account"})
                return
            provider = self.server.ocr
            audio = self.server.transcriber
            self.send_json(200, {"available": provider.available, "model": provider.model if provider.available else None,
                                 "transcription": {"available": audio.available,
                                                   "model": audio.model if audio.available else None}})
        else:
            try:
                payload = (project_fixtures(self.server.fixtures, principal) if path == "/workspace-fixtures.js"
                           else self.workspace_payload(principal))
            except PilotDatabaseError:
                self.send_json(503, {"error": "Private PostgreSQL access is unavailable"})
                return
            if path == "/api/workspace":
                self.send_json(200, payload)
                return
            binding = "const workspaceDatasets" if path == "/workspace-fixtures.js" else "window.V2_WORKSPACE_DATA"
            source = f"{binding} = {json.dumps(self.masked(payload), ensure_ascii=True)};\n".encode()
            self.reply(200, source, "application/javascript; charset=utf-8",
                       [("Cross-Origin-Resource-Policy", "same-origin")])

    def workspace_payload(self, principal, *, include_corrections=True):
        payload = self.server.payload
        store = self.server.evidence
        if store is not None:
            payload = {
                **payload, "datasets": [store.project(item, principal) for item in payload["datasets"]
                                        if item["id"] in principal["datasets"]]
            }
        scoped = project_workspace(payload, principal)
        for dataset in scoped["datasets"]:
            knowledge = dataset["knowledge"]
            if include_corrections:
                knowledge["mappings"] = self.corrected_mappings(
                    dataset, principal, knowledge["mappings"], knowledge["observations"],
                )
            for key in ("observations", "passages"):
                if key in knowledge:
                    knowledge[key] = mask_extracted(knowledge[key])
        return scoped

    def corrected_mappings(self, workspace, principal, mappings, observations):
        if self.server.corrections is None:
            return mappings
        context = CorrectionContext(workspace, principal, self.server.source_pins)
        latest = self.server.corrections.latest(workspace["id"])
        return overlay_mappings(context, mappings, observations, latest, editable=principal["role"] == "admin")

    def chunks(self, query_string):
        """Chunk rows of one source, under the same gate as mapped evidence."""
        principal = self.principal()
        if principal is None:
            return
        try:
            query = parse_qs(query_string, strict_parsing=True, max_num_fields=2)
            if set(query) != {"dataset", "sourceId"} or any(len(values) != 1 for values in query.values()):
                raise ValueError
        except ValueError:
            self.send_json(400, {"error": "Require dataset and sourceId"})
            return
        dataset, source_id = query["dataset"][0], query["sourceId"][0]
        workspace = self.server.workspaces.get(dataset)
        if workspace is None or not chunk_visible(workspace, principal, source_id):
            self.send_json(403, {"error": "Source access withheld for this account"})
            return
        index = self.server.chunk_index
        self.send_json(200, {"chunks": mask_extracted(index.read(dataset, source_id)) if index is not None else []})

    def evidence_links(self, query_string, *, export):
        """Output A. Same gate and masking as `chunks`; the export covers every source the account may see."""
        principal = self.principal()
        if principal is None:
            return
        try:
            keys = {"dataset"} if export else {"dataset", "sourceId"}
            query = parse_qs(query_string, strict_parsing=True, max_num_fields=2)
            if set(query) != keys or any(len(values) != 1 for values in query.values()):
                raise ValueError
        except ValueError:
            self.send_json(400, {"error": "Require dataset" if export else "Require dataset and sourceId"})
            return
        dataset, source_id = query["dataset"][0], query.get("sourceId", [None])[0]
        workspace = self.server.workspaces.get(dataset)
        if workspace is None or (source_id is not None and not chunk_visible(workspace, principal, source_id)) or (
                source_id is None and not chunk_visible(workspace, principal, "")):
            self.send_json(403, {"error": "Source access withheld for this account"})
            return
        try:
            corrected = self.visible_links(workspace, principal, source_id)
        except PilotDatabaseError:
            self.send_json(503, {"error": "Private PostgreSQL access is unavailable"})
            return
        links = mask_extracted(corrected)
        if export:
            self.reply(200, to_jsonl(links).encode(), "application/x-ndjson; charset=utf-8")
        else:
            self.send_json(200, {"links": links})

    def visible_links(self, workspace, principal, source_id=None):
        """Output A links the principal may read, with human corrections applied. One source, or the dataset."""
        dataset, store = workspace["id"], self.server.evidence_links
        links = store.read(dataset, source_id) if store is not None else []
        links = [k for k in links if chunk_visible(workspace, principal, k["source_id"])]
        corrected = []
        for source in dict.fromkeys(k["source_id"] for k in links):
            saved = self.server.evidence.read(workspace, principal, source) if self.server.evidence else None
            mine = [k for k in links if k["source_id"] == source]
            if saved is not None:
                mappings = self.corrected_mappings(workspace, principal, saved["mappings"], saved["observations"])
                keys = entity_keys_of(self.server, workspace)
                mine = overlay(mine, mappings, saved["observations"], keys.canonical_id)
            corrected += mine
        if self.server.catalog_corrections is not None:
            corrected = apply_links(corrected, self.server.catalog_corrections.latest(dataset))
        return corrected

    def dataset_request(self, query_string):
        """(workspace, principal) of a dataset-scoped read, or None after the error reply."""
        principal = self.principal()
        if principal is None:
            return None
        try:
            query = parse_qs(query_string, strict_parsing=True, max_num_fields=1)
            if set(query) != {"dataset"} or len(query["dataset"]) != 1:
                raise ValueError
        except ValueError:
            self.send_json(400, {"error": "Require dataset"})
            return None
        workspace = self.server.workspaces.get(query["dataset"][0])
        if workspace is None or not chunk_visible(workspace, principal, ""):
            self.send_json(403, {"error": "Dataset access withheld for this account"})
            return None
        return workspace, principal

    def catalog_rows(self, workspace, principal):
        """(version, concepts, links) the principal may read, with human corrections applied. All from the database."""
        dataset, semantic = workspace["id"], self.server.semantic
        version = semantic.latest_catalog_version(dataset)
        raw = semantic.read_catalog(dataset, version) if version else []
        everyone = self.server.evidence_links.read(dataset)
        source = {k["chunk_id"]: k["source_id"] for k in everyone}
        corrections = self.server.catalog_corrections.latest(dataset)
        concepts = scope_concepts(apply_concepts(raw, corrections), source,
                                  lambda s: chunk_visible(workspace, principal, s))
        visible = self.visible_links(workspace, principal)   # read once: each read overlays every source's corrections
        by_chunk = {k["chunk_id"]: k for k in visible}
        for c in concepts:
            c["evidenceDetail"] = [
                {"chunkId": x, "sourceId": source[x], "quote": by_chunk[x]["span"]["text"] if x in by_chunk else ""}
                for x in c["evidence"]["chunks"]]
        return version, concepts, visible

    def catalog_view(self, query_string, *, graph):
        context = self.dataset_request(query_string)
        if context is None:
            return
        workspace, principal = context
        if self.server.semantic is None:
            self.send_json(503, {"error": "Private PostgreSQL access is unavailable"})
            return
        try:
            version, concepts, links = self.catalog_rows(workspace, principal)
            if graph:
                definitions = self.server.semantic.list_definitions(workspace["id"])
                body = build_graph_view(definitions, concepts, links)
                body["note"] = ("Nothing is ingested for this dataset yet." if body["empty"] else
                                None if links or concepts else "Definitions only. No source is ingested yet.")
            elif not concepts and version is None:
                body = {"version": None, "concepts": [], "empty": True}
            else:
                body = {"version": version, "concepts": concepts, "empty": False,
                        "editable": principal["role"] == "admin"}
            self.send_json(200, mask_extracted(body))
        except PilotDatabaseError:
            self.send_json(503, {"error": "Private PostgreSQL access is unavailable"})

    def catalog_correction(self):
        principal = self.principal()
        if principal is None:
            return
        if principal["role"] != "admin":
            self.send_json(403, {"error": "Catalog corrections require an admin account"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= MAX_BODY or self.headers.get("Transfer-Encoding"):
                raise ValueError("Require a bounded JSON catalog correction")
            if self.headers.get_content_type() != "application/json":
                raise ValueError("Require application/json")
            body = json.loads(self.rfile.read(size))
            kind = body.get("kind") if isinstance(body, dict) else None
            if kind not in PATCHES or body.get("dataset") not in self.server.workspaces:
                raise ValueError("Require dataset and kind")
            fields = {"concept": {"conceptId"}, "concept_link": {"conceptId", "definitionId"},
                      "evidence_link": {"chunkId", "definitionId"}}[kind]
            if set(body) != {"dataset", "kind", "patch"} | fields:
                raise ValueError("Require " + ", ".join(sorted(fields)))
            dataset = body["dataset"]
            workspace = self.server.workspaces[dataset]
            if not chunk_visible(workspace, principal, ""):
                self.send_json(403, {"error": "Dataset access withheld for this account"})
                return
            patch = validate_patch(kind, body["patch"])
            key = correction_key(kind, body)
            version, concepts, links = self.catalog_rows(workspace, principal)
            if kind == "evidence_link":
                everyone = {(k["chunk_id"], k["definition_id"] or "") for k in self.server.evidence_links.read(dataset)}
                if (body["chunkId"], body["definitionId"] or "") not in everyone:
                    raise ValueError("Evidence link not found")
                if not any(k["chunk_id"] == body["chunkId"] for k in links):
                    self.send_json(403, {"error": "Source access withheld for this account"})
                    return
                target = patch.get("definitionId")
                structured = workspace["knowledge"]["structured"]
                if target and target not in {i["id"] for i in structured["fields"] + structured["metrics"]}:
                    raise ValueError("Definition is unavailable in this dataset")
            else:
                found = next((c for c in concepts if c["id"] == body["conceptId"]), None)
                if found is None or (kind == "concept_link" and not any(
                        k["definition_id"] == body["definitionId"] for k in found["links"])):
                    raise ValueError("Concept or link not found")
            with self.server.evidence_write_lock:
                revision = self.server.catalog_corrections.record(dataset, kind, key, patch, principal["id"])
            self.send_json(200, {"revision": revision})
        except PilotDatabaseError:
            self.send_json(503, {"error": "Private PostgreSQL access is unavailable"})
        except (UnicodeError, ValueError, KeyError, AttributeError) as error:
            self.send_json(400, {"error": str(error) or "Invalid catalog correction"})

    def approve_all(self):
        """Approve the listed evidence links or concepts in one transaction: {dataset, kind, items}. Each item passes the
        checks of a single correction; one unknown or withheld item refuses the whole request. Rejected and already
        approved items are skipped, so a second click writes nothing. The rows are the same correction rows as one
        approval (patch {status: corrected}), so they overlay a rebuilt catalog the same way."""
        principal = self.principal()
        if principal is None:
            return
        if principal["role"] != "admin":
            self.send_json(403, {"error": "Approving requires an admin account"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= MAX_APPROVE_BODY or self.headers.get("Transfer-Encoding") \
                    or self.headers.get_content_type() != "application/json":
                raise ValueError("Require a bounded JSON approval")
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict) or set(body) != {"dataset", "kind", "items"} \
                    or body["kind"] not in ("evidence_link", "concept") or body["dataset"] not in self.server.workspaces \
                    or not isinstance(body["items"], list) or not 0 < len(body["items"]) <= 5000:
                raise ValueError("Require dataset, kind (evidence_link or concept) and a list of items")
            dataset, kind = body["dataset"], body["kind"]
            workspace = self.server.workspaces[dataset]
            if dataset not in principal["datasets"] or not chunk_visible(workspace, principal, ""):
                self.send_json(403, {"error": "Dataset access withheld for this account"})
                return
            _, concepts, links = self.catalog_rows(workspace, principal)
            if kind == "evidence_link":
                if not all(isinstance(i, dict) and set(i) == {"chunkId", "definitionId"} for i in body["items"]):
                    raise ValueError("Require chunkId and definitionId on each item")
                keys = [correction_key(kind, i) for i in body["items"]]
                everyone = {json.dumps([k["chunk_id"], k["definition_id"] or ""]) for k in self.server.evidence_links.read(dataset)}
                if any(key not in everyone for key in keys):
                    raise ValueError("Evidence link not found")
                status = {json.dumps([k["chunk_id"], (k.get("notes") or {}).get("originalDefinitionId") or k["definition_id"] or ""]):
                          k["status"] for k in links}
            else:
                if not all(isinstance(i, str) for i in body["items"]):
                    raise ValueError("Require concept ids")
                keys = body["items"]
                if any(key not in {c["id"] for c in concepts} for key in keys):
                    raise ValueError("Concept not found")
                status = {c["id"]: c["status"] for c in concepts}
            if any(key not in status for key in keys):
                self.send_json(403, {"error": "Source access withheld for this account"})
                return
            keys = list(dict.fromkeys(keys))
            todo = [key for key in keys if status[key] not in ("rejected", "corrected")]
            with self.server.evidence_write_lock:
                revisions = self.server.catalog_corrections.record_many(dataset, kind, todo, {"status": "corrected"},
                                                                        principal["id"])
            self.send_json(200, {"approved": len(todo), "revisions": revisions, "skipped": {
                "rejected": sum(status[k] == "rejected" for k in keys), "approved": sum(status[k] == "corrected" for k in keys)}})
        except PilotDatabaseError:
            self.send_json(503, {"error": "Private PostgreSQL access is unavailable"})
        except (UnicodeError, ValueError, KeyError, AttributeError, TypeError) as error:
            self.send_json(400, {"error": str(error) or "Invalid approval"})

    def catalog_build(self, action):
        """Admin build of the concept catalog from the stored evidence links. Preview computes and holds, confirm writes
        exactly the held version. ponytail: the preview runs in the request thread; a job queue if it outgrows a timeout."""
        principal = self.principal()
        if principal is None:
            return
        if principal["role"] != "admin":
            self.send_json(403, {"error": "Building the catalog requires an admin account"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= MAX_BODY or self.headers.get_content_type() != "application/json":
                raise ValueError
            body = json.loads(self.rfile.read(size))
            expected = {"dataset"} | ({"version"} if action == "confirm" else set())
            if not isinstance(body, dict) or set(body) != expected or not all(isinstance(v, str) for v in body.values()):
                raise ValueError
        except (ValueError, UnicodeError):
            self.send_json(400, {"error": "Require dataset" + (" and version" if action == "confirm" else "")})
            return
        dataset, server = body["dataset"], self.server
        workspace = server.workspaces.get(dataset)
        if workspace is None or not chunk_visible(workspace, principal, ""):
            self.send_json(403, {"error": "Dataset access withheld for this account"})
            return
        if server.catalog_chat is None:
            self.send_json(503, {"error": "No chat client is configured (set MISTRAL_API_KEY); the catalog was not built"})
            return
        if server.semantic is None or server.chunk_index is None:
            self.send_json(503, {"error": "Private PostgreSQL access is unavailable"})
            return
        try:
            links = self.visible_links(workspace, principal)
            if not links:
                self.send_json(409, {"error": "Nothing is ingested for this dataset yet. Ingest a source first."})
                return
            fingerprint = sha256(json.dumps(links, sort_keys=True, default=str).encode()).hexdigest()
            if action == "confirm":
                held = server.catalog_previews.get(dataset)
                if held is None or held["version"] != body["version"]:
                    self.send_json(409, {"error": "Preview the catalog again before confirming"})
                    return
                if held["fingerprint"] != fingerprint:
                    server.catalog_previews.pop(dataset, None)
                    self.send_json(409, {"error": "Evidence links changed since the preview. Preview again."})
                    return
                with server.evidence_write_lock:
                    written = server.semantic.write_catalog(dataset, held["version"], held["concepts"])
                server.catalog_previews.pop(dataset, None)
                self.send_json(200, {"version": held["version"], "written": written})
                return
            from . import concept_catalog as cc
            chunks = []
            for source in dict.fromkeys(k["source_id"] for k in links):
                chunks += [{"chunk_id": r["chunk_id"], "text": mask_extracted(r["text"]), "title": source, "source": source}
                           for r in server.chunk_index.read(dataset, source)]
            known = {c["chunk_id"] for c in chunks}
            records = [k for k in links if k["chunk_id"] in known]
            confirmed = server.semantic.latest_catalog_version(dataset)   # new sources only: incremental update
            previous = server.semantic.read_catalog(dataset, confirmed) if confirmed else None
            catalog = cc.build_from_links(dataset, chunks, records, server.catalog_chat, server.chunk_embedder,
                                          previous=previous)
        except PilotDatabaseError:
            self.send_json(503, {"error": "Private PostgreSQL access is unavailable"})
            return
        except Exception as error:  # the provider, a recording or a prompt failed: nothing was written
            log.warning("catalog build failed: %s", type(error).__name__)
            self.send_json(502, {"error": f"The catalog could not be built ({type(error).__name__}). Nothing was written."})
            return
        concepts = catalog["concepts"]
        server.catalog_previews[dataset] = {"version": catalog["version"], "fingerprint": fingerprint, "concepts": concepts}
        by_status = {}
        for c in concepts:
            by_status[c["status"]] = by_status.get(c["status"], 0) + 1
        self.send_json(200, mask_extracted({
            "dataset": dataset, "version": catalog["version"], "incremental": catalog["stats"].get("incremental"),
            "counts": {"concepts": len(concepts), "links": sum(len(c["links"]) for c in concepts), "byStatus": by_status},
            "concepts": [{"id": c["id"], "label": c["label"], "origin": c["origin"], "status": c["status"],
                          "evidence": len(c["evidence"]["chunks"]),
                          "links": [{"definitionId": k["definition_id"], "predicate": k["predicate"], "status": k["status"]}
                                    for k in c["links"]]} for c in concepts]}))

    def readable_releases(self, workspace, principal):
        """Releases of the dataset, newest first, whose every source the principal may read."""
        return [r for r in self.server.releases.history(workspace["id"])
                if all(chunk_visible(workspace, principal, s) for s in r["sourceIds"])]

    def publish_read(self, what, query_string):
        """History and download of releases. A release with content from a source this account cannot read is hidden."""
        if what == "history":
            context = self.dataset_request(query_string)
        else:
            context = None
            try:
                query = parse_qs(query_string, strict_parsing=True, max_num_fields=2)
                if set(query) != {"dataset", "version"} or any(len(v) != 1 for v in query.values()) \
                        or not query["version"][0].isdigit():
                    raise ValueError
            except ValueError:
                self.send_json(400, {"error": "Require dataset and version"})
                return
            context = self.dataset_request(urlencode({"dataset": query["dataset"][0]}))
        if context is None:
            return
        workspace, principal = context
        if self.server.releases is None:
            self.send_json(503, {"error": "Private PostgreSQL access is unavailable"})
            return
        try:
            readable = self.readable_releases(workspace, principal)
            if what == "history":
                self.send_json(200, {"releases": [{k: v for k, v in r.items() if k != "sourceIds"} for r in readable]})
                return
            number = int(query["version"][0])
            known = any(r["version"] == number for r in self.server.releases.history(workspace["id"]))
            text = self.server.releases.document(workspace["id"], number) if known else None
            if text is None:
                self.send_json(404, {"error": "Release not found"})
            elif not any(r["version"] == number for r in readable):
                self.send_json(403, {"error": "Source access withheld for this account"})
            else:
                self.reply(200, text.encode(), "application/yaml; charset=utf-8")
        except PilotDatabaseError:
            self.send_json(503, {"error": "Private PostgreSQL access is unavailable"})

    def publish(self, action):
        """Admin publish of the approved catalog and mapping corrections as a versioned Ossie export. Preview computes the
        release and its fingerprint (the content SHA-256); confirm recomputes it and writes only if it still matches."""
        principal = self.principal()
        if principal is None:
            return
        if principal["role"] != "admin":
            self.send_json(403, {"error": "Publishing requires an admin account"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= MAX_BODY or self.headers.get_content_type() != "application/json":
                raise ValueError
            body = json.loads(self.rfile.read(size))
            expected = {"dataset"} | ({"fingerprint"} if action == "confirm" else set())
            if not isinstance(body, dict) or set(body) != expected or not all(isinstance(v, str) for v in body.values()):
                raise ValueError
        except (ValueError, UnicodeError):
            self.send_json(400, {"error": "Require dataset" + (" and fingerprint" if action == "confirm" else "")})
            return
        server = self.server
        workspace = server.workspaces.get(body["dataset"])
        if workspace is None or not chunk_visible(workspace, principal, ""):
            self.send_json(403, {"error": "Dataset access withheld for this account"})
            return
        if server.releases is None or server.semantic is None or server.evidence_links is None:
            self.send_json(503, {"error": "Private PostgreSQL access is unavailable"})
            return
        dataset = workspace["id"]
        try:
            with server.evidence_write_lock:
                version, concepts, _ = self.catalog_rows(workspace, principal)
                source = {k["chunk_id"]: (k["source_id"], k["source_revision"]) for k in server.evidence_links.read(dataset)}
                saved = {a: r for a, r in server.corrections.latest(dataset).items()
                         if chunk_visible(workspace, principal, r["sourceId"])}
                base = server.semantic.export_ossie(dataset).decode()
                rows = link_corrections(server.catalog_corrections.latest(dataset), source,
                                        lambda s: chunk_visible(workspace, principal, s))
                release = build_release(dataset, base, version, concepts, source, saved, rows)
                readable = self.readable_releases(workspace, principal)
                latest = server.releases.history(dataset)
                if action == "confirm":
                    if release["errors"]:
                        self.send_json(422, {"error": "The release fails the Ossie lint. Nothing was written.",
                                             "errors": release["errors"]})
                    elif release["sha256"] != body["fingerprint"]:
                        self.send_json(409, {"error": "The catalog or corrections changed since the preview. Preview again."})
                    else:
                        number, written = server.releases.add(dataset, release, principal["id"])
                        self.send_json(200, {
                            "dataset": dataset, "version": number, "written": written, "contentSha256": release["sha256"],
                            "message": f"Published as version {number}." if written else
                            f"Unchanged: version {number} already has this content. Nothing was written."})
                    return
                before = server.releases.document(dataset, readable[0]["version"]) if readable else None
                unchanged = bool(latest) and latest[0]["contentSha256"] == release["sha256"]
                self.send_json(200, {
                    "dataset": dataset, "catalogVersion": version, "modelSha256": release["modelSha256"],
                    "fingerprint": release["sha256"], "counts": release["counts"], "unchanged": unchanged,
                    "evidenceLinkCorrections": mask_extracted(rows),
                    "nextVersion": (latest[0]["version"] if unchanged else (latest[0]["version"] + 1 if latest else 1)),
                    "changes": diff(before, release["text"]) if release["text"] else None,
                    "lint": {"ok": not release["errors"], "errors": release["errors"]}})
        except EmptyRelease as error:
            self.send_json(422, {"error": str(error)})
        except ValueError as error:   # the model is not imported
            self.send_json(409, {"error": str(error)})
        except PilotDatabaseError:
            self.send_json(503, {"error": "Private PostgreSQL access is unavailable"})

    def link_evidence(self, workspace, source_id, result):
        """Interpret each chunk of a saved source and store its links. Failure never blocks the UI payload."""
        store, index = self.server.evidence_links, self.server.chunk_index
        if store is None or index is None:
            result["evidenceLinks"] = {"status": "unavailable", "rows": 0}
            return
        try:
            rows = index.read(workspace["id"], source_id)
            record = next(item for item in workspace["sources"] if item["id"] == source_id)
            keys = entity_keys_of(self.server, workspace)
            links = build_links(
                workspace, rows, Path1Mapper(workspace, self.server.mapping_embedder), self.server.interpreter, keys,
                title=record.get("title"), source_kind=record.get("kind"),
                artifact=index.artifact(rows[0]["content_hash"]) if rows else None)
            links = merge_links(links, template_links(workspace, rows))  # Output C: exact form-field links
            with self.server.evidence_write_lock:
                store.replace(workspace["id"], source_id, links)
                for link in links:
                    if link["definition_id"]:
                        index.add_labels(link["chunk_id"], [link["definition_id"]])
            result["evidenceLinks"] = {
                "status": "saved", "rows": len(links), "selected": sum(bool(k["definition_id"]) for k in links),
                "degraded": sum(k["status"] == "degraded" for k in links),
                "notProcessed": sum(k["status"] == "not_processed" for k in links),
                "model": links[0]["model_revision"] if links else None}
        except (ValueError, OSError, RuntimeError, KeyError, StopIteration):
            result["evidenceLinks"] = {"status": "failed", "rows": 0}

    def physical_rows(self, action, query_string=None):
        """Output C. TEMPLATE-BOUND physical mapping: rows of a predefined table from a templated form.
        GET reads the stored rows; POST preview stores previewed rows; POST confirm (admin) writes them once."""
        principal = self.principal()
        if principal is None:
            return
        try:
            if action is None:
                query = parse_qs(query_string, strict_parsing=True, max_num_fields=2)
                if set(query) != {"dataset", "sourceId"} or any(len(values) != 1 for values in query.values()):
                    raise ValueError
                body = {key: values[0] for key, values in query.items()}
            else:
                if principal["role"] != "admin":
                    self.send_json(403, {"error": "Writing rows requires an admin account"})
                    return
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= MAX_BODY or self.headers.get_content_type() != "application/json":
                    raise ValueError
                body = json.loads(self.rfile.read(size))
                extra = {"fingerprint", "iceberg", "approval"} if action == "confirm" else set()
                if not isinstance(body, dict) or not {"dataset", "sourceId"} <= set(body) <= {"dataset", "sourceId"} | extra:
                    raise ValueError
                if not all(isinstance(body.get(k, ""), str) for k in ("fingerprint", "approval")) or \
                        not isinstance(body.get("iceberg", False), bool):
                    raise ValueError
            dataset, source_id = body["dataset"], body["sourceId"]
            if not isinstance(dataset, str) or not isinstance(source_id, str):
                raise ValueError
        except (ValueError, UnicodeError):
            self.send_json(400, {"error": "Require dataset and sourceId"})
            return
        workspace = self.server.workspaces.get(dataset)
        if workspace is None or not chunk_visible(workspace, principal, source_id):
            self.send_json(403, {"error": "Source access withheld for this account"})
            return
        store, links = self.server.physical_rows, self.server.evidence_links
        if store is None or links is None:
            self.send_json(503, {"error": "Private PostgreSQL access is unavailable"})
            return
        ice = self.server.iceberg() if self.server.iceberg else None   # settings are read in place, at call time
        binding = ice.binding() if ice else ""
        trino = trino_from_env(os.environ)        # with the Trino route a confirm writes through Trino; no Spark job is submitted
        wants_ice = trino is None and action == "confirm" and body.get("iceberg") is True    # default off
        lakehouse = None
        if wants_ice:
            approval = self.server.iceberg_approvals.pop(body.get("approval", ""), None)   # one use
            if ice is None or ice.problem():
                self.send_json(409, {"error": "The Iceberg target is not available: "
                                              + (ice.problem() if ice else "not configured")})
                return
            if approval is None or approval["expires"] < time.time() or approval["bound"] != (
                    dataset, source_id, principal["id"], body.get("fingerprint")):
                self.send_json(409, {"error": "This Iceberg approval is unknown, used or expired. Preview the rows again."})
                return
        try:
            with self.server.evidence_write_lock:
                found = self.visible_links(workspace, principal, source_id)  # human corrections applied
                offered = bool(build_rows(workspace, found))
                rows = store.read(dataset, source_id)
                if action == "confirm" and not rows and not offered:
                    self.send_json(404, {"error": "This document type has no predefined table"})
                    return
                if action == "confirm" and not any(r["status"] in ("previewed", "written") for r in rows):
                    self.send_json(409, {"error": "Preview the rows before confirming"})
                    return
                if action == "confirm" and "fingerprint" in body and fingerprint(rows, binding) != body["fingerprint"]:
                    self.send_json(409, {"error": "The rows or the target settings changed after the preview. "
                                                  "Preview the rows again."})
                    return
                if action == "preview":
                    rows = store.preview(workspace, source_id, found, principal["id"])
                elif action == "confirm":
                    rows = store.confirm(workspace, source_id, principal["id"], links=found)
            jobs = self.server.iceberg_jobs
            if trino is not None and action == "confirm":   # after the local write; a failure is reported, never hidden
                generator = ice if ice is not None and ice.problem() is None else IcebergTarget.generator(ice)
                _, _, payload = generator.build(workspace, rows, source_id=source_id, actor=principal["id"],
                                                fingerprint=fingerprint(rows, binding))
                lakehouse = trino.write(payload)
            if wants_ice:   # after the PostgreSQL write; a failure here is reported, never rolled back or faked
                script, spec, _ = ice.build(workspace, rows, source_id=source_id, actor=principal["id"],
                                            fingerprint=body["fingerprint"])
                jobs.save(dataset, source_id, body["fingerprint"], principal["id"], ice.submit(script, spec))
            elif action == "iceberg-status":
                job = jobs.latest(dataset, source_id)
                if job and job.get("runId") and ice is not None and ice.problem() is None:
                    state = ice.status(job["runId"])
                    jobs.save(dataset, source_id, job["fingerprint"], job["actor"],
                              {**job, "status": state, "message": JOB_STATES[state]})
        except StalePreview as error:
            self.send_json(409, {"error": str(error)})
            return
        except PilotDatabaseError:
            self.send_json(503, {"error": "Private PostgreSQL access is unavailable"})
            return
        order = {t["name"]: n for n, t in enumerate(declared(workspace))}    # rows in the model's table order
        rows.sort(key=lambda r: (order.get(r["table"], len(order)), int(r["rowKey"])))
        table = rows[0]["table"] if rows else next(iter(order), None) if offered else None
        digest = fingerprint(rows, binding)
        iceberg = None
        if trino is not None:
            names = [f"{ICEBERG_ALLOWED[0]}.{ICEBERG_ALLOWED[1]}.{iceberg_table(t['name'])}" for t in declared(workspace)]
            lakehouse = {"tables": names, "result": lakehouse}
        elif ice is not None:
            kept = [r for r in rows if r["status"] in ("previewed", "written")]
            iceberg = {**ice.info(workspace), "rows": len(kept), "cells": sum(len(row_values(workspace, r)) for r in kept),
                       "job": self.server.iceberg_jobs.latest(dataset, source_id) if self.server.iceberg_jobs else None}
            if action == "preview" and iceberg["available"] and kept:
                now = time.time()
                for key in [k for k, v in self.server.iceberg_approvals.items() if v["expires"] < now]:
                    del self.server.iceberg_approvals[key]
                iceberg["approval"] = secrets.token_hex(16)   # hex: never mistaken for an NRIC by the response mask
                self.server.iceberg_approvals[iceberg["approval"]] = {
                    "bound": (dataset, source_id, principal["id"], digest), "expires": now + 600}
        self.send_json(200, mask_extracted({"sourceId": source_id, "table": table, "writeAvailable": offered or bool(rows),
                                            "tables": list(dict.fromkeys(r["table"] for r in rows)), "rows": rows,
                                            "target": TRINO_LABEL if trino is not None else "Private PostgreSQL",
                                            "fingerprint": digest, "iceberg": iceberg, "lakehouse": lakehouse}))

    def index_chunks(self, workspace, source_id, revision, result, kind, mapped):
        """Write chunk rows after the evidence save. Failure never blocks the UI payload."""
        index = self.server.chunk_index
        if index is None:
            return
        units = (units_from_observations(result["evidence"]["observations"]) if kind == "transcript"
                 else units_from_pages(result["pages"]))
        quotes = {o["id"]: o["quote"] for o in mapped["observations"]}
        labels = [m["agentLabel"] for m in mapped["mappings"] if "agentLabel" in m]
        labels += [{"definitionId": m["path1"]["selected"], "quote": quotes[m["observationId"]]}
                   for m in mapped["mappings"] if m.get("path1", {}).get("selected")]
        try:
            with self.server.evidence_write_lock:
                status = index.ingest(scope=workspace["id"], source_id=source_id, revision=revision, units=units,
                                      kind="document", embedder=self.server.chunk_embedder, labels=labels)
            result["chunkIndex"] = {k: status[k] for k in ("status", "rows", "embedding")}
            self.link_evidence(workspace, source_id, result)
        except (ValueError, OSError, RuntimeError):
            result["chunkIndex"] = {"status": "failed", "rows": 0}

    def mapped_evidence(self, query_string):
        principal = self.principal()
        if principal is None:
            return
        try:
            query = parse_qs(query_string, strict_parsing=True, max_num_fields=2)
            if set(query) != {"dataset", "sourceId"} or any(len(values) != 1 for values in query.values()):
                raise ValueError
        except ValueError:
            self.send_json(400, {"error": "Require dataset and sourceId"})
            return
        dataset, source_id = query["dataset"][0], query["sourceId"][0]
        if dataset not in principal["datasets"] or source_id in principal["withheld"]:
            self.send_json(403, {"error": "Source access withheld for this account"})
            return
        workspace = self.server.workspaces.get(dataset)
        if workspace is None or not any(item["id"] == source_id for item in workspace["sources"]):
            self.send_json(404, {"error": "Evidence source is unavailable in this dataset"})
            return
        store = self.server.evidence
        evidence = store.read(workspace, principal, source_id) if store is not None else None
        original = self.server.originals.get(source_id)
        if evidence is not None and original is not None:
            from .legacy_fields import recover_pdf_fields, restore_table_positions
            if evidence.get("fieldStatus") == "unavailable":
                evidence = recover_pdf_fields(evidence, original)
            if any(table["source"] == "stored_observations" for table in evidence.get("tables", ())):
                evidence = restore_table_positions(evidence, original)
                evidence.update(restore_document_tables(evidence["observations"], retained_tables=evidence["tables"]))
        if evidence is not None:
            try:
                evidence = {**evidence, "mappings": self.corrected_mappings(
                    workspace, principal, evidence["mappings"], evidence["observations"],
                )}
            except PilotDatabaseError:
                self.send_json(503, {"error": "Private PostgreSQL access is unavailable"})
                return
        self.send_json(200, {"evidence": mask_extracted(evidence)})

    def original(self, query_string, *, metadata_only):
        principal = self.principal()
        if principal is None:
            return
        try:
            query = parse_qs(query_string, strict_parsing=True, max_num_fields=2)
            if set(query) != {"dataset", "sourceId"} or any(len(values) != 1 for values in query.values()):
                raise ValueError("Require dataset and sourceId")
        except ValueError:
            self.send_json(400, {"error": "Require dataset and sourceId"})
            return
        dataset, source_id = query["dataset"][0], query["sourceId"][0]
        if dataset not in principal["datasets"] or source_id in principal["withheld"]:
            self.send_json(403, {"error": "Source access withheld for this account"})
            return
        workspace = self.server.workspaces.get(dataset)
        if workspace is None or source_id not in {entry["id"] for entry in workspace["sources"]}:
            self.send_json(404, {"error": "Source is unavailable in this dataset"})
            return
        original = self.server.originals.get(source_id)
        with checked_original(original, retain=not metadata_only) as (status, size, stream):
            detail = original_info(dataset, source_id, original, status, size)
            if metadata_only:
                self.send_json(200, detail)
            elif status != "available":
                error_code = {"changed": 409, "missing": 404, "unsupported": 404}[status]
                self.send_json(error_code, {"error": "Original file is unavailable", **detail})
            else:
                self.send_original(original, size, stream)

    def send_original(self, original, size, stream):
        selected_range = self.headers.get("Range")
        content_type, preview = original.content_type
        filename = original.relative.name
        ascii_name = re.sub(r"[^ -~]|[\"\\]", "_", filename)
        disposition = "inline" if preview else "attachment"
        headers = [
            ("Accept-Ranges", "bytes"),
            ("Content-Disposition", f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}"),
            ("Cross-Origin-Resource-Policy", "same-origin"),
            ("Content-Security-Policy", "frame-ancestors 'self'"),
        ]
        first, length = 0, size
        status = 200
        if selected_range is not None:
            try:
                first, last = byte_range(selected_range, size)
            except ValueError:
                headers.append(("Content-Range", f"bytes */{size}"))
                self.response_headers(416, 0, content_type, headers)
                return
            status, length = 206, last - first + 1
            headers.append(("Content-Range", f"bytes {first}-{last}/{size}"))
        self.response_headers(status, length, content_type, headers)
        stream.seek(first)
        try:
            while length:
                block = stream.read(min(BLOCK_SIZE, length))
                self.wfile.write(block)
                length -= len(block)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            # A browser can abandon a media range during a seek.
            return

    def do_POST(self):
        token = ui_lang.use(self.headers.get(ui_lang.HEADER))    # the UI language: read here only (plan D3, D4)
        try:
            self._post()
        finally:
            ui_lang.reset(token)

    def _post(self):
        if not self.local_request(mutating=True):
            return
        if self.path.startswith(("/api/ask", "/api/connections")):   # step 6 harness (ask_actions.py)
            from . import ask_actions
            return ask_actions.handle(self, "POST")
        if self.path.startswith("/api/entity/"):   # Entity 360 (entity_report.py)
            from . import entity_report
            return entity_report.handle(self, "POST")
        if self.path == "/api/ocr":
            self.run_ocr()
            return
        if self.path == "/api/talk-to-data":
            self.talk_to_data()
            return
        if self.path == "/api/answer":
            self.answer_question()
            return
        if self.path == "/api/answer/detail":
            self.answer_detail()
            return
        if self.path == "/api/mapping-correction":
            self.mapping_correction()
            return
        if self.path == "/api/catalog-correction":
            self.catalog_correction()
            return
        if self.path == "/api/catalog-correction/approve-all":
            self.approve_all()
            return
        if self.path in ("/api/catalog/build/preview", "/api/catalog/build/confirm"):
            self.catalog_build(self.path.rsplit("/", 1)[1])
            return
        if self.path in ("/api/publish/preview", "/api/publish/confirm"):
            self.publish(self.path.rsplit("/", 1)[1])
            return
        if self.path in ("/api/physical-rows/preview", "/api/physical-rows/confirm", "/api/physical-rows/iceberg-status"):
            self.physical_rows(self.path.rsplit("/", 1)[1])
            return
        if self.path not in {"/api/review", "/api/session"}:
            self.send_json(404, {"error": "Action unavailable"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= MAX_BODY or self.headers.get("Transfer-Encoding"):
                raise ValueError("Require a bounded JSON review request")
            if self.headers.get_content_type() != "application/json":
                raise ValueError("Require application/json")
            body = json.loads(self.rfile.read(size))
            if self.path == "/api/session":
                self.switch_session(body)
                return
            if (not isinstance(body, dict)
                    or set(body) != {"dataset", "mappingId", "decision", "note", "evidenceRevision"}
                    or any(not isinstance(value, str) or not value for value in body.values())):
                raise ValueError("Require dataset, mappingId, decision, note and evidenceRevision strings")
            self.review(body["dataset"], body["mappingId"], body["decision"],
                        note=body["note"], expected_revision=body["evidenceRevision"])
        except (UnicodeError, ValueError) as error:
            self.send_json(400, {"error": str(error)})

    def mapping_correction(self):
        principal = self.principal()
        if principal is None:
            return
        if principal["role"] != "admin":
            self.send_json(403, {"error": "Mapping corrections require an admin account"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= MAX_BODY or self.headers.get("Transfer-Encoding"):
                raise ValueError("Require a bounded JSON mapping correction")
            if self.headers.get_content_type() != "application/json":
                raise ValueError("Require application/json")
            request = CorrectionRequest.parse(json.loads(self.rfile.read(size)))
            workspace = self.server.workspaces.get(request.dataset)
            if workspace is None or request.dataset not in principal["datasets"]:
                self.send_json(403, {"error": "Dataset access withheld for this account"})
                return
            context = CorrectionContext(workspace, principal, self.server.source_pins)
            if not context.permits(request.source_id) or not context.permits(context.model):
                self.send_json(403, {"error": "Source access withheld for this account"})
                return
            if request.target_id is not None and not context.permits_target(request.target_id):
                raise ValueError("Definition is unavailable in this dataset")
            original = self.server.originals.get(request.source_id)
            if original is not None:
                with checked_original(original) as (status, _, _):
                    if status == "changed":
                        raise CorrectionConflict("Source revision changed. Reload the source evidence before editing.")
            with self.server.evidence_write_lock:
                anchor = self.correction_anchor(workspace, principal, context, request)
                self.require_active_editor(principal, request.dataset, request.source_id, context.model)
                revision = self.server.corrections.record(request, anchor, principal["id"])
            self.send_json(200, {"correctionRevision": revision})
        except OcrError as error:
            self.send_editor_error(error)
        except CorrectionConflict as error:
            self.send_json(409, {"error": str(error)})
        except PilotDatabaseError:
            self.send_json(503, {"error": "Private PostgreSQL access is unavailable"})
        except (UnicodeError, ValueError) as error:
            self.send_json(400, {"error": str(error)})

    def correction_anchor(self, workspace, principal, context, request):
        store = self.server.evidence
        saved = store.read(workspace, principal, request.source_id) if store is not None else None
        knowledge = workspace["knowledge"]
        for collection in (saved, knowledge):
            if collection is None:
                continue
            mapping = next((item for item in collection["mappings"] if
                            item.get("observationId") == request.observation_id
                            and item["sourceId"] == request.source_id), None)
            if mapping is None:
                continue
            observation = next((item for item in collection["observations"]
                                if item["id"] == request.observation_id), None)
            anchor = context.anchor(mapping, observation)
            if anchor is not None:
                return anchor
        raise CorrectionConflict("Cited evidence changed or is unavailable. Reload before editing.")

    def talk_to_data(self):
        principal = self.principal()
        if principal is None:
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= MAX_TALK_BODY or self.headers.get("Transfer-Encoding"):
                raise TalkError(400, "Require a bounded JSON Talk to Data request")
            if self.headers.get_content_type() != "application/json":
                raise TalkError(400, "Require application/json")
            body = TalkRequest.parse(json.loads(self.rfile.read(size)))
            if body.dataset not in principal["datasets"]:
                raise TalkError(403, "Dataset access withheld for this account")
            workspace = next((item for item in self.workspace_payload(principal, include_corrections=False)["datasets"]
                              if item["id"] == body.dataset), None)
            if workspace is None:
                raise TalkError(403, "Dataset access withheld for this account")
            fixtures = project_fixtures(self.server.fixtures, principal)
            concepts = fixtures.get(body.dataset, {}).get("definitions", ())
            reply = respond(workspace, body, admin=principal["role"] == "admin",
                            interpreter=self.server.talk_interpretation, domain_concepts=concepts,
                            database=self.server.review_store)
            self.send_json(200, mask_extracted(reply))
        except TalkError as error:
            self.send_json(error.status, {"error": str(error)})
        except TimeoutError:
            self.send_json(408, {"error": "Talk to Data request timed out"})
        except UnicodeError:
            self.send_json(400, {"error": "Invalid Talk to Data request"})
        except ValueError:
            log.exception("Talk to Data internal error")
            self.send_json(500, {"error": "Internal server error"})

    def answer_question(self):
        """R5. One structured answer from SQL facts and cited clauses, under the same gate as Talk to Data."""
        principal = self.principal()
        if principal is None:
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= MAX_TALK_BODY or self.headers.get("Transfer-Encoding"):
                raise TalkError(400, "Require a bounded JSON answer request")
            if self.headers.get_content_type() != "application/json":
                raise TalkError(400, "Require application/json")
            body = json.loads(self.rfile.read(size))
            if (not isinstance(body, dict) or set(body) - {"requestId"} != {"dataset", "question"} or not isinstance(body["dataset"], str)
                    or not isinstance(body["question"], str) or not body["question"].strip() or len(body["question"]) > 2000):
                raise TalkError(400, "Require a dataset and a question of up to 2,000 characters")
            if body["dataset"] not in principal["datasets"]:
                raise TalkError(403, "Dataset access withheld for this account")
            workspace = next((item for item in self.workspace_payload(principal, include_corrections=False)["datasets"]
                              if item["id"] == body["dataset"]), None)
            if workspace is None:
                raise TalkError(403, "Dataset access withheld for this account")
            server = self.server
            from .answer_path import published_catalog    # C1/D3: approved concepts of the latest release, scoped
            from . import ask_actions, ask_trace           # step 6 progress: the steps recorded as they run
            trace = ask_actions.start_trace(self, principal, body["dataset"], ask_trace.ANSWER, body.get("requestId"),
                                            "answer", "Asked through the answer route: a question about the data and documents.")
            typed, chat = body["question"].strip(), getattr(server.interpreter, "chat", None)
            with ask_trace.active(trace):
                try:        # Z7: a Chinese question in Chinese mode is answered in its checked English form
                    english = ui_lang.english(chat, typed)
                    reply = answer(
                        english or typed, workspace, principal, database=server.review_store, chunks=server.chunk_index,
                        links=(lambda: self.visible_links(workspace, principal)) if server.evidence_links is not None else None,
                        entity_keys=entity_keys_of(server, workspace),
                        chat=chat, embedder=server.chunk_embedder,
                        catalog=published_catalog(getattr(server, "releases", None), workspace["id"],
                                                  lambda source: chunk_visible(workspace, principal, source)))
                except ui_lang.NotUnderstood:
                    english, reply = None, ui_lang.refusal(typed, body["dataset"])
            held = server.answer_details.register(reply, body["dataset"], principal)
            if english:     # registered with the English question (suggestions read it); shown with both
                held = {**held, "question": typed, "questionEnglish": english}
            self.send_json(200, mask_extracted({**held, "trace": ask_trace.answer_steps(trace, held)}))
        except TalkError as error:
            self.send_json(error.status, {"error": str(error)})
        except PilotDatabaseError:
            self.send_json(503, {"error": "Private PostgreSQL access is unavailable"})
        except (UnicodeError, json.JSONDecodeError):
            self.send_json(400, {"error": "Invalid answer request"})
        except (OSError, ValueError, KeyError):
            log.exception("Answer path internal error")
            self.send_json(500, {"error": "Internal server error"})

    def answer_detail(self):
        """Rows behind a figure, a context series or suggested questions for an answer this account received. The body
        names a server-issued handle, never SQL; the same dataset gate as /api/answer."""
        principal = self.principal()
        if principal is None:
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= MAX_BODY or self.headers.get("Transfer-Encoding") \
                    or self.headers.get_content_type() != "application/json":
                raise ValueError
            body = json.loads(self.rfile.read(size))
        except (UnicodeError, ValueError):
            self.send_json(400, {"error": "Invalid answer detail request"})
            return
        dataset = body.get("dataset") if isinstance(body, dict) else None
        workspace = next((item for item in self.workspace_payload(principal, include_corrections=False)["datasets"]
                          if item["id"] == dataset), None) if isinstance(dataset, str) and dataset in principal["datasets"] else None
        status, payload = detail_reply(self.server.answer_details, body, principal, workspace, self.server.review_store)
        self.send_json(status, payload)

    def run_ocr(self):
        principal = self.principal()
        if principal is None:
            return
        if principal["role"] != "admin":
            self.send_json(403, {"error": "OCR requires an admin account"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size > MAX_REQUEST:
                raise OcrError(413, "OCR files are limited to 12 MiB")
            if (not 0 < size or self.headers.get("Transfer-Encoding")
                    or self.headers.get_content_type() != "application/json"):
                raise OcrError(400, "Require a bounded JSON OCR request")
            body = json.loads(self.rfile.read(size))
            self.send_json(200, mask_extracted(self.process_ocr(body, principal)))
        except OcrError as error:
            self.send_editor_error(error)
        except (UnicodeError, ValueError):
            self.send_json(400, {"error": "Invalid OCR request"})
        except TimeoutError:
            self.send_json(408, {"error": "OCR input upload timed out"})

    def send_editor_error(self, error):
        payload = {"error": str(error)}
        if error.status == 401:
            payload["accounts"] = demo_accounts()
        self.send_json(error.status, payload)

    def require_active_editor(self, previous, dataset, *sources):
        try:
            current = resolve_session(self.server.review_store, self.session_token())
        except PilotDatabaseError:
            raise OcrError(503, "Private PostgreSQL access is unavailable") from None
        if current is None or current["id"] != previous["id"]:
            raise OcrError(401, "Demo session is missing, replaced or expired")
        if current["role"] != "admin" or dataset not in current["datasets"] or any(
            source in current["withheld"] for source in sources
        ):
            raise OcrError(403, "This account cannot edit the requested evidence")

    def process_ocr(self, body, principal):
        if (not isinstance(body, dict) or set(body) - {"dataset", "sourceId", "file", "options", "mode"}
                or ("sourceId" in body) == ("file" in body) or not isinstance(body.get("dataset"), str)):
            raise OcrError(400, "Require a dataset and exactly one OCR source")
        dataset, options, mode = body["dataset"], body.get("options", {}), body.get("mode", "ocr")
        if dataset not in principal["datasets"] or dataset not in self.server.workspaces:
            raise OcrError(403, "Dataset access withheld for this account")
        if mode not in ("ocr", "xfa-fields", "text"):
            raise OcrError(400, "Unknown OCR action")
        if not isinstance(options, dict):
            raise OcrError(400, "OCR options must be an object")
        definitions = ()
        workspace = self.server.workspaces[dataset]
        model_source = workspace["knowledge"]["structured"]["modelSource"]
        model_visible = model_source not in principal["withheld"]
        if mode == "ocr" and options.get("definitionGuided") is True:
            if not model_visible:
                raise OcrError(403, "Model definitions are withheld for this account")
            from .mapped_evidence import definition_context
            hint = options.get("annotationPrompt")
            definitions = definition_context(workspace, hint if isinstance(hint, str) else "")
        if "file" in body:
            if mode != "ocr":
                raise OcrError(415, "Use the browser reader for local text; embedded forms require a pinned source")
            upload = document_input(body["file"])
            label_sources = (model_source,) if model_visible else ()
            self.require_active_editor(principal, dataset, *label_sources)
            result = self.server.ocr.process(*upload, options, definitions=definitions)
            return self.transient_evidence(result, workspace, definitions, model_visible,
                                           check_access=lambda: self.require_active_editor(
                                               principal, dataset, *label_sources))

        source_id = body["sourceId"]
        if not isinstance(source_id, str) or source_id in principal["withheld"]:
            raise OcrError(403, "Source access withheld for this account")
        record = next((item for item in workspace["sources"]
                       if item["id"] == source_id), None)
        original = self.server.originals.get(source_id) if record else None
        if original is None:
            raise OcrError(404, "OCR source is unavailable in this dataset")
        xfa = record.get("status") == "xfa_render_required"
        if mode == "ocr" and xfa:
            raise OcrError(415, "This XFA PDF requires the local embedded form-field reader")
        if mode == "xfa-fields" and (not xfa or options != {}):
            raise OcrError(415, "Embedded form reading requires a pinned XFA source without OCR options")
        if mode == "text" and (not original.content_type[0].startswith("text/plain") or options):
            raise OcrError(415, "Local text reading requires a pinned text original without OCR options")
        media = original.content_type[0].startswith(("audio/", "video/"))
        if media and (mode != "ocr" or options):
            raise OcrError(400 if mode == "ocr" else 415, "Transcription takes no options and no other reader")
        with checked_original(original, retain=True) as (status, size, stream):
            if status != "available":
                raise OcrError(409 if status == "changed" else 404, "Pinned OCR original is unavailable")
            if size > (MAX_MEDIA if media else MAX_DOCUMENT):
                raise OcrError(413, "Transcription files are limited to 25 MiB" if media
                               else "OCR files are limited to 12 MiB")
            raw = stream.read()
        filename, mime = original.relative.name, original.content_type[0].split(";", 1)[0]
        stages = (source_id, model_source) if model_visible else (source_id,)

        def check_access():
            self.require_active_editor(principal, dataset, *stages)

        check_access()
        if media:
            result = self.server.transcriber.process(filename, mime, raw, source_id=source_id)
            kind = "transcript"
        elif mode == "ocr":
            result = self.server.ocr.process(filename, mime, raw, options, source_id=source_id,
                                             definitions=definitions)
            kind = "ocr_markdown"
        else:
            result, kind = self.read_local_text(mode, raw, source_id, filename, mime)
        store = self.server.evidence
        result.update(evidenceStatus="not_saved", evidence=None,
                      fields=field_values(result["pages"], kind), fieldStatus="available", fieldOrigin="extraction")
        if kind == "ocr_markdown":
            result.update(document_tables(result["pages"]))
        if not model_visible:
            result["labeling"] = {"status": "skipped", "reason": "model_withheld"}
        else:
            from .mapped_evidence import map_pages

            try:
                mapping_options = dict(kind=kind, proposals=result.get("annotation", {}).get("findings", []),
                                       offered_definitions=definitions,
                                       mapper=Path1Mapper(workspace, self.server.mapping_embedder))
                arguments = (workspace, source_id, result["source"]["sha256"], result["pages"])
                previous = store.read(workspace, principal, source_id) if store is not None else None
                mapped = (store.prepare(*arguments, **mapping_options) if store is not None else
                          map_pages(*arguments, source_pins={source_id: original.digest}, **mapping_options))
                result["evidence"] = label_result(workspace, mapped, self.server.evidence_labeler,
                                                  previous=previous, before_batch=check_access)
                result["labeling"] = mapped["labeling"]
                result["mapping"] = {"path": "path1", "degraded": next(
                    (m["path1"]["degraded"] for m in mapped["mappings"] if m.get("path1", {}).get("degraded")), None)}
                if store is not None:
                    with self.server.evidence_write_lock:
                        check_access()
                        store.save(workspace, mapped)
                    result["evidenceStatus"] = "saved"
                    self.index_chunks(workspace, source_id, result["source"]["sha256"], result, kind, mapped)
                    try:
                        result["evidence"] = {**mapped, "mappings": self.corrected_mappings(
                            workspace, principal, mapped["mappings"], mapped["observations"],
                        )}
                    except PilotDatabaseError:
                        result["mappingCorrectionsStatus"] = "degraded"
            except OcrError:
                raise
            except (ValueError, OSError):
                result["evidenceError"] = "The original was processed, but mapped evidence could not be saved."
        check_access()
        return result

    def transient_evidence(self, result, workspace, definitions, model_visible, *, check_access):
        from .mapped_evidence import map_pages
        source = result["source"]
        revision = source["sha256"]
        source_id = f"{workspace['id']}:source:local-{revision}"
        source["id"] = source_id
        scoped = {**workspace, "sources": [*workspace["sources"], {
            "id": source_id, "title": source["filename"], "citation": {"sha256": revision},
        }]}
        result.update(evidenceStatus="ephemeral", evidence=None,
                      fields=field_values(result["pages"], "ocr_markdown"),
                      fieldStatus="available", fieldOrigin="extraction")
        result.update(document_tables(result["pages"]))
        if not model_visible:
            result["labeling"] = {"status": "skipped", "reason": "model_withheld"}
            check_access()
            return result
        try:
            mapped = map_pages(
                scoped, source_id, revision, result["pages"],
                proposals=result.get("annotation", {}).get("findings", []),
                offered_definitions=definitions, mapper=Path1Mapper(scoped, self.server.mapping_embedder),
            )
            result["evidence"] = label_result(scoped, mapped, self.server.evidence_labeler,
                                              before_batch=check_access)
            result["labeling"] = mapped["labeling"]
        except OcrError:
            raise
        except ValueError:
            result["evidenceError"] = "The file was processed, but the result could not be mapped."
        check_access()
        return result

    @staticmethod
    def read_local_text(mode, raw, source_id, filename, mime):
        if mode == "text":
            model, kind, locator = "utf8-text", "native_text", "original text"
            try:
                pages = [{"index": 0, "markdown": raw.decode("utf-8")}]
            except UnicodeError:
                raise OcrError(422, "The pinned original is not valid UTF-8 text") from None
        else:
            from .xfa import form_pages
            model, kind, locator = "xfa-datasets", "xfa_fields", "embedded form data"
            try:
                pages = validated_pages({"model": model, "pages": form_pages(raw)}, None)
            except (OcrError, ValueError, OSError):
                raise OcrError(422, "Unable to read populated XFA form fields") from None
        return ({
            "status": "completed", "provider": "local", "model": model, "pages": pages,
            "source": {"id": source_id, "filename": filename, "mimeType": mime,
                       "sha256": sha256(raw).hexdigest(), "locator": locator},
        }, kind)

    def review(self, dataset, selection, decision=None, *, note=None, expected_revision=None):
        principal = self.principal()
        if principal is None:
            return
        if principal["role"] != "admin" or dataset not in principal["datasets"]:
            self.send_json(403, {"error": "Review requires an admin account with dataset access"})
            return
        workspace = self.server.workspaces.get(dataset)
        if workspace is None:
            self.send_json(400, {"error": "Unknown dataset"})
            return
        try:
            context = CorrectionContext(workspace, principal, self.server.source_pins)
            mapping = next((item for item in workspace["knowledge"]["mappings"] if item["id"] == selection), None)
            if mapping is not None and not (context.permits(mapping["sourceId"]) and context.permits(context.model)):
                self.send_json(403, {"error": "Source access withheld for this account"})
                return
            # The principal-scoped view carries the latest correction revision, so decisions go stale with it.
            workspace = next(item for item in self.workspace_payload(principal)["datasets"] if item["id"] == dataset)
            if self.server.review_store is None:
                self.send_json(503, {"error": "Private PostgreSQL review is unavailable"})
            elif selection is None:
                self.send_json(200, {"receipts": list_reviews(workspace, self.server.review_store)})
            elif decision is None:
                revision = review_revision(workspace, selection)
                self.send_json(200, {
                    "receipt": get_review(workspace, selection, self.server.review_store),
                    "evidenceRevision": revision,
                })
            else:
                receipt = record_review(workspace, selection, decision, self.server.review_store,
                                        note=note, evidence_revision=expected_revision)
                self.send_json(200, receipt)
        except ReviewConflict as error:
            self.send_json(409, {"error": str(error)})
        except ValueError as error:
            self.send_json(400, {"error": str(error)})
        except PilotDatabaseError:
            self.send_json(503, {"error": "Private PostgreSQL review is unavailable"})

    def switch_session(self, body):
        if (not isinstance(body, dict) or set(body) not in ({"userId"}, {"userId", "recover"})
                or ("recover" in body and body["recover"] is not True)
                or not isinstance(body["userId"], str) or body["userId"] not in {user[0] for user in ACCOUNTS}):
            self.send_json(400, {"error": "Require a predefined demo userId"})
            return
        if self.server.review_store is None:
            self.send_json(503, {"error": "Private PostgreSQL access is unavailable"})
            return
        try:
            previous = self.session_token()
            recovery = "recover" in body and resolve_session(self.server.review_store, previous) is None
            token = (new_session(self.server.review_store, body["userId"], explicit=True)
                     if recovery else new_session(self.server.review_store, body["userId"], previous or ""))
            principal = resolve_session(self.server.review_store, token)
            if principal is None:
                self.send_json(401, {"error": "Demo session is missing, replaced or expired"})
                return
            self.send_json(200, session_payload(self.server.review_store, principal),
                           self.session_cookies(token))
        except PilotDatabaseError:
            self.send_json(503, {"error": "Private PostgreSQL access is unavailable"})


_ENV = object()


def create_server(port, review_store=None, *, ocr=None, transcriber=None, evidence_dir=None, evidence=None, talk_interpretation=None,
                  evidence_labeler=None, chunk_embedder=_ENV, chunk_space=None, mapping_embedder=_ENV, interpreter=_ENV,
                  catalog_chat=_ENV, iceberg=_ENV):
    workspaces = {key: load_workspace(key) for key in DATASETS}
    originals = load_originals()
    raw_fixtures = (STATIC / "workspace-fixtures.js").read_text().strip()
    prefix = "const workspaceDatasets ="
    if not raw_fixtures.startswith(prefix) or not raw_fixtures.endswith(";"):
        raise ValueError("Workspace fixtures require a JSON initializer")
    fixtures = json.loads(raw_fixtures[len(prefix):-1])
    if review_store is not None:
        initialize_access(review_store)
    corrections = MappingCorrections(review_store) if review_store is not None else None
    server = WorkspaceServer(("127.0.0.1", port), WorkspaceRequest)
    server.review_store = review_store
    server.workspaces = workspaces
    server.originals = originals
    server.source_pins = {key: item.digest for key, item in originals.items()}
    server.corrections = corrections
    server.evidence_write_lock = RLock()
    server.ocr = configuration() if ocr is None else ocr
    server.transcriber = transcription_configuration() if transcriber is None else transcriber
    server.evidence_labeler = configured_labeler() if evidence_labeler is None else evidence_labeler
    server.talk_interpretation = configured_interpretation() if talk_interpretation is None else talk_interpretation
    if evidence_dir is not None:
        from .mapped_evidence import MappedEvidence
        evidence = MappedEvidence(evidence_dir, source_pins=server.source_pins)
    server.evidence = evidence
    # Chunk rows are written whenever evidence is stored. Embedding runs only when a key is configured.
    server.chunk_embedder = configured_embedder(os.environ) if chunk_embedder is _ENV else chunk_embedder
    space = chunk_space or getattr(server.chunk_embedder, "space", None) or EmbeddingSpace(
        "mistral-embed", "provider-alias", 1024, "chunk-retrieval")
    server.chunk_index = ChunkIndex(Path(evidence_dir) / "chunk-index", space) if evidence_dir is not None else None
    # Path 1 maps each passage with vectors when a key is configured. Embeddings persist beside the evidence.
    if mapping_embedder is _ENV:
        mapping_embedder = configured_embedder(os.environ)
        if mapping_embedder is not None and evidence_dir is not None:
            mapping_embedder = CachedEmbedder(EmbeddingCache(Path(evidence_dir) / "embedding-cache"),
                                              mapping_embedder, autosave=True)
    server.mapping_embedder = mapping_embedder
    # Output A: the interpreter needs a chat key; without one every link is stored as degraded.
    if interpreter is _ENV:
        chat = configured_chat(os.environ)
        interpreter = Interpreter(chat) if chat is not None else None
    server.interpreter = interpreter
    # Output B build (admin preview, then confirm): a chat client that answers induction and decision tasks.
    if catalog_chat is _ENV:
        from .concept_catalog import configured_build_chat
        catalog_chat = configured_build_chat(os.environ)
    server.catalog_chat, server.catalog_previews = catalog_chat, {}
    server.evidence_links = EvidenceLinks(review_store) if review_store is not None else None
    server.entity_keys = {}
    server.semantic = SemanticStore(review_store) if review_store is not None else None
    server.catalog_corrections = CatalogCorrections(review_store) if review_store is not None else None
    server.releases = Releases(review_store) if review_store is not None else None
    server.physical_rows = PhysicalRows(review_store) if review_store is not None else None
    # Output C, optional Iceberg target: a callable that reads the settings in place at each call; None when unset.
    server.iceberg = (lambda: load_iceberg(os.environ)) if iceberg is _ENV else iceberg
    server.iceberg_jobs = IcebergJobs(review_store) if review_store is not None else None
    server.iceberg_approvals = {}
    server.answer_details = Details()     # handles of the rows, series and suggestions behind each answer
    from . import ask_actions   # step 6: connections store, writer; the Iceberg target reads the store first
    ask_actions.attach(server, evidence_dir, from_store=iceberg is _ENV)
    server.fixtures = fixtures
    server.payload = {
        "version": 1, "stages": STAGES, "datasets": list(workspaces.values()),
        "reviewAvailable": review_store is not None,
    }
    return server


FACT_FOLDERS = {"financial-services": ROOT / "demo_data/finance/local/finance_workbook",
                "manufacturing": ROOT / "demo_data/manufacturing/data/csv"}


def load_facts(database, say=print):
    """Load the structured tables (CSV projections) into the private database, once per file revision.
    Without them every database question is refused. A failure is reported, never raised."""
    from .metric_sql import ensure_projections_loaded
    for dataset, folder in FACT_FOLDERS.items():
        try:
            if not folder.is_dir():
                say(f"{dataset}: structured tables not loaded (folder absent: {folder.relative_to(ROOT)})")
                continue
            loaded = ensure_projections_loaded(database, dataset, folder)
            say(f"{dataset}: {len(loaded)} structured tables present")
        except Exception as error:
            say(f"{dataset}: structured tables failed to load: {error}")


def import_models(database, say=print, only=None):
    """Setup of the private V2 database: import each workspace model with lint, once per revision. One line each.
    `only` {dataset: path} replaces the workspace models. A failure is reported, never raised."""
    store = SemanticStore(database)
    if only is None:
        only = {}
        for key in DATASETS:
            workspace = load_workspace(key)
            structured = workspace["knowledge"]["structured"]
            source = next(s for s in workspace["sources"] if s["id"] == structured["modelSource"])
            only[workspace["id"]] = ROOT / source["citation"]["path"]
    for dataset, path in only.items():
        try:
            revision = sha256(Path(path).read_bytes()).hexdigest()
            present = database.sql("SELECT count(*) FROM v2_ontology_release WHERE dataset = :'d' AND source_sha256 = :'s';",
                                   d=dataset, s=revision).strip() != "0"
            if present:
                say(f"{dataset}: model already present ({revision[:12]})")
            else:
                store.import_ossie(dataset, path)
                say(f"{dataset}: model imported ({revision[:12]})")
        except Exception as error:  # the graph then reports that definitions are missing
            say(f"{dataset}: model import failed: {error}")


def default_port():
    """The port Cloudera AI gives an application, else the local default."""
    return int(os.environ.get("CDSW_APP_PORT") or 8137)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=default_port())
    parser.add_argument("--pg-bin", type=Path, default=os.environ.get("V2_PG_BIN"))
    parser.add_argument("--state-dir", type=Path, help="absolute V2 state root outside the repository")
    parser.add_argument("--import-models", action="store_true",
                        help="with --state-dir: import both workspace models into the private database (idempotent)")
    args = parser.parse_args()

    def terminate(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    with ExitStack() as stack:
        database = None
        if args.state_dir is not None:
            try:
                database = stack.enter_context(review_database(args.state_dir, args.pg_bin))
            except (OSError, ValueError, PilotDatabaseError) as error:
                print(f"Review unavailable: {error}", flush=True)
        if args.import_models and database is not None:
            import_models(database)
        if database is not None:
            load_facts(database)   # answers read these tables; only the tests loaded them before 2026-11-22
        evidence_dir = args.state_dir / "mapped-evidence" if database is not None else None
        server = stack.enter_context(create_server(args.port, database, evidence_dir=evidence_dir))
        print(f"http://127.0.0.1:{server.server_port}/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
