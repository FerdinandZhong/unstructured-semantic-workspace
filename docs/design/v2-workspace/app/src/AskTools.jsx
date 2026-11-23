// Step 6 tools beside the answer card (plan 2026-11-22-v2-step6-harness, contracts C1-C3):
// the semantic context of an answer, the semantic card of a term, the write card (preview, then confirm) and the
// connections panel. Every write is admin-only and happens only on the "Confirm write" or "Save" click.
import {useEffect, useState} from 'react';
import {Tag} from './ui.jsx';
import {humanize, shortSource, t, serverText, withLang} from './lib.js';
import {InlineSteps} from './AskProgress.jsx';

const TARGET_KEY = 'v2-ask-write-target';
export function sessionTarget() { try { return sessionStorage.getItem(TARGET_KEY) || 'postgres'; } catch { return 'postgres'; } }
export function rememberTarget(id) { try { sessionStorage.setItem(TARGET_KEY, id); } catch { /* per-tab convenience only */ } }

// Business name of a written column: the model's own title or spelling of that column; the raw name stays as a tooltip.
const ADDED = {v2_question: 'Question asked', v2_written_at: 'Written at', v2_principal: 'Written by', v2_source_id: 'Source document',
  v2_source_revision: 'Source revision', v2_row_key: 'Row in the form'};
function columnNames(workspace) {
  const names = new Map();
  for (const field of workspace?.knowledge?.structured?.fields || []) {
    const column = String(field.name).split('/').at(-1), key = column.toLowerCase();
    if (!names.has(key)) names.set(key, field.title || humanize(column));
  }
  return raw => (ADDED[raw] && t(ADDED[raw])) || names.get(String(raw).toLowerCase()) || humanize(raw);
}

const short = (text, n) => { const s = String(text || '').replace(/\s+/g, ' ').trim(); return s.length > n ? s.slice(0, n - 1) + '…' : s; };

// ---------------------------------------------------------------- C1: semantic context of an answer

export function SemanticContext({ctx, model, answer}) {
  const s = answer.semantics;
  const live = s && s.mode !== 'off' && s.release;
  const used = (s?.definitionsUsed?.length ? s.definitionsUsed : answer.definitions || []).filter(id => model.defs.get(id));
  return <section className="pf-semantic" aria-label={t('Semantic context')} data-semantic>
    <div className="pf-semantic-head"><h3>{t('Semantic context')}</h3>
      <span className="pf-muted" data-release>{live ? (s.release.catalogVersion ? t('Published release {version} · catalog {catalog}', {version: s.release.version, catalog: s.release.catalogVersion}) : t('Published release {version}', {version: s.release.version}))
        : t('No published release: answers use the model only')}</span></div>
    {live && <div className="pf-semantic-row"><span className="pf-locator">{t('Concepts in the question')}</span>
      {s.concepts?.length ? <ul className="pf-inline">{s.concepts.map(concept => <li key={concept.id} data-concept>
        <a className="text-link" href={ctx.href('concepts', concept.id)}>{concept.label}</a>
        <span className="pf-muted"> “{concept.matched}”{concept.via === 'synonym' ? ' ' + t('(synonym)') : ''}</span>{' '}
        <Tag tone={concept.used ? 'green' : ''}>{concept.used ? t('used by the plan') : t('not used')}</Tag></li>)}</ul>
        : <span className="pf-muted">{t('No approved concept is named in the question.')}</span>}</div>}
    {used.length > 0 && <div className="pf-semantic-row"><span className="pf-locator">{t('Definitions used')}</span>
      <ul className="pf-inline defs">{used.map(id => { const def = model.defs.get(id); return <li key={id} data-definition>
        <strong>{def.label}</strong>{def.description && <span className="pf-muted"> — {short(def.description, 140)}</span>}</li>; })}</ul></div>}
  </section>;
}

// ---------------------------------------------------------------- the semantic tool: what a term means

