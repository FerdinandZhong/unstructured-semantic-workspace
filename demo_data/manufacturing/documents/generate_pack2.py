"""Deterministic generator of the synthetic manufacturing document pack 2 (MFG-101 ...).

Every customer, vendor, product, order, work order and BOM line a document names is a row of the AdventureWorks
CSV projections. Recorded values are read from those rows. Commitments are numbers the data does not record; each is
checked against the rows and the truth is written to manifest.json. No model call, no network, no randomness.

Run:  .venv-v2/bin/python demo_data/manufacturing/documents/generate_pack2.py
"""
import csv
import hashlib
import json
import re
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
CSV_DIR = HERE.parent / "data/csv"
OUT = HERE / "pack2"
SEED = 20261121
PREFIX = "manufacturing:definition:adventureworks_manufacturing_demo/"
LABEL = ("SYNTHETIC DEMO DOCUMENT - generated for the V2 manufacturing demonstration. It is not a real contract or report. "
         "Every party, product and order it names is a row of the fictional AdventureWorks sample data.")
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
MONEY = {"StandardCost", "ListPrice", "UnitPrice", "LineTotal", "SubTotal", "TaxAmt", "Freight", "TotalDue", "StandardPrice",
         "LastReceiptCost", "PlannedCost", "ActualCost", "ShipBase", "ShipRate", "CostRate"}
Y24 = ("2024-01-01", "2024-12-31")


def norm(v):
    v = v.strip()
    return v[:10] if re.fullmatch(r"\d{4}-\d\d-\d\d 00:00:00\.000", v) else v


def fmt(field, raw, show=None):
    raw = raw.strip()
    if show and field in show:
        return show[field][raw]
    if re.fullmatch(r"\d{4}-\d\d-\d\d 00:00:00\.000", raw):
        d = date.fromisoformat(raw[:10])
        return f"{d.day} {MONTHS[d.month - 1]} {d.year}"
    if re.fullmatch(r"-?\d+(\.\d+)?", raw):
        v = float(raw)
        if field in MONEY:
            return f"{v:,.2f}"
        if v == int(v):
            return f"{int(v):,}" if abs(v) >= 1000 else str(int(v))
        return f"{v:g}"
    return raw


class Data:
    def __init__(self, root):
        self.root, self.cache, self.index = Path(root), {}, {}

    def rows(self, table):
        if table not in self.cache:
            with open(self.root / f"{table}.csv", newline="", encoding="utf-8") as handle:
                self.cache[table] = list(csv.DictReader(handle))
        return self.cache[table]

    def row(self, table, key):
        cols = tuple(sorted(key))
        idx = self.index.setdefault((table, cols), {})
        if not idx:
            for r in self.rows(table):
                idx.setdefault(tuple(r[c].strip() for c in cols), []).append(r)
        found = idx.get(tuple(str(key[c]) for c in cols), [])
        assert len(found) == 1, (table, key, len(found))
        return found[0]

    def where(self, table, **eq):
        return [r for r in self.rows(table) if all(r[k].strip() == str(v) for k, v in eq.items())]

    def compute(self, c):
        op = c["op"]
        inr = lambda v: c["from"] <= v[:10] <= c["to"]
        if op in ("sales_units", "sales_revenue", "sales_orders", "avg_unit_price", "late_dispatch", "ship_gap_over"):
            heads = {r["SalesOrderID"]: r for r in self.where("Sales_SalesOrderHeader", CustomerID=c["customer"]) if inr(r["OrderDate"])}
            if op == "sales_orders":
                return float(len(heads))
            if op == "late_dispatch":
                return float(sum(1 for r in heads.values() if r["ShipDate"][:10] > r["DueDate"][:10]))
            if op == "ship_gap_over":
                return float(sum(1 for r in heads.values() if (date.fromisoformat(r["ShipDate"][:10]) - date.fromisoformat(r["OrderDate"][:10])).days > c["days"]))
            lines = [d for d in self.rows("Sales_SalesOrderDetail")
                     if d["SalesOrderID"] in heads and (c.get("product") is None or d["ProductID"] == str(c["product"]))]
            if op == "sales_units":
                return float(sum(int(d["OrderQty"]) for d in lines))
            if op == "sales_revenue":
                return round(sum(float(d["LineTotal"]) for d in lines), 2)
            return round(sum(float(d["UnitPrice"]) for d in lines) / len(lines), 4)
        if op.startswith("wo_"):
            wos = [w for w in self.where("Production_WorkOrder", ProductID=c["product"]) if inr(w["StartDate"])]
            req, scr = sum(int(w["OrderQty"]) for w in wos), sum(int(w["ScrappedQty"]) for w in wos)
            return {"wo_requested": float(req), "wo_scrapped": float(scr), "wo_scrap_rate": round(scr / req, 6),
                    "wo_open": float(sum(1 for w in wos if not w["EndDate"].strip()))}[op]
        if op == "inventory":
            rows = self.where("Production_ProductInventory", ProductID=c["product"])
            return float(sum(int(float(i["Quantity"])) for i in rows if c.get("location") is None or i["LocationID"] == str(c["location"])))
        if op.startswith("po_"):
            heads = {h["PurchaseOrderID"] for h in self.where("Purchasing_PurchaseOrderHeader", VendorID=c["vendor"]) if inr(h["OrderDate"])}
            if op == "po_count":
                return float(len(heads))
            lines = [d for d in self.rows("Purchasing_PurchaseOrderDetail") if d["PurchaseOrderID"] in heads]
            if op == "po_reject_rate":
                return round(sum(float(d["RejectedQty"]) for d in lines) / sum(float(d["ReceivedQty"]) for d in lines), 6)
            return float(sum(float(d["OrderQty"]) for d in lines))
        if op == "field_value":
            return float(self.row(c["table"], c["key"])[c["field"]])
        if op == "days_since":
            start = self.row(c["table"], c["key"])[c["field"]][:10]
            return float((date.fromisoformat(c["until"]) - date.fromisoformat(start)).days)
        raise AssertionError(op)


