// Ask: POST /api/answer. Facts from the database and passages from documents stay apart; code computes the comparison.
// Each answer has three views (Answer, Table & chart, Path) and suggested next steps. The table rows, the chart series
// and the suggestions come from POST /api/answer/detail with a handle the server issued with the answer (never SQL).
import {useEffect, useMemo, useRef, useState} from 'react';
import {createPortal} from 'react-dom';
import {allLinks, buildModel, shortSource, humanize, serverText, t, withLang} from './lib.js';
import {Tag, Elapsed, Seg} from './ui.jsx';
import Flow from './Flow.jsx';
import {SemanticContext, SemanticCard, WriteCard, ConnectionsPanel, useConnections, icebergReadyOf} from './AskTools.jsx';
import {ProgressPanel, InlineSteps} from './AskProgress.jsx';

const STATE = {answered: ['Answered', 'green'], refused: ['Not answered', 'red'], clarify: ['Needs clarification', 'amber']};
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const ISO = /^\d{4}-\d{2}-\d{2}$/;
const AGG = {sum: 'sum', count: 'count', min: 'minimum', max: 'maximum'};
const show = value => value === null || value === undefined ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value);
const detail = (ctx, handle, mode) => ctx.request('/api/answer/detail', {method: 'POST', headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({dataset: ctx.dataset, handle, mode})});

export const fmtDate = (iso, month) => { const [y, m, d] = iso.slice(0, 10).split('-'); return month ? `${MONTHS[m - 1]} ${y}` : `${+d} ${MONTHS[m - 1]} ${y}`; };
const isNumber = value => typeof value === 'number' || (typeof value === 'string' && /^-?\d+(\.\d+)?$/.test(value));
// The summary line repeats the figure as the server wrote it ("…: 356152.44"); show it as the conclusion shows it.
function summaryText(answer) {
  const raw = String(answer.conclusion ?? ''), text = String(answer.summary);
  return isNumber(answer.conclusion) && raw.length > 3 && text.endsWith(raw) ? text.slice(0, -raw.length) + fmtValue(answer.conclusion) : text;
}

export function fmtValue(value) {
  if (typeof value === 'boolean') return value ? t('Yes') : t('No');
  if (isNumber(value)) return Number(value).toLocaleString('en-GB', {maximumFractionDigits: 2});
  if (typeof value === 'string' && ISO.test(value.slice(0, 10)) && value.length <= 19) return fmtDate(value);
  return show(value);
}