export function SemanticCard({ctx, model, result}) {
  return <div className="pf-tool" data-tool="semantic">
    <p className="pf-muted">{t('From the concept catalog and the model. No model call.')}</p>
    {!result.concepts?.length && !result.definitions?.length && <p className="pf-muted">{t('Nothing in the catalog or the model matches “{term}”.', {term: result.term})}</p>}
    {result.concepts?.length > 0 && <><h3>{t('Concepts')}</h3><ul className="pf-plain">{result.concepts.map(concept => <li key={concept.id}>
      <a className="text-link" href={ctx.href('concepts', concept.id)}><strong>{concept.label}</strong></a>{' '}
      <Tag tone={concept.status === 'corrected' ? 'green' : 'amber'}>{t(concept.status === 'corrected' ? 'approved' : concept.status)}</Tag>
      {concept.definition && <p className="pf-muted">{concept.definition}</p>}
      {concept.definitions?.length > 0 && <p className="pf-muted">{t('Linked to: {list}', {list: concept.definitions.map(id => model.label(id)).join(' · ')})}</p>}</li>)}</ul></>}
    {result.definitions?.length > 0 && <><h3>{t('Definitions in the model')}</h3><ul className="pf-plain">{result.definitions.map(def => <li key={def.id}>
      <strong>{model.defs.get(def.id)?.label || def.label}</strong><p className="pf-muted">{short(def.definition, 220)}</p></li>)}</ul></>}
    {result.passages?.length > 0 && <><h3>{t('Passages')}</h3><ul className="pf-citations">{result.passages.map(item => <li key={item.chunkId}>
      <blockquote>{short(item.quote, 400)}</blockquote>
      <div className="pf-cite-meta"><span data-passage-source>{(ctx.workspace.sources || []).find(source => source.id === item.sourceId)?.title || shortSource(item.sourceId)}</span>
        {item.sourceId && <a className="text-link" href={ctx.href('evidence', shortSource(item.sourceId) + '/' + item.chunkId)}>{t('Open at passage')}</a>}</div></li>)}</ul></>}
  </div>;
}

// ---------------------------------------------------------------- C3: the write card

function SparkJob({preview}) {
  const spark = preview.spark;
  const [copied, setCopied] = useState(false);
  if (!spark) return null;
  // the shown script hides restricted identifiers; the copy and the download are the real script from the server
  const url = '/api/ask/write/script?' + new URLSearchParams({fingerprint: preview.fingerprint});
  const copy = () => fetch(url).then(response => response.ok ? response.text() : Promise.reject()).then(text => navigator.clipboard?.writeText(text))
    .then(() => setCopied(true), () => setCopied(false));
  return <details className="pf-spark" data-spark><summary>{t('Spark job')}</summary>
    <div className="pf-actions"><button type="button" className="button secondary small" onClick={copy}>{copied ? t('Copied') : t('Copy script')}</button>
      <a className="button secondary small" href={url} download>{t('Download script')}</a></div>
    <h4>{t('Script')}</h4><p className="pf-muted">{t('Restricted identifiers are hidden in this view. The copied or downloaded script holds the real values.')}</p><pre className="pf-code" data-script>{spark.script}</pre>
    <h4>{t('Settings')}</h4><pre className="pf-code small">{JSON.stringify(spark.spec, null, 2)}</pre>
    <h4>{t('Submit command')}</h4><pre className="pf-code small" data-command>{spark.command}</pre>
  </details>;
}

// The Iceberg state in plain words, only from what the server returned.
function icebergState(preview, result) {
  const job = result?.job;
  if (job) return {tone: job.status === 'failed' || job.status === 'unknown' ? 'red' : job.status === 'completed' ? 'green' : 'blue',
    word: {submitted: t('Submitted'), running: t('Submitted, running'), completed: job.runId == null && /Trino/.test(job.name) ? t('Written and read back') : t('Completed'), failed: t('Failed')}[job.status] || serverText(job.status), job};
  if (!result && preview.target?.submits) return {tone: 'blue', word: /Trino/.test(preview.target.label || '') ? t('Will be written through Trino when you confirm') : t('Will be submitted when you confirm')};
  const chosen = (preview.warnings || []).some(text => /local database was chosen/.test(text));
  return {tone: '', word: chosen ? t('Generated, not submitted — the local database was chosen') : t('Generated, not submitted — no live connection')};
}

