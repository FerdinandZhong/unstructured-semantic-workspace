"""Read one synthetic order line through an isolated V2 PostgreSQL pilot."""

import argparse
import csv
from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
import io
import json
import os
from pathlib import Path

from ._pilot_postgres import PilotDatabaseError, PilotPostgres


@dataclass(frozen=True)
class LineIdentity:
    scope: str
    customer: int
    order: int
    detail: int

    def parameters(self):
        return {
            "scope": self.scope,
            "customer_id": self.customer,
            "order_id": self.order,
            "detail_id": self.detail,
        }


LINE = LineIdentity("adventureworks_demo", 29641, 51131, 36824)
HEADER = "data/csv/Sales_SalesOrderHeader.csv"
DETAIL = "data/csv/Sales_SalesOrderDetail.csv"
# Prepared export pins from the manufacturing delivery manifest.
SOURCE_HASHES = {
    HEADER: "3bca1acfb9418acc5f7a7ed82138a493fe47e0aff5799f9a282aaa2dd7f52d70",
    DETAIL: "5e743295f200e320561239b0532bc91dcead9c726009334c4f5d5f93c2d54072",
}


@dataclass(frozen=True)
class Fixture:
    identity: LineIdentity
    facts: dict


@dataclass(frozen=True)
class DispatchEvent:
    event_id: str
    dispatch_date: date
    observed_date: date
    units: int


SIMULATION = (
    DispatchEvent("F42-S1", date(2024, 6, 5), date(2024, 6, 10), 10),
    DispatchEvent("F42-S2", date(2024, 6, 12), date(2024, 6, 17), 6),
)


def _source_row(pack: Path, file: str, keys: dict[str, int]):
    raw = (pack / file).read_bytes()
    checksum = sha256(raw).hexdigest()
    if checksum != SOURCE_HASHES[file]:
        raise ValueError(f"prepared source checksum mismatch for {file}")
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8"), newline=""))
    matches = [
        row for row in reader
        if all(row[key] == str(value) for key, value in keys.items())
    ]
    if len(matches) != 1:
        raise ValueError(f"expected one source row for {keys} in {file}")
    return matches[0], {
        "file": file,
        "sha256": checksum,
        "key": keys,
        "dataset": "synthetic Microsoft AdventureWorks OLTP sample",
    }


def load_fixture(pack: Path) -> Fixture:
    header, header_source = _source_row(pack, HEADER, {"SalesOrderID": LINE.order})
    detail, detail_source = _source_row(
        pack, DETAIL, {"SalesOrderID": LINE.order, "SalesOrderDetailID": LINE.detail}
    )
    if int(header["CustomerID"]) != LINE.customer:
        raise ValueError("source order customer does not match the F42 fixture")
    date_fields = {
        "order_date": header["OrderDate"],
        "source_due_date": header["DueDate"],
        "header_ship_date": header["ShipDate"],
    }
    facts = {
        name: datetime.fromisoformat(value).date().isoformat() if value else None
        for name, value in date_fields.items()
    }
    facts.update(
        product_id=int(detail["ProductID"]),
        ordered_units=int(detail["OrderQty"]),
        header_evidence=header_source,
        detail_evidence=detail_source,
    )
    if facts["product_id"] != 954 or facts["ordered_units"] <= 0:
        raise ValueError("source product or ordered quantity is invalid for the F42 fixture")
    return Fixture(LINE, facts)


_SCHEMA = """
BEGIN;
SELECT pg_advisory_xact_lock(51131, 36824);
CREATE TABLE IF NOT EXISTS pilot_order_line (
    scope text NOT NULL,
    customer_id bigint NOT NULL,
    order_id bigint NOT NULL,
    detail_id bigint NOT NULL,
    facts jsonb NOT NULL,
    PRIMARY KEY (scope, customer_id, order_id, detail_id)
);
CREATE TABLE IF NOT EXISTS pilot_dispatch (
    scope text NOT NULL,
    customer_id bigint NOT NULL,
    order_id bigint NOT NULL,
    detail_id bigint NOT NULL,
    event_id text NOT NULL,
    dispatch_date date NOT NULL,
    observed_date date NOT NULL CHECK (observed_date >= dispatch_date),
    units integer NOT NULL CHECK (units > 0),
    PRIMARY KEY (scope, event_id),
    FOREIGN KEY (scope, customer_id, order_id, detail_id)
        REFERENCES pilot_order_line (scope, customer_id, order_id, detail_id)
);
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'f42_reader') THEN
        CREATE ROLE f42_reader NOLOGIN;
    END IF;
END $$;
GRANT SELECT ON pilot_order_line, pilot_dispatch TO f42_reader;
COMMIT;
"""

_INSTALL = r"""
BEGIN;
INSERT INTO pilot_order_line VALUES (
    :'scope', :'customer_id'::bigint, :'order_id'::bigint, :'detail_id'::bigint,
    :'facts'::jsonb
) ON CONFLICT DO NOTHING;
SELECT EXISTS (
    SELECT 1 FROM pilot_order_line
    WHERE (scope, customer_id, order_id, detail_id) =
        (:'scope', :'customer_id'::bigint, :'order_id'::bigint, :'detail_id'::bigint)
    AND facts = :'facts'::jsonb
) AS unchanged
\gset
\if :unchanged
COMMIT;
\else
DO $$ BEGIN RAISE EXCEPTION 'fixture key already has different facts'; END $$;
\endif
"""

