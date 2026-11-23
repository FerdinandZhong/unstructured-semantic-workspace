"""V2-CORE C1: Ossie foundation checks T1 to T3 and T32 on the promoted live models.

The live model is the enriched model: enrich(live, overlay) == live. Models follow the Ossie authority rule.
"""
import copy
import importlib.util
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT.parent / 'semantic-hub/shared/helios_core/ossie/ossie-schema.json'
MODELS = {
    'finance': (ROOT / 'demo_data/finance/finance.ossie.yaml',
                ROOT / 'demo_data/finance/semantics_overlay.yaml'),
    'manufacturing': (ROOT / 'demo_data/manufacturing/semantics/manufacturing.ossie.yaml',
                      ROOT / 'demo_data/manufacturing/semantics/semantics_overlay.yaml'),
}

_spec = importlib.util.spec_from_file_location('enrich_ossie', ROOT / 'demo_data/enrich_ossie.py')
enrich_ossie = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(enrich_ossie)


def _enriched(name):
    base, overlay = MODELS[name]
    return enrich_ossie.enrich(yaml.safe_load(base.read_text()), yaml.safe_load(overlay.read_text()))


@pytest.mark.parametrize('name', MODELS)
def test_t1_candidate_passes_lint(name):
    assert enrich_ossie.lint(_enriched(name)) == []


def _live(name):
    return yaml.safe_load(MODELS[name][0].read_text())


@pytest.mark.parametrize('name', MODELS)
def test_t1_live_model_is_an_enrichment_fixpoint(name):
    overlay = yaml.safe_load(MODELS[name][1].read_text())
    assert enrich_ossie.enrich(_live(name), overlay) == _live(name)


@pytest.mark.parametrize('name', MODELS)
def test_t1_live_model_passes_lint(name):
    assert enrich_ossie.lint(_live(name)) == []


@pytest.mark.parametrize('name', MODELS)
def test_review_candidates_are_gone(name):
    assert not MODELS[name][0].with_suffix('.review.yaml').exists()
    assert not list(MODELS[name][0].parent.glob('*.ossie.review.yaml'))


@pytest.mark.parametrize('name', MODELS)
def test_t1_candidate_keeps_base_structure(name):
    base = yaml.safe_load(MODELS[name][0].read_text())
    model = _enriched(name)
    shape = lambda m: [(d['name'], d.get('primary_key'), [(f['name'], f['expression'], f.get('datatype')) for f in d['fields']])
                       for d in m['datasets']]
    assert shape(model) == shape(base)
    assert model.get('relationships') == base.get('relationships')
    assert [(m['name'], m['expression']) for m in model['metrics']] == [(m['name'], m['expression']) for m in base['metrics']]


@pytest.mark.parametrize('name', MODELS)
@pytest.mark.skipif(not SCHEMA.exists(), reason='Helios Ossie schema is not present beside this worktree')
def test_t1_candidate_matches_helios_schema(name):
    import jsonschema
    jsonschema.Draft202012Validator(json.loads(SCHEMA.read_text())).validate(_enriched(name))


@pytest.mark.parametrize('name', MODELS)
def test_t2_missing_metric_unit_fails(name):
    model = _enriched(name)
    metric = model['metrics'][0]
    data = enrich_ossie.ext(metric)
    del data['unit']
    metric['custom_extensions'] = [{'vendor_name': 'HELIOS', 'data': json.dumps(data)}]
    assert f'metric {metric["name"]}: no unit' in enrich_ossie.lint(model)


@pytest.mark.parametrize('name', MODELS)
def test_t2_missing_field_label_fails(name):
    model = _enriched(name)
    dataset = model['datasets'][0]
    field = dataset['fields'][0]
    field.pop('label')
    assert f'field {dataset["name"]}.{field["name"]}: no business label' in enrich_ossie.lint(model)


@pytest.mark.parametrize('name', MODELS)
def test_t3_six_competency_questions_resolve(name):
    model = _enriched(name)
    questions = enrich_ossie.ext(model)['competency_questions']
    assert len(questions) >= 6
    assert all(q['question'] and q['definitions'] for q in questions)


@pytest.mark.parametrize('name', MODELS)
def test_t3_question_outside_model_is_reported(name):
    model = copy.deepcopy(_enriched(name))
    data = enrich_ossie.ext(model)
    data['competency_questions'].append({'question': 'What is the weather?', 'definitions': ['Weather.Temperature']})
    model['custom_extensions'] = [{'vendor_name': 'HELIOS', 'data': json.dumps(data)}]
    errors = enrich_ossie.lint(model)
    assert any('unknown definition Weather.Temperature' in e for e in errors)


def test_unknown_overlay_name_is_rejected():
    base = yaml.safe_load(MODELS['finance'][0].read_text())
    with pytest.raises(ValueError, match='field Customers.NoSuchField'):
        enrich_ossie.enrich(base, {'datasets': {'Customers': {'fields': {'NoSuchField': {'label': 'x'}}}}})


def _metrics(name):
    return {m['name']: m for m in _live(name)['metrics']}


