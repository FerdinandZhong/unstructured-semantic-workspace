import hashlib
import json
import os
from pathlib import Path
import pytest
import yaml
from semantic_pipeline_v2.core import State, Packages, normalize, digest, EmbeddingSpace, Evidence, Discovery, SQLiteFacts

PACK=Path(os.environ.get('V2_TEST_PACK',Path(__file__).resolve().parents[1]/'demo_data/manufacturing'))

def model():
    return {'version':'0.2.0.dev0','name':'fixture','datasets':[{'name':'parts','source':'parts','description':'Parts fixture','fields':[{'name':'id','description':'Part ID','expression':{'dialects':[{'dialect':'ANSI_SQL','expression':'id'}]}}]}]}

def encode(value): return yaml.safe_dump(value).encode()

def state(tmp_path): return State.create(tmp_path/'state','test-instance',[tmp_path/'protected'])

@pytest.mark.parametrize('placement',['same','ancestor','descendant','symlink'])
def test_isolation(tmp_path,placement):
    protected=tmp_path/'protected'; protected.mkdir()
    target={'same':protected,'ancestor':tmp_path,'descendant':protected/'new'} .get(placement)
    if placement=='symlink':
        alias=tmp_path/'alias'; alias.symlink_to(protected,target_is_directory=True); target=alias/'new'
    with pytest.raises(ValueError): State.create(target,'test',[protected])
    assert list(protected.iterdir())==[]
    assert not (tmp_path/'instance.json').exists()

def test_instance(tmp_path):
    one=state(tmp_path)
    with pytest.raises(ValueError): State.create(one.root,'other',[tmp_path/'protected'])
    with pytest.raises(ValueError): State.create(tmp_path/'new','',[tmp_path/'protected'])

def test_actual_package(tmp_path):
    raw=(PACK/'semantics/manufacturing.ossie.yaml').read_bytes()
    checksum=json.loads((PACK/'semantics/preparation.json').read_text())['model_sha256']
    result=normalize(raw,checksum)
    assert len(result['records'])>200
    assert normalize(raw,checksum)==result
    packages=Packages(state(tmp_path))
    with pytest.raises(ValueError): packages.activate(raw,checksum)
    packages.activate(raw,checksum,qualification_only=True)
    assert packages.active()['status']=='candidate'
    with pytest.raises(ValueError): packages.activate(raw+b'bad',checksum,qualification_only=True)
    assert packages.active()['revision']==checksum

@pytest.mark.parametrize('bad',['duplicate','empty','version','expression','dialect','relationship','metric','feature','pk'])
def test_invalid_models(bad):
    value=model()
    if bad=='duplicate': value['datasets'].append(value['datasets'][0])
    if bad=='empty': value['datasets'][0]['fields'][0]['description']=''
    if bad=='version': value['version']='1.0'
    if bad=='expression': value['datasets'][0]['fields'][0]['expression']['dialects'][0]['expression']='DROP TABLE parts'
    if bad=='dialect': value['datasets'][0]['fields'][0]['expression']['dialects'][0]['dialect']='other'
    if bad=='relationship': value['relationships']=[{'name':'bad','from':'parts','to':'missing','from_columns':['id'],'to_columns':['id']}]
    if bad=='metric': value['metrics']=[{'name':'bad','description':'bad','expression':{'dialects':[{'dialect':'ANSI_SQL','expression':'SUM(missing.id)'}]}}]
    if bad=='feature': value['unknown']='ignored?'
    if bad=='pk': value['datasets'][0]['primary_key']=['missing']
    raw=encode(value)
    with pytest.raises(ValueError): normalize(raw,digest(raw))

def test_approval_and_last_good(tmp_path):
    packages=Packages(state(tmp_path)); raw=encode(model()); checksum=digest(raw)
    packages.activate(raw,checksum,approval={'reviewer':'fixture-only-reviewer','decision':'approved','revision':checksum})
    before=packages.active()
    with pytest.raises(ValueError): packages.activate(raw,checksum,approval={'reviewer':'x','decision':'approved','revision':'stale'})
    assert packages.active()==before

