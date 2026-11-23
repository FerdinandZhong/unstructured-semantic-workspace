"""PostgreSQL-backed permissions for the loopback V2 demonstration."""

from copy import deepcopy
from hashlib import sha256
import json
import re
from secrets import token_urlsafe

from .workspace import scoped


COOKIE = "v2_demo_session"
SESSION_SECONDS = 8 * 3600
ACCOUNTS = (
    ("admin", "Admin", "admin", ("financial-services", "manufacturing")),
    ("finance_reader", "Financial Services reader", "reader", ("financial-services",)),
    ("finance_restricted", "Financial Services restricted reader", "restricted", ("financial-services",)),
    ("manufacturing_reader", "Manufacturing reader", "reader", ("manufacturing",)),
    ("manufacturing_restricted", "Manufacturing restricted reader", "restricted", ("manufacturing",)),
)
RESTRICTIONS = {
    "finance_restricted": ("financial-services", ("jade-adverse-media", "cleaned_audio", "jade_call")),
    "manufacturing_restricted": ("manufacturing", ("MFG-003", "MFG-007")),
}
CONFIGURE = ["upload", "process", "review", "publish", "build_graph", "build_ontology"]


def initialize_access(database):
    database.sql("""
        BEGIN;
        CREATE TABLE IF NOT EXISTS v2_demo_users (
            id text PRIMARY KEY, display_name text NOT NULL, role text NOT NULL
                CHECK (role IN ('admin', 'reader', 'restricted'))
        );
        CREATE TABLE IF NOT EXISTS v2_demo_grants (
            user_id text REFERENCES v2_demo_users(id), dataset text,
            PRIMARY KEY (user_id, dataset)
        );
        CREATE TABLE IF NOT EXISTS v2_demo_restrictions (
            user_id text REFERENCES v2_demo_users(id), source_id text,
            PRIMARY KEY (user_id, source_id)
        );
        CREATE TABLE IF NOT EXISTS v2_demo_sessions (
            token_hash text PRIMARY KEY, user_id text NOT NULL REFERENCES v2_demo_users(id),
            expires_at timestamptz NOT NULL
        );
        COMMIT;
    """)
    for user_id, display_name, role, datasets in ACCOUNTS:
        denied = []
        if user_id in RESTRICTIONS:
            dataset, keys = RESTRICTIONS[user_id]
            denied = [scoped(dataset, "source", key) for key in keys]
        database.sql("""
            WITH new_user AS (
                INSERT INTO v2_demo_users VALUES (:'user_id', :'display_name', :'role')
                ON CONFLICT DO NOTHING
                RETURNING id
            ), grants AS (
                INSERT INTO v2_demo_grants
                SELECT id, dataset FROM new_user
                CROSS JOIN jsonb_array_elements_text(:'datasets'::jsonb) AS dataset
            )
            INSERT INTO v2_demo_restrictions
            SELECT id, source_id FROM new_user
            CROSS JOIN jsonb_array_elements_text(:'denied'::jsonb) AS source_id;
        """, user_id=user_id, display_name=display_name, role=role,
                     datasets=json.dumps(datasets), denied=json.dumps(denied))


_USERS = """
SELECT jsonb_build_object('id', u.id, 'displayName', u.display_name, 'role', u.role,
    'datasets', COALESCE((SELECT jsonb_agg(g.dataset ORDER BY g.dataset)
        FROM v2_demo_grants g WHERE g.user_id = u.id), '[]'::jsonb),
    'withheld', COALESCE((SELECT jsonb_agg(r.source_id)
        FROM v2_demo_restrictions r WHERE r.user_id = u.id), '[]'::jsonb))
FROM v2_demo_users u
"""


def token_hash(token):
    return sha256(token.encode("ascii")).hexdigest() if token and re.fullmatch(r"[\w-]{43}", token, re.ASCII) else None


def resolve_session(database, token):
    hashed = token_hash(token)
    if hashed is None:
        return None
    output = database.sql(_USERS + """
        JOIN v2_demo_sessions s ON s.user_id = u.id
        WHERE s.token_hash = :'token_hash' AND s.expires_at > now();
    """, token_hash=hashed)
    return json.loads(output) if output else None