def _set_ext(node, **values):
    data = enrich_ossie.ext(node)
    data.update(values)
    node['custom_extensions'] = [{'vendor_name': 'HELIOS', 'data': json.dumps(data)}]


def _concepts(name):
    return {c['id']: c for c in enrich_ossie.ext(_live(name))['concepts']}


# ---- Ossie authority rule: finance ----

def test_finance_metric_names_state_what_they_compute():
    names = set(_metrics('finance'))
    assert {'credit_card_transaction_value', 'casa_transaction_count'} <= names
    assert not names & {'transaction_value', 'transaction_count'}


def test_finance_value_metrics_require_one_currency_and_one_date():
    metrics = _metrics('finance')
    for name in ('casa_transaction_value', 'credit_card_transaction_value'):
        required = enrich_ossie.ext(metrics[name])['required_filters']
        assert required['Currency'] == 'single_value', name
        assert required['BusinessDate'] == 'single_value', name


def test_finance_time_field_is_the_required_date_filter():
    for name, metric in _metrics('finance').items():
        data = enrich_ossie.ext(metric)
        dates = [k for k in data['required_filters'] if k in ('BusinessDate', 'TransactionDate')]
        # an Output C metric dates by the form's own StartDate and requires no snapshot date
        assert dates == ([data['time_field']] if data['time_field'] in ('BusinessDate', 'TransactionDate') else []), name


def test_finance_transaction_volume_is_not_under_an_amount():
    assert _concepts('finance')['transaction_volume']['broader'] == 'transaction'


def test_lint_rejects_value_metric_without_currency_filter():
    model = _enriched('finance')
    metric = next(m for m in model['metrics'] if m['name'] == 'casa_transaction_value')
    required = dict(enrich_ossie.ext(metric)['required_filters'])
    del required['Currency']
    _set_ext(metric, required_filters=required)
    assert 'metric casa_transaction_value: unit source currency needs a single_value Currency filter' in enrich_ossie.lint(model)


def test_lint_rejects_time_field_that_differs_from_required_date_filter():
    model = _enriched('finance')
    metric = next(m for m in model['metrics'] if m['name'] == 'casa_transaction_count')
    _set_ext(metric, time_field='TransactionDate')
    assert any(e.startswith('metric casa_transaction_count: time_field TransactionDate differs from required date filter')
               for e in enrich_ossie.lint(model))


# ---- Ossie authority rule: manufacturing ----

def test_manufacturing_units_do_not_claim_a_currency():
    for name, metric in _metrics('manufacturing').items():
        assert 'USD' not in enrich_ossie.ext(metric)['unit'], name
    assert enrich_ossie.ext(_metrics('manufacturing')['sales_line_revenue'])['unit'] == \
        'currency amount (currency not recorded in the source)'


def test_inventory_units_has_no_business_time_field():
    assert enrich_ossie.ext(_metrics('manufacturing')['inventory_units'])['time_field'] == 'none recorded'


def test_lint_rejects_audit_column_as_time_field():
    model = _enriched('manufacturing')
    metric = next(m for m in model['metrics'] if m['name'] == 'inventory_units')
    _set_ext(metric, time_field='ModifiedDate')
    assert any(e.startswith('metric inventory_units: time_field ModifiedDate') for e in enrich_ossie.lint(model))


@pytest.mark.parametrize('value', ['None recorded', 'none', 'n/a', 'none recorded.'])
def test_lint_accepts_only_the_exact_none_recorded_value(value):
    model = _enriched('manufacturing')
    metric = next(m for m in model['metrics'] if m['name'] == 'inventory_units')
    _set_ext(metric, time_field=value)
    assert any(e.startswith('metric inventory_units: time_field') for e in enrich_ossie.lint(model))


def test_lint_rejects_dimension_outside_the_metric_dataset():
    model = _enriched('manufacturing')
    metric = next(m for m in model['metrics'] if m['name'] == 'sales_ordered_units')
    _set_ext(metric, allowed_dimensions=['ProductID', 'CustomerID'])
    assert 'metric sales_ordered_units: dimension CustomerID is not a field of Sales_SalesOrderDetail' in enrich_ossie.lint(model)


def test_manufacturing_concept_parents_follow_meaning():
    concepts = _concepts('manufacturing')
    assert concepts['sales_order'].get('broader') != 'purchase_commitment'
    assert concepts['unit_price'].get('broader') != 'revenue'
    assert concepts['line_discount'].get('broader') != 'unit_price'
    assert concepts['production_scrap'].get('broader') != 'scrap_quality_context'
    assert concepts['production_stocked_quantity'].get('broader') != 'production_volume'


def test_sales_ordered_units_binds_the_ordered_quantity_concept():
    assert enrich_ossie.ext(_metrics('manufacturing')['sales_ordered_units'])['concept'] == 'ordered_quantity'


def test_concept_labels_name_the_concept_not_a_metric():
    concepts = _concepts('manufacturing')
    assert concepts['work_order_status']['label'] == 'Work order status'
    assert concepts['delivery_lateness']['label'] == 'Delivery lateness'


