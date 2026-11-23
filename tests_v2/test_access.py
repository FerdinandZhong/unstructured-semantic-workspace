"""Check the loopback demo policy with test-owned PostgreSQL."""

import os
from pathlib import Path
from urllib.parse import urlencode

import pytest

from semantic_pipeline_v2 import access, workspace
from test_workspace import running_server


def cookies(headers):
    return "; ".join(value.split(";", 1)[0] for name, value in headers if name == "Set-Cookie")


def original(dataset, source):
    return "/api/original?" + urlencode({"dataset": dataset, "sourceId": source})


def test_all_accounts_scope_sources_scripts_and_mutations(tmp_path):
    pg_bin = os.environ.get("V2_PG_BIN")
    if not pg_bin:
        pytest.skip("set V2_PG_BIN for native access integration")
    state = tmp_path / "access"
    with workspace.review_database(state, Path(pg_bin)) as database, running_server(database, bootstrap=False) as call:
        assert call("GET", "/api/workspace")[0] == 401
        assert call("GET", "/workspace-data.js")[0] == 401
        status, _, headers = call("GET", "/", include_headers=True)
        assert status == 200
        active = cookies(headers)
        assert "HttpOnly" in str(headers) and "SameSite=Strict" in str(headers)
        assert call("GET", "/api/session", headers={"Cookie": active})[1]["principal"]["id"] == "admin"
        for user_id, _, role, datasets in access.ACCOUNTS:
            last = active
            status, result, headers = call("POST", "/api/session", {"userId": user_id},
                                           {"Cookie": last}, include_headers=True)
            assert status == 200 and result["principal"]["id"] == user_id
            assert ("review" in result["principal"]["capabilities"]) == (role == "admin")
            active = cookies(headers)
            assert active != last
            assert call("GET", "/api/session", headers={"Cookie": last})[0] == 401
            headers = {"Cookie": active}
            status, catalog = call("GET", "/api/workspace", headers=headers)
            assert status == 200 and [entry["id"] for entry in catalog["datasets"]] == list(datasets)
            assert all(entry["knowledge"]["structured"]["datasets"] for entry in catalog["datasets"])
            corpus = "".join(call("GET", path, headers=headers)[1].decode() for path in
                             ("/workspace-data.js", "/workspace-fixtures.js"))
            for other in set(workspace.DATASETS) - set(datasets):
                assert other + ":source:" not in corpus
                assert call("GET", original(other, other + ":source:workbook"), headers=headers)[0] == 403
            if user_id in access.RESTRICTIONS:
                dataset, denied = access.RESTRICTIONS[user_id]
                phrases = {
                    "financial-services": ("opaque cross-border payment flows", "controlling or directorship"),
                    "manufacturing": ("changes from 2024-06-10 to 2024-06-17", "by 17 Jun 2024",
                                      "separate illustrative commitment of 40 units"),
                }
                for phrase in phrases[dataset]:
                    assert phrase not in corpus
                for key in denied:
                    ident = f"{dataset}:source:{key}"
                    for path in ("/api/original", "/api/original-info"):
                        assert call("GET", original(dataset, ident).replace("/api/original", path), headers=headers)[0] == 403
            dataset = datasets[0]
            mapping = "risk_rating" if dataset == "financial-services" else "delivery-vs-due-date"
            selection = f"{dataset}:mapping:{mapping}"
            query = "/api/review?" + urlencode({"dataset": dataset, "mappingId": selection})
            review_status, information = call("GET", query, headers=headers)
            review = {"dataset": dataset, "mappingId": selection, "decision": "keep_separate",
                      "note": "Evidence limits remain in effect.",
                      "evidenceRevision": information.get("evidenceRevision", "unknown")}
            assert call("POST", "/api/review", review, headers)[0] == (200 if role == "admin" else 403)
            assert review_status == (200 if role == "admin" else 403)
            assert call("POST", "/api/publish", {}, headers)[0] == 404

        token = active.split(";", 1)[0].split("=", 1)[1]
        hashed = access.token_hash(token)
        assert len(hashed) == 64 and hashed != token
        assert database.sql("SELECT token_hash FROM v2_demo_sessions;") == hashed
        database.sql("UPDATE v2_demo_sessions SET expires_at = now() - interval '1 minute';")
        assert call("GET", "/api/session", headers={"Cookie": active})[0] == 401
        expired = f"{access.COOKIE}={token}; v2_demo_visited=1"
        forged = f"{access.COOKIE}=admin"
        for prior in (expired, forged, "v2_demo_visited=1"):
            _, _, headers = call("GET", "/", headers={"Cookie": prior}, include_headers=True)
            assert cookies(headers) == ""
            assert call("POST", "/api/session", {"userId": "admin"}, {"Cookie": prior})[0] == 401
        assert call("POST", "/api/session", {"userId": "admin", "recover": True},
                    {"Cookie": expired, "Origin": "https://invalid.local"})[0] == 403
        status, _, headers = call("POST", "/api/session", {"userId": "manufacturing_reader", "recover": True},
                                  {"Cookie": expired}, include_headers=True)
        assert status == 200
        recovered = cookies(headers)
        database.sql("DELETE FROM v2_demo_grants WHERE user_id = 'manufacturing_reader';")
        assert call("GET", "/api/workspace", headers={"Cookie": recovered})[1]["datasets"] == []
    with workspace.review_database(state, Path(pg_bin)) as database:
        access.initialize_access(database)
        assert database.sql("SELECT count(*) FROM v2_demo_users;") == "5"
        assert database.sql("SELECT count(*) FROM v2_demo_restrictions;") == "5"
        assert database.sql("SELECT count(*) FROM v2_demo_grants WHERE user_id = 'manufacturing_reader';") == "0"
        assert database.sql("SELECT count(*) FROM review_receipts;") == "1"
