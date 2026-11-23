from __future__ import annotations
from semantic_pipeline_v2.model_cache import load_yaml
import hashlib
import json
import os
import re
import sqlite3
import struct
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Protocol
import yaml


# Source checkout layout: this package lives directly under its V2 repository.
# Additional external checkouts must be supplied through State.create(protected=...).
_REPOSITORY_PARENT = Path(__file__).resolve().parents[2]


def v1_repository():
    """The optional V1 checkout: `V2_V1_REPO`, else the single sibling named `*-knowledge-journey`.
    # ponytail: suffix discovery keeps the sibling's name out of the public code; set V2_V1_REPO when several match."""
    if os.environ.get('V2_V1_REPO'):
        return Path(os.environ['V2_V1_REPO'])
    found = sorted(_REPOSITORY_PARENT.glob('*-knowledge-journey'))
    return found[0] if len(found) == 1 else _REPOSITORY_PARENT / 'v1-knowledge-journey'


PROTECTED_ROOTS = (v1_repository(), _REPOSITORY_PARENT / 'helios')


def _validate_metric_expr(expr_str: str, tables: dict, defined_metrics: set) -> set:
    """Validate a metric expression using sqlglot. Returns the set of referenced tables.

    Accepts:
    - SUM, COUNT, COUNT DISTINCT, AVG, MIN, MAX over a qualified table.column
    - Any of the above with a single-table CASE WHEN as the aggregate argument
    - metric_a / metric_b (ratio of two already-declared metric names)

    Rejects subqueries, unknown functions, multi-table CASE WHEN, and dangling
    table or column references.
    """
    try:
        import sqlglot
        import sqlglot.expressions as exp
    except ImportError:
        raise ValueError('sqlglot is required for metric expression validation')

    _ALLOWED_AGGS = (exp.Sum, exp.Count, exp.Avg, exp.Min, exp.Max)

    try:
        stmt = sqlglot.parse_one(f"SELECT {expr_str}")
    except Exception as e:
        raise ValueError(f'metric expression parse error: {e}')

    if not isinstance(stmt, exp.Select) or len(stmt.expressions) != 1:
        raise ValueError('unsupported metric expression structure')

    node = stmt.expressions[0]

    # Reject subqueries anywhere in the expression tree
    for n in node.walk():
        if isinstance(n, exp.Subquery):
            raise ValueError('subqueries are not allowed in metric expressions')

    # Pattern 1: ratio of two declared metrics — metric_a / metric_b
    if isinstance(node, exp.Div):
        left, right = node.left, node.right
        if (isinstance(left, exp.Column) and not left.table and
                isinstance(right, exp.Column) and not right.table):
            ln, rn = left.name, right.name
            if ln not in defined_metrics:
                raise ValueError(f'ratio references unknown metric {ln!r}')
            if rn not in defined_metrics:
                raise ValueError(f'ratio references unknown metric {rn!r}')
            return set()
        raise ValueError('ratio operands must be unqualified declared metric names')

    # Pattern 2: allowlisted aggregate function
    if not isinstance(node, _ALLOWED_AGGS):
        raise ValueError('unsupported function; allowed: SUM, COUNT, AVG, MIN, MAX, or metric ratio')

    agg_arg = node.this

    # COUNT(DISTINCT table.col) or COUNT(DISTINCT CASE WHEN ... THEN table.col END)
    if isinstance(agg_arg, exp.Distinct):
        inner = agg_arg.expressions
        if len(inner) != 1:
            raise ValueError('COUNT DISTINCT requires exactly one argument')
        if isinstance(inner[0], exp.Column):
            return _check_col(inner[0], tables)
        if isinstance(inner[0], exp.Case):
            return _check_case(inner[0], tables)
        raise ValueError('COUNT DISTINCT argument must be a qualified column or a single-table CASE WHEN')

    # agg(table.col)
    if isinstance(agg_arg, exp.Column):
        return _check_col(agg_arg, tables)

    # agg(CASE WHEN table.col <cond> THEN <val> [ELSE <val>] END)
    if isinstance(agg_arg, exp.Case):
        return _check_case(agg_arg, tables)

    raise ValueError('unsupported aggregate argument; expected table.column, DISTINCT, or CASE WHEN')


def _check_col(col_node, tables: dict) -> set:
    """Validate a qualified column reference. Returns {table_name}."""
    table = col_node.table
    column = col_node.name
    if not table:
        raise ValueError(f'column {column!r} must be qualified with a table name')
    if table not in tables:
        raise ValueError(f'unknown table {table!r} in metric expression')
    if column not in tables[table]:
        raise ValueError(f'unknown column {column!r} in table {table!r}')
    return {table}


