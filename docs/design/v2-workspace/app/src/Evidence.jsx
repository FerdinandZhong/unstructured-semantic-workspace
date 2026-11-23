// Evidence (Output A, and Output C for forms): each passage beside the table fields it links to.
import {useEffect, useMemo, useState} from 'react';
import {allLinks, buildModel, getChunks, forget, useLoad, shortSource, RELATION, HOW, dataLabel, counted, t, serverText} from './lib.js';
import Flow from './Flow.jsx';
import {Seg, Empty, Tag, Loading, Problem} from './ui.jsx';
import FormRows from './FormRows.jsx';
import ApproveAll from './ApproveAll.jsx';

const STATUS = {proposed: 'Not reviewed', corrected: 'Corrected', rejected: 'Removed', degraded: 'Degraded', not_processed: 'Not processed'};
// A person approved a link without changing it (`changed` is empty) or corrected its target or value.
const statusWord = link => link.status === 'corrected' && Array.isArray(link.changed) && !link.changed.length ? 'Approved' : STATUS[link.status] || link.status;
const needsReview = link => link.status !== 'corrected' && link.status !== 'rejected';
const rawDefinition = link => link.notes?.originalDefinitionId ?? link.definition_id;

function entityText(model, entry) {
  const [left, value] = String(entry.key).split('=');
  const [table, column] = left.split('.');
  const field = model.tables.get(table)?.fields.find(item => item.column === column);
  return {name: field ? field.label : left, value};
}

function LinkEditor({ctx, model, link, onDone}) {
  const [form, setForm] = useState({definitionId: link.definition_id || '', relation: link.relation,
    value: link.stated_value || '', unit: link.unit || '', period: link.period || ''});
  const [state, setState] = useState({});
  const set = key => event => setForm({...form, [key]: event.target.value});
  const choices = [...model.tables.values()].map(table => ({table,
    items: [...table.fields.filter(field => !field.system), ...table.metrics]})).filter(group => group.items.length);
  async function save(event) {
    event.preventDefault();
    const patch = {relation: form.relation, statedValue: form.value.trim() || null, unit: form.unit.trim() || null,
      period: form.period.trim() || null};
    if ((form.definitionId || null) !== (link.definition_id || null)) patch.definitionId = form.definitionId || null;
    setState({busy: true});
    try {
      await ctx.request('/api/catalog-correction', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({dataset: ctx.dataset, kind: 'evidence_link', chunkId: link.chunk_id,
          definitionId: rawDefinition(link) ?? null, patch})});
      onDone(true);
    } catch (error) { setState({error: error.message}); }
  }
  return <form className="pf-editor" onSubmit={save}>
    <label>{t('Field')}<select value={form.definitionId} onChange={set('definitionId')}>
      <option value="">{t('No link')}</option>
      {choices.map(group => <optgroup key={group.table.id} label={group.table.label}>
        {group.items.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</optgroup>)}
    </select></label>
    <label>{t('Relation')}<select value={form.relation} onChange={set('relation')}>
      {Object.entries(RELATION).map(([value, text]) => <option key={value} value={value}>{t(text)}</option>)}</select></label>
    <div className="pf-editor-row">
      <label>{t('Stated value')}<input value={form.value} maxLength={200} onChange={set('value')} /></label>
      <label>{t('Unit')}<input value={form.unit} maxLength={200} onChange={set('unit')} /></label>
      <label>{t('Period')}<input value={form.period} maxLength={200} onChange={set('period')} /></label>
    </div>
    {state.error && <p className="pf-error" role="alert">{serverText(state.error)}</p>}
    <div className="pf-actions"><button type="submit" className="button primary" disabled={state.busy}>{state.busy ? t('Saving…') : t('Save correction')}</button>
      <button type="button" className="button secondary" onClick={() => onDone(false)}>{t('Cancel')}</button></div>
  </form>;
}