def test_lance_actual(tmp_path):
    import lancedb
    assert lancedb.__version__=='0.41.0'
    store=Evidence(state(tmp_path),EmbeddingSpace('test-only','fixture-v1',4,'offline-qualification'))
    space=store.space
    raw=b'Synthetic kiln rest stabilization requires twelve minutes.'
    uid=store.put('plant-a',raw,0,raw.decode(),[1,0,0,0],space)
    assert store.put('plant-a',raw,0,raw.decode(),[1,0,0,0],space)==uid
    store.put('plant-b',raw,0,raw.decode(),[1,0,0,0],space)
    assert store.table.count_rows()==2
    assert (store.raw/digest(raw)).read_bytes()==raw
    assert [row['id'] for row in store.search('plant-a',[1,0,0,0],space)]==[uid]
    assert store.search("plant-a' OR scope = 'plant-b",[1,0,0,0],space)==[]
    with pytest.raises(ValueError): store.search('plant-a',[1,0],space)
    with pytest.raises(ValueError): store.search('plant-a',[1,0,0,0],EmbeddingSpace('different','v1',4,'offline-qualification'))
    with pytest.raises(ValueError): Evidence(state(tmp_path),EmbeddingSpace('different','v1',4,'offline-qualification'))
    before=store.table.version
    store.derive(uid,'kiln_rest_stabilization','recorded-provider/fixture-v1')
    assert store.table.search().where(f"id = '{uid}'").to_list()[0]['provenance']=='recorded-provider/fixture-v1'
    store.table.checkout(before)
    assert store.table.search().where(f"id = '{uid}'").to_list()[0]['feature']==''
    store.table.checkout_latest()
    with pytest.raises(ValueError): store.put('plant-a',raw,0,'Synthetic',[1,0,0,0],space)

class RecordedProvider:
    def __init__(self,**overrides): self.overrides=overrides
    def propose(self,evidence):
        assert set(evidence[0])=={'unit_id','text'}
        return [dict(name='Kiln rest stabilization',definition='Required rest after kiln processing.',unit_id=evidence[0]['unit_id'],quote='twelve minutes',**{}) | self.overrides]

def test_open_discovery():
    discovery=Discovery(); assert discovery.proposals=={}
    selected=[{'id':'u1','text':'Synthetic kiln rest stabilization requires twelve minutes.'}]
    first=discovery.discover(selected,RecordedProvider())
    assert first[0]['name']=='Kiln rest stabilization'
    assert discovery.discover(selected,RecordedProvider())==first
    with pytest.raises(ValueError): discovery.discover(selected,RecordedProvider(quote='invented quote'))
    with pytest.raises(ValueError): discovery.discover(selected,RecordedProvider(unit_id='unknown'))
    with pytest.raises(ValueError): discovery.discover(selected,RecordedProvider(definition='conflicting replay'))
    assert len(discovery.proposals)==1

def test_sqlite_and_no_fallback(tmp_path):
    facts=SQLiteFacts(PACK/'data/adventureworks.sqlite')
    assert facts.ordered_units(29641,954,'2024-01-01','2025-01-01')['value']==51
    assert facts.ordered_units(29629,954,'2024-01-01','2025-01-01')['value']==21
    assert facts.ordered_units(29641,954,'2025-01-01','2026-01-01')['value']==8
    assert SQLiteFacts(tmp_path/'missing.sqlite').ordered_units(1,1,'2024','2025')['status']=='unavailable'
    assert not (tmp_path/'missing.sqlite').exists()

def test_protected_defaults_and_nested_symlink(tmp_path):
    from semantic_pipeline_v2.core import PROTECTED_ROOTS
    import semantic_pipeline_v2.core as core
    assert PROTECTED_ROOTS[0].parent == Path(core.__file__).resolve().parents[2]
    for protected in PROTECTED_ROOTS:
        with pytest.raises(ValueError): State.create(protected/'new-denied-state','test',[tmp_path/'other'])
    root=tmp_path/'root'; root.mkdir()
    outside=tmp_path/'outside'; outside.mkdir()
    (root/'raw').symlink_to(outside,target_is_directory=True)
    with pytest.raises(ValueError): State.create(root,'test',[tmp_path/'other'])
    assert list(outside.iterdir())==[]

def test_no_reference_catalog_or_labels_in_core():
    import semantic_pipeline_v2.core as core
    text=Path(core.__file__).read_text()
    for forbidden in ['concept_catalog.json','mapping_cases.json','expected_answers.json','evaluation/']:
        assert forbidden not in text

