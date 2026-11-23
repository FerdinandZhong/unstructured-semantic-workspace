from semantic_pipeline_v2 import model_cache


def test_same_content_is_parsed_once_and_copies_are_private(monkeypatch):
    calls = []
    real = model_cache.yaml.safe_load
    monkeypatch.setattr(model_cache.yaml, "safe_load", lambda raw: calls.append(1) or real(raw))
    model_cache._parse.cache_clear()
    first = model_cache.load_yaml(b"a: [1, 2]\n")
    first["a"].append(3)
    assert model_cache.load_yaml("a: [1, 2]\n") == {"a": [1, 2]} and len(calls) == 1
    assert model_cache.load_yaml(b"a: [9]\n") == {"a": [9]} and len(calls) == 2
