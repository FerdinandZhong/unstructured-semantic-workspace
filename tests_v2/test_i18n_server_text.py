"""ZH-T5 (plan 2026-11-23-v2-zh-amp): every code, label and fixed sentence the server can send to the Ask UI has a
non-empty Chinese entry in docs/design/v2-workspace/i18n/zh-CN.server.js. The server keeps English (decision D2); the
browser looks the text up. Sentences built with an f-string are matched by a `{placeholder}` pattern key."""
import ast
import json
import re
from pathlib import Path

import semantic_pipeline_v2.answer_path as ap
from semantic_pipeline_v2 import ask_actions, ask_router, ask_trace, connections, trino_target, ui_lang
from semantic_pipeline_v2.ask_actions import HINT_LABEL, JOB_MESSAGES, NOT_CHOSEN, NOT_SUBMITTED, SPARK_DOWNLOAD
from semantic_pipeline_v2.ask_router import WHY

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "semantic_pipeline_v2"
DICT = ROOT / "docs/design/v2-workspace/i18n/zh-CN.server.js"


def dictionary():
    text = DICT.read_text()
    body = text[text.index("register('zh-CN', ") + len("register('zh-CN', "):text.rindex(")")]
    return json.loads(body)


def shape(text):
    """A sentence with its interpolated parts as `{}`, so a key `{title} is not a recording` matches an f-string.
    Adjacent parts count as one (`{known}{more}` is one value in the sent sentence)."""
    return re.sub(r"(\{\})+", "{}", re.sub(r"\{\w*\}", "{}", text))


def literal(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        return ["".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)]
    if isinstance(node, ast.IfExp):
        return literal(node.body) + literal(node.orelse)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):     # "The ... reached: " + str(error)
        return [a + b for a in literal(node.left) or ["{}"] for b in literal(node.right) or ["{}"]]
    return []          # str(error) and the like: text from elsewhere


def sentences(name):
    """Fixed messages of `_Refusal(status, message)` and `{"error": message}` in one module."""
    out = []
    for node in ast.walk(ast.parse((PKG / name).read_text())):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_Refusal" and len(node.args) == 2:
            out += literal(node.args[1])
        if isinstance(node, ast.Dict):
            out += [s for k, v in zip(node.keys, node.values)
                    if isinstance(k, ast.Constant) and k.value == "error" for s in literal(v)]
    return out


