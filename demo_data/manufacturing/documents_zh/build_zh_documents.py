"""Deterministic builder of the Chinese manufacturing contract samples (MFG-ZH-01 ... MFG-ZH-06).

Three documents fill official model contract texts (official_text/*.md, wording unchanged except filled blanks).
Three are synthetic documents written for this demo. Every party, product, order and vendor value comes from the
AdventureWorks CSV projection in ../data/csv. No network, no model call, no randomness.

Run (needs PyMuPDF; python-docx only for --source-dir; pdftotext for --check):
  python build_zh_documents.py                 # build PDFs, links.json
  python build_zh_documents.py --check         # rebuild nothing; verify links, PDF text and determinism inputs
  python build_zh_documents.py --check --source-dir <dir with the official .docx files>   # also diff clause wording
Font: Arial Unicode MS from macOS (/System/Library/Fonts/Supplemental), subset-embedded. It has no f-ligatures,
so Latin names extract verbatim.
"""
import argparse
import csv
import hashlib
import html
import io
import json
import re
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import fitz  # PyMuPDF

HERE = Path(__file__).resolve().parent
CSV_DIR = HERE.parent / "data" / "csv"
OFFICIAL = HERE / "official_text"
FONT_DIR = "/System/Library/Fonts/Supplemental"
FONT_CSS = "@font-face {font-family: au; src: url('Arial Unicode.ttf');}"
SELLER = "示例自行车制造有限公司"
SELLER_NOTE = "（合成名称，代表样例数据中的制造商）"
PH = "（合成占位）"
CITY = "示例市"
BODIES = {
    "BTHF-2023-0102": ("北京市市场监督管理局、天津市市场监督管理委员会、河北省市场监督管理局",
                       "Beijing Municipal Administration for Market Regulation, Tianjin Municipal Commission for "
                       "Market Regulation and Hebei Provincial Administration for Market Regulation"),
    "GF-2000-0101": ("国家工商行政管理总局", "State Administration for Industry and Commerce of the PRC"),
    "GF-2000-0301": ("国家工商行政管理总局", "State Administration for Industry and Commerce of the PRC"),
}


# ---------------------------------------------------------------- data
class Data:
    def __init__(self, root):
        self.root, self.cache = Path(root), {}

    def rows(self, table):
        if table not in self.cache:
            path = self.root / f"{table}.csv"
            if not path.exists():
                sys.exit(f"missing CSV {path}; run ../scripts/prepare_data.py first")
            with open(path, newline="", encoding="utf-8") as fh:
                self.cache[table] = list(csv.DictReader(fh))
        return self.cache[table]

    def one(self, table, **key):
        hits = [r for r in self.rows(table) if all(r[k] == str(v) for k, v in key.items())]
        if len(hits) != 1:
            raise SystemExit(f"{table} {key}: expected 1 row, found {len(hits)}")
        return hits[0]

    def value(self, table, column, key, agg=None):
        if agg == "sum_2024_qty_by_customer_product":
            orders = {r["SalesOrderID"] for r in self.rows("Sales_SalesOrderHeader")
                      if r["CustomerID"] == key["CustomerID"] and r["OrderDate"][:4] == "2024"}
            return str(sum(int(r["OrderQty"]) for r in self.rows("Sales_SalesOrderDetail")
                           if r["SalesOrderID"] in orders and r["ProductID"] == key["ProductID"]))
        if agg == "reject_rate_2024_by_vendor":
            pos = {r["PurchaseOrderID"] for r in self.rows("Purchasing_PurchaseOrderHeader")
                   if r["VendorID"] == key["VendorID"] and r["OrderDate"][:4] == "2024"}
            lines = [r for r in self.rows("Purchasing_PurchaseOrderDetail") if r["PurchaseOrderID"] in pos]
            rej = sum(Decimal(r["RejectedQty"]) for r in lines)
            rec = sum(Decimal(r["ReceivedQty"]) for r in lines)
            return f"{(rej / rec * 100).quantize(Decimal('0.01'))}%"
        return self.one(table, **key)[column].strip()


def money(raw):
    s = format(Decimal(raw).normalize(), "f")
    whole, _, frac = s.partition(".")
    return f"{whole}.{frac.ljust(2, '0')}"


def cdate(raw):
    y, m, d = raw[:10].split("-")
    return f"{int(y)}年{int(m)}月{int(d)}日"


def ymd(raw):
    y, m, d = raw[:10].split("-")
    return str(int(y)), str(int(m)), str(int(d))


def norm(v):
    """Comparable form: Chinese or ISO dates -> ISO, numbers -> Decimal, else stripped text."""
    v = str(v).strip()
    m = re.fullmatch(r"(\d{4})年(\d{1,2})月(\d{1,2})日", v)
    if m:
        return f"{m[1]}-{int(m[2]):02d}-{int(m[3]):02d}"
    m = re.fullmatch(r"(\d{4}-\d\d-\d\d)( 00:00:00\.000)?", v)
    if m:
        return m[1]
    try:
        return Decimal(v.rstrip("%").replace(",", ""))
    except Exception:
        return v


# ---------------------------------------------------------------- documents
def unit(product):
    return "台" if product["ProductNumber"].startswith("BK-") else "件"


