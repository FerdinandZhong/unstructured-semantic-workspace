"""One parser for the periods a question states (2026-11-22).

Each period resolves to an inclusive range of ISO days: a day, a range of days, a month, a month-end, a quarter, a
half year or a year (introduced or bare). Wording that names a period without an anchor ("last year", "in October",
"Q3") is returned as unresolved; a caller decides whether it matters. Nothing is guessed: a month or a quarter needs
its year, and relative words need a date the question does not give.
"""

from __future__ import annotations

import calendar
import datetime
import re

_MONTHS = {}
for _i in range(1, 13):
    _MONTHS[calendar.month_name[_i].lower()] = _i
    _MONTHS[calendar.month_abbr[_i].lower()] = _i
_MONTHS["sept"] = 9
_MON = "|".join(sorted(_MONTHS, key=len, reverse=True))
_YEAR = r"(?:19|20)\d{2}"
_ORD = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4, "last": 4}
_ORD_ALT = "|".join(_ORD)
_DAY = rf"(?:\d{{4}}-\d{{2}}-\d{{2}}|(?:the\s+)?\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:{_MON})\.?,?\s+{_YEAR}|(?:{_MON})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+{_YEAR})"

_PATTERNS = [  # (kind, regex); the first that matches at a position wins, longer forms first
    ("range", re.compile(rf"\b(?:between\s+|from\s+)?(?P<a>{_DAY})\s+(?:and|to|until|through|-|–)\s+(?P<b>{_DAY})", re.I)),
    # one year after two days of two months: "1 July and 30 September 2026", "March 1 to May 31, 2024"
    ("range", re.compile(rf"\b(?:between\s+|from\s+)?(?:the\s+)?(?P<e1>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<n1>{_MON})\.?\s+"
                         rf"(?:and|to|until|through|-|–)\s+(?:the\s+)?(?P<e2>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<n2>{_MON})\.?,?\s+"
                         rf"(?P<y3>{_YEAR})\b"
                         rf"|\b(?:between\s+|from\s+)?(?P<n3>{_MON})\.?\s+(?P<e3>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:and|to|until|through|-|–)\s+"
                         rf"(?P<n4>{_MON})\.?\s+(?P<e4>\d{{1,2}})(?:st|nd|rd|th)?,?\s+(?P<y4>{_YEAR})\b", re.I)),
    ("range", re.compile(rf"\b(?:between\s+|from\s+)?(?:the\s+)?(?P<d1>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:and|to|-|–)\s+(?:the\s+)?(?P<d2>\d{{1,2}})"
                         rf"(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<m>{_MON})\.?,?\s+(?P<y>{_YEAR})\b", re.I)),
    # a month or quarter named by its last day: "the month ending 31 May 2024", "the quarter ended 30 June 2024"
    ("ending", re.compile(rf"\b(?:the\s+)?(?P<unit>month|quarter)\s+(?:ending|ended|to)\s+(?:on\s+)?(?P<a>{_DAY})", re.I)),
    ("month_end", re.compile(rf"\b(?:the\s+)?(?:end\s+of|month-end|month\s+end)\s+(?P<m>{_MON})\.?,?\s+(?P<y>{_YEAR})\b", re.I)),
    ("quarter", re.compile(rf"\b(?:the\s+)?(?P<o>{_ORD_ALT})\s+quarter\s+(?:of\s+)?(?P<y>{_YEAR})\b", re.I)),
    ("quarter", re.compile(rf"\bQ(?P<q>[1-4])\s*(?P<y>{_YEAR})\b|\b(?P<y2>{_YEAR})\s*Q(?P<q2>[1-4])\b", re.I)),
    ("half", re.compile(rf"\b(?:the\s+)?(?P<o>first|second|1st|2nd)\s+half\s+(?:of\s+)?(?P<y>{_YEAR})\b|\bH(?P<h>[12])\s*(?P<y2>{_YEAR})\b", re.I)),
    ("day", re.compile(rf"\b(?P<a>{_DAY})\b", re.I)),
    ("month", re.compile(rf"\b(?P<m>{_MON})\.?,?\s+(?P<y>{_YEAR})\b", re.I)),
    ("year", re.compile(rf"\b(?:calendar\s+)?(?:year\s+)?(?<![\d-])(?<!\d[.,/])(?P<y>{_YEAR})(?![\d-]|[.,/]\d|\s*(?:units?|pcs|items?)\b)", re.I)),
    ("today", re.compile(r"\btoday\b", re.I)),
]
_OPEN = re.compile(r"\b(?:since|until|till|before|after|by|prior\s+to)\s*$", re.I)
_UNRESOLVED = re.compile(
    rf"\b(?:{'|'.join(n for n in _MONTHS if len(n) > 3 and n != 'sept')})\b"
    r"|\bq[1-4]\b|\bquarter\b|\bhalf[- ]year\b"
    r"|\b(?:yesterday|tomorrow|tonight|ytd|mtd|qtd|recent(?:ly)?)\b"
    r"|\b(?:last|this|next|previous|current|past|prior|coming)\s+(?:week|month|quarter|year|fortnight|day)s?\b"
    r"|\b\d+\s+(?:days?|weeks?|months?|years?)\s+ago\b"
    r"|\b\d{1,4}\s*/\s*\d{1,2}\s*/\s*\d{1,4}\b",
    re.I)


