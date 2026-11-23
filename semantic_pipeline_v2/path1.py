"""Path 1: map one passage to a definition from its content, with graded relations and abstention.

Query    the passage text plus its context (section heading, table caption, row header, speaker).
Legs     lexical (labels, names and synonyms) and vector (cosine over definition cards), unioned.
Checks   deterministic compatibility before selection: value type, unit, grain, time basis,
         commitment versus actual, allegation versus recorded field.
Relation exact (V1 `candidate_meaning`), related (V1 `related_context`) or rejected (V1 `incompatible`).
Select   documented score, then abstain below THRESHOLDS["threshold"] or when the top two are within
         THRESHOLDS["margin"] (decision `ambiguous`, both kept).
Without an embedder, or when it fails, the result is lexical only and carries `degraded`. A vector is
never substituted. Retrieval proximity is not confidence.

The relation names and the V1 rule set come from multimedia_rag/understanding.py `compatibility`.
That file is not imported: it is concept-card specific. Its checks are ported as the small pure
functions below.
"""

import re

import numpy as np

from .definition_cards import load_cards
from .semantic_retrieval import DefinitionCatalog, label_query, words

ARMS = ("lexical", "hybrid", "hybrid_context")
# Frozen on 2026-11-20. See the report of action C3 for how each value was chosen.
THRESHOLDS = {"k": 12, "threshold": 0.5, "margin": 0.05, "z_lo": 2.0, "z_hi": 4.5, "lex_ref": 45.0,
              "related_factor": 0.85}
_ARM = {"lexical": (False, None, False), "hybrid": (True, "definition_only", False),
        "hybrid_context": (True, "context", True)}  # vector leg, card variant, passage as query
_GENERIC_SINGLE = frozenset("amount code date description id name number quantity rate rating status total type value".split())

# ---------------------------------------------------------------- compatibility

_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}[/.]\d{1,2}[/.]\d{2,4}\b"
                   r"|\b\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?\s+\d{4}\b"
                   r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}\b", re.I)
_UNIT_PASSAGE = (
    ("currency", re.compile(r"[$€£]|\b(?:usd|sgd|eur|gbp|jpy|cny|aud|myr|hkd|dollars?|euros?)\b", re.I)),
    ("percent", re.compile(r"%|\bper\s?cent\b|\bpercent\b", re.I)),
    ("weight", re.compile(r"\d\s*(?:kg|kgs|kilograms?|grams?|g|lbs?|pounds?|tonnes?|tons?)\b", re.I)),
    ("days", re.compile(r"\d\s*days?\b", re.I)),
    ("hours", re.compile(r"\d\s*(?:hours?|hrs?)\b", re.I)),
    ("number", re.compile(r"\d\s*(?:units?|pcs|pieces)\b", re.I)),
)
_COMMIT = ("commit", "plan", "forecast", "target", "budget", "schedul", "contract", "promis", "request", "ordered")
_ACTUAL = ("actual", "receiv", "receipt", "deliver", "ship", "dispatch", "invoic", "paid", "produc", "scrap",
           "reject", "complet", "realis", "realiz", "incurred", "stocked", "yield")
_POINT = re.compile(r"\bon\b[^.|]{0,20}\bbusiness date\b|\bsnapshot\b|\bas[- ]of\b|\bdaily\b", re.I)
_PERIOD = re.compile(r"\b(?:in|during|across|over|within)\s+(?:the\s+|a\s+)?(?:selected\s+)?(?:period|quarter|month|year|week)\b"
                     r"|\bbetween\b|\bytd\b|\byear to date\b|\b(?:monthly|quarterly|annual|annually|weekly)\b"
                     r"|\bper (?:month|quarter|year)\b", re.I)
_PER = re.compile(r"\bper\s+([a-z]+)", re.I)
_GRAIN = {"piece": "unit", "item": "unit", "each": "unit", "unit": "unit"}
_ALLEGATION = re.compile(r"\b(?:alleg\w*|reportedly|reported|suspect\w*|accus\w*|rumou?r\w*|unverified|purported\w*|"
                         r"claimed|claims?|investigat\w*|probe)\b", re.I)