const rowsPhrase = (n, isNew) => t(`{count} ${isNew ? 'new ' : ''}${n === 1 ? 'row' : 'rows'}`, {count: n});

// The table as it now stands in the local database, read back by the server after the write (never the preview).
function Snapshot({snap, label}) {
  const when = new Date(snap.readAt);
  return <div className="pf-snapshot" data-snapshot>
    <p data-snapshot-count><strong>{rowsPhrase(snap.added, true)}</strong>{t('; the table holds {rows}.', {rows: t(snap.total === 1 ? '{count} row' : '{count} rows', {count: snap.total.toLocaleString('en-GB')})})}
      {snap.total > snap.rows.length ? ' ' + t('First {count} shown.', {count: snap.rows.length}) : ''}</p>
    {snap.rows.length > 0 && <div className="pf-table-wrap pf-rows-wrap"><table><thead><tr>{snap.columns.map(column => <th key={column} title={column}>{label(column)}</th>)}</tr></thead>
      <tbody>{snap.rows.map((row, index) => <tr key={index}>{snap.columns.map(column => <td key={column}>{String(row[column] ?? '')}</td>)}</tr>)}</tbody></table></div>}
    <p className="pf-muted" data-snapshot-source>{t('Read from {table} in the local database at {time}. System columns are hidden.', {table: snap.table, time: isNaN(when) ? snap.readAt : when.toLocaleString('en-GB')})}</p>
  </div>;
}

