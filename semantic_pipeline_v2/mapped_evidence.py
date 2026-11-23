"""Persist source-scoped OCR observations and unapproved definition candidates."""

from collections import defaultdict
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
from threading import RLock

from .core import PROTECTED_ROOTS
from .extracted_fields import field_values, fragments
from .passages import from_observations
from .ocr_tables import document_tables, restore_document_tables, restore_source_pages, tables_from_observations
from .semantic_retrieval import DefinitionCatalog, label_query, observation_title, presentation_heading, words


LOCATORS = {"xfa_fields": "embedded form data", "native_text": "original text", "transcript": "timestamped transcript"}
KINDS = {"ocr_markdown", *LOCATORS}
MAX_BYTES = 1024 * 1024
MAX_LINES = 1600
_HASH = re.compile(r"[a-f0-9]{64}")
_TABLE_PLACEHOLDER = re.compile(r"\s*\[tbl-[^\]]+\]\(tbl-[^\s)]+\.(?:md|html)\)\s*")
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})(.*)$")


def _digest(value):
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode()).hexdigest()


def _is_hash(value):
    return isinstance(value, str) and bool(_HASH.fullmatch(value))


def definition_context(workspace, hint=""):
    """Return bounded model metadata for optional provider extraction guidance."""
    return DefinitionCatalog(workspace).offered(hint)


def _canonical(workspace, source_id, source_pins):
    dataset = workspace["id"]
    if not source_id.startswith(dataset + ":source:"):
        raise ValueError("Source belongs to another dataset")
    by_id = {source["id"]: source for source in workspace["sources"]}
    source = by_id.get(source_id)
    if source is None:
        raise ValueError("Source is unavailable")
    model_source = workspace["knowledge"]["structured"]["modelSource"]
    model_revision = by_id.get(model_source, {}).get("citation", {}).get("sha256")
    source_revision = source_pins.get(source_id, source.get("citation", {}).get("sha256"))
    if not _is_hash(source_revision) or not _is_hash(model_revision):
        raise ValueError("Source and model must have pinned revisions")
    return source_revision, model_revision


def _result_key(workspace, source_id, source_sha256, model_sha256):
    return _digest([workspace["id"], source_id, source_sha256, model_sha256])


def _anchors(pages, kind):
    if not isinstance(pages, list) or len(pages) > 100:
        raise ValueError("Invalid evidence pages")
    seen = set()
    byte_count = 0
    line_count = 0
    emitted = set()
    for sheet in pages:
        if not isinstance(sheet, dict):
            raise ValueError("Invalid evidence page")
        index, markdown = sheet.get("index"), sheet.get("markdown")
        if type(index) is not int or index < 0 or index in seen or not isinstance(markdown, str):
            raise ValueError("Invalid or duplicate evidence page")
        if kind in LOCATORS and index:
            raise ValueError("A logical original must use a single text block")
        seen.add(index)
        for fragment in fragments(sheet, kind):
            byte_count += len(fragment.text.encode())
            if byte_count > MAX_BYTES:
                raise ValueError("Evidence exceeds the text size limit")
            for position, line in enumerate(fragment.text.splitlines(), 1):
                identity = (index, fragment.kind, fragment.table_id, position, line)
                if (not words(line) and not _FENCE.fullmatch(line)) or identity in emitted or (
                    kind == "ocr_markdown" and fragment.kind == kind and _TABLE_PLACEHOLDER.fullmatch(line)
                ):
                    continue
                emitted.add(identity)
                line_count += 1
                if line_count > MAX_LINES:
                    raise ValueError("Evidence exceeds the observation limit")
                yield fragment, position, line


def _provider_candidates(anchors, proposals, offered_ids):
    candidates = defaultdict(set)
    if not isinstance(proposals, list):
        return candidates
    for proposal in proposals[:100]:
        if not isinstance(proposal, dict):
            continue
        definition_id = proposal.get("definitionId")
        index, quote = proposal.get("pageIndex"), proposal.get("quote")
        if (not isinstance(definition_id, str) or definition_id not in offered_ids
                or type(index) is not int or not isinstance(quote, str)
                or not quote.strip() or len(quote) > 4000):
            continue
        locations = [(fragment.index, fragment.kind, fragment.table_id, position)
                     for fragment, position, line in anchors if quote in line]
        if len(locations) == 1 and locations[0][0] == index:
            candidates[locations[0]].add(definition_id)
    return candidates


