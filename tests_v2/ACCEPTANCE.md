# Acceptance scenarios fixed before implementation — 2026-11-16
1. Deny protected roots, ancestors, and resolved symlink overlaps before creating state. Require explicit instance identity.
2. Load actual pinned manufacturing Ossie bytes with checksum. Stable normalized IDs survive reload. Reject duplicate IDs, dangling joins, empty definitions, unsupported expressions/version, checksum mismatch. Candidate activation only in qualification mode; normal activation needs approval matching revision. Failed replacement preserves active revision.
3. Use real LanceDB 0.41.0. Retry same scoped unit idempotently. Preserve raw hash bytes. Pre-filter scope. Reject vector dimension and space mismatch. Add provenance-bearing derived feature; read previous table version.
4. Discovery starts empty and reads only selected indexed units. Inject recorded open vocabulary concept with exact quote. Reject invalid quote, replay conflict and unknown unit. Never import reference catalog/evaluation labels.
5. Query actual read-only SQLite using fixed parameterized query. Verify customer 29641/product/period fixture returns 51. Missing SQLite stays unavailable while definitions load.
6. Offline smoke blocks socket connections, uses temporary roots, <=20 units, four-dimensional test-only vectors, one writer, no port.
