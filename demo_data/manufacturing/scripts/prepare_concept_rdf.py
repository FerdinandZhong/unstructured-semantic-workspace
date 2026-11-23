#!/usr/bin/env python3
"""Serialize authored concept definitions and proposed bindings, never approved facts."""
from pathlib import Path
import argparse, json
from rdflib import Graph, Namespace, URIRef, Literal
from rdflib.namespace import RDF, SKOS, RDFS, XSD
from pyshacl import validate
ROOT=Path(__file__).resolve().parents[1]
V=Namespace('urn:demo:manufacturing:vocab:')
SHAPES='''@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix v: <urn:demo:manufacturing:vocab:> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
v:ConceptShape a sh:NodeShape; sh:targetClass skos:Concept;
 sh:property [sh:path skos:prefLabel; sh:minCount 1; sh:maxCount 1; sh:datatype xsd:string; sh:minLength 1];
 sh:property [sh:path skos:definition; sh:minCount 1; sh:maxCount 1; sh:datatype xsd:string; sh:minLength 1].
v:ProposalShape a sh:NodeShape; sh:targetClass v:MappingProposal;
 sh:property [sh:path v:sourceConcept; sh:minCount 1; sh:maxCount 1; sh:nodeKind sh:IRI; sh:class skos:Concept];
 sh:property [sh:path v:targetDefinition; sh:minCount 1; sh:maxCount 1; sh:nodeKind sh:IRI; sh:class skos:Concept];
 sh:property [sh:path v:relation; sh:minCount 1; sh:maxCount 1; sh:in ("identifier_binding" "related_context")];
 sh:property [sh:path v:status; sh:hasValue "authored_demo_candidate"; sh:maxCount 1].
'''
def main(root):
    catalog=json.loads((root/'semantics/concept_catalog.json').read_text());binds={b['definition_id']:b for b in json.loads((root/'semantics/bindings.json').read_text())}
    model=json.loads(json.dumps(__import__('yaml').safe_load((root/'semantics/manufacturing.ossie.yaml').read_text())))
    fields={(d['name'],f['name']):f for d in model['datasets'] for f in d['fields']}
    g=Graph();g.bind('v',V);g.bind('skos',SKOS)
    scheme=URIRef('urn:demo:manufacturing:catalog:0.1.0')
    g.add((scheme,RDF.type,SKOS.ConceptScheme));g.add((scheme,SKOS.prefLabel,Literal('Manufacturing demonstration concept catalog')))
    for c in catalog['concepts']:
        u=URIRef(c['id']);g.add((u,RDF.type,SKOS.Concept));g.add((u,SKOS.inScheme,scheme));g.add((u,SKOS.prefLabel,Literal(c['label'])));g.add((u,SKOS.definition,Literal(c['definition'])));g.add((u,V.status,Literal(c['review_state'])))
        if c['target_definition']:
            target=URIRef('urn:demo:manufacturing:definition:'+c['target_definition'])
            b=binds[c['target_definition']];f=fields[b['dataset'],b['field']]
            g.add((target,RDF.type,SKOS.Concept));g.add((target,SKOS.prefLabel,Literal(f['label'])));g.add((target,SKOS.definition,Literal(f['description'])));g.add((target,V.sourceColumn,Literal(c['target_definition'])))
            prop=URIRef('urn:demo:manufacturing:proposal:'+c['key']);g.add((prop,RDF.type,V.MappingProposal));g.add((prop,V.sourceConcept,u));g.add((prop,V.targetDefinition,target));g.add((prop,V.relation,Literal(c['expected_relation'])));g.add((prop,V.status,Literal('authored_demo_candidate')));g.add((prop,V.rule,Literal(c['compatibility_rule'])))
    shapes=Graph().parse(data=SHAPES,format='turtle');ok,_,report=validate(g,shacl_graph=shapes,inference='none')
    if not ok: raise ValueError(report)
    bad=Graph();bad+=g
    first=URIRef(catalog['concepts'][0]['id']);bad.remove((first,SKOS.definition,None))
    rejected,_,_=validate(bad,shacl_graph=shapes,inference='none')
    if rejected: raise ValueError('SHACL failed to reject missing definition')
    (root/'semantics/concepts.ttl').write_text(g.serialize(format='turtle'))
    (root/'semantics/concept_shapes.ttl').write_text(SHAPES)
    (root/'evaluation/rdf_verification.json').write_text(json.dumps({'conforms':True,'missing_definition_rejected':True,'triples':len(g),'validator':'pySHACL','inference':'none','meaning':'Structural conformance only; not factual truth, human approval, OWL reasoning or app integration.'},indent=2)+'\n')
    print('Concept RDF shape checks passed; missing-definition mutation rejected.')
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=ROOT);main(p.parse_args().root)
