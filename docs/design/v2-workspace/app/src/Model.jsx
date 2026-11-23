// Model: the semantic model as a table-relationship diagram. One card per table, lines for declared relationships.
import {useMemo, useState} from 'react';
import {Handle, Position} from '@xyflow/react';
import {buildModel, isSystemField, useLoad, loadedRows, counted, t} from './lib.js';
import Flow from './Flow.jsx';
import {Empty, Tag} from './ui.jsx';

const ROW = 22;
const visibleFields = (table, system) => table.fields.filter(field => system || !field.system);

export function TableNode({data, selected}) {
  const {table, open, system} = data;
  const fields = visibleFields(table, system);
  return <div className={'pf-table-node' + (selected ? ' selected' : '') + (table.fromDocuments ? ' documents' : '')} data-node-kind="table">
    <Handle type="target" position={Position.Left} />
    <div className="pf-table-head">
      <strong>{table.label}</strong>
      <span>{table.rows != null ? `${counted(table.rows, 'row')} · ` : ''}{counted(fields.length, 'field')}{table.metrics.length ? ` · ${counted(table.metrics.length, 'metric')}` : ''}</span>
      {table.fromDocuments && <span className="pf-doc-mark" title={(data.loaded?.forms || []).join(', ')}>{data.loaded
        ? t('{rows} loaded from {forms}', {rows: t(data.loaded.rows === 1 ? '{count} row' : '{count} rows', {count: data.loaded.rows}), forms: t(data.loaded.forms.length === 1 ? '{count} form' : '{count} forms', {count: data.loaded.forms.length})})
        : t('no rows yet · loaded from forms')}</span>}
    </div>
    {open && <ul className="pf-table-fields">{fields.map(field => <li key={field.id} className={field.system ? 'system' : ''}>
      <span>{field.title}</span>{table.key.includes(field.column) && <em>{t('key')}</em>}</li>)}</ul>}
    <Handle type="source" position={Position.Right} />
  </div>;
}

// One relationship line, selected on the canvas: both tables, the join columns, and a button to open each table.
function Relationship({model, id, onOpen}) {
  const rels = model.relationships.filter(rel => rel.from + '>' + rel.to === id);
  if (!rels.length) return null;
  const from = model.tables.get(rels[0].from), to = model.tables.get(rels[0].to);
  const column = (table, name) => table.fields.find(field => field.column === name)?.title || name;
  return <div className="pf-relationship" data-relationship>
    <div className="pf-side-head"><h2>{from.label} → {to.label}</h2></div>
    <p className="pf-muted">{t('Relationship: {from} joins to {to}.', {from: from.label, to: to.label})}</p>
    <ul className="pf-plain">{rels.map(rel => <li key={rel.id}>{t('Joined on {columns}', {columns: rel.columns.map(name => column(from, name)).join(', ') || t('snapshot keys')})}</li>)}</ul>
    <div className="pf-actions">
      <button type="button" className="button secondary small" onClick={() => onOpen(from.name)}>{t('Open {label}', {label: from.label})}</button>
      <button type="button" className="button secondary small" onClick={() => onOpen(to.name)}>{t('Open {label}', {label: to.label})}</button></div>
  </div>;
}

