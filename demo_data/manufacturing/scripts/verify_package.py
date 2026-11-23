#!/usr/bin/env python3
"""Offline artifact checks. Does not publish a model or run the V2 application."""
from pathlib import Path
import argparse, json, sqlite3, hashlib, sys, datetime, zipfile
import xml.etree.ElementTree as ET
import yaml
import jsonschema
ROOT=Path(__file__).resolve().parents[1]

def check(root,helios):
    results={}
    schema=json.loads((root/'metadata/schema.json').read_text())
    model=yaml.safe_load((root/'semantics/manufacturing.ossie.yaml').read_text())
    # Read the actual local contract; do not copy or change the Helios project.
    schema_path=helios/'shared/helios_core/ossie/ossie-schema.json'
    jsonschema.Draft202012Validator(json.loads(schema_path.read_text())).validate(model)
    results['ossie_schema']='passed'
    sys.dont_write_bytecode=True
    sys.path.insert(0,str(helios/'shared'))
    from helios_core.ossie.model import SemanticModel
    m=SemanticModel(model)
    assert len(m.datasets)==len(schema['tables'])>=10
    assert len(m.relationships)>0
    for r in m.relationships.values():
        assert r.from_dataset in m.datasets and r.to_dataset in m.datasets
        for col in r.from_columns: assert col in m.datasets[r.from_dataset].fields
        for col in r.to_columns: assert col in m.datasets[r.to_dataset].fields
    for d in m.datasets.values():
        assert d.primary_key
        for c in d.primary_key: assert c in d.fields
    results['helios_model_load_and_references']='passed'
    for ds,col in [('Production_Product','MakeFlag'),('Production_Product','FinishedGoodsFlag'),('Sales_SalesOrderHeader','OnlineOrderFlag'),('Purchasing_Vendor','PreferredVendorStatus'),('Purchasing_Vendor','ActiveFlag')]:
        assert m.datasets[ds].fields[col].datatype=='Boolean', (ds,col)
    results['source_flag_alias_semantics']='passed'
    conn=sqlite3.connect(f'file:{root/"data/adventureworks.sqlite"}?mode=ro',uri=True);conn.row_factory=sqlite3.Row
    manifests=json.loads((root/'documents/manifest.json').read_text())['documents']
    assert len(manifests)>=6
    for item in manifests:
        p=root/item['primary_path'];s=p.read_text()
        assert 'SYNTHETIC DEMO DOCUMENT — NOT A REAL CONTRACT' in s
        assert hashlib.sha256(p.read_bytes()).hexdigest()==item['sha256']
        with zipfile.ZipFile(root/item['alternative_path']) as z:
            xml=ET.fromstring(z.read('word/document.xml'))
            words=' '.join(xml.itertext())
            assert 'SYNTHETIC DEMO DOCUMENT' in words
            assert item['id'] in words
    results['synthetic_labels_hashes_and_docx_xml']='passed'
    cases=json.loads((root/'evaluation/mapping_cases.json').read_text())
    known={b['definition_id'] for b in json.loads((root/'semantics/bindings.json').read_text())}
    catalog=json.loads((root/'semantics/concept_catalog.json').read_text())['concepts']
    keys={c['key'] for c in catalog}
    for c in cases:
        text=(root/c['path']).read_text();l=c['locator']
        assert text[l['start']:l['end']]==c['quote']
        assert c['concept'] in keys
        if c['target_definition']: assert c['target_definition'] in known
        if c['expected_relation']=='unmapped': assert c['target_definition'] is None
    results['quotes_concepts_and_target_bindings']='passed'
    answers=json.loads((root/'evaluation/expected_answers.json').read_text())
    for a in answers:
        if 'sql' in a:
            found=dict(conn.execute(a['sql'],a['parameters']).fetchone())
            assert found==a['expected_sql_result'],(a['id'],found,a['expected_sql_result'])
    results['expected_answer_sql']='passed'
    ctx=json.loads((root/'metadata/demo_context.json').read_text())
    assert ctx['hero']['CustomerID']!=ctx['second_customer']['CustomerID']
    assert ctx['authored_terms']['amendment_effective']>str(ctx['order']['OrderDate'])[:10]
    assert ctx['authored_terms']['amended_delivery_promise']>ctx['authored_terms']['original_delivery_promise']
    results['customer_and_amendment_boundaries']='passed'
    negatives=json.loads((root/'evaluation/negative_cases.json').read_text())
    assert len(negatives)>=8 and all(n['expected'] in ['reject','unresolved','exclude_evidence','use_base_promise','require_domain_review'] for n in negatives)
    # Mutation check: invalid schema input must fail. No production data is mutated.
    bad=dict(model);bad['version']='unsupported'
    try: jsonschema.validate(bad,json.loads(schema_path.read_text()))
    except jsonschema.ValidationError: pass
    else: raise AssertionError('Unsupported semantic version passed validation')
    results['invalid_model_version_rejected']='passed'
    results['evaluation_fixture_integrity']='passed (authored expectations only; AI and policy evaluation not run)'
    # Metrics execute over the declared base grain with no fanout joins.
    for metric in model['metrics']:
        ext=json.loads(metric['custom_extensions'][0]['data']);ds=ext['dataset'];expr=metric['expression']['dialects'][0]['expression']
        value=conn.execute(f'SELECT {expr} FROM {ds}').fetchone()[0]
        assert value is not None and value>=0
    results['metric_sql_execution']='passed'
    conn.close()
    result={'observed':datetime.datetime.now(datetime.timezone.utc).isoformat(),'checks':results,'counts':{'datasets':len(m.datasets),'fields':sum(len(d.fields) for d in m.datasets.values()),'relationships':len(m.relationships),'metrics':len(m.metrics),'concepts':len(catalog),'documents':len(manifests),'positive_and_unmapped_cases':len(cases),'negative_cases':len(negatives)},'helios_schema_sha256':hashlib.sha256(schema_path.read_bytes()).hexdigest(),'limitations':['Helios schema and reader only; no Helios proposal/model call or publication','No embeddings, model extraction accuracy, app integration, access-policy execution, or enterprise acceptance tested','Authored labels are not independently human-adjudicated']}
    (root/'evaluation/package_verification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=ROOT);p.add_argument('--helios',type=Path,required=True);a=p.parse_args();check(a.root,a.helios)