def build_docs(db):
    docs, links = [], []

    def link(doc, clause, label, written, table, column, key, agg=None, differs=False, reason=None):
        links.append({"document": doc, "clause": clause, "label_zh": label, "value_as_written": written,
                      "table": table, "column": column, "key": key, "aggregate": agg,
                      "table_value": db.value(table, column, key, agg) if table else None,
                      "differs": differs, **({"reason": reason} if reason else {})})

    def store_of(cid):
        cust = db.one("Sales_Customer", CustomerID=cid)
        return cust, db.one("Sales_Store", BusinessEntityID=cust["StoreID"])

    def order_items(doc, soid, delivery_label):
        head = db.one("Sales_SalesOrderHeader", SalesOrderID=soid)
        lines = [r for r in db.rows("Sales_SalesOrderDetail") if r["SalesOrderID"] == soid]
        rows = []
        for i, ln in enumerate(lines, 1):
            p = db.one("Production_Product", ProductID=ln["ProductID"])
            rows.append((ln, p))
            k = {"SalesOrderDetailID": ln["SalesOrderDetailID"]}
            link(doc, "标的物表 第%d项" % i, "名称", p["Name"], "Production_Product", "Name", {"ProductID": p["ProductID"]})
            link(doc, "标的物表 第%d项" % i, "规格型号", p["ProductNumber"], "Production_Product", "ProductNumber",
                 {"ProductID": p["ProductID"]})
            link(doc, "标的物表 第%d项" % i, "数量", ln["OrderQty"], "Sales_SalesOrderDetail", "OrderQty", k)
            link(doc, "标的物表 第%d项" % i, "单价（美元）", money(ln["UnitPrice"]), "Sales_SalesOrderDetail", "UnitPrice", k)
            link(doc, "标的物表 第%d项" % i, "金额（美元）", money(ln["LineTotal"]), "Sales_SalesOrderDetail", "LineTotal", k)
            link(doc, "标的物表 第%d项" % i, delivery_label, cdate(head["DueDate"]), "Sales_SalesOrderHeader", "DueDate",
                 {"SalesOrderID": soid})
        return head, rows

    common_sig = {}
    for side in "bskc":
        common_sig.update({f"sig_{side}_{f}": PH for f in ("code", "addr", "tel", "fax", "bank", "acct", "zip", "id")})
        common_sig.update({f"sig_{side}_rep": "（略）", f"sig_{side}_agent": "（略）"})
    nota = {"notary": "×", "notary_agent": "×", "notary_y": "×", "notary_m": "×", "notary_d": "×"}

    # ---- (a) MFG-ZH-01: BTHF-2023-0102 filled for sales order 57159
    d = "MFG-ZH-01"
    cust, store = store_of("29863")
    head, rows = order_items(d, "57159", "交货期限")
    sy, sm, sd = ymd(head["OrderDate"])
    link(d, "首页 买受人", "买受人", store["Name"], "Sales_Store", "Name", {"BusinessEntityID": cust["StoreID"]})
    link(d, "首页 买受人", "客户编号", cust["AccountNumber"], "Sales_Customer", "AccountNumber", {"CustomerID": "29863"})
    link(d, "首页", "签订日期", cdate(head["OrderDate"]), "Sales_SalesOrderHeader", "OrderDate", {"SalesOrderID": "57159"})
    link(d, "三、费用", "合同总额（美元）", money(head["SubTotal"]), "Sales_SalesOrderHeader", "SubTotal", {"SalesOrderID": "57159"})
    link(d, "九、交付", "交货时间", cdate(head["DueDate"]), "Sales_SalesOrderHeader", "DueDate", {"SalesOrderID": "57159"})
    link(d, "十八、其他约定事项", "买受人采购单号", head["PurchaseOrderNumber"], "Sales_SalesOrderHeader",
         "PurchaseOrderNumber", {"SalesOrderID": "57159"})
    for clause, label, val in [("十、检验", "检验期限（日）", "10"), ("十、检验", "异议通知期限（日）", "3"),
                               ("十五、质量保证期", "质量保证期（月）", "12"), ("十五、质量保证期", "服务响应（小时）", "48"),
                               ("十六、违约责任（一）1", "逾期交货违约金（合同总额每日‰）", "0.5"),
                               ("十六、违约责任（一）1", "买受人可解除合同的迟延天数", "15"),
                               ("十七、争议解决", "仲裁机构", "示例市（合成名称）仲裁委员会")]:
        link(d, clause, label, val, None, None, None)
    total = Decimal(head["SubTotal"])
    fills = dict(common_sig, contract_no="SC-ZH-2024-057159", buyer=f"{store['Name']}（样例客户，客户编号 {cust['AccountNumber']}）",
                 seller=SELLER + SELLER_NOTE, sign_y=sy, sign_m=sm, sign_d=sd, sign_place=CITY + "（合成地点）",
                 price_excl_tax=True, total_excl_tax=True, total_words="×", total_rmb="×",
                 total_note=f"合计美元 {money(head['SubTotal'])}（不含税，见第十八条）",
                 q1=True, quality_standard="符合出卖人现行产品目录所列规格，以标的物表规格型号为准", q2=False, tech_req="×",
                 q3=False, quality_other="×", f1=False, f1_who="×", f1_amt="×", f2=False, f2_who="×", f2_amt="×",
                 f3=False, f3_name="×", f3_who="×", f3_amt="×", contract_total=f"美元 {money(head['SubTotal'])}",
                 incl_vat=False, excl_vat=True, p1=True, pack_std="出卖人出厂标准包装", p2=True,
                 pack_supply="由出卖人提供，费用包含在单价内", p3=False, pack_return="×", accessories="×",
                 pay_once=True, pay_at_sign=False, pay_once_other="（验收合格后30日内）", pay_inst=False, inst_n="×",
                 inst_first_when="×", inst_first_amt="×", inst_rest_when="×", pay_adv=False, adv_amt="×", adv_rest="×",
                 adv_when="×", pay_dep=False, dep_amt="×", dep_rest="×", dep_when="×", invoice_days="15", loss_std="×",
                 self_pickup=False, seller_delivers=True, one_batch=True, batches=False, transport="公路货运",
                 delivery_time=f"{cdate(head['DueDate'])}前", delivery_place="买受人指定收货地点" + PH,
                 inspect_std="出卖人产品目录", inspect_days="10", notice_days="3", inspect_place="买受人收货地点",
                 appraisal_who="过错方", own1=True, own_when="×", own2=False, own_other="×",
                 risk="标的物交付买受人之前由出卖人承担，交付之后由买受人承担", install="×", install_days="×",
                 warranty_months="12", response_hours="48", late_rate="0.5", late_cancel_days="15", cancel_rate="50",
                 buyer_late_rate="0.5", buyer_late_days="30", inst_cure_days="×", pickup_rate="×", pickup_days="×",
                 court=False, court_name="×", arb=True, arb_name=CITY + "（合成名称）",
                 other_terms=f"本合同以美元（USD）计价，单价与总额均不含税；合计美元 {money(head['SubTotal'])}。"
                             f"本合同对应买受人采购单号 {head['PurchaseOrderNumber']}",
                 eff_sign=True, eff_date=False, eff_y="×", eff_m="×", eff_d="×", copies="二", buyer_copies="一",
                 seller_copies="一", sig_b_seal=store["Name"], sig_s_seal=SELLER,
                 items=[[str(i), p["Name"], "×", p["ProductNumber"], "出卖人", unit(p), ln["OrderQty"], money(ln["UnitPrice"]),
                         money(ln["LineTotal"]), cdate(head["DueDate"]), "×"] for i, (ln, p) in enumerate(rows, 1)])
    assert sum(Decimal(ln["LineTotal"]) for ln, _ in rows) == total
    docs.append(dict(id=d, kind="official", number="BTHF-2023-0102", template="BTHF-2023-0102.md", fills=fills,
                     title="京津冀地区工业品买卖合同（样例填写）", parties=[store["Name"], SELLER], rows=rows))
    a_rows, a_head = rows, head

    # ---- (b) MFG-ZH-02: GF-2000-0101 filled for sales order 50234
    d = "MFG-ZH-02"
    cust, store = store_of("29626")
    head, rows = order_items(d, "50234", "交（提）货时间")
    sy, sm, sd = ymd(head["OrderDate"])
    link(d, "首页 买受人", "买受人", store["Name"], "Sales_Store", "Name", {"BusinessEntityID": cust["StoreID"]})
    link(d, "首页 买受人", "客户编号", cust["AccountNumber"], "Sales_Customer", "AccountNumber", {"CustomerID": "29626"})
    link(d, "首页", "签订时间", cdate(head["OrderDate"]), "Sales_SalesOrderHeader", "OrderDate", {"SalesOrderID": "50234"})
    link(d, "第十八条", "合计金额（美元）", money(head["SubTotal"]), "Sales_SalesOrderHeader", "SubTotal", {"SalesOrderID": "50234"})
    for clause, label, val in [("第三条", "质量负责期限（月）", "6"), ("第十条", "检验期限（日）", "7"),
                               ("第十二条", "付款期限（日）", "30"), ("第十五条", "逾期交货违约金（每日‰）", "0.5")]:
        link(d, clause, label, val, None, None, None)
    fills = dict(common_sig, **nota, contract_no="SC-ZH-2024-050234", seller=SELLER + SELLER_NOTE, sign_place=CITY + "（合成地点）",
                 buyer=f"{store['Name']}（样例客户，客户编号 {cust['AccountNumber']}）", sign_y=sy, sign_m=sm, sign_d=sd,
                 total_rmb_words="×（本合同以美元计价，合计金额见第十八条）",
                 quality_standard="符合出卖人现行产品规格，以规格型号为准",
                 quality_period="自交货之日起6个月内，非因买受人使用不当造成的质量问题由出卖人负责修理或更换",
                 packaging="出卖人出厂标准包装，包装物不回收", accessories="×", loss_std="×", own_when="交付",
                 own_holder="出卖人", delivery_place="出卖人送货至买受人指定地点" + PH, transport="公路运输，运费由出卖人承担",
                 inspection="买受人于到货后7日内按规格型号和数量验收；逾期未提出书面异议的，视为验收合格", install="×",
                 payment="验收合格后30日内一次性电汇付清，以美元结算", guarantee="×",
                 termination="出卖人逾期交货超过15日的，买受人有权解除本合同",
                 liability="出卖人逾期交货的，每日按逾期交货部分货款的0.5‰支付违约金；买受人逾期付款的，每日按逾期部分价款的0.5‰支付违约金",
                 dispute_choice="一", arb_name=CITY + "（合成名称）", effective="双方签字盖章之日",
                 other_terms=f"本合同以美元（USD）计价，单价、金额均不含税；合计美元 {money(head['SubTotal'])}。"
                             f"本合同对应买受人采购单号 {head['PurchaseOrderNumber']}。",
                 sig_s_seal=SELLER, sig_b_seal=store["Name"],
                 items=[[p["Name"], "×", p["ProductNumber"], "出卖人", unit(p), ln["OrderQty"], money(ln["UnitPrice"]),
                         money(ln["LineTotal"]), f"{cdate(head['DueDate'])}前交 {ln['OrderQty']}"] for ln, p in rows])
    docs.append(dict(id=d, kind="official", number="GF-2000-0101", template="GF-2000-0101.md", fills=fills,
                     title="工业品买卖合同（GF—2000—0101 样例填写）", parties=[store["Name"], SELLER], rows=rows))

    # ---- (c) MFG-ZH-03: GF-2000-0301 processing contract, product 762 on sales order 50658 line 33540
    d = "MFG-ZH-03"
    cust, store = store_of("29781")
    head = db.one("Sales_SalesOrderHeader", SalesOrderID="50658")
    ln = db.one("Sales_SalesOrderDetail", SalesOrderDetailID="33540")
    p = db.one("Production_Product", ProductID=ln["ProductID"])
    k = {"ProductID": p["ProductID"]}
    sy, sm, sd = ymd(head["OrderDate"])
    target_days = str(int(p["DaysToManufacture"]) - 1)
    link(d, "首页 定制人", "定作人", store["Name"], "Sales_Store", "Name", {"BusinessEntityID": cust["StoreID"]})
    link(d, "第一条", "加工物名称", p["Name"], "Production_Product", "Name", k)
    link(d, "第一条", "规格型号", p["ProductNumber"], "Production_Product", "ProductNumber", k)
    link(d, "第一条", "数量", ln["OrderQty"], "Sales_SalesOrderDetail", "OrderQty", {"SalesOrderDetailID": "33540"})
    link(d, "第一条", "单价（美元）", money(ln["UnitPrice"]), "Sales_SalesOrderDetail", "UnitPrice", {"SalesOrderDetailID": "33540"})
    link(d, "第一条", "金额（美元）", money(ln["LineTotal"]), "Sales_SalesOrderDetail", "LineTotal", {"SalesOrderDetailID": "33540"})
    link(d, "第一条", "交货期限", cdate(head["DueDate"]), "Sales_SalesOrderHeader", "DueDate", {"SalesOrderID": "50658"})
    link(d, "第四条", "颜色", p["Color"], "Production_Product", "Color", k)
    link(d, "第四条", "车架尺寸（CM）", p["Size"], "Production_Product", "Size", k)
    link(d, "第四条", "整车重量（LB）", p["Weight"], "Production_Product", "Weight", k)
    link(d, "第四条", "生产周期上限（日）", target_days, "Production_Product", "DaysToManufacture", k, differs=True,
         reason="Contract build-time target is one day below the product record's DaysToManufacture; "
                "a promise, not the recorded manufacturing time.")
    for clause, label, val in [("第五条", "质量负责期限（月）", "12"), ("第十一条", "检验期限（日）", "7")]:
        link(d, clause, label, val, None, None, None)
    tech = (f"颜色 {p['Color']}；车架尺寸 {p['Size']} CM；整车重量 {p['Weight']} LB；产品线 {p['ProductLine'].strip()}"
            f"（公路车）；等级 {p['Class'].strip()}。承揽人承诺每台加工物的生产周期（自投产至完工）不超过{target_days}日")
    fills = dict(common_sig, **nota, contract_no="PC-ZH-2024-050658", customer=f"{store['Name']}（样例客户，客户编号 {cust['AccountNumber']}）",
                 contractor=SELLER + SELLER_NOTE, sign_place=CITY + "（合成地点）", sign_y=sy, sign_m=sm, sign_d=sd,
                 items=[[p["Name"], p["ProductNumber"], unit(p), ln["OrderQty"], money(ln["UnitPrice"]), money(ln["LineTotal"]),
                         f"{cdate(head['DueDate'])}前交付 {ln['OrderQty']}"]],
                 total_rmb_words="×（本合同以美元计价，合计金额见第十八条）", materials=[["×"] * 7],
                 material_check="×（定作人不提供材料，全部材料由承揽人自备）", tech_std=tech,
                 quality_period="自交付之日起12个月内，加工物不符合第四条技术标准的，承揽人负责免费修理或更换",
                 packaging="承揽人出厂标准包装，费用由承揽人承担",
                 drawings="定作人于签约时提供颜色及标识要求；承揽人对上述资料负有保密义务", objection_days="3", reply_days="3",
                 third_party="（本合同选择：否）", delivery="承揽人送货至定作人指定地点" + PH,
                 inspection="定作人于收货后7日内按第四条技术标准验收", payment="验收合格后30日内一次性付清，以美元结算",
                 deposit_y="×", deposit_m="×", deposit_d="×", deposit="×",
                 termination="承揽人逾期交付超过15日的，定作人有权解除本合同", lien="（本合同选择：是）",
                 liability="承揽人逾期交付的，每日按逾期部分报酬的0.5‰支付违约金；任一台加工物的生产周期超过第四条约定的，承揽人应书面说明原因",
                 dispute_choice="一", arb_name=CITY + "（合成名称）",
                 other_terms=f"本合同以美元（USD）计价，报酬不含税；合计美元 {money(ln['LineTotal'])}。"
                             f"本合同对应定作人采购单号 {head['PurchaseOrderNumber']}。",
                 sig_c_seal=store["Name"], sig_k_seal=SELLER)
    docs.append(dict(id=d, kind="official", number="GF-2000-0301", template="GF-2000-0301.md", fills=fills,
                     title="加工合同（GF—2000—0301 样例填写）", parties=[store["Name"], SELLER], rows=[(ln, p)]))

    # ---- (d) MFG-ZH-04: synthetic framework supply agreement, customer 29683
    d = "MFG-ZH-04"
    cust, store = store_of("29683")
    prods = [(db.one("Production_Product", ProductID="782"), "8"), (db.one("Production_Product", ProductID="867"), "10")]
    link(d, "首部 甲方", "甲方（买方）", store["Name"], "Sales_Store", "Name", {"BusinessEntityID": cust["StoreID"]})
    link(d, "首部 甲方", "客户编号", cust["AccountNumber"], "Sales_Customer", "AccountNumber", {"CustomerID": "29683"})
    for i, (p, mn) in enumerate(prods, 1):
        k = {"ProductID": p["ProductID"]}
        link(d, f"第二条 第{i}项", "产品名称", p["Name"], "Production_Product", "Name", k)
        link(d, f"第二条 第{i}项", "规格型号", p["ProductNumber"], "Production_Product", "ProductNumber", k)
        link(d, f"第二条 第{i}项", "协议单价（美元）", money(p["ListPrice"]), "Production_Product", "ListPrice", k)
        actual = db.value(None, None, {"CustomerID": "29683", "ProductID": p["ProductID"]}, "sum_2024_qty_by_customer_product")
        link(d, f"第二条 第{i}项", "2024年度最低采购量", mn, "Sales_SalesOrderDetail", "OrderQty",
             {"CustomerID": "29683", "ProductID": p["ProductID"], "OrderDate_year": "2024"}, "sum_2024_qty_by_customer_product",
             differs=Decimal(mn) != Decimal(actual),
             reason=("Minimum take is a commitment, not ordered quantity; 2024 orders total " + actual +
                     (", a shortfall of %d." % (int(mn) - int(actual)) if int(mn) > int(actual) else ", commitment met.")))
    for clause, label, val in [("第四条", "价格有效期", "2024年1月1日至2024年12月31日"), ("第五条", "解除通知期（日）", "90"),
                               ("第三条", "短缺补偿（协议单价的%）", "10"), ("第七条", "适用法律", "中华人民共和国法律")]:
        link(d, clause, label, val, None, None, None)
    docs.append(dict(id=d, kind="synthetic", title="产品采购框架协议（合成文件）", parties=[store["Name"], SELLER],
                     rows=[(None, p) for p, _ in prods], text=f"""# 产品采购框架协议
协议编号：{{{{no}}}}
甲方（买方）：{{{{buyer}}}}
乙方（卖方）：{{{{seller}}}}
签订日期：{{{{sign}}}}　签订地点：{{{{place}}}}
甲乙双方依据《中华人民共和国民法典》，在平等、自愿、公平和诚信的基础上，就甲方在协议期内向乙方采购下列产品，经友好协商订立本框架协议。
## 第一条 协议性质
本协议为框架协议。每批采购以甲方发出并经乙方书面确认的销售订单为准。订单与本协议不一致的，以本协议为准，但订单明确约定变更本协议的除外。
## 第二条 产品、协议单价及年度最低采购量
序号 | 产品名称 | 规格型号 | 计量单位 | 协议单价（美元，不含税） | 2024年度最低采购量
{{{{ROWS:items}}}}
## 第三条 最低采购量的计算
年度最低采购量按自然年计算，即自2024年1月1日起至2024年12月31日止。计算时以订单日期在该年度内的甲方销售订单的订购数量合计为准。退货、乙方自身的采购订单及生产工单均不计入。甲方年度实际采购量低于最低采购量的，就差额部分按协议单价的{{{{shortfall_pct}}}}%向乙方支付补偿金。
## 第四条 价格有效期
协议单价的有效期为{{{{price_valid}}}}。各订单可在协议单价基础上给予折扣，折扣以订单载明为准。有效期内乙方调整目录价的，应提前30日书面通知甲方，调整不影响已确认的订单。
## 第五条 协议期限与解除
本协议自2024年1月1日起生效，至2025年12月31日终止。任何一方可提前{{{{notice_days}}}}日书面通知对方解除本协议。解除前已确认的订单继续履行。
## 第六条 交付、检验与质量保证
交付、检验及质量保证期按双方就各订单另行签订的工业品买卖合同执行。
## 第七条 适用法律与争议解决
本协议适用{{{{law}}}}。因本协议发生的争议，双方应协商解决；协商不成的，提交示例市仲裁委员会（合成名称）仲裁。
## 第八条 其他
本协议一式二份，甲乙双方各执一份，具有同等法律效力。
甲方（盖章）：{{{{buyer_seal}}}}<br>地址：{PH}<br>电话：{PH}<br>授权代表：（略） | 乙方（盖章）：{{{{seller_seal}}}}<br>地址：{PH}<br>电话：{PH}<br>授权代表：（略）
""", fills=dict(no="FA-ZH-2024-029683", buyer=f"{store['Name']}（样例客户，客户编号 {cust['AccountNumber']}）",
                seller=SELLER + SELLER_NOTE, sign="2024年1月1日", place=CITY + "（合成地点）", shortfall_pct="10",
                price_valid="2024年1月1日至2024年12月31日", notice_days="90", law="中华人民共和国法律",
                buyer_seal=store["Name"], seller_seal=SELLER,
                items=[[str(i), p["Name"], p["ProductNumber"], unit(p), money(p["ListPrice"]), mn]
                       for i, (p, mn) in enumerate(prods, 1)])))

    # ---- (e) MFG-ZH-05: synthetic amendment to MFG-ZH-01
    d = "MFG-ZH-05"
    cust, store = store_of("29863")
    (l1, p1), (l3, p3) = a_rows[0], a_rows[2]
    new_qty = int(l1["OrderQty"]) + 1
    new_line = Decimal(l1["UnitPrice"]) * new_qty
    new_total = Decimal(a_head["SubTotal"]) + Decimal(l1["UnitPrice"])
    new_date = "2024-10-25"
    link(d, "首部", "原合同买受人", store["Name"], "Sales_Store", "Name", {"BusinessEntityID": cust["StoreID"]})
    link(d, "第一条", "原交货期限（第3项）", cdate(a_head["DueDate"]), "Sales_SalesOrderHeader", "DueDate", {"SalesOrderID": "57159"})
    link(d, "第一条", "变更后交货期限（第3项 " + p3["ProductNumber"] + "）", cdate(new_date), "Sales_SalesOrderHeader", "DueDate",
         {"SalesOrderID": "57159"}, differs=True,
         reason="The amendment changes the contractual delivery date; the operational DueDate stays 2024-10-11 "
                "(ShipDate 2024-10-06).")
    link(d, "第二条", "原数量（第1项 " + p1["ProductNumber"] + "）", l1["OrderQty"], "Sales_SalesOrderDetail", "OrderQty",
         {"SalesOrderDetailID": l1["SalesOrderDetailID"]})
    link(d, "第二条", "变更后数量（第1项 " + p1["ProductNumber"] + "）", str(new_qty), "Sales_SalesOrderDetail", "OrderQty",
         {"SalesOrderDetailID": l1["SalesOrderDetailID"]}, differs=True,
         reason="Amended contract quantity; the sales order line still records the original quantity.")
    link(d, "第二条", "单价（美元，不变）", money(l1["UnitPrice"]), "Sales_SalesOrderDetail", "UnitPrice",
         {"SalesOrderDetailID": l1["SalesOrderDetailID"]})
    link(d, "第二条", "变更后该项总额（美元）", money(str(new_line)), "Sales_SalesOrderDetail", "LineTotal",
         {"SalesOrderDetailID": l1["SalesOrderDetailID"]}, differs=True, reason="Follows from the amended quantity.")
    link(d, "第三条", "原合同总额（美元）", money(a_head["SubTotal"]), "Sales_SalesOrderHeader", "SubTotal", {"SalesOrderID": "57159"})
    link(d, "第三条", "变更后合同总额（美元）", money(str(new_total)), "Sales_SalesOrderHeader", "SubTotal", {"SalesOrderID": "57159"},
         differs=True, reason="Amended contract total; the order SubTotal is unchanged.")
    link(d, "第四条", "生效日期", "2024年10月1日", None, None, None)
    docs.append(dict(id=d, kind="synthetic", title="工业品买卖合同补充协议（第1号，合成文件）", parties=[store["Name"], SELLER],
                     rows=[(l1, p1), (None, p3)], text=f"""# 工业品买卖合同补充协议（第1号）
补充协议编号：{{{{no}}}}
原合同：京津冀地区工业品买卖合同，合同编号 SC-ZH-2024-057159，签订日期 {cdate(a_head['OrderDate'])}
买受人：{{{{buyer}}}}
出卖人：{{{{seller}}}}
鉴于买受人与出卖人已签订上述原合同，现双方经协商一致，对原合同作如下变更：
## 第一条 交货期限变更
原合同“一、标的物”第3项 {p3['Name']}（规格型号 {p3['ProductNumber']}）的交货期限由{{{{old_date}}}}变更为{{{{new_date}}}}。原合同其余各项的交货期限不变。
## 第二条 数量变更
原合同“一、标的物”第1项 {p1['Name']}（规格型号 {p1['ProductNumber']}）的数量由{{{{old_qty}}}}台增加至{{{{new_qty}}}}台，单价{{{{price}}}}美元不变。该项总额由{money(l1['LineTotal'])}美元变更为{{{{new_line}}}}美元。
## 第三条 合同总额变更
原合同“三、费用”所列合同总额由美元{{{{old_total}}}}元变更为美元{{{{new_total}}}}元（不含增值税）。
## 第四条 生效及效力
本补充协议自{{{{eff}}}}起生效。本补充协议是原合同不可分割的组成部分。本补充协议未变更的内容，仍按原合同执行；两者不一致的，以本补充协议为准。
## 第五条 其他
本补充协议一式二份，买受人、出卖人各执一份，具有同等法律效力。
买受人（盖章/签名）：{{{{buyer_seal}}}}<br>委托代表人：（略）<br>日期：{{{{eff}}}} | 出卖人（盖章/签名）：{{{{seller_seal}}}}<br>委托代表人：（略）<br>日期：{{{{eff}}}}
""", fills=dict(no="SC-ZH-2024-057159-A1", buyer=f"{store['Name']}（样例客户，客户编号 {cust['AccountNumber']}）",
                seller=SELLER + SELLER_NOTE, old_date=cdate(a_head["DueDate"]), new_date=cdate(new_date),
                old_qty=l1["OrderQty"], new_qty=str(new_qty), price=money(l1["UnitPrice"]), new_line=money(str(new_line)),
                old_total=money(a_head["SubTotal"]), new_total=money(str(new_total)), eff="2024年10月1日",
                buyer_seal=store["Name"], seller_seal=SELLER)))

    # ---- (f) MFG-ZH-06: synthetic quality and acceptance agreement with vendor 1538
    d = "MFG-ZH-06"
    vendor = db.one("Purchasing_Vendor", BusinessEntityID="1538")
    parts = [db.one("Production_Product", ProductID=x) for x in ("928", "929")]
    pv = [db.one("Purchasing_ProductVendor", ProductID=p["ProductID"], BusinessEntityID="1538") for p in parts]
    link(d, "首部 乙方", "供应方", vendor["Name"], "Purchasing_Vendor", "Name", {"BusinessEntityID": "1538"})
    link(d, "首部 乙方", "供应商编号", vendor["AccountNumber"], "Purchasing_Vendor", "AccountNumber", {"BusinessEntityID": "1538"})
    for i, (p, v) in enumerate(zip(parts, pv), 1):
        k = {"ProductID": p["ProductID"]}
        link(d, f"第一条 第{i}项", "零部件名称", p["Name"], "Production_Product", "Name", k)
        link(d, f"第一条 第{i}项", "规格型号", p["ProductNumber"], "Production_Product", "ProductNumber", k)
        link(d, f"第一条 第{i}项", "标准交货周期（日）", v["AverageLeadTime"], "Purchasing_ProductVendor", "AverageLeadTime",
             {"ProductID": p["ProductID"], "BusinessEntityID": "1538"})
    link(d, "第四条", "年度不合格率上限", "2.00%", "Purchasing_PurchaseOrderDetail", "RejectedQty/ReceivedQty",
         {"VendorID": "1538", "OrderDate_year": "2024"}, "reject_rate_2024_by_vendor", differs=True,
         reason="Threshold versus the recorded 2024 rejection rate (sum RejectedQty / sum ReceivedQty over the vendor's "
                "2024 purchase orders); the vendor exceeded the threshold.")
    for clause, label, val in [("第三条", "到货检验期限（日）", "5"), ("第三条", "性能抽检时的检验期限（日）", "10"),
                               ("第五条", "取回不合格货物期限（日）", "10"), ("第五条", "补交合格货物期限（日）", "15"),
                               ("第六条", "质量保证期（月）", "12")]:
        link(d, clause, label, val, None, None, None)
    docs.append(dict(id=d, kind="synthetic", title="质量保证及验收协议（合成文件）", parties=[vendor["Name"], SELLER],
                     rows=[(None, p) for p in parts], text=f"""# 质量保证及验收协议
协议编号：{{{{no}}}}
甲方（采购方）：{{{{buyer}}}}
乙方（供应方）：{{{{vendor}}}}
签订日期：{{{{sign}}}}　签订地点：{{{{place}}}}
为明确乙方向甲方供应零部件的质量要求、验收程序和不合格品处理，双方依据《中华人民共和国民法典》等法律规定，经协商一致，订立本协议。
## 第一条 适用范围
本协议适用于乙方向甲方供应的下列零部件，以及甲方据此发出的全部采购订单。
序号 | 零部件名称 | 规格型号 | 计量单位 | 标准交货周期（日）
{{{{ROWS:items}}}}
## 第二条 质量要求
乙方供应的零部件应符合乙方技术规格及双方确认的样品，并符合国家相关强制性标准。
## 第三条 到货检验
甲方应在每批货物到达甲方仓库后{{{{inspect_days}}}}日内完成数量清点及外观、规格检验。需进行性能抽检的，检验期限延长至{{{{perf_days}}}}日。甲方在检验期限内未提出书面异议的，视为该批货物验收合格。
## 第四条 不合格率
不合格率按自然年计算，等于该年度甲方拒收数量之和除以甲方实收数量之和。乙方年度不合格率不得超过{{{{threshold}}}}。超过的，乙方应在接到甲方书面通知后30日内提交纠正和预防措施报告；连续两个年度超过的，甲方有权暂停向乙方下达新的采购订单。
## 第五条 退货处理
经检验不合格的货物，甲方应在检验期限届满前书面通知乙方并说明理由。乙方应在收到通知后{{{{pickup_days}}}}日内自费取回不合格货物，并在{{{{replace_days}}}}日内补交合格货物，或按甲方选择退还相应货款。退回货物的运输费用由乙方承担。
## 第六条 质量保证期
零部件的质量保证期为自验收合格之日起{{{{warranty}}}}个月。
## 第七条 违约责任
乙方逾期补交合格货物的，每逾期一日，按该批不合格货物货款的1‰向甲方支付违约金。
## 第八条 协议期限、适用法律与争议解决
本协议自2024年1月1日起生效，至2025年12月31日终止。本协议适用中华人民共和国法律。因本协议发生的争议，双方协商解决；协商不成的，提交示例市仲裁委员会（合成名称）仲裁。
甲方（盖章）：{{{{buyer_seal}}}}<br>地址：{PH}<br>授权代表：（略） | 乙方（盖章）：{{{{vendor_seal}}}}<br>地址：{PH}<br>授权代表：（略）
""", fills=dict(no="QA-ZH-2024-1538", buyer=SELLER + SELLER_NOTE,
                vendor=f"{vendor['Name']}（样例供应商，供应商编号 {vendor['AccountNumber']}）", sign="2024年1月1日",
                place=CITY + "（合成地点）", inspect_days="5", perf_days="10", threshold="2.00%", pickup_days="10",
                replace_days="15", warranty="12", buyer_seal=SELLER, vendor_seal=vendor["Name"],
                items=[[str(i), p["Name"], p["ProductNumber"], "条", v["AverageLeadTime"]]
                       for i, (p, v) in enumerate(zip(parts, pv), 1)])))
    return docs, links


