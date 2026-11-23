// Entity 360: pick a customer (or an entity known only from documents) and build its report on request.
// GET /api/entity/search lists at most 20 matches; POST /api/entity/report builds one report. Every section names
// its source. Totals come from SQL; the summary is model text that the server validated against what it cites.
import {useEffect, useMemo, useState} from 'react';
import {buildModel, humanize, shortSource, RELATION, t, tn, serverText} from './lib.js';
import {Tag, Seg, Elapsed, Empty} from './ui.jsx';
import {Bars, fmtValue} from './Ask.jsx';

const HOW = {stated: ['stated in the passage', 'green'], named: ['named in the passage', 'blue'], derived: ['derived through a key', 'blue'],
  inherited: ['inherited from its document', 'amber'], scoped: ['document scoped to this entity', 'blue']};
const RESULT = {agree: ['agree', 'green'], differ: ['differ', 'red'], 'not comparable': ['not comparable', '']};
const STATUS = {approved: 'green', corrected: 'green', proposed: 'amber', candidate: ''};
// Identifiers stay as written (an account number is not 1,234,…); numbers and ISO dates are formatted.
const show = value => typeof value === 'number' || /^\d{4}-\d{2}-\d{2}/.test(String(value ?? '')) ? fmtValue(value) : String(value ?? '');
// Account numbers are restricted (the model says never display them). Every one the report holds is masked to its last
// four characters before anything renders: in the accounts table, in fact and passage text and in the summary.
const ACCOUNT = /^(account(number|no|id)?|accountnumber|cardaccountno|cardnumber)$/i;
const maskNumber = value => '•••• ' + String(value).slice(-4);
export function maskAccounts(report) {
  const numbers = new Set();
  const collect = value => {
    if (Array.isArray(value)) value.forEach(collect);
    else if (value && typeof value === 'object') Object.entries(value).forEach(([key, item]) =>
      ACCOUNT.test(key) && item !== null && typeof item !== 'object' && String(item).length > 4 ? numbers.add(String(item)) : collect(item));
  };
  collect(report);
  const longest = [...numbers].sort((a, b) => b.length - a.length);
  const text = value => longest.reduce((out, number) => out.split(number).join(maskNumber(number)), value);
  const walk = value => typeof value === 'string' ? text(value) : Array.isArray(value) ? value.map(walk)
    : value && typeof value === 'object' ? Object.fromEntries(Object.entries(value).map(([key, item]) =>
      [key, ACCOUNT.test(key) && item !== null && typeof item !== 'object' ? maskNumber(item) : walk(item)])) : value;
  return walk(report);
}
const post = (ctx, body) => ctx.request('/api/entity/report', {method: 'POST', headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({dataset: ctx.dataset, ...body})});

function Card({title, source, children, id}) {
  return <section className="pf-card e360-card" aria-label={title} data-section={id}>
    <div className="pf-card-head"><h3>{title}</h3>{source && <span className="pf-muted">{t('Source: {source}', {source: t(source)})}</span>}</div>{children}</section>;
}

// A raw server error (an old server answers 404 "Asset unavailable" for a route it lacks) is never shown as is.
const plain = failure => failure.status === 404 && /^(Asset|Action) unavailable$/.test(failure.message)
  ? t('The customer view is not available on this server. Restart the workspace server to load it.')
  : failure.status === 404 ? serverText(failure.message) : t('The report could not be built: {message}', {message: serverText(failure.message)});

