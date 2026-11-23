"""Deterministic definition retrieval over one authorized semantic model."""

from collections import defaultdict
from dataclasses import dataclass
import re


_COMMON = frozenset("a an and by can could describe do find for from how i in is of on or our please show tell that the their to use want was what when where which who will with would you".split())
_GENERIC = frozenset("amount code date description id name number quantity rate rating status total type value".split())
_INFLECTIONS = {"accounts": "account", "customers": "customer", "payments": "payment",
                "transactions": "transaction", "orders": "order", "products": "product",
                "items": "item", "suppliers": "supplier", "balances": "balance"}
_PRESENTATION = re.compile(r"^\s*(?:\[tbl-[^\]]+\]\([^)]*\)|[| :+━─\-]{3,}|page\s+\d+(?:\s+of\s+\d+)?)\s*$", re.I)


def words(value):
    value = re.sub(r"([A-Z])([A-Z][a-z])", r"\1 \2", value)
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    return tuple(_INFLECTIONS.get(part, part) for part in re.findall(r"[^\W_]+", value.casefold())
                 if part not in _COMMON)


def _sequence(phrase, query):
    return any(query[index:index + len(phrase)] == phrase for index in range(len(query) - len(phrase) + 1))


@dataclass(frozen=True)
class Definition:
    id: str
    title: str
    description: str
    labels: tuple
    label_terms: frozenset
    qualified: tuple
    scope: frozenset
    vocabulary: frozenset


class DefinitionCatalog:
    """A bounded metadata index. It cannot query or identify data rows."""

    def __init__(self, workspace):
        prefix = workspace["id"] + ":definition:"
        model = workspace["knowledge"]["structured"]
        self.entries = []
        self._postings = defaultdict(set)
        for item in model["fields"] + model["metrics"]:
            if not item["id"].startswith(prefix):
                raise ValueError("Definition belongs to another dataset")
            name, _, field = item["name"].rpartition("/")
            title = item["title"].rsplit("/", 1)[-1]
            labels = tuple(sorted({parts for value in (field, title) if (parts := words(value))}))
            if not labels:
                continue
            description = item.get("description", "")[:300]
            label_terms = frozenset().union(*labels)
            for token in label_terms:
                self._postings[token].add(len(self.entries))
            self.entries.append(Definition(
                item["id"], item["name"], description,
                labels, label_terms, words(item["name"]), frozenset(words(name)),
                frozenset(words(description)),
            ))
        self.ids = {item.id for item in self.entries}

    def search(self, query, context="", limit=8):
        """Return relevant definitions, ordered by exact terms and scoped context."""
        terms = words(query)
        query_terms = set(terms)
        context_terms = set(words(context))
        specific = query_terms - _GENERIC
        matches = []
        # Both scoring branches require at least one query token from a definition label.
        candidates = {index for token in query_terms for index in self._postings.get(token, ())}
        for index in candidates:
            item = self.entries[index]
            label_hits = [label for label in item.labels if _sequence(label, terms)]
            scope_hits = (query_terms | context_terms) & item.scope
            exact = max(label_hits, key=lambda label: (len(label), label), default=())
            if exact:
                # A specific compound label beats a field that shares only a generic suffix.
                score = 30 + 6 * len(exact) + 3 * len(set(exact) - _GENERIC)
                if len(item.qualified) > len(exact) and _sequence(item.qualified, terms):
                    score += 12
                score += min(6, 2 * len(scope_hits))
                if len(exact) == 1 and exact[0] in _GENERIC and len(specific & item.vocabulary) >= 1:
                    score += 1
                basis = "definition_label"
            else:
                overlap = specific & item.label_terms
                meaning = specific & item.vocabulary
                if len(overlap) < 2 and not (overlap and meaning - overlap and scope_hits):
                    continue
                score = 13 + 3 * len(overlap) + min(4, len(meaning)) + min(4, len(scope_hits))
                basis = "definition_context"
            if scope_hits and context_terms & item.scope:
                score += min(4, len(context_terms & item.scope))
            matches.append({
                "id": item.id, "title": item.title, "definition": item.description,
                "score": score, "basis": basis,
            })
        return sorted(matches, key=lambda entry: (-entry["score"], entry["id"]))[:limit]

    def offered(self, hint="", limit=24):
        ranked = [match["id"] for match in self.search(hint, limit=limit)]
        positions = {ident: index for index, ident in enumerate(ranked)}
        catalog = sorted(self.entries, key=lambda item: (positions.get(item.id, len(ranked)), item.title))
        return [{"id": entry.id, "title": entry.title, "definition": entry.description}
                for entry in catalog[:limit]]


def label_query(text, kind):
    """Prefer key labels to value tokens. Keep the original assertion for its citation."""
    clean = text.strip()
    if kind == "xfa_fields" and clean.startswith("xfa:") and " = " in clean:
        path = clean.split(" = ", 1)[0].split("/")
        labels = [re.sub(r"\[\d+\]", "", part) for part in path]
        return labels[-1], " ".join(labels[-4:-1])
    if "|" in clean:
        parts = list(filter(None, (part.strip(" *`_") for part in re.split(r"(?<!\\)\|", clean))))
        if len(parts) > 1 and words(parts[0]):
            return parts[0], ""
    pair = re.match(r"([^:=\n]{1,100})\s*[:=]\s*\S", clean)
    if pair:
        return pair[1].strip(" *`_"), ""
    return clean, ""


def presentation_heading(text):
    clean = text.strip()
    if not clean or _PRESENTATION.fullmatch(clean):
        return ""
    if match := re.fullmatch(r"#{1,6}\s+(.+)", clean):
        return match[1].strip()
    return None


def hybrid_candidates(query, context, catalog, concept_catalog=None, limit=12):
    """Union of lexical definition candidates and concept-routed definition candidates.

    Lexical path: ``catalog.search(query, context, limit=limit)`` (unchanged,
    recall-only).  Concept path: lexical concept search → expand to linked
    definitions.  Both contribute up to *limit* candidates before merging.

    Returns a list of candidate dicts ordered by descending score, capped at
    *limit*.  When *concept_catalog* is None or empty, returns the lexical
    result only.
    """
    lexical = catalog.search(query, context, limit=limit)
    if concept_catalog is None or concept_catalog.empty:
        return lexical
    top_concepts = concept_catalog.search(query, context, limit=limit)
    concept_def_entries = concept_catalog.expand_to_definitions(
        [c["id"] for c in top_concepts], catalog
    )
    by_id: dict = {}
    for item in lexical:
        by_id[item["id"]] = item
    for item in concept_def_entries:
        existing = by_id.get(item["id"])
        if existing is None or item.get("score", 0) > existing.get("score", 0):
            by_id[item["id"]] = item
    return sorted(by_id.values(), key=lambda x: (-x.get("score", 0), x["id"]))[:limit]


def observation_title(label):
    label = label.strip(" *`_").replace("_", " ")
    label = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", label)
    return label if len(label) <= 96 else label[:95].rstrip() + "…"