def _check_case(case_node, tables: dict) -> set:
    """Validate a CASE WHEN inside an aggregate (single-table rule). Returns referenced tables."""
    try:
        import sqlglot.expressions as exp
    except ImportError:
        raise ValueError('sqlglot is required for CASE WHEN validation')
    referenced: set = set()
    for n in case_node.walk():
        if isinstance(n, exp.Column) and n.table:
            tbl, col = n.table, n.name
            if tbl not in tables:
                raise ValueError(f'unknown table {tbl!r} in CASE WHEN expression')
            if col not in tables[tbl]:
                raise ValueError(f'unknown column {col!r} in table {tbl!r}')
            referenced.add(tbl)
    if len(referenced) > 1:
        raise ValueError('CASE WHEN in aggregate must reference at most one single table')
    return referenced


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_json(path: Path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, sort_keys=True))
    temporary.replace(path)


@dataclass(frozen=True)
class State:
    root: Path
    instance: str

    @classmethod
    def create(cls, root, instance, protected):
        if not instance or not re.fullmatch(r'[a-zA-Z0-9_-]+', instance):
            raise ValueError('explicit safe instance identity required')
        root = Path(root)
        if not root.is_absolute() or not protected:
            raise ValueError('absolute root and protected roots required')
        resolved = root.resolve()
        for item in (*PROTECTED_ROOTS, *protected):
            item = Path(item).resolve()
            if resolved == item or resolved in item.parents or item in resolved.parents:
                raise ValueError('protected path overlap')
        if resolved.exists() and any(path.is_symlink() for path in resolved.rglob('*')):
            raise ValueError('state tree contains symlink escape')
        identity = resolved / 'instance.json'
        if identity.exists() and json.loads(identity.read_text()) != {'instance': instance}:
            raise ValueError('state belongs to another instance')
        resolved.mkdir(parents=True, exist_ok=True)
        atomic_json(identity, {'instance': instance})
        return cls(resolved, instance)


def _semantics(node):
    """Label, synonyms, examples and HELIOS role/concept/unit/values of one Ossie node."""
    data = {}
    for item in node.get('custom_extensions') or []:
        if item.get('vendor_name') == 'HELIOS':
            raw = item.get('data')
            data = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
    ctx = node.get('ai_context')
    ctx = ctx if isinstance(ctx, dict) else {}
    return {'label': node.get('label') or data.get('label'),
            'synonyms': list(ctx.get('synonyms') or []), 'examples': list(ctx.get('examples') or []),
            'role': data.get('role'), 'concept': data.get('concept'),
            'unit': data.get('unit'), 'values': data.get('values')}