function Picker({ctx, onGenerate, busy}) {
  const [text, setText] = useState('');
  const [chosen, setChosen] = useState(null);
  const [found, setFound] = useState(null);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!text.trim()) { setFound(null); return undefined; }
    let live = true;
    const timer = setTimeout(() => ctx.request('/api/entity/search?' + new URLSearchParams({dataset: ctx.dataset, q: text.trim()}))
      .then(out => { if (live) { setFound(out); setError(''); } }, failure => live && setError(plain(failure))), 250);
    return () => { live = false; clearTimeout(timer); };
  }, [text]);
  const results = found?.results || [];
  async function generate() {
    const wanted = text.trim().toLowerCase();
    const choose = list => chosen && chosen.label.toLowerCase() === wanted ? chosen
      : list.find(item => item.label.toLowerCase() === wanted) || list[0];
    let pick = choose(results);
    if (!pick || (pick !== chosen && pick.label.toLowerCase() !== wanted)) {
      // The matches may not have arrived yet (the search waits 250 ms after the last key): look the text up now.
      try {
        const out = await ctx.request('/api/entity/search?' + new URLSearchParams({dataset: ctx.dataset, q: text.trim()}));
        setFound(out); pick = choose(out.results || []);
      } catch (failure) { setError(plain(failure)); return; }
    }
    if (!pick) { setError(t('No customer or entity matches “{text}”.', {text: text.trim()})); return; }
    setError('');
    onGenerate(pick);
  }
  return <section className="pf-card e360-picker" aria-label={t('Choose a customer')}>
    <form className="e360-pick-row" onSubmit={event => { event.preventDefault(); generate(); }}>
      <label className="field e360-search">{t('Customer ID or name')}
        <input type="search" value={text} placeholder={t('For example CUST00004')} onChange={event => { setText(event.target.value); setChosen(null); }}
          data-entity-search /></label>
      <button type="submit" className="button primary" disabled={!text.trim() || busy} data-generate>{t('Generate report')}</button>
    </form>
    {error && <p className="pf-error" role="alert" data-entity-error>{error}</p>}
    {found && text.trim() && <ul className="e360-results" aria-label={t('Matches')} data-results>
      {results.map(item => <li key={item.id}><button type="button" aria-pressed={chosen?.id === item.id}
        className={'e360-result' + (chosen?.id === item.id ? ' on' : '')} onClick={() => { setChosen(item); setText(item.label); }} data-entity={item.id}>
        <strong>{item.label}</strong><span className="pf-muted">{item.detail}</span></button></li>)}
      {!results.length && <li className="pf-muted">{t('No customer or entity matches.')}</li>}
    </ul>}
    {found?.more && text.trim() && <p className="pf-muted">{t('Showing the first 20 matches. Type more of the ID to narrow the list.')}</p>}
  </section>;
}

function Fields({fields, model}) {
  return <dl className="e360-kv">{fields.map(field => <div key={field.definition}>
    <dt>{model.defs.get(field.definition)?.title || humanize(field.column)}</dt><dd>{field.value === null || field.value === '' ? '—' : show(field.value)}</dd></div>)}</dl>;
}

function Profile({report, model}) {
  const {profile} = report;
  return <Card title={t('Profile')} source="SQL, Customers and relationship-manager tables" id="profile">
    {!profile.rows ? <p className="pf-muted">{t('No row in the Customers table for this entity.')}</p> : <>
      <p className="pf-muted">{t('Snapshot of business date {date}.', {date: fmtValue(report.businessDate)})}</p>
      <Fields fields={profile.fields} model={model} />
      <h4 className="e360-sub">{t('Relationship manager')}</h4>
      {profile.rm.length ? profile.rm.map(rm => <Fields key={rm.RMID} fields={rm.fields} model={model} />)
        : <p className="pf-muted">{t('No relationship manager is assigned on this date.')}</p>}
    </>}
  </Card>;
}