_ADD_DISPATCH = r"""
BEGIN;
INSERT INTO pilot_dispatch VALUES (
    :'scope', :'customer_id'::bigint, :'order_id'::bigint, :'detail_id'::bigint,
    :'event_id', :'dispatch_date'::date, :'observed_date'::date, :'units'::integer
) ON CONFLICT DO NOTHING;
SELECT EXISTS (
    SELECT 1 FROM pilot_dispatch
    WHERE (scope, customer_id, order_id, detail_id, event_id) =
        (:'scope', :'customer_id'::bigint, :'order_id'::bigint, :'detail_id'::bigint,
         :'event_id')
    AND (dispatch_date, observed_date, units) =
        (:'dispatch_date'::date, :'observed_date'::date, :'units'::integer)
) AS unchanged
\gset
\if :unchanged
COMMIT;
\else
DO $$ BEGIN RAISE EXCEPTION 'dispatch event key already has different facts'; END $$;
\endif
"""

_READ = """
BEGIN READ ONLY;
SET LOCAL ROLE f42_reader;
SELECT jsonb_build_object(
    'status', 'available',
    'synthetic', true,
    'scope', line.scope,
    'customer_id', line.customer_id,
    'order_id', line.order_id,
    'detail_id', line.detail_id,
    'observation_cutoff', :'cutoff'::date,
    'observation_model', 'frozen source snapshot with date filtering; simulated events use assigned observation dates',
    'order_date', line.facts->'order_date',
    'source_due_date', line.facts->'source_due_date',
    'header_ship_date', CASE
        WHEN (line.facts->>'header_ship_date')::date <= :'cutoff'::date
        THEN line.facts->'header_ship_date'
    END,
    'product_id', line.facts->'product_id',
    'ordered_units', line.facts->'ordered_units',
    'header_evidence', line.facts->'header_evidence',
    'detail_evidence', line.facts->'detail_evidence',
    'dispatch_simulation', :'simulate'::boolean,
    'documented_line_dispatch_units', dispatch.units,
    'line_dispatch_evidence', dispatch.evidence,
    'delivered_units', NULL,
    'delivery_status', 'unknown'
)
FROM pilot_order_line AS line
CROSS JOIN LATERAL (
    SELECT sum(units) AS units, coalesce(jsonb_agg(
        jsonb_build_object(
            'event_id', event_id, 'dispatch_date', dispatch_date,
            'observed_date', observed_date, 'units', units,
            'source', 'synthetic F42 dispatch simulation'
        ) ORDER BY observed_date, event_id
    ), '[]'::jsonb) AS evidence
    FROM pilot_dispatch
    WHERE (scope, customer_id, order_id, detail_id) =
        (line.scope, line.customer_id, line.order_id, line.detail_id)
    AND :'simulate'::boolean
    AND observed_date <= :'cutoff'::date
) AS dispatch
WHERE (line.scope, line.customer_id, line.order_id, line.detail_id) =
    (:'scope', :'customer_id'::bigint, :'order_id'::bigint, :'detail_id'::bigint)
AND (line.facts->>'order_date')::date <= :'cutoff'::date;
COMMIT;
"""


def install_fixture(database: PilotPostgres, fixture: Fixture) -> None:
    if fixture.identity != LINE:
        raise ValueError("only the scoped F42 fixture can be installed")
    database.sql(_SCHEMA)
    database.sql(_INSTALL, **fixture.identity.parameters(), facts=json.dumps(fixture.facts))


def add_simulated_dispatch(
    database: PilotPostgres, identity: LineIdentity, event: DispatchEvent
) -> None:
    if identity != LINE:
        raise ValueError("only the scoped F42 fixture can receive simulated dispatch")
    database.sql(
        _ADD_DISPATCH, **identity.parameters(), event_id=event.event_id,
        dispatch_date=event.dispatch_date.isoformat(),
        observed_date=event.observed_date.isoformat(), units=event.units,
    )


class PostgresOrderLineFacts:
    def __init__(self, database: PilotPostgres):
        self._database = database

    def read(
        self, identity: LineIdentity, cutoff: date, *, simulate_dispatch: bool = False
    ) -> dict:
        output = self._database.sql(
            _READ, **identity.parameters(), cutoff=cutoff.isoformat(),
            simulate=str(simulate_dispatch).lower(),
        )
        return json.loads(output) if output else {"status": "unavailable"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--pg-bin", type=Path, default=os.environ.get("V2_PG_BIN"))
    parser.add_argument("--scope", required=True)
    parser.add_argument("--customer", type=int, required=True)
    parser.add_argument("--order", type=int, required=True)
    parser.add_argument("--detail", type=int, required=True)
    parser.add_argument("--cutoff", type=date.fromisoformat, required=True, action="append")
    parser.add_argument("--simulate-dispatch", action="store_true")
    args = parser.parse_args()
    identity = LineIdentity(args.scope, args.customer, args.order, args.detail)
    try:
        fixture = load_fixture(args.pack)
        with PilotPostgres.start(args.pg_bin) as database:
            install_fixture(database, fixture)
            adapter = PostgresOrderLineFacts(database)
            snapshots = []
            for cutoff in args.cutoff:
                if args.simulate_dispatch:
                    for event in SIMULATION:
                        if event.observed_date <= cutoff:
                            add_simulated_dispatch(database, fixture.identity, event)
                snapshots.append(adapter.read(identity, cutoff, simulate_dispatch=args.simulate_dispatch))
        print(json.dumps(snapshots, indent=2))
    except (OSError, ValueError, KeyError, PilotDatabaseError) as error:
        parser.exit(1, f"F42 pilot failed: {error}\n")


if __name__ == "__main__":
    main()
