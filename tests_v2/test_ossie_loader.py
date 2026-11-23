"""V2-CORE C1: the Ossie normaliser accepts ai_context and exposes per-definition semantics."""
from pathlib import Path

import pytest
import yaml

from semantic_pipeline_v2.core import digest, normalize

ROOT = Path(__file__).resolve().parents[1]
MODELS = {
    'finance': ROOT / 'demo_data/finance/finance.ossie.yaml',
    'manufacturing': ROOT / 'demo_data/manufacturing/semantics/manufacturing.ossie.yaml',
}


def _normalized(path):
    raw = path.read_bytes()
    return normalize(raw, digest(raw))


@pytest.mark.parametrize('name', MODELS)
def test_enriched_model_loads(name):
    assert _normalized(MODELS[name])['records']


def _record(package, source_id):
    return next(r for r in package['records'] if r['source_id'].endswith(source_id))


def test_field_exposes_label_synonyms_examples_role_concept_unit_values():
    package = _normalized(MODELS['manufacturing'])
    semantics = _record(package, '/field/Production_ProductInventory/Quantity')['semantics']
    assert set(semantics) == {'label', 'synonyms', 'examples', 'role', 'concept', 'unit', 'values'}
    assert semantics['role'] == 'measure' and semantics['unit'] and semantics['concept']
    assert semantics['label'] and isinstance(semantics['synonyms'], list)


def test_metric_and_dataset_expose_ai_context():
    package = _normalized(MODELS['finance'])
    metric = _record(package, '/metric/casa_transaction_count')['semantics']
    assert metric['label'] and metric['synonyms'] and metric['examples'] and metric['concept'] == 'transaction_volume'
    assert _record(package, '/dataset/Customers')['semantics']['synonyms']


def test_values_come_from_the_helios_extension():
    package = _normalized(MODELS['finance'])
    with_values = [r for r in package['records'] if r['source_id'].split('/')[1] == 'field' and r['semantics']['values']]
    assert with_values


def test_definition_without_semantics_gets_empty_defaults():
    package = _normalized(MODELS['finance'])
    relationship = next(r for r in package['records'] if '/relationship/' in r['source_id'])
    assert relationship['semantics'] == {'label': None, 'synonyms': [], 'examples': [], 'role': None,
                                         'concept': None, 'unit': None, 'values': None}


def test_string_ai_context_is_accepted_and_unknown_dataset_key_is_still_rejected():
    model = yaml.safe_load(MODELS['finance'].read_bytes())
    model['datasets'][0]['ai_context'] = 'Use for customer counts.'
    raw = yaml.safe_dump(model).encode()
    assert normalize(raw, digest(raw))['records']
    model['datasets'][0]['surprise'] = 1
    raw = yaml.safe_dump(model).encode()
    with pytest.raises(ValueError, match='unsupported dataset feature'):
        normalize(raw, digest(raw))