// `past`: a card stored in a chat session, shown as it ended. Nothing runs and Confirm is not offered (an approval
// expires); Preview again asks for a new preview only when pressed.
export function WriteCard({ctx, model, source: first, preview: given, trace: givenTrace, onClose, onTrace, onOpenSteps, icebergReady, past, done, onDone}) {
  const [source, setSource] = useState(first || given?.source || null);       // grows when the user accepts a suggested group
  const label = columnNames(ctx.workspace);
  const [target, setTarget] = useState(given?.target?.id || (icebergReady ? sessionTarget() : 'postgres'));
  const [preview, setPreview] = useState(given || null);
  const [state, setState] = useState(given ? {} : {busy: 'preview'});
  const [result, setResult] = useState(done || null);
  const [old, setOld] = useState(Boolean(past));
  const [trace, setTrace] = useState(givenTrace || []);
  const traced = steps => { if (!steps?.length) return; setTrace(steps); onTrace?.(steps); };
  const later = (steps, more) => more?.length ? [...steps.filter(step => !more.some(item => item.key === step.key)), ...more] : steps;
  useEffect(() => {
    if (given && target === given.target?.id && source === (first || given?.source || null)) return;
    if (!source) return;
    let live = true;
    setState({busy: 'preview'}); setResult(null);
    fetch('/api/ask/write/preview', withLang({method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({dataset: ctx.dataset, source, target})}))
      .then(async response => { const out = await response.json(); if (live) traced(out.trace); if (!response.ok) throw new Error(out.error || t('The preview failed.')); return out; })
      .then(out => live && (setPreview(out), setState({})), error => live && setState({error: error.message}));
    return () => { live = false; };
  }, [target, source]);
  async function confirm() {
    setState({busy: 'confirm'});
    try {
      const response = await fetch('/api/ask/write/confirm', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({fingerprint: preview.fingerprint, approval: preview.approval})});
      const out = await response.json();
      traced(later(trace, out.trace));
      if (!response.ok) throw new Error(out.error || t('The write failed.'));
      setResult(out); setState({}); onDone?.(out);
    } catch (error) { setState({error: error.message}); }
  }
  async function check() {
    setState({busy: 'status'});
    try {
      const response = await fetch('/api/ask/write/status', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({job: result.job.name})});
      const out = await response.json();
      if (!response.ok) throw new Error(out.error || t('The job status could not be read.'));
      setResult({...result, job: out}); setState({}); onDone?.({...result, job: out});
    } catch (error) { setState({error: error.message}); }
  }
  if (ctx.readOnly) return null;
  const ice = preview && icebergState(preview, result);
  const viaTrino = /Trino/.test(preview?.target?.label || '');
  const general = (preview?.warnings || []).filter(text => !/^Iceberg:|Iceberg connection|^Spark job:/.test(text));
  // the server's own words for the Iceberg part: its confirm label, else its preview warning
  const iceNotes = result ? (result.labels || []).filter(text => /^Iceberg/.test(text))
    : (preview?.warnings || []).filter(text => /^Iceberg:|Iceberg connection/.test(text));
  const rows = (preview?.tables || []).reduce((sum, table) => sum + (table.rowCount || 0), 0);
  const discovered = source?.kind === 'document' || (preview?.tables || []).some(table => table.discovered);
  return <section className="pf-card pf-write" aria-label={t('Write preview')} data-write onClick={event => event.stopPropagation()}>
    <div className="pf-card-head"><h3>{!source ? t('Write preview') : discovered ? (source.sourceIds?.length > 1 ? t('Write these documents to a new table') : t('Write this document to a new table')) : source.kind === 'form' ? (source.sourceIds?.length > 1 ? t("Load these forms' rows") : t("Load this form's rows"))
      : source.kind === 'call' ? t("Write this recording's stated values") : t('Save this result as a table')}</h3>
      <span className="pf-muted">{t('Nothing is written until you select Confirm write.')}</span></div>
    {model && <InlineSteps model={model} steps={trace} onOpenPanel={onOpenSteps && (() => onOpenSteps(trace))} />}
    {state.busy === 'preview' && <p className="pf-muted" role="status">{t('Preparing the preview…')}</p>}
    {state.error && <p className="pf-error" role="alert">{serverText(state.error)}</p>}
    {preview && <>
      <p className="pf-muted" data-target>{t('Target:')} <strong>{serverText(preview.target?.label)}</strong>
        {icebergReady && source && !result && !old && <label className="pf-check inline"><input type="checkbox" checked={target === 'iceberg_cde'} disabled={Boolean(state.busy)}
          onChange={event => { const id = event.target.checked ? 'iceberg_cde' : 'postgres'; rememberTarget(id); setTarget(id); }} /> {t('Also submit to Iceberg')}</label>}</p>
      {(preview.tables || []).map(table => <div key={table.name} className="pf-write-table" data-write-table>
        <p><strong>{table.name}</strong> · {t(table.rowCount === 1 ? '{count} row' : '{count} rows', {count: table.rowCount})}{table.icebergName ? <span className="pf-muted"> · {t('Iceberg name {name}', {name: table.icebergName})}</span> : null}</p>
        {table.discovered && <p data-proposed-table>{t('Proposed table name {name}', {name: table.proposed})}</p>}
        {table.discovered ? <ul className="pf-plain" data-discovered-columns>{table.columns.filter(column => !column.name.startsWith('v2_')).map(column => <li key={column.name} data-column={column.name}>
          <code>{column.name}</code> <em>{column.type}</em> · <span className="pf-muted">{column.definition ? model?.label?.(column.definition) || column.definition : t('no definition yet')}</span></li>)}</ul>
          : <p className="pf-cols">{table.columns.map(column => <span className="pf-col" key={column.name} title={column.name}>{label(column.name)} <em>{column.type}</em></span>)}</p>}
        {table.sample?.length > 0 && <div className="pf-table-wrap pf-rows-wrap"><table><thead><tr>{Object.keys(table.sample[0]).map(key => <th key={key} title={key}>{label(key)}</th>)}</tr></thead>
          <tbody>{table.sample.map((row, index) => <tr key={index}>{Object.keys(table.sample[0]).map(key => <td key={key}>{String(row[key] ?? '')}</td>)}</tr>)}</tbody></table></div>}
      </div>)}
      {!result && !old && preview.suggested?.sources?.length > 0 && <div className="pf-suggest" data-suggested>
        <p><strong>{t('Suggested group.')}</strong> {preview.suggested.sources.map(item => `${item.title} (${t(item.rows === 1 ? '{count} row' : '{count} rows', {count: item.rows})})`).join(t(', '))}{t('.')}
          {' '}{preview.suggested.hint ? <><span data-ai-hint>{preview.suggested.hint.text}</span> <span className="pf-muted">{serverText(preview.suggested.hint.label)}</span></>
            : <>{serverText(preview.suggested.reason)} <span className="pf-muted">{t('Derived from the evidence links; check the preview before you confirm.')}</span></>}</p>
        <button type="button" className="button secondary small" disabled={Boolean(state.busy)} data-write-together
          onClick={() => setSource({...source, sourceIds: [...(source.sourceIds || [source.sourceId]), ...preview.suggested.sources.map(item => item.sourceId)]})}>
          {t('Write together · {count} sources', {count: (source.sourceIds?.length || 1) + preview.suggested.sources.length})}</button>
      </div>}
      {general.length > 0 && <ul className="pf-warnings">{general.map(text => <li key={text}>{serverText(text)}</li>)}</ul>}
      {viaTrino ? <div className="pf-write-parts">
        <div className="pf-write-part" data-part="iceberg"><h4>{t('Iceberg (through Trino)')}</h4>
          <p data-iceberg-state><Tag tone={ice.tone}>{ice.word}</Tag></p>
          {result ? <>
            <p data-iceberg-result>{ice.job?.message}</p>
            {(result.iceberg || []).map(item => <p key={item.table} className="pf-muted">{t('{table}: {written} new, read back {found} of {expected}', {table: item.table, written: item.written, found: item.found, expected: item.expected})}</p>)}
            <p className="pf-muted" data-local-result>{t('{label} (working copy).', {label: serverText(result.labels?.[0])})}</p>
          </> : <p className="pf-muted">{t('{rows} will be written to {tables}.', {rows: t(rows === 1 ? '{count} row' : '{count} rows', {count: rows}), tables: (preview.tables || []).map(table => table.icebergName).join(t(' and '))})}{' '}
            {t('A repeated write adds nothing. A working copy is kept in the local database.')}</p>}</div>
        <div className="pf-write-part" data-part="spark"><h4>{t('Spark job (download)')}</h4>
          <p data-spark-state><Tag tone="">{t('Generated for download, not submitted')}</Tag></p>
          <p className="pf-muted">{t('The same rows as a Spark job. Download the script and run it yourself, with your own arguments.')}
            <code> --target-namespace catalog.schema</code>{' '}{t('writes to another schema.')}</p>
          <SparkJob preview={preview} /></div>
      </div> : <div className="pf-write-parts">
        <div className="pf-write-part" data-part="local"><h4>{t('Local database')}</h4>
          {result ? <p data-local-result>{serverText(result.labels?.[0]) || t('Written: {count} rows', {count: result.postgres?.written})}{result.postgres?.found != null
            ? ' · ' + t('read back {found} of {expected}', {found: result.postgres.found, expected: result.postgres.expected}) : ''}</p>
            : <p className="pf-muted">{t('{rows} will be written to the private PostgreSQL.', {rows: t(rows === 1 ? '{count} row' : '{count} rows', {count: rows})})} {t('A repeated write adds nothing.')}</p>}</div>
        <div className="pf-write-part" data-part="iceberg"><h4>{t('Iceberg (Cloudera Data Engineering)')}</h4>
          <p data-iceberg-state><Tag tone={ice.tone}>{ice.word}</Tag></p>
          {ice.job && <p className="pf-muted">{t('Job {name}', {name: ice.job.name})}{ice.job.runId ? ' · ' + t('run {id}', {id: ice.job.runId}) : ''}{t('. ')}{serverText(ice.job.message)}</p>}
          {iceNotes.map(text => <p key={text} className="pf-muted">{serverText(text)}</p>)}
          <SparkJob preview={preview} /></div>
      </div>}
      {result?.snapshots?.length > 0 && <section className="pf-snapshots" aria-label={t('The table now')} data-snapshots>
        <h4>{viaTrino ? t('Working copy in the local database · the table now') : t('Local database · the table now')}</h4>
        {result.snapshots.map(snap => <Snapshot key={snap.table} snap={snap} label={label} />)}</section>}
      {result?.snapshotError && <p className="pf-muted" data-snapshot-error>{serverText(result.snapshotError)}</p>}
      {old ? <div className="pf-actions" data-past-write>
        <p className="pf-muted">{t('Shown from this chat as it ended; nothing runs again. An approval expires, so Confirm is not offered.')}</p>
        {source && <button type="button" className="button secondary" data-preview-again onClick={() => { setOld(false); setResult(null); setSource({...source}); }}>{t('Preview again')}</button>}
      </div> : <div className="pf-actions">
        {!result && <button type="button" className="button primary" disabled={Boolean(state.busy)} onClick={confirm} data-confirm-write>{state.busy === 'confirm' ? t('Writing…') : t('Confirm write')}</button>}
        {result?.job && !['completed', 'failed'].includes(result.job.status) && <button type="button" className="button secondary" disabled={Boolean(state.busy)} onClick={check}>{t('Check job status')}</button>}
        <button type="button" className="button secondary" onClick={onClose}>{result ? t('Close') : t('Cancel')}</button>
      </div>}
    </>}
  </section>;
}

