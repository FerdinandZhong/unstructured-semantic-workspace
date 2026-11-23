"""Keep explicit source-to-definition corrections outside extracted evidence."""

from dataclasses import dataclass
from hashlib import sha256
import json
import re


ACTIONS = {"replace", "remove", "restore"}
HASH = re.compile(r"[a-f0-9]{64}")
MAX_NOTE = 480
REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{1,80}")


def digest(value):
    return sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class CorrectionRequest:
    dataset: str
    source_id: str
    observation_id: str
    target_id: str | None
    action: str
    revision: str
    request_id: str
    note: str = ""

    @classmethod
    def parse(cls, body):
        keys = ("dataset", "sourceId", "observationId", "targetId", "action",
                "correctionRevision", "requestId")
        if not isinstance(body, dict) or not set(keys) <= set(body) <= {*keys, "note"}:
            raise ValueError("Require dataset, source, observation, target, action and correction revisions")
        note = body.get("note", "")
        if not isinstance(note, str) or len(note) > MAX_NOTE or "\x00" in note:
            raise ValueError(f"Correction note must contain at most {MAX_NOTE} characters")
        if any(not isinstance(body[key], str) or not body[key] or "\x00" in body[key]
               for key in keys if key != "targetId"):
            raise ValueError("Invalid mapping correction identifiers")
        action, target = body["action"], body["targetId"]
        if action not in ACTIONS or (
            action == "replace" and (not isinstance(target, str) or not target or "\x00" in target)
        ) or (action != "replace" and target is not None):
            raise ValueError("Replace requires a definition and remove or restore requires a null target")
        if not HASH.fullmatch(body["correctionRevision"]) or not REQUEST_ID.fullmatch(body["requestId"]):
            raise ValueError("Invalid mapping correction revision or request ID")
        return cls(*(body[key] for key in keys), note.strip())


class CorrectionConflict(ValueError):
    """The client must reload before applying a different correction."""


class CorrectionContext:
    def __init__(self, workspace, principal, source_pins):
        self.dataset = workspace["id"]
        self.denied = set(principal["withheld"])
        self.allowed = self.dataset in principal["datasets"]
        self.sources = {
            source["id"]: source.get("citation", {}).get("sha256") for source in workspace["sources"]
        }
        self.sources.update({key: value for key, value in source_pins.items() if key in self.sources})
        model = workspace["knowledge"]["structured"]
        self.model = model["modelSource"]
        self.definitions = {
            item["id"]: item["sourceId"] for item in model["fields"] + model["metrics"]
        }
        self.definition_revisions = {
            source: self.sources.get(source) for source in self.definitions.values()
        }

    def permits(self, source_id):
        return bool(self.allowed and source_id in self.sources and source_id not in self.denied)

    def permits_target(self, target_id):
        source = self.definitions.get(target_id)
        return self.permits(source) and self.pinned(source)

    def pinned(self, source_id):
        revision = self.sources.get(source_id)
        return isinstance(revision, str) and bool(HASH.fullmatch(revision))

    def anchor(self, mapping, observation):
        source_id = mapping["sourceId"]
        if not observation or not self.permits(source_id) or not self.permits(self.model):
            return None
        if (observation["sourceId"] != source_id or not self.pinned(source_id) or not self.pinned(self.model)
                or observation.get("citation", {}).get("sha256") != self.sources[source_id]):
            return None
        return digest([
            self.dataset, self.model, self.sources[self.model], self.sources[source_id],
            self.definition_revisions, observation["id"], observation["citation"],
            observation["quote"], mapping["id"],
        ])


class MappingCorrections:
    def __init__(self, database):
        self.database = database
        database.sql("""
            CREATE TABLE IF NOT EXISTS v2_mapping_corrections (
                event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                request_id text UNIQUE NOT NULL,
                dataset text NOT NULL,
                anchor text NOT NULL,
                receipt jsonb NOT NULL,
                recorded_at timestamptz NOT NULL DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS v2_mapping_corrections_anchor
                ON v2_mapping_corrections (dataset, anchor, event_id DESC);
        """)

    def latest(self, dataset):
        output = self.database.sql("""
            SELECT COALESCE(jsonb_object_agg(anchor, receipt), '{}'::jsonb)
            FROM (
                SELECT DISTINCT ON (anchor) anchor, receipt || jsonb_build_object('recordedAt', recorded_at) AS receipt
                FROM v2_mapping_corrections
                WHERE dataset = :'dataset'
                ORDER BY anchor, event_id DESC
            ) AS latest;
        """, dataset=dataset)
        return json.loads(output)

    def record(self, request, anchor, actor):
        revision = digest([anchor, request.request_id])
        receipt = {
            "sourceId": request.source_id, "observationId": request.observation_id,
            "action": request.action, "targetId": request.target_id, "actor": actor,
            "previousRevision": request.revision, "correctionRevision": revision,
            **({"note": request.note} if request.note else {}),
        }
        persisted = self.database.sql("""
            BEGIN;
            LOCK TABLE v2_mapping_corrections IN SHARE ROW EXCLUSIVE MODE;
            INSERT INTO v2_mapping_corrections (request_id, dataset, anchor, receipt)
            SELECT :'request_id', :'dataset', :'anchor', :'receipt'::jsonb
            WHERE :'previous' = COALESCE((
                SELECT receipt->>'correctionRevision' FROM v2_mapping_corrections
                WHERE dataset = :'dataset' AND anchor = :'anchor'
                ORDER BY event_id DESC LIMIT 1
            ), :'anchor')
            ON CONFLICT (request_id) DO NOTHING;
            SELECT receipt
            FROM v2_mapping_corrections
            WHERE request_id = :'request_id' AND dataset = :'dataset' AND anchor = :'anchor';
            COMMIT;
        """, request_id=request.request_id, dataset=request.dataset, anchor=anchor,
            receipt=json.dumps(receipt), previous=request.revision)
        if not persisted or json.loads(persisted) != receipt:
            raise CorrectionConflict("Mapping changed. Reload the source evidence before editing.")
        return revision


def overlay_mappings(context, mappings, observations, latest, *, editable):
    observed = {item["id"]: item for item in observations}
    projected = []
    for mapping in mappings:
        anchor = context.anchor(mapping, observed.get(mapping.get("observationId")))
        if anchor is None:
            projected.append(mapping)
            continue
        receipt = latest.get(anchor)
        action = receipt["action"] if receipt else None
        if action == "replace" and not context.permits_target(receipt["targetId"]):
            action = None
        result = dict(mapping)
        if editable or action:
            result.update(originalTargetId=mapping.get("targetId"), correctionAction=action)
        if editable:
            result.update(originalStatus=mapping.get("status"),
                          originalCandidateIds=mapping.get("candidateIds", []))
            result["correctionRevision"] = receipt["correctionRevision"] if receipt else anchor
            if receipt:
                result.update(correctionNote=receipt.get("note", ""), correctionActor=receipt.get("actor"),
                              correctionAt=receipt.get("recordedAt"))
        if action == "replace":
            result.update(targetId=receipt["targetId"], candidateIds=[receipt["targetId"]],
                          basis=["manual_correction"], status="corrected",
                          description="A reviewer selected this definition for the cited evidence.")
        if action == "remove":
            result.update(targetId=None, candidateIds=[], basis=["manual_correction"], status="unmapped",
                          description="A reviewer removed the definition mapping for the cited evidence.")
        projected.append(result)
    return projected