def test_source_comment_conflicts_state_source_wording_and_observed_data():
    model = _live('manufacturing')
    desc = {(d['name'], f['name']): f['description'] for d in model['datasets'] for f in d['fields']}
    for key in (('Production_Document', 'FolderFlag'), ('Sales_SalesOrderDetail', 'UnitPriceDiscount'),
                ('Purchasing_ProductVendor', 'LastReceiptDate')):
        assert 'source comment' in desc[key].lower() and 'observed' in desc[key].lower(), key


# ---- T32: relationships are declared by the source ----

def test_t32_manufacturing_relationships_are_in_scope_source_foreign_keys():
    schema = json.loads((ROOT / 'demo_data/manufacturing/metadata/schema.json').read_text())
    declared = {(fk['name'], fk['from_table'].replace('.', '_'), fk['to_table'].replace('.', '_'),
                 tuple(fk['from_columns']), tuple(fk['to_columns']))
                for fk in schema['foreign_keys'] if fk['in_scope']}
    rels = _live('manufacturing')['relationships']
    derived = [r for r in rels if enrich_ossie.ext(r).get('derived')]
    model = {(r['name'], r['from'], r['to'], tuple(r['from_columns']), tuple(r['to_columns']))
             for r in rels if r not in derived}
    assert model <= declared
    # the one derived relationship: line to product, through SpecialOfferProduct (outside the projection), justified
    assert [(r['from'], r['to']) for r in derived] == [('Sales_SalesOrderDetail', 'Production_Product')]
    assert 'SpecialOfferProduct' in enrich_ossie.ext(derived[0])['justification']
    assert not any(r[1] == 'Sales_SalesOrderDetail' and r[2] == 'Production_Product' for r in model)


def test_t32_finance_relationships_are_the_workbook_projection_links():
    spec = importlib.util.spec_from_file_location('build_model', ROOT / 'demo_data/finance/build_model.py')
    build_model = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build_model)
    model = _live('finance')
    columns = {d['name']: {f['name'] for f in d['fields']} for d in model['datasets']}
    declared = {f'{a}_to_{b}': (a, b, [*build_model.SNAPSHOT_KEY, c]) for a, b, c in build_model.LINKS}
    # P2e: the one derived join between the two form-read tables (not a workbook link)
    declared['STR_Transaction_to_STR_AccountEntity_derived'] = ('STR_Transaction', 'STR_AccountEntity', ['SourceRevision'])
    assert {r['name']: (r['from'], r['to'], r['from_columns']) for r in model['relationships']} == declared
    for r in model['relationships']:
        assert set(r['from_columns']) <= columns[r['from']] and set(r['to_columns']) <= columns[r['to']]


@pytest.mark.parametrize('name', MODELS)
def test_t32_every_concept_reference_resolves_in_the_model(name):
    model = _live(name)
    concepts = {c['id'] for c in enrich_ossie.ext(model)['concepts']}
    refs = {enrich_ossie.ext(f).get('concept') for d in model['datasets'] for f in d['fields']}
    refs |= {enrich_ossie.ext(m).get('concept') for m in model['metrics']}
    refs.discard(None)
    assert refs and refs <= concepts


TYPOS = ('heirarchical', 'manfacturing')


def test_dataset_descriptions_have_no_typos():
    text = ' '.join(d.get('description') or '' for d in _live('manufacturing')['datasets'])
    for typo in TYPOS:
        assert typo not in text


# ---- P0: a unit that names a currency needs a recorded currency; no unit without a basis ----

def _lint_unit(name, metric_name, unit):
    model = _enriched(name)
    metric = next(m for m in model['metrics'] if m['name'] == metric_name)
    _set_ext(metric, unit=unit)
    return enrich_ossie.lint(model)


def test_lint_rejects_a_currency_unit_when_no_currency_is_recorded():
    errors = _lint_unit('manufacturing', 'sales_line_revenue', 'USD')
    assert any(e.startswith('metric sales_line_revenue: unit names currency USD') for e in errors)


def test_lint_accepts_a_currency_unit_with_a_single_valued_currency_filter():
    assert _lint_unit('finance', 'casa_transaction_value', 'USD') == []


def test_lint_rejects_a_currency_unit_on_a_field_without_a_recorded_currency():
    model = _enriched('manufacturing')
    field = next(f for d in model['datasets'] for f in d['fields'] if enrich_ossie.ext(f).get('unit'))
    _set_ext(field, unit='SGD')
    assert any('unit names currency SGD' in e for e in enrich_ossie.lint(model))


def test_manufacturing_quantities_state_that_the_unit_is_not_recorded():
    wanted = 'units (unit of measure not recorded in the source)'
    fields = {f['name']: enrich_ossie.ext(f) for d in _live('manufacturing')['datasets'] for f in d['fields']}
    for name in ('OrderQty', 'SafetyStockLevel', 'ReorderPoint', 'Quantity'):
        assert fields[name]['unit'] == wanted, name
    for data in fields.values():
        assert data.get('unit') != 'each'
