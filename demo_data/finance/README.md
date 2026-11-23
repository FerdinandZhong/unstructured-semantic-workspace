# Financial Services dataset

Open **Financial Services → Ingest → Structured data** to inspect the tables derived from the synthetic workbook. The [verification sheet](../../docs/plans/2026-11-19-v2-structured-workbook.md) records the accepted row counts and open Ossie decisions.

The Customers table has two named metrics. **Customers on business date** counts distinct customer IDs in one daily snapshot. **Distinct customers in period** counts each ID once across an inclusive date range. Both require one source batch. The 6 November 2026 snapshot contains 51 customers. The 4–6 November period contains 52.

## Source inventory

| Source | Available locally | Use in V2 |
| --- | --- | --- |
| Supplied mock banking workbook | `~/Downloads/MockUp Data.xlsx`, 17 worksheets | The [Ossie package](finance.ossie.yaml) models 11 snapshot datasets. The builder can materialize 11 CSVs from accepted rows. The other six sheets supply reference or documentation. |
| Cleaned documents | Five original, one-page PDFs in `~/Downloads/cleaned data/` | Three support ordinary PDF processing. Two display an XFA compatibility page. **Read form fields** reads their populated embedded data locally and preserves field paths. The first form contains 326 populated fields and the second contains 331. |
| Cleaned recording | One original M4A in the same folder, approximately 7 minutes 37 seconds | Retain the original as one source. The separate V1 demo used split recordings. Source attribution requires review. |
| Existing Jade Logistics files | One synthetic adverse-media document in the tracked preload set and one synthetic RM call in the active V1 hero pack | Restrict both to the Jade demo. Do not use a reported allegation as the recorded customer risk rating. |
| Existing Looker layer | V1’s reviewed field mapping for the `bank_trino` model and its two banking Explores | Reuse the definitions through the [candidate mapping table](looker_crosswalk.json). The V1 check is historical. The V2 preparation uses local metadata. |

The [source manifest](sources.json) pins byte identities and records extraction readiness. Its roots resolve relative to Downloads, this checkout and the active V1 checkout. Raw documents, recordings and workbook rows stay outside the shared model.

## Reproduce the structured data

Run from the V2 root with the dedicated V2 environment active. The default builder checks the pinned source and the checked-in model. Add the schema and projection options to validate Ossie and write the local CSVs. The command uses the active V1 importer’s offline validation rules.

```sh
python -B demo_data/finance/build_model.py \
  --workbook "$HOME/Downloads/MockUp Data.xlsx" \
  --schema ../semantic-hub/shared/helios_core/ossie/ossie-schema.json \
  --preview-importer ../v1-knowledge-journey/c360_db/import_mock_workbook.py \
  --write-projection

python demo_data/finance/verify_sources.py
```

Use a Python environment with PyYAML for the source check. Use `--downloads` and `--v1-root` to locate files on another machine. Physical CSVs live in ignored `local/finance_workbook/`. Source `finance_workbook.Customers` resolves to `local/finance_workbook/Customers.csv`. The check produced 1,654 accepted rows, including 797 transaction snapshots. Keep batch, business date and currency with the facts. A transaction can occur across multiple snapshots.

## Combine definitions and evidence

The workbook owns the values for its population. Looker supplies business meaning. Four field pairs are candidates for review across the two sources. These cover industry, customer segment, recorded risk rating and relationship-manager assignment. Each candidate records a qualification. Nine proposals remain unmapped, including snapshot deposits. The workbook’s account rows contain no deposit balance. Summing transaction amounts cannot supply that balance.

For example, inspect `Customers.RiskRating` at a specific business date and compare its definition with `corporate_customer.customer_risk_rating`. Confirm applicable customer type, code vocabulary and policy before approving the mapping. A Jade adverse-media passage can illustrate a separate evidence review. The supplied workbook contains no exact Jade Logistics name match in its customer name column. This pack creates no identity link between the workbook, cleaned documents and Jade. A reviewed link would need an independent identifier and scoped source evidence.

The overlay `semantics_overlay.yaml` plus `demo_data/enrich_ossie.py` is now the maintained path for the model. `build_model.py` is an older generator and no longer reproduces `finance.ossie.yaml`.
