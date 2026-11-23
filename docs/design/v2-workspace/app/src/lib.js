// Shared data access and business naming for the React pages. Every read goes through ctx.request (callLocal).
import {createElement, Fragment, useEffect, useState} from 'react';

// Fixed text through the shell's i18n.js (rules: ../../i18n/README.md). Call at render time, never at module load.
// t('Show steps'), t('Guard: {reason}', {reason}); serverText(sent) for a code, label or sentence the server sent.
// A key with {name} is stored in zh-CN.pipeline.js with full-width braces (｛name｝). V2I18n.server() turns every dictionary key
// that holds {name} into a sentence pattern, and a key such as "{count} rows" or "{label} by {axis}" would then rewrite any
// server sentence that ends the same way. Full-width braces keep these keys out of that matching.
const stored = key => key.replace(/\{(\w+)\}/g, '｛$1｝');
const find = key => {
  if (!window.V2I18n) return key;
  const wide = stored(key), text = window.V2I18n.t(wide);
  return text === wide ? window.V2I18n.t(key) : text;      // not in the dictionary: the English key
};
export const t = (key, vars) => {
  const text = find(key);
  return vars ? text.replace(/\{(\w+)\}/g, (all, name) => name in vars ? String(vars[name]) : all) : text;
};
export const serverText = text => window.V2I18n ? window.V2I18n.server(text) : text;
// fetch options with the UI language header, for the calls that do not go through ctx.request (callLocal adds it there).
export const withLang = options => window.V2I18n?.withLang ? window.V2I18n.withLang(options) : options;
// Like t, for a sentence that holds markup: each {name} in vars may be a React node (<strong>{n}</strong>).
export function tn(key, vars) {
  return find(key).split(/(\{\w+\})/).map((part, index) => {
    const name = /^\{(\w+)\}$/.exec(part)?.[1];
    return name && name in vars ? createElement(Fragment, {key: index}, vars[name]) : part;
  });
}

const SCHEMAS = new Set(['Person', 'Production', 'Sales', 'Purchasing', 'HumanResources']);
const WORDS = {Txn: 'transactions', TXN: 'transactions', LC: 'LC', FX: 'FX', RM: 'RM', CASA: 'CASA', STR: 'STR', ID: 'ID', BOM: 'BOM'};

// "Customer_RM_Relationship" -> "Customer RM relationship"; "Production_ProductInventory" -> "Product inventory".
// "1 metric", "2 metrics": every printed count agrees with its number.
// The key is the English phrase with {count}, so each phrase is in the dictionary: t('{count} links').
export const counted = (n, word, many = word + 's') => t(`{count} ${n === 1 ? word : many}`, {count: typeof n === 'number' ? n.toLocaleString('en-GB') : n});

export function humanize(name) {
  const parts = String(name || '').split('_');
  const body = parts.length > 1 && SCHEMAS.has(parts[0]) ? parts.slice(1) : parts;
  const words = body.join(' ').replace(/([a-z0-9])([A-Z])/g, '$1 $2').replace(/([A-Z]+)([A-Z][a-z])/g, '$1 $2')
    .split(/\s+/).filter(Boolean);
  return words.map((word, index) => WORDS[word] || WORDS[word.toUpperCase()] || (/^[A-Z0-9]{2,}$/.test(word) ? word
    : index ? word.toLowerCase() : word[0].toUpperCase() + word.slice(1).toLowerCase())).join(' ');
}

// System fields carry provenance or snapshot identity, not business meaning.
export const isSystemField = column => /^(SourceBatch|BusinessDate|SourceRevision|GroupSeq|TransactionSeq|rowguid|ModifiedDate)$/.test(column);

