// Output C for one fill-in form: preview its rows, then write them only on "Confirm write". Shared by Evidence and Ask.
import {useEffect, useState} from 'react';
import {forget, t, serverText} from './lib.js';
import {Tag} from './ui.jsx';

export default function FormRows({ctx, sourceId, compact}) {
  const [view, setView] = useState(null);
  const [busy, setBusy] = useState('');
  const [toIceberg, setToIceberg] = useState(false);
  const load = (action) => {
    setBusy(action || 'load');
    if (action === 'confirm') setToIceberg(false);   // default off for every new confirm
    const ice = action === 'confirm' && toIceberg ? {iceberg: true, approval: view.iceberg.approval} : {};
    const extra = action === 'confirm' ? {fingerprint: view.fingerprint, ...ice} : {};
    const request = action
      ? ctx.request('/api/physical-rows/' + action, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({dataset: ctx.dataset, sourceId, ...extra})})
      : ctx.request('/api/physical-rows?' + new URLSearchParams({dataset: ctx.dataset, sourceId}));
    request.then(setView, error => setView({error: error.message})).finally(() => { setBusy(''); if (action === 'confirm') forget(ctx.dataset + ':formrows'); });
  };
  useEffect(() => { load(null); }, [sourceId]);
  if (!view) return null;
  // Two routes for a file. Link: its passages point at the model. Load: a fill-in form also becomes rows in the form tables.
  if (!view.error && !view.writeAvailable) return <p className="pf-route"><Tag tone="blue">{t('Link')}</Tag> {t('This document links to the model. It adds no rows.')}</p>;
  const kept = (view.rows || []).filter(row => row.status !== 'rejected');
  const written = kept.filter(row => row.status === 'written').length;
  const tableCount = new Set(kept.map(row => row.table)).size;
  const headline = !kept.length ? t('This is a fill-in form. Preview the rows it adds to the form tables.')
    : t('This form adds {rows} to {tables} · {state}', {rows: t(kept.length === 1 ? '{count} row' : '{count} rows', {count: kept.length}), tables: t(tableCount === 1 ? '{count} table' : '{count} tables', {count: tableCount}),
      state: written === kept.length ? t('written') : written ? t('{count} written', {count: written}) : t('not written yet')});
  if (compact) return <p className="pf-route"><Tag tone="amber">{t('Load')}</Tag> {headline} {t('Open Reading to preview and confirm.')}</p>;
  const cells = (view.rows || []).flatMap(row => row.cells.map(cell => ({row, cell})));
  const pending = (view.rows || []).some(row => row.status === 'previewed');
  const ice = view.iceberg;
  const iceReady = Boolean(ice && ice.available && ice.approval);
  const job = ice && ice.job;
  const lake = view.lakehouse;      // set when writes go to Iceberg through Trino
  return <section className="pf-card pf-form-rows" aria-label={t('Table rows from this form')}>
    <div className="pf-card-head"><h3><Tag tone="amber">{t('Load')}</Tag> {view.error ? t('Table rows from this form') : headline}</h3>
      <span className="pf-muted">{t('Rows are written only after you confirm. The Model step then shows them.')}</span></div>
    {view.error ? <p className="pf-error" role="alert">{serverText(view.error)}</p> : cells.length ?
      <div className="pf-table-wrap"><table><thead><tr><th>{t('Row')}</th><th>{t('Column')}</th><th>{t('Value')}</th><th>{t('Check')}</th></tr></thead>
        <tbody>{cells.map(({row, cell}, index) => <tr key={index}><td>{row.rowKey} <small>{t(row.status)}</small></td><td>{cell.column}</td>
          <td><code>{cell.value}</code></td><td>{t(cell.status)}{cell.reason ? <small> {serverText(cell.reason)}</small> : null}</td></tr>)}</tbody></table></div>
      : <p className="pf-muted">{t('No rows yet. Preview the rows before anything is written.')}</p>}
    {view.error || !lake ? null : <p className="pf-muted" data-lakehouse>{t('Target: {target} · {tables}.', {target: serverText(view.target), tables: lake.tables.join(t(', '))})} {t('A working copy is kept in the local database.')}{' '}
      {t('The same rows are available as a Spark job for download on the Ask step.')}</p>}
    {lake?.result ? <p className={lake.result.status === 'completed' ? 'pf-muted' : 'pf-error'} role="status" data-lakehouse-result>{serverText(lake.result.message)}</p> : null}
    {view.error || lake ? null : <p className="pf-muted">{t('Target:')} {serverText(view.target)}{ice ? <> · {ice.available ? t('also available') : t('not available')}{t(': ')}{ice.targets.join(t(', '))}{ice.reason ? ' (' + serverText(ice.reason) + ')' : ''}</> : null}</p>}
    {ice && ice.available ? <p className="pf-muted">{t('{rows} rows, {cells} cells, {jobs} Spark job.', {rows: ice.rows, cells: ice.cells, jobs: ice.jobs})}</p> : null}
    {job ? <p className={job.status === 'failed' || job.status === 'unknown' ? 'pf-error' : 'pf-muted'} role="status">{job.runId ? t('Iceberg job {name} (run {id}): {status}. {message}', {name: job.jobName, id: job.runId, status: serverText(job.status), message: serverText(job.message)})
      : t('Iceberg job {name}: {status}. {message}', {name: job.jobName, status: serverText(job.status), message: serverText(job.message)})}</p> : null}
    {ctx.readOnly ? <p className="pf-muted">{t('Writing rows needs an admin account.')}</p> :
      <div className="pf-actions"><button type="button" className="button secondary" disabled={Boolean(busy)} onClick={() => load('preview')}>{busy === 'preview' ? t('Previewing…') : t('Preview rows')}</button>
        {iceReady ? <label><input type="checkbox" checked={toIceberg} onChange={event => setToIceberg(event.target.checked)} /> {t('Also write to Iceberg')}</label> : null}
        <button type="button" className="button primary" disabled={Boolean(busy) || !(pending || (toIceberg && iceReady))} onClick={() => load('confirm')}>{busy === 'confirm' ? t('Writing…') : t('Confirm write')}</button>
        {job && job.runId ? <button type="button" className="button secondary" disabled={Boolean(busy)} onClick={() => load('iceberg-status')}>{t('Check job status')}</button> : null}</div>}
  </section>;
}