def answer_codes():
    tree = ast.parse((PKG / "answer_path.py").read_text())
    reasons, statuses = set(), {"answered"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_refusal":
            statuses |= set(literal(node.args[1]))
            reasons |= {s for k in node.keywords if k.arg == "refusalReason" for s in literal(k.value)}
    return reasons, statuses


def calls(name, function, *positions):
    """String arguments at `positions` of every call of `function` in one module."""
    out = []
    for node in ast.walk(ast.parse((PKG / name).read_text())):
        if isinstance(node, ast.Call) and (getattr(node.func, "id", None) or getattr(node.func, "attr", None)) == function:
            out += [s for i in positions if i < len(node.args) for s in literal(node.args[i])]
    return out


# Fixed sentences of the answer, entity, connection and write paths (ZH-T5 extended, 23 November 2026). A sentence
# whose shape starts with a placeholder ("{n} rows failed ...") cannot be matched by V2I18n.server() (a pattern must
# start with literal text), so it stays English and is not listed.
ANSWER_DETAIL = ["previous period", "next period", "completes the question", "nearest in the data",
                 "The field holds more than 12 values."]
ENTITY = ["Generated summary, for review", "Known from documents only", "Customer ID stated in a document; no Customers row",
          "Known from documents only. No reviewed link connects it to a row of the Customers table.",
          "A Customer ID that a document states. No row of the Customers table holds it.",
          "No model is configured on this server, so no summary was written.", "There are no facts or passages to summarize.",
          "The field's table is not in the model.", "No key in the passage identifies a row of this table.",
          "No row holds this value.", "No rows for this entity in the structured tables: it is known from documents only.",
          "No link states that {label} is a customer of the Customers table. It is shown separately (not linked).",
          "The Customers table holds no customer name. The customer is identified by its Customer ID.",
          "No document passage this account may read is linked to this entity.",
          "No concept of the catalog draws on this entity's passages.",
          "Sources withheld for this account are left out of this report.",
          "The model call failed. The rest of the report does not depend on it.",
          "Every sentence the model wrote failed validation.", "Several rows match, so there is no one recorded value.",
          "No transactions for this customer in the transaction tables.",
          "No passage states a value that the tables also record for this customer.",
          "The workspace scopes this document to the entity.", "The passage states this Customer ID.",
          "The passage names the entity.", "The one Customer ID this document states."]
WRITE = ["Waits for Confirm write.", "The answer covers {total} rows; only the first {n} are written.",
         "At most {cap} rows are written.", "Their evidence links fill the same tables, so one job can write them together.",
         "Written to the local database: {n} rows", "The written table could not be read back.",
         "Written through Trino when you confirm.", "Submitted after the local write when you confirm.",
         "The Iceberg connection is not ready: {problem}. The rows are written to the local database only.",
         "Iceberg: Spark job {name} submitted to Cloudera Data Engineering", "Iceberg: Spark job {name} {status}: {message}",
         "Iceberg: The Trino write failed: {error}", "The Trino write failed: {error}",
         "Iceberg: Written through Trino: {written} new rows; read back {found} of {expected}.",
         "Iceberg: Written through Trino: {written} new row; read back {found} of {expected}.",
         "Written through Trino: {written} new rows; read back {found} of {expected}.",
         "Written through Trino: {written} new row; read back {found} of {expected}.",
         "The lakehouse will refuse the write to {name}: {reason}.",
         trino_target.MASKED[0].upper() + trino_target.MASKED[1:] + ".",
         "The lakehouse will refuse the write to {name}: " + trino_target.MASKED + ".",
         "A correction was made after the preview. Preview the rows again.",
         "The private PostgreSQL answered.", "The private PostgreSQL did not answer.", "Local database",
         ui_lang.REFUSAL, ui_lang.NOT_UNDERSTOOD]
CONNECTIONS = ["Iceberg settings are incomplete: {missing}", "Only Iceberg lakehouse.demo_silver is authorized",
               "Job submission is disabled (enabled)",
               "No sign-in: set cde_token, or cdp_endpoint with access_key_id and access_key_secret",
               "The cdp command is not installed on the server; paste a CDE token instead",
               "The local PostgreSQL connection is managed by the server and is read-only",
               "V2_ICEBERG_SETTINGS_DIR is not set for the server", "Sign-in by access key failed (cdp exit {code}): {message}",
               "The control plane reply had no token", "Unknown connection field: {name}", "Only catalog lakehouse is authorized",
               "Only namespace demo_silver is authorized", "The workspace settings file could not be read",
               "Sign-in by access key timed out", "The cdp command could not run ({error})", connections.POLARIS_NOTE]


def answer_sentences():
    """Refusal and clarification reasons of answer_path: the third argument of `_refusal`, and the combined sentence of
    a missing document value with its tail."""
    raised = [s for e in ("PeriodOutside", "UnknownRecord", "LookupError") for s in calls("answer_path.py", e, 0) if " " in s]
    shaped = [s for s in calls("answer_path.py", "_refusal", 2) + raised if not s.startswith("{")]
    return shaped + ["No readable document states {needs} for {ids}.",
                     "No readable document states {needs} for {ids}. A source this account cannot read may hold it."]


def expected():
    reasons, statuses = answer_codes()
    assert {"source withheld", "no supported answer", "model unavailable"} <= reasons and {"refused", "clarify"} <= statuses
    steps = [item for name in ("ANSWER", "PREVIEW", "CONFIRM", "SEMANTIC", "CONNECTIONS") for item in getattr(ask_trace, name)]
    texts = {*reasons, *statuses, *(k for k, _ in steps), *(t for _, t in steps),
             *WHY.values(), *connections.LABELS.values(), trino_target.LABEL, SPARK_DOWNLOAD, HINT_LABEL,
             NOT_CHOSEN, NOT_SUBMITTED, *JOB_MESSAGES, *JOB_MESSAGES.values(),
             "written", "submitted", "failed",              # write statuses (ask_actions confirm)
             "waiting", "running", "done", "skipped"}       # trace step states
    fixed = sentences("ask_actions.py") + sentences("entity_report.py")
    assert len(fixed) >= 40, len(fixed)
    more = [*sentences("answer_detail.py"), *ANSWER_DETAIL, *ENTITY, *WRITE, *CONNECTIONS, *answer_sentences(),
            *calls("connections.py", "check", 0, 2)]
    assert len(answer_sentences()) >= 26, answer_sentences()
    return texts | set(fixed) | {s for s in more if not s.startswith("{")}


def test_every_server_code_and_sentence_has_a_chinese_entry():
    entries = dictionary()
    by_shape = {shape(k): v for k, v in entries.items()}
    missing = sorted(text for text in expected() if not str(by_shape.get(shape(text), "")).strip())
    assert not missing, missing


def test_dictionary_values_keep_their_placeholders():
    for key, value in dictionary().items():
        assert value.strip(), key
        assert sorted(re.findall(r"\{\w+\}", key)) == sorted(re.findall(r"\{\w+\}", value)), key


def test_i18n_files_are_served_and_loaded_before_the_pages():
    from semantic_pipeline_v2.workspace_server import ASSETS
    page = (ROOT / "docs/design/v2-workspace/index.html").read_text()
    scripts = re.findall(r'<script src="([^"]+)" defer></script>', page)
    names = ["i18n.js"] + sorted(p.relative_to(DICT.parents[1]).as_posix() for p in DICT.parent.glob("*.js"))
    assert scripts[:len(names)] == names, scripts
    assert set(names) <= ASSETS


# ---------------------------------------------------------------- clarifications (24 November 2026)
# Every clarification reason, note and fixed hint label the answer and write paths can send has a Chinese entry, and a
# sentence that starts with a value is matchable: its key must hold LEAD literal characters (the i18n.js rule).
LEAD = 24


def module_constants(name):
    tree = ast.parse((PKG / name).read_text())
    return {t.id: n.value.value for n in tree.body if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant)
            and isinstance(n.value.value, str) for t in n.targets if isinstance(t, ast.Name)}


