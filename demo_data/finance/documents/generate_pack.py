"""Deterministic generator of the synthetic finance document pack (FIN-001 ...).

Every customer, account, relationship manager and transaction a document names is a row of the finance workbook
CSV projections. Recorded values are read from those rows. Commitments are numbers the workbook does not record;
each is checked against the rows and the truth is written to manifest.json. No model call, no network, no randomness.

Run:  .venv-v2/bin/python demo_data/finance/documents/generate_pack.py
"""
import csv
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECTIONS = HERE.parent / "local/finance_workbook"
SEED = 20261121
PERIOD = ("2026-11-04", "2026-11-06")
PTXT = "4 November 2026 to 6 November 2026"
PREFIX = "financial-services:definition:finance_workbook_demo/"
LABEL = ("SYNTHETIC DEMO DOCUMENT - generated for the V2 finance demonstration. It is not a real bank document. "
         "Every party it names is a row of a synthetic workbook.")
KEYS = {"Customers": "CustomerID", "Accounts": "AccountNumber", "RelationshipManagers": "RMID",
        "Customer_RM_Relationship": "CustomerID"}


class Data:
    def __init__(self, root):
        self.root, self.cache = Path(root), {}

    def rows(self, table):
        if table not in self.cache:
            with open(self.root / f"{table}.csv", newline="") as handle:
                self.cache[table] = list(csv.DictReader(handle))
        return self.cache[table]

    def row(self, table, key):
        found = [r for r in self.rows(table) if all(r[k] == v for k, v in key.items())]
        assert found, (table, key)
        return found[0]

    def compute(self, check):
        if check["op"] == "status_all_active":
            rows = [r for r in self.rows("Accounts") if r["AccountNumber"] == check["account"]]
            return 1.0 if rows and all(r["Status"] == "Active" for r in rows) else 0.0
        seen, vals = set(), []
        for r in self.rows(check["table"]):
            if r["AccountNumber"] != check["account"] or r["TransactionID"] in seen:
                continue
            if not check["period"][0] <= r["TransactionDate"] <= check["period"][1]:
                continue
            if check.get("debit_credit") and r["DebitCredit"] != check["debit_credit"]:
                continue
            if check.get("over") is not None and float(r["Amount"]) <= check["over"]:
                continue
            seen.add(r["TransactionID"])
            vals.append(float(r["Amount"]))
        return round(sum(vals), 2) if check["op"] == "sum" else float(len(vals))


def fmt(field, value):
    return f"{float(value):,.2f}" if field == "Amount" else value


def logic(check, comparison, threshold):
    if check["op"] == "status_all_active":
        return f"Every Accounts row for AccountNumber {check['account']} has Status Active."
    what = ("Sum of Amount (native currency, no conversion)" if check["op"] == "sum"
            else "Count of distinct TransactionID")
    parts = [f"{check['table']} rows with AccountNumber {check['account']}"]
    if check.get("debit_credit"):
        parts.append(f"DebitCredit {check['debit_credit']}")
    if check.get("over") is not None:
        parts.append(f"Amount above {check['over']:,}")
    parts.append(f"TransactionDate from {check['period'][0]} to {check['period'][1]}")
    return f"{what} over " + ", ".join(parts) + f"; compare {'at least' if comparison == 'ge' else 'at most'} {threshold:,}."