def _section_heading(text, kind):
    heading = presentation_heading(text)
    if heading is not None:
        return heading
    if kind != "ocr_markdown":
        return None
    # OCR frequently omits heading markup. Require a specific heading suffix.
    match = re.fullmatch(r"[A-Z][\w /&-]{1,75}", text.strip())
    if match and re.search(r"(Information|Detail[s]?|Overview|Summary|Section|Narrative)$", match[0], re.I):
        return match[0]
    if match and re.fullmatch(r"(?:[A-Z][a-z]+ ){1,4}(?:Transaction|Report|Account)", match[0]):
        return match[0]
    return None


def _load_column_terms() -> set:
    """Load column-header vocabulary from concept_constraints.json.

    Falls back to the inline list when the file is missing (e.g. stripped
    installs or test environments that set a different working dir).
    """
    _fallback = set(
        "account amount balance category code column count currency customer date debit credit description "
        "field id key label name number period product quantity rate reference status total transaction type unit value".split()
    )
    try:
        _cfile = Path(__file__).with_name("concept_constraints.json")
        return set(json.loads(_cfile.read_text())["column_terms"])
    except Exception:
        return _fallback


_COLUMN_TERMS = _load_column_terms()


def _column_heading(row, declared):
    """A divider states layout, while evidence determines whether cells are column labels."""
    populated = [cell for cell in row if cell.strip()]
    if len(populated) < 2 or any(re.search(r"\d|[@$€]", cell) for cell in populated):
        return False
    if len(row) >= 3 and declared:
        return True
    labels = [set(words(cell)) for cell in populated]
    # An inherited row can lose its divider. Classify it only when all cells are known labels.
    return all(label and label <= _COLUMN_TERMS for label in labels)


def _document_context(observations, tables):
    section = {}
    header = {}
    column_labels = {}
    table_labels = {}
    fenced = {}
    for table in tables:
        key = table["page"], table["id"]
        table_labels[key] = table.get("caption") or ""
        declared = table.get("headerRows", 0)
        for offset, row in enumerate(table["rows"][:max(1, declared)]):
            if _column_heading(row, offset < declared):
                line = table["startLine"] + offset
                column_labels[key[0], key[1] if table.get("placement") == "detached" else None, line] = True
    for item in observations:
        citation = item["citation"]
        if citation.get("kind") == "ocr_header":
            header.setdefault(citation.get("page"), []).append(item["quote"])
    for item in observations:
        citation = item["citation"]
        page = citation.get("page")
        kind = citation["kind"]
        table = citation.get("tableId")
        fence_key = (page, kind, table)
        fence = _FENCE.fullmatch(item["quote"])
        in_fence = fence_key in fenced
        if fence and not in_fence:
            fenced[fence_key] = fence[1]
        elif (fence and not fence[2].strip() and fence[1][0] == fenced[fence_key][0]
              and len(fence[1]) >= len(fenced[fence_key])):
            del fenced[fence_key]
        heading = _section_heading(item["quote"], kind)
        notice = item["quote"].strip().casefold().startswith((
            "synthetic test data - not for regulatory submission", "synthetic test data – not for regulatory submission",
            "synthetic demonstration document — exported from", "important: all names, balances, transactions,",
        ))
        if heading and not in_fence and kind in {"ocr_markdown", "native_text"}:
            section[page] = heading
        presentation = (kind in {"ocr_header", "ocr_footer"} or heading is not None or notice or fence or in_fence
                        or (page, table, citation.get("startLine")) in column_labels)
        label, nested = label_query(item["quote"], kind)
        contextual = [nested, *header.get(page, ())]
        contextual.append(table_labels.get((page, table), "") if table else section.get(page, ""))
        context = " | ".join(dict.fromkeys(part for part in contextual if part))[:600]
        yield item, label, context, presentation


def _historical_candidates(mapping, catalog):
    explicit = mapping.get("guidedCandidateIds")
    if isinstance(explicit, list):
        return set(explicit) & catalog.ids, set()
    basis = set(mapping.get("basis", []))
    old = set(mapping.get("candidateIds", [])) & catalog.ids
    if basis == {"provider_exact_quote"}:
        return old, set()
    if "provider_exact_quote" in basis or "historical_mixed" in basis:
        return set(), old
    return set(), set()


