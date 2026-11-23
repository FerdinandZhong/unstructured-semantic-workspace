"""Bounded offline qualification CLI. This is not a server or live AI workflow."""
import argparse
import importlib.metadata
import json
from pathlib import Path
import socket
import tempfile
from .core import State, Packages, digest, EmbeddingSpace, Evidence, Discovery, SQLiteFacts


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pack',type=Path,required=True,help='prepared manufacturing pack, read only')
    args=parser.parse_args()
    def no_network(*args,**kwargs):
        raise RuntimeError('network disabled for offline qualification')
    socket.socket.connect=no_network
    socket.socket.connect_ex=no_network
    socket.create_connection=no_network
    socket.getaddrinfo=no_network
    with tempfile.TemporaryDirectory(prefix='v2-qualification-') as temporary:
        root=Path(temporary)
        state=State.create(root/'state','offline-qualification',[args.pack])
        raw=(args.pack/'semantics/manufacturing.ossie.yaml').read_bytes()
        checksum=json.loads((args.pack/'semantics/preparation.json').read_text())['model_sha256']
        packages=Packages(state)
        active=packages.activate(raw,checksum,qualification_only=True)
        space=EmbeddingSpace('test-only-fixture','fixture-v1',4,'offline-qualification')
        evidence=Evidence(state,space)
        document=b'SYNTHETIC: Kiln rest stabilization requires twelve minutes before packing.'
        uid=evidence.put('manufacturing-qualification',document,0,document.decode(),[1,0,0,0],space)
        evidence.put('manufacturing-qualification',document,0,document.decode(),[1,0,0,0],space)
        manufacturing=(args.pack/'documents/markdown/MFG-001.md').read_bytes()
        evidence.put('manufacturing-document',manufacturing,0,manufacturing.decode(),[0,1,0,0],space)
        selected=evidence.search('manufacturing-qualification',[1,0,0,0],space)
        class RecordedProvider:
            def propose(self,units):
                return [{'name':'Kiln rest stabilization','definition':'Required rest after kiln processing.',
                         'unit_id':units[0]['unit_id'],'quote':'twelve minutes'}]
        proposals=Discovery().discover(selected,RecordedProvider())
        old_version=evidence.table.version
        evidence.add_derived_columns()
        evidence.derive(uid,'kiln_rest_stabilization','recorded-provider/fixture-v1; exact quoted evidence')
        evidence.table.checkout(old_version)
        assert 'derived_concept' not in evidence.table.schema.names
        evidence.table.checkout_latest()
        result=SQLiteFacts(args.pack/'data/adventureworks.sqlite').ordered_units(29641,954,'2024-01-01','2025-01-01')
        assert result['value']==51 and result['status']=='available'
        unavailable=SQLiteFacts(root/'missing.sqlite').ordered_units(29641,954,'2024-01-01','2025-01-01')
        assert unavailable['status']=='unavailable' and packages.active()['revision']==checksum
        output={'status':'passed','scope':'P0 local qualification only; converter probe separate',
                'versions':{name:importlib.metadata.version(name) for name in ['lancedb','pyarrow','numpy','PyYAML','jsonschema','pytest']},
                'limits':{'max_units':20,'indexed_units':evidence.table.count_rows(),'dimensions':4,'writers':1,
                          'network':'disabled','model_calls':0,'services':0,'vector_label':'test-only fixture'},
                'package':{'revision':active['revision'],'definitions':len(active['records']),
                           'mode':active['activation_mode'],'human_approval':'not_recorded'},
                'discovery':{'source':'injected recorded provider','concept':proposals[0]['name'],'quote':proposals[0]['quote']},
                'sql':result,'unavailable_backend':unavailable,'previous_lance_version_read':old_version,
                'schema_evolution':{'added_columns':['derived_concept','derived_provenance'],'previous_version_lacks_columns':True},
                'manufacturing_document':{'id':'MFG-001','label':'synthetic demo contract','raw_hash':digest(manufacturing)}}
        print(json.dumps(output,indent=2))

if __name__=='__main__': main()