def resolved(node, constants):
    """Literal text of a hint value: a string, an f-string, a module constant, or a constant's `.format(...)`."""
    if isinstance(node, ast.Name):
        return [constants[node.id]] if node.id in constants else []
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
        return resolved(node.func.value, constants)
    return literal(node)


def clarification_texts():
    out = []
    for name in ("answer_path.py", "metric_sql.py"):
        for node in ast.walk(ast.parse((PKG / name).read_text())):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "ClarificationNeeded":
                out += [s for k in node.keywords if k.arg == "message" for s in literal(k.value)]
    constants = module_constants("answer_path.py")
    out += [s for node in ast.walk(ast.parse((PKG / "answer_path.py").read_text())) if isinstance(node, ast.Call)
            and getattr(node.func, "id", None) == "_refusal" and len(node.args) > 2 for s in resolved(node.args[2], constants)]
    out.append("{value} is not a known value for {field}. Known values: {known}.")      # ClarificationNeeded's own text
    for name in ("answer_path.py", "ask_actions.py"):
        constants = module_constants(name)
        out += [v for k, v in constants.items() if k.startswith("NOTE_")]
        for node in ast.walk(ast.parse((PKG / name).read_text())):
            if isinstance(node, ast.Dict):
                out += [s for k, v in zip(node.keys, node.values) if isinstance(k, ast.Constant)
                        and k.value in ("label", "detail", "hintNote", "reason") for s in resolved(v, constants)]
            if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "append" and node.args \
                    and getattr(node.func.value, "attr", None) in ("caveats",):
                out += resolved(node.args[0], constants)
    router = module_constants("ask_router.py")
    out += [router[k] for k in ("CHOICES", "NAMED_TABLE")] + [ask_router.WITH_ROWS, ask_router.NO_ROWS]
    # text made only of values ("{name} ({table})", "{word} {value}") holds nothing to translate
    return sorted({s for s in out if re.search(r"[A-Za-z]{3}", re.sub(r"\{\w*\}", "", s))})


def test_every_clarification_reason_note_and_hint_label_has_a_chinese_entry():
    texts = clarification_texts()
    assert len(texts) >= 60, len(texts)
    for must in (ap.ONE_TOTAL, ap.NOTE_RANGE, ap.NOTE_EXAMPLES, ask_actions.ALL_FILES, ask_actions.ROWS_FOR, ap.NOT_PLANNED):
        assert must in texts, must
    by_shape = {shape(k): k for k in dictionary()}
    missing = [t for t in texts if shape(t) not in by_shape]
    assert not missing, "\n".join(missing)
    short = [by_shape[shape(t)] for t in texts if t.startswith("{") and len(re.sub(r"\{\w*\}", "", by_shape[shape(t)]).strip()) < LEAD]
    assert not short, short