# ---------------------------------------------------------------- rendering
PLACE = re.compile(r"\{\{(cb:|ROWS:)?(\w+)\}\}")


def template_body(text):
    return [ln for ln in text.splitlines() if not ln.startswith("> ")]


def fill_line(line, fills, used):
    def sub(m):
        kind, name = m.group(1), m.group(2)
        if name not in fills:
            raise SystemExit(f"unfilled blank {name}")
        used.add(name)
        if kind == "cb:":
            return "\x00☑\x01" if fills[name] else "\x00☒\x01"
        return "\x02" + str(fills[name]) + "\x03"
    out = html.escape(PLACE.sub(sub, line), quote=False).replace("&lt;br&gt;", "<br>")
    return (out.replace("\x00", "<b>").replace("\x01", "</b>")
            .replace("\x02", '<span class="f">').replace("\x03", "</span>"))


def to_html(lines, fills):
    used, out, table, width = set(), [], [], [0]

    def cell(text):
        return f'<td class="f">{html.escape(text)}</td>'

    def flush():
        if table:
            out.append("<table>" + "".join(table) + "</table>")
            table.clear()
    for raw in lines:
        if not raw.strip() or raw.strip() == "<!-- omitted -->":
            continue
        m = re.fullmatch(r"\{\{ROWS:(\w+)\}\}", raw.strip())
        if m:
            used.add(m[1])
            for r in fills[m[1]]:
                table.append("<tr>" + "".join(cell(str(c)) for c in r) + "</tr>")
            continue
        if "|" in raw and not raw.startswith("#"):
            cells = [fill_line(c.strip(), fills, used) for c in raw.split("|")]
            if len(cells) != width[0]:  # MuPDF has no colspan: a row of another width starts its own table
                flush()
                width[0] = len(cells)
            table.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
            continue
        flush()
        if raw.startswith("# "):
            out.append(f"<h1>{fill_line(raw[2:], fills, used)}</h1>")
        elif raw.startswith("## "):
            out.append(f"<h2>{fill_line(raw[3:], fills, used)}</h2>")
        else:
            out.append(f"<p>{fill_line(raw, fills, used)}</p>")
    flush()
    missing = set(k for k in fills if not k.startswith("sig_") and not k.startswith("notary")) - used
    if missing:
        raise SystemExit(f"fills not used: {sorted(missing)}")
    return "\n".join(out)


