"""Iceberg writes through Trino (user decision of 23 November 2026: the Spark job's catalog identity may not create
tables in lakehouse.demo_silver; the platform user may, through Trino). No test contacts Trino: `run` is a fake."""
import httpx
import pytest

from semantic_pipeline_v2 import trino_target
from semantic_pipeline_v2.ask_actions import SPARK_DOWNLOAD, Writer
from semantic_pipeline_v2.iceberg_target import IcebergTarget
from semantic_pipeline_v2.trino_target import TrinoTarget, statements
from test_ask_actions import form_ctx, form_pg  # noqa: F401  (fixture)
from test_iceberg_target import SETTINGS, TOKEN, Cde
from test_output_c_sources import DATASET, call_ctx, link

FORM = {"target": "lakehouse.demo_silver.ext_str_transaction", "expected": 1,
        "columns": [["accountno", "STRING"], ["amountoriginal", "DECIMAL(38,6)"], ["startdate", "DATE"], ["transactionseq", "BIGINT"],
                    ["v2_source_id", "STRING"], ["v2_source_revision", "STRING"], ["v2_row_key", "STRING"],
                    ["v2_written_at", "TIMESTAMP"], ["v2_principal", "STRING"]],
        "keys": ["v2_source_id", "v2_source_revision", "v2_row_key"], "nullSafe": False,
        "rows": [["O'Brien", "12.50", "2025-09-03", "1", "s", "r", "1", "2026-11-23 04:00:00", "admin"]]}


def test_the_statements_create_the_table_and_insert_only_missing_rows():
    ddl, insert, check = statements(FORM)
    assert ddl == ("CREATE TABLE IF NOT EXISTS lakehouse.demo_silver.ext_str_transaction (accountno varchar, "
                   "amountoriginal decimal(38,6), startdate date, transactionseq bigint, v2_source_id varchar, "
                   "v2_source_revision varchar, v2_row_key varchar, v2_written_at timestamp(6), v2_principal varchar)")
    assert insert.startswith("INSERT INTO lakehouse.demo_silver.ext_str_transaction SELECT CAST(s.accountno AS varchar), "
                             "CAST(s.amountoriginal AS decimal(38,6)), CAST(s.startdate AS date)")
    assert "(VALUES ('O''Brien', '12.50', '2025-09-03', '1', 's', 'r', '1', '2026-11-23 04:00:00', 'admin')) AS s(accountno," in insert
    key_t = ("coalesce(CAST(t.v2_source_id AS varchar), chr(0)) || chr(1) || coalesce(CAST(t.v2_source_revision AS varchar), chr(0))"
             " || chr(1) || coalesce(CAST(t.v2_row_key AS varchar), chr(0))")
    key_s = key_t.replace("CAST(t.", "CAST(CAST(s.").replace(" AS varchar), chr(0))", " AS varchar) AS varchar), chr(0))")
    assert insert.endswith(f"WHERE NOT EXISTS (SELECT 1 FROM lakehouse.demo_silver.ext_str_transaction t WHERE {key_t} = {key_s})")
    assert check.startswith("SELECT count(*) FROM lakehouse.demo_silver.ext_str_transaction t WHERE EXISTS (SELECT 1 FROM (VALUES")


def test_null_values_and_null_safe_keys():
    table = {**FORM, "keys": ["accountno", "amountoriginal"], "nullSafe": True,
             "rows": [[None, "1", None, "1", "s", "r", "1", "2026-11-23 04:00:00", "admin"]]}
    _, insert, _ = statements(table)
    assert "(VALUES (CAST(NULL AS varchar), '1', CAST(NULL AS varchar), '1'," in insert
    # one combined key and a single "=": NULL is chr(0), and a typed column is cast to its type before it is compared
    assert insert.endswith("WHERE coalesce(CAST(t.accountno AS varchar), chr(0)) || chr(1) || coalesce(CAST(t.amountoriginal AS varchar), chr(0))"
                           " = coalesce(CAST(CAST(s.accountno AS varchar) AS varchar), chr(0)) || chr(1) || "
                           "coalesce(CAST(CAST(s.amountoriginal AS decimal(38,6)) AS varchar), chr(0)))")
    assert "IS NOT DISTINCT FROM" not in insert       # several of them over a multi-row VALUES list miscounted on Trino


def test_a_name_that_is_not_a_plain_identifier_is_refused():
    with pytest.raises(ValueError):
        statements({**FORM, "target": "lakehouse.demo_silver.x; DROP TABLE y"})
    with pytest.raises(ValueError):
        statements({**FORM, "columns": [["a b", "STRING"]], "rows": [["1"]], "keys": ["a b"]})


