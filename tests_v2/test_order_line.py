"""Acceptance cases for the private PostgreSQL order-line pilot."""

from dataclasses import replace
from datetime import date
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from semantic_pipeline_v2._pilot_postgres import PilotDatabaseError, PilotPostgres
from semantic_pipeline_v2.order_line import (
    HEADER,
    LINE,
    SIMULATION,
    SOURCE_HASHES,
    PostgresOrderLineFacts,
    add_simulated_dispatch,
    install_fixture,
    load_fixture,
)


PACK = Path(__file__).resolve().parents[1] / "demo_data/manufacturing"
FIRST_CUTOFF = date(2024, 6, 10)
SECOND_CUTOFF = date(2024, 6, 17)


@pytest.fixture(scope="module")
def fixture():
    return load_fixture(PACK)


@pytest.fixture(scope="module")
def pg_bin():
    value = os.environ.get("V2_PG_BIN")
    if not value:
        pytest.skip("set V2_PG_BIN for private PostgreSQL integration tests")
    return Path(value)


@pytest.fixture(scope="module")
def database(pg_bin, fixture):
    with PilotPostgres.start(pg_bin) as cluster:
        install_fixture(cluster, fixture)
        socket_directory = Path(cluster.sql("SHOW unix_socket_directories;"))
        assert socket_directory.stat().st_mode & 0o777 == 0o700
        assert (socket_directory / ".s.PGSQL.5432").is_socket()
        yield cluster
    assert not socket_directory.exists()


def test_prepared_source_pins_and_scope(fixture, tmp_path):
    """OF1. Source rows match the prepared pack pins and F42 identity."""
    assert fixture.identity == LINE
    assert fixture.facts["ordered_units"] == 16
    assert fixture.facts["detail_evidence"]["key"] == {
        "SalesOrderID": 51131, "SalesOrderDetailID": 36824,
    }
    for evidence in ("header_evidence", "detail_evidence"):
        record = fixture.facts[evidence]
        assert record["sha256"] == SOURCE_HASHES[record["file"]]

    changed_file = tmp_path / HEADER
    changed_file.parent.mkdir(parents=True)
    changed_file.write_bytes((PACK / HEADER).read_bytes() + b"\n")
    with pytest.raises(ValueError, match="checksum mismatch"):
        load_fixture(tmp_path)


def test_real_postgres_reads_only_scoped_line(database):
    """OF1 and OF2. Bound request keys and dates determine fact visibility."""
    adapter = PostgresOrderLineFacts(database)
    result = adapter.read(LINE, FIRST_CUTOFF)
    assert result["ordered_units"] == 16
    assert result["product_id"] == 954
    assert result["source_due_date"] == "2024-06-10"
    assert result["header_ship_date"] == "2024-06-05"
    assert result["header_evidence"]["key"] == {"SalesOrderID": 51131}
    assert result["line_dispatch_evidence"] == []
    assert result["documented_line_dispatch_units"] is None
    assert result["delivery_status"] == "unknown"
    assert result["delivered_units"] is None
    assert adapter.read(LINE, date(2024, 6, 4))["header_ship_date"] is None
    assert adapter.read(LINE, date(2024, 6, 5))["header_ship_date"] == "2024-06-05"
    assert adapter.read(LINE, date(2024, 5, 28)) == {"status": "unavailable"}
    assert adapter.read(LINE, date(2024, 5, 29))["ordered_units"] == 16
    for wrong_identity in (
        replace(LINE, scope="other"),
        replace(LINE, scope="adventureworks_demo' OR true --"),
        replace(LINE, order=51130),
        replace(LINE, customer=29642),
        replace(LINE, detail=36825),
    ):
        assert adapter.read(wrong_identity, FIRST_CUTOFF) == {"status": "unavailable"}
    assert database.sql("SHOW listen_addresses;") == ""
    with pytest.raises(PilotDatabaseError, match="permission denied"):
        database.sql("SET ROLE f42_reader; DELETE FROM pilot_order_line;")