def _day(text):
    text = text.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return datetime.date.fromisoformat(text)
    m = re.fullmatch(rf"(?:the\s+)?(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MON})\.?,?\s+({_YEAR})", text, re.I) or None
    if m:
        return datetime.date(int(m[3]), _MONTHS[m[2].lower()], int(m[1]))
    m = re.fullmatch(rf"({_MON})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+({_YEAR})", text, re.I)
    return datetime.date(int(m[3]), _MONTHS[m[1].lower()], int(m[2]))


def _month(year, month):
    return datetime.date(year, month, 1), datetime.date(year, month, calendar.monthrange(year, month)[1])


def _resolve(kind, m, today):
    g = m.groupdict()
    if kind == "range":
        if g.get("a"):
            return _day(g["a"]), _day(g["b"])
        for d1, m1, d2, m2, y in (("e1", "n1", "e2", "n2", "y3"), ("e3", "n3", "e4", "n4", "y4")):
            if g.get(y):
                year = int(g[y])
                return (datetime.date(year, _MONTHS[g[m1].lower()], int(g[d1])),
                        datetime.date(year, _MONTHS[g[m2].lower()], int(g[d2])))
        month, year = _MONTHS[g["m"].lower()], int(g["y"])
        return datetime.date(year, month, int(g["d1"])), datetime.date(year, month, int(g["d2"]))
    if kind == "day":
        day = _day(g["a"])
        return day, day
    if kind == "ending":
        day = _day(g["a"])
        months = 1 if g["unit"].lower() == "month" else 3
        first = datetime.date(day.year, day.month, 1)
        for _ in range(months - 1):
            first = datetime.date(first.year - (first.month == 1), (first.month - 2) % 12 + 1, 1)
        return first, day
    if kind == "month_end":
        end = _month(int(g["y"]), _MONTHS[g["m"].lower()])[1]
        return end, end
    if kind == "month":
        return _month(int(g["y"]), _MONTHS[g["m"].lower()])
    if kind == "quarter":
        q = _ORD[g["o"].lower()] if g.get("o") else int(g.get("q") or g.get("q2"))
        year = int(g.get("y") or g.get("y2"))
        return datetime.date(year, 3 * q - 2, 1), _month(year, 3 * q)[1]
    if kind == "half":
        h = 1 if (g.get("o") or "").lower() in ("first", "1st") or g.get("h") == "1" else 2
        year = int(g.get("y") or g.get("y2"))
        return (datetime.date(year, 1, 1), datetime.date(year, 6, 30)) if h == 1 else (datetime.date(year, 7, 1), datetime.date(year, 12, 31))
    if kind == "year":
        year = int(g["y"])
        return datetime.date(year, 1, 1), datetime.date(year, 12, 31)
    day = datetime.date.fromisoformat(today) if today else datetime.date.today()
    return day, day


def parse_periods(text, *, today=None):
    """([{text, kind, from, to, start, end}], [unresolved wording]). `from` and `to` are inclusive ISO days; `start`
    and `end` are character offsets in `text`. A span an earlier period covers is never read twice."""
    taken, found, unresolved = [], [], []
    for kind, pattern in _PATTERNS:
        for m in pattern.finditer(text):
            if any(m.start() < e and s < m.end() for s, e in taken):
                continue
            taken.append((m.start(), m.end()))
            try:
                start, end = _resolve(kind, m, today)
            except (ValueError, KeyError, TypeError):
                start = end = None    # an impossible date (31 February) is not a period, and not its month either
            if start is None or start > end or _OPEN.search(text[:m.start()]):
                unresolved.append(m.group(0).strip())   # an open-ended period ("since 2024") has no range to apply
                continue
            found.append({"text": m.group(0).strip(), "kind": kind, "from": start.isoformat(), "to": end.isoformat(),
                          "start": m.start(), "end": m.end()})
    found.sort(key=lambda p: p["start"])
    rest = list(text)
    for s, e in taken:
        rest[s:e] = " " * (e - s)
    unresolved += [m.group(0).strip() for m in _UNRESOLVED.finditer("".join(rest))]
    return found, unresolved


def blank(text, periods):
    """The text with each period's words replaced by spaces."""
    out = list(text)
    for p in periods:
        out[p["start"]:p["end"]] = " " * (p["end"] - p["start"])
    return "".join(out)