def test_float32_retry(tmp_path):
    store=Evidence(state(tmp_path),EmbeddingSpace('test-only','fixture-v1',4,'offline-qualification'))
    raw=b'Synthetic decimal vector fixture'
    first=store.put('a',raw,0,raw.decode(),[0.1,0.2,0.3,0.4],store.space)
    assert store.put('a',raw,0,raw.decode(),[0.1,0.2,0.3,0.4],store.space)==first
    assert store.table.count_rows()==1

def test_runtime_catalog_reads_denied(monkeypatch,tmp_path):
    original=Path.open
    def guarded(path,*args,**kwargs):
        if path.name in {'concept_catalog.json','mapping_cases.json','expected_answers.json'} or 'evaluation' in path.parts:
            raise AssertionError('reference-only data access')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',guarded)
    raw=(PACK/'semantics/manufacturing.ossie.yaml').read_bytes()
    assert len(normalize(raw,digest(raw))['records'])==299
    store=Evidence(state(tmp_path),EmbeddingSpace('test-only','fixture-v1',4,'offline-qualification'))
    document=b'Synthetic kiln rest stabilization requires twelve minutes.'
    store.put('a',document,0,document.decode(),[1,0,0,0],store.space)
    assert Discovery().discover(store.search('a',[1,0,0,0],store.space),RecordedProvider())[0]['name']=='Kiln rest stabilization'

def test_discovery_does_not_return_previous_scope():
    discovery=Discovery()
    a=[{'id':'scope-a-unit','text':'Scope A: twelve minutes'}]
    b=[{'id':'scope-b-unit','text':'Scope B: twelve minutes'}]
    assert discovery.discover(a,RecordedProvider())[0]['unit_id']=='scope-a-unit'
    second=discovery.discover(b,RecordedProvider())
    assert [p['unit_id'] for p in second]==['scope-b-unit']
    class Empty:
        def propose(self,evidence): return []
    assert discovery.discover([],Empty())==[]
    assert [p['unit_id'] for p in discovery.discover(a,Empty())]==['scope-a-unit']

@pytest.mark.parametrize('damage',['missing','wrong-row','wrong-schema'])
def test_embedding_identity_fail_closed(tmp_path,damage):
    configured=state(tmp_path); space=EmbeddingSpace('test-only','fixture-v1',4,'offline-qualification')
    store=Evidence(configured,space)
    store.put('a',b'fixture',0,'fixture',[1,0,0,0],space)
    if damage=='missing':
        (configured.root/'embedding-space.json').unlink()
        attempted=EmbeddingSpace('new-model','v2',4,'offline-qualification')
    elif damage=='wrong-row':
        store.table.update(values={'space':'another-embedding-space'})
        attempted=space
    else:
        store.table.alter_columns({'path':'text','rename':'unexpected_text'})
        attempted=space
    with pytest.raises(ValueError): Evidence(configured,attempted)
    if damage=='missing': assert not (configured.root/'embedding-space.json').exists()

def test_real_add_columns_preserves_previous_schema(tmp_path):
    store=Evidence(state(tmp_path),EmbeddingSpace('test-only','v1',4,'qualification'))
    uid=store.put('a',b'fixture',0,'fixture',[1,0,0,0],store.space)
    previous=store.table.version
    assert 'derived_concept' not in store.table.schema.names
    store.add_derived_columns()
    store.derive(uid,'kiln_rest','recorded-provider/v1')
    row=store.table.search().to_list()[0]
    assert row['derived_concept']=='kiln_rest'
    assert row['derived_provenance']=='recorded-provider/v1'
    store.table.checkout(previous)
    assert 'derived_concept' not in store.table.schema.names
    assert store.table.search().to_list()[0]['id']==uid


def test_explicit_actual_worktree_protection(tmp_path):
    from semantic_pipeline_v2.core import PROTECTED_ROOTS
    actual_roots=list(PROTECTED_ROOTS)   # the V1 sibling and Helios checkouts beside this repository
    for protected in actual_roots:
        with pytest.raises(ValueError):
            State.create(protected/'new-denied-state','test',actual_roots)
        assert not (protected/'new-denied-state').exists()
