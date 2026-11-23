"""Restricted identifiers (account numbers, customer IDs, relationship manager IDs) are masked in every reply.

The data model says of these columns: never display the value. The customer view keeps customer and manager IDs,
because it is searched by customer ID."""
import json

from semantic_pipeline_v2 import identifier_mask as im
from semantic_pipeline_v2.workspace_server import WorkspaceRequest

VALUES = {"account": {"SG-Sv-12345-6", "4111111111111111", "123456"}, "customer": {"CUST00004"}, "manager": {"RM001"}}


def test_values_in_text_rows_and_numbers_are_masked():
    out = im.mask({"conclusion": "Account SG-Sv-12345-6 of CUST00004 (manager RM001).",
                   "rows": [{"AccountNumber": "4111111111111111", "CustomerID": "CUST00004", "Amount": 879.9},
                            {"AccountNumber": 123456}]}, VALUES)
    text = json.dumps(out, ensure_ascii=False)
    for value in ("SG-Sv-12345-6", "4111111111111111", "CUST00004", "RM001", "123456"):
        assert value not in text
    assert out["conclusion"] == "Account •••• 45-6 of •••• 0004 (manager •••• 01)."
    assert out["rows"][0] == {"AccountNumber": "•••• 1111", "CustomerID": "•••• 0004", "Amount": 879.9}
    assert out["rows"][1] == {"AccountNumber": "•••• 56"}


def test_other_text_and_identities_are_left_alone():
    same = {"text": "SGD 123456.78 and 1,123456 and X123456 and 123456-A; hash ab123456cd", "id": "entity:CUST00004",
            "sourceId": "CUST00004", "amount": 123456.5, "flag": True}
    assert im.mask(same, VALUES) == same


def test_customer_view_keeps_customer_and_manager_ids():
    out = im.mask({"t": "CUST00004 RM001 4111111111111111"}, VALUES, keep=("customer", "manager"))
    assert out == {"t": "CUST00004 RM001 •••• 1111"}


def test_masking_twice_changes_nothing():
    once = im.mask({"t": "CUST00004 SG-Sv-12345-6"}, VALUES)
    assert im.mask(once, VALUES) == once


def test_columns_are_found_in_the_model():
    ws = {"knowledge": {"structured": {"fields": [{"name": "Accounts/AccountNumber"}, {"name": "Accounts/CustomerID"},
                                                  {"name": "RelationshipManagers/RMID"}, {"name": "Accounts/Currency"},
                                                  {"name": "STR_AccountEntity/AccNum"}]}}}
    assert im.columns(ws) == [("account", "Accounts", "AccountNumber"), ("customer", "Accounts", "CustomerID"),
                              ("manager", "RelationshipManagers", "RMID"), ("account", "STR_AccountEntity", "AccNum")]


class _Reply:
    """A request that records what send_json writes."""
    send_json, masked, reads_identifiers = WorkspaceRequest.send_json, WorkspaceRequest.masked, WorkspaceRequest.reads_identifiers

    def __init__(self, path):
        self.path, self.sent = path, None
        self.server = type("S", (), {"identifier_values": VALUES})()

    def reply(self, status, body, content_type, headers=()):
        self.sent = json.loads(body)


def test_every_json_reply_is_masked_and_the_customer_view_keeps_customer_ids():
    ask = _Reply("/api/ask")
    ask.send_json(200, {"result": "CUST00004 holds 4111111111111111"})
    assert ask.sent == {"result": "•••• 0004 holds •••• 1111"}
    view = _Reply("/api/entity/report")
    view.send_json(200, {"result": "CUST00004 holds 4111111111111111"})
    assert view.sent == {"result": "CUST00004 holds •••• 1111"}


class _Links:
    """An evidence link store whose database answers the one query with these (definition, stated value) pairs."""
    def __init__(self, pairs):
        self.database = self
        self.pairs = pairs

    def sql(self, text, **params):
        assert params == {"dataset": "d1"}
        return json.dumps(self.pairs)


def test_identifiers_a_document_states_are_masked_too():
    ws = {"id": "d1", "knowledge": {"structured": {"fields": [{"name": "CreditCard_Txn/CustomerID"},
                                                              {"name": "CreditCard_Txn/Amount"}]}}}
    store = _Links([["d1:definition:m/field/CreditCard_Txn/CustomerID", "CUS48271"],
                    ["d1:definition:m/field/CreditCard_Txn/CustomerID", "45000"],       # a bare short number is not trusted
                    ["d1:definition:m/field/CreditCard_Txn/Amount", "AB879900"]])
    assert im.stated(store, ws) == {"account": set(), "customer": {"CUS48271"}, "manager": set()}
    request = _Reply("/api/ask")
    request.server.workspaces, request.server.evidence_links = {"d1": ws}, store
    request.send_json(200, {"span": "Yes, it's CUS48271. The limit is 45000 and AB879900."})
    assert request.sent == {"span": "Yes, it's •••• 8271. The limit is 45000 and AB879900."}


def test_an_identifier_glued_to_chinese_text_is_masked():
    out = im.mask({"t": "客户CUST00004的账户4111111111111111已冻结，经理RM001。金额123456.78元"}, VALUES)
    assert out == {"t": "客户•••• 0004的账户•••• 1111已冻结，经理•••• 01。金额123456.78元"}


def test_an_admin_reads_identifiers_unmasked_and_every_other_account_reads_them_masked(monkeypatch):
    """User decision of 23 November 2026: the admin checks previews before a write and needs the real values.
    Any other account, a request with no account, and a failed account lookup all stay masked."""
    from semantic_pipeline_v2 import workspace_server
    body = {"result": "CUST00004 holds 4111111111111111"}

    def reply(resolve, store=object()):
        monkeypatch.setattr(workspace_server, "resolve_session", resolve)
        request = _Reply("/api/ask")
        request.server.review_store, request.session_token = store, lambda: "token"
        request.send_json(200, body)
        return request.sent

    assert reply(lambda store, token: {"id": "admin", "role": "admin"}) == body
    masked = {"result": "•••• 0004 holds •••• 1111"}
    assert reply(lambda store, token: {"id": "reader", "role": "reader"}) == masked
    assert reply(lambda store, token: None) == masked
    assert reply(lambda store, token: {"id": "admin", "role": "admin"}, store=None) == masked      # no account store

    def broken(store, token):
        raise RuntimeError("the account store is down")
    assert reply(broken) == masked
