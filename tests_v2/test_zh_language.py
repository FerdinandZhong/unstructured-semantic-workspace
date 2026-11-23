"""ZH-T7 and ZH-T8 (plan 2026-11-23-v2-zh-amp, steps Z6 and Z7): Chinese answers and Chinese questions.

The browser sends `X-V2-Lang: zh-CN`; the server reads it in one place. Without it, or with any other value, every
prompt and reply is byte-for-byte what it was before (the prompt digests below are those of 23 November 2026).
All identifiers, documents and figures here are made up. No provider is called: every model is a fake.
"""
import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

import semantic_pipeline_v2.answer_path as ap
from semantic_pipeline_v2 import ask_actions, ui_lang, workspace_server
from semantic_pipeline_v2.ask_router import route
from semantic_pipeline_v2.interpreter import ChatError
from test_answer_plan import MFG, Script, plan, run
import test_workspace
from test_workspace import running_server

PKG = Path(__file__).resolve().parents[1] / "semantic_pipeline_v2"
DIGESTS = {"READ": "fd17bdfa58f05ffc1d13c5be73156ce906a1d124dded1c064c589451d87adb87",
           "PLAN": "fe0baeaa541a403921c512da201f575a476edf2615e9408aa1abbea3d65b9ece",
           "HINT": "b4df60dcf60420ecd4192f1c7362f2891b33a9da300f98bae4593edbab9a61f5"}


@contextmanager
def language(value):
    token = ui_lang.use(value)
    try:
        yield
    finally:
        ui_lang.reset(token)


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


# ---------------------------------------------------------------- the header: one place, unknown values are English

def test_the_header_is_read_in_one_place_and_only_zh_cn_is_chinese():
    users = [p.name for p in PKG.glob("*.py") if "X-V2-Lang" in p.read_text() or "ui_lang.HEADER" in p.read_text()]
    assert sorted(users) == ["ui_lang.py", "workspace_server.py"], users
    assert (PKG / "workspace_server.py").read_text().count("ui_lang.HEADER") == 1
    for value in (None, "", "en", "fr", "zh", "zh-TW", "ZH-cn ", "zh-CN;q=1"):
        assert ui_lang.of(value) == "en", value
    assert ui_lang.of("zh-CN") == "zh-CN" and ui_lang.of(" zh-CN ") == "zh-CN"
    assert ui_lang.current() == "en"


def test_english_prompts_are_byte_for_byte_unchanged():
    assert digest(ap.READ_SYSTEM) == DIGESTS["READ"] and digest(ap.PLAN_SYSTEM) == DIGESTS["PLAN"]
    assert digest(ask_actions.HINT_PROMPT) == DIGESTS["HINT"]
    for value in (None, "en", "fr"):
        with language(value):
            assert ui_lang.chinese(ap.READ_SYSTEM, "the summary") is ap.READ_SYSTEM
            assert ui_lang.chinese(ask_actions.HINT_PROMPT, "the reason") is ask_actions.HINT_PROMPT


# ---------------------------------------------------------------- ZH-T7: a Chinese summary keeps exact figures

DOCS = {"warranty-qx7": "The warranty of the QX7 gearbox lasts 12000 hours from acceptance."}
NEED = {"need": "warranty length of the QX7 gearbox", "type": "text", "party": None, "required": True}
ASKED = "How long does the warranty of the QX7 gearbox last?"


def reading(summary):
    return Script([plan(evidence=[NEED], answer="text")],
                  [{"found": [{"need": 1, "clause": 1, "value": "12000 hours"}], "supporting": [], "summary": summary}])


def test_a_chinese_summary_is_kept_when_its_digits_match_and_the_prompt_asks_for_chinese(answer_pg):
    chat = reading("QX7 齿轮箱的保修期为自验收起 12000 小时。")
    with language("zh-CN"):
        result = run(answer_pg, MFG, ASKED, chat, DOCS)
    assert result["status"] == "answered" and result["conclusion"] == "12000 hours"
    assert result["summary"] == "QX7 齿轮箱的保修期为自验收起 12000 小时。"
    read_system = chat.calls[-1][0]
    assert read_system.startswith(ap.READ_SYSTEM) and read_system != ap.READ_SYSTEM
    assert "Simplified Chinese" in read_system and "Arabic digits" in read_system and "万" in read_system
    assert chat.calls[0][0] == ap.PLAN_SYSTEM            # the plan prompt never changes


@pytest.mark.parametrize("summary", ["QX7 齿轮箱的保修期为 1.2万 小时。", "QX7 齿轮箱的保修期为 36000 小时。",
                                     "保修期为 12000 小时，约 1.2 万小时。"])