def test_write_runs_the_three_statements_per_table_and_reports_the_read_back_count():
    ran = []

    def run(sql):
        ran.append(sql)
        return [[1]] if sql.startswith(("INSERT", "SELECT")) else []
    out = TrinoTarget(run).write({"tables": [FORM]})
    assert [s.split()[0] for s in ran] == ["CREATE", "INSERT", "SELECT"]
    assert out == {"status": "completed", "message": "Written through Trino: 1 new row; read back 1 of 1.",
                   "tables": [{"table": FORM["target"], "expected": 1, "written": 1, "found": 1}]}


def test_a_trino_failure_is_reported_and_never_shown_as_written():
    def run(sql):
        raise trino_target.TrinoError("Access Denied: Cannot create table")
    out = TrinoTarget(run).write({"tables": [FORM]})
    assert out["status"] == "failed" and "Access Denied" in out["message"] and out["tables"] == []


def trino_ctx(pg, iceberg=None):
    links = [link("CustomerID", "states_value_of", "CUS48271", "corrected"),
             link("Amount", "states_value_of", "879.90", "corrected", unit="SGD", chunk="k3")]
    c, audio = call_ctx(pg, links)
    ran = []

    def run(sql):
        ran.append(sql)
        return [[1]] if sql.startswith(("INSERT", "SELECT")) else []
    c.trino = lambda: TrinoTarget(run)
    if iceberg is not None:
        c.iceberg = iceberg
    return c, audio, ran


@pytest.mark.parametrize("target", ["postgres", "iceberg_cde"])
def test_with_the_trino_route_every_write_goes_to_iceberg_through_trino_and_no_spark_job_is_submitted(form_pg, target):
    """User decision of 23 November 2026: the direct write is Trino (in place of the local database as the visible
    target); the Spark job is generated for download only. It needs no CDE connection."""
    cde = Cde()
    c, audio, ran = trino_ctx(form_pg, iceberg=lambda: IcebergTarget(SETTINGS, TOKEN, transport=httpx.MockTransport(cde)))
    writer = Writer()
    status, preview = writer.preview(c, {"dataset": DATASET, "source": {"kind": "call", "sourceId": audio["id"]}, "target": target})
    assert status == 200 and preview["target"] == {"id": target, "label": "Iceberg through Trino", "submits": True}, preview
    assert preview["warnings"] == [SPARK_DOWNLOAD]
    status, done = writer.confirm(c, {"fingerprint": preview["fingerprint"], "approval": preview["approval"]})
    assert status == 200 and done["status"] == "written", done
    assert done["labels"] == ["Written to the local database: 1 rows", "Iceberg: Written through Trino: 1 new row; read back 1 of 1.",
                              SPARK_DOWNLOAD]
    assert done["iceberg"] == [{"table": "lakehouse.demo_silver.ext_call_fact", "expected": 1, "written": 1, "found": 1}]
    assert [s.split()[0] for s in ran] == ["EXPLAIN", "CREATE", "INSERT", "SELECT"] and "'CUS48271'" in ran[2]   # the preview validates first
    assert cde.calls == []                                      # no Spark job was submitted


def test_the_trino_route_needs_no_cde_connection(form_pg):
    c, audio, ran = trino_ctx(form_pg)                           # c.iceberg() is None: nothing configured for CDE
    writer = Writer()
    status, preview = writer.preview(c, {"dataset": DATASET, "source": {"kind": "call", "sourceId": audio["id"]}})
    assert status == 200 and preview["target"]["submits"] is True and "MERGE INTO lakehouse.demo_silver.ext_call_fact" in preview["spark"]["script"]
    status, done = writer.confirm(c, {"fingerprint": preview["fingerprint"], "approval": preview["approval"]})
    assert status == 200 and done["status"] == "written" and len(ran) == 4      # one validate-only check, then three statements


def test_the_downloaded_spark_script_takes_the_users_own_target_namespace(form_pg):
    c, audio, _ = trino_ctx(form_pg)
    _, preview = Writer().preview(c, {"dataset": DATASET, "source": {"kind": "call", "sourceId": audio["id"]}})
    script = preview["spark"]["script"]
    assert '--target-namespace' in script and '"namespace": "lakehouse.demo_silver"' in script.replace('\\"', '"')
    scope = {}
    exec(compile(script.split("def main():")[0].replace("from pyspark.sql import SparkSession", ""), "generated", "exec"), scope)
    table = scope["retarget"](scope["PAYLOAD"]["tables"][0], "lake.my_schema")
    assert table["target"] == "lake.my_schema.ext_call_fact" and table["ddl"].startswith("CREATE TABLE IF NOT EXISTS lake.my_schema.ext_call_fact (")
    assert "MERGE INTO lake.my_schema.ext_call_fact t" in table["merge"] and "lakehouse.demo_silver" not in table["check"]
    assert scope["retarget"](scope["PAYLOAD"]["tables"][0], None) == scope["PAYLOAD"]["tables"][0]


# ---------------------------------------------------------------- the form rows panel of the Evidence step

