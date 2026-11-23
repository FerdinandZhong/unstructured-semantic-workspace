#!/usr/bin/env python3
"""Author a bounded demo semantic package from the pinned projection metadata.
No model calls, embedding generation, publication, or human-approval simulation.
"""
from pathlib import Path
import argparse
import json
import re
import hashlib
import yaml

ROOT = Path(__file__).resolve().parents[1]

def extension(**data):
    return [{'vendor_name':'HELIOS','data':json.dumps(data,sort_keys=True)}]

def expression(s):
    return {'dialects':[{'dialect':'ANSI_SQL','expression':s}]}

# Definitions below are authored demo specifications, not inferred operational truth.
OVERRIDES = {
 ('Production.Product','ProductNumber'):('Product number','Product identifier in the AdventureWorks namespace. The same text in another supplier namespace does not establish identity.','identifier'),
 ('Sales.Customer','CustomerID'):('Customer identifier','Identifier of one AdventureWorks customer account; StoreID links a business customer to its store.','identifier'),
 ('Sales.SalesOrderDetail','OrderQty'):('Sales ordered quantity','Count of product units on one sales-order line. This is an order quantity, not a contractual minimum or proof of delivery.','measure'),
 ('Sales.SalesOrderHeader','DueDate'):('Order due date','Due date recorded on a sales order. This does not establish a negotiated delivery promise or actual receipt date.','time'),
 ('Sales.SalesOrderHeader','ShipDate'):('Shipment date','Shipment date recorded for an order. Shipment does not prove delivery or customer acceptance.','time'),
 ('Purchasing.ProductVendor','AverageLeadTime'):('Average supplier lead time','Source-described average time in days between placing a purchase order and receiving the purchased product. It is not a contract promise.','measure'),
 ('Purchasing.ProductVendor','MinOrderQty'):('Minimum supplier order quantity','Minimum order quantity under the demo interpretation of the column name. Upstream description says maximum; domain-owner review is required.','measure'),
 ('Purchasing.ProductVendor','MaxOrderQty'):('Maximum supplier order quantity','Maximum order quantity under the demo interpretation of the column name. Upstream description says minimum; domain-owner review is required.','measure'),
 ('Production.WorkOrder','OrderQty'):('Production order quantity','Requested product quantity on a work order. It is not the customer sales-order quantity.','measure'),
 ('Production.WorkOrder','ScrappedQty'):('Scrapped quantity','Quantity scrapped on one work order. A nonzero quantity does not by itself establish a contract-quality breach.','measure'),
 ('Production.ProductInventory','Quantity'):('Inventory quantity','Quantity held for one product and location at the source snapshot; shelf and bin describe its position. It is not available-to-promise inventory.','measure'),
 ('Production.BillOfMaterials','PerAssemblyQty'):('Component quantity per assembly','Quantity of a component needed per assembly in the specified component unit of measure and effective period.','measure'),
}

