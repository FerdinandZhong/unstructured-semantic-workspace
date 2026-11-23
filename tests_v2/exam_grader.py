"""Deterministic grader of the exam pass rule (tests_v2/fixtures/exam/README.md), plus diagnostics that are not part of
the pass rule: facts and citations scored separately, citation precision/recall/F1 and the kind of each citation miss."""
import os
import re


def norm(text):
    return " ".join(str(text).split()).lower()


def same_source(cited, expected, aliases):
    """`cited` is an answer citation, `expected` a key's source string (path or id); aliases: id -> {path, title}."""
    name = os.path.basename(expected.rstrip("/"))
    ids = {cited.get("source"), cited.get("path"), cited.get("title")}
    return expected in ids or name in {os.path.basename(str(i)) for i in ids if i} or any(
        os.path.basename(str(a)) == name for a in aliases.get(cited.get("source"), ()))


def overlaps(cited_span, expected_span):
    # Punctuation is ignored (controller, 2026-11-22): a field passage is cited as "Label: value" while the printed
    # report, and so the key, reads "Label value". Words, digits and their order must still match.
    a, b = (norm(re.sub(r"[^\w\s]", " ", str(span))) for span in (cited_span, expected_span))
    return bool(a) and (a in b or b in a)


def _value_ok(got, want, tolerance):
    try:
        return abs(float(got) - float(want)) <= float(tolerance or 0)
    except (TypeError, ValueError):
        return str(got) == str(want)


def _filters_ok(got, want):
    return {k: str(v) for k, v in got.items()} == {k: str(v) for k, v in want.items()}


def _conclusion_ok(spec, value):
    kind, want = spec["type"], spec["value"]
    if kind == "none":
        return value is None
    if value is None:
        return False
    if kind == "boolean":
        return isinstance(value, bool) and value == want
    if kind == "number":
        return not isinstance(value, bool) and _value_ok(value, want, 0)
    if kind == "choice":
        squash = lambda t: re.sub(r"[\s_]+", "", str(t)).lower()  # noqa: E731
        return squash(value) == squash(want)
    return any(norm(w) in norm(value) for w in ([want] if isinstance(want, str) else want))


def grade(key, answer, aliases=None):
    """{"pass": bool, "parts": {name: bool}, "failing": [...], "facts_ok", "citations": {...}}."""
    aliases = aliases or {}
    parts = {"status": answer["status"] == key["expected_status"]}
    returned = set(answer["definitions"])
    parts["definitions"] = not key["required_definitions"] or any(set(alt) <= returned for alt in key["required_definitions"])
    parts["facts"] = all(any(f["definition"] == e["definition"] and _filters_ok(f["filters"], e["filters"])
                             and _value_ok(f["value"], e["value"], e.get("tolerance")) for f in answer["facts"])
                         for e in key["expected_facts"])
    cites = answer["citations"]
    matched = [[c for c in cites if same_source(c, r["source"], aliases) and overlaps(c["span"], r["span"])]
               for r in key["required_citations"]]
    parts["citations"] = all(matched)
    parts["conclusion"] = _conclusion_ok(key["conclusion"], answer["conclusion"])
    if key["expected_status"] != "answered":
        withheld = set(key.get("withheld_sources", ()))
        parts["nothing_leaked"] = (not answer["facts"] and not cites and answer["conclusion"] is None
                                   and not any(c.get("source") in withheld for c in cites))
    hit = {id(c) for group in matched for c in group}
    recall = sum(bool(g) for g in matched) / len(matched) if matched else 1.0
    precision = len(hit) / len(cites) if cites else (1.0 if not matched else 0.0)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    misses = []
    for r, group in zip(key["required_citations"], matched):
        if not group:
            same = [c for c in cites if same_source(c, r["source"], aliases)]
            misses.append("wrong selection" if same else "omission")
    misses += ["over-citation"] * (len(cites) - len(hit))
    failing = [n for n, ok in parts.items() if not ok]
    return {"pass": not failing, "parts": parts, "failing": failing,
            "citations": {"precision": precision, "recall": recall, "f1": f1, "exact": recall == 1 and precision == 1,
                          "misses": misses},
            "facts_ok": parts["facts"], "joint": parts["facts"] and recall == 1 and precision == 1}
