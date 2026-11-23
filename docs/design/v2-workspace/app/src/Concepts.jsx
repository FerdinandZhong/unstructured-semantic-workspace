// Concepts (Output B): the concept catalog, its links to definitions, its evidence, and the admin build.
import {useMemo, useState} from 'react';
import {buildModel, getCatalog, forget, useLoad, shortSource, counted, t, serverText} from './lib.js';
import Flow from './Flow.jsx';
import ApproveAll from './ApproveAll.jsx';
import {Seg, Empty, Tag, Loading, Problem, Elapsed} from './ui.jsx';

const PREDICATE = {exactMatch: 'exact', closeMatch: 'close', relatedMatch: 'related', broadMatch: 'broader', narrowMatch: 'narrower'};
const STATUS = {proposed: 'proposed', candidate: 'candidate', corrected: 'reviewed', rejected: 'rejected'};
const ORIGIN = {both: 'anchored', document: 'induced', definition: 'anchored'};
const ORIGIN_HELP = {anchored: 'Seeded from a model definition and found in documents', induced: 'Found in documents only'};
const title = text => String(text || '').replace(/^./, ch => ch.toUpperCase());
const plain = text => String(text || '').replace(/^#+\s*/gm, '');
const post = (ctx, body) => ctx.request('/api/catalog-correction', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});