CONCEPTS = [
 ('product_identifier','Product identifier','A product key interpreted within its source namespace.','identifier','Production.Product.ProductNumber','identifier_binding','Match the namespace and verified key, never name similarity alone.'),
 ('customer_identifier','Customer account identifier','A customer account key interpreted within the source system.','identifier','Sales.Customer.CustomerID','identifier_binding','An organization name alone does not prove an account match.'),
 ('purchase_commitment','Minimum purchase commitment','A contract obligation to order at least a stated quantity of a specified product during a defined period.','obligation','Sales.SalesOrderDetail.OrderQty','related_context','An obligation is compared with actual ordered quantity; it is never an equivalent field.'),
 ('delivery_promise','Promised delivery date','A date promised by a contract or amendment for delivery of a scoped order.','obligation','Sales.SalesOrderHeader.DueDate','related_context','An operational due date may differ; shipment does not establish delivery.'),
 ('supplier_lead_time_promise','Promised supplier lead time','Contractually promised elapsed days from the stated start event to receipt for a scoped supplier/product.','obligation','Purchasing.ProductVendor.AverageLeadTime','related_context','Promised duration is not the source historical average. Start/end events and calendar matter.'),
 ('scrap_quality_context','Reported production scrap','A source reports scrapped quantity for a product/work order.','observation','Production.WorkOrder.ScrappedQty','related_context','No inference of defect rate, root cause or contractual acceptance without evidence.'),
 ('inventory_position','Reported inventory quantity','A source reports quantity at a product/location snapshot.','observation','Production.ProductInventory.Quantity','related_context','Confirm the same snapshot and location; do not equate with available-to-promise.'),
 ('warranty_duration','Warranty duration','A promised warranty interval with a specified start event and exclusions.','obligation',None,'unmapped','No native warranty-duration field is established in this package.'),
 ('governing_law','Governing law','The legal jurisdiction named by a contract clause.','contract_metadata',None,'unmapped','Do not force a geographic-address mapping.'),
 ('contract_effective_date','Contract effective date','Date from which a specified contract version applies.','contract_metadata',None,'unmapped','Do not equate with order date or a generic ModifiedDate.'),
 ('inspection_acceptance','Inspection and acceptance condition','Conditions and evidence required for accepting supplied goods.','obligation',None,'unmapped','Scrap outcomes provide context only, not evidence of customer acceptance.'),
 ('supplier_identifier','Supplier identifier','An AdventureWorks vendor business-entity key in its declared source namespace.','identifier','Purchasing.Vendor.BusinessEntityID','identifier_binding','Supplier and customer roles stay distinct even when labels match.'),
]

def datatype(t):
    t=t.lower()
    if any(x in t for x in ['datetime','smalldatetime']): return 'DateTime'
    if t == 'date': return 'Date'
    if 'int' in t: return 'Integer'
    if t in ['bit','flag']: return 'Boolean'
    if any(x in t for x in ['decimal','numeric','money']): return 'Decimal'
    if any(x in t for x in ['float','real']): return 'Float'
    if any(x in t for x in ['binary','image','geometry','geography','hierarchyid']): return 'Opaque'
    return 'String'

