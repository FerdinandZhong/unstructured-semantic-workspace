# AdventureWorks source and projection

Prepared on 2026-11-16. This pack uses Microsoft's synthetic AdventureWorks OLTP sample. It is not real customer or supplier data.

Source repository: https://github.com/microsoft/sql-server-samples

Pinned commit: `8fe3fd0c9a825fef3ae6d601e97537b612d7b214`.

Source directory: `samples/databases/adventure-works/oltp-install-script/`.

License: MIT. A tracked copy is `LICENSE-MICROSOFT.txt`. Preserve `raw/license.txt` with redistributed source and exports. `metadata/source.json` records immutable download URLs, byte sizes and SHA256 hashes for all 27 downloaded files. No third-party mirror supplies this pack.

## Reproduction

1. Run `python scripts/prepare_data.py` from this package or another directory.
2. Run `python scripts/prepare_anchors.py`.
3. Run `python scripts/extract_native_documents.py`.
4. Run `python -m unittest discover -s scripts -p test_prepare_data.py`.
5. Run `python scripts/verify_data.py`.

The first script downloads missing pinned files with curl. It verifies existing and fresh files against `metadata/source.json`. A failed download or checksum mismatch stops the process. Preserve the manifest when deleting cached downloads. A new build uses a temporary SQLite file. It replaces the last database only after data validation succeeds. Native DOC text conversion uses macOS `textutil` when available. Original DOC export requires only Python.

## Included data

The pack contains 25 populated tables and 391,347 rows. `data/adventureworks.sqlite` provides local queries. `data/csv/` contains header-bearing CSV exports. `metadata/schema.json` provides column types, source descriptions, computed expressions, keys and relationships. `metadata/data-verification.json` gives per-table counts and key checks.

The projection preserves all source rows for each selected table. It is not the full 71-table OLTP schema. Thirty-three in-scope foreign keys have no unmatched non-null references. Ten foreign keys target omitted tables. The verification manifest lists these dependencies. Examples include Employee, StateProvince, SalesPerson, SalesTerritory, CreditCard, CurrencyRate and SpecialOfferProduct. In-scope relationships do not imply foreign-key closure.

SQLite table names replace the schema dot with an underscore. SQL Server alias types appear in the schema metadata. Name and AccountNumber aliases remain text. Flag becomes an integer boolean. No SQL Server engine was installed or contacted. No shared database was accessed.

## Projection limits

- Integer quantities become SQLite INTEGER. Date/time values remain validated ISO text. SQLite date functions support local date comparisons.
- Money and decimals remain exact canonical strings in both CSV and SQLite TEXT. Use Python Decimal for exact calculations. SQLite SUM or CAST on these strings can introduce floating point approximation.
- Computed column exports remain fixed source snapshots. The manifest preserves expressions. SQLite does not recompute these values after edits.
- XML, spatial values and hierarchy identifiers remain opaque text. SQL Server XML queries, spatial methods and hierarchy methods are unsupported.
- Document hierarchy identifiers preserve their exported hexadecimal representation. The root identifier contains a NUL character. It is not a slash path.
- The parser uses the official import delimiters. It normalizes CRLF in the projection. Raw files remain byte-for-byte unchanged. Document and ProductModel are not conventional tab-separated files.
- The original Document file preserves encoded binary payloads. Eight decoded OLE DOC files and their text conversions are in `data/native-documents/`. Text extraction succeeded, but no human reviewed rendering or extraction completeness.
- Source keys and in-scope foreign keys are checked. SQL Server triggers, defaults, checks, collation, indexes and complete constraint behavior are not recreated. SQLite PK indexes preserve uniqueness after the build. Foreign-key validation occurs in the build and is not an enforced SQLite FK declaration.
- Source extended properties are Microsoft descriptions, not governed semantic definitions. ProductVendor MinOrderQty and MaxOrderQty descriptions conflict with their names. Do not silently repair the source descriptions.

## Anchors and evidence

`metadata/demo_context.json` defines the final presentation anchors. `metadata/source_candidate_anchors.json` records an exploratory source candidate, not the final hero. The following figures describe that exploratory candidate. Customer 29705 is Rewarding Activities Company. Customer 29722 is Vigorous Exercise Company. Product 712 is AWC Logo Cap, product number CA-1098. Its vendor 1636 is Integrated Sport Products. The ProductVendor row records AverageLeadTime 45. This is contextual sample data, not a contractual promise.

The exploratory customer has 12 sales lines and 113 units for this product. Its 2024 quantity is 44. The whole sample's sales order dates span 2022-05-30 through 2025-06-29. Do not substitute older AdventureWorks date assumptions. ModifiedDate values may extend beyond transaction dates.

Nine parser tests cover delimiter preservation, truncated records, invalid UTF-8, invalid types, exact decimal preservation, composite keys and checksum corruption. Query verification checks database integrity, all sales order date relationships and 121,317 exact sales-line decimal formulas. Native document metadata records source hierarchy IDs, product links and decoded file hashes. These checks qualify this local data projection. They do not establish V2 application integration or AI extraction accuracy.