function Holdings({report, model}) {
  const {holdings} = report;
  const tables = holdings.transactions;
  const [table, setTable] = useState(tables[0]?.table);
  const current = tables.find(item => item.table === table) || tables[0];
  const currencies = current ? current.currencies.map(item => item.currency) : [];
  const [measure, setMeasure] = useState('count');
  const shown = currencies.includes(measure) ? measure : 'count';
  const days = (current?.days || []).map(day => ({key: day.day, label: fmtValue(day.day), answered: true,
    value: shown === 'count' ? day.count : day.totals[shown] || 0}));
  return <Card title={t('Holdings and activity')} source="SQL, Accounts and the transaction tables" id="holdings">
    <h4 className="e360-sub">{t('Accounts on {date}: {count}', {date: fmtValue(holdings.accounts.businessDate), count: holdings.accounts.count})}</h4>
    {holdings.accounts.rows.length ? <div className="pf-table-wrap"><table data-accounts><thead><tr>
      {Object.keys(holdings.accounts.rows[0]).map(column => <th key={column}>{model.defs.get(`${model.prefix}Accounts/${column}`)?.title || humanize(column)}</th>)}</tr></thead>
      <tbody>{holdings.accounts.rows.map((row, index) => <tr key={index}>{Object.entries(row).map(([column, value]) =>
        <td key={column}>{show(value)}</td>)}</tr>)}</tbody></table></div> : <p className="pf-muted">{t('No accounts on this date.')}</p>}
    <h4 className="e360-sub">{t('Transactions')}</h4>
    {!tables.length ? <p className="pf-muted">{t('No transactions for this customer in the transaction tables.')}</p> : <>
      <div className="pf-table-wrap"><table data-transactions><thead><tr><th>{t('Transaction table')}</th><th>{t('Currency')}</th><th className="num">{t('Transactions')}</th><th className="num">{t('Total amount')}</th></tr></thead>
        <tbody>{tables.flatMap(item => item.currencies.map(row => <tr key={item.table + row.currency}>
          <td>{humanize(item.table)}</td><td>{row.currency}</td><td className="num">{fmtValue(row.count)}</td><td className="num">{fmtValue(row.total)} {row.currency}</td></tr>))}</tbody></table></div>
      <p className="pf-muted">{t('Totals are SQL sums per currency. Amounts in different currencies are never added together.')}</p>
      <div className="e360-charts">
        <figure><figcaption>{t('Transactions by table')}</figcaption>
          <Bars points={tables.map(item => ({key: item.table, label: humanize(item.table).replace(/ transactions$/, ''), value: item.count, answered: true}))}
            yLabel={t('Transactions')} xLabel={t('Transaction table')} /></figure>
        <figure><figcaption><span>{t('Per day')}</span>
          <Seg value={current.table} onChange={setTable} options={tables.map(item => [item.table, humanize(item.table).replace(/ transactions$/, '')])} label={t('Transaction table')} />
          <Seg value={shown} onChange={setMeasure} options={[['count', t('Count')], ...currencies.map(code => [code, t('Amount {code}', {code})])]} label={t('Measure')} /></figcaption>
          <Bars points={days} yLabel={shown === 'count' ? t('Transactions') : t('Amount {code}', {code: shown})} xLabel={t('Transaction date, {table}', {table: humanize(current.table)})} /></figure>
        <figure><figcaption>{t('{table} by currency', {table: humanize(current.table)})}</figcaption>
          <Bars points={current.currencies.map(item => ({key: item.currency, label: item.currency, value: item.count, answered: true}))}
            yLabel={t('Transactions')} xLabel={t('Currency')} /></figure>
      </div>
    </>}
  </Card>;
}

function Quote({text}) {
  const [open, setOpen] = useState(false);
  return <><p className={'pf-quote' + (open ? ' open' : '')}>{text}</p>
    {text.length > 160 && <button type="button" className="pf-link-button" onClick={() => setOpen(!open)}>{open ? t('Show less') : t('Show all')}</button>}</>;
}

