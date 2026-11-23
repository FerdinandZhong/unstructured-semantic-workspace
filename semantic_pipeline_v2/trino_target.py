"""Write confirmed rows to Iceberg through Trino.

The Spark job signs in to the catalog with a workload secret; in `lakehouse.demo_silver` that identity may not create
tables. The platform user may, through Trino, so this is the write route there (user decision, 23 November 2026).
It writes exactly the tables the Spark job would: same names, columns, rows and insert-only rule."""
import base64
import json
import re
import ssl
import time
import urllib.error
import urllib.request

NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
TYPES = {"STRING": "varchar", "BIGINT": "bigint", "DATE": "date", "TIMESTAMP": "timestamp(6)"}
LABEL = "Iceberg through Trino"


class TrinoError(Exception):
    pass


def _type(spark):
    decimal = re.fullmatch(r"DECIMAL\((\d+),(\d+)\)", spark)
    if decimal:
        return f"decimal({decimal[1]},{decimal[2]})"
    if spark not in TYPES:
        raise ValueError(f"No Trino type for {spark}")
    return TYPES[spark]


def _literal(value):
    return "CAST(NULL AS varchar)" if value is None else "'" + str(value).replace("'", "''") + "'"


def statements(table):
    """(create, insert, check) for one table of the job payload. Every name must be a plain identifier; every value is a
    quoted literal. The insert adds a row only when no row has the same key, so a replay adds nothing."""
    target, columns = table["target"], [(n, _type(t)) for n, t in table["columns"]]
    if not all(NAME.match(part) for part in target.split(".")) or target.count(".") != 2 \
            or not all(NAME.match(n) for n, _ in columns) or not set(table["keys"]) <= {n for n, _ in columns}:
        raise ValueError("A table or column name is not a plain identifier")
    kinds = dict(columns)
    names = ", ".join(n for n, _ in columns)
    values = "(VALUES " + ", ".join("(" + ", ".join(_literal(v) for v in row) + ")" for row in table["rows"]) + f") AS s({names})"
    # One combined key per row, compared with a single "=". Several IS NOT DISTINCT FROM conditions over a multi-row
    # VALUES list gave a wrong (too small) match count on Trino on 23 November 2026, which let a replay insert a duplicate.
    # chr(0) stands for NULL and chr(1) separates the parts; neither occurs in a value.
    part = lambda side, k: (f"coalesce(CAST(t.{k} AS varchar), chr(0))" if side == "t"
                            else f"coalesce(CAST(CAST(s.{k} AS {kinds[k]}) AS varchar), chr(0))")
    key = lambda side: " || chr(1) || ".join(part(side, k) for k in table["keys"])
    match = f"{key('t')} = {key('s')}"
    return (f"CREATE TABLE IF NOT EXISTS {target} ({', '.join(f'{n} {t}' for n, t in columns)})",
            f"INSERT INTO {target} SELECT {', '.join(f'CAST(s.{n} AS {t})' for n, t in columns)} FROM {values}"
            f" WHERE NOT EXISTS (SELECT 1 FROM {target} t WHERE {match})",
            f"SELECT count(*) FROM {target} t WHERE EXISTS (SELECT 1 FROM {values} WHERE {match})")


class TrinoTarget:
    def __init__(self, run, check=None):
        self.run = run          # run(sql) -> rows; raises TrinoError
        self.check = check or run      # the same for a preview check, which must not wait long

    def write(self, payload):
        """{status, message, tables}: `found` is read back from the table. A failure is reported, never hidden."""
        done = []
        try:
            for table in payload["tables"]:
                create, insert, check = statements(table)
                self.run(create)
                written = int((self.run(insert) or [[0]])[0][0])
                found = int(self.run(check)[0][0])
                done.append({"table": table["target"], "expected": table["expected"], "written": written, "found": found})
        except (TrinoError, ValueError, LookupError, TypeError) as error:
            return {"status": "failed", "message": f"The Trino write failed: {_plain(str(error))[:400]}", "tables": done}
        written, found, expected = (sum(t[k] for t in done) for k in ("written", "found", "expected"))
        text = f"Written through Trino: {written} new row{'' if written == 1 else 's'}; read back {found} of {expected}."
        return {"status": "completed" if found == expected else "failed", "message": text, "tables": done}