def _path1(workspace, result, rows, previous, mapper):
    """Run path 1 once per new assertion. A stored decision is never recomputed on read."""
    todo = [(observation, label, context) for observation, label, context, presentation in rows
            if not presentation and previous.get(observation["id"]) is not None
            and "path1" not in previous[observation["id"]]]
    if mapper is None or not todo:
        return
    kind = next((item.get("kind") for item in workspace["sources"] if item["id"] == result["sourceId"]), None)
    decisions = mapper.map_many([{"text": o["quote"], "label": label, "context": context, "source_kind": kind,
                                  "anchor_kind": result["kind"]} for o, label, context in todo])
    for (observation, _, _), decision in zip(todo, decisions):
        # The span is a citation. The text stays in the observation, where the response boundary masks it.
        previous[observation["id"]]["path1"] = {**decision, "evidence": {
            "observationId": observation["id"], "citation": observation["citation"]}}


def _rerank(workspace, result, *, catalog=None, mapper=None):
    catalog = catalog or DefinitionCatalog(workspace)
    previous = {item["observationId"]: item for item in result["mappings"]}
    mappings = []
    rows = list(_document_context(result["observations"], result.get("tables", ())))
    _path1(workspace, result, rows, previous, mapper)
    for observation, label, context, presentation in rows:
        observation["observationRole"] = "context" if presentation else "assertion"
        observation["title"] = observation_title(label)
        if context:
            observation["context"] = context
        else:
            observation.pop("context", None)
        mapping = previous.get(observation["id"])
        if presentation or mapping is None:
            continue
        if "path1" in mapping:
            decided = mapping["path1"]
            ids = [decided["selected"]] if decided["decision"] == "selected" else decided["ambiguousIds"]
            selected = [{"id": ident, "basis": f"path1_{decided['relation'] or decided['decision']}"} for ident in ids]
        else:
            matches = catalog.search(label, context)
            selected = [item for item in matches if item["score"] >= matches[0]["score"] - 1]
        guided, mixed = _historical_candidates(mapping, catalog)
        if "guidedCandidateIds" in mapping:
            mapping["guidedCandidateIds"] = sorted(guided)
        agent = mapping.get("agentLabel") or {}
        agent_id, agent_quote = agent.get("definitionId"), agent.get("quote")
        agent_ids = set()
        if (isinstance(agent_id, str) and agent_id in catalog.ids and isinstance(agent_quote, str)
                and agent_quote and agent_quote in observation["quote"]):
            agent_ids.add(agent_id)
        else:
            mapping.pop("agentLabel", None)
        candidates = sorted({item["id"] for item in selected} | guided | mixed | agent_ids)
        basis = list(dict.fromkeys(item["basis"] for item in selected))
        if guided:
            basis.append("provider_exact_quote")
        if agent_ids:
            basis.append("agent_exact_quote")
        if mixed:
            basis.append("historical_mixed")
        status = "unmapped" if not candidates else "candidate" if len(candidates) == 1 and not mixed else "ambiguous"
        if agent_ids and not (guided | mixed) - agent_ids:
            # A verified model label resolves a lexical shortlist. Preserve a disagreement
            # between two independently quoted interpretations for optional inspection.
            candidates = [agent_id]
            status = "mapped"
        mapping.update({
            "title": f"Definition mapping for {observation['title']}",
            "description": {
                "mapped": "The mapping agent linked this definition to an exact source quotation.",
                "candidate": "The source wording suggests this definition.",
                "ambiguous": "Candidate meaning is uncertain. Review the source before selecting a definition.",
                "unmapped": "No definition candidate has a verified source match.",
            }[status],
            "status": status, "targetId": candidates[0] if status in {"candidate", "mapped"} else None,
            "candidateIds": candidates, "basis": basis,
        })
        mappings.append(mapping)
    # The curated article carries source paragraphs between the original cited anchors.
    # Reuse it when native extraction reads that same file revision.
    curated = [item for item in workspace["knowledge"].get("passages", ())
               if item["sourceId"] == result["sourceId"] and item["kind"] == "article"
               and item["citation"].get("sha256") == result["sourceSha256"]]
    article_source = any(item["id"] == result["sourceId"] and item.get("kind") == "adverse-media"
                         for item in workspace["sources"])
    if result["kind"] == "native_text" and article_source and curated:
        retained = set()
        passages = []
        for article in curated:
            ref = article["citation"]
            members = [item["id"] for item in result["observations"] if
                       ref["startLine"] <= item["citation"]["startLine"] <= ref["endLine"]]
            retained.update(members)
            passages.append({**article, "observationIds": list(dict.fromkeys(article["observationIds"] + members))})
        for item in result["observations"]:
            if item["id"] not in retained:
                item["observationRole"] = "context"
        mappings = [item for item in mappings if item["observationId"] in retained]
        result["passages"] = passages
    else:
        result["passages"] = from_observations(result["observations"], result.get("tables", ()))
    result["mappings"] = mappings
    return result