class Doc:
    def __init__(self, data, doc_id, title, kind, customers, date, amends=None):
        self.data, self.id, self.title, self.kind = data, doc_id, title, kind
        self.customers, self.amends = customers, amends
        self.lines = [LABEL, "", f"# {title}", "", f"Document ID: {doc_id}", f"Date: {date}",
                      "Entity scope: " + ", ".join(f"customer:{c}" for c in customers)]
        self.statements = []

    def h(self, text):
        self.lines += ["", f"## {text}"]

    def p(self, text):
        self.lines.append(text)

    def _line(self, num, sentence):
        self.lines.append(f"{num} {sentence}" if num else sentence)

    def rec(self, num, template, table, key, fields):
        row = self.data.row(table, key)
        sentence = template
        for f in fields:
            sentence = sentence.replace(f"[{f}]", fmt(f, row[f]))
        self._line(num, sentence)
        for f in fields:
            value = f"{float(row[f]):.2f}" if f == "Amount" else row[f]
            self.statements.append({
                "sentence": sentence, "kind": "recorded_value", "definition": f"{PREFIX}field/{table}/{f}",
                "stated": {"value": value, "unit": "source currency" if f == "Amount" else None, "period": None},
                "recorded": {"table": table, "key": key, "field": f, "value": value, "surface": fmt(f, row[f])}})

    def commit(self, num, sentence, definition, stated, check, comparison, threshold, supersedes=None, note=""):
        self._line(num, sentence)
        actual = self.data.compute(check)
        met = actual >= threshold if comparison == "ge" else actual <= threshold
        st = {"sentence": sentence, "kind": "commitment", "definition": PREFIX + definition,
              "stated": stated,
              "commitment": {"check": check, "comparison": comparison, "threshold": threshold, "actual": actual,
                             "met": met, "logic": logic(check, comparison, threshold), "note": note}}
        if supersedes:
            st["supersedes"] = supersedes
        self.statements.append(st)
        return sentence

    def none(self, num, sentence, why=""):
        self._line(num, sentence)
        self.statements.append({"sentence": sentence, "kind": "no_counterpart", "why": why or "The workbook records nothing like it."})

    def trap(self, num, sentence, reason, tempts=None):
        self._line(num, sentence)
        self.statements.append({"sentence": sentence, "kind": "trap",
                                "trap": {"reason": reason, "tempts": PREFIX + tempts if tempts else None}})

    def text(self):
        return "\n".join(self.lines) + "\n"


def customer_block(d, cust, num="1"):
    key = {"CustomerID": cust}
    d.h("1. Parties")
    d.p("The workbook holds no customer names, so this synthetic document identifies the customer by Customer ID only.")
    d.rec(f"{num}.1", f"The Customer is Customer ID {cust}, recorded as a [CustomerType] customer in the [CustomerSegment] segment.",
          "Customers", key, ["CustomerType", "CustomerSegment"])
    d.rec(f"{num}.2", "Its industry code is [IndustryCode] and its country of operation is [CountryOfOperation].",
          "Customers", key, ["IndustryCode", "CountryOfOperation"])
    d.rec(f"{num}.3", "The Bank records the Customer's risk rating as [RiskRating].", "Customers", key, ["RiskRating"])
    rm = d.data.row("Customer_RM_Relationship", key)["RMID"]
    d.rec(f"{num}.4", f"Relationship manager {rm} covers the Customer as [RelationshipType].",
          "Customer_RM_Relationship", key, ["RelationshipType"])
    d.rec(f"{num}.5", f"Relationship manager {rm} holds the title [Title].", "RelationshipManagers", {"RMID": rm}, ["Title"])
    return rm


def account_line(d, num, account, template):
    d.rec(num, template, "Accounts", {"AccountNumber": account}, ["ProductType", "ProductSubType", "Currency", "Status"])


def general_terms(d, governing=False, signatory=False, retention=False, notice=False):
    d.h("General terms")
    d.p("This document is confidential between the Bank and the Customer.")
    d.p("Each party will tell the other promptly if a fact stated here becomes incorrect.")
    n = 1
    if governing:
        d.none(f"G.{n}", "This document is governed by the laws of the Republic of Singapore."); n += 1
    if notice:
        d.none(f"G.{n}", "Notices to the Bank go to the Corporate Banking notices desk at the branch address held on the customer file."); n += 1
    if retention:
        d.none(f"G.{n}", "The Bank keeps this document for seven years after the relationship ends."); n += 1
    if signatory:
        d.none(f"G.{n}", "Signed for the Bank by an authorised signatory whose name is withheld in this synthetic pack."); n += 1
    d.p("This is synthetic demonstration content. It names no real person, company, address, telephone number or email address.")


def credit_turnover(d, num, acct, ccy, amount, table="CASA_Txn", **kw):
    return d.commit(num, f"The Customer shall maintain credit turnover of at least {ccy} {amount:,} in account {acct} during the first review period, {PTXT}.",
                    "field/CASA_Txn/Amount", {"value": amount, "unit": ccy, "period": "/".join(PERIOD)},
                    {"op": "sum", "table": "CASA_Txn", "account": acct, "debit_credit": "CR", "period": list(PERIOD)}, "ge", amount, **kw)