def logic(c, cmp_, thr):
    op = c["op"]
    per = f"OrderDate from {c.get('from')} to {c.get('to')}"
    sel = {
        "sales_units": f"Sum of OrderQty on Sales_SalesOrderDetail lines of orders of CustomerID {c.get('customer')}" + (f", ProductID {c.get('product')}" if c.get("product") else "") + f", {per}",
        "sales_revenue": f"Sum of LineTotal (currency not recorded) on lines of orders of CustomerID {c.get('customer')}, {per}",
        "sales_orders": f"Count of distinct SalesOrderID of CustomerID {c.get('customer')}, {per}",
        "avg_unit_price": f"Mean UnitPrice of lines of ProductID {c.get('product')} on orders of CustomerID {c.get('customer')}, {per}",
        "late_dispatch": f"Count of orders of CustomerID {c.get('customer')} with ShipDate after DueDate, {per}",
        "ship_gap_over": f"Count of orders of CustomerID {c.get('customer')} shipped more than {c.get('days')} days after OrderDate, {per}",
        "wo_requested": f"Sum of OrderQty on Production_WorkOrder rows of ProductID {c.get('product')}, StartDate from {c.get('from')} to {c.get('to')}",
        "wo_scrapped": f"Sum of ScrappedQty on Production_WorkOrder rows of ProductID {c.get('product')}, StartDate from {c.get('from')} to {c.get('to')}",
        "wo_scrap_rate": f"Sum of ScrappedQty divided by sum of OrderQty on work orders of ProductID {c.get('product')}, StartDate from {c.get('from')} to {c.get('to')}",
        "wo_open": f"Count of work orders of ProductID {c.get('product')} with no EndDate, StartDate from {c.get('from')} to {c.get('to')}",
        "inventory": f"Sum of Quantity on Production_ProductInventory rows of ProductID {c.get('product')}" + (f", LocationID {c.get('location')}" if c.get("location") else " across all locations"),
        "po_count": f"Count of distinct purchase orders of VendorID {c.get('vendor')}, OrderDate from {c.get('from')} to {c.get('to')}",
        "po_units": f"Sum of OrderQty on purchase order lines of VendorID {c.get('vendor')}, OrderDate from {c.get('from')} to {c.get('to')}",
        "po_reject_rate": f"Sum of RejectedQty divided by sum of ReceivedQty on purchase order lines of VendorID {c.get('vendor')}, OrderDate from {c.get('from')} to {c.get('to')}",
        "field_value": f"Value of {c.get('table')}.{c.get('field')} for key {c.get('key')}",
        "days_since": f"Days from {c.get('table')}.{c.get('field')} (key {c.get('key')}) to {c.get('until')}",
    }[op]
    return f"{sel}; compare {'at least' if cmp_ == 'ge' else 'at most'} {thr}."


class Doc:
    def __init__(self, data, doc_id, title, kind, date_, customers=(), vendors=(), entities=None, amends=None):
        self.data, self.id, self.title, self.kind = data, doc_id, title, kind
        self.customers, self.vendors, self.entities, self.amends = list(customers), list(vendors), entities or {}, amends
        self.lines = [LABEL, "", f"# {title}", "", f"Document ID: {doc_id}", f"Date: {date_}",
                      "Entity scope: " + ", ".join([f"customer:{c}" for c in self.customers] + [f"vendor:{v}" for v in self.vendors]
                                                   + [f"{k}:{v}" for k, vs in self.entities.items() for v in vs])]
        self.statements = []

    def h(self, text):
        self.lines += ["", f"## {text}"]

    def p(self, text):
        self.lines.append(text)

    def _line(self, num, sentence):
        self.lines.append(f"{num} {sentence}" if num else sentence)

    @staticmethod
    def _para(definition, sentence):
        name = definition.rsplit("/", 1)[1]
        spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name).replace("_", " ").lower()
        low = sentence.lower()
        return name.lower() not in low and spaced not in low

    def rec(self, num, template, table, key, fields, show=None):
        row = self.data.row(table, key)
        sentence = template
        for f in fields:
            sentence = sentence.replace(f"[{f}]", fmt(f, row[f], show))
        self._line(num, sentence)
        for f in fields:
            definition = f"{PREFIX}field/{table}/{f}"
            self.statements.append({
                "sentence": sentence, "kind": "recorded_value", "definition": definition,
                "stated": {"value": norm(row[f]), "unit": None, "period": None},
                "recorded": {"table": table, "key": key, "field": f, "value": norm(row[f]), "surface": fmt(f, row[f], show)},
                "paraphrase": self._para(definition, sentence)})

    def commit(self, num, sentence, definition, unit, period, check, cmp_, thr, note="", supersedes=None):
        self._line(num, sentence)
        actual = self.data.compute(check)
        met = actual >= thr if cmp_ == "ge" else actual <= thr
        d = PREFIX + definition
        st = {"sentence": sentence, "kind": "commitment", "definition": d,
              "stated": {"value": thr, "unit": unit, "period": period},
              "commitment": {"check": check, "comparison": cmp_, "threshold": thr, "actual": actual, "met": met,
                             "logic": logic(check, cmp_, thr), "note": note},
              "paraphrase": self._para(d, sentence)}
        if supersedes:
            st["supersedes"] = supersedes
        self.statements.append(st)
        return sentence

    def none(self, num, sentence, why=""):
        self._line(num, sentence)
        self.statements.append({"sentence": sentence, "kind": "no_counterpart", "why": why or "The data records nothing like it."})

    def trap(self, num, sentence, reason, tempts=None):
        self._line(num, sentence)
        self.statements.append({"sentence": sentence, "kind": "trap", "trap": {"reason": reason, "tempts": PREFIX + tempts if tempts else None}})

    def text(self):
        return "\n".join(self.lines) + "\n"


def closing(d, governing=False, signatory=False, retention=False, notice=False, note=""):
    d.h("General terms")
    d.p("This document is confidential between the parties named in it.")
    d.p("Each party will tell the other promptly if a fact stated here becomes incorrect.")
    d.p("Quantities are counted in the unit of measure of the product concerned unless a clause says otherwise.")
    d.p("A period stated as a calendar year runs from 1 January to 31 December of that year.")
    n = 1
    if governing:
        d.none(f"G.{n}", "The courts of King County in the State of Washington decide any dispute about this document."); n += 1
    if notice:
        d.none(f"G.{n}", "Notices go to the contracts desk at the correspondence address held on the supplier file."); n += 1
    if retention:
        d.none(f"G.{n}", "The company keeps this document for ten years after the relationship ends."); n += 1
    if signatory:
        d.none(f"G.{n}", "Signed for the company by an authorised officer whose name is withheld in this synthetic pack."); n += 1
    if note:
        d.p(note)
    d.p("This is synthetic demonstration content. It names no real person, company, address, telephone number or email address.")


def store_block(d, cust, store, role="buyer"):
    d.h("1. Parties")
    d.p(f"The {role} is customer {cust} of the fictional AdventureWorks sample company.")
    d.rec("1.1", f"The {role} trades as [Name].", "Sales_Store", {"BusinessEntityID": store}, ["Name"])
    d.rec("1.2", f"The {role}'s ledger code is [AccountNumber] and its orders are booked to outlet [StoreID].", "Sales_Customer",
          {"CustomerID": cust}, ["AccountNumber", "StoreID"])
    d.rec("1.3", f"The {role} is served from sales region [TerritoryID].", "Sales_Customer", {"CustomerID": cust}, ["TerritoryID"])


def Y(year):
    return f"{year}-01-01", f"{year}-12-31"


