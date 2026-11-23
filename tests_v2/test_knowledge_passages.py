from semantic_pipeline_v2.access import project_workspace
from semantic_pipeline_v2.mapped_evidence import map_pages
from semantic_pipeline_v2.workspace import load_workspace


def test_table_passages_keep_every_row_and_use_actual_values():
    data = load_workspace("financial-services")
    source = next(item for item in data["sources"] if item["kind"] == "adverse-media")
    result = map_pages(data, source["id"], source["citation"]["sha256"], [{
        "index": 0, "markdown": "# Report\n[tbl-0.md](tbl-0.md)\n[tbl-1.md](tbl-1.md)",
        "tables": [{"id": "tbl-0.md", "content": "| Account balance | SGD 33,120.10 |\n| --- | --- |\n| Account type | Current |"},
                   {"id": "tbl-1.md", "content": "| Amount | SGD 156,750.00 |\n| --- | --- |\n| Currency | SGD |"}],
    }])
    tables = [p for p in result["passages"] if p["kind"] == "table"]
    assert [p["title"] for p in tables] == ["Account balance — SGD 33,120.10", "Amount — SGD 156,750.00"]
    assert [p["rows"][-1] for p in tables] == [["Account type", "Current"], ["Currency", "SGD"]]
    assert all(len(p["observationIds"]) == 2 for p in tables)


def test_native_article_replay_preserves_qualification_and_scope():
    from semantic_pipeline_v2.workspace import ROOT
    data = load_workspace("financial-services")
    source = next(item for item in data["sources"] if item["kind"] == "adverse-media")
    original = (ROOT / source["citation"]["path"]).read_text()
    result = map_pages(data, source["id"], source["citation"]["sha256"],
                       [{"index": 0, "markdown": original}], kind="native_text")
    article, = result["passages"]
    assert article["text"] == "\n".join(original.splitlines()[11:19])
    assert "scrutiny from counterparties" in article["text"]
    assert "unverified open-source material" in article["text"]
    assert all(o["citation"]["startLine"] >= 12 for o in result["observations"] if o["observationRole"] == "assertion")
    reader = {"datasets": [data["id"]], "role": "reader", "withheld": [source["id"]]}
    projected = project_workspace({"datasets": [data], "reviewAvailable": False}, reader)["datasets"][0]
    assert all(p["sourceId"] != source["id"] for p in projected["knowledge"]["passages"])
