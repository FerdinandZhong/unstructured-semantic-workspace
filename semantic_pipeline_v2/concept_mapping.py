"""Schema-level mapping: hybrid candidates, two-step resolution, graph edges.

Hybrid candidates are the union of:
  - lexical: DefinitionCatalog.search, recall-only, top-k=12
  - vector: cosine over concept+definition text via injectable embedder, top-k=12

Two-step resolution:
  Step 1: evidence → concept (via per-metric HELIOS links in the OSSIE YAML)
  Step 2: concept → definition (constrained to metrics linked to that concept)
  Fallback: unlinked_concept when no concept found for the matched definition.

Domain-reject rules are loaded from concept_constraints.json.
Similarity is described as retrieval proximity, never confidence.
"""

from semantic_pipeline_v2.model_cache import load_yaml
import json
import math
from pathlib import Path
from typing import Protocol

from .semantic_retrieval import DefinitionCatalog


# ---------------------------------------------------------------------------
# Constraints loaded from data
# ---------------------------------------------------------------------------

_CONSTRAINTS_PATH = Path(__file__).parent / "concept_constraints.json"
_CONSTRAINTS: dict | None = None


def _load_constraints() -> dict:
    global _CONSTRAINTS
    if _CONSTRAINTS is None:
        try:
            _CONSTRAINTS = json.loads(_CONSTRAINTS_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            _CONSTRAINTS = {}
    return _CONSTRAINTS


def column_terms() -> frozenset:
    """Column heading terms, loaded from concept_constraints.json."""
    data = _load_constraints()
    terms = data.get("column_terms", ())
    return frozenset(terms) if terms else frozenset()


def source_kind_reject_titles() -> dict:
    """Return {source_kind: frozenset of words()-tuples} for domain rejects.

    Converts each reject_field_name to its words() tuple so the caller can
    compare directly with words(title).
    """
    from .semantic_retrieval import words as _words

    data = _load_constraints()
    sk_rejects = data.get("source_kind_rejects", {})
    result: dict = {}
    for kind, entry in sk_rejects.items():
        field_names = entry.get("reject_field_names", [])
        result[kind] = frozenset(_words(name) for name in field_names)
    return result


# ---------------------------------------------------------------------------
# HELIOS concept loader
# ---------------------------------------------------------------------------

def _load_model_concepts(yaml_path: str, namespace: str) -> tuple[dict, dict]:
    """Parse HELIOS concepts and per-metric concept links from an OSSIE YAML.

    Returns (concepts, def_to_concept) where:
      concepts        {concept_id: concept_dict}
      def_to_concept  {local_path: concept_id}  e.g. "ns/metric/name" → "risk_rating"
    """
    import yaml as _yaml

    raw = Path(yaml_path).read_bytes()
    doc = load_yaml(raw)

    concepts: dict = {}
    for ext in doc.get("custom_extensions", []):
        if ext.get("vendor_name") == "HELIOS":
            try:
                d = json.loads(ext["data"])
            except (json.JSONDecodeError, TypeError, KeyError):
                continue
            for c in d.get("concepts", []):
                if isinstance(c, dict) and "id" in c:
                    concepts[c["id"]] = c
            break  # only one HELIOS top-level extension expected

    def_to_concept: dict = {}
    for metric in doc.get("metrics", []):
        for ext in metric.get("custom_extensions", []):
            if ext.get("vendor_name") == "HELIOS":
                try:
                    d = json.loads(ext["data"])
                except (json.JSONDecodeError, TypeError, KeyError):
                    continue
                cid = d.get("concept")
                if cid and isinstance(cid, str):
                    local_path = f"{namespace}/metric/{metric['name']}"
                    def_to_concept[local_path] = cid
    return concepts, def_to_concept


# ---------------------------------------------------------------------------
# Embedder protocol
# ---------------------------------------------------------------------------

class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _cosine_scores(q: list[float], matrix: list[list[float]]) -> list[float]:
    """Dot-product cosine similarity: q against each row in matrix.

    ponytail: O(n*d) pure Python; replace with numpy matrix multiply if throughput matters.
    """
    q_norm = math.sqrt(sum(x * x for x in q)) or 1.0
    scores = []
    for row in matrix:
        dot = sum(a * b for a, b in zip(q, row))
        r_norm = math.sqrt(sum(x * x for x in row)) or 1.0
        scores.append(dot / (q_norm * r_norm))
    return scores


# ---------------------------------------------------------------------------
# HybridCandidateIndex
# ---------------------------------------------------------------------------

TOP_K = 12


class HybridCandidateIndex:
    """Union of lexical (DefinitionCatalog) and vector (cosine) candidates.

    Lexical scoring is for recall only; the returned score is retrieval
    proximity and must not be described as confidence.
    """

    def __init__(
        self,
        catalog: DefinitionCatalog,
        embedder: Embedder | None = None,
        k: int = TOP_K,
    ):
        self.catalog = catalog
        self.k = k
        self._entries = catalog.entries
        self._matrix: list[list[float]] = []
        self._embedder: Embedder | None = embedder
        if embedder is not None and self._entries:
            # Embed label (last segment) + description for each definition.
            texts = [
                f"{e.title.rsplit('/', 1)[-1]} {e.description}"
                for e in self._entries
            ]
            self._matrix = embedder.embed(texts)

    def search(self, query: str, context: str = "") -> list[dict]:
        """Return up to 2*k candidate definitions (lexical ∪ vector).

        Each candidate carries its basis ('lexical', 'vector_proximity', or both)
        and retrieval_proximity (cosine score, or None if only lexical).
        """
        # Lexical candidates (top-k, recall-only)
        lexical_results = self.catalog.search(query, context, limit=self.k)
        lexical_ids = {item["id"] for item in lexical_results}

        # Vector candidates (top-k by cosine similarity)
        # NOTE: when using FakeEmbedder or any non-live embedder, vector rows
        # contribute pseudo-random recall; they are not evidence of live quality.
        vector_proximity: dict[str, float] = {}
        if self._embedder is not None and self._matrix:
            q_vec = self._embedder.embed([f"{query} {context}"])[0]
            scores = _cosine_scores(q_vec, self._matrix)
            top_k_idxs = sorted(range(len(scores)), key=lambda i: -scores[i])[: self.k]
            for i in top_k_idxs:
                vector_proximity[self._entries[i].id] = scores[i]

        # Build union
        all_ids = lexical_ids | set(vector_proximity)
        entry_map = {e.id: e for e in self._entries}
        candidates = []
        for eid in all_ids:
            if eid not in entry_map:
                continue
            e = entry_map[eid]
            basis = []
            if eid in lexical_ids:
                basis.append("lexical")
            if eid in vector_proximity:
                basis.append("vector_proximity")
            candidates.append({
                "id": eid,
                "title": e.title,
                "definition": e.description,
                "basis": basis,
                "retrieval_proximity": vector_proximity.get(eid),
            })
        return candidates


# ---------------------------------------------------------------------------
# ConceptIndex
# ---------------------------------------------------------------------------

class ConceptIndex:
    """Index of business concepts for two-step mapping.

    Concepts are loaded from the HELIOS custom_extensions block in the OSSIE YAML
    referenced by workspace['knowledge']['structured']['modelSource'].
    """

    def __init__(self, workspace):
        self._dataset = workspace.get("id", "")
        self.concepts: dict[str, dict] = {}
        self._def_to_concept: dict[str, str] = {}  # local_path → concept_id
        self._field_to_concept: dict[str, str] = {}  # field links, used for constraints only

        model_source_id = (
            workspace.get("knowledge", {})
            .get("structured", {})
            .get("modelSource")
        )
        if not model_source_id:
            return
        src = next(
            (s for s in workspace.get("sources", []) if s["id"] == model_source_id),
            None,
        )
        if src is None or "citation" not in src:
            return
        yaml_path = Path(__file__).resolve().parents[1] / src["citation"]["path"]
        namespace = workspace["knowledge"]["structured"].get("namespace", "")
        try:
            self.concepts, self._def_to_concept = _load_model_concepts(
                str(yaml_path), namespace
            )
            self._field_to_concept = _load_field_concepts(str(yaml_path), namespace)
        except Exception:
            pass

    @property
    def has_concepts(self) -> bool:
        return bool(self.concepts)

    def for_definition(self, definition_id: str) -> str | None:
        """Return concept_id linked to this definition, or None.

        Only metric definitions carry per-metric concept links; field definitions
        return None.
        """
        # Strip "dataset:definition:" prefix → local path like "ns/metric/name"
        parts = definition_id.split(":", 2)
        if len(parts) < 3:
            return None
        return self._def_to_concept.get(parts[2])

    def reject(self, concept_id: str, source_kind: str) -> bool:
        """True if this source_kind conflicts with the concept's constraints.

        Uses concept.constraints from the HELIOS YAML and reject_constraints
        from concept_constraints.json to decide.
        """
        concept = self.concepts.get(concept_id, {})
        concept_constraints = set(concept.get("constraints", []))
        if not concept_constraints:
            return False
        data = _load_constraints()
        sk_entry = data.get("source_kind_rejects", {}).get(source_kind, {})
        reject_constraints = set(sk_entry.get("reject_constraints", []))
        return bool(concept_constraints & reject_constraints)


def _load_field_concepts(yaml_path: str, namespace: str) -> dict:
    """Return {"ns/field/Dataset/Field": concept_id} from the HELIOS extension of each field."""
    import yaml as _yaml

    doc = load_yaml(Path(yaml_path).read_bytes())
    links: dict = {}
    for dataset in doc.get("datasets", []):
        for field in dataset.get("fields", []):
            for ext in field.get("custom_extensions", []):
                if ext.get("vendor_name") == "HELIOS":
                    try:
                        cid = json.loads(ext["data"]).get("concept")
                    except (json.JSONDecodeError, TypeError, KeyError, AttributeError):
                        continue
                    if isinstance(cid, str) and cid:
                        links[f"{namespace}/field/{dataset['name']}/{field['name']}"] = cid
    return links


# ---------------------------------------------------------------------------
# ConceptCatalog — extended index used by evidence_labeler.py
# ---------------------------------------------------------------------------

class ConceptCatalog(ConceptIndex):
    """ConceptIndex with constraint queries for evidence_labeler.py."""

    def all_definition_ids_with_constraint(self, constraint_name: str) -> frozenset:
        """Return full definition IDs linked to concepts carrying constraint_name.

        Metric links and field links both count. Fields appear here only, so
        for_definition() still answers for metrics.
        """
        result = set()
        for local_path, cid in {**self._def_to_concept, **self._field_to_concept}.items():
            concept = self.concepts.get(cid, {})
            if constraint_name in concept.get("constraints", []):
                result.add(f"{self._dataset}:definition:{local_path}")
        return frozenset(result)


# ---------------------------------------------------------------------------
# Two-step resolution
# ---------------------------------------------------------------------------

def _shares_best_label(cands: list[dict], catalog: DefinitionCatalog) -> bool:
    """True when top-2 candidates share the same best (longest) label tuple.

    This is the tie-band check: if two definitions are identically named
    (e.g. DueDate in two tables, Amount in many tables), the evidence is
    genuinely ambiguous and the system should abstain rather than guess.
    """
    if len(cands) < 2:
        return False
    entry_map = {e.id: e for e in catalog.entries}
    top_labels = []
    for c in cands[:2]:
        entry = entry_map.get(c["id"])
        if entry and entry.labels:
            top_labels.append(max(entry.labels, key=len))
        else:
            top_labels.append(())
    return len(top_labels) == 2 and top_labels[0] == top_labels[1] and bool(top_labels[0])


def resolve_passage(
    text: str,
    context: str,
    source_kind: str,
    index: HybridCandidateIndex,
    concept_index: ConceptIndex,
    *,
    sk_rejects: dict | None = None,
) -> dict:
    """Resolve evidence text to a definition via two-step concept mapping.

    Step 1: evidence → concept (concept_index required; falls through to
            unlinked_concept when model has no concepts).
    Step 2: concept → definition (constrained to concept's linked metrics;
            fallback to direct definition candidates, labelled unlinked_concept).

    Domain rejects (sk_rejects) filter candidates where source_kind is
    disallowed for a field.  When sk_rejects is None it is loaded from
    concept_constraints.json.

    Returns a dict with:
      candidates       list[dict]  hybrid candidates (lexical ∪ vector)
      definition_id    str|None    chosen definition (None = abstained)
      concept_id       str|None    chosen concept (None = unlinked_concept)
      method           str         lexical|vector_proximity|abstained
      unlinked_concept bool        True when no concept was found
      reason           str|None    abstention reason when definition_id is None
    """
    if sk_rejects is None:
        sk_rejects = source_kind_reject_titles()

    candidates = index.search(text, context)

    # Apply domain rejects
    sk_entry = sk_rejects.get(source_kind, frozenset())

    def _is_rejected(candidate: dict) -> bool:
        # Concept-level reject: concept has constraint blocked by this source_kind
        cid = concept_index.for_definition(candidate["id"])
        if cid and concept_index.reject(cid, source_kind):
            return True
        # JSON-level reject: match last path segment via words()
        from .semantic_retrieval import words as _words
        field_words = _words(candidate["id"].rsplit("/", 1)[-1])
        return field_words in sk_entry

    valid = [c for c in candidates if not _is_rejected(c)]

    if not valid:
        return {
            "candidates": candidates,
            "definition_id": None,
            "concept_id": None,
            "method": "abstained",
            "unlinked_concept": True,
            "reason": "all_rejected" if candidates else "no_candidates",
        }

    # Tie-band check: abstain when top candidates share the same label.
    # Exception: break the tie when one candidate's title appears verbatim
    # in the query (e.g. "ProductNumber: X" resolves to ProductNumber, not
    # ProductID, even though both reduce to ("product",) after generic filtering).
    if _shares_best_label(valid, index.catalog):
        entry_map = {e.id: e for e in index.catalog.entries}
        q_lower = text.lower()
        tie_cands = valid[:2]
        exact = [
            c for c in tie_cands
            if entry_map.get(c["id"])
            and entry_map[c["id"]].title.rsplit("/", 1)[-1].lower() in q_lower
        ]
        if len(exact) == 1:
            # Clear title match — not a genuine ambiguity
            chosen = exact[0]
            cid = concept_index.for_definition(chosen["id"]) if concept_index.has_concepts else None
            method = "vector_proximity" if chosen.get("retrieval_proximity") else "lexical"
            return {
                "candidates": candidates,
                "definition_id": chosen["id"],
                "concept_id": cid,
                "method": method,
                "unlinked_concept": cid is None,
                "reason": None,
            }
        return {
            "candidates": candidates,
            "definition_id": None,
            "concept_id": None,
            "method": "abstained",
            "unlinked_concept": True,
            "reason": "ambiguous_label_tie",
        }

    # Step 1: concept-constrained path
    if concept_index.has_concepts:
        concept_hits: dict[str, list[dict]] = {}
        for c in valid:
            cid = concept_index.for_definition(c["id"])
            if cid:
                concept_hits.setdefault(cid, []).append(c)
        if concept_hits:
            # Prefer concept with most supporting candidates
            best_concept = max(concept_hits, key=lambda k: len(concept_hits[k]))
            linked = concept_hits[best_concept]
            best = max(linked, key=lambda c: c.get("retrieval_proximity") or 0.0)
            method = "vector_proximity" if best.get("retrieval_proximity") is not None else "lexical"
            return {
                "candidates": candidates,
                "definition_id": best["id"],
                "concept_id": best_concept,
                "method": method,
                "unlinked_concept": False,
                "reason": None,
            }

    # Step 2: unlinked_concept fallback — pick lexical candidate first
    lexical_first = next(
        (c for c in valid if "lexical" in c.get("basis", [])), None
    )
    chosen = lexical_first or valid[0]
    method = "lexical" if lexical_first else "vector_proximity"
    return {
        "candidates": candidates,
        "definition_id": chosen["id"],
        "concept_id": None,
        "method": method,
        "unlinked_concept": True,
        "reason": None,
    }


# ---------------------------------------------------------------------------
# Graph edge writer
# ---------------------------------------------------------------------------

def _lit(s: str) -> str:
    """PostgreSQL single-quoted text literal."""
    return "'" + s.replace("'", "''") + "'"


def write_graph_edges(
    store,
    passage_id: str,
    result: dict,
    evidence: dict,
) -> list[str]:
    """Write graph edges for a resolved passage.

    When a concept is found:
      passage → concept  (mentions)
      concept → definition  (realised_by)

    When no concept (unlinked_concept fallback):
      passage → definition  (mentions)

    evidence: {quote: str, page: int|None, revision: str}

    Similarity is stored as retrieval_proximity in the evidence JSONB, never
    as the confidence column (which stays NULL for lexical/vector methods).

    Returns list of request_ids written.
    """
    import hashlib

    definition_id = result.get("definition_id")
    if not definition_id:
        return []

    concept_id = result.get("concept_id")
    method = result.get("method", "lexical")
    written = []

    # Enrich evidence with proximity for traceability
    top_candidate = next(
        (c for c in result.get("candidates", []) if c["id"] == definition_id), {}
    )
    ev_payload = {
        **evidence,
        "retrieval_proximity": top_candidate.get("retrieval_proximity"),
        "unlinked_concept": result.get("unlinked_concept", True),
        "method": method,
    }
    ev_json = json.dumps(ev_payload, sort_keys=True, ensure_ascii=True)

    if concept_id:
        # passage → concept (mentions)
        req_id1 = hashlib.sha256(
            json.dumps(
                [passage_id, concept_id, "mentions", evidence], sort_keys=True
            ).encode()
        ).hexdigest()
        store.db.sql(
            "INSERT INTO v2_graph_edge"
            " (request_id, src, dst, relation, evidence, method, status)"
            f" VALUES ({_lit(req_id1)}, {_lit(passage_id)}, {_lit(concept_id)},"
            f" 'mentions', {_lit(ev_json)}::jsonb, {_lit(method)}, 'proposed')"
            " ON CONFLICT (request_id) DO NOTHING;"
        )
        written.append(req_id1)

        # concept → definition (realised_by)
        req_id2 = hashlib.sha256(
            json.dumps(
                [concept_id, definition_id, "realised_by", evidence], sort_keys=True
            ).encode()
        ).hexdigest()
        store.db.sql(
            "INSERT INTO v2_graph_edge"
            " (request_id, src, dst, relation, evidence, method, status)"
            f" VALUES ({_lit(req_id2)}, {_lit(concept_id)}, {_lit(definition_id)},"
            f" 'realised_by', {_lit(ev_json)}::jsonb, {_lit(method)}, 'proposed')"
            " ON CONFLICT (request_id) DO NOTHING;"
        )
        written.append(req_id2)
    else:
        # unlinked_concept fallback: passage → definition (mentions)
        req_id = hashlib.sha256(
            json.dumps(
                [passage_id, definition_id, "mentions", evidence], sort_keys=True
            ).encode()
        ).hexdigest()
        store.db.sql(
            "INSERT INTO v2_graph_edge"
            " (request_id, src, dst, relation, evidence, method, status)"
            f" VALUES ({_lit(req_id)}, {_lit(passage_id)}, {_lit(definition_id)},"
            f" 'mentions', {_lit(ev_json)}::jsonb, {_lit(method)}, 'proposed')"
            " ON CONFLICT (request_id) DO NOTHING;"
        )
        written.append(req_id)

    return written


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def evaluate_gold(
    gold_path,
    catalog: DefinitionCatalog,
    embedder: Embedder | None = None,
    *,
    tie_band: bool = True,
    workspace=None,
) -> dict:
    """Evaluate candidate recall@12 and final precision on the gold set.

    Results labelled 'fake-embedding offline' are not live quality numbers.
    Vector rows from FakeEmbedder contribute pseudo-random recall; they are
    not evidence that live embeddings will improve results.

    Args:
        gold_path: path to mapping_gold.jsonl
        catalog: DefinitionCatalog for the target workspace
        embedder: injectable embedder (None = lexical only)
        tie_band: if True, apply label-tie abstention (see _shares_best_label)
        workspace: optional workspace dict; when provided, ConceptIndex is built
                   from it and concept-step precision is reported.

    Returns dict with:
        recall_at_12            fraction of positive anchors where candidate set contains expected
        precision               fraction of positive anchors where final definition = expected
        concept_precision       fraction of concept-anchors where concept step = expected_concept
        abstention_accuracy     fraction of abstain anchors where system also abstains
        via_concept_path        count resolved via concept (not unlinked)
        via_fallback            count resolved via unlinked_concept fallback
        n_total / n_hit / n_abstain / n_concept_anchors
        recall_hits / precision_hits / concept_hits / abstention_hits
        tie_band_enabled        bool
        note                    disclaimer string
    """
    index = HybridCandidateIndex(catalog, embedder)

    if workspace is not None:
        concept_index = ConceptIndex(workspace)
    else:
        class _EmptyConceptIndex:
            has_concepts = False
            def for_definition(self, _): return None
            def reject(self, _, __): return False
        concept_index = _EmptyConceptIndex()

    sk_rejects = source_kind_reject_titles()

    records = [
        json.loads(line)
        for line in Path(gold_path).read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    hit_records = [r for r in records if r.get("expected_definition_id") or r.get("abstain") is False]
    abstain_records = [r for r in records if not r.get("expected_definition_id") or r.get("abstain")]
    concept_records = [r for r in hit_records if r.get("expected_concept")]

    recall_hits = 0
    for r in hit_records:
        candidates = index.search(r["text"], r.get("context", ""))
        expected = r.get("expected_definition_id", "")
        if any(c["id"] == expected for c in candidates):
            recall_hits += 1

    precision_hits = 0
    concept_hits = 0
    via_concept_path = 0
    via_fallback = 0
    for r in hit_records:
        result = resolve_passage(
            r["text"], r.get("context", ""), r.get("source_kind", ""),
            index, concept_index,
            sk_rejects=sk_rejects,
        )
        if result.get("definition_id") == r.get("expected_definition_id"):
            precision_hits += 1
        if not result.get("unlinked_concept") and result.get("definition_id"):
            via_concept_path += 1
        elif result.get("definition_id"):
            via_fallback += 1
        if r.get("expected_concept") and result.get("concept_id") == r["expected_concept"]:
            concept_hits += 1

    abstention_hits = 0
    for r in abstain_records:
        result = resolve_passage(
            r["text"], r.get("context", ""), r.get("source_kind", ""),
            index, concept_index,
            sk_rejects=sk_rejects,
        )
        if result.get("definition_id") is None:
            abstention_hits += 1

    n_hit = len(hit_records)
    n_abstain = len(abstain_records)
    n_concept = len(concept_records)
    return {
        "recall_at_12": recall_hits / n_hit if n_hit else 0.0,
        "precision": precision_hits / n_hit if n_hit else 0.0,
        "concept_precision": concept_hits / n_concept if n_concept else 0.0,
        "abstention_accuracy": abstention_hits / n_abstain if n_abstain else 1.0,
        "via_concept_path": via_concept_path,
        "via_fallback": via_fallback,
        "n_total": len(records),
        "n_hit": n_hit,
        "n_abstain": n_abstain,
        "n_concept_anchors": n_concept,
        "recall_hits": recall_hits,
        "precision_hits": precision_hits,
        "concept_hits": concept_hits,
        "abstention_hits": abstention_hits,
        "tie_band_enabled": tie_band,
        "note": "fake-embedding offline results only; vector rows from FakeEmbedder are not evidence of live quality",
    }
