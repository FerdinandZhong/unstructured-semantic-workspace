"""Build metadata-only Ossie definitions from the pinned finance workbook."""

import argparse
from collections import Counter
import csv
from hashlib import sha256
import importlib.util
import io
import json
from pathlib import Path
import posixpath
import sys
import tempfile
from xml.etree import ElementTree
from zipfile import ZipFile

import yaml


ROOT = Path(__file__).resolve().parents[2]
SOURCE_SHA256 = "22461ccdef508009f6f229f9cfd3ad22802a3b32316170b2254efc113d0f9b79"
MODEL = Path(__file__).with_name("finance.ossie.yaml")
LOCAL = MODEL.parent / "local"
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REFERENCES = {
    "Index": "Workbook navigation.",
    "Param": "Code reference with unverified key and applicability.",
    "Dictionary": "Source descriptions and design intent, which can differ from populated columns.",
    "ER": "Illustrative relationship layout.",
    "Company List - External Data": "External company reference with no approved banking identity link.",
    "Incident File - External Data": "External reports with no approved banking identity or recorded-risk mapping.",
}
TRANSACTIONS = {
    "CASA_Txn": "CASA",
    "CreditCard_Txn": "credit card",
    "Treasury_LC_Txn": "treasury LC",
    "Treasury_FX_Txn": "treasury FX",
    "Loan_Txn": "loan",
    "Investment_Txn": "investment",
}
SNAPSHOT_KEY = ("SourceBatch", "BusinessDate")
DATE_FIELDS = {"BusinessDate", "TransactionDate", "StartDate", "EndDate", "OpenDate"}
ID_FIELDS = {"SourceBatch", "CustomerID", "AccountNumber", "RMID", "HierarchyID", "TransactionID"}
FIELDS = {
    "SourceBatch": "SHA-256 identity of the immutable source workbook batch, supplied by the projection.",
    "BusinessDate": "Business snapshot date. This is separate from an event or relationship effective date.",
    "CustomerID": "Workbook-local customer identifier. It may identify a corporate or individual customer.",
    "CustomerType": "Source customer type for an accepted customer snapshot.",
    "IndustryCode": "Industry code as supplied. A governed industry classification has not been established.",
    "RiskRating": "Workbook-recorded risk rating in its own source population.",
    "CustomerSegment": "Workbook customer segment. The banking and Looker tiers require a crosswalk.",
    "CountryOfOperation": "Country of operation as supplied for the customer.",
    "RMID": "Workbook-local relationship manager identifier.",
    "HierarchyID": "Workbook-local hierarchy record identifier.",
    "RelationshipType": "Source classification of the customer–manager relationship.",
    "StartDate": "Effective start of the source relationship record.",
    "EndDate": "Optional effective end of the source relationship record.",
    "Level": "Hierarchy level as supplied. This does not establish a manager-to-manager edge.",
    "Region": "Regional assignment label as supplied.",
    "Department": "Department label as supplied.",
    "ActiveFlag": "Source hierarchy flag. Its codes remain in source form.",
    "AccountNumber": "Restricted workbook-local account identifier.",
    "Currency": "Native currency code. This projection performs no conversion.",
    "ProductType": "Source account product category.",
    "ProductSubType": "Source account product subcategory.",
    "ProductCode": "Source product code with no certified reference mapping.",
    "OpenDate": "Recorded account opening date.",
    "BookingCountry": "Source account booking country.",
    "BranchCode": "Source branch code with no certified reference mapping.",
    "Status": "Status as recorded by this worksheet. Code meanings can vary across worksheets.",
    "Title": "Source relationship manager title.",
    "TransactionID": "Workbook event identity within this transaction worksheet.",
    "TransactionDate": "Recorded event date. It is distinct from the business snapshot date.",
    "Amount": "Unsigned exact amount in the row currency. This amount is not an account balance.",
    "DebitCredit": "Direction code as supplied. This model does not infer a signed or converted amount.",
    "Channel": "Source transaction channel.",
}
# Explicit allowlists exclude identity attributes, contacts, free text and weakly mapped codes.
TRANSACTION_FIELDS = (
    "TransactionID", "AccountNumber", "CustomerID", "TransactionDate", "Currency",
    "Amount", "DebitCredit", "Channel", "Status", "BranchCode",
)
SHEETS = {
    "Customers": (
        ("CustomerID",),
        "One accepted customer per workbook business snapshot.",
        ("CustomerID", "CustomerType", "IndustryCode", "RiskRating", "CustomerSegment",
         "CountryOfOperation", "BranchCode"),
    ),
    "Accounts": (
        ("AccountNumber",),
        "One accepted account per workbook business snapshot. No balance value is supplied.",
        ("AccountNumber", "CustomerID", "Currency", "ProductType", "ProductSubType",
         "ProductCode", "OpenDate", "BookingCountry", "BranchCode", "Status"),
    ),
    "RelationshipManagers": (
        ("RMID",),
        "One accepted relationship manager per workbook business snapshot.",
        ("RMID", "Title", "BranchCode", "Region", "Status"),
    ),
    "Customer_RM_Relationship": (
        ("CustomerID", "RMID", "RelationshipType"),
        "One accepted customer–manager relationship of a given type per workbook business snapshot.",
        ("CustomerID", "RMID", "RelationshipType", "StartDate", "EndDate"),
    ),
    "RMHierarchy": (
        ("HierarchyID",),
        "One accepted hierarchy record per workbook business snapshot.",
        ("HierarchyID", "RMID", "Level", "Region", "Department", "StartDate", "ActiveFlag"),
    ),
    **{
        sheet: (
            ("TransactionID",),
            f"One accepted {kind} event row per workbook business snapshot. An event can recur across snapshots.",
            TRANSACTION_FIELDS,
        )
        for sheet, kind in TRANSACTIONS.items()
    },
}
LINKS = (
    ("Accounts", "Customers", "CustomerID"),
    ("Customer_RM_Relationship", "Customers", "CustomerID"),
    ("Customer_RM_Relationship", "RelationshipManagers", "RMID"),
    ("RMHierarchy", "RelationshipManagers", "RMID"),
    *((name, target, column) for name in TRANSACTIONS for target, column in
      (("Accounts", "AccountNumber"), ("Customers", "CustomerID"))),
)


