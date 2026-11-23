"""Mask restricted identifiers at the response boundary.

The data model says of account numbers, customer IDs and relationship manager IDs: never display the value. The
values a reply may hold are the values those columns hold, so each one is replaced wherever it stands as a whole
token. Source identities (`id`, `sourceId`, hashes) are left intact, as in `mask_extracted`."""
import json
import re
import time

from ._pilot_postgres import PilotDatabaseError
from .extracted_fields import _PASSTHROUGH

# ponytail: columns are picked by name; read the model's "never render" access marker if a dataset names them otherwise.
_COLUMNS = {"account": re.compile(r"Account(Number|No)|AccNum|CounterpartyAccountNo"),
            "customer": re.compile(r"CustomerID"), "manager": re.compile(r"RMID")}
_KEY = re.compile("|".join(p.pattern for p in _COLUMNS.values()), re.I)


def columns(workspace):
    """[(kind, table, column)] of the restricted identifier columns of a workspace's model."""
    out = []
    for field in workspace["knowledge"]["structured"]["fields"]:
        table, _, column = field["name"].partition("/")
        out += [(kind, table, column) for kind, pattern in _COLUMNS.items() if pattern.fullmatch(column)]
    return out


def values(workspaces, keys_of):
    """{kind: set of values} over every workspace. `keys_of(workspace)` gives its EntityKeys."""
    out = {kind: set() for kind in _COLUMNS}
    for workspace in workspaces:
        for kind, table, column in columns(workspace):
            out[kind] |= {str(v) for v in keys_of(workspace).values(table, column) if len(str(v)) > 4}
    return out


def stated(store, workspace):
    """{kind: values} that evidence links state for a restricted column, such as a customer ID said on a call that
    no table holds. A value needs a letter or eight digits: a short bare number may be a misread amount."""
    pairs = json.loads(store.database.sql("""
        SELECT COALESCE(jsonb_agg(DISTINCT jsonb_build_array(definition_id, stated_value)), '[]'::jsonb)
        FROM v2_evidence_links
        WHERE dataset = :'dataset' AND relation = 'states_value_of' AND stated_value IS NOT NULL;""", dataset=workspace["id"]))
    kinds = {f"/field/{table}/{column}": kind for kind, table, column in columns(workspace)}
    out = {kind: set() for kind in _COLUMNS}
    for definition, value in pairs:
        kind = next((k for tail, k in kinds.items() if str(definition).endswith(tail)), None)
        if kind and re.fullmatch(r"(?=.*\d)(?=.*[A-Za-z]|\d{8})[A-Za-z0-9-]{5,40}", str(value)):
            out[kind].add(str(value))
    return out


def current(server, keys_of):
    """{kind: values} to mask now: what the tables hold (read once) and what documents state (re-read every 20 s)."""
    if getattr(server, "identifier_values", None) is None:
        server.identifier_values = values(server.workspaces.values(), keys_of)
    found, store = server.identifier_values, getattr(server, "evidence_links", None)
    if store is None:
        return found
    at, extra = getattr(server, "identifier_stated", (None, {}))
    if at is None or time.monotonic() - at > 20:      # ponytail: a 20 s window; refresh on link writes if that matters
        try:
            extra = {}
            for workspace in server.workspaces.values():
                for kind, more in stated(store, workspace).items():
                    extra[kind] = extra.get(kind, set()) | more
        except PilotDatabaseError:
            extra = server.identifier_stated[1] if at is not None else {}      # keep the last known values
        server.identifier_stated = (time.monotonic(), extra)
    return {kind: held | extra.get(kind, set()) for kind, held in found.items()}


def _masked(value):
    value = str(value)
    return "•••• " + value[-4 if len(value) >= 8 else -2:]


# A whole token that holds a digit, as every identifier does. ASCII classes only: `\w` also matches Chinese
# characters, so an identifier written next to Chinese text with no space would not stand as a token.
_TOKEN = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]*[0-9][A-Za-z0-9_-]*(?![A-Za-z0-9_-])")
_DIGIT = re.compile(r"\d")


def mask(value, found, keep=()):
    """`value` with every restricted identifier masked. `found`: {kind: values}. `keep`: kinds left as written."""
    hidden = set().union(*(v for kind, v in found.items() if kind not in keep))
    if not hidden:
        return value

    def swap(m):
        token, text = m.group(), m.string
        if token not in hidden:
            return token
        # A digit-only value is not part of a decimal or grouped number.
        if token.isdigit() and (re.fullmatch(r"\d[.,]", text[max(m.start() - 2, 0):m.start()])
                                or re.fullmatch(r"[.,]\d", text[m.end():m.end() + 2])):
            return token
        return _masked(token)

    def walk(item, key=""):
        if isinstance(item, str):
            return _TOKEN.sub(swap, item) if _DIGIT.search(item) else item
        if isinstance(item, dict):
            return {k: v if k in _PASSTHROUGH else walk(v, k) for k, v in item.items()}
        if isinstance(item, list):
            return [walk(v, key) for v in item]
        if isinstance(item, int) and not isinstance(item, bool) and str(item) in hidden and _KEY.fullmatch(str(key)):
            return _masked(item)
        return item
    return walk(value)
