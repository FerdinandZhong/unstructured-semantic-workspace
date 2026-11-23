#!/usr/bin/env python3
"""Apply an authored semantics overlay to a base Ossie model.

Writes a review candidate and a review sheet. Never edits the base model.
Usage: enrich_ossie.py BASE.yaml OVERLAY.yaml OUT.yaml OUT.md [--schema ossie-schema.json]
"""
import argparse
import copy
import json
import re
import sys
from pathlib import Path

import yaml

VENDOR = 'HELIOS'
ROLES = {'identifier', 'dimension', 'measure', 'time', 'attribute', 'audit'}
MIN_DESCRIPTION = 25
NO_TIME_FIELD = 'none recorded'  # the only accepted value for a metric whose source has no business date


def ext(node):
    """Return the HELIOS extension of a node as a dict."""
    for item in node.get('custom_extensions') or []:
        if item.get('vendor_name') == VENDOR:
            return json.loads(item['data'])
    return {}


def _set_ext(node, data):
    others = [e for e in node.get('custom_extensions') or [] if e.get('vendor_name') != VENDOR]
    node['custom_extensions'] = others + [{'vendor_name': VENDOR, 'data': json.dumps(data, sort_keys=True)}]


def _merge_ext(node, **values):
    data = ext(node)
    data.update({k: v for k, v in values.items() if v is not None})
    if data:
        _set_ext(node, data)


def _ai(node, synonyms=None, examples=None):
    ctx = node.get('ai_context')
    ctx = {'instructions': ctx} if isinstance(ctx, str) else dict(ctx or {})
    if synonyms:
        ctx['synonyms'] = list(synonyms)
    if examples:
        ctx['examples'] = list(examples)
    if ctx:
        node['ai_context'] = ctx


def enrich(base, overlay):
    """Return a new model: base plus overlay. Raise ValueError on an unknown overlay name."""
    model = copy.deepcopy(base)
    unknown = []
    area_of = {d: a['id'] for a in overlay.get('subject_areas') or [] for d in a.get('datasets') or []}
    datasets = {d['name']: d for d in model['datasets']}
    unknown += [f'dataset {n}' for n in set(overlay.get('datasets') or {}) | set(area_of) if n not in datasets]
    for name, dataset in datasets.items():
        o = (overlay.get('datasets') or {}).get(name) or {}
        if o.get('description'):
            dataset['description'] = o['description']
        _ai(dataset, o.get('synonyms'))
        _merge_ext(dataset, label=o.get('label'), subject_area=area_of.get(name))
        fields = {f['name']: f for f in dataset.get('fields') or []}
        unknown += [f'field {name}.{n}' for n in o.get('fields') or {} if n not in fields]
        for fname, field in fields.items():
            fo = (o.get('fields') or {}).get(fname) or {}
            for key in ('label', 'description'):
                if fo.get(key):
                    field[key] = fo[key]
            _ai(field, fo.get('synonyms'), fo.get('examples'))
            _merge_ext(field, role=fo.get('role'), concept=fo.get('concept'), unit=fo.get('unit'), values=fo.get('values'))
    metrics = {m['name']: m for m in model.get('metrics') or []}
    unknown += [f'metric {n}' for n in overlay.get('metrics') or {} if n not in metrics]
    for name, metric in metrics.items():
        mo = (overlay.get('metrics') or {}).get(name) or {}
        if mo.get('description'):
            metric['description'] = mo['description']
        _ai(metric, mo.get('synonyms'), mo.get('examples'))
        _merge_ext(metric, label=mo.get('label'), scope_required=mo.get('scope_required'))
    _add_relationships(model, overlay.get('relationships') or [], unknown)
    if unknown:
        raise ValueError('overlay names not in the base model: ' + ', '.join(sorted(unknown)))
    _merge_ext(model, concepts=overlay.get('concepts'), subject_areas=overlay.get('subject_areas'),
               competency_questions=overlay.get('competency_questions'), attributions=overlay.get('attributions'))
    return model