function Build({ctx, onWritten}) {
  const [state, setState] = useState({});
  const call = (action, body) => ctx.request('/api/catalog/build/' + action, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  async function preview() {
    setState({running: 'preview', since: Date.now()});
    try { setState({preview: await call('preview', {dataset: ctx.dataset})}); }
    catch (error) { setState({error: error.message}); }
  }
  async function confirm() {
    const held = state.preview;
    setState({...state, running: 'confirm', since: Date.now()});
    try {
      const done = await call('confirm', {dataset: ctx.dataset, version: held.version});
      setState({done: t('Catalog {version} written: {concepts}.', {version: done.version, concepts: counted(done.written, 'concept')})});
      onWritten();
    } catch (error) { setState({error: error.message}); }
  }
  if (ctx.readOnly) return null;
  const {preview: held, running} = state;
  const inc = held?.incremental;
  return <section className="pf-card pf-build" aria-label={t('Build catalog')}>
    <div className="pf-card-head"><div><h3>{t('Build catalog')}</h3>
      <p className="pf-muted">{t('Preview first. Nothing is written until you confirm the previewed version.')}</p></div>
      {!held && <button type="button" className="button primary" disabled={Boolean(running)} onClick={preview} data-build-preview>
        {running === 'preview' ? t('Building preview…') : t('Build catalog')}</button>}</div>
    {running && <p className="pf-running" role="status"><span className="pf-spinner" aria-hidden="true" />
      {running === 'preview' ? t('Reading the evidence links and grouping concepts. This calls the model and can take a few minutes.') : t('Writing the catalog.')} <Elapsed since={state.since} /></p>}
    {state.error && <p className="pf-error" role="alert">{serverText(state.error)}</p>}
    {state.done && <p className="pf-ok" role="status" data-build-done>{state.done}</p>}
    {held && <div className="pf-build-preview" data-build-counts>
      <p><strong>{t('Version {version}', {version: held.version})}</strong> · {counted(held.counts.concepts, 'concept')} · {counted(held.counts.links, 'definition link')} ·{' '}
        {Object.entries(held.counts.byStatus).map(([key, value]) => `${value} ${t(STATUS[key] || key)}`).join(t(', '))}</p>
      {inc && inc.new_sources && <p className="pf-muted">{t('Incremental update: {sources}{list}; {earlier} earlier concepts kept, {fresh} with new evidence.', {
        sources: t(inc.new_sources.length === 1 ? '{count} new source' : '{count} new sources', {count: inc.new_sources.length}),
        list: inc.new_sources.length ? ` (${inc.new_sources.map(shortSource).join(t(', '))})` : '', earlier: inc.earlier_concepts ?? 0, fresh: inc.earlier_concepts_with_new_evidence ?? 0})}</p>}
      <div className="pf-actions"><button type="button" className="button primary" disabled={Boolean(running)} onClick={confirm} data-build-confirm>{t('Confirm and write {version}', {version: held.version})}</button>
        <button type="button" className="button secondary" disabled={Boolean(running)} onClick={() => setState({})}>{t('Discard preview')}</button></div>
    </div>}
  </section>;
}

function Detail({ctx, model, concept, onSaved}) {
  const [rename, setRename] = useState(null);
  const [state, setState] = useState({});
  const [open, setOpen] = useState(null);
  const act = async (bodies, message) => {
    setState({busy: true});
    try {
      for (const body of bodies) await post(ctx, {dataset: ctx.dataset, ...body});
      setState({message}); setRename(null); onSaved();
    } catch (error) { setState({error: error.message}); }
  };
  const setStatus = status => act([{kind: 'concept', conceptId: concept.id, patch: {status}}], status === 'rejected' ? t('Rejected.') : t('Approved.'));
  const sources = new Map((ctx.workspace.sources || []).map(item => [item.id, item]));
  return <article className="pf-detail" aria-label={t('Concept detail')}>
    <header className="pf-detail-head">
      {rename === null ? <h2>{title(concept.label)}</h2> :
        <form className="pf-rename" onSubmit={event => { event.preventDefault(); if (rename.trim()) act([{kind: 'concept', conceptId: concept.id, patch: {label: rename.trim()}}], t('Renamed.')); }}>
          <label className="visually-hidden" htmlFor="pf-rename">{t('New label')}</label>
          <input id="pf-rename" value={rename} maxLength={200} onChange={event => setRename(event.target.value)} autoFocus />
          <button type="submit" className="button primary small" disabled={state.busy}>{t('Save')}</button>
          <button type="button" className="button secondary small" onClick={() => setRename(null)}>{t('Cancel')}</button></form>}
      <div className="pf-tags"><Tag tone={concept.status === 'corrected' ? 'green' : concept.status === 'rejected' ? 'red' : concept.status === 'candidate' ? 'amber' : ''}>{t(STATUS[concept.status] || concept.status)}</Tag>
        <span title={ORIGIN_HELP[ORIGIN[concept.origin]] ? t(ORIGIN_HELP[ORIGIN[concept.origin]]) : ''}><Tag>{t(ORIGIN[concept.origin] || concept.origin)}</Tag></span></div>
    </header>
    {!ctx.readOnly && rename === null && <div className="pf-actions">
      <button type="button" className="button primary small" disabled={state.busy || concept.status === 'corrected'} onClick={() => setStatus('corrected')}>{t('Approve')}</button>
      <button type="button" className="button secondary small" disabled={state.busy || concept.status === 'rejected'} onClick={() => setStatus('rejected')}>{t('Reject')}</button>
      <button type="button" className="button secondary small" disabled={state.busy} onClick={() => setRename(concept.label)}>{t('Rename')}</button>
    </div>}
    {ctx.readOnly && <p className="pf-muted">{t('Read-only account. Review actions need an admin.')}</p>}
    {state.error && <p className="pf-error" role="alert">{serverText(state.error)}</p>}
    {state.message && <p className="pf-ok" role="status">{state.message}</p>}
    <section><h3>{t('Meaning')}</h3><p className="pf-prose">{concept.definition || t('No meaning recorded.')}</p>
      {alsoCalled(concept).length > 0 && <p className="pf-muted">{t('Also called: {list}', {list: alsoCalled(concept).join(t(', '))})}</p>}</section>
    <section><h3>{t('Linked definitions')} <span className="pf-count">{concept.links.length}</span></h3>
      {concept.links.length ? <ul className="pf-deflinks">{concept.links.map(link => <li key={link.definition_id} className={link.status === 'rejected' ? 'removed' : ''}>
        <strong>{model.label(link.definition_id)}</strong>
        {ctx.readOnly ? <Tag tone="blue">{t(PREDICATE[link.predicate] || link.predicate)}</Tag> :
          <select aria-label={t('Match')} value={link.predicate} disabled={state.busy}
            onChange={event => act([{kind: 'concept_link', conceptId: concept.id, definitionId: link.definition_id, patch: {predicate: event.target.value}}], 'Match saved.')}>
            {Object.entries(PREDICATE).map(([value, text]) => <option key={value} value={value}>{t(text)}</option>)}</select>}
        {link.status === 'rejected' ? <Tag tone="red">{t('removed')}</Tag> : null}
        {!ctx.readOnly && <button type="button" className="button secondary small" disabled={state.busy}
          onClick={() => act([{kind: 'concept_link', conceptId: concept.id, definitionId: link.definition_id, patch: {status: link.status === 'rejected' ? 'proposed' : 'rejected'}}], 'Link saved.')}>
          {link.status === 'rejected' ? t('Restore') : t('Remove')}</button>}
        {link.reason && <small className="pf-muted">{support(link)}</small>}
      </li>)}</ul> : <p className="pf-muted">{t('No definition link. This concept comes from documents only.')}</p>}</section>
    <section><h3>{t('Evidence passages')} <span className="pf-count">{concept.evidenceDetail.length}</span></h3>
      <ul className="pf-evidence">{concept.evidenceDetail.map((item, index) => {
        const source = sources.get(item.sourceId);
        const isOpen = open === index;
        return <li key={item.chunkId + index}>
          <button type="button" className="pf-evidence-head" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : index)}>
            <span>{source?.title || shortSource(item.sourceId)}</span><span className="pf-muted">{isOpen ? t('Close') : t('Open')}</span></button>
          <p className={isOpen ? 'pf-quote open' : 'pf-quote'}>{plain(item.quote) || t('Passage withheld for this account.')}</p>
          {isOpen && <a className="text-link" href={ctx.href('evidence', shortSource(item.sourceId) + '/' + item.chunkId)}>{t('Open in Evidence')}</a>}
        </li>;
      })}</ul></section>
  </article>;
}