def _spaced(value):
    return re.sub(r"[_]+", " ", re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value or ""))


def _stems(text, stems):
    tokens = re.findall(r"[a-z]+", text.lower())
    return any(t.startswith(s) for t in tokens for s in stems)


def _commitment_class(text):
    commit, actual = _stems(text, _COMMIT), _stems(text, _ACTUAL)
    return "commitment" if commit and not actual else "actual" if actual and not commit else None


def _passage_units(text):
    return {family for family, pattern in _UNIT_PASSAGE if pattern.search(text)}


def _card_unit(unit):
    low = (unit or "").lower()
    for family, tokens in (("currency", ("currency",)), ("percent", ("ratio", "percent")), ("days", ("days",)),
                           ("hours", ("hours",)), ("weight", ("grams", "pound", "weight")),
                           ("number", ("each", "units", "unit of measure", "count"))):
        if any(t in low for t in tokens):
            return family
    return None


def _grain(text):
    match = _PER.search(text)
    return None if match is None else _GRAIN.get(match[1].lower().rstrip("s"), match[1].lower().rstrip("s"))


def compatibility(text, card, *, source_kind=None, constrained=False):
    """Graded relation of one passage to one definition card.

    Returns {"relation": exact|related|rejected, "reasons": [...], "selectable": bool}.
    `constrained` marks a recorded field that an allegation or adverse media may not supply.
    """
    reasons, label = [], f"{card['label']} {_spaced(card['name'])}"
    if constrained:
        if source_kind == "adverse-media":
            return {"relation": "rejected", "selectable": False,
                    "reasons": ["adverse media cannot supply a recorded field"]}
        if _ALLEGATION.search(text):
            return {"relation": "related", "selectable": False,
                    "reasons": ["an allegation is not a recorded field value"]}
    if card.get("role") == "audit":
        return {"relation": "rejected", "selectable": False, "reasons": ["audit field is provenance, not content"]}
    dates = _DATE.findall(text)
    stripped = _DATE.sub(" ", text)
    has_date, has_number = bool(dates), bool(re.search(r"\d", stripped))
    expects_date = card.get("datatype") in ("Date", "DateTime") or card.get("role") == "time"
    numeric = card.get("datatype") in ("Integer", "Decimal") and (
        card["kind"] == "metric" or card.get("role") in ("measure", "attribute"))
    if expects_date and has_number and not has_date:
        reasons.append("value type: a number is not a date")
    if numeric and has_date and not has_number:
        reasons.append("value type: a date is not a number")
    families, family = _passage_units(text), _card_unit(card.get("unit"))
    if families and family and family not in families:
        reasons.append(f"unit differs: passage {'/'.join(sorted(families))}, definition {family}")
    own, other = _grain(text), _grain(f"{label} {card.get('unit') or ''}")
    if own and other and own != other:
        reasons.append(f"grain differs: per {own} versus per {other}")
    point, period = bool(_POINT.search(text)), bool(_PERIOD.search(text))
    c_point, c_period = bool(_POINT.search(label)), bool(_PERIOD.search(label))
    if (point != period and c_point != c_period) and (point, period) != (c_point, c_period):
        reasons.append("time basis differs: point in time versus period")
    if reasons:
        return {"relation": "rejected", "selectable": False, "reasons": reasons}
    wanted, have = _commitment_class(text), _commitment_class(label)
    if wanted and have and wanted != have:
        return {"relation": "related", "selectable": True,
                "reasons": [f"the passage states a {wanted}, the definition records the {have}"]}
    return {"relation": "exact", "selectable": True, "reasons": []}


# ---------------------------------------------------------------- mapper

def _clip(value):
    return min(1.0, max(0.0, value))