def build_docs(data):
    docs = []

    # FIN-001 facility letter, CUST00004
    d = Doc(data, "FIN-001", "Banking facility letter", "facility letter", ["CUST00004"], "2026-10-26")
    d.p("From the Bank to the Customer. This letter sets out the terms of a revolving working-capital facility.")
    customer_block(d, "CUST00004")
    d.h("2. Operating account")
    account_line(d, "2.1", "MY-CA-00004-2", "The operating account is MY-CA-00004-2, a [ProductType] account of type [ProductSubType] held in [Currency] with status [Status].")
    d.h("3. Facility")
    d.none("3.1", "The facility limit is USD 2,500,000 and the tenor is 36 months.")
    d.none("3.2", "The Bank will review the facility once every 12 months.")
    d.trap("3.3", "Facility reference FAC-2026-0417 identifies this letter.", "A document reference number from the bank's letter series, not a workbook customer ID or account number.", "field/Accounts/AccountNumber")
    d.h("4. Covenants")
    c1 = credit_turnover(d, "4.1", "MY-CA-00004-2", "USD", 40000)
    d.commit("4.2", f"The Customer shall not make more than 2 transactions above 50,000 in account MY-CA-00004-2 during the first review period, {PTXT}.",
             "metric/large_casa_transaction_count", {"value": 2, "unit": "transactions", "period": "/".join(PERIOD)},
             {"op": "count", "table": "CASA_Txn", "account": "MY-CA-00004-2", "over": 50000, "period": list(PERIOD)}, "le", 2,
             note="The model defines a large transaction as an Amount above 50000 in source currency.")
    d.trap("4.3", "The Customer expects typical monthly credit turnover of USD 180,000 across all its accounts.",
           "A monthly figure across accounts, where the workbook holds one amount per transaction.", "field/CASA_Txn/Amount")
    general_terms(d, governing=True, signatory=True)
    docs.append(d)

    # FIN-002 amendment
    d2 = Doc(data, "FIN-002", "Amendment to banking facility letter FIN-001", "facility letter amendment", ["CUST00004"], "2026-11-02", amends="FIN-001")
    d2.p("From the Bank to the Customer. This letter amends facility letter FIN-001 dated 26 October 2026.")
    customer_block(d2, "CUST00004")
    d2.h("2. Operating account")
    account_line(d2, "2.1", "MY-CA-00004-2", "The operating account remains MY-CA-00004-2, a [ProductType] account of type [ProductSubType] held in [Currency] with status [Status].")
    d2.h("3. Amended term")
    d2.commit("3.1", f"Clause 4.1 of FIN-001 is replaced: the Customer shall maintain credit turnover of at least USD 45,000 in account MY-CA-00004-2 during the first review period, {PTXT}, in place of USD 40,000.",
              "field/CASA_Txn/Amount", {"value": 45000, "unit": "USD", "period": "/".join(PERIOD)},
              {"op": "sum", "table": "CASA_Txn", "account": "MY-CA-00004-2", "debit_credit": "CR", "period": list(PERIOD)}, "ge", 45000,
              supersedes=c1, note="Replaces the 40,000 threshold of FIN-001 clause 4.1. The amended threshold applies.")
    d2.p("3.2 All other terms of FIN-001 remain unchanged.")
    d2.trap("3.3", "Amendment reference AMD-2026-0044 identifies this letter.", "A document reference number, not a workbook key.", "field/Accounts/AccountNumber")
    d2.none("3.4", "The Customer must return a signed copy within 10 business days.")
    general_terms(d2, signatory=True, retention=True)
    docs += [d2]

    # FIN-003 and FIN-008 KYC memos
    def kyc(doc_id, cust, date, acct_a, acct_b, ccy_b, n_max, credit_min):
        k = Doc(data, doc_id, "Customer due-diligence review memo", "KYC review memo", [cust], date)
        k.p("From the compliance analyst to the relationship manager. This memo records the periodic customer due-diligence review.")
        customer_block(k, cust)
        k.h("2. Accounts reviewed")
        account_line(k, "2.1", acct_a, f"Account {acct_a} is a [ProductType] account of type [ProductSubType] held in [Currency] with status [Status].")
        account_line(k, "2.2", acct_b, f"Account {acct_b} is a [ProductType] account of type [ProductSubType] held in [Currency] with status [Status].")
        k.h("3. Expected activity")
        k.commit("3.1", f"Expected activity in account {acct_a} is no more than {n_max} transactions during the review period, {PTXT}.",
                 "metric/casa_transaction_count", {"value": n_max, "unit": "transactions", "period": "/".join(PERIOD)},
                 {"op": "count", "table": "CASA_Txn", "account": acct_a, "period": list(PERIOD)}, "le", n_max)
        k.commit("3.2", f"Expected credit turnover in account {acct_b} is at least {ccy_b} {credit_min:,} during the review period, {PTXT}.",
                 "field/CASA_Txn/Amount", {"value": credit_min, "unit": ccy_b, "period": "/".join(PERIOD)},
                 {"op": "sum", "table": "CASA_Txn", "account": acct_b, "debit_credit": "CR", "period": list(PERIOD)}, "ge", credit_min)
        k.commit("3.3", f"The analyst expects account {acct_a} to stay Active through the review period.",
                 "field/Accounts/Status", {"value": "Active", "unit": "status", "period": "/".join(PERIOD)},
                 {"op": "status_all_active", "account": acct_a}, "ge", 1)
        k.h("4. Other findings")
        k.none("4.1", "The next periodic review is due within 365 days of this memo.")
        k.trap("4.2", f"The Customer stated an expected annual turnover of {ccy_b} 18,000,000.",
               "An annual figure across the relationship, where the workbook holds one amount per transaction.", "field/CASA_Txn/Amount")
        k.trap("4.3", "A company registration reference ROC-5520-18 was sighted.", "A registration-style number from another namespace. It is not a workbook customer ID.", "field/Customers/CustomerID")
        general_terms(k, retention=True, signatory=True)
        return k
    docs.append(kyc("FIN-003", "CUST00028", "2026-11-09", "MY-CA-00028-0", "MY-CA-00028-2", "SGD", 5, 80000))
    docs.append(kyc("FIN-008", "CUST00040", "2026-11-09", "MY-CA-00040-3", "MY-CA-00040-3", "HKD", 4, 100000))

    # FIN-004 FX hedging term sheet, CUST00028
    d = Doc(data, "FIN-004", "Foreign exchange hedging term sheet", "FX hedging term sheet", ["CUST00028"], "2026-11-09")
    d.p("From the Treasury desk to the Customer. This term sheet sets out the hedging programme and confirms one booked deal.")
    customer_block(d, "CUST00028")
    d.h("2. Treasury account")
    account_line(d, "2.1", "MY-Tr-00028-3", "The hedging account is MY-Tr-00028-3, a [ProductType] account of type [ProductSubType] held in [Currency] with status [Status].")
    d.rec("2.2", "Deal Tre0000052, booked on [TransactionDate] for [Currency] [Amount] on account MY-Tr-00028-3, is confirmed under this term sheet.",
          "Treasury_FX_Txn", {"TransactionID": "Tre0000052"}, ["TransactionDate", "Currency", "Amount"])
    d.h("3. Hedging terms")
    d.none("3.1", "The hedge tenor is 6 months and the indicative forward rate is 1.3450.")
    d.trap("3.2", "The programme notional is USD 500,000 per quarter.", "A quarterly notional, where the workbook holds one amount per transaction.", "field/Treasury_FX_Txn/Amount")
    d.trap("3.3", "The single-deal cap is EUR 40,000.", "A limit in euros, where the account and its deals are in USD.", "field/Treasury_FX_Txn/Amount")
    d.commit("3.4", f"The Customer shall book at least 5 foreign exchange transactions on account MY-Tr-00028-3 during {PTXT}.",
             "field/Treasury_FX_Txn/TransactionID", {"value": 5, "unit": "transactions", "period": "/".join(PERIOD)},
             {"op": "count", "table": "Treasury_FX_Txn", "account": "MY-Tr-00028-3", "period": list(PERIOD)}, "ge", 5)
    d.commit("3.5", f"The Customer shall not book more than 2 foreign exchange transactions above USD 40,000 on account MY-Tr-00028-3 during {PTXT}.",
             "field/Treasury_FX_Txn/Amount", {"value": 2, "unit": "transactions above USD 40,000", "period": "/".join(PERIOD)},
             {"op": "count", "table": "Treasury_FX_Txn", "account": "MY-Tr-00028-3", "over": 40000, "period": list(PERIOD)}, "le", 2)
    general_terms(d, governing=True, signatory=True)
    docs.append(d)

    # FIN-005 RM call note, CUST00032
    d = Doc(data, "FIN-005", "Relationship manager call note", "RM call note", ["CUST00032"], "2026-11-09")
    d.p("Internal note by the relationship manager after a call with the Customer's finance contact.")
    rm = customer_block(d, "CUST00032")
    d.h("2. Account")
    account_line(d, "2.1", "HK-CA-00032-1", "The Customer's main account is HK-CA-00032-1, a [ProductType] account of type [ProductSubType] held in [Currency] with status [Status].")
    d.h("3. What the Customer said")
    d.commit("3.1", f"The Customer promised to route at least HKD 60,000 of credits through account HK-CA-00032-1 during {PTXT}.",
             "field/CASA_Txn/Amount", {"value": 60000, "unit": "HKD", "period": "/".join(PERIOD)},
             {"op": "sum", "table": "CASA_Txn", "account": "HK-CA-00032-1", "debit_credit": "CR", "period": list(PERIOD)}, "ge", 60000)
    d.commit("3.2", f"The Customer promised at least 8 credit transactions on account HK-CA-00032-1 during {PTXT}.",
             "metric/casa_transaction_count", {"value": 8, "unit": "credit transactions", "period": "/".join(PERIOD)},
             {"op": "count", "table": "CASA_Txn", "account": "HK-CA-00032-1", "debit_credit": "CR", "period": list(PERIOD)}, "ge", 8,
             note="The model metric counts all transactions. The commitment counts credit transactions only.")
    d.commit("3.3", f"The Customer will keep account HK-CA-00032-0 Active through {PTXT}.",
             "field/Accounts/Status", {"value": "Active", "unit": "status", "period": "/".join(PERIOD)},
             {"op": "status_all_active", "account": "HK-CA-00032-0"}, "ge", 1)
    d.trap("3.4", "The finance contact quoted last month's turnover as about SGD 2,000,000.", "A figure in another currency, another period and another grain than any workbook field.", "field/CASA_Txn/Amount")
    d.trap("3.5", "A third party told me the Customer is under investigation for sanctions evasion, which is unverified, and I have not changed the recorded risk rating.",
           "An allegation-style statement. It is not a recorded risk rating and must not map to the risk rating field.", "field/Customers/RiskRating")
    d.rec("3.6", "The recorded risk rating stays [RiskRating].", "Customers", {"CustomerID": "CUST00032"}, ["RiskRating"])
    d.h("4. Follow-up")
    d.none("4.1", f"I will call the Customer back next quarter.")
    general_terms(d, retention=True)
    docs.append(d)

    # FIN-006 account-opening summary, CUST00048
    d = Doc(data, "FIN-006", "Account-opening summary", "account-opening summary", ["CUST00048"], "2026-10-30")
    d.p("Onboarding summary refreshed on 30 October 2026. It restates the account set held for the Customer.")
    customer_block(d, "CUST00048")
    d.h("2. Account opened")
    account_line(d, "2.1", "SG-CA-00048-2", "Account SG-CA-00048-2 is a [ProductType] account of type [ProductSubType] held in [Currency] with status [Status].")
    d.rec("2.2", "It was opened on [OpenDate] and is booked in [BookingCountry] at branch [BranchCode].", "Accounts", {"AccountNumber": "SG-CA-00048-2"}, ["OpenDate", "BookingCountry", "BranchCode"])
    d.h("3. Opening conditions")
    d.commit("3.1", f"The Customer shall credit at least SGD 30,000 to account SG-CA-00048-2 during the first review period, {PTXT}.",
             "field/CASA_Txn/Amount", {"value": 30000, "unit": "SGD", "period": "/".join(PERIOD)},
             {"op": "sum", "table": "CASA_Txn", "account": "SG-CA-00048-2", "debit_credit": "CR", "period": list(PERIOD)}, "ge", 30000)
    d.commit("3.2", f"The Customer shall make at least 4 credit transactions on account SG-CA-00048-2 during the first review period, {PTXT}.",
             "metric/casa_transaction_count", {"value": 4, "unit": "credit transactions", "period": "/".join(PERIOD)},
             {"op": "count", "table": "CASA_Txn", "account": "SG-CA-00048-2", "debit_credit": "CR", "period": list(PERIOD)}, "ge", 4,
             note="The model metric counts all transactions. The commitment counts credit transactions only.")
    d.trap("3.3", "The Customer's initial deposit of USD 10,000 was received on the account.", "A figure in USD, where the account and its transactions are in SGD.", "field/CASA_Txn/Amount")
    d.none("3.4", "A welcome pack is posted within 7 days of account opening.")
    d.none("3.5", "The Bank marks the account dormant after 12 months without activity.")
    general_terms(d, governing=True, notice=True)
    docs.append(d)

    # FIN-007 card programme agreement, CUST00032
    d = Doc(data, "FIN-007", "Corporate credit card programme agreement", "credit card programme agreement", ["CUST00032"], "2026-10-27")
    d.p("Agreement between the Bank and the Customer for a corporate credit card programme.")
    customer_block(d, "CUST00032")
    d.h("2. Card account")
    account_line(d, "2.1", "1234567833450073", "The card account is 1234567833450073, a [ProductType] account of type [ProductSubType] held in [Currency] with status [Status].")
    d.h("3. Limits and charges")
    d.commit("3.1", f"The Customer shall keep card spending on account 1234567833450073 within SGD 20,000 during the first review period, {PTXT}.",
             "metric/credit_card_transaction_value", {"value": 20000, "unit": "SGD", "period": "/".join(PERIOD)},
             {"op": "sum", "table": "CreditCard_Txn", "account": "1234567833450073", "debit_credit": "DR", "period": list(PERIOD)}, "le", 20000,
             note="Spending is read as debit postings. The model metric sums all card amounts without a direction.")
    d.commit("3.2", f"The Customer shall make at least 2 card transactions on account 1234567833450073 during the first review period, {PTXT}.",
             "metric/credit_card_transaction_value", {"value": 2, "unit": "transactions", "period": "/".join(PERIOD)},
             {"op": "count", "table": "CreditCard_Txn", "account": "1234567833450073", "period": list(PERIOD)}, "ge", 2,
             note="The model has no card transaction count metric. The count uses the CreditCard_Txn rows.")
    d.trap("3.3", "The cash advance limit is USD 5,000 per transaction.", "A limit in USD per transaction, where the card account is held in SGD.", "field/CreditCard_Txn/Amount")
    d.trap("3.4", "Card programme reference CPA-2026-118 identifies this agreement.", "A document reference number, not a workbook account number.", "field/Accounts/AccountNumber")
    d.none("3.5", "The interest rate on unpaid card balances is 1.8 percent per month.")
    d.none("3.6", "Each statement is payable within 25 days of the statement date.")
    general_terms(d, governing=True, signatory=True)
    docs.append(d)

    # FIN-009 transaction-monitoring alert memo, CUST00024
    d = Doc(data, "FIN-009", "Transaction-monitoring alert review memo", "alert review memo", ["CUST00024"], "2026-11-10")
    d.p("From the monitoring analyst to the relationship manager. This memo reviews one alert on the Customer's letter of credit account.")
    customer_block(d, "CUST00024")
    d.h("2. Account and transaction reviewed")
    account_line(d, "2.1", "SG-Tr-00024-1", "The alerted account is SG-Tr-00024-1, a [ProductType] account of type [ProductSubType] held in [Currency] with status [Status].")
    d.rec("2.2", "The alert cites transaction Tre0000040, dated [TransactionDate], for [Currency] [Amount] through the [Channel] channel.",
          "Treasury_LC_Txn", {"TransactionID": "Tre0000040"}, ["TransactionDate", "Currency", "Amount", "Channel"])
    d.h("3. Review")
    d.trap("3.1", "Alert reference TM-2026-0113 identifies this review.", "An alert reference from the monitoring system, not a workbook transaction ID.", "field/Treasury_LC_Txn/TransactionID")
    d.trap("3.2", "The pattern could indicate trade-based layering, but this is a hypothesis and not a finding.", "An unverified hypothesis. It is not a suspicious transaction report and not a recorded risk rating.", "concept/suspicious_transaction")
    d.commit("3.3", f"The Customer undertakes not to make more than 2 transactions above SGD 40,000 on account SG-Tr-00024-1 during {PTXT}.",
             "field/Treasury_LC_Txn/Amount", {"value": 2, "unit": "transactions above SGD 40,000", "period": "/".join(PERIOD)},
             {"op": "count", "table": "Treasury_LC_Txn", "account": "SG-Tr-00024-1", "over": 40000, "period": list(PERIOD)}, "le", 2)
    d.none("3.4", "The analyst will close the alert within 5 business days.")
    general_terms(d, retention=True, signatory=True)
    docs.append(d)

    # FIN-010 covenant compliance certificate, CUST00004
    d = Doc(data, "FIN-010", "Covenant compliance certificate", "covenant compliance certificate", ["CUST00004"], "2026-11-09")
    d.p("Certificate from the Customer to the Bank under facility letter FIN-001 as amended by FIN-002, for the first review period.")
    customer_block(d, "CUST00004")
    d.h("2. Accounts")
    account_line(d, "2.1", "MY-CA-00004-0", "Account MY-CA-00004-0 is a [ProductType] account of type [ProductSubType] held in [Currency] with status [Status].")
    d.h("3. Certification")
    d.commit("3.1", f"The Customer certifies that credit turnover in account MY-CA-00004-2 for {PTXT} was at least USD 45,000.",
             "field/CASA_Txn/Amount", {"value": 45000, "unit": "USD", "period": "/".join(PERIOD)},
             {"op": "sum", "table": "CASA_Txn", "account": "MY-CA-00004-2", "debit_credit": "CR", "period": list(PERIOD)}, "ge", 45000,
             note="A certified claim by the customer. The rows show the actual credit turnover.")
    d.commit("3.2", f"The Customer certifies that account MY-CA-00004-0 stayed Active throughout {PTXT}.",
             "field/Accounts/Status", {"value": "Active", "unit": "status", "period": "/".join(PERIOD)},
             {"op": "status_all_active", "account": "MY-CA-00004-0"}, "ge", 1)
    d.commit("3.3", f"The Customer certifies that it made no more than 2 transactions above 50,000 in account MY-CA-00004-2 during {PTXT}.",
             "metric/large_casa_transaction_count", {"value": 2, "unit": "transactions", "period": "/".join(PERIOD)},
             {"op": "count", "table": "CASA_Txn", "account": "MY-CA-00004-2", "over": 50000, "period": list(PERIOD)}, "le", 2)
    d.none("3.4", "The Customer must also keep audited net worth of at least USD 3,000,000.")
    d.trap("3.5", "Certificate number CC-2026-0007 identifies this certificate.", "A document reference number, not a workbook key.", "field/Accounts/AccountNumber")
    general_terms(d, signatory=True, notice=True)
    docs.append(d)

    # FIN-011 fee schedule letter, CUST00048
    d = Doc(data, "FIN-011", "Fee schedule letter", "fee schedule letter", ["CUST00048"], "2026-10-30")
    d.p("From the Bank to the Customer. This letter sets out the fees on the Customer's current account.")
    customer_block(d, "CUST00048")
    d.h("2. Account")
    account_line(d, "2.1", "SG-CA-00048-2", "The fees apply to account SG-CA-00048-2, a [ProductType] account of type [ProductSubType] held in [Currency] with status [Status].")
    d.rec("2.2", "The account is held at branch [BranchCode].", "Accounts", {"AccountNumber": "SG-CA-00048-2"}, ["BranchCode"])
    d.h("3. Fees")
    d.none("3.1", "The monthly account fee is SGD 20 and each outgoing SWIFT payment costs SGD 35.")
    d.commit("3.2", f"The monthly account fee is waived if account SG-CA-00048-2 records at least 5 transactions during {PTXT}.",
             "metric/casa_transaction_count", {"value": 5, "unit": "transactions", "period": "/".join(PERIOD)},
             {"op": "count", "table": "CASA_Txn", "account": "SG-CA-00048-2", "period": list(PERIOD)}, "ge", 5)
    d.trap("3.3", "A fee of 0.15 percent applies to each SWIFT transaction.", "A percentage rate, where the workbook holds an amount per transaction.", "field/CASA_Txn/Amount")
    d.trap("3.4", "Fee invoice INV-2026-3321 lists these fees.", "An invoice number, not a workbook key.", "field/CASA_Txn/TransactionID")
    general_terms(d, governing=True, notice=True)
    docs.append(d)

    # FIN-012 loan instalment notice, CUST00040
    d = Doc(data, "FIN-012", "Loan repayment schedule notice", "loan repayment notice", ["CUST00040"], "2026-11-09")
    d.p("From the Bank to the Customer. This notice restates the repayment expectations on the Customer's loan account.")
    customer_block(d, "CUST00040")
    d.h("2. Loan account")
    account_line(d, "2.1", "MY-Lo-00040-2", "The loan account is MY-Lo-00040-2, a [ProductType] account of type [ProductSubType] held in [Currency] with status [Status].")
    d.rec("2.2", "Posting Loa0000247, dated [TransactionDate], shows [Currency] [Amount].", "Loan_Txn", {"TransactionID": "Loa0000247"}, ["TransactionDate", "Currency", "Amount"])
    d.h("3. Repayment terms")
    d.commit("3.1", f"The Customer shall post at least EUR 60,000 of debits to loan account MY-Lo-00040-2 during {PTXT}.",
             "field/Loan_Txn/Amount", {"value": 60000, "unit": "EUR", "period": "/".join(PERIOD)},
             {"op": "sum", "table": "Loan_Txn", "account": "MY-Lo-00040-2", "debit_credit": "DR", "period": list(PERIOD)}, "ge", 60000,
             note="The source does not record whether a posting is a repayment. Debit postings are the closest proxy.")
    d.commit("3.2", f"The Customer shall have at least 6 postings on loan account MY-Lo-00040-2 during {PTXT}.",
             "field/Loan_Txn/TransactionID", {"value": 6, "unit": "postings", "period": "/".join(PERIOD)},
             {"op": "count", "table": "Loan_Txn", "account": "MY-Lo-00040-2", "period": list(PERIOD)}, "ge", 6)
    d.trap("3.3", "The scheduled monthly instalment is EUR 12,000.", "A monthly scheduled figure, where the workbook holds one posting amount per transaction.", "field/Loan_Txn/Amount")
    d.trap("3.4", "Loan reference LN-2021-5589 identifies this loan.", "A loan reference from the bank's letter series, not a workbook account number.", "field/Accounts/AccountNumber")
    d.none("3.5", "Final maturity is 15 March 2036 and prepayment needs 30 days of notice.")
    general_terms(d, governing=True, signatory=True)
    docs.append(d)

    docs.sort(key=lambda x: x.id)
    return docs


def build(projections, out):
    out = Path(out)
    (out / "markdown").mkdir(parents=True, exist_ok=True)
    docs = build_docs(Data(projections))
    entries = []
    for d in docs:
        text = d.text()
        path = f"markdown/{d.id}.md"
        (out / path).write_bytes(text.encode("utf-8"))
        e = {"id": d.id, "title": d.title, "type": d.kind, "path": path, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
             "customer_keys": d.customers}
        if d.amends:
            e["amends"] = d.amends
        e["statements"] = d.statements
        entries.append(e)
    manifest = {
        "generator": "demo_data/finance/documents/generate_pack.py", "seed": SEED, "date": "2026-11-21", "synthetic": True,
        "note": ("Planted truth from the generator, not an independent gold set. Every document is synthetic. Recorded values are read "
                 "from the finance workbook CSV projections. Commitment truth is computed from the same rows. The workbook holds "
                 "transactions dated 2026-11-04 to 2026-11-06 only, so every period is that window. No randomness is used."),
        "model_definition_format": PREFIX + "{field|metric}/...",
        "documents": entries}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    build(PROJECTIONS, HERE)