def map_pages(workspace, source_id, source_sha256, pages, *, kind="ocr_markdown", source_pins=None,
              proposals=None, offered_definitions=(), mapper=None):
    """Propose definition mappings using exact source anchors and offered definitions."""
    if kind not in KINDS:
        raise ValueError("Unsupported evidence anchor kind")
    expected, model_revision = _canonical(workspace, source_id, source_pins or {})
    if source_sha256 != expected:
        raise ValueError("Source revision differs from the original")
    result_key = _result_key(workspace, source_id, source_sha256, model_revision)
    catalog = DefinitionCatalog(workspace)
    anchors = sorted(_anchors(pages, kind), key=lambda row: row[0].index)
    offered_ids = {item["id"] for item in offered_definitions} & catalog.ids
    annotated = _provider_candidates(anchors, proposals, offered_ids) if kind == "ocr_markdown" else {}
    observations = []
    mappings = []
    for fragment, position, quote in anchors:
        index = fragment.index
        citation = {"sha256": source_sha256, **fragment.location(position, position)}
        if kind == "ocr_markdown":
            title = f"OCR page {index + 1}, line {position}"
            if fragment.kind != kind:
                title = f"OCR page {index + 1}, {fragment.locator}, line {position}"
            else:
                citation.pop("locator")
        else:
            citation["locator"] = LOCATORS[kind]
            title = f"{LOCATORS[kind].capitalize()}, line {position}"
            if kind == "transcript":   # one line per segment; the page carries the aligned times
                timing = pages[0]["segments"][position - 1]
                citation.update({key: timing[key] for key in ("startSeconds", "endSeconds", "speaker", "language")})
                title = f"Transcript {timing['startSeconds']:.1f}-{timing['endSeconds']:.1f}s"
        anchor_parts = [result_key, kind, index, position, quote]
        if fragment.kind != kind:
            anchor_parts.extend([fragment.kind, fragment.table_id])
        anchor = _digest(anchor_parts)
        observation_id = f"{workspace['id']}:observation:processed-{anchor}"
        guided = annotated.get((index, fragment.kind, fragment.table_id, position), set())
        observations.append({
            "id": observation_id, "title": title, "description": "Exact extracted text",
            "sourceId": source_id, "quote": quote, "citation": citation,
        })
        mappings.append({
            "id": f"{workspace['id']}:mapping:processed-{anchor}",
            "sourceId": source_id, "observationId": observation_id,
            "guidedCandidateIds": sorted(guided),
        })
    return _rerank(workspace, {
        "sourceId": source_id, "sourceSha256": source_sha256, "modelSha256": model_revision, "kind": kind,
        "observations": observations, "mappings": mappings,
        "fields": field_values(pages, kind), "fieldStatus": "available", "fieldOrigin": "extraction",
        **(document_tables(pages) if kind == "ocr_markdown" else {}),
    }, catalog=catalog, mapper=mapper)


