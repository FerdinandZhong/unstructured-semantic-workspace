"""Rehearsal UI fixes (23 November 2026), server side of the display: an evidence link says which fields a person
changed, so the UI can tell "Approved" (status only) from "Corrected" (target or value changed)."""
import json

from semantic_pipeline_v2.catalog_api import apply_links


def _link(**kw):
    return {"chunk_id": "c1", "definition_id": "d1", "stated_value": "5", "status": "proposed", "notes": {}, **kw}


def _rows(*patches):
    return {("evidence_link", json.dumps(["c1", "d1"])): [{"patch": p, "actor": "admin", "at": "t", "revision": i}
                                                           for i, p in enumerate(patches)]}


def test_an_approval_changes_nothing_and_an_edit_names_what_it_changed():
    [approved] = apply_links([_link()], _rows({"status": "corrected"}))
    assert approved["status"] == "corrected" and approved["changed"] == []
    [edited] = apply_links([_link()], _rows({"definitionId": "d2"}))
    assert edited["status"] == "corrected" and edited["changed"] == ["definition_id"] and edited["definition_id"] == "d2"
    [both] = apply_links([_link()], _rows({"statedValue": "6"}, {"status": "corrected"}))
    assert both["changed"] == ["stated_value"]
    [untouched] = apply_links([_link()], {})
    assert "changed" not in untouched