function LinkCard({ctx, model, link, onSaved}) {
  const [editing, setEditing] = useState(false);
  const [state, setState] = useState({});
  const none = link.relation === 'none' || !link.definition_id;
  async function remove() {
    setState({busy: true});
    try {
      await ctx.request('/api/catalog-correction', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({dataset: ctx.dataset, kind: 'evidence_link', chunkId: link.chunk_id,
          definitionId: rawDefinition(link) ?? null, patch: {status: 'rejected'}})});
      onSaved();
    } catch (error) { setState({error: error.message}); }
  }
  if (editing) return <li className="pf-link editing"><LinkEditor ctx={ctx} model={model} link={link}
    onDone={saved => { setEditing(false); if (saved) onSaved(); }} /></li>;
  return <li className={'pf-link' + (none ? ' none' : '') + (link.status === 'rejected' ? ' removed' : '')} data-link>
    <div className="pf-link-head">
      <strong className="pf-target">{none ? t('No link') : model.label(link.definition_id)}</strong>
      {!none && <Tag tone={link.relation === 'states_value_of' ? 'blue' : ''}>{t(RELATION[link.relation] || link.relation)}</Tag>}
      {link.status !== 'proposed' && <Tag tone={link.status === 'corrected' ? 'green' : link.status === 'rejected' ? 'red' : ''}>{t(statusWord(link))}</Tag>}
    </div>
    {link.stated_value != null && <p className="pf-value"><span>{t('Stated value')}</span><code>{[link.stated_value, link.unit].filter(Boolean).join(' ')}</code>{link.period && <small>{link.period}</small>}</p>}
    {none && link.notes?.reason && <p className="pf-muted">{serverText(link.notes.reason)}</p>}
    {link.status === 'corrected' && <p className="pf-muted">{t('{status} by {actor}', {status: t(statusWord(link)), actor: link.actor})} · {String(link.recorded_at || '').slice(0, 16).replace('T', ' ')}</p>}
    {state.error && <p className="pf-error" role="alert">{serverText(state.error)}</p>}
    {ctx.readOnly ? null : <div className="pf-actions">
      <button type="button" className="button secondary small" onClick={() => setEditing(true)}>{t('Edit')}</button>
      {!none && link.status !== 'rejected' && <button type="button" className="button secondary small" disabled={state.busy} onClick={remove}>{t('Remove')}</button>}
    </div>}
  </li>;
}

// The entities a passage is about, once per passage. A key stated anywhere in the passage shows as stated.
function Entities({model, links}) {
  const rank = {stated: 0, named: 1, labelled: 1, derived: 2, inherited: 3};
  const keys = new Map();
  links.flatMap(link => link.entity_keys || []).forEach(entry => {
    const known = keys.get(entry.key);
    if (!known || (rank[entry.how] ?? 9) < (rank[known.how] ?? 9)) keys.set(entry.key, entry);
  });
  if (!keys.size) return null;
  return <div className="pf-about"><span className="pf-locator">{t('About')}</span><ul className="pf-entities">{[...keys.values()].map(entry => {
    const {name, value} = entityText(model, entry);
    return <li key={entry.key} title={entry.note || ''}><span>{name}</span> <code>{value}</code> <em>{t(HOW[entry.how] || entry.how)}</em></li>;
  })}</ul></div>;
}

const ALL = 'all';
// 'No link' and 'Other' are group names the code compares in English; every other name is a table label (data).
const tt = name => name === 'No link' || name === 'Other' ? t(name) : name;

function tableOf(model, link) {
  if (link.relation === 'none' || !link.definition_id) return 'No link';
  return model.defs.get(link.definition_id)?.table?.label || 'Other';
}