function Evidence({ctx, report, model}) {
  const {groups, byTable} = report.evidence;
  return <Card title={t('Evidence')} source="evidence links with entity keys" id="evidence">
    {!groups.length ? <p className="pf-muted">{t('No document passage this account may read is linked to this entity.')}</p> : <>
      <figure><figcaption>{t('Linked passages by table')}</figcaption>
        <Bars points={byTable.map(item => ({key: item.table, label: item.table === 'No field' ? t('No field') : humanize(item.table), value: item.count, answered: item.table !== 'No field'}))}
          yLabel={t('Links')} xLabel={t('Table the passage is about')} /></figure>
      {groups.map(group => <div className="e360-group" key={group.definition || 'none'} data-group>
        <h4 className="e360-sub">{group.definition ? model.label(group.definition) : t('Passages not about a model field')}</h4>
        <ul className="e360-items">{group.items.map(item => <li key={item.chunkId + (group.definition || '')} data-evidence-item>
          <div className="e360-item-head"><strong>{item.sourceTitle}</strong>
            {group.definition && <Tag tone="blue">{item.relation === 'states_value_of' ? t('states a value') : t(RELATION[item.relation] || item.relation)}</Tag>}
            <Tag tone={(HOW[item.how] || [])[1]}>{t((HOW[item.how] || [item.how])[0])}</Tag>
            {item.status !== 'proposed' && <Tag>{t(item.status)}</Tag>}</div>
          {item.statedValue && <p className="e360-values"><span>{tn('Document states {value}', {value: <strong>{item.statedValue}{item.unit ? ' ' + item.unit : ''}</strong>})}</span>
            {'recorded' in item && <span>{tn('Table records {value}', {value: <strong>{item.recorded ?? t('no single value')}</strong>})}</span>}
            {item.result && <Tag tone={RESULT[item.result][1]}>{t(RESULT[item.result][0])}</Tag>}</p>}
          <Quote text={item.text} />
          <a className="text-link" href={ctx.href('evidence', shortSource(item.sourceId) + '/' + item.chunkId)} data-open-passage>{t('Open at passage')}</a>
        </li>)}</ul></div>)}
    </>}
  </Card>;
}

function Comparisons({ctx, report, model}) {
  const rows = report.comparisons;
  if (report.entity.kind !== 'customer') return null;
  return <Card title={t('Stated versus recorded')} source="values stated in the evidence links and SQL recorded values, compared by code" id="comparisons">
    {!rows.length ? <p className="pf-muted">{t('No passage states a value that the tables also record for this customer.')}</p>
      : <div className="pf-table-wrap"><table data-comparisons><thead><tr><th>{t('Field')}</th><th>{t('Document states')}</th><th>{t('Table records')}</th><th>{t('Result')}</th><th>{t('Document')}</th></tr></thead>
        <tbody>{rows.map(row => <tr key={row.chunkId + row.definition}><td>{model.label(row.definition)}</td><td>{row.stated}{row.unit ? ' ' + row.unit : ''}</td>
          <td>{row.recorded ?? <span className="pf-muted">{serverText(row.why)}</span>}</td><td><Tag tone={RESULT[row.result][1]}>{t(RESULT[row.result][0])}</Tag></td>
          <td><a className="text-link" href={ctx.href('evidence', shortSource(row.sourceId) + '/' + row.chunkId)}>{row.sourceTitle || t('Open')}</a></td></tr>)}</tbody></table></div>}
  </Card>;
}

function Concepts({ctx, report, model}) {
  return <Card title={t('Concepts')} source="concept catalog, concepts drawn from this entity's passages" id="concepts">
    {!report.concepts.length ? <p className="pf-muted">{t("No concept of the catalog draws on this entity's passages.")}</p>
      : <ul className="e360-items">{report.concepts.map(item => <li key={item.id} data-concept>
        <div className="e360-item-head"><a className="text-link" href={ctx.href('concepts', item.id)} data-open-concept><strong>{item.label}</strong></a>
          <Tag tone={STATUS[item.status] ?? ''}>{t(item.status)}</Tag>{item.type && <span className="pf-muted">{item.type}</span>}</div>
        {item.definition && <p className="e360-def">{item.definition}</p>}
        <p className="pf-muted">{item.links.length ? t('Linked definitions: {list}', {list: item.links.map(link => model.label(link.definition) + ` (${t(link.status)})`).join(t('; '))})
          : t('No definition linked yet.')} · {t("{count} of this entity's passages", {count: item.chunks.length})}</p></li>)}</ul>}
  </Card>;
}

