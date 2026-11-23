# Access and masking

Access is checked on every request.
A withheld source is never read, cited or summarised for the principal it is withheld from.
Restricted identifiers are masked in every JSON reply, at the response boundary.

## Accounts

The workspace ships five demonstration accounts.
They live in the private PostgreSQL.

| Account | Role | Datasets |
| --- | --- | --- |
| `admin` | admin | Financial Services, Manufacturing |
| `finance_reader` | reader | Financial Services |
| `finance_restricted` | restricted | Financial Services |
| `manufacturing_reader` | reader | Manufacturing |
| `manufacturing_restricted` | restricted | Manufacturing |

Roles grant capabilities.

| Role | Capabilities |
| --- | --- |
| admin | query, view insights, upload, process, review, publish, build the graph, build the catalog |
| reader | query, view insights |
| restricted | query, view insights; some sources withheld |

### Withheld sources

Each restricted account has sources withheld.

- Financial Services restricted reader: an adverse-media report and the recorded call.
- Manufacturing restricted reader: two contracts.

### Sessions

A session is a random token in a cookie.
The server stores only its SHA-256 hash.
A session lasts eight hours.
The first visit opens an admin session. Switching account replaces the session.

!!! note "Demonstration accounts"
    These accounts are for demonstration.
    They have no passwords.
    Use them only on a deployment that is itself access-controlled, such as a Cloudera AI application.

## Where access is enforced

| Place | Rule |
| --- | --- |
| Every `/api/*` route | The dataset must be granted to the principal |
| Chunks and links | A chunk from a withheld source is not visible |
| Concepts | A concept with any withheld evidence passage hides its examples and empties its synonyms for that reader |
| Document-only concepts | The meaning is replaced by a withheld notice |
| Answers | A concept with any withheld passage is left out; a question only a withheld source can answer is refused as "source withheld" |
| Releases | A release with content from a source the account cannot read is hidden |
| Fact detail | Withheld sources are refused with the compiler's own check |
| Progress trace | Names no source beyond what the reply itself says |
| Entity 360 | Every link, passage and concept passes the visibility check |

### Why concepts need care

A concept's label, meaning and synonyms are written by a model from all of its evidence.
A withheld passage could leak through them.
The paper reports such a fault, found late.
The catalog route returned a concept's examples, phrases copied from evidence chunks.
One came from a withheld source.
The fix hides examples, empties synonyms and replaces document-only meanings when any evidence passage is withheld.
A meaning anchored on the model stays, because it comes from the model.

## Identifier masking

The data model says of some columns: never display the value.
The server masks those values in every JSON reply.

| Kind | Columns matched by name |
| --- | --- |
| Account | `AccountNumber`, `AccountNo`, `AccNum`, `CounterpartyAccountNo` |
| Customer | `CustomerID` |
| Relationship manager | `RMID` |

### What is masked

The set of values to mask has two parts.

- Values the restricted columns hold. These are read once.
- Values that evidence links state for a restricted column, such as an identifier said on a call that no table holds.
  These are re-read every 20 seconds.
  A stated value needs a letter or eight digits, because a short bare number may be a misread amount.

Values of four characters or fewer are not collected.

### How it is masked

Every string in a reply is scanned for whole tokens that hold a digit.
A token equal to a restricted value is replaced.
A digit-only token that is part of a decimal or grouped number is left alone.
An integer field whose name is a restricted column is masked too.

The masked form keeps the tail:

```text
•••• 1234     (values of eight characters or more keep four)
•••• 34       (shorter values keep two)
```

Token matching uses ASCII classes only.
An identifier written next to Chinese text with no space still stands as a token.

### Exceptions

- Source identities (`id`, `sourceId`, hashes) are left intact.
- The Entity 360 route keeps customer and relationship-manager identifiers, because it is searched by them. Account numbers stay masked there.

### Identity numbers

National identity numbers in extracted text are masked before the text reaches the browser.
The pattern comes from the inherited field extractor in `multimedia_rag/fields.py`.
Stored form cells hold the masked value.

## Entities are never merged

Entities keep the key they are known by.
A name in a document never resolves to a customer identifier.
A customer identifier stated in a call transcript may differ by one character from an identifier in the table.
The two are shown as similar and are never merged.

## Request origin

The server accepts same-origin requests only.

- The `Host` header must be the loopback address with the server port, or a host named in `V2_ALLOWED_HOSTS`.
- A cross-site or same-site fetch is refused.
- A request with an `Origin` header must come from the same host.
- A request that changes state must carry an `Origin` header.

Behind the Cloudera AI application proxy, set `V2_ALLOWED_HOSTS` to the application's public host name.
See [Deployment](deployment.md).