MASKED = ("the table has a column masking policy that applies to the writing user, and Trino cannot insert into such a "
          "table. Ask the lakehouse admin to exclude the writing user from that policy")


def _plain(error):
    return MASKED[0].upper() + MASKED[1:] + "." if "column masks is not supported" in error else error


UNREACHABLE = "The lakehouse could not be reached, so this preview did not check whether it accepts the write."


def _refusals(self, payload):
    """One sentence per table the lakehouse will refuse, found without writing: Trino validates the insert and does not
    run it. A table that does not exist yet is not a refusal; the write creates it."""
    lines = []
    for table in payload["tables"]:
        name = table["target"]
        try:
            self.check(f"EXPLAIN (TYPE VALIDATE) INSERT INTO {name} SELECT * FROM {name} WHERE false")
        except TrinoError as error:
            text = str(error)
            if getattr(error, "connection", False):     # each further table would wait as long: say so once and stop
                return lines + [UNREACHABLE]
            if "does not exist" in text:
                continue
            lines.append(f"The lakehouse will refuse the write to {name}: "
                         + (MASKED if "column masks is not supported" in text else text[:200]) + ".")
    return lines


TrinoTarget.refusals = _refusals


def from_env(environ):
    """The Trino route, when V2_ICEBERG_ROUTE=trino and V2_TRINO_URL and CDE_TOKEN are set; else None. The user is the
    token's subject. TLS is verified against V2_TRINO_CA when given, else the system store."""
    url, token = (environ.get("V2_TRINO_URL") or "").rstrip("/"), environ.get("CDE_TOKEN") or ""
    if environ.get("V2_ICEBERG_ROUTE") != "trino" or not url or token.count(".") != 2:
        return None
    body = token.split(".")[1]
    user = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))).get("sub") or ""
    context = ssl.create_default_context(cafile=environ.get("V2_TRINO_CA") or None)
    if environ.get("V2_TRINO_CA"):      # the file may pin the server's own certificate, not its issuer
        context.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    headers = {"Authorization": "Bearer " + token, "X-Trino-User": user, "X-Trino-Source": "v2-workspace"}

    def run(sql, attempts=3, timeout=60):
        """Rows of one statement. A dropped connection is retried: each statement is safe to repeat (CREATE IF NOT
        EXISTS, an insert of missing rows only, a count)."""
        for attempt in range(attempts):
            try:
                return once(sql, timeout)
            except TrinoError as error:
                if not getattr(error, "connection", False) or attempt == attempts - 1:
                    raise
                time.sleep(2)

    def once(sql, timeout):
        try:
            reply = json.load(urllib.request.urlopen(urllib.request.Request(url + "/v1/statement", sql.encode(), headers),
                                                     context=context, timeout=timeout))
            rows, deadline = [], time.monotonic() + 300
            while True:
                rows += reply.get("data") or []
                if reply.get("error"):
                    raise TrinoError(str(reply["error"].get("message") or "query failed"))
                if not reply.get("nextUri"):
                    return rows
                if time.monotonic() > deadline:
                    raise TrinoError("the statement did not finish in 5 minutes")
                time.sleep(0.3)
                reply = json.load(urllib.request.urlopen(urllib.request.Request(reply["nextUri"], headers=headers),
                                                         context=context, timeout=timeout))
        except urllib.error.HTTPError as error:
            raise TrinoError(f"HTTP {error.code} from Trino") from None
        except (urllib.error.URLError, OSError, ValueError) as error:
            failure = TrinoError(str(error).replace(token, "***")[:300])
            failure.connection = not isinstance(error, ValueError)
            raise failure from None
    return TrinoTarget(run, lambda sql: run(sql, 1, 10))