// Names only: at most four words, no sentence punctuation, not the concept's own name, no repeats. At most six.
function alsoCalled(concept) {
  const seen = new Set([String(concept.name || concept.label || '').toLowerCase()]);
  return (concept.synonyms || []).map(text => String(text).trim()).filter(text => {
    const key = text.toLowerCase();
    if (!text || seen.has(key) || text.split(/\s+/).length > 4 || /[.:;()\d]/.test(text)) return false;
    return seen.add(key);
  }).slice(0, 6);
}

// "2 passages state its value · 1 passage is about it", from the stored counts; the stored sentence is the fallback.
function support(link) {
  const states = link.support?.states_value_of, about = link.support?.is_about;
  if (states == null && about == null) return serverText(String(link.reason).replace(/chunk\(s\)/g, 'passages'));
  const say = (count, one, many) => t(`{count} ${count === 1 ? one : many}`, {count});
  return [states ? say(states, 'passage states its value', 'passages state its value') : '',
    about ? say(about, 'passage is about it', 'passages are about it') : ''].filter(Boolean).join(' · ') || t('No supporting passage');
}

export default function Concepts({ctx}) {
  const model = useMemo(() => buildModel(ctx.workspace), [ctx.workspace]);
  const catalog = useLoad(() => getCatalog(ctx), [ctx.dataset]);
  const [view, setView] = useState('list');
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('all');
  const [selected, setSelected] = useState(ctx.detail || '');
  const refresh = () => { forget(ctx.dataset + ':catalog'); catalog.reload(); ctx.refreshSteps?.(); };
  const concepts = catalog.data?.concepts || [];
  const shown = concepts.filter(item => (status === 'all' || item.status === status) &&
    (!query || (item.label + ' ' + (item.synonyms || []).join(' ')).toLowerCase().includes(query.toLowerCase())));
  const current = shown.find(item => item.id === selected) || shown[0];
  const counts = concepts.reduce((all, item) => ({...all, [item.status]: (all[item.status] || 0) + 1}), {});

  const head = <Build ctx={ctx} onWritten={() => { setStatus('all'); setQuery(''); refresh(); }} />;
  if (catalog.error) return <>{head}<Problem text={serverText(catalog.error)} /></>;
  if (catalog.loading && !catalog.data) return <Loading text={t('Reading the concept catalog…')} />;
  if (!concepts.length) return <>{head}<Empty title={t('No concept catalog yet')} text={ctx.readOnly
    ? t('An admin builds the catalog from the evidence links.') : t('Build the catalog from the evidence links. You preview it before anything is written.')} /></>;

  const graph = () => {
    const byTable = new Map();
    const edges = [];
    shown.forEach(concept => {
      const tables = new Map();
      concept.links.filter(link => link.status !== 'rejected').forEach(link => {
        const table = model.defs.get(link.definition_id)?.table;
        if (table && !tables.has(table.name)) tables.set(table.name, PREDICATE[link.predicate] || link.predicate);
      });
      tables.forEach((predicate, name) => { byTable.set(name, (byTable.get(name) || 0) + 1);
        edges.push({id: concept.id + '>' + name, source: 'c:' + concept.id, target: 't:' + name, label: predicate}); });
    });
    const linked = shown.filter(concept => edges.some(edge => edge.source === 'c:' + concept.id));
    // Clustered layout: each table with its concepts in a small grid beside it, clusters stacked. A bipartite graph
    // laid out by rank puts every concept in one tall column, which is unreadable at any zoom.
    const W = 190, H = 52, GAP = 16, COLS = 3;
    const home = new Map();
    linked.forEach(concept => home.set(concept.id, edges.find(edge => edge.source === 'c:' + concept.id).target.slice(2)));
    const nodes = [];
    let y = 0;
    [...byTable].sort((a, b) => b[1] - a[1]).forEach(([name, count]) => {
      const members = linked.filter(concept => home.get(concept.id) === name);
      const rows = Math.max(1, Math.ceil(members.length / COLS));
      const height = rows * (H + GAP) - GAP;
      nodes.push({id: 't:' + name, type: 'pfNode', width: 210, height: H, position: {x: 0, y: y + (height - H) / 2},
        data: {title: model.tables.get(name).label, meta: `${t('table')} · ${t(count === 1 ? '{count} concept' : '{count} concepts', {count})}`, kind: 'table', reverse: true}});
      members.forEach((concept, index) => nodes.push({id: 'c:' + concept.id, type: 'pfNode', width: W, height: H,
        position: {x: 300 + (index % COLS) * (W + GAP), y: y + Math.floor(index / COLS) * (H + GAP)},
        data: {title: title(concept.label), meta: `${t(STATUS[concept.status] || concept.status)} · ${t(concept.evidence.chunks.length === 1 ? '{count} passage' : '{count} passages', {count: concept.evidence.chunks.length})}`, kind: 'concept', reverse: true}}));
      y += height + 48;
    });
    return <>
      <p className="pf-muted">{t(linked.length === 1 ? '{count} concept is linked to tables.' : '{count} concepts are linked to tables.', {count: linked.length})} {t('{count} come from documents only and appear in the list view.', {count: shown.length - linked.length})}</p>
      <Flow nodes={nodes} edges={edges} nodeTypes={ctx.nodeTypes} label={t('Concept graph')}
        onSelect={node => { if (node.id.startsWith('c:')) { setSelected(node.id.slice(2)); setView('list'); } }} /></>;
  };

  return <div className="pf-page">
    {head}
    <div className="pf-toolbar">
      <label className="pf-field grow">{t('Find')}<input type="search" value={query} placeholder={t('Concept or synonym')} onChange={event => setQuery(event.target.value)} /></label>
      <label className="pf-field">{t('Status')}<select value={status} onChange={event => setStatus(event.target.value)}>
        <option value="all">{t('All')} · {concepts.length}</option>
        {Object.entries(counts).map(([key, value]) => <option key={key} value={key}>{t(STATUS[key] || key)} · {value}</option>)}</select></label>
      <Seg value={view} onChange={setView} options={[['list', t('List')], ['graph', t('Graph')]]} label={t('Concept view')} />
      <ApproveAll ctx={ctx} kind="concept" noun="concepts" groupWord="origin" flaggedWord="candidate (needs review)" onDone={refresh}
        items={shown.filter(item => item.status === 'proposed' || item.status === 'candidate').map(item => ({key: item.id,
          group: ORIGIN[item.origin] === 'induced' ? t('found in documents only') : t(ORIGIN[item.origin] || item.origin), flagged: item.status === 'candidate'}))} />
    </div>
    <p className="pf-muted pf-meta-line">{t('Catalog {version}', {version: catalog.data.version})} · {counted(concepts.length, 'concept')}
      {shown.length < concepts.length && <> · <strong>{status !== 'all' ? t('filter on: showing {count} {status}', {count: shown.length, status: t(STATUS[status] || status)}) : t('filter on: showing {count}', {count: shown.length})}</strong>{t(', {count} hidden', {count: concepts.length - shown.length})} ·{' '}
        <button type="button" className="pf-linkish" onClick={() => { setStatus('all'); setQuery(''); }}>{t('Show all')}</button></>}</p>
    {view === 'graph' ? graph() : <div className="pf-split">
      <ul className="pf-list" aria-label={t('Concepts')}>{shown.map(item => <li key={item.id}>
        <button type="button" aria-pressed={item.id === current?.id} onClick={() => setSelected(item.id)}>
          <span className="pf-list-title">{title(item.label)}</span>
          <span className="pf-list-meta">{t(ORIGIN[item.origin] || item.origin)} · {t(item.evidence.chunks.length === 1 ? '{count} passage' : '{count} passages', {count: item.evidence.chunks.length})} · {t(STATUS[item.status] || item.status)}</span>
        </button></li>)}</ul>
      {current ? <Detail key={current.id} ctx={ctx} model={model} concept={current} onSaved={refresh} /> : <p className="pf-empty-line">{t('No concept matches.')}</p>}
    </div>}
  </div>;
}