def build(schema):
    datasets=[]; bindings=[]; conflicts=[]
    for table in schema['tables']:
        name=table['name']; local=table['sqlite_name']; pk=table['primary_key']; fields=[]
        for col in table['columns']:
            cn=col['name']; st=schema.get('source_type_aliases',{}).get(col['source_type'],col['source_type']); label=re.sub(r'(?<=[a-z])(?=[A-Z])',' ',cn)
            raw=col.get('description') or ''
            desc=raw or f'{cn} on {name}. Source description unavailable; review before broader business use.'
            role='identifier' if cn in pk else ('time' if datatype(st) in ['Date','DateTime'] else 'attribute')
            if (name,cn) in OVERRIDES: label,desc,role=OVERRIDES[(name,cn)]
            field={'name':cn,'expression':expression(cn),'label':label,'description':desc,'datatype':datatype(st),
                   'custom_extensions':extension(role=role,source_table=name,source_column=cn,source_type=col['source_type'],normalized_type=st,source_description=raw,review_state='authored_demo_candidate')}
            if role!='measure': field['dimension']={'is_time':role=='time'}
            fields.append(field)
            bindings.append({'definition_id':name+'.'+cn,'dataset':local,'field':cn,'source_table':name,'source_column':cn,'state':'authored_demo_candidate'})
            if name=='Purchasing.ProductVendor' and cn in ['MinOrderQty','MaxOrderQty']:
                conflicts.append({'field':name+'.'+cn,'source_description':raw,'proposed_definition':desc,'status':'requires_domain_review','exclude_from_certified_metrics':True})
        datasets.append({'name':local,'source':local,'primary_key':pk,'description':table.get('description') or f'Demo projection of {name}. Grain: one row per '+', '.join(pk)+'.',
                         'fields':fields,'custom_extensions':extension(kind='fact' if any(x in name for x in ['Order','Inventory']) else 'dimension',source_table=name)})
    names={t['name']:t['sqlite_name'] for t in schema['tables']}
    relationships=[]
    for i,r in enumerate(schema['foreign_keys']):
        if r['from_table'] not in names or r['to_table'] not in names: continue
        relationships.append({'name':r.get('name',f'relation_{i}'),'from':names[r['from_table']],'to':names[r['to_table']],
                              'from_columns':r['from_columns'],'to_columns':r['to_columns'],
                              'custom_extensions':extension(source='declared_source_foreign_key',is_default=True)})
    metrics=[]
    for n,table,col,desc,unit,grain in [
      ('sales_ordered_units','Sales.SalesOrderDetail','OrderQty','Sum ordered units after filtering product and customer through the many-to-one order header; time filter uses OrderDate. Not shipments, revenue or contract compliance.','each','sales-order line'),
      ('production_requested_units','Production.WorkOrder','OrderQty','Sum requested units for a selected product and period using StartDate. Keep separate from sales quantities.','each','work order'),
      ('production_scrapped_units','Production.WorkOrder','ScrappedQty','Sum scrapped units for a selected product and period using StartDate. Not a defect percentage.','each','work order'),
      ('inventory_units','Production.ProductInventory','Quantity','Sum inventory units for a selected product and source snapshot across selected locations. Not a historical stock trend.','each','product/location')]:
        if table in names:
            ds=names[table]
            metrics.append({'name':n,'expression':expression(f'SUM({ds}.{col})'),'description':desc,'datatype':'Integer',
                            'custom_extensions':extension(dataset=ds,unit=unit,grain=grain,review_state='authored_demo_candidate',join_safety_requirement='Preserve base grain; runtime enforcement not implemented')})
    model={'version':'0.2.0.dev0','name':'adventureworks_manufacturing_demo','description':'Authored demonstration semantic model from pinned Microsoft AdventureWorks metadata. This is not Helios AI-generated or human-certified.',
           'ai_context':{'instructions':'Use source namespace and verified keys for identity. Separate obligations, observations and SQL facts. Do not use unresolved metadata conflicts as certified definitions. SQL dialect portability beyond the local preparation fixture is unverified.'},
           'datasets':datasets,'relationships':relationships,'metrics':metrics,
           'custom_extensions':extension(review_state='authored_demo_candidate',package_version='0.1.0',scope='demo_manufacturing',producer='offline_metadata_preparation')}
    concepts=[]
    known={b['definition_id'] for b in bindings}
    for id,label,definition,kind,target,relation,guard in CONCEPTS:
        if target and target not in known: raise ValueError(f'Missing concept target {target}')
        concepts.append({'id':'urn:demo:manufacturing:concept:'+id,'key':id,'label':label,'definition':definition,'kind':kind,'target_definition':target,'expected_relation':relation,'compatibility_rule':guard,'review_state':'authored_demo_candidate'})
    return model,bindings,conflicts,concepts

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--root',type=Path,default=ROOT); args=parser.parse_args(); root=args.root
    schema=json.loads((root/'metadata/schema.json').read_text())
    model,bindings,conflicts,concepts=build(schema)
    out=root/'semantics'; out.mkdir(exist_ok=True)
    (out/'manufacturing.ossie.yaml').write_text(yaml.safe_dump(model,sort_keys=False,allow_unicode=True))
    for name,data in [('bindings.json',bindings),('metadata_conflicts.json',conflicts),('concept_catalog.json',{'version':'0.1.0','scope':'demo_manufacturing','review_state':'authored_demo_candidate','concepts':concepts})]:
        (out/name).write_text(json.dumps(data,indent=2)+'\n')
    (out/'preparation.json').write_text(json.dumps({'producer':'authored offline from source metadata','model_sha256':hashlib.sha256((out/'manufacturing.ossie.yaml').read_bytes()).hexdigest(),'human_approval':'not_recorded','helios_generation':'not_run','embeddings':'not_run','publication':'not_run'},indent=2)+'\n')
    print(f'Authored {len(model["datasets"])} datasets, {len(bindings)} fields, {len(model["relationships"])} relationships, {len(concepts)} concepts.')
if __name__=='__main__': main()