def build_docs(data):
    docs = []
    pr = lambda p: data.row("Production_Product", {"ProductID": p})

    # MFG-101 customer supply agreement, customer 29837
    d = Doc(data, "MFG-101", "Customer supply agreement", "customer supply agreement", "2024-01-15", customers=[29837], entities={"product": [813]})
    d.p("Agreement for the supply of cycling components and accessories to one retail customer.")
    store_block(d, 29837, 1072)
    d.h("2. Covered product")
    d.rec("2.1", "The principal article is [Name].", "Production_Product", {"ProductID": 813}, ["Name"])
    d.rec("2.2", "Its catalogue code is [ProductNumber] and its catalogue price is [ListPrice] per unit.", "Production_Product", {"ProductID": 813}, ["ProductNumber", "ListPrice"])
    d.h("3. Purchase commitments")
    c31 = d.commit("3.1", "The buyer commits to take at least 25 units across all products during 2024.", "metric/sales_ordered_units", "units", "2024",
                   {"op": "sales_units", "customer": 29837, "from": Y24[0], "to": Y24[1]}, "ge", 25)
    d.commit("3.2", "The buyer commits to take at least 10 units of the HL Road Handlebars during 2024.", "metric/sales_ordered_units", "units", "2024",
             {"op": "sales_units", "customer": 29837, "product": 813, "from": Y24[0], "to": Y24[1]}, "ge", 10)
    d.commit("3.3", "The buyer's invoiced spend for 2024 will be at least USD 10,000.", "metric/sales_line_revenue", "USD", "2024",
             {"op": "sales_revenue", "customer": 29837, "from": Y24[0], "to": Y24[1]}, "ge", 10000,
             note="The source records no currency. The contract states USD.")
    d.commit("3.4", "The buyer will place at least 8 separate orders during 2024.", "metric/sales_order_count", "orders", "2024",
             {"op": "sales_orders", "customer": 29837, "from": Y24[0], "to": Y24[1]}, "ge", 8)
    d.h("4. Commercial terms")
    d.trap("4.1", "The agreed price for the handlebars is EUR 110 per unit.", "A price in euros. The source records no currency and the catalogue price is a different figure.", "field/Production_Product/ListPrice")
    d.trap("4.2", "Framework reference SA-2024-0117 identifies this agreement.", "A document reference, not a customer number, order number or ledger code.", "field/Sales_Customer/AccountNumber")
    d.none("4.3", "Invoices are payable within 45 days of the invoice date.")
    d.none("4.4", "This agreement runs for 3 years and renews automatically unless either party gives notice.")
    closing(d, governing=True, signatory=True)
    docs.append(d)

    # MFG-102 amendment
    d2 = Doc(data, "MFG-102", "Amendment to customer supply agreement MFG-101", "supply agreement amendment", "2024-07-01", customers=[29837], amends="MFG-101")
    d2.p("This amendment changes one term of customer supply agreement MFG-101 dated 15 January 2024.")
    store_block(d2, 29837, 1072)
    d2.h("2. Amended term")
    d2.commit("2.1", "Clause 3.1 of MFG-101 is replaced: the buyer commits to take at least 40 units across all products during 2024, in place of 25 units.",
              "metric/sales_ordered_units", "units", "2024", {"op": "sales_units", "customer": 29837, "from": Y24[0], "to": Y24[1]}, "ge", 40,
              supersedes=c31, note="Replaces the 25 unit threshold of MFG-101 clause 3.1. The amended threshold applies.")
    d2.p("2.2 All other terms of MFG-101 remain unchanged.")
    d2.h("3. Other points")
    d2.rec("3.1", "The first call-off under the amendment was dispatched on [ShipDate].", "Sales_SalesOrderHeader", {"SalesOrderID": 51170}, ["ShipDate"])
    d2.rec("3.2", "That order is numbered [SalesOrderNumber] and was placed on [OrderDate].", "Sales_SalesOrderHeader", {"SalesOrderID": 51170}, ["SalesOrderNumber", "OrderDate"])
    d2.trap("3.3", "Amendment reference AM-2024-0031 identifies this letter.", "A document reference, not an order number.", "field/Sales_SalesOrderHeader/SalesOrderNumber")
    d2.none("3.4", "The buyer must return a signed copy within 10 working days.")
    d2.trap("3.5", "The buyer says it plans to sell roughly 300 units a year across all its outlets.", "An unverified sales forecast of the buyer, not an order quantity of the data.", "metric/sales_ordered_units")
    closing(d2, signatory=True, retention=True)
    docs.append(d2)

    # MFG-103 purchase order confirmation, vendor 1492, PO 325
    d = Doc(data, "MFG-103", "Purchase order confirmation", "purchase order confirmation", "2024-04-24", vendors=[1492], entities={"purchase_order": [325]})
    d.p("Confirmation from the supplier of one purchase order placed by the AdventureWorks purchasing team.")
    d.h("1. Supplier")
    d.rec("1.1", "The supplier trades as [Name].", "Purchasing_Vendor", {"BusinessEntityID": 1492}, ["Name"])
    d.rec("1.2", "The supplier's ledger code is [AccountNumber].", "Purchasing_Vendor", {"BusinessEntityID": 1492}, ["AccountNumber"])
    d.h("2. Order")
    po = {"PurchaseOrderID": 325}
    d.rec("2.1", "The order was placed on [OrderDate] and dispatch is expected on [ShipDate].", "Purchasing_PurchaseOrderHeader", po, ["OrderDate", "ShipDate"])
    d.rec("2.2", "It travels by shipping option [ShipMethodID] and carries revision [RevisionNumber].", "Purchasing_PurchaseOrderHeader", po, ["ShipMethodID", "RevisionNumber"])
    d.rec("2.3", "The goods are worth [SubTotal] before tax of [TaxAmt] and freight of [Freight].", "Purchasing_PurchaseOrderHeader", po, ["SubTotal", "TaxAmt", "Freight"])
    d.rec("2.4", "The amount payable is [TotalDue].", "Purchasing_PurchaseOrderHeader", po, ["TotalDue"])
    d.rec("2.5", "The buyer processed the order as employee [EmployeeID].", "Purchasing_PurchaseOrderHeader", po, ["EmployeeID"])
    ln = {"PurchaseOrderID": 325, "PurchaseOrderDetailID": 726}
    d.rec("2.6", "Line 726 is for product [ProductID], [OrderQty] units at [UnitPrice] each, due on [DueDate].", "Purchasing_PurchaseOrderDetail", ln,
          ["ProductID", "OrderQty", "UnitPrice", "DueDate"])
    d.h("3. Supplier undertakings")
    d.commit("3.1", "The supplier will deliver at least 3 units on line 726 by 8 May 2024.", "field/Purchasing_PurchaseOrderDetail/ReceivedQty", "units", "by 2024-05-08",
             {"op": "field_value", "table": "Purchasing_PurchaseOrderDetail", "key": ln, "field": "ReceivedQty"}, "ge", 3)
    d.commit("3.2", "The supplier will have no more than 0 units turned away on line 726.", "field/Purchasing_PurchaseOrderDetail/RejectedQty", "units", "line 726",
             {"op": "field_value", "table": "Purchasing_PurchaseOrderDetail", "key": ln, "field": "RejectedQty"}, "le", 0)
    d.commit("3.3", "The amount payable for the whole order will not exceed USD 1,600.", "field/Purchasing_PurchaseOrderHeader/TotalDue", "USD", "order 325",
             {"op": "field_value", "table": "Purchasing_PurchaseOrderHeader", "key": po, "field": "TotalDue"}, "le", 1600,
             note="The source records no currency. The contract states USD.")
    d.none("3.4", "Payment is due 30 days after receipt of the supplier invoice.")
    d.none("3.5", "Ownership of the goods passes to the buyer on delivery.")
    d.trap("3.6", "The supplier's acknowledgement number is OA-77412.", "A document reference of the supplier, not a purchase order number.", "field/Purchasing_PurchaseOrderHeader/PurchaseOrderID")
    d.trap("3.7", "Carriage is charged at USD 8 per carton.", "A rate per carton, where the data holds one freight amount per order.", "field/Purchasing_PurchaseOrderHeader/Freight")
    closing(d, governing=True, notice=True)
    docs.append(d)

    # MFG-104 supplier scorecard, vendor 1492
    def scorecard(doc_id, vendor, date_, product, rej_note):
        k = Doc(data, doc_id, "Supplier performance scorecard", "supplier scorecard", date_, vendors=[vendor], entities={"product": [product]})
        k.p("Annual review of one supplier by the AdventureWorks purchasing team. The review year is 2024.")
        k.h("1. Supplier")
        k.rec("1.1", "The supplier trades as [Name].", "Purchasing_Vendor", {"BusinessEntityID": vendor}, ["Name"])
        k.rec("1.2", "Its credit grade is [CreditRating].", "Purchasing_Vendor", {"BusinessEntityID": vendor}, ["CreditRating"])
        k.rec("1.3", "The supplier [PreferredVendorStatus] and is [ActiveFlag].", "Purchasing_Vendor", {"BusinessEntityID": vendor},
              ["PreferredVendorStatus", "ActiveFlag"], show={"PreferredVendorStatus": {"1": "is on the preferred supplier list", "0": "is not preferred"},
                                                             "ActiveFlag": {"1": "currently trading", "0": "no longer trading"}})
        k.h("2. Reference product")
        key = {"ProductID": product, "BusinessEntityID": vendor}
        k.rec("2.1", "For product [ProductID] deliveries usually arrive [AverageLeadTime] days after the order is placed.", "Purchasing_ProductVendor", key, ["ProductID", "AverageLeadTime"])
        k.rec("2.2", "The supplier accepts orders from [MinOrderQty] up to [MaxOrderQty] units.", "Purchasing_ProductVendor", key, ["MinOrderQty", "MaxOrderQty"])
        k.h("3. Performance targets for 2024")
        k.commit("3.1", "The supplier will receive at least 15 purchase orders from us during 2024.", "field/Purchasing_PurchaseOrderHeader/PurchaseOrderID", "orders", "2024",
                 {"op": "po_count", "vendor": vendor, "from": Y24[0], "to": Y24[1]}, "ge", 15)
        k.commit("3.2", "Units turned away on arrival will stay at or below 3 percent of units received during 2024.", "field/Purchasing_PurchaseOrderDetail/RejectedQty", "ratio", "2024",
                 {"op": "po_reject_rate", "vendor": vendor, "from": Y24[0], "to": Y24[1]}, "le", 0.03, note=rej_note)
        k.commit("3.3", "We will buy at least 200 units from this supplier during 2024.", "field/Purchasing_PurchaseOrderDetail/OrderQty", "units", "2024",
                 {"op": "po_units", "vendor": vendor, "from": Y24[0], "to": Y24[1]}, "ge", 200)
        k.h("4. Observations")
        k.none("4.1", "The supplier scored 94 out of 100 on our delivery punctuality survey.")
        k.trap("4.2", "Spend with the supplier in the third quarter was USD 40,000.", "A quarterly spend figure, where the data holds an amount per order and no spend by quarter for a vendor.", "field/Purchasing_PurchaseOrderHeader/TotalDue")
        k.trap("4.3", "Supplier audit certificate QA-2201 was sighted.", "A certificate number of another namespace, not a vendor number or account code.", "field/Purchasing_Vendor/AccountNumber")
        closing(k, retention=True, signatory=True)
        return k
    docs.append(scorecard("MFG-104", 1492, "2025-01-20", 422, "The ratio is above the target."))

    # MFG-105 lead-time and minimum-order agreement, vendor 1496, product 359
    d = Doc(data, "MFG-105", "Vendor lead-time and minimum-order agreement", "lead-time and minimum-order agreement", "2024-02-12", vendors=[1496], entities={"product": [359]})
    d.p("Agreement between the AdventureWorks purchasing team and one supplier on lead time and order sizes for one product.")
    d.h("1. Parties and product")
    d.rec("1.1", "The supplier trades as [Name].", "Purchasing_Vendor", {"BusinessEntityID": 1496}, ["Name"])
    d.rec("1.2", "The product is [Name] with catalogue code [ProductNumber].", "Production_Product", {"ProductID": 359}, ["Name", "ProductNumber"])
    key = {"ProductID": 359, "BusinessEntityID": 1496}
    d.h("2. Recorded supply terms")
    d.rec("2.1", "The supplier usually delivers within [AverageLeadTime] days of the order.", "Purchasing_ProductVendor", key, ["AverageLeadTime"])
    d.rec("2.2", "Its usual price is [StandardPrice] and the last delivery was invoiced at [LastReceiptCost].", "Purchasing_ProductVendor", key, ["StandardPrice", "LastReceiptCost"])
    d.rec("2.3", "The last delivery arrived on [LastReceiptDate].", "Purchasing_ProductVendor", key, ["LastReceiptDate"])
    d.rec("2.4", "The supplier takes orders of at least [MinOrderQty] and at most [MaxOrderQty] units, counted in [UnitMeasureCode].", "Purchasing_ProductVendor", key,
          ["MinOrderQty", "MaxOrderQty", "UnitMeasureCode"])
    d.rec("2.5", "Right now [OnOrderQty] units are on order with the supplier.", "Purchasing_ProductVendor", key, ["OnOrderQty"])
    d.h("3. New undertakings")
    d.commit("3.1", "The supplier will deliver within 14 days of each order.", "field/Purchasing_ProductVendor/AverageLeadTime", "days", "standing term",
             {"op": "field_value", "table": "Purchasing_ProductVendor", "key": key, "field": "AverageLeadTime"}, "le", 14)
    d.commit("3.2", "The usual price will not rise above USD 46 per unit.", "field/Purchasing_ProductVendor/StandardPrice", "USD", "standing term",
             {"op": "field_value", "table": "Purchasing_ProductVendor", "key": key, "field": "StandardPrice"}, "le", 46, note="The source records no currency. The contract states USD.")
    d.commit("3.3", "The smallest order the supplier may insist on will not exceed 2 units.", "field/Purchasing_ProductVendor/MinOrderQty", "units", "standing term",
             {"op": "field_value", "table": "Purchasing_ProductVendor", "key": key, "field": "MinOrderQty"}, "le", 2)
    d.commit("3.4", "The supplier will keep at least 5 units on order for us at all times.", "field/Purchasing_ProductVendor/OnOrderQty", "units", "standing term",
             {"op": "field_value", "table": "Purchasing_ProductVendor", "key": key, "field": "OnOrderQty"}, "ge", 5)
    d.none("3.5", "Either party may end this agreement with 90 days of written notice.")
    d.trap("3.6", "Supplier agreement reference VA-1496-02 identifies this agreement.", "A document reference, not a vendor number.", "field/Purchasing_Vendor/BusinessEntityID")
    d.trap("3.7", "The supplier quoted a lead time of 3 weeks for rush orders.", "A lead time in weeks for a different order class, where the data holds an average in days.", "field/Purchasing_ProductVendor/AverageLeadTime")
    closing(d, governing=True, notice=True)
    docs.append(d)

    # MFG-106 production work-order summary, product 950
    d = Doc(data, "MFG-106", "Production work-order summary with scrap", "work-order summary", "2025-01-10", entities={"product": [950], "work_order": [39419]})
    d.p("Summary of one product's work orders for the year 2024 from the plant control office.")
    d.h("1. Product")
    d.rec("1.1", "The product is [Name] with catalogue code [ProductNumber].", "Production_Product", {"ProductID": 950}, ["Name", "ProductNumber"])
    d.rec("1.2", "It [MakeFlag] and is [FinishedGoodsFlag].", "Production_Product", {"ProductID": 950}, ["MakeFlag", "FinishedGoodsFlag"],
          show={"MakeFlag": {"1": "is built in house", "0": "is bought in"}, "FinishedGoodsFlag": {"1": "sold as a finished item", "0": "used only as a part"}})
    d.rec("1.3", "Each build is expected to take [DaysToManufacture] days.", "Production_Product", {"ProductID": 950}, ["DaysToManufacture"])
    d.h("2. Example work order")
    wo = {"WorkOrderID": 39419}
    d.rec("2.1", "Work order 39419 asked for [OrderQty] units, of which [StockedQty] passed into stock and [ScrappedQty] were thrown away.", "Production_WorkOrder", wo,
          ["OrderQty", "StockedQty", "ScrappedQty"])
    d.rec("2.2", "It began on [StartDate], finished on [EndDate] and was promised for [DueDate].", "Production_WorkOrder", wo, ["StartDate", "EndDate", "DueDate"])
    d.rec("2.3", "The loss was blamed on the cause coded [ScrapReasonID].", "Production_WorkOrder", wo, ["ScrapReasonID"])
    d.rec("2.4", "Cause 3 is described as [Name].", "Production_ScrapReason", {"ScrapReasonID": 3}, ["Name"])
    rt = data.where("Production_WorkOrderRouting", WorkOrderID=39419)[0]
    rkey = {"WorkOrderID": 39419, "ProductID": 950, "OperationSequence": rt["OperationSequence"]}
    d.rec("2.5", f"Step {rt['OperationSequence']} of that order ran at work centre [LocationID] and used [ActualResourceHrs] machine hours.", "Production_WorkOrderRouting", rkey,
          ["LocationID", "ActualResourceHrs"])
    d.rec("2.6", "The step was budgeted at [PlannedCost] and cost [ActualCost].", "Production_WorkOrderRouting", rkey, ["PlannedCost", "ActualCost"])
    d.h("3. Targets for 2024")
    d.commit("3.1", "Scrap for the product will stay at or below 0.5 percent of units asked for in 2024.", "metric/production_scrap_rate", "ratio", "2024",
             {"op": "wo_scrap_rate", "product": 950, "from": Y24[0], "to": Y24[1]}, "le", 0.005)
    d.commit("3.2", "The plant will start builds for at least 20,000 units of the product in 2024.", "metric/production_requested_units", "units", "2024",
             {"op": "wo_requested", "product": 950, "from": Y24[0], "to": Y24[1]}, "ge", 20000)
    d.commit("3.3", "No more than 150 units of the product will be thrown away in 2024.", "metric/production_scrapped_units", "units", "2024",
             {"op": "wo_scrapped", "product": 950, "from": Y24[0], "to": Y24[1]}, "le", 150)
    d.commit("3.4", "No build of the product started in 2024 will be left unfinished.", "metric/open_work_orders", "work orders", "2024",
             {"op": "wo_open", "product": 950, "from": Y24[0], "to": Y24[1]}, "le", 0, note="The metric is zero for every product in this data set.")
    d.trap("3.5", "The yield target is 99.8 percent per batch.", "A yield per batch, where the data holds scrap totals per work order and a rate over a period.", "metric/production_scrap_rate")
    d.trap("3.6", "Lot reference LOT-8841 covers the example build.", "A lot reference, not a work order number.", "field/Production_WorkOrder/WorkOrderID")
    d.none("3.7", "The plant manager reviews scrap figures every Monday.")
    closing(d, retention=True)
    docs.append(d)

    # MFG-107 quality inspection report, product 327
    d = Doc(data, "MFG-107", "Quality inspection report", "quality inspection report", "2024-12-18", entities={"product": [327], "work_order": [47662, 50532]})
    d.p("Inspection of one fabricated part after a year of production. The review year is 2024.")
    d.h("1. Part")
    d.rec("1.1", "The part is [Name], catalogue code [ProductNumber].", "Production_Product", {"ProductID": 327}, ["Name", "ProductNumber"])
    d.rec("1.2", "A unit costs [StandardCost] to make on the plant's books.", "Production_Product", {"ProductID": 327}, ["StandardCost"])
    d.h("2. Worst work orders")
    d.rec("2.1", "Work order 47662 started on [StartDate] with [OrderQty] units requested and [ScrappedQty] discarded.", "Production_WorkOrder", {"WorkOrderID": 47662}, ["StartDate", "OrderQty", "ScrappedQty"])
    d.rec("2.2", "The recorded cause code was [ScrapReasonID].", "Production_WorkOrder", {"WorkOrderID": 47662}, ["ScrapReasonID"])
    d.rec("2.3", "That cause reads [Name].", "Production_ScrapReason", {"ScrapReasonID": 10}, ["Name"])
    d.rec("2.4", "Work order 50532 started on [StartDate] with [ScrappedQty] units discarded.", "Production_WorkOrder", {"WorkOrderID": 50532}, ["StartDate", "ScrappedQty"])
    d.rec("2.5", "Its cause reads [Name].", "Production_ScrapReason", {"ScrapReasonID": 5}, ["Name"])
    d.h("3. Acceptance limits for 2024")
    d.commit("3.1", "Discards of the part must stay at or below 350 units in 2024.", "metric/production_scrapped_units", "units", "2024",
             {"op": "wo_scrapped", "product": 327, "from": Y24[0], "to": Y24[1]}, "le", 350)
    d.commit("3.2", "Discards must stay at or below 1 percent of units asked for in 2024.", "metric/production_scrap_rate", "ratio", "2024",
             {"op": "wo_scrap_rate", "product": 327, "from": Y24[0], "to": Y24[1]}, "le", 0.01)
    d.commit("3.3", "The plant must start builds for at least 40,000 units of the part in 2024.", "metric/production_requested_units", "units", "2024",
             {"op": "wo_requested", "product": 327, "from": Y24[0], "to": Y24[1]}, "ge", 40000)
    d.trap("3.4", "A line operator suggested that drill wear was caused by a bad lot from a supplier, which is unverified.", "An unverified opinion. It is not a recorded cause code.", "field/Production_ScrapReason/Name")
    d.trap("3.5", "The customer complaint rate for the part is 0.2 percent of units shipped.", "A rate on shipped units, where the data records discards on work orders.", "metric/production_scrap_rate")
    d.none("3.6", "Inspectors sample 1 unit in 50 at the end of each build.")
    closing(d, retention=True, signatory=True)
    docs.append(d)

    # MFG-108 BOM change notice, assembly 972
    d = Doc(data, "MFG-108", "Bill-of-materials change notice", "bill-of-materials change notice", "2024-03-04", entities={"product": [972, 806, 521], "bom_line": [99, 299, 3393]})
    d.p("Engineering notice about the parts list of one finished bicycle.")
    d.h("1. Assembly")
    d.rec("1.1", "The finished bicycle is [Name], catalogue code [ProductNumber].", "Production_Product", {"ProductID": 972}, ["Name", "ProductNumber"])
    d.rec("1.2", "The part under review is [Name].", "Production_Product", {"ProductID": 806}, ["Name"])
    d.h("2. Current parts-list lines")
    d.rec("2.1", "List line 99 uses part [ComponentID] in an amount of [PerAssemblyQty] for each bicycle, from [StartDate].", "Production_BillOfMaterials", {"BillOfMaterialsID": 99},
          ["ComponentID", "PerAssemblyQty", "StartDate"])
    d.rec("2.2", "List line 99 sits at depth [BOMLevel] of the tree and counts in [UnitMeasureCode].", "Production_BillOfMaterials", {"BillOfMaterialsID": 99}, ["BOMLevel", "UnitMeasureCode"])
    d.rec("2.3", "List line 299 for the same part stopped applying on [EndDate].", "Production_BillOfMaterials", {"BillOfMaterialsID": 299}, ["EndDate"])
    d.rec("2.4", "List line 299 had called for [PerAssemblyQty] of the part per bicycle.", "Production_BillOfMaterials", {"BillOfMaterialsID": 299}, ["PerAssemblyQty"])
    d.rec("2.5", "List line 3393 links the bicycle to assembly [ComponentID].", "Production_BillOfMaterials", {"BillOfMaterialsID": 3393}, ["ComponentID"])
    d.h("3. Changes announced")
    d.commit("3.1", "From the next revision no more than 1 headset will be fitted per bicycle on line 99.", "field/Production_BillOfMaterials/PerAssemblyQty", "units", "next revision",
             {"op": "field_value", "table": "Production_BillOfMaterials", "key": {"BillOfMaterialsID": 99}, "field": "PerAssemblyQty"}, "le", 1)
    d.commit("3.2", "Line 3393 will call for at least 2 seat assemblies per bicycle.", "field/Production_BillOfMaterials/PerAssemblyQty", "units", "next revision",
             {"op": "field_value", "table": "Production_BillOfMaterials", "key": {"BillOfMaterialsID": 3393}, "field": "PerAssemblyQty"}, "ge", 2,
             note="The change is announced, not yet recorded. The row still shows 1.")
    d.commit("3.3", "Line 99 will stay at depth 1 of the parts tree.", "field/Production_BillOfMaterials/BOMLevel", "level", "next revision",
             {"op": "field_value", "table": "Production_BillOfMaterials", "key": {"BillOfMaterialsID": 99}, "field": "BOMLevel"}, "le", 1)
    d.trap("3.4", "Engineering change request ECR-5530 authorises this notice.", "A change request number, not a parts-list line number.", "field/Production_BillOfMaterials/BillOfMaterialsID")
    d.trap("3.5", "The change saves 0.4 kilograms per bicycle.", "A weight in kilograms per bicycle, where the data records an amount of a part per assembly.", "field/Production_BillOfMaterials/PerAssemblyQty")
    d.none("3.6", "Drawings are updated within 5 working days of approval.")
    closing(d, signatory=True, retention=True)
    docs.append(d)

    # MFG-109 product specification sheet, product 794
    pk = {"ProductID": 794}
    d = Doc(data, "MFG-109", "Product specification sheet", "product specification sheet", "2024-06-03", entities={"product": [794]})
    d.p("Specification for one finished road bicycle, issued by the product office.")
    d.h("1. Identification")
    d.rec("1.1", "The bicycle is [Name], catalogue code [ProductNumber].", "Production_Product", pk, ["Name", "ProductNumber"])
    d.rec("1.2", "It belongs to the model family [Name].", "Production_ProductModel", {"ProductModelID": 26}, ["Name"])
    d.rec("1.3", "It is sold under the heading [Name].", "Production_ProductSubcategory", {"ProductSubcategoryID": 2}, ["Name"])
    d.rec("1.4", "That heading sits in the group [Name].", "Production_ProductCategory", {"ProductCategoryID": 1}, ["Name"])
    d.h("2. Physical and commercial data")
    d.rec("2.1", "It is finished in [Color] and the frame measures [Size].", "Production_Product", pk, ["Color", "Size"])
    d.rec("2.2", "It weighs [Weight] kilograms.", "Production_Product", pk, ["Weight"])
    d.rec("2.3", "The retail tag shows [ListPrice] and the plant's books carry a build cost of [StandardCost].", "Production_Product", pk, ["ListPrice", "StandardCost"])
    d.rec("2.4", "A build takes [DaysToManufacture] days.", "Production_Product", pk, ["DaysToManufacture"])
    d.rec("2.5", "It belongs to [ProductLine] and to the [Class] tier.", "Production_Product", pk, ["ProductLine", "Class"],
          show={"ProductLine": {"R": "the road range", "M": "the mountain range", "T": "the touring range", "S": "the standard range"},
                "Class": {"H": "high-end", "M": "medium", "L": "low-end"}})
    d.rec("2.6", "A buffer of [SafetyStockLevel] units is held and replenishment starts when stock falls to [ReorderPoint] units.", "Production_Product", pk, ["SafetyStockLevel", "ReorderPoint"])
    d.rec("2.7", "It has been on sale since [SellStartDate].", "Production_Product", pk, ["SellStartDate"])
    d.h("3. Specification targets")
    d.commit("3.1", "The build time will be cut to no more than 3 days.", "field/Production_Product/DaysToManufacture", "days", "next model year",
             {"op": "field_value", "table": "Production_Product", "key": pk, "field": "DaysToManufacture"}, "le", 3)
    d.commit("3.2", "The buffer stock will stay at 100 units or more.", "field/Production_Product/SafetyStockLevel", "units", "standing term",
             {"op": "field_value", "table": "Production_Product", "key": pk, "field": "SafetyStockLevel"}, "ge", 100)
    d.commit("3.3", "The build cost will not exceed USD 1,600.", "field/Production_Product/StandardCost", "USD", "standing term",
             {"op": "field_value", "table": "Production_Product", "key": pk, "field": "StandardCost"}, "le", 1600, note="The source records no currency. The sheet states USD.")
    d.trap("3.4", "The frame weighs 33 pounds when packed in its carton.", "A weight in pounds including packaging, where the data records kilograms of the bicycle.", "field/Production_Product/Weight")
    d.trap("3.5", "Specification sheet number SS-0794 identifies this sheet.", "A document number, not a product number.", "field/Production_Product/ProductNumber")
    d.none("3.6", "The frame carries a lifetime guarantee against cracks.")
    closing(d, retention=True)
    docs.append(d)

    # MFG-110 shipment and delivery notice, customer 29745, SO 55272
    d = Doc(data, "MFG-110", "Shipment and delivery notice", "shipment notice", "2024-09-05", customers=[29745], entities={"sales_order": [55272]})
    d.p("Notice to one retail customer about the dispatch of one order.")
    store_block(d, 29745, 876)
    so = {"SalesOrderID": 55272}
    d.h("2. Order and dispatch")
    d.rec("2.1", "The order is numbered [SalesOrderNumber] and was placed on [OrderDate].", "Sales_SalesOrderHeader", so, ["SalesOrderNumber", "OrderDate"])
    d.rec("2.2", "It was promised for [DueDate] and left the warehouse on [ShipDate].", "Sales_SalesOrderHeader", so, ["DueDate", "ShipDate"])
    d.rec("2.3", "It travels by shipping option [ShipMethodID].", "Sales_SalesOrderHeader", so, ["ShipMethodID"])
    d.rec("2.4", "That option is called [Name].", "Purchasing_ShipMethod", {"ShipMethodID": 5}, ["Name"])
    d.rec("2.5", "The carrier charges a base of [ShipBase] plus [ShipRate] per unit of weight.", "Purchasing_ShipMethod", {"ShipMethodID": 5}, ["ShipBase", "ShipRate"])
    d.rec("2.6", "The delivery cost billed on this order is [Freight] and the amount owed is [TotalDue].", "Sales_SalesOrderHeader", so, ["Freight", "TotalDue"])
    d.rec("2.7", "The order [OnlineOrderFlag].", "Sales_SalesOrderHeader", so, ["OnlineOrderFlag"], show={"OnlineOrderFlag": {"0": "was taken by a sales representative", "1": "was placed on the web shop"}})
    ln = {"SalesOrderID": 55272, "SalesOrderDetailID": 55268}
    d.rec("2.8", "The largest line carries parcel tag [CarrierTrackingNumber] for [OrderQty] units of product [ProductID].", "Sales_SalesOrderDetail", ln, ["CarrierTrackingNumber", "OrderQty", "ProductID"])
    d.rec("2.9", "Each unit is priced at [UnitPrice] with a price reduction of [UnitPriceDiscount], so the line is worth [LineTotal].", "Sales_SalesOrderDetail", ln,
          ["UnitPrice", "UnitPriceDiscount", "LineTotal"])
    d.h("3. Service levels for 2024")
    d.commit("3.1", "No order of this customer will leave the warehouse after its promised date during 2024.", "metric/late_dispatch_orders", "orders", "2024",
             {"op": "late_dispatch", "customer": 29745, "from": Y24[0], "to": Y24[1]}, "le", 0, note="The metric is zero for every customer in this data set.")
    d.commit("3.2", "Every order will leave the warehouse within 3 days of being placed during 2024.", "field/Sales_SalesOrderHeader/ShipDate", "orders over 3 days", "2024",
             {"op": "ship_gap_over", "customer": 29745, "days": 3, "from": Y24[0], "to": Y24[1]}, "le", 0)
    d.commit("3.3", "The customer will place at least 2 orders in the third quarter of 2024.", "metric/sales_order_count", "orders", "2024-07-01/2024-09-30",
             {"op": "sales_orders", "customer": 29745, "from": "2024-07-01", "to": "2024-09-30"}, "ge", 2)
    d.trap("3.4", "Container MSKU-0004417 carries the pallets to the depot.", "A container number, not a parcel tag.", "field/Sales_SalesOrderDetail/CarrierTrackingNumber")
    d.trap("3.5", "Delivery to the depot is expected within two weeks of the notice.", "A relative promise, where the data records absolute ship and due dates.", "field/Sales_SalesOrderHeader/DueDate")
    d.none("3.6", "Damaged parcels must be reported within 48 hours of delivery.")
    closing(d, notice=True)
    docs.append(d)

    # MFG-111 inventory count memo, products 950 and 794
    d = Doc(data, "MFG-111", "Inventory count memo", "inventory count memo", "2025-06-03", entities={"product": [950, 794], "location": [1, 5, 10, 50]})
    d.p("Memo from the stores office after a stock count of two products.")
    d.h("1. Products")
    d.rec("1.1", "The first product is [Name].", "Production_Product", {"ProductID": 950}, ["Name"])
    d.rec("1.2", "The second product is [Name].", "Production_Product", {"ProductID": 794}, ["Name"])
    d.h("2. Counted stock")
    d.rec("2.1", "Metal Storage holds [Quantity] units of the first product on its shelf [Shelf].", "Production_ProductInventory", {"ProductID": 950, "LocationID": 5}, ["Quantity", "Shelf"])
    d.rec("2.2", "The Tool Crib holds [Quantity] units of the first product in slot [Bin].", "Production_ProductInventory", {"ProductID": 950, "LocationID": 1}, ["Quantity", "Bin"])
    d.rec("2.3", "Finished Goods Storage holds [Quantity] units of the second product.", "Production_ProductInventory", {"ProductID": 794, "LocationID": 7}, ["Quantity"])
    d.rec("2.4", "Location 50 is labelled [Name].", "Production_Location", {"LocationID": 50}, ["Name"])
    d.rec("2.5", "Location 10 bills at [CostRate] per hour and offers [Availability] hours.", "Production_Location", {"LocationID": 10}, ["CostRate", "Availability"])
    d.h("3. Stock expectations")
    d.commit("3.1", "The first product will have at least 1,000 units in stock across all locations.", "metric/inventory_units", "units", "standing target",
             {"op": "inventory", "product": 950}, "ge", 1000)
    d.commit("3.2", "Metal Storage will keep at least 300 units of the first product.", "metric/inventory_units", "units", "standing target",
             {"op": "inventory", "product": 950, "location": 5}, "ge", 300)
    d.commit("3.3", "The Tool Crib will hold no more than 300 units of the first product.", "metric/inventory_units", "units", "standing target",
             {"op": "inventory", "product": 950, "location": 1}, "le", 300)
    d.commit("3.4", "The second product will have at least 200 units in stock across all locations.", "metric/inventory_units", "units", "standing target",
             {"op": "inventory", "product": 794}, "ge", 200)
    d.trap("3.5", "The count was taken on 3 June 2025.", "A count date. The source records no business date for inventory, only an audit timestamp.", "metric/inventory_units")
    d.trap("3.6", "Stores report 18 pallets of the second product.", "A count in pallets, where the data holds units.", "metric/inventory_units")
    d.none("3.7", "A full recount is scheduled for the end of each financial year.")
    closing(d, retention=True)
    docs.append(d)

    # MFG-112 / MFG-114 sales quotation (same template)
    def quotation(doc_id, cust, store, product, date_, units_min, price_max, orders_min):
        k = Doc(data, doc_id, "Sales quotation", "sales quotation", date_, customers=[cust], entities={"product": [product]})
        k.p("Quotation to one retail customer for one product.")
        store_block(k, cust, store, role="customer")
        k.h("2. Quoted product")
        k.rec("2.1", "The quoted article is [Name], catalogue code [ProductNumber].", "Production_Product", {"ProductID": product}, ["Name", "ProductNumber"])
        k.rec("2.2", "The catalogue price is [ListPrice] and the plant's books carry a cost of [StandardCost].", "Production_Product", {"ProductID": product}, ["ListPrice", "StandardCost"])
        k.h("3. Terms of the quotation")
        k.commit("3.1", f"The customer will take at least {units_min} units of the quoted article during 2024.", "metric/sales_ordered_units", "units", "2024",
                 {"op": "sales_units", "customer": cust, "product": product, "from": Y24[0], "to": Y24[1]}, "ge", units_min)
        k.commit("3.2", f"The average price per unit of the quoted article will not exceed USD {price_max:,.2f} during 2024.", "metric/average_unit_price", "USD per unit", "2024",
                 {"op": "avg_unit_price", "customer": cust, "product": product, "from": Y24[0], "to": Y24[1]}, "le", price_max,
                 note="The source records no currency. The quotation states USD.")
        k.commit("3.3", f"The customer will place at least {orders_min} orders in the first half of 2025.", "metric/sales_order_count", "orders", "2025-01-01/2025-06-30",
                 {"op": "sales_orders", "customer": cust, "from": "2025-01-01", "to": "2025-06-30"}, "ge", orders_min)
        k.trap("3.4", "The quotation is valid for 30 days and carries reference QT-2024-0526.", "A document reference, not an order number.", "field/Sales_SalesOrderHeader/SalesOrderNumber")
        k.trap("3.5", "A volume discount of 4 percent applies above 100 units.", "A percentage discount rule, where the data holds a discount amount per line.", "field/Sales_SalesOrderDetail/UnitPriceDiscount")
        k.none("3.6", "Delivery is free of charge to the main warehouse.")
        closing(k, governing=True, signatory=True)
        return k
    docs.append(quotation("MFG-112", 29586, 532, 875, "2024-03-18", 30, 5.0, 3))

    # MFG-113 warranty claim note, customer 29745, SO 61197
    d = Doc(data, "MFG-113", "Warranty claim note", "warranty claim note", "2025-02-20", customers=[29745], entities={"sales_order": [61197], "product": [794]})
    d.p("Note from the service desk about a claim filed by one retail customer on 20 February 2025.")
    store_block(d, 29745, 876, role="claimant")
    d.h("2. Order concerned")
    d.rec("2.1", "The order is numbered [SalesOrderNumber] and was placed on [OrderDate].", "Sales_SalesOrderHeader", {"SalesOrderID": 61197}, ["SalesOrderNumber", "OrderDate"])
    d.rec("2.2", "It left the warehouse on [ShipDate].", "Sales_SalesOrderHeader", {"SalesOrderID": 61197}, ["ShipDate"])
    ln = {"SalesOrderID": 61197, "SalesOrderDetailID": 76149}
    d.rec("2.3", "The line concerned covers [OrderQty] units of product [ProductID] at [UnitPrice] each, [LineTotal] in all.", "Sales_SalesOrderDetail", ln, ["OrderQty", "ProductID", "UnitPrice", "LineTotal"])
    d.rec("2.4", "The product is [Name] and is finished in [Color].", "Production_Product", {"ProductID": 794}, ["Name", "Color"])
    d.h("3. Claim assessment")
    d.trap("3.1", "The claimant alleges that two frames cracked at the head tube, which is unverified because no unit has been inspected.", "An unverified allegation. It is not a recorded scrap reason or a quality finding.", "field/Production_ScrapReason/Name")
    d.commit("3.2", "The claim covers at most 4 units, the amount bought on the line.", "field/Sales_SalesOrderDetail/OrderQty", "units", "order line",
             {"op": "field_value", "table": "Sales_SalesOrderDetail", "key": ln, "field": "OrderQty"}, "le", 4)
    d.commit("3.3", "Claims are accepted only on order lines worth at least USD 5,000.", "field/Sales_SalesOrderDetail/LineTotal", "USD", "order line",
             {"op": "field_value", "table": "Sales_SalesOrderDetail", "key": ln, "field": "LineTotal"}, "ge", 5000, note="The source records no currency. The note states USD.")
    d.commit("3.4", "Claims must be raised within 60 days of the dispatch date.", "field/Sales_SalesOrderHeader/ShipDate", "days", "claim window",
             {"op": "days_since", "table": "Sales_SalesOrderHeader", "key": {"SalesOrderID": 61197}, "field": "ShipDate", "until": "2025-02-20"}, "le", 60,
             note="Days from ShipDate to the claim date 2025-02-20.")
    d.none("3.5", "The warranty runs 24 months from delivery.")
    d.none("3.6", "Replacement units ship within 10 working days of approval.")
    d.trap("3.7", "Claim reference WC-2025-0063 identifies this note.", "A claim reference, not an order number.", "field/Sales_SalesOrderHeader/SalesOrderNumber")
    closing(d, retention=True)
    docs.append(d)

    docs.append(quotation("MFG-114", 29745, 876, 999, "2024-03-18", 2, 300.0, 3))
    docs.sort(key=lambda x: x.id)
    return docs