class MappedEvidence:
    """Store one atomic current extraction per pinned source and model revision."""

    def __init__(self, root, *, source_pins=None):
        import lancedb
        import pyarrow as pa

        root = Path(root)
        target = root / "mapped-evidence"
        if not root.is_absolute() or root.is_symlink() or target.is_symlink():
            raise ValueError("Mapped evidence requires an absolute local state directory")
        repository = Path(__file__).resolve().parents[1]
        resolved = target.resolve()
        for protected in (*PROTECTED_ROOTS, repository, repository.parent / "semantic-hub"):
            protected = Path(protected).resolve()
            if resolved == protected or protected in resolved.parents or resolved in protected.parents:
                raise ValueError("Mapped evidence overlaps protected state")
        target.mkdir(parents=True, mode=0o700, exist_ok=True)
        if target.stat().st_mode & 0o077:
            raise ValueError("Mapped evidence directory must be private")
        self.source_pins = dict(source_pins or {})
        self._lock = RLock()
        self._db = lancedb.connect(str(target))
        schema = pa.schema([("key", pa.string()), ("payload", pa.string())])
        self._table = self._db.create_table("processed_sources", schema=schema, exist_ok=True)
        if not self._table.schema.equals(schema):
            raise ValueError("Mapped evidence schema mismatch")

    def record(self, workspace, source_id, source_sha256, pages, *, kind="ocr_markdown",
               proposals=None, offered_definitions=(), mapper=None):
        result = self.prepare(workspace, source_id, source_sha256, pages, kind=kind,
                              proposals=proposals, offered_definitions=offered_definitions, mapper=mapper)
        return self.save(workspace, result)

    def prepare(self, workspace, source_id, source_sha256, pages, *, kind="ocr_markdown",
                proposals=None, offered_definitions=(), mapper=None):
        return map_pages(
            workspace, source_id, source_sha256, pages, kind=kind, source_pins=self.source_pins,
            proposals=proposals, offered_definitions=offered_definitions, mapper=mapper,
        )

    def save(self, workspace, result):
        source_id = result["sourceId"]
        source_revision, model_revision = _canonical(workspace, source_id, self.source_pins)
        if (result["sourceSha256"], result["modelSha256"]) != (source_revision, model_revision):
            raise ValueError("Evidence revisions differ from the pinned source or model")
        key = _result_key(workspace, source_id, source_revision, model_revision)
        row = {"key": key, "payload": json.dumps(result, ensure_ascii=False, sort_keys=True)}
        with self._lock:
            self._table.merge_insert("key").when_matched_update_all().when_not_matched_insert_all().execute([row])
        return result

    def _visible_key(self, workspace, principal, source_id):
        if workspace["id"] not in principal["datasets"] or source_id in principal["withheld"]:
            return None
        try:
            source_revision, model_revision = _canonical(workspace, source_id, self.source_pins)
        except ValueError:
            return None
        model_source = workspace["knowledge"]["structured"]["modelSource"]
        if model_source in principal["withheld"]:
            return None
        return _result_key(workspace, source_id, source_revision, model_revision)

    def read(self, workspace, principal, source_id):
        key = self._visible_key(workspace, principal, source_id)
        if key is None:
            return None
        with self._lock:
            rows = self._table.search().where(f"key = '{key}'").limit(1).to_list()
        if not rows:
            return None
        result = json.loads(rows[0]["payload"])
        result.setdefault("fields", [])
        result.setdefault("fieldStatus", "unavailable")
        if result.get("kind") == "ocr_markdown" and "tables" not in result:
            result.update(restore_document_tables(result["observations"]))
        elif any("format" not in table for table in result.get("tables", ())):
            formats = {(table["page"], table["id"]): table["format"]
                       for table in tables_from_observations(result["observations"])}
            for table in result["tables"]:
                table.setdefault("format", formats.get((table["page"], table["id"]), "plain"))
        if result.get("kind") == "ocr_markdown" and "sourcePages" not in result:
            result.update(restore_source_pages(result["observations"], result.get("tables", ())))
        return _rerank(workspace, result)

    def project(self, workspace, principal):
        keys = [
            key for source in workspace["sources"]
            if (key := self._visible_key(workspace, principal, source["id"])) is not None
        ]
        if not keys:
            return workspace
        values = ", ".join(f"'{key}'" for key in keys)
        with self._lock:
            rows = self._table.search().where(f"key IN ({values})").limit(len(keys)).to_list()
        if not rows:
            return workspace
        projected = deepcopy(workspace)
        for row in sorted(rows, key=lambda item: item["key"]):
            result = json.loads(row["payload"])
            if result.get("kind") == "ocr_markdown" and "tables" not in result:
                result["tables"] = tables_from_observations(result["observations"])
            _rerank(workspace, result)
            projected["knowledge"]["observations"].extend(
                item for item in result["observations"] if item["observationRole"] == "assertion"
            )
            projected["knowledge"]["mappings"].extend(result["mappings"])
            previous = projected["knowledge"].get("passages", [])
            projected["knowledge"]["passages"] = list({item["id"]: item for item in
                                                       [*previous, *result["passages"]]}.values())
        return projected