CSS = FONT_CSS + """
* {font-family: au;}
body {font-size: 10pt; line-height: 1.45;}
h1 {font-size: 17pt; text-align: center; margin: 6pt 0;}
h2 {font-size: 11.5pt; margin: 7pt 0 2pt 0;}
p {margin: 2pt 0;}
table {border-collapse: collapse; width: 100%; margin: 3pt 0;}
td {border: 0.6pt solid #444; padding: 1.5pt 2pt; font-size: 7.5pt; vertical-align: top;}
.f {text-decoration: underline; color: #10306a;}
b {font-size: 11pt;}
.banner {border: 1pt solid #a33; background-color: #fff3f0; padding: 4pt; font-size: 8.5pt; margin-bottom: 6pt;}
"""


def labels(doc):
    if doc["kind"] == "official":
        zh, en = BODIES[doc["number"]]
        return (f"样例演示数据。本文件依据{zh}制定的合同示范文本 {doc['number']} 填写，所填内容均为样例值，并非真实合同。",
                f"Sample data for demonstration. Based on the official model text {doc['number']}, issued by {en}; "
                f"filled with sample values. Not a real contract.")
    return ("样例演示数据。合成文件，并非真实合同或官方示范文本。",
            "Sample data for demonstration. Synthetic document; not a real contract and not an official model text.")