def _add_relationships(model, declared, unknown):
    """Add or replace (by name) the relationships an overlay declares. A derived one carries its justification."""
    columns = {d['name']: {f['name'] for f in d.get('fields') or []} for d in model['datasets']}
    existing = {r['name']: n for n, r in enumerate(model.get('relationships') or [])}
    for rel in declared:
        pairs = [(rel['from'], c) for c in rel['from_columns']] + [(rel['to'], c) for c in rel['to_columns']]
        bad = [f'relationship {rel["name"]}: {t}.{c}' for t, c in pairs if c not in columns.get(t, ())]
        if bad or len(rel['from_columns']) != len(rel['to_columns']):
            unknown += bad or [f'relationship {rel["name"]}: column counts differ']
            continue
        data = {'is_default': True, 'source': 'derived_from_source_foreign_keys' if rel.get('derived') else 'declared_source_foreign_key'}
        if rel.get('derived'):
            data.update(derived=True, justification=rel['justification'])
        if rel.get('join_scope'):
            data['join_scope'] = rel['join_scope']
        item = {'name': rel['name'], 'from': rel['from'], 'to': rel['to'], 'from_columns': list(rel['from_columns']),
                'to_columns': list(rel['to_columns']),
                'custom_extensions': [{'vendor_name': VENDOR, 'data': json.dumps(data, sort_keys=True)}]}
        if rel['name'] in existing:
            model['relationships'][existing[rel['name']]] = item
        else:
            model.setdefault('relationships', []).append(item)


def _synonyms(node):
    ctx = node.get('ai_context')
    return (ctx.get('synonyms') or []) if isinstance(ctx, dict) else []


def _examples(node):
    ctx = node.get('ai_context')
    return (ctx.get('examples') or []) if isinstance(ctx, dict) else []


# ISO 4217 codes that can appear as a unit. A unit naming one needs a currency the model records.
CURRENCY_CODES = set(
    'AED AUD BRL CAD CHF CNY CZK DKK EUR GBP HKD HUF IDR ILS INR JPY KRW MXN MYR NOK NZD PHP PLN RUB SAR SEK SGD THB TRY TWD USD VND ZAR'.split())


def _currency_errors(where, unit, required, model_text):
    """A specific currency in a unit needs a single_value currency filter or the model's own statement of it."""
    single = any('currency' in key.lower() and value == 'single_value' for key, value in required.items())
    return [f'{where}: unit names currency {code} but the model records no single-valued currency for it'
            for code in sorted(set(re.findall(r'\b[A-Z]{3}\b', unit or '')) & CURRENCY_CODES)
            if not single and not re.search(rf'\b{code}\b', model_text)]


ORIGINS = {'structured', 'document', 'both'}
PREDICATES = {'exactMatch', 'closeMatch', 'broadMatch', 'narrowMatch', 'relatedMatch'}


def _local_definition(definition_id):
    """`<dataset id>:definition:<model>/field/<Dataset>/<Field>` or the part after `:definition:`."""
    return (definition_id or '').split(':definition:', 1)[-1]