def normalize(raw: bytes, checksum: str):
    if digest(raw) != checksum:
        raise ValueError('checksum mismatch')
    model = load_yaml(raw)
    if not isinstance(model, dict) or model.get('version') != '0.2.0.dev0':
        raise ValueError('unsupported Ossie version')
    allowed = {'version','name','description','ai_context','datasets','relationships','metrics','custom_extensions'}
    if set(model) - allowed:
        raise ValueError('unsupported model feature')
    namespace = model['name']
    if not isinstance(namespace,str) or not re.fullmatch(r'[A-Za-z_][\w]*',namespace):
        raise ValueError('invalid model namespace')
    records, ids, tables = [], set(), {}
    def record(kind, name, description, expression=None, metadata=None):
        source_id = f'{namespace}/{kind}/{name}'
        if source_id in ids or not isinstance(description, str) or not description.strip():
            raise ValueError('duplicate ID or empty definition')
        ids.add(source_id)
        records.append({'source_id':source_id,'revision':checksum,'definition':description,
                        'expression':expression,'execution':'metadata_only','source_metadata':metadata or {},
                        'semantics':_semantics(metadata or {})})
    def expression(value, metric=False):
        if not isinstance(value, dict) or set(value) != {'dialects'}:
            raise ValueError('unsupported expression structure')
        entries=value['dialects']
        if not isinstance(entries,list) or not entries:
            raise ValueError('missing expression')
        for entry in entries:
            if set(entry) != {'dialect','expression'} or entry['dialect'] != 'ANSI_SQL':
                raise ValueError('unsupported expression dialect')
            if not metric and not re.fullmatch(r'[A-Za-z_]\w*',entry['expression']):
                raise ValueError('unsupported expression')
        return entries
    for table in model.get('datasets',[]):
        if set(table)-{'name','source','description','ai_context','primary_key','fields','custom_extensions'}:
            raise ValueError('unsupported dataset feature')
        if not re.fullmatch(r'[A-Za-z_][\w]*',table['name']):
            raise ValueError('invalid dataset identity')
        record('dataset',table['name'],table.get('description'),metadata=table)
        fields=set()
        for field in table['fields']:
            if set(field)-{'name','expression','label','description','ai_context','datatype','custom_extensions','dimension'}:
                raise ValueError('unsupported field feature')
            if not re.fullmatch(r'[A-Za-z_][\w]*',field['name']):
                raise ValueError('invalid field identity')
            record('field',f"{table['name']}/{field['name']}",field.get('description'),expression(field['expression']),metadata=field)
            fields.add(field['name'])
        if not set(table.get('primary_key',[])) <= fields:
            raise ValueError('dangling primary key')
        tables[table['name']]=fields
    for rel in model.get('relationships',[]):
        if set(rel)-{'name','from','to','from_columns','to_columns','custom_extensions'}:
            raise ValueError('unsupported relationship feature')
        if not rel.get('from_columns') or len(rel['from_columns']) != len(rel['to_columns']):
            raise ValueError('invalid relationship columns')
        for side in ('from','to'):
            if rel[side] not in tables or not set(rel[f'{side}_columns']) <= tables[rel[side]]:
                raise ValueError('dangling relationship')
        record('relationship',rel['name'],f"{rel['from']} to {rel['to']}",metadata=rel)
    # Pre-collect all metric names so ratios can reference any declared metric.
    all_metric_names = {m['name'] for m in model.get('metrics', [])}
    for metric in model.get('metrics',[]):
        if set(metric)-{'name','expression','description','ai_context','datatype','custom_extensions'}:
            raise ValueError('unsupported metric feature')
        expressions=expression(metric['expression'],True)
        metric_tables = set()
        for entry in expressions:
            metric_tables.update(_validate_metric_expr(entry['expression'], tables, all_metric_names))
        record('metric',metric['name'],metric.get('description'),expressions,metadata=metric)
        records[-1]['input_tables'] = sorted(metric_tables)
    if not records:
        raise ValueError('empty model')
    return {'revision':checksum,'namespace':namespace,'records':records,
            'status':'candidate','unsupported_metadata':{'ai_context':model.get('ai_context'),
            'custom_extensions':'preserved in source_metadata and hash-addressed original; not interpreted',
            'datatype_dimension_label_primary_key':'preserved as metadata; no runtime type, grain, key or unit enforcement' }}


class Packages:
    def __init__(self,state:State):
        self.root=state.root/'packages'; self.root.mkdir(exist_ok=True)
    def activate(self,raw,checksum,*,qualification_only=False,approval=None):
        package=normalize(raw,checksum)
        if not qualification_only:
            if not isinstance(approval,dict) or approval.get('revision') != checksum or not approval.get('reviewer') or approval.get('decision') != 'approved':
                raise ValueError('separate matching approval required')
            package['status']='approved'
        package['activation_mode']='qualification_only' if qualification_only else 'approved'
        (self.root/f'{checksum}.yaml').write_bytes(raw)
        if approval is not None:
            atomic_json(self.root/f'{checksum}.approval.json',approval)
        atomic_json(self.root/'active.json',package)
        return package
    def active(self):
        return json.loads((self.root/'active.json').read_text())


@dataclass(frozen=True)
class EmbeddingSpace:
    model:str
    revision:str
    dimension:int
    purpose:str
    def __post_init__(self):
        if not self.model or not self.revision or not self.purpose or self.dimension < 1:
            raise ValueError('complete embedding identity required')
    @property
    def identity(self):
        return digest(json.dumps(asdict(self),sort_keys=True).encode())