def render(doc, archive):
    lines = template_body((OFFICIAL / doc["template"]).read_text(encoding="utf-8")) if doc["kind"] == "official" \
        else doc["text"].splitlines()
    zh, en = labels(doc)
    banner = (f'<div class="banner"><p>{html.escape(zh)}</p><p>{html.escape(en)}</p>'
              f"<p>当事人、产品与订单取自 AdventureWorks 样例数据（Microsoft，MIT 许可，虚构公司）；“{SELLER}”及“{CITY}”为合成名称；"
              f"地址、电话、证照号码与账号均为占位符。文件编号 {doc['id']}。</p></div>")
    body = banner + to_html(lines, doc["fills"])
    story = fitz.Story(html=f"<body>{body}</body>", user_css=CSS, archive=archive)
    buf = io.BytesIO()
    writer = fitz.DocumentWriter(buf)
    page, where, more = fitz.paper_rect("a4"), fitz.paper_rect("a4") + (42, 46, -42, -64), 1
    while more:
        dev = writer.begin_page(page)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
    writer.close()
    pdf = fitz.open("pdf", buf.getvalue())
    short = ("依据官方示范文本 %s 填写样例值 / based on official model text %s, filled with sample values"
             % (doc["number"], doc["number"])) if doc["kind"] == "official" else "合成文件 / synthetic document"
    n = pdf.page_count
    for i, pg in enumerate(pdf, 1):
        pg.insert_htmlbox(fitz.Rect(54, page.height - 52, page.width - 54, page.height - 18),
                          f"<p>样例演示数据 Sample data for demonstration · {short} · {doc['id']} · {i}/{n}</p>",
                          css=FONT_CSS + "* {font-family: au; font-size: 7pt; color: #666; text-align: center;}",
                          archive=archive)
    pdf.subset_fonts()
    pdf.set_metadata({"title": doc["title"], "author": "synthetic demo generator", "subject": doc["id"],
                      "creator": "build_zh_documents.py", "producer": "PyMuPDF", "creationDate": "", "modDate": ""})
    return pdf.tobytes(garbage=4, deflate=True, no_new_id=True), n