from semantic_pipeline_v2 import workspace_server                                             # noqa: E402
from semantic_pipeline_v2.metric_sql import _LOAD_TRACK_DDL                                   # noqa: E402
from semantic_pipeline_v2.semantic_store import SemanticStore                                 # noqa: E402
from semantic_pipeline_v2.workspace import review_database                                    # noqa: E402
from test_interpreter import pg_bin                                                           # noqa: E402
from test_physical_mapping import FORM_1, act, needs_forms, read_form, server                 # noqa: E402
from test_physical_second_table import FIN_YAML                                               # noqa: E402
from test_workspace import running_server                                                     # noqa: E402


@needs_forms
def test_the_form_rows_panel_writes_through_trino_and_shows_no_spark_job_state(tmp_path, monkeypatch):
    """23 November 2026: the Evidence step's panel still said "Private PostgreSQL, also available: Iceberg via Cloudera
    Data Engineering" and showed the refused Spark run. With the Trino route it follows the same two-path model."""
    ran = []

    def run(sql):
        ran.append(sql)
        return [[1]] if sql.startswith(("INSERT", "SELECT")) else []
    monkeypatch.setattr(workspace_server, "trino_from_env", lambda environ: TrinoTarget(run))
    _, options = server(tmp_path, monkeypatch)
    with review_database(tmp_path / "state", pg_bin()) as database, running_server(database, **options) as call:
        SemanticStore(database).import_ossie(DATASET, FIN_YAML)
        database.sql(_LOAD_TRACK_DDL)
        read_form(call, FORM_1)
        status, preview = act(call, "preview", FORM_1)
        assert status == 200 and preview["target"] == "Iceberg through Trino" and preview["iceberg"] is None
        assert preview["lakehouse"] == {"tables": ["lakehouse.demo_silver.ext_str_transaction", "lakehouse.demo_silver.ext_str_account_entity"],
                                        "result": None}
        assert ran == []                                                # a preview writes nothing
        status, done = call("POST", "/api/physical-rows/confirm", {"dataset": DATASET, "sourceId": FORM_1, "fingerprint": preview["fingerprint"]})
        assert status == 200 and done["lakehouse"]["result"]["status"] == "completed", done
        assert done["lakehouse"]["result"]["message"] == "Written through Trino: 2 new rows; read back 2 of 2."
        assert [s.split()[0] for s in ran] == ["CREATE", "INSERT", "SELECT"] * 2
        status, later = call("GET", f"/api/physical-rows?dataset={DATASET}&sourceId={FORM_1}", None)
        assert status == 200 and later["iceberg"] is None and later["lakehouse"]["result"] is None      # no stale job state


def test_the_preview_learns_which_tables_the_lakehouse_will_refuse_without_writing():
    """Trino validates the insert without running it. A masking policy for the writing user shows before any confirm."""
    from semantic_pipeline_v2.trino_target import TrinoError
    seen = []

    def run(sql):
        seen.append(sql)
        if "ext_masked" in sql:
            raise TrinoError("line 1:25: Insert into table with column masks is not supported")
        if "ext_new" in sql:
            raise TrinoError("line 1:97: Table 'c.s.ext_new' does not exist")      # it will be created: not a refusal
        if "ext_other" in sql:
            raise TrinoError("Access Denied: Cannot insert into table c.s.ext_other")
        return [[True]]

    lines = TrinoTarget(run).refusals({"tables": [{"target": f"c.s.{n}"} for n in ("ext_ok", "ext_masked", "ext_new", "ext_other")]})
    assert all(s.startswith("EXPLAIN (TYPE VALIDATE) INSERT INTO c.s.") and s.endswith("WHERE false") for s in seen) and len(seen) == 4
    assert len(lines) == 2
    assert "c.s.ext_masked" in lines[0] and "masking policy" in lines[0] and "writing user" in lines[0]
    assert "c.s.ext_other" in lines[1] and "Access Denied" in lines[1]


def test_a_failed_write_on_a_masked_table_is_explained_in_plain_words():
    from semantic_pipeline_v2.trino_target import TrinoError

    def run(sql):
        raise TrinoError("line 1:1: Insert into table with column masks is not supported")
    out = TrinoTarget(run).write({"tables": [FORM]})
    assert out["status"] == "failed" and "masking policy" in out["message"] and "lakehouse admin" in out["message"]


def test_an_unreachable_lakehouse_stops_the_preview_check_after_one_table():
    from semantic_pipeline_v2.trino_target import TrinoError
    seen = []

    def run(sql):
        seen.append(sql)
        error = TrinoError("<urlopen error timed out>")
        error.connection = True
        raise error

    lines = TrinoTarget(run).refusals({"tables": [{"target": f"c.s.{n}"} for n in ("ext_a", "ext_b", "ext_c")]})
    assert len(seen) == 1                       # the other tables are not tried: each try may wait a minute
    assert lines == ["The lakehouse could not be reached, so this preview did not check whether it accepts the write."]
