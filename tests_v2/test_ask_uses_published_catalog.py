"""The chat route must hand the published catalog to the answer path, like `/api/answer` does.

Found in the recorded rehearsal of 23 November 2026: after a publish, `/api/ask` replies said
"No published release" while `/api/answer` reported release 2 for the same question.
"""
from types import SimpleNamespace

from semantic_pipeline_v2 import answer_path, ask_actions


def test_the_ask_route_passes_the_scoped_published_catalog(monkeypatch):
    seen = {}

    def fake_catalog(releases, dataset, visible):
        seen["catalog_args"] = (releases, dataset, callable(visible))
        return {"marker": True}

    def fake_answer(question, workspace, principal, **kw):
        seen["catalog"] = kw.get("catalog")
        return {"status": "refused", "facts": []}

    monkeypatch.setattr(answer_path, "published_catalog", fake_catalog)
    monkeypatch.setattr(answer_path, "answer", fake_answer)
    server = SimpleNamespace(review_store=None, chunk_index=None, evidence_links=None, interpreter=None,
                             chunk_embedder=None, releases="RELEASES",
                             answer_details=SimpleNamespace(register=lambda reply, dataset, principal: reply))
    req = SimpleNamespace(server=server,
                          workspace_payload=lambda principal, include_corrections=False: {"datasets": [{"id": "d1"}]})
    monkeypatch.setattr("semantic_pipeline_v2.workspace_server.entity_keys_of", lambda server, workspace: None)
    ask_actions._answer(req, {"id": "admin"}, SimpleNamespace(remember_question=lambda *a: None), "d1", "q?")
    assert seen.get("catalog") == {"marker": True}
    assert seen["catalog_args"] == ("RELEASES", "d1", True)