def build(db):
    docs, links = build_docs(db)
    archive = fitz.Archive(FONT_DIR)
    out = []
    for doc in docs:
        data, pages = render(doc, archive)
        (HERE / f"{doc['id']}.pdf").write_bytes(data)
        out.append((doc["id"], pages, len(data), hashlib.sha256(data).hexdigest()))
    (HERE / "links.json").write_text(json.dumps({"note": "Answer key: values stated in documents_zh PDFs and their "
                                                  "AdventureWorks table counterparts. table=null means no table counterpart.",
                                                  "links": links}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for row in out:
        print("%s.pdf pages=%d bytes=%d sha256=%s" % row)
    return docs


# ---------------------------------------------------------------- checks
def squash(s):
    return re.sub(r"[\s|#_]|<br>", "", s)


def docx_lines(path):
    import docx
    from docx.oxml.ns import qn
    out = []
    for el in docx.Document(path).element.body.iterchildren():
        if el.tag == qn("w:p"):
            out.append("".join(t.text or "" for t in el.iter(qn("w:t"))))
        elif el.tag == qn("w:tbl"):
            for tr in el.iter(qn("w:tr")):
                out.append(" | ".join("".join(t.text or "" for t in tc.iter(qn("w:t"))) for tc in tr.iter(qn("w:tc"))))
    return out


SOURCE_DOCX = {"BTHF-2023-0102.md": "samr_bthf_industrial_goods_sale_2023.docx",
               "GF-2000-0101.md": "samr_industrial_goods_sale_2000.docx",
               "GF-2000-0301.md": "samr_processing_2000.docx"}


def check(db, source_dir):
    docs, links = build_docs(db)
    fails = 0
    stored = json.loads((HERE / "links.json").read_text(encoding="utf-8"))["links"]
    if stored != links:
        print("FAIL links.json differs from a fresh computation"); fails += 1
    eq = df = 0
    for ln in links:
        if ln["table"] is None:
            continue
        fresh = db.value(ln["table"], ln["column"], ln["key"], ln["aggregate"])
        same = norm(ln["value_as_written"]) == norm(fresh)
        if ln["differs"] == same or fresh != ln["table_value"]:
            print("FAIL link", ln["document"], ln["label_zh"], ln["value_as_written"], fresh); fails += 1
        eq += not ln["differs"]; df += bool(ln["differs"])
    print(f"links: {eq} equal-to-table verified, {df} deliberate differences verified, "
          f"{sum(ln['table'] is None for ln in links)} document-only values")
    for doc in docs:
        pdf = HERE / f"{doc['id']}.pdf"
        text = squash(subprocess.run(["pdftotext", "-enc", "UTF-8", str(pdf), "-"], capture_output=True, text=True,
                                     check=True).stdout)
        needles = list(doc["parties"]) + [p["ProductNumber"] for _, p in doc["rows"]] + \
            [money(ln["LineTotal"]) for ln, _ in doc["rows"] if ln] + \
            [ln["value_as_written"] for ln in links if ln["document"] == doc["id"]] + ["样例演示数据", "Sampledatafordemonstration"]
        miss = [n for n in needles if squash(n) not in text]
        print(f"{doc['id']}.pdf pdftotext: {len(needles) - len(miss)}/{len(needles)} expected strings found"
              + (f"; MISSING {miss}" if miss else ""))
        fails += bool(miss)
    if source_dir:
        for md, src in SOURCE_DOCX.items():
            body = "\n".join(template_body((OFFICIAL / md).read_text(encoding="utf-8")))
            source = squash("".join(docx_lines(Path(source_dir) / src)))
            blocks = body.split("<!-- omitted -->")
            ok = 0
            for b in blocks:
                form = squash(re.sub(r"\{\{ROWS:\w+\}\}", "", re.sub(r"\{\{cb:\w+\}\}", "□", re.sub(r"\{\{\w+\}\}", "", b))))
                if form in source:
                    ok += 1
                else:
                    i = next(i for i in range(len(form)) if form[:i + 1] not in source)
                    print(f"FAIL {md}: wording diverges near …{form[max(0, i - 15):i + 15]}…"); fails += 1
            print(f"{md}: {ok}/{len(blocks)} kept blocks identical to {src} outside filled blanks "
                  f"({len(squash(body))} chars compared)")
    print("CHECK", "PASSED" if not fails else f"FAILED ({fails})")
    return fails


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--source-dir")
    a = ap.parse_args()
    data = Data(CSV_DIR)
    sys.exit(1 if check(data, a.source_dir) else 0) if a.check else build(data)