def xml_path(name):
    return f"{{{MAIN}}}{name}"


def worksheet_headers(raw):
    """Read only the modeled header rows from the pinned XLSX package."""
    if sha256(raw).hexdigest() != SOURCE_SHA256:
        raise ValueError("Workbook checksum does not match the approved source pin")
    with ZipFile(io.BytesIO(raw)) as archive:
        workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        relationships = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        paths = {item.attrib["Id"]: item for item in relationships}
        shared = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
        strings = ["".join(entry.itertext()) for entry in shared]
        sheets = workbook.find(xml_path("sheets"))
        names = [item.attrib["name"] for item in sheets]
        if len(names) != len(set(names)) or set(names) != set(SHEETS) | set(REFERENCES):
            raise ValueError("Workbook sheet inventory differs from the finance model")
        headers = {}
        for item in sheets:
            name = item.attrib["name"]
            if name not in SHEETS:
                continue
            relation = paths[item.attrib[f"{{{REL}}}id"]]
            target = relation.attrib["Target"]
            member = posixpath.normpath(
                target.lstrip("/") if target.startswith("/") else posixpath.join("xl", target)
            )
            if relation.get("TargetMode") == "External" or not member.startswith("xl/worksheets/"):
                raise ValueError("Invalid worksheet relationship")
            with archive.open(member) as stream:
                first = next(element for _, element in ElementTree.iterparse(stream)
                             if element.tag == xml_path("row"))
                cells = [
                    strings[int(cell.findtext(xml_path("v")))]
                    for cell in first if cell.get("t") == "s"
                ]
            if len(cells) != len(set(cells)) or not cells:
                raise ValueError(f"Invalid header in modeled worksheet: {name}")
            headers[name] = cells
        return headers


def extension(**data):
    return [{"vendor_name": "HELIOS", "data": json.dumps(data, ensure_ascii=False, sort_keys=True)}]


def field(name):
    datatype = "Date" if name in DATE_FIELDS else "Decimal" if name == "Amount" else "String"
    return {
        "name": name,
        "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": name}]},
        "description": FIELDS[name],
        "datatype": datatype,
        "dimension": {"is_time": name in DATE_FIELDS},
        "custom_extensions": extension(
            provenance="batch hash" if name == "SourceBatch" else "worksheet header",
            access="internal join key; never render source identifier values" if name in ID_FIELDS
            else "source-scoped metadata",
        ),
    }