function Summary({ctx, report}) {
  const {summary, citations} = report;
  return <Card title={serverText(summary.label)} source="model text over the cited facts and passages, checked by code" id="summary">
    {!summary.available ? <p className="pf-muted" data-summary-unavailable>{t('Summary unavailable.')} {serverText(summary.reason)}</p> : <>
      {summary.reason && <p className="pf-muted">{serverText(summary.reason)}</p>}
      <ol className="e360-summary" data-summary>{summary.sentences.map((sentence, index) => <li key={index}>{sentence.text}
        <span className="e360-cites">{sentence.cites.map(id => {
          const cite = citations[id];
          return cite?.kind === 'passage'
            ? <a key={id} className="pf-chip" href={ctx.href('evidence', shortSource(cite.sourceId) + '/' + cite.chunkId)} title={cite.source}>{cite.source}</a>
            : <span key={id} className="pf-chip" title={cite?.text}>{(cite?.text || id).split(': ')[0]}</span>;
        })}</span></li>)}</ol>
      {summary.dropped > 0 && <p className="pf-muted">{t(summary.dropped > 1 ? '{count} sentences failed validation and were left out.' : '{count} sentence failed validation and was left out.', {count: summary.dropped})}</p>}
    </>}
  </Card>;
}

function Report({ctx, report, model}) {
  const {entity} = report;
  return <div className="e360-report" data-report={entity.id}>
    <section className="pf-card e360-head" aria-label={t('Entity')}>
      <div className="pf-card-head"><h2>{entity.label}</h2>
        <Tag tone={entity.kind === 'customer' ? 'blue' : 'amber'}>{entity.kind === 'customer' ? t('Customer with rows in the tables') : t('Known from documents only')}</Tag></div>
      {entity.description && <p className="pf-muted">{serverText(entity.description)}</p>}
      {report.notes.length > 0 && <ul className="e360-notes" data-notes>{report.notes.map(note => <li key={note}>{serverText(note)}</li>)}</ul>}
    </section>
    <Summary ctx={ctx} report={report} />
    <Profile report={report} model={model} />
    {report.holdings && <Holdings key={entity.id} report={report} model={model} />}
    <Comparisons ctx={ctx} report={report} model={model} />
    <Evidence ctx={ctx} report={report} model={model} />
    <Concepts ctx={ctx} report={report} model={model} />
    <Card title={t('What is missing')} id="missing">
      {report.missing.length ? <ul className="e360-notes" data-missing>{report.missing.map(item => <li key={item}>{serverText(item)}</li>)}</ul>
        : <p className="pf-muted">{t('Every section has data.')}</p>}
    </Card>
  </div>;
}

// The report shown on opening: a customer the tables hold and a cleaned customer file (customer-form5.pdf) states.
const FIRST = {id: 'customer:CUST00007', label: 'CUST00007'};

export default function Entity360({ctx}) {
  const model = useMemo(() => {
    const built = buildModel(ctx.workspace);
    built.prefix = (ctx.workspace?.knowledge?.structured?.fields?.[0]?.id || '').replace(/[^/]+\/[^/]+$/, '');
    return built;
  }, [ctx.workspace]);
  const [pending, setPending] = useState(null);
  const [report, setReport] = useState(null);
  const [error, setError] = useState('');
  const supported = ctx.dataset === 'financial-services';
  async function generate(entity) {
    setPending({label: entity.label, since: Date.now()}); setError('');
    try { setReport(maskAccounts(await post(ctx, {entity: entity.id}))); } catch (failure) { setError(plain(failure)); setReport(null); }
    setPending(null);
  }
  useEffect(() => {
    if (!supported) return;
    const detail = ctx.detail && decodeURIComponent(ctx.detail);
    void generate(detail ? {id: detail, label: detail.split(':').slice(1).join(':')} : FIRST);
  }, []);
  if (!supported) return <Empty title={t('Not built for this dataset')} text={t('The customer view is built for Financial Services only.')} />;
  return <div className="pf-page e360">
    <Picker ctx={ctx} onGenerate={generate} busy={Boolean(pending)} />
    {pending && <p className="e360-progress" role="status" data-progress>{t('Building the report for {label}: reading the tables, the evidence links and the catalog, then asking the model for a cited summary.', {label: pending.label})} <Elapsed since={pending.since} /></p>}
    {error && <p className="pf-error" role="alert" data-entity-error>{error}</p>}
    {report && !pending && <Report ctx={ctx} report={report} model={model} />}
  </div>;
}
