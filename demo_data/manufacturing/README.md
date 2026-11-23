# Manufacturing demonstration foundation

This package prepares a coherent AdventureWorks demonstration before V2 application work.
The records describe a fictional manufacturer and fictional customers.
The contracts are newly authored synthetic examples, not Microsoft contracts or real customer agreements.

```text
Pinned Microsoft sample -> relational projection -> predefined Ossie definitions
            |                         |                         |
       linked documents      verified entity keys               |
                                      |                         |
                           synthetic contract pack               |
                                      |                         |
                           evidence-backed concepts -> proposed meaning links
                                      |
                          checked SQL + expected answers
```

## Start here

1. Read `SOURCES.md` for source attribution, the pinned revision and preparation limits.
2. Open `metadata/demo_context.json` for the chosen customer, product, supplier and dates.
3. Read `documents/markdown/MFG-001.md` through `MFG-007.md`.
4. Inspect `semantics/manufacturing.ossie.yaml` and `semantics/concept_catalog.json`.
5. Read `DEMO_STORY.md` and `evaluation/expected_answers.json`.

## Contents

| Path | Purpose |
|---|---|
| `raw/` | Unchanged, downloaded Microsoft source files and licence notice; ignored |
| `metadata/source.json` | Source URLs, commit and SHA256 values |
| `metadata/schema.json` | Original table/column definitions, keys and projection notes |
| `data/adventureworks.sqlite` | Local query fixture; no running database server required; ignored |
| `data/csv/` | Portable headered table extracts; ignored |
| `metadata/native-documents.json` | Original Microsoft document hashes, conversion status and product links |
| `data/native-documents/` | Eight original DOC files and converted text; ignored |
| `semantics/manufacturing.ossie.yaml` | Authored structured model using the inspected Helios Ossie contract |
| `semantics/bindings.json` | Stable definition IDs and physical source bindings |
| `semantics/concept_catalog.json` | Authored unstructured concepts and expected relation types |
| `semantics/concepts.ttl` | SKOS concepts and mapping proposals; no approved equivalence claims |
| `semantics/metadata_conflicts.json` | Contradictions needing domain-owner review |
| `documents/manifest.json` | Scoped IDs, effective dates, source hashes and alternative file formats |
| `documents/markdown/` | Seven synthetic agreements, readable and ingestible |
| `documents/docx/` | The same seven agreements in Word format; ignored |
| `evaluation/` | Expected links, rejection cases, SQL answers and verification evidence |

## Reproduce locally

Requires Python 3.12, curl and the packages in `requirements-preparation.txt`.
Use a separate preparation environment; no application credentials are needed.
The final reader check requires a local Helios checkout, read-only.
It does not call Helios services or write to its artifact store.

```bash
python scripts/prepare_data.py
python scripts/prepare_anchors.py
python scripts/extract_native_documents.py
python scripts/verify_data.py
python -m unittest discover -s scripts -p test_prepare_data.py
python scripts/prepare_semantics.py
python scripts/prepare_documents.py
python scripts/prepare_concept_rdf.py
python scripts/verify_package.py --helios /path/to/helios
```

Run the data-script unit tests as described in `SOURCES.md`.
Download and preparation failures must stop the process.
Do not replace missing or failed source data with invented records.

## Review state and limits

The model is an **authored demonstration candidate** built from source metadata and explicit business definitions.
The package checks the real Helios schema and reader; it does not run Helios's AI proposal workflow.
Domain-owner approval remains pending.
Source descriptions alone do not make a governed semantic model.

Synthetic promises remain separate from sample operational facts.
A commitment is not ordered quantity. A promise is not average lead time. A shipment is not delivery.
Warranty and acceptance concepts may remain unmapped.
Matching product text in another namespace must not establish identity.
A delivery amendment changes the contract interpretation, not the operational DueDate.

The local projection has explicit omissions and external references.
It does not recreate SQL Server triggers, generated columns, spatial behavior or all database constraints.
Inspect `metadata/schema.json` and the data verification notes before using it elsewhere.
This package's data types do not certify portability to Iceberg, Trino or another warehouse.
The annual sales comparison uses 2024; it is not a whole-dataset as-of snapshot.
Inventory is a source snapshot, not verified inventory at 2024 year-end.
Metric fanout requirements express intended query safeguards; the future V2 runtime must enforce them.

The gold cases are authored expectations with pending human adjudication.
They are not measured AI accuracy or evidence that V2 access controls already work.
The RDF shape test proves structural conformance only, not factual truth or OWL reasoning.
No files were uploaded, embedded or indexed into V1 or V2.

## Ingestion preparation

Use `documents/manifest.json` to choose **one format per document ID**.
Markdown and DOCX files are alternate representations, not separate sources.
Use the manifest's entity scope and source namespace when preparing V2 fixtures.
Do not ingest this manufacturing pack into the current banking application.
Native Microsoft documents retain their original attribution and entity mappings.
Do not describe synthetic contracts as extracted original records.

The overlay `semantics/semantics_overlay.yaml` plus `demo_data/enrich_ossie.py` is now the maintained path for the model. `scripts/prepare_semantics.py` is an older generator and no longer reproduces `semantics/manufacturing.ossie.yaml`.