def customer_count_metric(name, label, description, date_filter):
    return {
        "name": name,
        "description": description,
        "datatype": "Integer",
        "expression": {"dialects": [
            {"dialect": "ANSI_SQL", "expression": "COUNT(DISTINCT Customers.CustomerID)"}
        ]},
        "custom_extensions": extension(
            dataset="Customers", label=label, required_filters={
                "SourceBatch": "single_value", "BusinessDate": date_filter,
            }, additive_across_dates=False,
        ),
    }


def build_model(headers):
    datasets = []
    for name, (key, grain, columns) in SHEETS.items():
        missing = {"BusinessDate", *columns} - set(headers[name])
        if missing:
            raise ValueError(f"Modeled column missing from worksheet: {name}")
        datasets.append({
            "name": name,
            "source": f"finance_workbook.{name}",
            "description": grain,
            "primary_key": [*SNAPSHOT_KEY, *key],
            "fields": [field(column) for column in (*SNAPSHOT_KEY, *columns)],
            "custom_extensions": extension(
                worksheet=name, retention="accepted only", source_type="logical projection",
                excluded_headers=[column for column in headers[name]
                                  if column != "BusinessDate" and column not in columns],
            ),
        })
    relationships = [
        {
            "name": f"{source}_to_{target}",
            "from": source, "to": target,
            "from_columns": [*SNAPSHOT_KEY, column],
            "to_columns": [*SNAPSHOT_KEY, column],
            "custom_extensions": extension(cardinality="many_to_one", basis="source snapshot validation"),
        }
        for source, target, column in LINKS
    ]
    return {
        "version": "0.2.0.dev0",
        "name": "finance_workbook_demo",
        "description": "Authored finance workbook model candidate. Contains metadata only.",
        "ai_context": {"instructions": (
            "Use only authorized accepted rows and join on the workbook batch and business date. "
            "Never sum repeated snapshots, combine native currencies, derive balances or rank across "
            "unreviewed populations. Require a separately reviewed identity decision for document or "
            "external-company links. Preserve recorded risk separately from allegations."
        )},
        "datasets": datasets,
        "relationships": relationships,
        "metrics": [
            customer_count_metric(
                "customers_on_business_date", "Customers on business date",
                "Count distinct customer IDs in one source batch on the selected business date.",
                "single_value",
            ),
            customer_count_metric(
                "distinct_customers_in_period", "Distinct customers in period",
                "Count each customer ID once in one source batch across the selected business dates, including both endpoints.",
                "inclusive_range",
            ),
        ],
        "custom_extensions": extension(
            source_workbook="MockUp Data.xlsx", source_sha256=SOURCE_SHA256,
            review_state="authored_demo_candidate",
            execution="metadata_only; optional local CSV projection",
            reference_sheets=REFERENCES,
        ),
    }


def accepted_projection(workbook, importer_path, model):
    """Reuse the V1 preview validator without applying a database import."""
    spec = importlib.util.spec_from_file_location("finance_workbook_preview", importer_path)
    if spec is None or spec.loader is None:
        raise ValueError("The local workbook preview importer is required")
    importer = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(importer)
    finally:
        sys.dont_write_bytecode = previous
    original, records, report = importer.prepare(workbook)
    if report["source_sha256"] != SOURCE_SHA256 or report["validator_version"] != 1:
        raise ValueError("The importer preview does not match the pinned source contract")
    with ZipFile(io.BytesIO(original)) as archive:
        book = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        properties = book.find(xml_path("workbookPr"))
    epoch1904 = properties is not None and properties.get("date1904") in ("true", "1")
    columns = {dataset["name"]: [field["name"] for field in dataset["fields"]]
               for dataset in model["datasets"]}
    projected = {name: [] for name in columns}
    for record in records:
        if record["status"] != "accepted":
            continue
        name = record["sheet"]
        row = {}
        for column in columns[name]:
            value = record["data"].get(column, "")
            if column == "SourceBatch":
                value = SOURCE_SHA256
            elif column == "BusinessDate":
                value = record["business_date"]
            elif column in DATE_FIELDS and value:
                value = importer.normalized_date(value, epoch1904)
                if value is None:
                    raise ValueError(f"Invalid modeled date in {name}.{column}")
            row[column] = value
        projected[name].append(row)

    check_projection(model, projected)
    return columns, projected