def test_a_chinese_summary_that_writes_a_figure_not_allowed_is_dropped(answer_pg, summary):
    with language("zh-CN"):
        result = run(answer_pg, MFG, ASKED, reading(summary), DOCS)
    assert result["status"] == "answered" and result["conclusion"] == "12000 hours"
    assert "万" not in json.dumps(result, ensure_ascii=False) and "36000" not in json.dumps(result)
    assert result["summary"] != summary


def test_magnitude_words_are_dropped_even_when_the_digits_are_allowed():
    with language("zh-CN"):
        assert ap._adds_figure("约 1.2万 小时", {"1.2", "12000"})
        assert ap._adds_figure("约一万二千小时", {"12000"})
        assert not ap._adds_figure("共 12,000 小时", {"12000"})
    assert not ap._adds_figure("about 1.2万 hours", {"1.2"})      # English path: the guard is what it was
    assert ap._adds_figure("It lasts 999 months.", {"24"})


def test_the_english_read_prompt_is_unchanged_without_the_language(answer_pg):
    for value in (None, "en", "de"):
        chat = reading("It lasts 12000 hours from acceptance.")
        with language(value):
            result = run(answer_pg, MFG, ASKED, chat, DOCS)
        assert [s for s, _ in chat.calls] == [ap.PLAN_SYSTEM, ap.READ_SYSTEM]
        assert result["summary"] == "It lasts 12000 hours from acceptance."


# ---------------------------------------------------------------- the grouping hint

class Hint:
    def __init__(self, reply):
        self.reply, self.sent = reply, []

    def complete(self, system, user):
        self.sent.append((system, user))
        return self.reply


HINT_WS = {"knowledge": {"structured": {"fields": [{"name": "zq_orders/zq_ref", "title": "Order ref"}], "datasets": []}},
           "sources": [{"id": "s1", "title": "zqForm1.pdf"}]}
HINT_PLAN = {"sourceIds": ["s1"], "tables": [{"name": "x.zq_orders", "columns": [{"name": "zq_ref"}]}]}


def test_the_hint_prompt_gains_the_chinese_line_only_in_chinese():
    english = Hint('{"reason": "Both files fill the order ref field of zq_orders."}')
    assert ask_actions._hint(english, HINT_WS, HINT_PLAN, [{"title": "zqForm2.pdf"}])
    assert english.sent[0][0] == ask_actions.HINT_PROMPT
    chinese = Hint('{"reason": "两个文件都填写 zq_orders 表的 Order ref 字段。"}')
    with language("zh-CN"):
        assert ask_actions._hint(chinese, HINT_WS, HINT_PLAN, [{"title": "zqForm2.pdf"}]) == "两个文件都填写 zq_orders 表的 Order ref 字段。"
        assert ask_actions._hint(Hint('{"reason": "两个文件合计 1.2万 行，可以一起写入。"}'), HINT_WS, HINT_PLAN,
                                 [{"title": "zqForm2.pdf"}]) is None
    system = chinese.sent[0][0]
    assert system.startswith(ask_actions.HINT_PROMPT) and system != ask_actions.HINT_PROMPT and "Simplified Chinese" in system


# ---------------------------------------------------------------- ZH-T8: a Chinese question

EN = "What was the profit margin of the QX7 gearbox in 2031?"
ZH = "QX7 齿轮箱在 2031 年的利润率是多少？"
WRITE_EN, WRITE_ZH = "Write zqForm9 to a new table", "把 zqForm9 写入新表"


class Fake:
    """The rewriter (by its prompt), then plan replies. Records every call."""

    def __init__(self, rewrite=None):
        self.rewrite, self.calls = rewrite, []

    def complete(self, system, user):
        self.calls.append((system, user))
        if system == ui_lang.REWRITE_SYSTEM:
            reply = self.rewrite(user) if callable(self.rewrite) else self.rewrite
            if isinstance(reply, Exception):
                raise reply
            return reply if isinstance(reply, str) and not reply.startswith("=") else json.dumps({"english": (reply or "=")[1:]})
        return json.dumps(plan(unsupported="a profit margin"))

    def rewrites(self):
        return [u for s, u in self.calls if s == ui_lang.REWRITE_SYSTEM]

    def plans(self):
        return [u for s, u in self.calls if s == ap.PLAN_SYSTEM]


def rewriter(text):
    return "=" + {ZH: EN, WRITE_ZH: WRITE_EN}[text]