def build(csv_dir, out):
    out = Path(out)
    (out / "markdown").mkdir(parents=True, exist_ok=True)
    docs = build_docs(Data(csv_dir))
    entries = []
    for d in docs:
        text = d.text()
        path = f"markdown/{d.id}.md"
        (out / path).write_bytes(text.encode("utf-8"))
        e = {"id": d.id, "title": d.title, "type": d.kind, "path": path, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
             "customer_keys": [str(c) for c in d.customers], "vendor_keys": [str(v) for v in d.vendors],
             "entity_keys": {k: [str(x) for x in v] for k, v in d.entities.items()}}
        if d.amends:
            e["amends"] = d.amends
        e["statements"] = d.statements
        entries.append(e)
    manifest = {
        "generator": "demo_data/manufacturing/documents/generate_pack2.py", "seed": SEED, "date": "2026-11-21", "synthetic": True,
        "note": ("Planted truth from the generator, not an independent gold set. Every document is synthetic. Recorded values are read "
                 "from the AdventureWorks CSV projections. Commitment truth is computed from the same rows. Sales and purchase periods "
                 "are real calendar years and quarters of the data. No randomness is used. The paraphrase flag is true when the sentence "
                 "contains neither the field or metric name nor its spaced form."),
        "model_definition_format": PREFIX + "{field/<Dataset>/<Field>|metric/<name>}",
        "documents": entries}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    build(CSV_DIR, OUT)