def lint(model):
    """Return a list of foundation errors. An empty list means the model passes."""
    errors = []
    top = ext(model)
    concepts = {c['id']: c for c in top.get('concepts') or []}
    known = {f'{model["name"]}/field/{d["name"]}/{f["name"]}' for d in model['datasets'] for f in d.get('fields') or []}
    known |= {f'{model["name"]}/metric/{m["name"]}' for m in model.get('metrics') or []}
    areas = top.get('subject_areas') or []
    in_area = {d for a in areas for d in a.get('datasets') or []}
    bound = set()
    described = {}  # description -> field name, across datasets
    fields_of = {}
    roles_of = {}
    for dataset in model['datasets']:
        dname = dataset['name']
        fields_of[dname] = {f['name'] for f in dataset.get('fields') or []}
        roles_of[dname] = {f['name']: ext(f).get('role') for f in dataset.get('fields') or []}
        if dname not in in_area:
            errors.append(f'dataset {dname}: not in any subject area')
        if not ext(dataset).get('label'):
            errors.append(f'dataset {dname}: no label')
        if not _synonyms(dataset):
            errors.append(f'dataset {dname}: no synonyms')
        seen = {}
        for field in dataset.get('fields') or []:
            where = f'field {dname}.{field["name"]}'
            data = ext(field)
            label = field.get('label') or ''
            desc = ' '.join((field.get('description') or '').split())
            if not label or (label == field['name'] and re.search(r'[a-z][A-Z]|_', label)):
                errors.append(f'{where}: no business label')
            if len(desc) < MIN_DESCRIPTION:
                errors.append(f'{where}: description shorter than {MIN_DESCRIPTION} characters')
            elif desc in seen:
                errors.append(f'{where}: same description as {seen[desc]}')
            elif described.get(desc, field['name']) != field['name']:
                errors.append(f'{where}: same description as field {described[desc]} in another dataset')
            seen[desc] = field['name']
            described.setdefault(desc, field['name'])
            role = data.get('role')
            if role not in ROLES:
                errors.append(f'{where}: role {role!r} not in {sorted(ROLES)}')
            if role in ('dimension', 'measure') and not _synonyms(field):
                errors.append(f'{where}: {role} has no synonyms')
            if role == 'measure' and not data.get('unit'):
                errors.append(f'{where}: measure has no unit')
            errors += _currency_errors(where, data.get('unit'), {}, model.get('description') or '')
            if data.get('concept'):
                bound.add(data['concept'])
                if data['concept'] not in concepts:
                    errors.append(f'{where}: unknown concept {data["concept"]}')
    metric_dataset = {}
    joined = {frozenset((r['from'], r['to'])) for r in model.get('relationships') or []}
    for metric in model.get('metrics') or []:
        where = f'metric {metric["name"]}'
        data = ext(metric)
        metric_dataset[metric['name']] = data.get('dataset')
        for key in ('label', 'unit', 'grain', 'time_field', 'concept'):
            if not data.get(key):
                errors.append(f'{where}: no {key}')
        if data.get('concept'):
            bound.add(data['concept'])
            if data['concept'] not in concepts:
                errors.append(f'{where}: unknown concept {data["concept"]}')
        home = data.get('dataset')
        required = data.get('required_filters') or {}
        if home in fields_of:
            time_field = data.get('time_field')
            if time_field and time_field != NO_TIME_FIELD:
                other, _, field_name = time_field.rpartition('.')
                other = other or home
                reachable = other == home or frozenset((home, other)) in joined
                if not reachable or roles_of.get(other, {}).get(field_name) != 'time':
                    errors.append(f'{where}: time_field {time_field} is not a business date of {home} or of a dataset it joins to')
                dates = [k for k in required if roles_of[home].get(k) == 'time']
                if dates and dates != [time_field]:
                    errors.append(f'{where}: time_field {time_field} differs from required date filter {dates[0]}')
            for dim in data.get('allowed_dimensions') or []:
                if dim not in fields_of[home]:
                    errors.append(f'{where}: dimension {dim} is not a field of {home}')
        errors += _currency_errors(where, data.get('unit'), required, model.get('description') or '')
        if data.get('unit') == 'source currency' and required.get('Currency') != 'single_value':
            errors.append(f'{where}: unit source currency needs a single_value Currency filter')
        if not _synonyms(metric):
            errors.append(f'{where}: no synonyms')
        if not _examples(metric):
            errors.append(f'{where}: no example question')
    for cid, concept in concepts.items():
        if len(concept.get('definition') or '') < MIN_DESCRIPTION:
            errors.append(f'concept {cid}: definition shorter than {MIN_DESCRIPTION} characters')
        fibo = concept.get('fibo')
        if fibo is not None:  # a FIBO reference needs a FIBO IRI, a copied definition and a licence note
            if not str(fibo.get('iri') or '').startswith('https://spec.edmcouncil.org/fibo/ontology/'):
                errors.append(f'concept {cid}: fibo reference needs a FIBO IRI')
            for key in ('label', 'definition', 'licence', 'licence_url', 'attribution'):
                if not fibo.get(key):
                    errors.append(f'concept {cid}: fibo reference has no {key}')
        if concept.get('broader') and concept['broader'] not in concepts:
            errors.append(f'concept {cid}: unknown broader concept {concept["broader"]}')
        origin = concept.get('origin', 'structured')
        if origin not in ORIGINS:
            errors.append(f'concept {cid}: origin {origin!r} not in {sorted(ORIGINS)}')
        if cid not in bound and origin != 'document':  # a document concept may stay unlinked; that is visible, not an error
            errors.append(f'concept {cid}: bound to no field or metric')
        for link in concept.get('links') or []:
            if link.get('predicate') not in PREDICATES:
                errors.append(f'concept {cid}: link predicate {link.get("predicate")!r} not in {sorted(PREDICATES)}')
            if _local_definition(link.get('definition_id')) not in known:
                errors.append(f'concept {cid}: link to unknown definition {link.get("definition_id")}')
    for area in areas:
        for dname in area.get('datasets') or []:
            if dname not in fields_of:
                errors.append(f'subject area {area["id"]}: unknown dataset {dname}')
    relationships = {r['name']: r for r in model.get('relationships') or []}
    for name, rel in relationships.items():
        if ext(rel).get('derived') and not ext(rel).get('justification'):
            errors.append(f'relationship {name}: derived without a justification')
    for number, cq in enumerate(top.get('competency_questions') or [], 1):
        where = f'competency question {number}'
        used = set()
        for ref in cq.get('definitions') or []:
            dname, _, fname = ref.partition('.')
            if fname and fname in fields_of.get(dname, ()):
                used.add(dname)
            elif not fname and metric_dataset.get(ref):
                used.add(metric_dataset[ref])
            else:
                errors.append(f'{where}: unknown definition {ref}')
        links = []
        for name in cq.get('joins') or []:
            if name in relationships:
                links.append({relationships[name]['from'], relationships[name]['to']})
            else:
                errors.append(f'{where}: unknown relationship {name}')
        if used:
            reached = {sorted(used)[0]}
            while True:
                more = {d for link in links if link & reached for d in link} - reached
                if not more:
                    break
                reached |= more
            if used - reached:
                errors.append(f'{where}: joins do not connect {sorted(used - reached)}')
    return errors