export default function Evidence({ctx}) {
  const model = useMemo(() => buildModel(ctx.workspace), [ctx.workspace]);
  const links = useLoad(() => allLinks(ctx), [ctx.dataset]);
  const sources = ctx.workspace.sources || [];
  const [view, setView] = useState(ctx.observation ? 'reading' : 'graph');   // graph first; a link to one passage opens the reading view
  const [relation, setRelation] = useState('all');
  const [review, setReview] = useState(false);
  const [table, setTable] = useState('');
  const bySource = useMemo(() => {
    const map = new Map();
    (links.data || []).forEach(link => map.set(link.source_id, [...(map.get(link.source_id) || []), link]));
    return map;
  }, [links.data]);
  const processed = sources.filter(source => bySource.has(source.id));
  const requested = ctx.detail && processed.find(source => shortSource(source.id) === ctx.detail);
  const [sourceId, setSourceId] = useState('');
  // Open on the first document with a linked passage (a readable letter, not a raw form dump), unless a link named one.
  const linked = item => bySource.get(item.id).some(link => link.definition_id);
  const richest = (processed.find(item => linked(item) && item.kind !== 'pdf') || processed.find(linked) || processed[0])?.id;
  // No source chosen: the overall view across every processed source. A passage link opens its own source.
  const current = sourceId || (requested ? requested.id : ctx.observation ? richest : ALL);
  const overall = current === ALL;
  const chunks = useLoad(() => current && !overall ? getChunks(ctx, current) : Promise.resolve([]), [current]);

  if (links.error) return <Problem text={serverText(links.error)} />;
  if (links.loading && !links.data) return <Loading text={t('Reading the evidence links…')} />;
  if (!processed.length) return <Empty title={t('No evidence yet')} text={t('Process a source on the Sources step first. Its passages and links appear here.')} href={ctx.href('ingest')} action={t('Open Sources')} />;

  const mine = overall ? links.data || [] : bySource.get(current) || [];
  const keep = link => (relation === 'all' || link.relation === relation || (relation === 'none' && !link.definition_id))
    && (!review || needsReview(link)) && (!table || tableOf(model, link) === table);
  const summary = new Map();
  mine.forEach(link => summary.set(tableOf(model, link), (summary.get(tableOf(model, link)) || 0) + 1));
  const ordered = [...summary].sort((a, b) => (a[0] === 'No link') - (b[0] === 'No link') || b[1] - a[1]);
  const chunkRows = (chunks.data || []).slice().sort((a, b) => Number(a.order) - Number(b.order));
  const passages = chunkRows.map(chunk => ({chunk, links: mine.filter(link => link.chunk_id === chunk.chunk_id)}))
    .filter(item => item.links.some(keep));
  const source = sources.find(item => item.id === current);
  const saved = () => { forget(ctx.dataset + ':links'); forget(ctx.dataset + ':catalog'); links.reload(); ctx.refreshSteps?.(); };

  const open = id => { setSourceId(id); setTable(''); };
  // Approve all: the links shown (this source, or every readable source; narrowed by the table and relation filters)
  // that are not yet reviewed. "No link" proposals are not links and are left out.
  const approvable = mine.filter(link => needsReview(link) && link.definition_id && link.relation !== 'none'
    && (!table || tableOf(model, link) === table) && (overall || relation === 'all' || link.relation === relation))
    .map(link => ({key: {chunkId: link.chunk_id, definitionId: rawDefinition(link) ?? null}, group: tt(tableOf(model, link)),
      flagged: link.status === 'degraded' || link.status === 'not_processed'}));
  // Overall: one row per source, with its links counted by table.
  const perSource = processed.map(item => {
    const own = bySource.get(item.id), counts = new Map();
    own.filter(link => link.definition_id).forEach(link => counts.set(tableOf(model, link), (counts.get(tableOf(model, link)) || 0) + 1));
    return {item, counts, linked: own.filter(link => link.definition_id).length, passages: new Set(own.map(link => link.chunk_id)).size,
      review: own.filter(needsReview).length};
  }).filter(row => (!table || (table === 'No link' ? !row.linked : row.counts.has(table))) && (!review || row.review));
  const overallGraph = () => {
    const tables = ordered.filter(([name]) => name !== 'No link').map(([name]) => name);
    const top = row => Math.min(...[...row.counts.keys()].map(name => tables.indexOf(name)));
    const rows = perSource.filter(row => row.linked).sort((a, b) => top(a) - top(b));
    if (!rows.length) return <p className="pf-empty-line">{t('No source has a link yet.')}</p>;
    const height = Math.max(rows.length * 46, tables.length * 70), step = height / tables.length;
    const nodes = [...rows.map((row, index) => ({id: 's:' + row.item.id, type: 'pfNode', width: 320, height: 38, position: {x: 0, y: index * height / rows.length},
        data: {title: row.item.title, kind: 'source'}})),
      ...tables.map((name, index) => ({id: 't:' + name, type: 'pfNode', width: 210, height: 56, position: {x: 1000, y: index * step + step / 2 - 28},
        data: {title: tt(name), meta: `${counted(summary.get(name), 'link')} · ${counted(rows.filter(row => row.counts.has(name)).length, 'source')}`, kind: 'table'}}))];
    const edges = rows.flatMap(row => [...row.counts].map(([name, count]) => ({id: `e:${row.item.id}:${name}`, source: 's:' + row.item.id, target: 't:' + name, type: 'straight'})));   // counts sit on the table nodes and in the List view
    return <Flow nodes={nodes} edges={edges} nodeTypes={ctx.nodeTypes} label={t('Evidence across all sources')}
      onSelect={node => node.id.startsWith('s:') ? open(node.id.slice(2)) : (setTable(node.id.slice(2)), setView('reading'))} />;
  };
  const overallList = () => !perSource.length ? <p className="pf-empty-line">{t('No source matches these filters.')}</p> :
    <div className="pf-table-wrap pf-card"><table aria-label={t('Links by source')}><thead><tr><th>{t('Source')}</th><th>{t('Passages')}</th><th>{t('Links')}</th><th>{t('Links by table')}</th><th>{t('Needs review')}</th></tr></thead>
      <tbody>{perSource.map(row => <tr key={row.item.id}><td><button type="button" className="pf-linkish" onClick={() => { open(row.item.id); setView('reading'); }}>{row.item.title}</button></td>
        <td>{row.passages}</td><td>{row.linked}</td>
        <td>{row.linked ? [...row.counts].sort((a, b) => b[1] - a[1]).map(([name, count]) => `${tt(name)} ${count}`).join(' · ') : <span className="pf-muted">{t('No link')}</span>}</td>
        <td>{row.review || ''}</td></tr>)}</tbody></table></div>;

  const graph = () => {
    const tables = ordered.map(([name, count]) => ({name, count}));
    const nodes = [{id: 'source', type: 'pfNode', width: 220, height: 64, data: {title: source?.title, meta: counted(mine.length, 'link'), kind: 'source'}},
      ...tables.map(item => ({id: 't:' + item.name, type: 'pfNode', width: 200, height: 56,
        data: {title: tt(item.name), meta: t(item.count === 1 ? '{count} link' : '{count} links', {count: item.count}), kind: item.name === 'No link' ? 'muted' : 'table'}}))];
    const edges = tables.map(item => ({id: 'e:' + item.name, source: 'source', target: 't:' + item.name, label: String(item.count)}));
    return <Flow nodes={nodes} edges={edges} nodeTypes={ctx.nodeTypes} label={t('Evidence by table')}
      onSelect={node => { if (node.id.startsWith('t:')) { setTable(node.id.slice(2)); setView('reading'); } }} />;
  };

  return <div className="pf-page">
    <div className="pf-toolbar">
      <label className="pf-field grow">{t('Source')}<select value={current} onChange={event => open(event.target.value)}>
        <option value={ALL}>{t('All sources')} · {counted((links.data || []).filter(link => link.definition_id).length, 'link')}</option>
        {processed.map(item => <option key={item.id} value={item.id}>{item.title} · {counted(bySource.get(item.id).length, 'link')}</option>)}</select></label>
      {!overall && <label className="pf-field">{t('Relation')}<select value={relation} onChange={event => setRelation(event.target.value)}>
        <option value="all">{t('All relations')}</option>{Object.entries(RELATION).map(([value, text]) => <option key={value} value={value}>{t(text)}</option>)}</select></label>}
      <label className="pf-check"><input type="checkbox" checked={review} onChange={event => setReview(event.target.checked)} /> {t('Needs review')}</label>
      <Seg value={view} onChange={setView} options={[['graph', t('Graph')], ['reading', overall ? t('List') : t('Reading')]]} label={t('Evidence view')} />
      <ApproveAll ctx={ctx} kind="evidence_link" items={approvable} noun="links" groupWord="table"
        flaggedWord="degraded (the labeller could not finish)" onDone={saved} />
    </div>
    <div className="pf-summary" aria-label={t('Links by table')}>
      <span className="pf-muted">{overall ? t('All sources') : <>{source?.title} · <Tag>{t(dataLabel(source))}</Tag></>} · {t('{done} of {sources} processed', {done: processed.length, sources: counted(sources.length, 'source')})}{overall ? ' · ' + t('select a source to read its passages') : ''}</span>
      <div className="pf-chips"><span className="pf-muted pf-chips-label">{t('Links by table')}</span>{ordered.map(([name, count]) => <button type="button" key={name}
        className={'pf-chip' + (table === name ? ' on' : '') + (name === 'No link' ? ' muted' : '')}
        aria-pressed={table === name} onClick={() => setTable(table === name ? '' : name)}>{tt(name)} <b>{count}</b></button>)}</div>
    </div>
    {!overall && <FormRows ctx={ctx} sourceId={current} key={current} compact={view === 'graph'} />}
    {overall ? (view === 'graph' ? overallGraph() : overallList()) : view === 'graph' ? graph() : <>
      {chunks.loading && !chunks.data ? <Loading text={t('Reading the passages…')} /> : chunks.error ? <Problem text={serverText(chunks.error)} /> :
        !passages.length ? <p className="pf-empty-line">{t('No passage matches these filters.')}</p> :
        <ol className="pf-reading">{passages.map(({chunk, links: own}) => <li key={chunk.chunk_id} id={'chunk-' + chunk.chunk_id}
          className={'pf-row' + (ctx.observation === chunk.chunk_id ? ' focus' : '')}>
          <div className="pf-passage"><div className="pf-locator">{t('Passage {number}', {number: Number(chunk.order) + 1})}{chunk.page !== 'None' ? ' · ' + t('page {number}', {number: Number(chunk.page) + 1}) : ''}</div>
            <p>{chunk.text.replace(/^#{1,6}\s+/gm, '')}</p><Entities model={model} links={own} /></div>
          <ul className="pf-links">{own.filter(keep).map((link, index) => <LinkCard key={link.chunk_id + (link.definition_id || 'none') + index}
            ctx={ctx} model={model} link={link} onSaved={saved} />)}</ul>
        </li>)}</ol>}
    </>}
  </div>;
}