export default function Model({ctx}) {
  const model = useMemo(() => buildModel(ctx.workspace), [ctx.workspace]);
  const [open, setOpen] = useState(() => new Set(ctx.detail ? [ctx.detail] : []));   // a link may name a table (Ask's path view)
  const [system, setSystem] = useState(false);
  const [selected, setSelected] = useState(ctx.detail || '');
  const [relation, setRelation] = useState(null);      // a relationship line selected on the canvas
  const tables = [...model.tables.values()];
  const loaded = useLoad(() => loadedRows(ctx), [ctx.dataset]);
  const graph = useMemo(() => {
    const nodes = tables.map(table => {
      const isOpen = open.has(table.name);
      const height = (table.fromDocuments ? 78 : 60) + (isOpen ? visibleFields(table, system).length * ROW + 10 : 0);
      return {id: table.name, type: 'pfTable', width: 230, height, data: {table, open: isOpen, system, loaded: loaded.data?.[table.name]}};
    });
    const seen = new Set();
    const edges = model.relationships.filter(rel => {
      const key = rel.from + '>' + rel.to;
      return !seen.has(key) && seen.add(key);
    }).map(rel => ({id: rel.from + '>' + rel.to, source: rel.from, target: rel.to}));
    return {nodes, edges};
  }, [model, open, system, loaded.data]);
  if (!tables.length) return <Empty title={t('No semantic model')} text={t('Import the structured model on the Sources step first.')} href={ctx.href('ingest')} action={t('Open Sources')} />;

  const table = model.tables.get(selected);
  const pick = name => { setRelation(null); setSelected(name); };
  const toggle = node => {
    pick(node.id);
    setOpen(previous => { const next = new Set(previous); next.has(node.id) ? next.delete(node.id) : next.add(node.id); return next; });
  };
  const withMetrics = tables.filter(item => item.metrics.length);
  const joins = table ? model.relationships.filter(rel => rel.from === table.name || rel.to === table.name) : [];
  return <div className="pf-page">
    <div className="pf-toolbar">
      <span className="pf-muted grow">{counted(tables.length, 'table')} · {counted(model.relationships.length, 'relationship')} · {counted(model.metrics.length, 'metric')}{t('.')} {t('Select a table to open its fields, or a line to see its relationship.')}</span>
      <label className="pf-check"><input type="checkbox" checked={system} onChange={event => setSystem(event.target.checked)} /> {t('Show system fields')}</label>
      <button type="button" className="button secondary small" onClick={() => setOpen(new Set())}>{t('Fold all')}</button>
    </div>
    <div className="pf-split wide">
      <Flow nodes={graph.nodes} edges={graph.edges} nodeTypes={ctx.nodeTypes} onSelect={toggle} nodesep={14} label={t('Table relationship diagram')}
        routed onSelectEdge={edge => setRelation(edge.id)} />
      <aside className="pf-side" aria-label={relation ? t('Selected relationship') : table ? t('Selected table') : t('Metrics')}>
        {relation ? <Relationship model={model} id={relation} onOpen={pick} /> : table ? <>
          <div className="pf-side-head"><h2>{table.label}</h2>
            <button type="button" className="button secondary small" onClick={() => pick('')}>{t('All metrics')}</button></div>
          {table.fromDocuments && <Tag tone="amber">{t('Filled from documents')}</Tag>}
          <p className="pf-prose">{table.description}</p>
          <dl className="pf-kv"><div><dt>{t('Key')}</dt><dd>{table.key.filter(column => system || !isSystemField(column)).map(column => table.fields.find(field => field.column === column)?.title || column).join(', ')}</dd></div>
            {table.rows != null && <div><dt>{t('Rows')}</dt><dd>{table.rows}</dd></div>}</dl>
          {joins.length > 0 && <><h3>{t('Relationships')}</h3><ul className="pf-plain">{joins.map(rel => <li key={rel.id}>
            <button type="button" className="pf-link-button" onClick={() => pick(rel.from === table.name ? rel.to : rel.from)}>
              {model.tables.get(rel.from).label} → {model.tables.get(rel.to).label}</button>
            <p className="pf-muted">{t('on {columns}', {columns: rel.columns.map(column => model.tables.get(rel.from).fields.find(field => field.column === column)?.title || column).join(', ') || t('snapshot keys')})}</p></li>)}</ul></>}
          {table.metrics.length > 0 && <><h3>{t('Metrics')}</h3><ul className="pf-plain">{table.metrics.map(metric => <li key={metric.id}><strong>{metric.title}</strong><p className="pf-muted">{metric.description}</p></li>)}</ul></>}
          <h3>{t('Fields')}</h3><ul className="pf-plain">{visibleFields(table, system).map(field => <li key={field.id}><strong>{field.title}</strong>
            {field.system && <Tag>{t('system')}</Tag>}<p className="pf-muted">{field.description}</p></li>)}</ul>
        </> : <>
          <h2>{t('Metrics')}</h2>
          {withMetrics.map(item => <section key={item.name}><button type="button" className="pf-link-button" onClick={() => pick(item.name)}>{item.label}</button>
            <ul className="pf-plain">{item.metrics.map(metric => <li key={metric.id}><strong>{metric.title}</strong><p className="pf-muted">{metric.description}</p></li>)}</ul></section>)}
        </>}
      </aside>
    </div>
  </div>;
}