def _cell(value):
    if isinstance(value, dict):
        value = '; '.join(f'{k} = {v}' for k, v in value.items())
    elif isinstance(value, (list, tuple)):
        value = ', '.join(map(str, value))
    return ' '.join(str(value or '').split()).replace('|', '\\|')


def review_sheet(model):
    """Return a Markdown sheet a reviewer can read instead of the YAML."""
    top = ext(model)
    out = [f'# Ossie review sheet: {model["name"]}', '',
           'Generated from the live model. Edit the overlay file, not this sheet.', '',
           '## Subject areas', '', '| Subject area | Description | Datasets |', '| --- | --- | --- |']
    out += [f'| {_cell(a.get("label"))} | {_cell(a.get("description"))} | {_cell(a.get("datasets"))} |'
            for a in top.get('subject_areas') or []]
    out += ['', '## Concepts', '', '| Concept | Definition | Synonyms | Broader |', '| --- | --- | --- | --- |']
    out += [f'| {_cell(c.get("label"))} (`{c["id"]}`) | {_cell(c.get("definition"))} | {_cell(c.get("synonyms"))} | {_cell(c.get("broader"))} |'
            for c in top.get('concepts') or []]
    out += ['', '## Metrics', '', '| Metric | Label | Expression | Unit | Grain | Time field | Concept | Description | Synonyms | Example questions |',
            '| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |']
    for m in model.get('metrics') or []:
        d = ext(m)
        expr = m['expression']['dialects'][0]['expression']
        out.append(f'| `{m["name"]}` | {_cell(d.get("label"))} | `{_cell(expr)}` | {_cell(d.get("unit"))} | {_cell(d.get("grain"))} | '
                   f'{_cell(d.get("time_field"))} | {_cell(d.get("concept"))} | {_cell(m.get("description"))} | {_cell(_synonyms(m))} | {_cell(_examples(m))} |')
    for dataset in model['datasets']:
        d = ext(dataset)
        out += ['', f'## {d.get("label") or dataset["name"]} (`{dataset["name"]}`)', '', _cell(dataset.get('description')), '',
                f'Synonyms: {_cell(_synonyms(dataset))}. Primary key: {_cell(dataset.get("primary_key"))}.', '',
                '| Field | Label | Role | Concept | Unit | Description | Synonyms | Values |', '| --- | --- | --- | --- | --- | --- | --- | --- |']
        for f in dataset.get('fields') or []:
            fd = ext(f)
            out.append(f'| `{f["name"]}` | {_cell(f.get("label"))} | {_cell(fd.get("role"))} | {_cell(fd.get("concept"))} | {_cell(fd.get("unit"))} | '
                       f'{_cell(f.get("description"))} | {_cell(_synonyms(f))} | {_cell(fd.get("values"))} |')
    out += ['', '## Competency questions', '', '| Question | Definitions | Joins |', '| --- | --- | --- |']
    out += [f'| {_cell(q.get("question"))} | {_cell(q.get("definitions"))} | {_cell(q.get("joins"))} |'
            for q in top.get('competency_questions') or []]
    return '\n'.join(out) + '\n'


def render(model):
    return yaml.safe_dump(model, sort_keys=False, allow_unicode=True, width=100)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('base'), parser.add_argument('overlay'), parser.add_argument('out_yaml'), parser.add_argument('out_md')
    parser.add_argument('--schema', help='Ossie JSON schema to validate against')
    args = parser.parse_args(argv)
    model = enrich(yaml.safe_load(Path(args.base).read_text()), yaml.safe_load(Path(args.overlay).read_text()))
    if args.schema:
        import jsonschema
        jsonschema.Draft202012Validator(json.loads(Path(args.schema).read_text())).validate(model)
    Path(args.out_yaml).write_text(render(model))
    Path(args.out_md).write_text(review_sheet(model))
    errors = lint(model)
    print('\n'.join(errors) if errors else 'lint passed')
    print(f'{len(errors)} lint errors')
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
