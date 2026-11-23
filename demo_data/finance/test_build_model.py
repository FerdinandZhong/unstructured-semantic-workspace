"""Negative checks use synthetic placeholders and do not need the workbook."""

from pathlib import Path
import json

import pytest

import build_model as finance


def modeled_headers():
    return {name: ["BusinessDate", *definition[2]] for name, definition in finance.SHEETS.items()}


def projected_snapshot(model):
    by_sheet = {
        item["name"]: [{field["name"]: "placeholder" for field in item["fields"]}]
        for item in model["datasets"]
    }
    return by_sheet


def test_unknown_source_and_missing_modeled_field_rejected():
    with pytest.raises(ValueError, match="checksum"):
        finance.worksheet_headers(b"changed workbook")
    headers = modeled_headers()
    headers["Customers"].remove("RiskRating")
    with pytest.raises(ValueError, match="missing from worksheet: Customers"):
        finance.build_model(headers)


def test_model_links_keep_both_scope_keys_and_exclude_identity_cells():
    model = finance.build_model(modeled_headers())
    assert {dataset["name"] for dataset in model["datasets"]}.isdisjoint(finance.REFERENCES)
    for dataset in model["datasets"]:
        assert dataset["primary_key"][:2] == list(finance.SNAPSHOT_KEY)
        assert not {"CustomerName", "ID", "PhoneNumber", "Email", "Description"} & {
            field["name"] for field in dataset["fields"]
        }
    for link in model["relationships"]:
        assert link["from_columns"][:2] == link["to_columns"][:2] == list(finance.SNAPSHOT_KEY)


def test_customer_metrics_count_distinct_with_different_date_scopes():
    model = finance.build_model(modeled_headers())
    metrics = {metric["name"]: metric for metric in model["metrics"]}
    assert set(metrics) == {"customers_on_business_date", "distinct_customers_in_period"}
    for name, date_filter in (
        ("customers_on_business_date", "single_value"),
        ("distinct_customers_in_period", "inclusive_range"),
    ):
        metric = metrics[name]
        assert metric["expression"]["dialects"] == [
            {"dialect": "ANSI_SQL", "expression": "COUNT(DISTINCT Customers.CustomerID)"}
        ]
        scope = json.loads(metric["custom_extensions"][0]["data"])
        assert scope["required_filters"] == {"SourceBatch": "single_value", "BusinessDate": date_filter}
        assert scope["additive_across_dates"] is False


@pytest.mark.parametrize(("sheet", "column", "value", "error"), [
    ("Accounts", "SourceBatch", "other batch", "relationship failed"),
    ("Accounts", "BusinessDate", "other day", "relationship failed"),
    ("CASA_Txn", "Currency", "other currency", "party or currency mismatch"),
])
def test_projection_rejects_cross_snapshot_joins_and_currency_mismatches(sheet, column, value, error):
    model = finance.build_model(modeled_headers())
    rows = projected_snapshot(model)
    finance.check_projection(model, rows)
    rows[sheet][0][column] = value
    with pytest.raises(ValueError, match=error):
        finance.check_projection(model, rows)


def test_projection_rejects_duplicates():
    model = finance.build_model(modeled_headers())
    rows = projected_snapshot(model)
    rows["CASA_Txn"].append(rows["CASA_Txn"][0].copy())
    with pytest.raises(ValueError, match="Invalid accepted snapshot key"):
        finance.check_projection(model, rows)


def test_projection_write_is_private_idempotent_and_preserves_changed_output(tmp_path, monkeypatch):
    local = tmp_path / "local"
    monkeypatch.setattr(finance, "LOCAL", local)
    columns = {"Customers": ["CustomerType"]}
    rows = {"Customers": [{"CustomerType": "fictional category"}]}
    finance.write_projection(columns, rows)
    finance.write_projection(columns, rows)
    assert local.stat().st_mode & 0o077 == 0
    output = local / Path("finance_workbook/Customers.csv")
    output.write_text("changed output")
    with pytest.raises(ValueError, match="Existing private projection differs"):
        finance.write_projection(columns, rows)
    assert output.read_text() == "changed output"