// ---------------------------------------------------------------- C2: connections

const FIELD_LABELS = {
  cde_url: 'CDE virtual cluster endpoint', cde_ca_bundle: 'CA bundle path', cdp_endpoint: 'CDP control plane endpoint',
  access_key_id: 'Access key ID', access_key_secret: 'Access key secret', cde_token: 'CDE token (instead of the access key)',
  iceberg_catalog: 'Iceberg catalog', iceberg_namespace: 'Iceberg namespace', polaris_uri: 'Polaris address',
  polaris_warehouse: 'Polaris warehouse', polaris_scope: 'Polaris scope', cde_polaris_secret_name: 'CDE secret name for Polaris',
  cde_polaris_secret_key: 'Key in that CDE secret', enabled: 'Submit jobs (true or false)'};

function Checks({test}) {
  if (!test) return <p className="pf-muted">{t('Not tested yet.')}</p>;
  return <ul className="pf-checks" data-checks>{test.checks.map(check => <li key={check.name} className={check.ok ? 'ok' : 'bad'}>
    <strong>{check.ok ? t('Passed') : check.required === false ? t('No answer') : t('Failed')}</strong> {serverText(check.name)}{t(': ')}{serverText(check.message)}
    {check.required === false ? <span className="pf-muted"> {t('(for information)')}</span> : null}</li>)}</ul>;
}