// Definition ids carry the model: "...:definition:<model>/metric/x". The planner's short id is the part after it.
const shortId = id => String(id || '').replace(/^.*?:definition:[^/]+\//, '');
const tableOf = (model, name) => name && [...model.tables.values()].find(table => table.name.toLowerCase() === String(name).toLowerCase());

function measureOf(model, answer, fact) {
  if (fact) return model.defs.get(fact.definition);
  const wanted = [...(answer.definitions || []).map(shortId), answer.plan?.measure].filter(Boolean);
  return [...model.defs.values()].find(def => def.kind !== 'table' && wanted.includes(shortId(def.id)));
}

const measureTitle = (model, fact) => model.defs.get(fact.definition)?.title || humanize(fact.label);   // short, for axes

function measureName(model, fact) {
  const def = model.defs.get(fact.definition);
  return def?.label || humanize(fact.label);
}

// "Business date = 5 Nov 2026 · Transaction currency = SGD"
function filterText(model, fact) {
  const base = model.defs.get(fact.definition)?.table;
  return Object.entries(fact.filters || {}).filter(([key]) => key !== 'aggregate').map(([key, value]) => {
    const [left, right] = key.includes('.') ? key.split('.') : [null, key];
    const table = left ? tableOf(model, left) : base;
    const field = table?.fields.find(item => item.column.toLowerCase() === right.toLowerCase());
    const name = (left && table && table !== base ? table.label + ' › ' : '') + (field?.title || humanize(right));
    let text;
    if (value && typeof value === 'object') {
      text = value.from ? `${fmtValue(value.from)}${t(' to ')}${fmtValue(value.to)}`
        : Object.entries(value).map(([op, v]) => `${{gte: '≥', gt: '>', lte: '≤', lt: '<'}[op] || op} ${fmtValue(v)}`).join(t(' and '));
      return `${name} ${value.from ? t('from') + ' ' : ''}${text}`;
    }
    return `${name} = ${isNumber(value) ? value : fmtValue(value)}`;   // an identifier keeps its digits
  }).join(' · ');
}

// ---------------------------------------------------------------- answer view

function Facts({facts, model}) {
  if (!facts.length) return <p className="pf-muted">{t('No database fact was needed or found.')}</p>;
  return <ul className="pf-facts">{facts.map((fact, index) => <li key={index} data-fact>
    <div className="pf-fact-head"><span>{measureName(model, fact)}</span><strong>{fmtValue(fact.value)}</strong></div>
    {filterText(model, fact) && <p className="pf-muted">{t('Filters: {text}', {text: filterText(model, fact)})}</p>}
  </li>)}</ul>;
}

function Passages({citations, ctx}) {
  if (!citations.length) return <p className="pf-muted">{t('No document passage was cited.')}</p>;
  return <ul className="pf-citations">{citations.map((item, index) => <li key={index} data-citation>
    <blockquote>{item.span}</blockquote>
    <div className="pf-cite-meta"><span>{item.title || shortSource(item.source)}</span>
      {item.role === 'amends' && <Tag tone="amber">{t('amends an earlier document')}</Tag>}
      {item.chunkId && <a className="text-link" href={ctx.href('evidence', shortSource(item.source) + '/' + item.chunkId)}>{t('Open at passage')}</a>}</div>
  </li>)}</ul>;
}

function sideName(model, answer, side) {
  const from = String(side?.from ?? '');
  if (from === 'fact') return answer.facts?.[0] ? measureTitle(model, answer.facts[0]) : t('Database fact');
  if (/^V\d+$/.test(from)) return answer.plan?.evidence?.[Number(from.slice(1)) - 1]?.need || t('Value from a document');
  if (/^N\d+$/.test(from)) return t('Number in the question');
  if (/^D\d+$/.test(from)) return t('Date in the question');
  return humanize(from);
}

function Comparison({value, model, answer}) {
  if (!value) return null;
  const side = item => item && typeof item === 'object' ? fmtValue(item.value) : show(item);
  return <section className="pf-card pf-comparison" data-comparison><h3>{t('Comparison computed by code')}</h3>
    <p>{side(value.left)} <b>{show(value.op)}</b> {side(value.right)} → <strong>{fmtValue(value.value)}</strong></p>
    {value.left?.from && <p className="pf-muted">{sideName(model, answer, value.left)} {show(value.op)} {sideName(model, answer, value.right)}</p>}</section>;
}

// ---------------------------------------------------------------- table and chart view

function RowsBehind({ctx, fact, model, title}) {
  const [state, setState] = useState(null);
  const load = () => {
    setState({loading: true});
    detail(ctx, fact.detail.id, 'rows').then(data => setState({data}), error => setState({error: error.message}));
  };
  const data = state?.data;
  return <section className="pf-rows" aria-label={t('Rows behind this figure')} data-rows-behind>
    <div className="pf-card-head"><h4>{t('Rows behind this figure')}{title ? ` · ${title}` : ''}</h4>
      {!data && <button type="button" className="button secondary small" disabled={state?.loading} onClick={load}>{state?.loading ? t('Reading…') : t('Show the first 50 rows')}</button>}</div>
    {state?.error && <p className="pf-error" role="alert">{serverText(state.error)}</p>}
    {data && !data.rows && <p className="pf-muted">{t('This figure is one recorded value, not an aggregate of rows.')}</p>}
    {data?.rows && <>
      <p className="pf-muted" data-rows-caption>{data.total > data.rows.length ? t('First {shown} of {total} rows the {aggregate} reads.', {shown: data.rows.length, total: data.total.toLocaleString('en-GB'), aggregate: t(AGG[data.aggregate])})
        : t('All {total} rows the {aggregate} reads.', {total: data.total, aggregate: t(AGG[data.aggregate])})} {t('System columns are hidden.')}</p>
      <div className="pf-table-wrap pf-rows-wrap"><table><thead><tr>{data.columns.map(column => <th key={column.name}
        className={column.name === data.measure ? 'measure' : ''}>{column.label}</th>)}</tr></thead>
        <tbody>{data.rows.map((row, index) => <tr key={index}>{data.columns.map(column => <td key={column.name}
          className={(isNumber(row[column.name]) ? 'num' : '') + (column.name === data.measure ? ' measure' : '')}>{column.name === data.measure || !isNumber(row[column.name]) ? fmtValue(row[column.name]) : row[column.name]}</td>)}</tr>)}</tbody></table></div></>}
  </section>;
}

function ResultTable({ctx, model, facts}) {
  const [sql, setSql] = useState(-1);
  return <>
    <div className="pf-table-wrap"><table className="pf-result" aria-label={t('Result table')}><thead><tr><th>{t('Measure')}</th><th>{t('Filters')}</th><th className="num">{t('Value')}</th><th /></tr></thead>
      <tbody>{facts.flatMap((fact, index) => {
        const aggregate = fact.filters?.aggregate;
        const rows = [<tr key={index} data-result-row>
          <td>{measureName(model, fact)}{aggregate && <> <Tag>{AGG[aggregate] ? t(AGG[aggregate]) : aggregate}</Tag></>}</td>
          <td>{filterText(model, fact) || <span className="pf-muted">{t('none')}</span>}</td>
          <td className="num" data-value><strong>{fmtValue(fact.value)}</strong></td>
          <td>{fact.sql && <button type="button" className="pf-linkish" aria-expanded={sql === index} onClick={() => setSql(sql === index ? -1 : index)}>SQL</button>}</td></tr>];
        if (sql === index) rows.push(<tr key={index + 's'}><td colSpan={4}><pre className="pf-sql">{fact.sql}</pre></td></tr>);
        return rows;
      })}</tbody></table></div>
    {facts.filter(fact => fact.detail?.rows).map((fact, index, all) => <RowsBehind key={fact.detail.id} ctx={ctx} fact={fact} model={model}
      title={all.length > 1 ? measureName(model, fact) : ''} />)}
  </>;
}

function nice(max) {
  if (max <= 0) return 1;
  const raw = max / 4, power = 10 ** Math.floor(Math.log10(raw));
  return [1, 2, 2.5, 5, 10].map(step => step * power).find(step => step >= raw);
}
const short = value => Math.abs(value) >= 1e6 ? (value / 1e6).toLocaleString('en-GB', {maximumFractionDigits: 1}) + 'M'
  : Math.abs(value) >= 1e4 ? (value / 1e3).toLocaleString('en-GB', {maximumFractionDigits: 1}) + 'k' : fmtValue(value);

// A plain SVG bar chart: highlighted bars are the answered period or value.
export function Bars({points, yLabel, xLabel, between}) {
  const W = 760, H = 250, L = 64, R = 16, T = 22, B = 52;
  const values = points.map(point => Number(point.value) || 0);
  const step = nice(Math.max(...values, 0));
  const top = Math.max(step, Math.ceil(Math.max(...values, 0) / step) * step);
  const ticks = Array.from({length: Math.round(top / step) + 1}, (_, i) => i * step);
  const slot = (W - L - R) / points.length, bar = Math.min(56, slot * 0.7);
  const y = value => T + (H - T - B) * (1 - value / top);
  const every = Math.ceil(points.length / 12);
  return <svg className="pf-chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={t('{yLabel} by {xLabel}', {yLabel, xLabel})} data-chart>
    {ticks.map(tick => <g key={tick}><line x1={L} x2={W - R} y1={y(tick)} y2={y(tick)} className="grid" />
      <text x={L - 6} y={y(tick) + 4} textAnchor="end" className="tick">{short(tick)}</text></g>)}
    {points.map((point, i) => {
      const x = L + slot * i + (slot - bar) / 2, value = Number(point.value) || 0;
      return <g key={point.key} data-bar={point.answered ? 'answered' : 'context'}>
        <rect x={x} y={y(value)} width={bar} height={Math.max(0, y(0) - y(value))} rx="2" className={point.answered ? 'on' : ''}><title>{point.label}: {fmtValue(point.value)}</title></rect>
        {(point.answered || points.length <= 6) && <text x={x + bar / 2} y={y(value) - 5} textAnchor="middle" className={'val' + (point.answered ? ' on' : '')}>{short(value)}</text>}
        {i % every === 0 && <text x={x + bar / 2} y={H - B + 16} textAnchor="middle" className="tick">{point.label}</text>}
      </g>;
    })}
    {between && <text x={W / 2} y={T + (H - T - B) / 2} textAnchor="middle" className="op">{between}</text>}
    <text x={14} y={T + (H - T - B) / 2} transform={`rotate(-90 14 ${T + (H - T - B) / 2})`} textAnchor="middle" className="axis">{yLabel}</text>
    <text x={L + (W - L - R) / 2} y={H - 8} textAnchor="middle" className="axis">{xLabel}</text>
  </svg>;
}

function Chart({ctx, model, answer}) {
  const fact = (answer.facts || []).find(item => item.detail);
  const comparison = answer.comparison;
  const numeric = comparison && comparison.op !== 'all' && [comparison.left?.value, comparison.right?.value].every(isNumber);
  const [state, setState] = useState(null);
  useEffect(() => {
    if (numeric || !fact) return;
    let live = true;
    detail(ctx, fact.detail.id, 'series').then(data => live && setState({data}), error => live && setState({error: error.message}));
    return () => { live = false; };
  }, [fact?.detail?.id, numeric]);
  if (numeric) {
    const points = ['left', 'right'].map(side => ({key: side, label: clip(sideName(model, answer, comparison[side]), 40),
      value: Number(comparison[side].value), answered: true}));
    return <figure className="pf-figure" data-chart-kind="comparison">
      <Bars points={points} yLabel={t('Value')} xLabel={t('The two values compared')} between={show(comparison.op)} />
      <figcaption>{t('The two values the comparison uses, {left} {op} {right}, gives {value}.', {left: fmtValue(comparison.left.value), op: show(comparison.op), right: fmtValue(comparison.right.value), value: fmtValue(comparison.value)})} {t('The comparison is computed by code.')}</figcaption></figure>;
  }
  if (!fact) return null;
  if (state?.error) return <p className="pf-error" role="alert">{t('The chart could not be read: {error}', {error: serverText(state.error)})}</p>;
  const data = state?.data;
  if (!data) return state === null ? <p className="pf-muted">{t('Reading the context series…')}</p> : null;
  if (!data.kind) return <p className="pf-muted" data-no-chart>{t('No chart: this figure has no date or category to set it beside.')}</p>;
  const measure = clip(measureTitle(model, fact) + (data.unit ? ` (${data.unit})` : ''), 34);
  const month = data.grain === 'month';
  const points = data.points.map(point => ({...point, label: data.kind === 'date' ? fmtDate(point.key, month) : point.key}));
  const xLabel = data.kind === 'date' ? t('{axis}, per {grain}', {axis: data.axis, grain: t(data.grain)}) : data.axis;
  const span = data.kind === 'date' ? `${t('from')} ${points[0].label}${t(' to ')}${points.at(-1).label}` : t('for each {axis}', {axis: data.axis.toLowerCase()});
  const answered = data.kind === 'date' ? (data.period.from === data.period.to ? fmtDate(data.period.from) : `${fmtDate(data.period.from)}${t(' to ')}${fmtDate(data.period.to)}`) : data.value;
  return <figure className="pf-figure" data-chart-kind={data.kind}>
    <Bars points={points} yLabel={measure} xLabel={xLabel} />
    <figcaption>{t('{measure} per {unit}, {span}, with the other filters of the answer; the highlighted {part} ({answered}) is the one answered.', {measure: measureName(model, fact),
      unit: data.kind === 'date' ? t(data.grain) : data.axis.toLowerCase(), span, part: data.kind === 'date' ? t('period') : t('bar'), answered})} {t('This is context computed by SQL, not part of the answer.')}</figcaption></figure>;
}

function TableAndChart({ctx, model, answer}) {
  const facts = answer.facts || [];
  if (!facts.length && !answer.comparison) return <p className="pf-muted">{t('No database fact was used, so there is no table or chart. The answer comes from documents.')}</p>;
  return <div className="pf-tc">{facts.length > 0 && <ResultTable ctx={ctx} model={model} facts={facts} />}
    <Chart ctx={ctx} model={model} answer={answer} /></div>;
}

// ---------------------------------------------------------------- path view

const clip = (text, n) => { const s = String(text || '').replace(/[|#*]+/g, ' ').replace(/\//g, ' ').replace(/\s+/g, ' ').trim(); return s.length > n ? s.slice(0, n - 1) + '…' : s; };

export function pathGraph(model, answer, links) {
  const nodes = new Map(), edges = new Map();
  const node = (id, kind, title, meta, width = 200) => { if (!nodes.has(id)) nodes.set(id, {id, type: 'pfNode', width, height: meta ? 54 : 40, data: {kind, title: clip(title, 44), meta: meta && clip(meta, 52)}}); };
  const edge = (source, target, extra = {}) => { const id = `${source}>${target}`; if (!edges.has(id)) edges.set(id, {id, source, target, ...extra}); };
  const define = def => {        // table -> field or metric
    if (!def) return null;
    if (def.table) { node('t:' + def.table.name, 'table', def.table.label, t('table')); }
    node('d:' + def.id, def.kind, def.title, def.kind === 'metric' ? t('metric') : t('field'));
    if (def.table) edge('t:' + def.table.name, 'd:' + def.id);
    return def;
  };
  node('q', 'question', answer.question, t('question'), 240);
  (answer.facts || []).forEach((fact, index) => {
    const def = model.defs.get(fact.definition);
    const tables = (fact.detail?.tables || []).map(name => tableOf(model, name)).filter(Boolean);
    const base = tables[0] || def?.table;
    if (base) { node('t:' + base.name, 'table', base.label, t('table')); edge('q', 't:' + base.name); }
    tables.slice(1).forEach(table => { node('t:' + table.name, 'table', table.label, t('joined table')); edge('t:' + base.name, 't:' + table.name, {label: t('join')}); });
    if (def) { define(def); if (!base) edge('q', 'd:' + def.id); }
    node('v:' + index, 'value', fmtValue(fact.value), filterText(model, fact) || t('value'));
    edge(def ? 'd:' + def.id : 'q', 'v:' + index);
  });
  (answer.citations || []).forEach(item => {
    const p = 'p:' + item.chunkId, s = 's:' + item.source;
    node(p, 'passage', item.span, t('passage'), 220);
    node(s, 'document', item.title || shortSource(item.source), t('document'));
    edge('q', p); edge(p, s);
    (links || []).filter(link => link.chunk_id === item.chunkId && link.definition_id && link.relation !== 'none' && link.status !== 'rejected')
      .slice(0, 3).forEach(link => { if (define(model.defs.get(link.definition_id))) edge(p, 'd:' + link.definition_id, {style: {strokeDasharray: '5 4'}}); });
  });
  if (answer.status !== 'answered') {
    const def = define(measureOf(model, answer));
    if (def) edge('q', def.table ? 't:' + def.table.name : 'd:' + def.id);
    node('x', 'muted', answer.status === 'clarify' ? t('Needs clarification') : t('Not answered'),
      serverText(answer.refusalReason || answer.reason), 230);
    edge(def ? 'd:' + def.id : 'q', 'x');
  }
  return {nodes: [...nodes.values()], edges: [...edges.values()]};
}

function PathView({ctx, model, answer}) {
  const [links, setLinks] = useState(null);
  useEffect(() => { allLinks(ctx).then(setLinks, () => setLinks([])); }, [ctx.dataset]);
  const graph = useMemo(() => links ? pathGraph(model, answer, links) : null, [links, answer]);
  if (!graph) return <p className="pf-muted">{t('Reading the evidence links…')}</p>;
  const go = node => {
    const [kind, ...rest] = node.id.split(':'), id = rest.join(':');
    if (kind === 't') location.hash = ctx.href('model', id);
    if (kind === 'd') { const def = model.defs.get(id); if (def?.table) location.hash = ctx.href('model', def.table.name); }
    if (kind === 'p') { const cite = answer.citations.find(item => item.chunkId === id); if (cite) location.hash = ctx.href('evidence', shortSource(cite.source) + '/' + id); }
    if (kind === 's') location.hash = ctx.href('evidence', shortSource(id));
  };
  return <>
    <p className="pf-muted">{t('From the question to the definitions and the value, and to the cited passages and their documents. A dashed line is an evidence link from a passage to a definition. Select a table, a field or a passage to open it.')}</p>
    <Flow nodes={graph.nodes} edges={graph.edges} nodeTypes={ctx.nodeTypes} onSelect={go} nodesep={18} label={t('Path of the answer')} className="pf-canvas-path" />
  </>;
}

// ---------------------------------------------------------------- suggested next steps

// A fill-in form the answer cites or reads whose rows are not all written yet. Admin only.
function useFormToLoad(ctx, model, answer) {
  const [found, setFound] = useState(null);
  useEffect(() => {
    if (ctx.readOnly) return;
    const sources = new Map((ctx.workspace.sources || []).map(item => [item.id, item]));
    const forms = [...sources.values()].filter(item => item.kind === 'pdf');
    const cited = (answer.citations || []).map(item => sources.get(item.source)).filter(item => item?.kind === 'pdf');
    const def = measureOf(model, answer, answer.facts?.[0]);
    const candidates = [...new Set([...cited, ...(def?.table?.fromDocuments ? forms : [])])].slice(0, 8);
    let live = true;
    (async () => {
      for (const source of candidates) {
        const view = await ctx.request('/api/physical-rows?' + new URLSearchParams({dataset: ctx.dataset, sourceId: source.id})).catch(() => null);
        const kept = (view?.rows || []).filter(row => row.status !== 'rejected');
        const tables = view?.tables?.length ? view.tables : [view?.table];
        if (view?.writeAvailable && (!kept.length || kept.some(row => row.status !== 'written'))
            && (cited.includes(source) || tables.includes(def?.table?.name))) { if (live) setFound(source); return; }
      }
    })();
    return () => { live = false; };
  }, [answer]);
  return found;
}

function Suggestions({ctx, model, answer, onAsk, onWrite}) {
  const [items, setItems] = useState([]);
  const form = useFormToLoad(ctx, model, answer);
  useEffect(() => {
    if (!answer.detail) return;
    let live = true;
    detail(ctx, answer.detail, 'next').then(out => live && setItems(out.suggestions || []), () => {});
    return () => { live = false; };
  }, [answer.detail]);
  const handle = savable(ctx, answer);
  const operations = [handle && {key: 'save', text: t('Save this result as a table'), source: {kind: 'answer', handle}},
    form && {key: 'form', text: t("Load this form's rows into the model"), source: {kind: 'form', sourceId: form.id}}].filter(Boolean);
  // a card with hints offers its own choices: a suggestion that only completes the question would compete with them
  const chips = items.filter(item => !(answer.hints?.length && item.why === 'completes the question')).slice(0, Math.max(0, 3 - operations.length));
  if (!chips.length && !operations.length) return null;
  return <section className="pf-next" aria-label={t('Suggested next steps')} data-suggestions>
    <div className="pf-card-head"><h3>{t('Suggested next steps')}</h3><span className="pf-muted">{t('Generated by code from this answer and the data, not by a model.')}</span></div>
    <div className="pf-chips">
      {chips.map(item => <button type="button" key={item.question} className="pf-chip" title={item.question} data-suggestion
        onClick={() => onAsk(item.question)}><span className="pf-muted">{serverText(item.why)}{t(': ')}</span>{item.label}</button>)}
      {operations.map(op => <button type="button" key={op.key} className="pf-chip op" data-operation={op.key} onClick={() => onWrite(op.source)}>
        {op.text} <Tag tone="amber">{t('needs confirmation')}</Tag></button>)}
    </div>
  </section>;
}

// A clarification's hints (built by the server from the semantic layer and the data, not by a model): a click sends
// the hint's full text as the next turn of this session. A stored session shows them as plain text and sends nothing.
function Hints({answer, past, onAsk}) {
  const hints = answer.hints || [];
  if (!hints.length && !answer.hintNote) return null;
  return <section className="pf-hints" aria-label={t('Clarification hints')} data-hints>
    {answer.hintNote && <p className="pf-muted" data-hint-note>{serverText(answer.hintNote)}</p>}
    {hints.length > 0 && <><h3>{answer.hintKind === 'choice' ? t('Did you mean one of these?') : t('You can ask:')}</h3>
      {past ? <ul className="pf-hint-list">{hints.map(hint => <li key={hint.text} data-hint data-data>{hint.text}</li>)}</ul>
        : <div className="pf-chips">{hints.map(hint => <button type="button" key={hint.text} className="pf-chip pf-hint" title={hint.text}
          data-hint data-hint-button data-data onClick={() => onAsk(hint.text, {hint: true})}>{serverText(hint.label)}
          {hint.detail && <span className="pf-muted pf-hint-detail">{serverText(hint.detail)}</span>}</button>)}</div>}</>}
  </section>;
}

// The first fact handle of an answered reply, for "Save this result as a table". Admin only.
const savable = (ctx, answer) => !ctx.readOnly && answer.status === 'answered' && (answer.facts || []).find(fact => fact.detail?.rows)?.detail.id;

// ---------------------------------------------------------------- the page

// The one currency a figure is filtered to, from the fact's own filters (code, never the model): "SGD" when the measure
// is filtered to exactly one currency code, else null (no currency filter, or more than one).
export function currencyOf(answer) {
  const facts = answer.facts || [];
  if (facts.length !== 1 || !isNumber(answer.conclusion) || String(facts[0].value) !== String(answer.conclusion)) return null;
  const codes = Object.entries(facts[0].filters || {}).filter(([key]) => /currency/i.test(key.split('.').at(-1)))
    .flatMap(([, value]) => Array.isArray(value) ? value : [value]);
  return codes.length === 1 && typeof codes[0] === 'string' && /^[A-Z]{3}$/.test(codes[0]) ? codes[0] : null;
}

// One exchange: the question as the user's bubble, then the reply card with its steps at the top.
function AnswerCard({ctx, model, text, answer, trace, onAsk, icebergReady, onOpenSteps, past, trimmed}) {
  const [tab, setTab] = useState('answer');
  const [write, setWrite] = useState(null);
  const [label, tone] = STATE[answer.status] ? [t(STATE[answer.status][0]), STATE[answer.status][1]] : [serverText(answer.status), ''];
  const handle = savable(ctx, answer);
  const writeTitle = write?.kind === 'form' ? t("Load this form's rows") : t('Save this result as a table');
  const concluded = answer.status === 'answered' && answer.conclusion !== null && answer.conclusion !== undefined;
  return <article className="pf-turn" data-answer>
    <h2 className="pf-user-bubble" data-user-bubble>{text}</h2>
    <Understood english={answer.questionStandalone || answer.questionEnglish} />
    {trimmed && <Trimmed />}
    <div className="pf-reply">
      <InlineSteps model={model} steps={trace} onOpenPanel={() => onOpenSteps(text, trace)} />
      <header><Tag tone={tone}>{label}</Tag>
        <Seg value={tab} onChange={setTab} options={[['answer', t('Answer')], ['table', t('Table & chart')], ['path', t('Path')]]} label={t('Answer view')} /></header>
      {tab === 'answer' && <>
        {answer.status !== 'answered' ? <p className={'pf-refusal ' + answer.status} data-refusal>{serverText(answer.reason || answer.summary || answer.error)}</p>
          : <div className="pf-verdict">
            {concluded && <p className="pf-conclusion"><span className="pf-eyebrow">{t('Conclusion')}</span>
              <strong>{currencyOf(answer) ? currencyOf(answer) + ' ' : ''}{fmtValue(answer.conclusion)}</strong></p>}
            {answer.summary && <p className="pf-prose">{summaryText(answer)}</p>}</div>}
        {answer.status !== 'answered' && <Hints answer={answer} past={past} onAsk={onAsk} />}
        <Comparison value={answer.comparison} model={model} answer={answer} />
        {(answer.status === 'answered' || answer.facts?.length > 0 || answer.citations?.length > 0) && <div className="pf-two">
          <section aria-label={t('Facts from the database')}><h3>{t('Facts from the database')}</h3><Facts facts={answer.facts || []} model={model} />
            {handle && !write && <button type="button" className="button secondary small pf-save" data-save-result
              onClick={() => setWrite({kind: 'answer', handle})}>{t('Save this result as a table')}</button>}</section>
          <section aria-label={t('Passages from documents')}><h3>{t('Passages from documents')}</h3><Passages citations={answer.citations || []} ctx={ctx} /></section>
        </div>}
        <SemanticContext ctx={ctx} model={model} answer={answer} />
      </>}
      {tab === 'table' && <TableAndChart ctx={ctx} model={model} answer={answer} />}
      {tab === 'path' && <PathView ctx={ctx} model={model} answer={answer} />}
      {write && <WriteCard key={JSON.stringify(write)} ctx={ctx} model={model} source={write} icebergReady={icebergReady} onClose={() => setWrite(null)}
        onOpenSteps={steps => onOpenSteps(writeTitle, steps)} />}
      {!past && <Suggestions ctx={ctx} model={model} answer={answer} onAsk={onAsk} onWrite={setWrite} />}
    </div>
  </article>;
}

// A question asked in Chinese (plan Z7), or a follow-up read with the earlier turns of its chat session, is routed and
// answered in the standalone English the server rewrote it to: shown, so the person can see what was asked.
const Understood = ({english}) => english ? <p className="pf-muted pf-understood" data-understood>{t('Understood as: {english}', {english})}</p> : null;
const Trimmed = () => <p className="pf-muted" data-trimmed>{t('Stored in this browser in short form: long lists and texts were cut.')}</p>;

const TOOL = {semantic: 'Meaning from the catalog', write: 'Write preview', connections: 'Connections'};

function ToolCard({ctx, model, turn, icebergReady, onClose, onDone, onOpenSteps}) {
  const {tool, result, trace} = turn.reply;
  return <article className="pf-turn" data-tool-card={tool}>
    <h2 className="pf-user-bubble" data-user-bubble>{turn.text}</h2>
    <Understood english={turn.reply.questionStandalone || turn.reply.questionEnglish} />
    {turn.trimmed && <Trimmed />}
    <div className="pf-reply">
      {tool !== 'write' && <InlineSteps model={model} steps={trace} onOpenPanel={() => onOpenSteps(turn.text, trace)} />}
      {(tool !== 'write' || result.error) && <header><Tag tone="blue">{t(TOOL[tool])}</Tag></header>}
      {tool === 'semantic' && <SemanticCard ctx={ctx} model={model} result={result} />}
      {tool === 'connections' && <p className="pf-muted">{t('The connections panel is open above.')}</p>}
      {tool === 'write' && (result.error ? <><InlineSteps model={model} steps={trace} /><p className="pf-refusal" role="alert">{serverText(result.error)}</p></>
        : <WriteCard ctx={ctx} model={model} preview={result} trace={trace} icebergReady={icebergReady} onClose={onClose}
          past={turn.past} done={turn.reply.done} onDone={onDone}
          onOpenSteps={steps => onOpenSteps(turn.text, steps)} />)}
    </div>
  </article>;
}

const requestId = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, '0')).join('');

// Polls the server's record of one running request about once a second; stops when `stop()` is called.
function pollProgress(ctx, id, onSteps) {
  let stopped = false, timer = null;
  const tick = async () => {
    try {
      const response = await fetch('/api/ask/progress?' + new URLSearchParams({id, dataset: ctx.dataset}));
      if (response.ok && !stopped) onSteps((await response.json()).steps);
    } catch { /* the next tick tries again; the reply carries the full trace */ }
    if (!stopped) timer = setTimeout(tick, 1000);
  };
  timer = setTimeout(tick, 250);
  return () => { stopped = true; clearTimeout(timer); };
}

// ---------------------------------------------------------------- history (this browser, this account, this data source)

// Chat sessions, keyed by account id and dataset: one account's sessions never show under another. A session holds its
// turns (the text and the reply it got) and stays current until New chat. A per-browser convenience only: storage may
// be blocked or full, so every read and write is guarded and the page works without it. The older one-question
// entries (`v2-ask-history:`) are ignored. Opening a stored session shows it and runs nothing.
const SESSIONS = 12, TURNS = 30, LIST = 25, TEXT = 4000;
const sessionsKey = ctx => ctx.principal?.id ? `v2-ask-sessions:${ctx.principal.id}:${ctx.dataset}` : null;
function readSessions(ctx) {
  const key = sessionsKey(ctx);
  try {
    const value = key ? JSON.parse(localStorage.getItem(key) || 'null') : null;
    const sessions = Array.isArray(value?.sessions) ? value.sessions.filter(item => item && item.account === ctx.principal.id
      && item.dataset === ctx.dataset && Array.isArray(item.turns) && item.turns.length) : [];
    return {sessions, current: sessions.some(item => item.id === value.current) ? value.current : null};
  } catch { return {sessions: [], current: null}; }
}
function writeSessions(ctx, store) {
  const key = sessionsKey(ctx);
  if (!key) return;
  // a full storage drops the oldest sessions until the rest fit (sessions are kept newest first)
  for (let sessions = store.sessions; ; sessions = sessions.slice(0, -1)) {
    try { sessions.length || store.current ? localStorage.setItem(key, JSON.stringify({...store, sessions})) : localStorage.removeItem(key); return; }
    catch { if (!sessions.length) return; /* storage unavailable */ }
  }
}
// ponytail: long lists and long texts in a stored reply are cut (a browser keeps about 5 MB per site); the turn says so
function slim(turn) {
  let trimmed = Boolean(turn.trimmed);
  const reply = JSON.parse(JSON.stringify(turn.reply, (key, value) => {
    if (Array.isArray(value) && value.length > LIST) { trimmed = true; return value.slice(0, LIST); }
    if (typeof value === 'string' && value.length > TEXT) { trimmed = true; return value.slice(0, TEXT) + '…'; }
    return value;
  }));
  return {id: turn.id, text: turn.text, at: turn.at, reply, ...(trimmed ? {trimmed} : {})};
}
const statusOf = reply => reply.tool === 'answer' ? reply.result?.status || 'answered' : reply.tool === 'semantic' ? 'term' : reply.tool;
const STATUS_WORD = {answered: ['Answered', 'green'], refused: ['Not answered', 'red'], clarify: ['Clarify', 'amber'], write: ['Write', 'blue'],
  term: ['Term', ''], connections: ['Connections', '']};
const when = iso => { const d = new Date(iso); return isNaN(d) ? '' : d.toLocaleString('en-GB', {day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'}); };
const cut = (value, n = 300) => String(value ?? '').slice(0, n);
// The earlier turns of the session as the server reads them (data, never instructions): the last six, each field cut.
function historyOf(turns) {
  return turns.slice(-6).map(({text, reply}) => {
    const result = reply.result || {}, status = statusOf(reply);
    const said = reply.tool === 'answer' ? (status === 'answered' ? result.summary || show(result.conclusion) : result.reason || result.summary || result.error)
      : reply.tool === 'write' ? (reply.done?.labels?.[0] || result.error || 'Write preview') : reply.tool === 'semantic' ? `Term: ${result.term || ''}` : '';
    const source = reply.tool === 'write' && result.source ? [result.source.kind, result.source.sourceId || ''].join(' ').trim() : '';
    // `understood`: the full request the server read for this turn, so a later follow-up builds on it, not on the typed text
    const understood = result.questionStandalone || reply.questionStandalone || result.questionEnglish || reply.questionEnglish;
    return {text: cut(text), ...(understood ? {understood: cut(understood)} : {}), status,
      ...(said ? {said: cut(said)} : {}), ...(source ? {source: cut(source)} : {})};
  });
}

// The conversation sidebar: New chat, this account's chat sessions on this data source, Clear history.
function History({items, current, onOpen, onClear, onHide, onNew, busy}) {
  return <aside className="pf-history" aria-label={t('History')} data-history>
    <div className="pf-history-head">
      <button type="button" className="button secondary small pf-new-chat" onClick={onNew} disabled={busy}>{t('New chat')}</button>
      <button type="button" className="pf-icon-button" aria-label={t('Hide history')} title={t('Hide history')} onClick={onHide}>«</button></div>
    <h3 className="pf-eyebrow">{t('History')}</h3>
    {!items.length ? <p className="pf-muted">{t('Your questions on this data source appear here. Only this account sees them.')}</p>
      : <ol className="pf-history-list">{items.map(item => { const status = statusOf(item.turns.at(-1).reply), tone = STATUS_WORD[status]?.[1] || ''; return <li key={item.id}>
        <button type="button" data-history-item data-status={status} data-turns={item.turns.length} aria-current={current === item.id || undefined} onClick={() => onOpen(item)}>
          <span className="pf-history-q">{item.turns[0].text}</span>
          <span className="pf-history-meta"><i className={'pf-dot ' + tone} aria-hidden="true" />
            <span>{t(item.turns.length === 1 ? '{count} turn' : '{count} turns', {count: item.turns.length})}</span><time dateTime={item.at}>{when(item.at)}</time></span></button></li>; })}</ol>}
    {items.length > 0 && <button type="button" className="pf-linkish pf-history-clear" onClick={onClear}>{t('Clear history')}</button>}
  </aside>;
}

const SAMPLES = {
  'financial-services': ['What was the total value of current and savings account transactions in Singapore dollars on 5 November 2026?',
    'What is the cash advance limit on the corporate credit card programme?', 'What does facility mean?'],
  manufacturing: ['How many orders were placed in sales territory 4 in 2023?',
    'Does the due date recorded on sales order 51131 equal the delivery date originally promised in the order schedule?', 'What does reorder point mean?']};

const wide = () => { try { return matchMedia('(min-width: 1400px)').matches; } catch { return false; } };
const roomy = () => { try { return matchMedia('(min-width: 1100px)').matches; } catch { return true; } };

export default function Ask({ctx}) {
  const model = useMemo(() => buildModel(ctx.workspace), [ctx.workspace]);
  const [question, setQuestion] = useState('');
  const [store, setStore] = useState(() => readSessions(ctx));     // {sessions, current}: the current session stays until New chat
  const past = session => (session?.turns || []).map(turn => ({...turn, past: true}));
  const [turns, setTurns] = useState(() => past(store.sessions.find(item => item.id === store.current)));   // newest last
  const [pending, setPending] = useState(null);
  const [error, setError] = useState('');
  const [panel, setPanel] = useState(false);
  const [side, setSide] = useState(null);           // {title, steps}: the steps shown in the side panel
  const [live, setLive] = useState(null);           // steps of the running request, read from the server
  const current = store.current;
  const setCurrent = id => setStore(previous => ({...previous, current: id}));
  const [showHistory, setShowHistory] = useState(wide);
  const [head, setHead] = useState(null);           // the page header, where the Connections button goes
  const stopPolling = useRef(() => {});
  const thread = useRef(null);
  useEffect(() => () => stopPolling.current(), []);
  useEffect(() => { setHead(document.querySelector('.pagehead')); }, []);
  useEffect(() => { thread.current?.lastElementChild?.scrollIntoView?.({block: 'start'}); }, [turns.length]);
  useEffect(() => { if (pending) thread.current?.lastElementChild?.scrollIntoView?.({block: 'end'}); }, [Boolean(pending), live?.length]);
  // below 1100 px only one side area is open at a time, so the thread is never squeezed
  const toggleHistory = open => { setShowHistory(open); if (open && !roomy()) setSide(null); };
  const openSteps = (title, steps) => { if (!steps?.length) return; setSide({title, steps}); if (!roomy()) setShowHistory(false); };
  // the current session follows its turns into storage; the sessions stay newest first
  useEffect(() => {
    let sessions = store.sessions;
    if (current && turns.length) {
      const session = {id: current, account: ctx.principal?.id, dataset: ctx.dataset, at: turns.at(-1).at, turns: turns.slice(-TURNS).map(slim)};
      sessions = [session, ...sessions.filter(item => item.id !== current)].sort((a, b) => String(b.at).localeCompare(String(a.at))).slice(0, SESSIONS);
      setStore(previous => ({...previous, sessions}));
    }
    writeSessions(ctx, {current, sessions});
  }, [turns, current]);
  const connections = useConnections(ctx);
  const icebergReady = icebergReadyOf(connections.data);
  // What "write this" refers to: the latest answer's first fact handle, and the fill-in form it cites.
  const lastAnswer = [...turns].reverse().find(turn => turn.reply.tool === 'answer')?.reply.result;
  const sources = new Map((ctx.workspace.sources || []).map(item => [item.id, item]));
  const context = {lastAnswer: (lastAnswer?.facts || []).find(fact => fact.detail)?.detail.id || null,
    lastForm: (lastAnswer?.citations || []).map(item => item.source).find(id => sources.get(id)?.kind === 'pdf') || null};
  async function ask(text, options) {
    if (!text || pending) return;
    const id = requestId(), history = historyOf(turns);
    setQuestion(''); setPending({text, since: Date.now()}); setError(''); setLive([]);
    if (!current) setCurrent(requestId());
    stopPolling.current = pollProgress(ctx, id, setLive);
    try {
      const response = await fetch('/api/ask', withLang({method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({dataset: ctx.dataset, text, context, requestId: id, ...(history.length ? {history} : {}),
          ...(options?.hint ? {hint: true} : {})})}));
      let reply = await response.json().catch(() => ({}));
      if (response.status === 404 && !reply.tool) {     // a server without the router: the answer route alone
        const result = await ctx.request('/api/answer', {method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({dataset: ctx.dataset, question: text})});
        reply = {tool: 'answer', result, trace: result.trace};
      } else if (!response.ok && !reply.tool) throw new Error(reply.error || t('The question could not be sent.'));
      if (reply.tool === 'connections') { setPanel(true); connections.reload(); }
      if (reply.tool === 'write' && !response.ok) reply = {tool: 'write', result: {error: reply.result?.error || t('The write preview failed.')}, trace: reply.trace};
      setTurns(previous => [...previous, {text, reply, id: Date.now(), at: new Date().toISOString()}]);
    } catch (failure) { setError(failure.message); setQuestion(text); }
    stopPolling.current(); setLive(null);
    setPending(null);
  }
  // Show a stored session as it ended. Nothing runs: no request, no model call, no preview. It becomes the current
  // session, so the next question continues it.
  function open(session) {
    if (pending) return;
    if (!wide()) setShowHistory(false);
    setTurns(past(session)); setCurrent(session.id); setSide(null); setError('');
  }
  const newChat = () => { if (pending) return; setTurns([]); setSide(null); setCurrent(null); setError(''); };
  const clear = () => { if (pending) return; writeSessions(ctx, {current: null, sessions: []}); setStore({current: null, sessions: []}); setTurns([]); setSide(null); };
  const submit = event => { event.preventDefault(); ask(question.trim()); };
  const keys = event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) submit(event); };
  // Cancel drops a write preview from the session; Close after a confirmed write keeps the card as it ended
  const drop = id => setTurns(previous => previous.flatMap(turn => turn.id !== id ? [turn] : turn.reply.done ? [{...turn, past: true}] : []));
  const done = (id, result) => setTurns(previous => previous.map(turn => turn.id === id ? {...turn, reply: {...turn.reply, done: result}} : turn));
  const empty = !turns.length && !pending;
  const samples = SAMPLES[ctx.dataset] || [];
  const columns = [showHistory && '232px', 'minmax(0, 1fr)', side && 'minmax(280px, 320px)'].filter(Boolean).join(' ');
  const connect = <button type="button" className="button secondary small pf-connect" aria-expanded={panel} data-open-connections
    onClick={() => { setPanel(!panel); connections.reload(); }}>{t('Connections')}</button>;
  return <div className="pf-page pf-ask" style={{gridTemplateColumns: columns}}>
    {head ? createPortal(connect, head) : <div className="pf-connect-row">{connect}</div>}
    {panel && <div className="pf-ask-wide"><ConnectionsPanel ctx={ctx} connections={connections} onClose={() => setPanel(false)} /></div>}
    {showHistory && <History items={store.sessions} current={current} onOpen={open} onClear={clear} onHide={() => toggleHistory(false)} onNew={newChat} busy={Boolean(pending)} />}
    <section className={'pf-chat' + (empty ? ' is-empty' : '')} aria-label={t('Conversation')} data-chat>
      {!showHistory && <div className="pf-chat-tools">
        <button type="button" className="pf-icon-button labelled" aria-label={t('Show history')} onClick={() => toggleHistory(true)}>» {t('History')}</button>
        <button type="button" className="pf-icon-button labelled" onClick={newChat} disabled={Boolean(pending)}>{t('New chat')}</button></div>}
      <div className="pf-chat-inner">
        {empty && <h2 className="pf-greeting" data-greeting>{t('Ask {name} a question', {name: ctx.workspace?.title || t('this data source')})}</h2>}
        <div className="pf-thread" ref={thread}>
          {turns.map(turn => {
            const common = {ctx, model, icebergReady, onOpenSteps: openSteps};
            return turn.reply.tool === 'answer'
              ? <AnswerCard key={turn.id} {...common} text={turn.text} answer={turn.reply.result} trace={turn.reply.trace || turn.reply.result?.trace} onAsk={ask}
                past={turn.past} trimmed={turn.trimmed} />
              : <ToolCard key={turn.id} {...common} turn={turn} onClose={() => drop(turn.id)} onDone={result => done(turn.id, result)} />;
          })}
          {pending && <div className="pf-turn" data-pending-turn>
            <h3 className="pf-user-bubble" data-user-bubble>{pending.text}</h3>
            <div className="pf-reply pending"><InlineSteps model={model} steps={live || []} live />
              <p className="pf-muted pf-elapsed-line"><Elapsed since={pending.since} /> {t('since the question was sent')}</p></div></div>}
        </div>
        <form className="pf-composer" onSubmit={submit}>
          {pending && <p className="pf-sr" role="status">{serverText(live?.find(step => step.state === 'running')?.title) || t('Sending the request')}</p>}
          {error && <p className="pf-error" role="alert">{serverText(error)}</p>}
          <div className="pf-composer-bar"><label htmlFor="pf-question" className="pf-sr">{t('Question')}</label>
            <textarea id="pf-question" rows={2} maxLength={2000} value={question} onChange={event => setQuestion(event.target.value)} onKeyDown={keys}
              placeholder={t('Ask about the data or the documents')} />
            <button type="submit" className="button primary" disabled={Boolean(pending) || !question.trim()}>{pending ? t('Answering…') : t('Ask')}</button></div>
          <p className="pf-composer-note">{t('Figures come from SQL. Statements come from cited passages.')}
            {ctx.readOnly && <span className="pf-tag" data-readonly-note>{t('Read-only account')}</span>}</p>
          {empty && samples.length > 0 && <ul className="pf-samples" aria-label={t('Sample requests')}>
            {samples.map(text => <li key={text}><button type="button" className="pf-sample" data-sample onClick={() => setQuestion(text)}>{text}</button></li>)}</ul>}
        </form>
      </div>
    </section>
    {side && <ProgressPanel model={model} run={side} onClose={() => setSide(null)} />}
  </div>;
}
