# Manufacturing demo rehearsal

The sample is fictional. Every contract is synthetic and visibly labelled.
The transaction period below is 2024, not the current year.
This is a prepared data walkthrough; the V2 application workflow is still planned.

## Presenter story

“We connect manufacturing records and document evidence through shared business definitions.
The system keeps a promise separate from a measured fact.
It shows the source and the reasoning behind each proposed connection.”

| Stage | Show | Expected point |
|---|---|---|
| 1. Structured foundation | 25-table schema and predefined Ossie model | Products, orders, suppliers, production and inventory have explicit definitions and keys |
| 2. Document foundation | MFG-001 and MFG-002 | The synthetic agreement names an actual sample account, product and order |
| 3. Meaning connection | Purchase commitment → ordered quantity | These concepts provide comparison context; they are not equivalent |
| 4. Evidence and facts | MFG-001 + Q1 SQL | Westside Plaza ordered 51 units against an illustrative 71-unit commitment |
| 5. Changes over time | MFG-002 and MFG-003 | The delivery promise changes while the operational due date stays unchanged |
| 6. Honest gaps | MFG-005 | Warranty remains a valid document concept without a fabricated operational field |
| 7. Shared ontology, separate evidence | MFG-007 | A second customer shares product/commitment meanings but retains its own quantities and agreement |

## Main scenario: Westside Plaza

- AdventureWorks CustomerID: **29641**. StoreID: **652**.
- Product: **Touring-1000 Yellow, 46**. ProductID: **954**. ProductNumber: **BK-T79Y-46**.
- Period: **2024-01-01 inclusive to 2025-01-01 exclusive**.
- Source orders: **51 units across three orders**.
- Synthetic minimum commitment: **71 units**.
- Arithmetic difference: **20 units**. This is a sample comparison, not a legal breach determination.

Use `evaluation/expected_answers.json` Q1 for the parameterized SQL and expected result.
The query joins each order line to its single order header.
Do not join another one-to-many table before summing quantities.

## Amendment scenario

MFG-002 references order **51131**, line **36824**, for **16 units**.
The sample order date is **2024-05-29**.
The original synthetic delivery promise is **2024-06-10**.
MFG-003 becomes effective on **2024-05-30** and changes the promise to **2024-06-17**.

A question as of May 29 uses the original promise.
A question as of May 30 uses the amendment.
The operational DueDate remains June 10.
The recorded ShipDate is June 5; shipment does not prove delivery.

## Supplier scenario

MFG-006 covers **International Trek Center**, Vendor BusinessEntityID **1518**.
It covers **Touring-Panniers, Large**, ProductID **842**, ProductNumber **PA-T100**.
This is a separate purchased item, not the touring bicycle in the customer scenario.

The authored promise is **14 calendar days after purchase-order acceptance**.
The sample AverageLeadTime is **120 days**, described as placement-to-receipt average time.
Show the semantic connection and the different start events.
Do not label the numerical difference as actual delay, supplier breach or a bad supplier rating.

## Second customer and unmapped concepts

MFG-007 belongs to **Camping and Sports Store**, CustomerID **29629**, StoreID **626**.
It uses the same product and purchase-commitment concept.
Its sample ordered quantity is **21 units** for 2024.
Its separate synthetic commitment is **40 units**.
MFG-001 must not supply this customer's obligation.

MFG-005 defines a **24-month warranty from documented acceptance**.
No acceptance date or equivalent operational warranty field is established.
The correct result retains the concept and reports the missing structured counterpart.

## Original product documents

`metadata/native-documents.json` lists eight original Microsoft DOC files and their converted text.
It preserves the ProductDocument links and source hashes.
These are service/assembly documents, not customer contracts.
Some have no explicit product link in the source; do not invent one.
They do not automatically belong to the selected touring-bicycle customer scenario.
Conversion succeeded locally, but text quality still needs presenter inspection.

## Preparation boundary

The package provides source material and expected results.
It has not run AI extraction, embeddings, candidate ranking or the V2 review UI.
The negative cases are future evaluation inputs, not passed runtime safeguards.
Use one Markdown or DOCX representation per document ID.
Human domain review remains required before certifying the semantic package.