def check_projection(model, projected):
    def keys(rows, names):
        return Counter(tuple(row[column] for column in names) for row in rows)

    for dataset in model["datasets"]:
        entries = keys(projected[dataset["name"]], dataset["primary_key"])
        if not entries or any(count != 1 or not all(key) for key, count in entries.items()):
            raise ValueError(f"Invalid accepted snapshot key in {dataset['name']}")
    for link in model["relationships"]:
        source = keys(projected[link["from"]], link["from_columns"])
        target = keys(projected[link["to"]], link["to_columns"])
        if any(target[key] != 1 for key in source):
            raise ValueError(f"Accepted snapshot relationship failed: {link['name']}")
    account_identity = {
        tuple(row[key] for key in (*SNAPSHOT_KEY, "AccountNumber")):
        (row["CustomerID"], row["Currency"])
        for row in projected["Accounts"]
    }
    for name in TRANSACTIONS:
        for row in projected[name]:
            identity = tuple(row[key] for key in (*SNAPSHOT_KEY, "AccountNumber"))
            if account_identity[identity] != (row["CustomerID"], row["Currency"]):
                raise ValueError(f"Account party or currency mismatch in {name}")


def write_projection(columns, projected):
    target = LOCAL / "finance_workbook"
    if LOCAL.is_symlink() or target.is_symlink():
        raise ValueError("Projection path cannot be a symlink")
    LOCAL.mkdir(mode=0o700, exist_ok=True)
    if LOCAL.stat().st_mode & 0o077:
        raise ValueError("Projection parent must be private")
    with tempfile.TemporaryDirectory(prefix=".projection-", dir=LOCAL) as temp:
        staged = Path(temp) / target.name
        staged.mkdir(mode=0o700)
        for sheet, header in columns.items():
            with (staged / f"{sheet}.csv").open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=header)
                writer.writeheader()
                writer.writerows(projected[sheet])
        if target.exists():
            files = {file.name for file in staged.iterdir()}
            if files != {file.name for file in target.iterdir()} or any(
                (target / name).is_symlink()
                or (target / name).read_bytes() != (staged / name).read_bytes()
                for name in files
            ):
                raise ValueError("Existing private projection differs from the pinned workbook")
        else:
            staged.rename(target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", required=True, type=Path)
    parser.add_argument("--schema", type=Path, help="optional local Helios Ossie JSON schema")
    parser.add_argument("--write-model", action="store_true", help="replace the checked-in model")
    parser.add_argument("--preview-importer", type=Path, help="local V1 import_mock_workbook.py for CSV projection")
    parser.add_argument("--write-projection", action="store_true", help="write ignored local finance CSVs")
    args = parser.parse_args()
    if args.write_projection and args.preview_importer is None:
        parser.error("--write-projection requires --preview-importer")
    if args.workbook.stat().st_size > 25_000_000:
        parser.error("Workbook exceeds the local size limit")
    raw = args.workbook.read_bytes()
    model = build_model(worksheet_headers(raw))
    if args.schema:
        from jsonschema import validate
        validate(model, json.loads(args.schema.read_text()))
    payload = yaml.safe_dump(model, sort_keys=False, allow_unicode=True).encode()
    sys.path.insert(0, str(ROOT))
    from semantic_pipeline_v2.core import normalize
    result = normalize(payload, sha256(payload).hexdigest())
    if not args.write_model and MODEL.read_bytes() != payload:
        raise SystemExit("Finance model differs from the generated candidate")
    counts = None
    if args.preview_importer is not None:
        columns, projected = accepted_projection(args.workbook, args.preview_importer, model)
        counts = {name: len(rows) for name, rows in projected.items()}
        if args.write_projection:
            write_projection(columns, projected)
    if args.write_model:
        MODEL.write_bytes(payload)
    print(json.dumps({
        "source_sha256": SOURCE_SHA256,
        "model_sha256": result["revision"],
        "worksheets": len(SHEETS) + len(REFERENCES),
        "datasets": len(model["datasets"]),
        "modeled_fields": sum(len(item["fields"]) for item in model["datasets"]),
        "relationships": len(model["relationships"]),
        "metadata_records": len(result["records"]),
        "reference_sheets": len(REFERENCES),
        "schema_checked": bool(args.schema),
        "accepted_projection_counts": counts,
    }))


if __name__ == "__main__":
    main()