class Path1Mapper:
    def __init__(self, workspace, embedder=None, *, arm="hybrid_context", strict=False, thresholds=None):
        if arm not in ARMS:
            raise ValueError("unknown arm")
        self.arm, self.strict = arm, strict
        self.use_vector, self.variant, self.full_query = _ARM[arm]
        self.embedder = embedder if self.use_vector else None
        self.t = {**THRESHOLDS, **(thresholds or {})}
        self.catalog = DefinitionCatalog(workspace)
        self.cards = load_cards(workspace)
        self.ids = list(self.cards)
        self.constrained = {i for i, c in self.cards.items() if "risk rating" in c["label"].lower()}
        try:
            from .concept_mapping import ConceptCatalog
            self.constrained |= set(ConceptCatalog(workspace).all_definition_ids_with_constraint("no_adverse_media"))
        except Exception:  # constraint data is optional; the label rule above remains
            pass
        self._synonyms = {i: [s for s in (words(x) for x in c["synonyms"]) if s and not (
            len(s) == 1 and s[0] in _GENERIC_SINGLE)] for i, c in self.cards.items()}
        self._matrix = None

    # -- legs
    def _lexical(self, item, k):
        text, context = item["text"], item.get("context", "")
        if self.full_query:
            query, ctx = text, context
        else:
            label, nested = item.get("label") or label_query(text, item.get("anchor_kind", ""))[0], ""
            query, ctx = label, f"{nested} {context}".strip()
        found = {m["id"]: (m["score"], m["basis"]) for m in self.catalog.search(query, ctx, limit=k)}
        terms = words(query)
        for ident, synonyms in self._synonyms.items():
            for syn in synonyms:
                if any(terms[i:i + len(syn)] == syn for i in range(len(terms) - len(syn) + 1)):
                    score = 24 + 4 * len(syn)
                    if score > found.get(ident, (0, ""))[0]:
                        found[ident] = (score, "synonym")
        return dict(sorted(found.items(), key=lambda kv: (-kv[1][0], kv[0]))[:k])

    def _vectors(self, items):
        """Cosine and z-score of every card per item. Raises when the embedder cannot answer."""
        if self._matrix is None:
            raw = np.asarray(self.embedder.embed([self.cards[i][self.variant] for i in self.ids]), dtype=np.float32)
            self._matrix = raw / np.maximum(np.linalg.norm(raw, axis=1, keepdims=True), 1e-9)
        queries = [(f"{x['text']}\n{x.get('context', '')}" if self.full_query else
                    f"{x.get('label') or label_query(x['text'], x.get('anchor_kind', ''))[0]} {x.get('context', '')}"
                    ).strip()[:2000] for x in items]
        raw = np.asarray(self.embedder.embed(queries), dtype=np.float32)
        raw = raw / np.maximum(np.linalg.norm(raw, axis=1, keepdims=True), 1e-9)
        cos = raw @ self._matrix.T
        z = (cos - cos.mean(axis=1, keepdims=True)) / np.maximum(cos.std(axis=1, keepdims=True), 1e-9)
        return cos, z

    @property
    def space(self):
        space = getattr(self.embedder, "space", None)
        return space.identity if space is not None else None

    def map_many(self, items, k=None, legs=("lexical", "vector")):
        """`k` cuts each retrieval leg (default `THRESHOLDS["k"]`); the candidates are the union of the `legs`.
        One leg alone ranks by its own score: the keyword score, or the cosine."""
        k = self.t["k"] if k is None else k
        degraded, cos, z = None, None, None
        if "vector" not in legs:
            pass
        elif self.use_vector and items:
            if self.embedder is None:
                degraded = "no_embedder"
            else:
                try:
                    cos, z = self._vectors(items)
                except Exception as error:  # the type name only: provider text may carry credentials
                    if self.strict:
                        raise
                    self._matrix, degraded = None, f"embedder_failed:{type(error).__name__}"
        elif self.use_vector:
            return []
        return [self._one(x, None if cos is None else cos[n], None if z is None else z[n], degraded, k, legs)
                for n, x in enumerate(items)]

    def _one_concept(self, rows):
        """True when the close candidates share one concept and are one field and one metric."""
        cards = [self.cards[r["id"]] for r in rows]
        return (len(cards) == 2 and cards[0]["concept"] and cards[0]["concept"] == cards[1]["concept"]
                and {c["kind"] for c in cards} == {"field", "metric"})

    def _one(self, item, cos, z, degraded, k, use=("lexical", "vector")):
        t, lexical = self.t, self._lexical(item, k) if "lexical" in use else {}
        legs = {i: ["lexical"] for i in lexical}
        if cos is not None:
            for n in np.argsort(-cos)[:k]:
                ident = self.ids[int(n)]
                legs.setdefault(ident, []).append("vector")
        rows = []
        for ident, leg in legs.items():
            lex = lexical.get(ident, (None, None))[0]
            vec = None if cos is None else float(cos[self.ids.index(ident)])
            zed = None if z is None else float(z[self.ids.index(ident)])
            lex_n = 0.0 if lex is None else _clip(lex / t["lex_ref"])
            vec_n = 0.0 if zed is None else _clip((zed - t["z_lo"]) / (t["z_hi"] - t["z_lo"]))
            retrieval = 1 - (1 - lex_n) * (1 - vec_n)  # strong evidence from either leg counts
            check = compatibility(item["text"], self.cards[ident], source_kind=item.get("source_kind"),
                                  constrained=ident in self.constrained)
            factor = 0.0 if not check["selectable"] else 1.0 if check["relation"] == "exact" else t["related_factor"]
            rows.append({
                "id": ident, "legs": leg, "lexical": lex, "lexicalBasis": lexical.get(ident, (None, None))[1],
                "proximity": None if vec is None else round(vec, 4), "z": None if zed is None else round(zed, 3),
                "retrieval": round(retrieval, 4), "relation": check["relation"], "reasons": check["reasons"],
                "score": round(retrieval * factor, 4),
            })
        if tuple(use) == ("lexical",):  # one leg ranks by its own score (the combined score ties at 1.0)
            rows.sort(key=lambda r: (-(r["lexical"] or 0), r["id"]))
        elif tuple(use) == ("vector",):
            rows.sort(key=lambda r: (-(r["proximity"] if r["proximity"] is not None else -2), r["id"]))
        else:
            rows.sort(key=lambda r: (-r["retrieval"], r["id"]))
        for rank, row in enumerate(rows, 1):
            row["retrievalRank"] = rank
        pool = sorted((r for r in rows if r["score"] > 0), key=lambda r: (-r["score"], r["id"]))
        decision, chosen, reason = "abstain", [], "no_compatible_candidate" if rows else "no_candidates"
        if pool and pool[0]["score"] < t["threshold"]:
            reason = "below_threshold"
        elif pool:
            close = [r for r in pool if pool[0]["score"] - r["score"] < t["margin"]][:3]
            if len(close) > 1 and self._one_concept(close):
                close = close[:1]  # a field and the metric that measures it are one meaning, not a conflict
            decision, chosen, reason = ("ambiguous", close, "margin_too_small") if len(close) > 1 else (
                "selected", close, None)
        top = chosen[0] if decision == "selected" else None
        return {
            "arm": self.arm, "decision": decision, "reason": reason, "selected": top["id"] if top else None,
            "relation": top["relation"] if top else None, "equivalentIds": [r["id"] for r in pool if top and r is not top and pool[0]["score"] - r["score"] < t["margin"] and self._one_concept([top, r])], "ambiguousIds": [r["id"] for r in chosen] if decision == "ambiguous" else [],
            "score": top["score"] if top else (pool[0]["score"] if pool else 0.0),
            "scoreParts": ({"lexical": top["lexical"], "proximity": top["proximity"], "z": top["z"],
                            "retrieval": top["retrieval"]} if top else None),
            "candidates": rows, "cardVariant": self.variant, "embeddingSpace": None if degraded or not self.use_vector else self.space,
            "degraded": degraded, "thresholds": {k: t[k] for k in ("k", "threshold", "margin")},
        }
