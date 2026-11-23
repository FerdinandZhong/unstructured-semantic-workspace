# Concepts

The task is a mapping.
Given a semantic layer, decide for each unstructured passage which definitions it states a value of or is about.
Or abstain.
Then group that evidence into business concepts linked to the layer.
The layer is never edited.

## The semantic layer

A semantic layer describes the tables of a warehouse.
It names each field and metric and defines it.
It declares how tables join.

In this system the layer is an Ossie YAML file per dataset.
It holds tables, fields with descriptions and datatypes, declared relationships and metrics.
The layer is given, not learned.
The system reads it and never changes it.

| Dataset | Definitions in the layer |
| --- | --- |
| Financial Services | 143 |
| Manufacturing | 240 |

Each definition has a text card, a unit and a table.
Declared relationships are many-to-one links between columns.

## Inputs

The paper lists six inputs. The system learns or changes none of them.

| Input | What it is |
| --- | --- |
| Structured data | Tables with rows. CSV projections loaded read-only into private PostgreSQL. |
| Semantic model | Tables, fields, relationships and metrics in an Ossie file. |
| Unstructured sources | Documents, fill-in forms and audio. Files are pinned by SHA-256. Each carries a data label. |
| Access policy | Principals with roles and datasets, and the sources withheld from each restricted principal. |
| Human decisions | Corrections, approvals and rejections, the confirm of a build or a write, the publish of a release. |
| Question | A natural-language question and the asking principal. |

Each source carries a data label: synthetic, public, or a cleaned sample file.
Public files have scope `extraction_only`.
They are linked to definitions.
They are never induced into concepts and never tied to an entity.

## Passages

A passage is a chunk of a readable source.
It has text and context.
The context is the title, the nearest heading and, for a call, the speaker.
See [Ingestion](ingestion.md) for how each file type is split.

## Evidence links

An evidence link records what a passage says in the terms of the layer.
It holds the chunk, the definition, the relation, the stated value, the unit and the period.
It also holds the entity keys of the chunk.

A passage can have up to three links, or none.
None is a recorded outcome, not a gap.

### Relations

| Relation | Meaning |
| --- | --- |
| `states_value_of` | The passage states a value of this definition for an identifiable entity. |
| `is_about` | The passage discusses the definition but does not state its value. |
| `none` | No definition fits. The passage abstains. |

The relation rule is strict.
`states_value_of` needs a value of that definition for an identifiable entity.
A promise, a target, an allegation, or a different unit, grain or period is `is_about` at most.

### Canonical definition

Two fields joined by a declared relationship can hold one key.
The canonical map follows a foreign-key column to the primary key it references.
Each link stores both the selected definition and its canonical definition.

### Entity keys

Entity keys belong to the chunk, not to the selected definition.
A key is found in one of three ways.

- **Stated.** The chunk holds an existing value of a key column.
- **Derived.** A stated key reaches another table through a declared relationship.
- **Inherited.** The source states exactly one key of a table, and this chunk states none.

A mixed alphanumeric identifier resolves anywhere.
A bare number resolves only next to a label derived from the model.
A name alone resolves nothing.

## Concepts

A concept is a business idea found in the evidence.
Each concept is linked to definitions of the layer by a mapping predicate.

| Origin | Meaning |
| --- | --- |
| `both` | Anchored on a structured concept of the layer |
| `document` | Found in documents only |

The predicates come from SKOS: `exactMatch`, `closeMatch` and `relatedMatch`.
Code computes the predicate from evidence counts.
A model does not choose it.
See [Pipeline B](pipeline-b.md).

## The layer is never edited

The system does not change the semantic model.
It does not merge entities or customers.
It writes nothing to a shared table without a human confirm after a visible preview.

A concept points at definitions. It does not add fields to the layer.
A release carries the base model plus the catalog in an extension block.
The base model inside the release is unchanged.

## Outputs

| Output | What it holds |
| --- | --- |
| A. Evidence links | For each passage, the definitions it states a value of or is about, or none |
| B. Concept catalog | Business concepts found in the evidence, linked to definitions |
| The answer | One reply per question, every number computed by SQL or code |

The paper defines outputs A and B.
The workspace also writes table rows as a system feature. See [Table rows](output-c.md).

## Glossary

Abstention
:   A passage with no fitting definition keeps no link. The outcome is stored.

Anchored concept
:   A concept of the layer that collects its evidence by aggregation over links, with no model call.

Candidate
:   A concept or concept link below minimum support. It is stored and shown, not exported.

Canonical definition
:   The primary-key definition a foreign-key definition resolves to through declared relationships.

Definition card
:   The text of one definition used for retrieval: label, synonyms, description.

Degraded
:   A chunk whose model reply held no valid item.

Evidence link
:   A record (chunk, definition, relation, value, unit, period) with entity keys.

False-link rate
:   The share of passages with no fitting definition that still received a link.

Guard
:   A deterministic rule applied after the model decision.

Release
:   A versioned Ossie export of the approved catalog and the mapping corrections.

Residual
:   Passages whose links state no value. Only these reach the induction model.

Retrieval proximity
:   The combined keyword and embedding score of a definition for a passage. It is not a confidence.

Restricted reader
:   An account from which some sources are withheld.

Withheld source
:   A source a principal may not read. It is never read, cited or summarised for that principal.
