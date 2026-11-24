# Semantic Workspace

**Bring documents, forms and recorded calls into the semantic layer your tables already have.**

The Semantic Workspace links unstructured sources to a governed semantic model.
It then answers questions across tables and documents, with a citation behind every answer.
It is packaged as an Applied ML Prototype (AMP) for Cloudera AI.

<div class="grid cards" markdown>

-   **Link**

    Each passage of a document is linked to the field or metric it is about, with the value it states.

-   **Organise**

    The linked evidence is grouped into a catalog of business concepts that a person reviews and publishes.

-   **Ask**

    People ask in plain language. Numbers come from SQL, wording comes from the cited passage.

-   **Write**

    Values stated in forms, reports and calls become rows of Iceberg tables, after a preview and a confirm.

</div>

## The background

Enterprises govern their tables well.
A semantic layer names each field and metric, defines it and declares how tables join.
Text-to-SQL tools and dashboards work on that layer.

Most business knowledge sits outside the tables.
Contracts hold limits and commitments. Forms hold reported transactions. Recorded calls hold what a customer said.
None of this has a semantic layer.

Retrieval alone does not close the gap.
It returns passages, not meaning.
It does not record which field a clause gives a value for, which customer or order it concerns, or whether it states the value or only discusses it.
So a delivery date in a contract cannot be compared with the order table, and the access rules of the tables do not reach the documents.

## The approach

The Semantic Workspace takes the existing semantic layer as the target.
It does not build a second model beside it, and it never edits it.

- **Documents join the model you already govern.** Evidence links attach passages to its fields and metrics.
- **Business vocabulary grows from evidence.** Concepts are anchored on the model first. Only what the model cannot explain is proposed as new.
- **People stay in control.** A person approves, corrects or rejects. Only a published release reaches answers.
- **One access policy.** A source a reader may not see is never read, cited or summarised for that reader.

## How it works

![Overview of the system: build time, question time and one question](assets/overview.png)

*The system at a glance. (a) Build time turns sources into evidence links and a concept catalog. (b) Question time routes each request to one of four fixed tools. The right column follows one question to its cited answer.*

### Build time

1. **Ingest.** PDFs, fill-in forms, audio and text become passages. Each passage is pinned to its source by hash.
2. **Link (Pipeline A).** A small language model chooses among retrieved definitions. Code then checks the stated value, settles the relation and attaches entity keys.
3. **Organise (Pipeline B).** Concepts of the model collect their evidence by aggregation. A model proposes concepts only for the remainder.
4. **Review and release.** A reviewer approves, corrects or rejects. A release fixes the result as a versioned file for Semantic Hub.

### Question time

A request goes to one of four tools: answer a question, explain a term, write a table, or list the connections.
Code rules choose the tool. A model helps only when a request to act is worded in a way the rules do not complete.

- **Answers are computed, not generated.** A guarded SQL compiler computes every number. The model plans the query and reads clause values.
- **Every answer shows its proof.** The reply names the definitions used, the facts with their filters, and up to four cited passages.
- **The app asks instead of guessing.** An ambiguous request gets a clarification with real choices from the semantic layer.
- **Follow-ups keep their context.** A chat session carries the earlier turns, so "and for 2022?" works.

## The design rule

One rule runs through the design: **the model chooses, code decides.**

Language models are used where language must be read: choosing a definition, proposing a concept, planning a question.
Deterministic code makes every decision that must be exact and repeatable: values, numbers, status, citations and access.
This keeps results explainable and auditable.

## What you get

| Result | What it is | Who uses it |
| --- | --- | --- |
| **Evidence links** | Passage to definition, with relation, value, unit, period and entity key | Reviewers, the answer path |
| **Concept catalog** | Business concepts linked to the model, versioned and published as a release | Data stewards, Semantic Hub |
| **Table rows** | Stated values written to declared Iceberg tables through Cloudera Lakehouse Engine (Trino) | Data engineers, analysts |
| **Cited answers** | One reply per question, with facts, citations and the path taken | Business users |

## Built for governance

- **Synthetic and public sample data** ship with the AMP. The model provider you configure receives only the content it is asked to process.
- **Identifier masking** hides account numbers and customer IDs from readers who may not see them.
- **Writes need a person.** Every write shows a preview and needs a separate confirm.
- **English and Simplified Chinese** are supported in the interface, and questions can be asked in either language.

## On the Cloudera platform

- **Cloudera AI** hosts the application and its jobs as an AMP.
- **Cloudera Lakehouse Engine (Trino)** is the direct write path for table rows to Iceberg.
- **Semantic Hub** is the target of the release export: an Ossie file that passes its schema check and lint.

## Two ready-made examples

| Dataset | Structured data | Unstructured sources |
| --- | --- | --- |
| Financial Services | A mock-up banking workbook, 11 tables, 143 definitions | Synthetic banking documents, fill-in forms and one recorded call |
| Manufacturing | AdventureWorks sample data, 25 tables, 240 definitions | Synthetic contracts and AdventureWorks sample documents |

See [Data](data.md) for what is synthetic, what is public and the attribution.

## Where to go next

**Understand the design**

1. [Concepts](concepts.md): the task, the terms and a glossary.
2. [Architecture](architecture.md): build time, question time, components and data stores.
3. [Ingestion](ingestion.md): how each file type becomes passages.
4. [Pipeline A](pipeline-a.md): passages to evidence links.
5. [Pipeline B](pipeline-b.md): evidence links to a concept catalog.
6. [Review and release](review-and-release.md): human decisions and what reaches answers.

**Use and operate**

- [Ask](ask.md): the router, chat sessions, clarifications, answers and citations.
- [Table rows](output-c.md): writing stated values to Iceberg.
- [Access and masking](access-and-masking.md): accounts, withheld sources and identifier masking.
- [Localization](localization.md): the English and Chinese language switch.
- [Deployment](deployment.md): the AMP on Cloudera AI.

**Go deeper**

- [Evaluation](evaluation.md): the measured results and the scope of each.
- The working paper, *Mapping Unstructured Documents onto a Governed Semantic Layer: Evidence Links and an Anchored Concept Catalog*:
  [`docs/paper/main.pdf`](https://github.com/FerdinandZhong/unstructured-semantic-workspace/blob/main/docs/paper/main.pdf).