export function buildModel(workspace) {
  const st = workspace?.knowledge?.structured || {};
  const imports = (workspace?.sources || []).flatMap(source => source.structuredImport?.tables || []);
  const rowCount = new Map(imports.map(table => [table.name, table.rowCount]));
  const tables = new Map((st.datasets || []).map(table => [table.name, {
    id: table.id, name: table.name, label: humanize(table.name), description: table.description,
    key: table.primary_key || [], fromDocuments: Boolean(table.origin) || table.name.startsWith('STR_'),
    rows: rowCount.get(table.name), fields: [], metrics: []
  }]));
  const defs = new Map();
  for (const table of tables.values()) defs.set(table.id, {kind: 'table', table, label: table.label, id: table.id});
  for (const field of st.fields || []) {
    const [tableName, column] = field.name.split('/');
    const table = tables.get(tableName);
    const item = {id: field.id, kind: 'field', column, table, title: field.title || humanize(column),
      description: field.description, datatype: field.datatype, system: isSystemField(column)};
    item.label = `${table ? table.label : humanize(tableName)} › ${item.title}`;
    table?.fields.push(item);
    defs.set(field.id, item);
  }
  const metricTable = new Map((st.datasets || []).flatMap(table => (table.metrics || []).map(metric => [metric.name, table.name])));
  for (const metric of st.metrics || []) {
    const table = tables.get(metricTable.get(metric.name));
    const item = {id: metric.id, kind: 'metric', table, title: humanize(metric.title || metric.name),
      description: metric.description, datatype: metric.datatype};
    item.label = `${table ? table.label : 'Metric'} › ${item.title}`;
    table?.metrics.push(item);
    defs.set(metric.id, item);
  }
  const relationships = (st.relationships || []).filter(rel => tables.has(rel.from) && tables.has(rel.to))
    .map(rel => ({id: rel.id, from: rel.from, to: rel.to, columns: (rel.from_columns || []).filter(column => !isSystemField(column))}));
  const label = id => defs.get(id)?.label || (id ? humanize(String(id).split('/').at(-1)) : t('No link'));
  return {tables, defs, relationships, label, metrics: (st.metrics || []).map(metric => defs.get(metric.id))};
}

// One cache per dataset and account session; the page reload on account switch clears it.
const cache = new Map();
export function cached(key, load) {
  if (!cache.has(key)) cache.set(key, load().catch(error => { cache.delete(key); throw error; }));
  return cache.get(key);
}
export const forget = prefix => [...cache.keys()].filter(key => key.startsWith(prefix)).forEach(key => cache.delete(key));

export function allLinks(ctx) {
  return cached(ctx.dataset + ':links', async () => {
    const response = await fetch('/api/evidence-links/export?' + new URLSearchParams({dataset: ctx.dataset}));
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || t('The evidence links could not be read.'));
    return (await response.text()).split('\n').filter(Boolean).map(line => JSON.parse(line));
  });
}

export const getCatalog = ctx => cached(ctx.dataset + ':catalog', () => ctx.request('/api/catalog?' + new URLSearchParams({dataset: ctx.dataset})));
export const getChunks = (ctx, sourceId) => cached(ctx.dataset + ':chunks:' + sourceId,
  () => ctx.request('/api/chunks?' + new URLSearchParams({dataset: ctx.dataset, sourceId})).then(result => result.chunks));

// {data, error, loading, reload}
export function useLoad(load, deps) {
  const [state, setState] = useState({loading: true});
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    setState(previous => ({...previous, loading: true}));
    load().then(data => live && setState({data}), error => live && setState({error: error.message}));
    return () => { live = false; };
  }, [...deps, tick]);
  return {...state, reload: () => setTick(value => value + 1)};
}

// Data label of a source: Synthetic, Public or Customer. The prepared record states it; an unstated label stays unstated.
export function dataLabel(source) {
  const kind = source?.kind || '';
  if (kind.startsWith('public')) return 'Public';
  if (kind.startsWith('synthetic') || source?.structuredImport?.synthetic || /synthetic/i.test(source?.description || '')) return 'Synthetic';
  if (source?.dataLabel) return source.dataLabel;
  if (/(^|:)cleaned_/.test(source?.id || '')) return 'Customer file (cleaned)';   // files supplied by the finance customer, cleaned before use
  return 'Label not stated';
}

export const shortSource = id => String(id || '').split(':source:').at(-1);
export const RELATION = {states_value_of: 'states value', is_about: 'is about', none: 'no link'};
export const HOW = {stated: 'stated', derived: 'derived', inherited: 'inherited', named: 'named', labelled: 'labelled'};

// Rows that fill-in forms have loaded into the form tables: {tableName: {rows, forms: [title]}}. Written rows only.
export const loadedRows = ctx => cached(ctx.dataset + ':formrows', async () => {
  const out = {};
  const forms = (ctx.workspace.sources || []).filter(source => source.kind === 'pdf');
  await Promise.all(forms.map(async source => {
    const view = await ctx.request('/api/physical-rows?' + new URLSearchParams({dataset: ctx.dataset, sourceId: source.id})).catch(() => null);
    for (const row of view?.rows || []) if (row.status === 'written') {
      const item = out[row.table] ||= {rows: 0, forms: []};
      item.rows += 1;
      if (!item.forms.includes(source.title)) item.forms.push(source.title);
    }
  }));
  return out;
});
