// Step 6 progress: the steps the server recorded for one Ask request, shown inline at the top of each reply (a live
// compact list while it runs, one line when finished, the full list on demand) and optionally in a side panel (POST /api/ask, /api/answer and the write
// routes return `trace`; GET /api/ask/progress returns the steps of a request still running). Nothing here guesses a
// stage: a step shows only the state, duration and detail the server sent. Business names come from the model.
import {useState} from 'react';
import {humanize, t, serverText, HOW} from './lib.js';
import {Tag} from './ui.jsx';
import {fmtValue} from './Ask.jsx';

const STATE = {waiting: ['Waiting', ''], running: ['Running', 'blue'], done: ['Done', 'green'], skipped: ['Skipped', ''], failed: ['Failed', 'red']};
const TOOL = {answer: 'Answer a question', semantic: 'Explain a term', write: 'Write a table', connections: 'Show the connections'};
const STATUS = {answered: 'Answered', refused: 'Not answered', clarify: 'Needs clarification'};
const OPS = {'=': '=', '>': '>', '>=': '≥', '<': '<', '<=': '≤', between: 'between', in: 'in'};
const plural = (n, word) => t(`{count} ${word}${n === 1 ? '' : 's'}`, {count: n});
// A status the code knows is translated as a label; any other value is a code the server sent.
const status = key => STATUS[key] ? t(STATUS[key]) : serverText(key);
const shortId = id => String(id || '').replace(/^.*?:definition:[^/]+\//, '');

export const duration = ms => ms === null || ms === undefined ? '' : ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;

// Business names: "Customers › Risk rating" for a definition, a field or a "Table.Column" key.
export function namer(model) {
  const byShort = new Map([...model.defs.values()].filter(def => def.kind !== 'table').map(def => [shortId(def.id), def]));
  const table = name => [...model.tables.values()].find(item => item.name.toLowerCase() === String(name || '').toLowerCase());
  const def = id => model.defs.get(id) || byShort.get(shortId(id));
  const tableName = name => table(name)?.label || humanize(String(name || '').split('.').at(-1).replace(/^v2_ask_/, ''));
  const column = (tableRaw, columnRaw) => {
    const t = table(tableRaw);
    const field = t?.fields.find(item => item.column.toLowerCase() === String(columnRaw).toLowerCase());
    return `${t ? t.label : humanize(tableRaw)} › ${field?.title || humanize(columnRaw)}`;
  };
  return {def: id => def(id)?.label || humanize(String(id || '').split('/').at(-1)), table: tableName, column};
}

const opText = op => OPS[op] ? (/^[a-z]/.test(OPS[op]) ? t(OPS[op]) : OPS[op]) : op;
const filterText = (names, f) => `${names.def(f.field)} ${opText(f.op)} ${Array.isArray(f.value) ? f.value.join(t(' and ')) : fmtValue(f.value)}`;
function entityText(names, e) {
  const [left, value] = String(e.key || '').split(/=(.*)/s);
  const [tbl, c] = left.split('.');
  return `${c ? names.column(tbl, c) : humanize(left)} = ${value ?? ''}${e.how ? ` (${HOW[e.how] ? t(HOW[e.how]) : e.how})` : ''}`;
}

// The one plain line of a step, and the lines of its expanded detail.
function describe(step, names, all) {
  const d = step.detail || {};
  const stop = d.stopped && `${status(d.stopped.status)}${t(': ')}${serverText(d.stopped.reason) || ''}`;
  const calls = d.modelCalls ? [plural(d.modelCalls, 'model call') + (d.modelCalls > 1 ? t(' (the first reply was sent back and repaired)') : '')] : [];
  if (step.state === 'waiting') return {line: serverText(d.text) || t('Not started yet.'), more: []};
  if (step.state === 'running') return {line: t('Running now.'), more: []};
  const reached = all.find(s => s.key === 'decide')?.state === 'done';
  if (step.state === 'skipped' && !(step.key === 'query' && reached) && !d.text) return {line: t('Not reached: the request stopped earlier.'), more: []};
  switch (step.key) {
    case 'route': return {line: t(TOOL[d.tool] || 'Routed'), more: [d.why && serverText(d.why),
      d.standalone && t('Read with the earlier turns as: {text}', {text: d.standalone}), d.context && serverText(d.context)].filter(Boolean)};
    case 'understand': {
      const found = [d.entities?.length && plural(d.entities.length, 'record'), d.periods?.length && plural(d.periods.length, 'period'),
        d.numbers?.length && plural(d.numbers.length, 'number')].filter(Boolean);
      const line = stop || `${found.length ? t('{found} found', {found: found.join(t(', '))}) : t('No record, period or number found')}${d.concepts?.length ? t('; {text} matched', {text: plural(d.concepts.length, 'published concept')}) : ''}`;
      return {line, more: [...(d.entities || []).map(e => t('Record: {text}', {text: entityText(names, e)})), ...(d.periods || []).map(p => t('Period: {text}', {text: p})),
        ...(d.numbers || []).map(n => t('Number: {text}', {text: n})), ...(d.concepts || []).map(c => t('Published concept: {text}', {text: c}))]};
    }
    case 'plan': {
      const periods = all.find(s => s.key === 'understand')?.detail?.periods || [];
      const parts = [d.measure && t('Measure: {text}', {text: `${names.def(d.measure)}${d.aggregate ? ` (${d.aggregate})` : ''}`}),
        d.needs?.length && plural(d.needs.length, 'document need')].filter(Boolean);
      return {line: stop || (parts.length ? parts.join(t('; ')) : t('No measure and no document need')) + (d.modelCalls > 1 ? t('; repaired once') : ''),
        more: [...(d.filters || []).map(f => t('Filter: {text}', {text: filterText(names, f)})),
          ...(d.time || []).map(slot => t('Period: {text}', {text: periods[Number(String(slot).slice(1)) - 1] || slot})),
          ...(d.needs || []).map(n => t('Document need: {text}', {text: `${n.need}${n.type ? ` (${n.type})` : ''}`})), ...calls]};
    }
    case 'query': {
      const facts = d.facts || [];
      if (step.state === 'skipped') return {line: t('No measure in the plan, so no query.'), more: []};
      const tables = [...new Set(facts.flatMap(f => f.tables || []))].map(names.table);
      return {line: stop || (facts.length ? t('{queries} on {tables}; value {values}', {queries: facts.length === 1 ? t('1 query') : t('{count} queries', {count: facts.length}),
          tables: tables.join(t(', ')) || t('the model'), values: facts.map(f => fmtValue(f.value)).join(t(', '))}) : t('No fact returned')),
        more: facts.map(f => `${names.def(f.definition)} = ${fmtValue(f.value)}`), sql: facts.map(f => f.sql).filter(Boolean)};
    }
    case 'read': return {line: stop || t('{retrieved} retrieved, {cited} cited', {retrieved: plural(d.retrieved ?? 0, 'passage'), cited: d.cited ?? 0}),
      more: [...(d.documents || []).filter(Boolean).map(title => t('Cited document: {text}', {text: title})), ...calls]};
    case 'decide': {
      const c = d.comparison;
      const arithmetic = c && c.op !== 'all' ? [t('Computed by code: {left} {op} {right} → {value}', {left: fmtValue(c.left?.value), op: c.op, right: fmtValue(c.right?.value), value: fmtValue(c.value)})] : [];
      return {line: stop || (d.status === 'answered' ? `${t('Answered')}${d.conclusion !== null && d.conclusion !== undefined ? t(': ') + fmtValue(d.conclusion) : ''}` : status(d.status)),
        more: [...arithmetic, ...(d.refusalReason ? [t('Guard: {reason}', {reason: serverText(d.refusalReason)})] : [])]};
    }
    case 'lookup': return {line: t('{concepts}, {definitions}, {passages} for “{term}”', {concepts: plural(d.concepts ?? 0, 'concept'), definitions: plural(d.definitions ?? 0, 'definition'),
      passages: plural(d.passages ?? 0, 'passage'), term: d.term}), more: [t('From the catalog and the model; no model call.')]};
    case 'connections': return {line: t('{targets}, {configured} configured', {targets: plural(d.targets ?? 0, 'target'), configured: d.configured ?? 0}), more: []};
    case 'preview': {
      if (d.error) return {line: serverText(d.error), more: []};
      const rows = (d.tables || []).reduce((sum, item) => sum + item.rowCount, 0);
      return {line: t('{rows} in {tables}', {rows: plural(rows, 'row'), tables: plural((d.tables || []).length, 'table')}),
        more: (d.tables || []).map(item => t('{table}: {rows}, {columns}', {table: names.table(item.name), rows: plural(item.rowCount, 'row'), columns: plural(item.columns, 'column')}))};
    }
    case 'spark': return {line: d.name ? t('Job {name} generated', {name: d.name}) : t('Generated'), more: [t('Nothing is submitted by this step.')]};
    case 'local': {
      if (d.error) return {line: serverText(d.error), more: []};
      if (step.state !== 'done') return {line: serverText(d.text) || t('Not written.'), more: []};
      return {line: t('{written}; read back {found} of {expected}', {written: plural(d.written ?? 0, 'new row'), found: d.found, expected: d.expected}),
        more: (d.tables || []).map(item => t('{table}: {added} new, {rows} in the table', {table: names.table(item.table), added: item.added, rows: plural(item.total, 'row')}))};
    }
    case 'iceberg': return d.job ? {line: t('Job {name}: {status}', {name: d.job.name, status: serverText(d.job.status)}), more: [serverText(d.job.message)].filter(Boolean)}
      : {line: d.text ? serverText(d.text).replace(/^Iceberg[:：]\s*/, '') : t('Not submitted — no live connection'), more: []};
    default: return {line: serverText(d.error) || '', more: []};
  }
}

// The full step list: state, duration, one plain line, and the detail on expand.
function StepList({model, steps, live}) {
  const names = namer(model);
  return <ol className="pf-steps">{steps.map((step, index) => {
    // a live view carries states and timings only; the detail of each step arrives with the reply
    const {line, more, sql} = live && step.state === 'done' ? {line: t('Done. The detail arrives with the reply.'), more: []}
      : describe(step, names, steps);
    const [word, tone] = STATE[step.state] || [serverText(step.state), ''];
    return <li key={step.key} className={'pf-step ' + step.state} data-step={step.key} data-state={step.state}>
      <details>
        <summary><span className="pf-step-row"><span className="pf-step-num">{index + 1}</span><strong>{serverText(step.title)}</strong>
          <span className="pf-step-meta">{step.state === 'running' ? <span className="pf-spinner" aria-hidden="true" /> : null}<Tag tone={tone}>{t(word)}</Tag>
            <span className="pf-step-ms" data-ms>{duration(step.ms)}</span></span></span>
          <span className="pf-step-line" data-line>{line}</span></summary>
        <div className="pf-step-more" data-step-more>
          {more.length || sql?.length ? <>{more.map((text, i) => <p key={i}>{text}</p>)}
            {sql?.map((text, i) => <pre key={'s' + i} className="pf-code small" data-step-sql>{text}</pre>)}</> : <p className="pf-muted">{t('No further detail.')}</p>}
          {step.startMs !== null && step.startMs !== undefined && <p className="pf-muted">{t('Started {time} after the request.', {time: duration(step.startMs) || '0 ms'})}</p>}</div>
      </details>
    </li>;
  })}</ol>;
}

const ICON = {done: '✓', failed: '✕', running: '', waiting: '', skipped: '–'};

// Inline at the top of a reply, the way chat apps show their work: a live compact list while the request runs; when
// it has finished, one line ("6 steps · 6.1 s") that opens the full list in place or in the side panel.
export function InlineSteps({model, steps, live, onOpenPanel}) {
  const [open, setOpen] = useState(false);
  steps = steps || [];
  if (live) return <div className="pf-isteps live" data-steps aria-live="polite">
    {!steps.length ? <p className="pf-muted">{t("Waiting for the server's first recorded step…")}</p>
      : <ol className="pf-isteps-live">{steps.map(step => <li key={step.key} className={step.state} data-step={step.key} data-state={step.state}>
        <span className="pf-ist-icon" aria-hidden="true">{step.state === 'running' ? <span className="pf-spinner" /> : ICON[step.state]}</span>
        <span className="pf-ist-title">{serverText(step.title)}</span><span className="pf-step-ms">{duration(step.ms)}</span></li>)}</ol>}
  </div>;
  if (!steps.length) return null;
  const total = Math.max(0, ...steps.map(step => (step.startMs ?? 0) + (step.ms ?? 0)));
  const failed = steps.find(step => step.state === 'failed');
  return <div className={'pf-isteps' + (open ? ' open' : '')} data-steps>
    <div className="pf-isteps-head">
      <span className="pf-ist-ribbon" aria-hidden="true">{steps.map(step => <i key={step.key} className={step.state} />)}</span>
      <span className="pf-ist-summary" data-steps-summary>{failed ? t('{count} steps · {time} · stopped at {title}', {count: steps.length, time: duration(total), title: serverText(failed.title)}) : t('{count} steps · {time}', {count: steps.length, time: duration(total)})}</span>
      <button type="button" className="pf-linkish" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? t('Hide steps') : t('Show steps')}</button>
      {onOpenPanel && <button type="button" className="pf-linkish" onClick={onOpenPanel}>{t('Open in side panel')}</button>}
    </div>
    {open && <StepList model={model} steps={steps} />}
  </div>;
}

// The same steps in a side panel that takes its own grid column; closed by default.
export function ProgressPanel({model, run, onClose}) {
  return <aside className="pf-progress" aria-label={t('Steps')} data-progress>
    <div className="pf-progress-head"><div><h3>{t('Steps')}</h3><span className="pf-muted" data-progress-title title={run.title}>{run.title}</span></div>
      <button type="button" className="pf-icon-button" aria-label={t('Close steps')} onClick={onClose}>×</button></div>
    <StepList model={model} steps={run.steps} />
  </aside>;
}
