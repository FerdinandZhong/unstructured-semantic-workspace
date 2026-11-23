// "Approve all": one confirm dialog that states exactly what is approved, then one request (one transaction).
// items: [{key, group, flagged}] — key is what the server route takes for one item.
import {useState} from 'react';
import {Confirm} from './ui.jsx';
import {t, tn, serverText} from './lib.js';

export default function ApproveAll({ctx, kind, items, noun, groupWord, flaggedWord, onDone}) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState({});
  if (ctx.readOnly) return null;
  const groups = new Map();
  items.forEach(item => groups.set(item.group, (groups.get(item.group) || 0) + 1));
  const flagged = items.filter(item => item.flagged).length;
  async function approve() {
    setState({busy: true});
    try {
      const out = await ctx.request('/api/catalog-correction/approve-all', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({dataset: ctx.dataset, kind, items: items.map(item => item.key)})});
      setOpen(false);
      setState({message: t('Approved {count}.', {count: out.approved}) + (out.skipped.approved ? ' ' + t('{count} were already approved.', {count: out.skipped.approved}) : '')});
      onDone(); ctx.refreshSteps?.();
    } catch (error) { setState({error: error.message}); }
  }
  return <>
    <button type="button" className="button primary small" disabled={!items.length} data-approve-all
      onClick={() => { setState({}); setOpen(true); }}>{t('Approve all {noun}', {noun: t(noun)})}{items.length ? ` · ${items.length}` : ''}</button>
    {state.message && <span className="pf-ok" role="status" data-approve-done>{state.message}</span>}
    {open && <Confirm title={t('Approve {count} {noun}?', {count: items.length, noun: t(noun)})} onClose={() => setOpen(false)} actions={<>
      <button type="button" className="button primary" disabled={state.busy} onClick={approve} data-approve-confirm>{state.busy ? t('Approving…') : t('Approve {count}', {count: items.length})}</button>
      <button type="button" className="button secondary" disabled={state.busy} onClick={() => setOpen(false)}>{t('Cancel')}</button></>}>
      <p data-approve-count>{tn('This approves {count} {noun} that are shown and not yet reviewed.', {count: <strong>{items.length}</strong>, noun: t(noun)})}</p>
      <p className="pf-muted">{tn('By {group}: {list}', {group: t(groupWord), list: <span data-approve-groups>{[...groups].sort((a, b) => b[1] - a[1]).map(([name, count]) => `${name} ${count}`).join(' · ')}</span>})}</p>
      <p className="pf-muted" data-approve-flagged>{t(flagged === 1 ? '{count} of them is flagged {flag}.' : '{count} of them are flagged {flag}.', {count: flagged, flag: t(flaggedWord)})}</p>
      <p className="pf-muted">{t('Rejected items are never changed. Each approval is recorded with your name and can be edited later.')}</p>
      {state.error && <p className="pf-error" role="alert">{serverText(state.error)}</p>}
    </Confirm>}
  </>;
}