class Evidence:
    """One writer, local LanceDB. Scoped IDs include artifact hash and unit position."""
    def __init__(self,state:State,space:EmbeddingSpace):
        import lancedb
        self.space=space; self.root=state.root
        self.raw=self.root/'raw'; self.raw.mkdir(exist_ok=True)
        manifest=self.root/'embedding-space.json'
        if manifest.exists() and json.loads(manifest.read_text()) != asdict(space):
            raise ValueError('embedding space mismatch')
        self.db=lancedb.connect(str(self.root/'lance'))
        import pyarrow as pa
        schema=pa.schema([('id',pa.string()),('scope',pa.string()),('raw_hash',pa.string()),
                          ('position',pa.int64()),('text',pa.string()),('space',pa.string()),
                          ('vector',pa.list_(pa.float32(),space.dimension)),('feature',pa.string()),('provenance',pa.string())])
        self.table=self.db.create_table('evidence',schema=schema,exist_ok=True)
        for field in schema:
            if field.name not in self.table.schema.names or self.table.schema.field(field.name).type != field.type:
                raise ValueError('persisted evidence schema mismatch')
        if self.table.count_rows() and not manifest.exists():
            raise ValueError('populated evidence has no embedding identity manifest')
        if self.table.count_rows(f"space IS NULL OR space != '{space.identity}'"):
            raise ValueError('persisted row embedding space mismatch')
        atomic_json(manifest,asdict(space))
    def vector(self,vector,space):
        import math
        if space != self.space or len(vector)!=space.dimension or not all(math.isfinite(x) for x in vector):
            raise ValueError('wrong embedding space or dimension')
    def put(self,scope,raw,position,text,vector,space):
        self.vector(vector,space)
        if not scope or not text or position<0 or text.encode() not in raw:
            raise ValueError('invalid scoped evidence')
        raw_hash=digest(raw)
        unit_id=digest(json.dumps([scope,raw_hash,position],separators=(',',':')).encode())
        row={'id':unit_id,'scope':scope,'raw_hash':raw_hash,'position':position,'text':text,
             'space':space.identity,'vector':vector,'feature':'','provenance':''}
        existing=self.table.search().where(f"id = '{unit_id}'").to_list()
        if existing:
            if existing[0]['text'] != text or existing[0]['vector'] != [struct.unpack('f',struct.pack('f',x))[0] for x in vector]:
                raise ValueError('retry content conflict')
            return unit_id
        (self.raw/raw_hash).write_bytes(raw)
        self.table.add([row]); return unit_id
    def search(self,scope,vector,space,limit=20):
        self.vector(vector,space)
        if not scope or not 1<=limit<=20:
            raise ValueError('scope and bounded limit required')
        escaped=scope.replace("'","''")
        return self.table.search(vector).where(f"scope = '{escaped}' AND space = '{space.identity}'",prefilter=True).limit(limit).to_list()
    def add_derived_columns(self):
        if 'derived_concept' in self.table.schema.names or 'derived_provenance' in self.table.schema.names:
            raise ValueError('derived schema already added')
        self.table.add_columns({'derived_concept':'CAST(NULL AS STRING)',
                                'derived_provenance':'CAST(NULL AS STRING)'})

    def derive(self,unit_id,feature,provenance):
        if not feature or not provenance:
            raise ValueError('feature provenance required')
        if not re.fullmatch('[a-f0-9]{64}',unit_id):
            raise ValueError('invalid unit ID')
        if not self.table.search().where(f"id = '{unit_id}'").to_list():
            raise ValueError('unknown unit')
        values={'feature':feature,'provenance':provenance}
        if 'derived_concept' in self.table.schema.names:
            values.update(derived_concept=feature,derived_provenance=provenance)
        self.table.update(where=f"id = '{unit_id}'",values=values)


class DiscoveryProvider(Protocol):
    def propose(self,evidence:list[dict])->list[dict]: ...


class Discovery:
    def __init__(self):
        self.proposals={}
    def discover(self,selected,provider:DiscoveryProvider):
        evidence=[{'unit_id':row['id'],'text':row['text']} for row in selected]
        indexed={row['unit_id']:row['text'] for row in evidence}
        pending={}
        for proposal in provider.propose(evidence):
            if set(proposal) != {'name','definition','unit_id','quote'} or not proposal['name'].strip() or not proposal['definition'].strip():
                raise ValueError('invalid open vocabulary proposal')
            if proposal['unit_id'] not in indexed or not proposal['quote'] or proposal['quote'] not in indexed[proposal['unit_id']]:
                raise ValueError('invalid quote')
            key=digest(json.dumps([proposal['name'],proposal['unit_id']],sort_keys=True).encode())
            previous=pending.get(key,self.proposals.get(key))
            if previous is not None and previous != proposal:
                raise ValueError('proposal replay conflict')
            pending[key]=proposal
        self.proposals.update(pending)
        return [proposal for proposal in self.proposals.values() if proposal['unit_id'] in indexed]


class SQLiteFacts:
    def __init__(self,path): self.path=Path(path)
    def ordered_units(self,customer,product,start,end):
        if not self.path.is_file():
            return {'status':'unavailable','reason':'SQLite fixture missing','value':None}
        try:
            with sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True) as conn:
                conn.execute('PRAGMA query_only=ON')
                row=conn.execute('''SELECT COALESCE(SUM(d.OrderQty),0) FROM Sales_SalesOrderDetail d
                  JOIN Sales_SalesOrderHeader h ON h.SalesOrderID=d.SalesOrderID
                  WHERE h.CustomerID=? AND d.ProductID=? AND h.OrderDate>=? AND h.OrderDate<?''',
                  (customer,product,start,end)).fetchone()
            return {'status':'available','value':row[0],'backend':'read-only SQLite qualification fixture'}
        except sqlite3.Error:
            return {'status':'unavailable','reason':'SQLite query unavailable','value':None}