def new_session(database, user_id, previous=None, *, explicit=False):
    token = token_urlsafe(32)
    hashed = token_hash(token)
    if previous is None:
        if user_id != "admin" and not explicit:
            raise ValueError("Only the initial demo entry can bootstrap a session")
        inserted = database.sql("""
            INSERT INTO v2_demo_sessions
            SELECT :'token_hash', id, now() + interval '8 hours'
            FROM v2_demo_users WHERE id = :'user_id'
            RETURNING user_id;
        """, token_hash=hashed, user_id=user_id)
    else:
        previous_hash = token_hash(previous)
        if previous_hash is None:
            return None
        inserted = database.sql("""
            WITH previous AS (
                DELETE FROM v2_demo_sessions
                WHERE token_hash = :'previous_hash' AND expires_at > now()
                    AND EXISTS (SELECT 1 FROM v2_demo_users WHERE id = :'user_id')
                RETURNING token_hash
            )
            INSERT INTO v2_demo_sessions
            SELECT :'token_hash', :'user_id', now() + interval '8 hours' FROM previous
            RETURNING user_id;
        """, token_hash=hashed, previous_hash=previous_hash, user_id=user_id)
    return token if inserted else None


def session_payload(database, principal):
    accounts = [json.loads(line) for line in database.sql(_USERS + """
        ORDER BY CASE WHEN u.role = 'admin' THEN 0 ELSE 1 END, u.id;
    """).splitlines()]
    public = lambda account: {key: value for key, value in account.items() if key != "withheld"}
    return {
        "principal": {
            **public(principal),
            "capabilities": ["query", "view_insights", *(CONFIGURE if principal["role"] == "admin" else [])],
        },
        "accounts": [public(account) for account in accounts],
        "demo": True,
    }


def demo_accounts():
    return [
        {"id": user_id, "displayName": label, "role": role, "datasets": list(datasets)}
        for user_id, label, role, datasets in ACCOUNTS
    ]


def project_workspace(payload, principal):
    """Return permitted content without modifying the prepared catalog."""
    denied = set(principal["withheld"])
    datasets = []
    for prepared in payload["datasets"]:
        if prepared["id"] not in principal["datasets"]:
            continue
        record = deepcopy(prepared)
        for source in record["sources"]:
            if source["id"] in denied:
                identity = {key: source[key] for key in ("id", "title", "kind")}
                source.clear()
                source.update(identity)
                source.update(access="withheld", examples=[], description="Source content withheld for this account.")
            else:
                source["access"] = "allowed"
        knowledge = record["knowledge"]
        withheld_ids = {item["id"] for item in knowledge["observations"] if item["sourceId"] in denied}
        knowledge["observations"] = [item for item in knowledge["observations"] if item["id"] not in withheld_ids]
        if "passages" in knowledge:
            knowledge["passages"] = [item for item in knowledge["passages"]
                                     if item["sourceId"] not in denied and withheld_ids.isdisjoint(item["observationIds"])]
        for mapping in knowledge["mappings"]:
            if mapping.get("sourceId") in denied or mapping.get("observationId") in withheld_ids:
                mapping.pop("observationId", None)
                mapping.update(access="withheld", description="Source content withheld for this account.")
        for entity in record["entities"]:
            entity["observationIds"] = [item for item in entity["observationIds"] if item not in withheld_ids]
            if "answer" in entity:
                answer = entity["answer"]
                retained = [item for item in answer["observationIds"] if item not in withheld_ids]
                answer["withheldCitationCount"] = len(answer["observationIds"]) - len(retained)
                if answer["withheldCitationCount"]:
                    answer["text"] = (
                        "The original schedule identifies line 36824 and 16 units. Receipt remains unknown."
                        if record["id"] == "manufacturing" and retained
                        else "The supporting report is withheld. Available evidence cannot establish report-based risk."
                    )
                answer["observationIds"] = retained
        datasets.append(record)
    return {**payload, "datasets": datasets,
            "reviewAvailable": payload["reviewAvailable"] and principal["role"] == "admin"}


def project_fixtures(fixtures, principal):
    projected = {}
    for dataset in principal["datasets"]:
        if dataset not in fixtures:
            continue
        fixture = deepcopy(fixtures[dataset])
        withheld = set()
        for source in fixture["sources"]:
            if scoped(dataset, "source", source.get("preparedId", "")) in principal["withheld"]:
                withheld.add(source["id"])
                for key in ("quote", "excerpt", "locator"):
                    source.pop(key, None)
                source.update(access="withheld", detail="Source content withheld for this account.")
        for node in fixture["trace"]:
            if node.get("source") in withheld:
                node.update(detail="Source content withheld", text="", access="withheld")
        proposal = fixture["proposal"]
        if proposal.get("source") in withheld:
            proposal.update(detail="Source content withheld", rationale="", effect="", access="withheld")
        entity = fixture["entity"]
        if entity.get("reference") in withheld:
            entity["answer"] = "Some source evidence is withheld. The structured facts remain available."
            entity["reference"] = None
        for key, conclusion in (("baselineSource", "baseline"), ("currentSource", "current")):
            if entity.get(key) in withheld:
                entity["conclusions"][conclusion] = "The source evidence for this conclusion is withheld."
                entity[key] = None
        projected[dataset] = fixture
    return projected