@pytest.fixture
def served(answer_pg, tmp_path, monkeypatch):
    prin = {"id": "p", "role": "admin", "datasets": [MFG], "withheld": []}
    monkeypatch.setattr(workspace_server.WorkspaceRequest, "principal", lambda _: prin)
    monkeypatch.setattr(workspace_server, "resolve_session", lambda *_: prin)
    slow = test_workspace.HTTPConnection        # the answer path over the manufacturing tables takes longer than 5 s
    monkeypatch.setattr(test_workspace, "HTTPConnection", lambda host, port, timeout=5: slow(host, port, timeout=120))
    fake = Fake(rewriter)
    with running_server(answer_pg, evidence_dir=tmp_path / "evidence", chunk_embedder=None, mapping_embedder=None,
                        interpreter=SimpleNamespace(chat=fake), catalog_chat=None, iceberg=None) as call:
        def ask(text, lang=None, path="/api/ask"):
            body = {"dataset": MFG, "text": text, "context": {}} if path == "/api/ask" else {"dataset": MFG, "question": text}
            status, out = call("POST", path, body, {"X-V2-Lang": lang} if lang else None)
            assert status == 200, out
            return out
        yield ask, fake


def without(value, *keys):
    return {k: v for k, v in value.items() if k not in keys}


def test_a_chinese_question_gives_the_route_and_plan_of_its_english_form(served):
    ask, fake = served
    english = ask(EN)
    assert fake.rewrites() == [] and "questionEnglish" not in english and "questionEnglish" not in english["result"]
    chinese = ask(ZH, "zh-CN")
    assert fake.rewrites() == [ZH]
    assert chinese["tool"] == english["tool"] == route(EN, {})["tool"] == "answer"
    assert chinese["question"] == ZH and chinese["questionEnglish"] == EN
    assert chinese["result"]["question"] == ZH and chinese["result"]["questionEnglish"] == EN
    assert fake.plans()[0] == fake.plans()[1]                       # the planner saw the same English question
    assert chinese["result"]["plan"] == english["result"]["plan"] and chinese["result"]["reason"] == english["result"]["reason"]
    assert without(chinese["result"], "question", "questionEnglish", "detail") == without(english["result"], "question", "detail")
    answered = ask(ZH, "zh-CN", path="/api/answer")
    assert answered["question"] == ZH and answered["questionEnglish"] == EN and answered["plan"] == english["result"]["plan"]


def test_a_chinese_write_request_goes_through_the_rewrite_then_the_normal_router(served):
    ask, fake = served
    english, chinese = ask(WRITE_EN), ask(WRITE_ZH, "zh-CN")
    assert chinese["tool"] == english["tool"] == route(WRITE_EN, {})["tool"] == "write"
    assert chinese["result"] == english["result"] and chinese["questionEnglish"] == WRITE_EN


def test_no_rewrite_in_english_mode_or_for_an_english_question(served):
    ask, fake = served
    english = ask(EN)
    for lang in (None, "en", "fr", "zh"):
        out = ask(ZH, lang)
        assert "questionEnglish" not in out and out["result"]["question"] == ZH
    assert fake.rewrites() == []
    in_chinese_mode = ask(EN, "zh-CN")
    assert fake.rewrites() == [] and "questionEnglish" not in in_chinese_mode
    assert without(in_chinese_mode["result"], "detail") == without(english["result"], "detail")


@pytest.mark.parametrize("reply", [
    ChatError("The provider did not return a usable reply (URLError)."),   # the call fails
    "=",                                                                    # empty
    "not json",
    '{"english": 7}',
    "=齿轮箱的利润率是多少",                                                   # no Latin letters
    "=What was the profit margin of the gearbox in 2031?",                  # an identifier dropped
    "=What was the profit margin of the QX7 gearbox?",                      # a number dropped
    "=" + "What was the profit margin of the QX7 gearbox in 2031? " * 6,   # far longer than the question
])
def test_an_unusable_rewrite_is_a_refusal_never_a_guess(served, reply):
    ask, fake = served
    fake.rewrite = reply
    for path in ("/api/ask", "/api/answer"):
        out = ask(ZH, "zh-CN", path)
        result = out["result"] if path == "/api/ask" else out
        assert result["status"] == "refused" and result["refusalReason"] == ui_lang.NOT_UNDERSTOOD
        assert result["reason"] == ui_lang.REFUSAL and result["question"] == ZH and "questionEnglish" not in result
        assert not result["facts"] and result["conclusion"] is None
    assert fake.plans() == []                     # nothing was planned from a rewrite that failed a check


def test_the_rewrite_keeps_quoted_text_and_needs_a_model():
    with language("zh-CN"):
        with pytest.raises(ui_lang.NotUnderstood):
            ui_lang.english(None, ZH)
        quoted = '名为“QX7 主轴”的零件有多少库存？'
        with pytest.raises(ui_lang.NotUnderstood):
            ui_lang.english(Fake("=How much stock does the part QX7 spindle hold?"), quoted)
        assert ui_lang.english(Fake('=How much stock does the part named "QX7 主轴" hold?'), quoted) \
            == 'How much stock does the part named "QX7 主轴" hold?'
        assert ui_lang.english(Fake("=x"), "What about QX7?") is None              # no CJK: no call, no rewrite
    assert ui_lang.english(Fake("=x"), ZH) is None                                  # English mode: never
