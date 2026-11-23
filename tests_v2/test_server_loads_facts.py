"""Found on 22 November 2026: only the test fixtures loaded the structured tables. A real server state had no
`v2_facts_*` tables, so every database question was refused as "query failed". The server now loads them at start."""
import os
from pathlib import Path

import pytest

from semantic_pipeline_v2 import workspace_server
from semantic_pipeline_v2.workspace import review_database


def test_a_fresh_server_state_gets_the_structured_tables(tmp_path):
    if not os.environ.get("V2_PG_BIN"):
        pytest.skip("set V2_PG_BIN")
    with review_database(tmp_path / "state", Path(os.environ["V2_PG_BIN"])) as database:
        said = []
        workspace_server.load_facts(database, say=said.append)
        assert int(database.sql("SELECT count(*) FROM v2_facts_financial_services.customers;").strip()) > 0
        assert int(database.sql("SELECT count(*) FROM v2_facts_manufacturing.production_workorder;").strip()) > 0
        workspace_server.load_facts(database, say=said.append)   # idempotent
        assert any("financial-services" in line for line in said)
