#!/usr/bin/env python3
"""Create labelled synthetic contracts anchored to real keys in the sample projection."""
from pathlib import Path
import argparse, sqlite3, json, datetime, zipfile, hashlib
from xml.sax.saxutils import escape
ROOT=Path(__file__).resolve().parents[1]
LABEL='SYNTHETIC DEMO DOCUMENT — NOT A REAL CONTRACT'
SCOPE='adventureworks_demo'

def row(conn,sql,params=()):
    x=conn.execute(sql,params).fetchone()
    if x is None: raise ValueError('No suitable source row for the demo; no fallback keys will be invented.')
    return dict(x)

def date(value): return str(value)[:10]
def plus(value,days): return (datetime.date.fromisoformat(date(value))+datetime.timedelta(days=days)).isoformat()

def docx(text,path):
    """Minimal OOXML document; same text as Markdown alternative, no macro content."""
    paragraphs=[]
    for line in text.splitlines():
        s=line.lstrip('# ').strip()
        paragraphs.append('<w:p><w:r><w:t xml:space="preserve">'+escape(s)+'</w:t></w:r></w:p>')
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml','<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr('_rels/.rels','<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr('word/document.xml','<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'+''.join(paragraphs)+'<w:sectPr/></w:body></w:document>')

def prepare(root):
    conn=sqlite3.connect(f'file:{root / "data/adventureworks.sqlite"}?mode=ro',uri=True); conn.row_factory=sqlite3.Row
    dates=conn.execute('SELECT MIN(OrderDate),MAX(OrderDate) FROM Sales_SalesOrderHeader').fetchone()
    year=int(str(dates[1])[:4])-1
    if year<=int(str(dates[0])[:4]): raise ValueError('No complete interior calendar year available')
    start=f'{year}-01-01'; end=f'{year+1}-01-01'; finish=f'{year}-12-31'
    hero=row(conn,'''SELECT c.CustomerID,c.StoreID,s.Name AS CustomerName,p.ProductID,p.ProductNumber,p.Name AS ProductName,
       SUM(d.OrderQty) AS AnnualOrderedUnits,COUNT(DISTINCT h.SalesOrderID) AS OrderCount
       FROM Sales_Customer c JOIN Sales_Store s ON s.BusinessEntityID=c.StoreID
       JOIN Sales_SalesOrderHeader h ON h.CustomerID=c.CustomerID
       JOIN Sales_SalesOrderDetail d ON d.SalesOrderID=h.SalesOrderID
       JOIN Production_Product p ON p.ProductID=d.ProductID
       WHERE h.OrderDate >= ? AND h.OrderDate < ?
       AND p.ProductSubcategoryID IN (SELECT ProductSubcategoryID FROM Production_ProductSubcategory WHERE ProductCategoryID=1)
       GROUP BY c.CustomerID,c.StoreID,s.Name,p.ProductID,p.ProductNumber,p.Name
       HAVING COUNT(DISTINCT h.SalesOrderID)>=2 ORDER BY AnnualOrderedUnits DESC,c.CustomerID,p.ProductID LIMIT 1''',(start,end))
    order=row(conn,'''SELECT h.SalesOrderID,h.OrderDate,h.DueDate,h.ShipDate,d.SalesOrderDetailID,d.OrderQty
        FROM Sales_SalesOrderHeader h JOIN Sales_SalesOrderDetail d ON d.SalesOrderID=h.SalesOrderID
        WHERE h.CustomerID=? AND d.ProductID=? AND h.OrderDate >= ? AND h.OrderDate < ?
        ORDER BY h.OrderDate,h.SalesOrderID,d.SalesOrderDetailID LIMIT 1''',(hero['CustomerID'],hero['ProductID'],start,end))
    other=row(conn,'''SELECT c.CustomerID,c.StoreID,s.Name AS CustomerName,SUM(d.OrderQty) AS AnnualOrderedUnits
       FROM Sales_Customer c JOIN Sales_Store s ON s.BusinessEntityID=c.StoreID
       JOIN Sales_SalesOrderHeader h ON h.CustomerID=c.CustomerID
       JOIN Sales_SalesOrderDetail d ON d.SalesOrderID=h.SalesOrderID
       WHERE c.CustomerID<>? AND d.ProductID=? AND h.OrderDate >= ? AND h.OrderDate < ?
       GROUP BY c.CustomerID,c.StoreID,s.Name ORDER BY AnnualOrderedUnits DESC,c.CustomerID LIMIT 1''',(hero['CustomerID'],hero['ProductID'],start,end))
    vendor=row(conn,'''SELECT v.BusinessEntityID AS VendorID,v.Name AS VendorName,p.ProductID,p.ProductNumber,p.Name AS ProductName,
       pv.AverageLeadTime,pv.MinOrderQty,pv.MaxOrderQty,pv.UnitMeasureCode
       FROM Purchasing_ProductVendor pv JOIN Purchasing_Vendor v ON v.BusinessEntityID=pv.BusinessEntityID
       JOIN Production_Product p ON p.ProductID=pv.ProductID
       ORDER BY pv.AverageLeadTime DESC,v.BusinessEntityID,p.ProductID LIMIT 1''')
    commitment=int(hero['AnnualOrderedUnits'])+20
    original=date(order['DueDate']); changed=plus(original,7); effective=plus(order['OrderDate'],1)
    context={'scope':SCOPE,'sample_period':[start,end],'annual_comparison_as_of':finish,'hero':hero,'order':order,'second_customer':other,'supplier':vendor,'authored_terms':{'minimum_commitment':commitment,'original_delivery_promise':original,'amended_delivery_promise':changed,'amendment_effective':effective,'supplier_promised_days':14},'warning':'All contract terms are newly authored synthetic fixtures. Operational records come from the Microsoft sample, not real customers.'}
    docs=[]; cases=[]
    def add(id,title,entity,sections,valid_from,revision=1,supersedes=None):
        lines=[f'# {title}','',LABEL,'','This document was authored for the V2 manufacturing demonstration. It is not an original Microsoft contract.','AdventureWorks identifiers refer only to the fictional sample dataset.',f'Source namespace: {SCOPE}',f'Document ID: {id}',f'Revision: {revision}',f'Effective date: {valid_from}',f'Entity scope: {entity}','','## 1. Parties and scope']
        for heading,body in sections:
            lines += ['',f'## {heading}',body]
        lines += ['','## Demonstration boundary','Operational rows do not prove acceptance, delivery, breach, or legal enforceability. Validate each conclusion against its own evidence.','']
        text='\n'.join(lines)
        path=root/'documents/markdown'/f'{id}.md'; path.parent.mkdir(parents=True,exist_ok=True); path.write_text(text)
        alt=root/'documents/docx'/f'{id}.docx'; alt.parent.mkdir(parents=True,exist_ok=True); docx(text,alt)
        record={'id':id,'scope':SCOPE,'entity_scope':entity,'synthetic':True,'revision':revision,'effective_from':valid_from,'supersedes':supersedes,'primary_path':str(path.relative_to(root)),'alternative_path':str(alt.relative_to(root)),'ingestion_rule':'Choose one representation per document ID; do not ingest both as separate sources.','sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        docs.append(record)
        return text
    def case(id,doc,quote,concept,target,relation,why):
        path=root/'documents/markdown'/f'{doc}.md'; text=path.read_text(); start=text.index(quote)
        cases.append({'id':id,'document_id':doc,'path':str(path.relative_to(root)),'quote':quote,'locator':{'kind':'unicode_codepoint_range','end_exclusive':True,'start':start,'end':start+len(quote)},'concept':concept,'target_definition':target,'expected_relation':relation,'rationale':why,'label_origin':'authored_demo_expectation','human_adjudication':'pending'})
    h=hero; o=order; v=vendor
    scope=f'customer:{h["CustomerID"]}'
    identity=f'Customer {h["CustomerName"]} uses AdventureWorks CustomerID {h["CustomerID"]} and StoreID {h["StoreID"]}.'
    product=f'The covered product is {h["ProductName"]}, ProductID {h["ProductID"]}, ProductNumber {h["ProductNumber"]}, in namespace {SCOPE}.'
    obligation=f'The customer commits to order at least {commitment} units of ProductNumber {h["ProductNumber"]} from {start} through {finish}.'
    add('MFG-001','Customer framework purchase agreement',scope,[('1.1 Account',identity),('1.2 Product',product),('2. Minimum purchase commitment',obligation),('3. Measurement','Count quantities on sales orders dated within the stated period. Do not count purchase orders, production work orders, returns without explicit rules, or another customer account.'),('4. Governing law','For this synthetic fixture, the agreement names the laws of Washington State. This clause has no asserted counterpart in the operational schema.')],start)
    case('G01','MFG-001',identity,'customer_identifier','Sales.Customer.CustomerID','identifier_binding','Explicit source namespace and account key match the sample.')
    case('G02','MFG-001',product,'product_identifier','Production.Product.ProductNumber','identifier_binding','Verify ProductID and ProductNumber together within the namespace.')
    case('G03','MFG-001',obligation,'purchase_commitment','Sales.SalesOrderDetail.OrderQty','related_context','Commitment is an obligation, not the actual ordered quantity.')
    due=f'The synthetic promised delivery date for SalesOrderID {o["SalesOrderID"]} is {original}.'
    add('MFG-002','Product order schedule',scope,[('1.1 Account',identity),('1.2 Product',product),('2. Order reference',f'This schedule references SalesOrderID {o["SalesOrderID"]}, line {o["SalesOrderDetailID"]}, with {o["OrderQty"]} units. The source order date is {date(o["OrderDate"])}.'),('3. Delivery promise',due),('4. Evidence limit','The order DueDate provides operational context only. A ShipDate is a shipment record and does not confirm delivery.')],date(o['OrderDate']))
    case('G04','MFG-002',due,'delivery_promise','Sales.SalesOrderHeader.DueDate','related_context','A documented promise and operational due date have separate provenance.')
    amend=f'Effective {effective}, the promised delivery date for SalesOrderID {o["SalesOrderID"]} changes from {original} to {changed}.'
    add('MFG-003','Delivery amendment to MFG-002',scope,[('1.1 Account',identity),('2. Amendment',amend),('3. Retained history','This amendment replaces only the delivery-promise clause in MFG-002. Keep the original clause for historical questions. This document does not update the operational order DueDate or ShipDate.')],effective,revision=2,supersedes='MFG-002:delivery_promise')
    case('G05','MFG-003',amend,'delivery_promise','Sales.SalesOrderHeader.DueDate','related_context','Resolve effective version for the question date; never overwrite source facts.')
    quality='The customer will inspect the supplied goods within ten calendar days after documented receipt. Written acceptance is required.'
    add('MFG-004','Quality and acceptance agreement',scope,[('1.1 Account',identity),('1.2 Product',product),('2. Acceptance',quality),('3. Production evidence','Production scrap records may support investigation. They do not prove that this customer received defective goods or accepted a delivery.')],start)
    case('G06','MFG-004',quality,'inspection_acceptance',None,'unmapped','No inspected operational field establishes customer acceptance.')
    warranty='The synthetic warranty lasts 24 months from documented customer acceptance. The start date remains unknown until acceptance evidence exists.'
    add('MFG-005','Warranty addendum',scope,[('1.1 Account',identity),('1.2 Product',product),('2. Warranty duration',warranty),('3. Limits','Misuse and unauthorized modifications are excluded in this illustrative clause. The operational sample has no verified warranty-duration field.')],start)
    case('G07','MFG-005',warranty,'warranty_duration',None,'unmapped','A useful concept can remain unmapped without fabricating a schema field.')
    supplier=f'Supplier {v["VendorName"]} uses AdventureWorks Vendor BusinessEntityID {v["VendorID"]}.'
    lead=f'The supplier promises receipt within 14 calendar days after purchase-order acceptance for ProductNumber {v["ProductNumber"]} (ProductID {v["ProductID"]}).'
    add('MFG-006','Supplier lead-time agreement',f'vendor:{v["VendorID"]}',[('1.1 Supplier',supplier),('1.2 Scope',f'This agreement covers {v["ProductName"]} only. It does not assert that this purchased item is the customer sales product in MFG-001.'),('2. Lead-time promise',lead),('3. Operational comparison','Compare this promise with the source AverageLeadTime only as context. That field describes placement-to-receipt average time; this clause starts at acceptance. The two start events differ.')],start)
    case('G08','MFG-006',supplier,'supplier_identifier','Purchasing.Vendor.BusinessEntityID','identifier_binding','Vendor key binding is separate from a customer account binding.')
    case('G09','MFG-006',lead,'supplier_lead_time_promise','Purchasing.ProductVendor.AverageLeadTime','related_context','Promise versus average, plus different start events; exact equivalence is forbidden.')
    othertext=f'Customer {other["CustomerName"]}, CustomerID {other["CustomerID"]}, has a separate illustrative commitment of 40 units of ProductNumber {h["ProductNumber"]} during calendar year {year}.'
    add('MFG-007','Second customer purchase agreement',f'customer:{other["CustomerID"]}',[('1.1 Separate account',othertext),('2. Isolation','The product concept is shared with MFG-001. This customer agreement and ordered quantities remain scoped to this account. Do not apply MFG-001 obligations to this account.')],start)
    case('G10','MFG-007',othertext,'purchase_commitment','Sales.SalesOrderDetail.OrderQty','related_context','Same concept and product, separate account evidence and quantity filter.')
    # Hard negatives are separate evaluation records, not plausible positive mappings.
    negatives=[
      {'id':'N01','concept':'purchase_commitment','target':'Sales.SalesOrderDetail.OrderQty','proposed_relation':'exact','expected':'reject','reason':'Obligation is not an operational quantity.'},
      {'id':'N02','concept':'supplier_lead_time_promise','target':'Purchasing.ProductVendor.AverageLeadTime','proposed_relation':'exact','expected':'reject','reason':'Promise differs from average and start events differ.'},
      {'id':'N03','concept':'delivery_promise','target':'Sales.SalesOrderHeader.ShipDate','proposed_relation':'exact','expected':'reject','reason':'Shipment is not delivery.'},
      {'id':'N04','concept':'warranty_duration','target':'Production.Product.SellEndDate','proposed_relation':'exact','expected':'reject','reason':'Sales availability is not warranty duration.'},
      {'id':'N05','concept':'product_identifier','input':{'namespace':'unrelated_supplier','product_number':h['ProductNumber']},'proposed_entity':h['ProductID'],'expected':'unresolved','reason':'Matching text in another namespace does not establish product identity.'},
      {'id':'N06','document':'MFG-007','requested_customer':h['CustomerID'],'expected':'exclude_evidence','reason':'Different customer evidence cannot satisfy the selected customer question.'},
      {'id':'N07','document':'MFG-003','as_of':date(o['OrderDate']),'expected':'use_base_promise','reason':'Amendment is not effective yet.'},
      {'id':'N08','concept':'minimum_supplier_order','target':'Purchasing.ProductVendor.MinOrderQty','expected':'require_domain_review','reason':'Source description contradicts column name.'},
    ]
    sql='''SELECT SUM(d.OrderQty) AS ordered_units FROM Sales_SalesOrderHeader h JOIN Sales_SalesOrderDetail d ON d.SalesOrderID=h.SalesOrderID WHERE h.CustomerID=? AND d.ProductID=? AND h.OrderDate>=? AND h.OrderDate<?'''
    params=[h['CustomerID'],h['ProductID'],start,end]
    actual=int(row(conn,sql,params)['ordered_units'])
    expected=[{'id':'Q1','question':'How do the selected customer orders compare with the illustrative annual commitment?','sql':sql,'parameters':params,'expected_sql_result':{'ordered_units':actual},'document':'MFG-001','authored_commitment':commitment,'computed_gap_units':commitment-actual,'interpretation':'Comparison of this sample snapshot only; no legal breach or fulfillment claim.'},
      {'id':'Q2','question':'What delivery promise applies before and after the amendment?','evidence':['MFG-002','MFG-003'],'before':{'as_of':date(o['OrderDate']),'promise':original},'after':{'as_of':effective,'promise':changed},'sql_due_date_stays':original},
      {'id':'Q3','question':'Is the supplier promise the same as the recorded average lead time?','sql':'SELECT AverageLeadTime FROM Purchasing_ProductVendor WHERE BusinessEntityID=? AND ProductID=?','parameters':[v['VendorID'],v['ProductID']],'expected_sql_result':{'AverageLeadTime':v['AverageLeadTime']},'document':'MFG-006','authored_promise_days':14,'expected_relation':'related_context','interpretation':'Different meaning and start event; do not label performance breach.'},
      {'id':'Q4','question':'What warranty duration applies?','document':'MFG-005','expected':'24 months from documented acceptance; no operational equivalent and no known start date.'},
      {'id':'Q5','question':'What quantity belongs to the second customer?','sql':sql,'parameters':[other['CustomerID'],h['ProductID'],start,end],'expected_sql_result':{'ordered_units':other['AnnualOrderedUnits']},'excluded_document':'MFG-001'}]
    (root/'metadata/demo_context.json').write_text(json.dumps(context,indent=2)+'\n')
    (root/'documents/manifest.json').write_text(json.dumps({'documents':docs,'rights':'New synthetic demo text authored for this project; source entity labels and keys derive from MIT-licensed Microsoft sample.','ingestion':'One format per document ID. No app upload performed.'},indent=2)+'\n')
    for name,data in [('mapping_cases.json',cases),('negative_cases.json',negatives),('expected_answers.json',expected)]:
        (root/'evaluation'/name).write_text(json.dumps(data,indent=2)+'\n')
    print(json.dumps({'documents':len(docs),'mapping_cases':len(cases),'negative_cases':len(negatives),'hero':hero,'supplier':vendor},indent=2))
    conn.close()
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=ROOT);prepare(p.parse_args().root)