function TargetCard({ctx, item, chosen, onChoose, onChanged}) {
  const [edits, setEdits] = useState({});
  const [state, setState] = useState({});
  const [test, setTest] = useState(item.lastTest);
  const read = async response => { const out = await response.json(); if (!response.ok) throw new Error(out.error || t('The request failed.')); return out; };
  const run = async (what, work) => { setState({busy: what}); try { await work(); setState({message: {save: t('Saved.'), import: t('Imported.'), test: ''}[what]}); } catch (error) { setState({error: error.message}); } };
  const save = () => run('save', async () => {
    const fields = Object.fromEntries(Object.entries(edits).filter(([key, value]) => !(item.fields[key]?.secret && value === '')));
    await read(await fetch('/api/connections/' + item.id, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({fields})}));
    setEdits({}); onChanged();
  });
  const importIt = () => run('import', async () => {
    await read(await fetch('/api/connections/' + item.id + '/import', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({from: 'workspace'})}));
    onChanged();
  });
  const testIt = () => run('test', async () => {
    setTest(await read(await fetch('/api/connections/' + item.id + '/test', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'})));
    onChanged();
  });
  const ready = item.configured && (item.kind === 'postgres' || test?.ok);
  return <article className="pf-target-card" data-connection={item.id}>
    <div className="pf-card-head"><h3>{serverText(item.label)}</h3>
      <div className="pf-tags"><Tag tone={item.configured ? 'green' : 'amber'}>{item.configured ? t('configured') : t('not configured')}</Tag>
        {test && <Tag tone={test.ok ? 'green' : 'red'}>{test.ok ? t('test passed') : t('test failed')}</Tag>}</div></div>
    {item.problem && <p className="pf-muted">{t('Cannot submit yet: {problem}', {problem: String(serverText(item.problem)).replace(/\b[a-z]+(?:_[a-z]+)+\b/g, name => FIELD_LABELS[name] ? t(FIELD_LABELS[name]) : name)})}</p>}
    {!ctx.readOnly && <label className="pf-check"><input type="radio" name="pf-write-target" checked={chosen === item.id} disabled={!ready}
      onChange={() => onChoose(item.id)} /> {t('Write target for this session')}{!ready ? ' ' + t('(configure and test it first)') : ''}</label>}
    {!ctx.readOnly && Object.keys(item.fields || {}).length > 0 && <div className="pf-conn-fields">{Object.entries(item.fields).map(([name, field]) =>
      <label key={name} className="pf-field">{FIELD_LABELS[name] ? t(FIELD_LABELS[name]) : name}{field.secret
        ? <span className="pf-secret"><span className="pf-muted" data-secret-state>{field.set ? t('set') : t('not set')}</span>
          <input type="password" autoComplete="off" placeholder={field.set ? t('Leave empty to keep') : t('Enter to set')} value={edits[name] ?? ''}
            onChange={event => setEdits({...edits, [name]: event.target.value})} /></span>
        : <input value={edits[name] ?? field.value ?? ''} onChange={event => setEdits({...edits, [name]: event.target.value})} />}</label>)}</div>}
    {state.error && <p className="pf-error" role="alert">{serverText(state.error)}</p>}
    {state.message && <p className="pf-ok" role="status">{state.message}</p>}
    <Checks test={test} />
    {!ctx.readOnly && <div className="pf-actions">
      {item.kind === 'iceberg_cde' && <button type="button" className="button secondary small" disabled={Boolean(state.busy)} onClick={importIt}>{t('Import from workspace settings')}</button>}
      {item.kind === 'iceberg_cde' && <button type="button" className="button primary small" disabled={Boolean(state.busy) || !Object.keys(edits).length} onClick={save}>{t('Save')}</button>}
      <button type="button" className="button secondary small" disabled={Boolean(state.busy)} onClick={testIt}>{state.busy === 'test' ? t('Testing…') : t('Test connection')}</button></div>}
  </article>;
}

export function useConnections(ctx) {
  const [data, setData] = useState(null);
  const load = () => fetch('/api/connections').then(response => response.ok ? response.json() : null).then(setData, () => setData(null));
  useEffect(() => { load(); }, []);
  return {data, reload: load};
}

export const icebergReadyOf = data => Boolean(data?.targets?.some(item => item.kind === 'iceberg_cde' && item.configured && item.lastTest?.ok));

export function ConnectionsPanel({ctx, connections, onClose}) {
  const [chosen, setChosen] = useState(sessionTarget());
  const choose = id => { rememberTarget(id); setChosen(id); };
  return <section className="pf-card pf-connections" aria-label={t('Connections')} data-connections>
    <div className="pf-card-head"><h3>{t('Connections')}</h3>
      <button type="button" className="button secondary small" onClick={onClose}>{t('Close')}</button></div>
    <p className="pf-muted">{t('Write targets of step 6. Secrets stay on the server and show only as set or not set.')}{ctx.readOnly ? ' ' + t('Changing a connection needs an admin account.') : ''}</p>
    {!connections.data ? <p className="pf-muted">{t('The connections could not be read.')}</p> :
      <div className="pf-targets">{connections.data.targets.map(item => <TargetCard key={item.id + JSON.stringify(item.lastTest)} ctx={ctx} item={item}
        chosen={chosen} onChoose={choose} onChanged={connections.reload} />)}</div>}
  </section>;
}