def test_real_postgres_idempotence_cutoffs_and_conflict(database, fixture):
    """OF3 and OF4. An additive dispatch leaves earlier reads and source facts stable."""
    adapter = PostgresOrderLineFacts(database)
    install_fixture(database, fixture)
    with pytest.raises(PilotDatabaseError, match="fixture key already has different facts"):
        install_fixture(database, replace(fixture, facts={**fixture.facts, "ordered_units": 17}))
    with pytest.raises(ValueError, match="scoped F42 fixture"):
        install_fixture(database, replace(fixture, identity=replace(LINE, scope="other")))

    first, second = SIMULATION
    with pytest.raises(ValueError, match="scoped F42 fixture"):
        add_simulated_dispatch(database, replace(LINE, order=51130), first)
    for _ in range(2):
        add_simulated_dispatch(database, LINE, first)
    early = adapter.read(LINE, FIRST_CUTOFF, simulate_dispatch=True)
    assert early["observation_cutoff"] == "2024-06-10"
    assert early["documented_line_dispatch_units"] == 10
    assert [event["event_id"] for event in early["line_dispatch_evidence"]] == ["F42-S1"]

    for _ in range(2):
        add_simulated_dispatch(database, LINE, second)
    with pytest.raises(PilotDatabaseError, match="dispatch event key already has different facts"):
        add_simulated_dispatch(database, LINE, replace(second, units=7))
    later = adapter.read(LINE, SECOND_CUTOFF, simulate_dispatch=True)
    assert later["documented_line_dispatch_units"] == 16
    assert [event["event_id"] for event in later["line_dispatch_evidence"]] == [
        "F42-S1", "F42-S2",
    ]
    assert later["delivery_status"] == "unknown"
    assert later["delivered_units"] is None
    assert later["ordered_units"] == 16
    assert adapter.read(LINE, FIRST_CUTOFF, simulate_dispatch=True) == early
    assert adapter.read(LINE, date(2024, 6, 12), simulate_dispatch=True) == {
        **early, "observation_cutoff": "2024-06-12",
    }
    hidden = adapter.read(LINE, SECOND_CUTOFF)
    assert hidden["documented_line_dispatch_units"] is None
    assert hidden["line_dispatch_evidence"] == []


@pytest.mark.parametrize("simulate", [False, True])
def test_real_postgres_cli(pg_bin, simulate):
    """OF5. The normal entry point uses real PostgreSQL with explicit simulation."""
    command = [
        sys.executable, "-m", "semantic_pipeline_v2.order_line",
        "--pack", str(PACK), "--scope", LINE.scope, "--customer", str(LINE.customer),
        "--order", str(LINE.order), "--detail", str(LINE.detail),
        "--cutoff", "2024-06-10", "--cutoff", "2024-06-17",
    ]
    if simulate:
        command.append("--simulate-dispatch")
    environment = {
        **os.environ, "V2_PG_BIN": str(pg_bin), "PGHOSTADDR": "192.0.2.1",
        "PGDATABASE": "unrelated_db", "PGOPTIONS": "-c default_transaction_read_only=on",
    }
    result = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    snapshots = json.loads(result.stdout)
    expected = [10, 16] if simulate else [None, None]
    assert [row["documented_line_dispatch_units"] for row in snapshots] == expected
    assert [row["delivered_units"] for row in snapshots] == [None, None]


def test_real_postgres_removes_private_cluster_on_failure(pg_bin):
    """OF5. An exception still stops PostgreSQL and removes the private socket."""
    with pytest.raises(PilotDatabaseError, match="division by zero"):
        with PilotPostgres.start(pg_bin) as cluster:
            socket_directory = Path(cluster.sql("SHOW unix_socket_directories;"))
            assert (socket_directory / ".s.PGSQL.5432").is_socket()
            cluster.sql("SELECT 1 / 0;")
    assert not socket_directory.exists()
