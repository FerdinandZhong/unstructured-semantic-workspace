// The pipeline steps, in order. Stage ids `ingest` and `review` keep their old hashes; `knowledge` stays reachable
// by link (definition-mapping editor, Talk to Data) but is not a step. `later` steps sit below the pipeline.
const pipelineSteps = [
  ['ingest', 'Sources', 'doc'], ['model', 'Model', 'table'], ['evidence', 'Evidence', 'quote'],
  ['concepts', 'Concepts', 'graph'], ['review', 'Publish', 'check'], ['ask', 'Ask', 'search'],
  ['entity', 'Entity 360', 'user', 'later'], ['knowledge', 'Knowledge', 'graph', 'hidden']
];
const reactSteps = new Set(['model', 'evidence', 'concepts', 'ask', 'entity']);   // entity: Financial Services only (entity.js)
const stepStateLabels = {empty: 'Empty', ready: 'Ready', review: 'Needs review', changes: 'Changes to publish', done: 'Done'};
// "1 dataset", "2 datasets": every printed count agrees with its number (shared by the workspace scripts).
function counted(n, word, many = word + 's') { return t(`{n} ${n === 1 ? word : many}`, {n: typeof n === 'number' ? V2I18n.number(n) : n}); }
// A source kind as shown in a table ("adverse-media" -> "adverse media"), in the current language.
const kindText = kind => { const text = String(kind).replaceAll('-', ' '); return text.startsWith('Local file · ') ? t('Local file') + text.slice(10) : t(text); };
const kindRaw = kind => V2I18n.lang === 'en' ? kind : kindText(kind);   // English shows the kind as sent
const stepStates = new Map();
let steps = [];
let unmountPage = null;
const legacy = {
  pipeline: ['manufacturing', 'ingest'], source: ['manufacturing', 'ingest'],
  knowledge: ['manufacturing', 'knowledge'], review: ['manufacturing', 'review'],
  entity: ['manufacturing', 'entity'], 'bank-ingest': ['financial-services', 'ingest'],
  'bank-knowledge': ['financial-services', 'knowledge'], banking: ['financial-services', 'entity']
};
const icons = {
  pipe: '<path d="M12 16V4M7 9l5-5 5 5M4 16v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/>',
  graph: '<circle cx="5" cy="5" r="2"/><circle cx="19" cy="6" r="2"/><circle cx="12" cy="19" r="2"/><path d="m7 5 10 1M6 7l5 10m7-9-5 9"/>',
  user: '<circle cx="12" cy="8" r="3.5"/><path d="M5 21v-2a7 7 0 0 1 14 0v2"/>',
  check: '<path d="m5 12 5 5 9-10"/>',
  table: '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M3.5 9.5h17M9.5 9.5v10"/>',
  quote: '<path d="M4 6h16M4 11h10M4 16h7M16 14l2 2 3-4"/>',
  doc: '<path d="M5 3h9l5 5v13H5zM14 3v6h5M8 13h8m-8 4h6"/>',
  arrow: '<path d="M4 12h15m-6-6 6 6-6 6"/>',
  search: '<circle cx="10" cy="10" r="5.5"/><path d="m15 15 4 4"/>'
};
// English as written when i18n.js did not load (a server started before i18n.js joined its allow-list).
window.V2I18n ??= {lang: 'en', languages: {}, t: (key, vars) => vars ? key.replace(/\{(\w+)\}/g, (all, name) => name in vars ? String(vars[name]) : all) : key,
  server: text => text, number: (n, options) => new Intl.NumberFormat('en-GB', options).format(n), register() {}, setLang() {}};
window.t ??= V2I18n.t;
const main = document.querySelector('#workspace-main');
const dialog = document.querySelector('#detail-dialog');
const decisionLabels = { keep_separate: 'Keep separate', request_evidence: 'Request evidence' };
const promptLimit = 2000;
const localRuntime = document.querySelector('meta[name="workspace-runtime"]')?.content === 'local';
const accountChanges = localRuntime && typeof BroadcastChannel === 'function'
  ? new BroadcastChannel('v2-demo-account') : null;
const fixtures = typeof workspaceDatasets === 'undefined' ? {} : workspaceDatasets;
let prepared = new Map();
const sessions = Object.fromEntries(Object.keys(fixtures).map(id => [id, {
  files: [], nextFileId:1, ingestMode:'all', ingestSearch:'', newFiles:[], workbookMessages:[], selectionVersion:0, ingestSettings:new Map(), progress: 'proposed', timeframe: 'current', answer: false,
  decisions: [], error: '', ocrEvidence: new Map()
}]));
let locationView;
let knowledgeCanvas;
let fileFocus;
let principal = null;
let accounts = [];
let ocrConnector = {available:false, model:null};
const readOnly = () => localRuntime && principal?.role !== 'admin';
const permittedDataset = id => prepared.has(id) && Object.hasOwn(fixtures, id) &&
  (!localRuntime || principal?.datasets.includes(id));

const html = value => String(value ?? '').replace(/[&<>"']/g, ch => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
}[ch]));
const icon = name => `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name]}</svg>`;
const hash = (stage, suffix = '') => `#${locationView.dataset}/${stage}${suffix ? '/' + suffix : ''}`;
const dataset = () => fixtures[locationView.dataset];
const session = () => sessions[locationView.dataset];
const stageTitle = key => t(steps.find(([id]) => id === key)[1]);
const badge = (name, tone = '') => `<span class="badge ${tone}">${html(name)}</span>`;
const btn = (name, action, css = 'secondary', disabled = false) => `<button type="button" data-action="${action}" class="button ${css}"${disabled ? ' disabled' : ''}>${name}</button>`;
const link = (name, url, css = 'secondary') => `<a class="button ${css}" href="${url}">${name}</a>`;
const fieldRows = values => `<dl class="kv">${values.map(([label, value]) => `<div><dt>${html(label)}</dt><dd>${html(value)}</dd></div>`).join('')}</dl>`;
const heading = (title, text, action = '') => `<div class="pagehead"><div><h1>${html(title)}</h1><p>${html(text)}</p></div>${action}</div>`;
const panel = (title, content, extra = '', action = '') => `<section class="panel ${extra}"><div class="panel-head"><div><h2>${html(title)}</h2></div>${action}</div><div class="panel-body">${content}</div></section>`;

function parseRoute(fragment) {
  const [first, second, third, fourth] = fragment.split('/');
  const alias = legacy[first];
  const id = alias?.[0] || (first === 'finance' ? 'financial-services' : first);
  if (!['financial-services', 'manufacturing'].includes(id)) return {stage:'datasets'};
  if (!permittedDataset(id)) return {stage:'denied'};
  const stage = alias?.[1] || (steps.some(([key]) => key === second) ? second : 'ingest');
  if (readOnly() && stage === 'review') return {dataset:id, stage:'denied'};
  const requested = first === 'source' ? second || 'amendment' : alias ? second : third;
  const detail = stage === 'ingest' && ['workbook','finance-model','manufacturing-model'].includes(requested) ? 'structured' : requested;
  return {dataset:id, stage, detail, observation:fourth};
}

function currentSources() {
  const prefix = locationView.dataset + ':source:';
  const statusLabels = {
    xfa_render_required: t('XFA rendering required'), text_layer_present: t('Text layer inventoried'),
    inventory_only: t('Inventory only'), local_source: t('Local source')
  };
  const actualSources = prepared.get(locationView.dataset)?.sources || [];
  const fileKinds = new Set(['adverse-media', 'synthetic-contract', 'pdf', 'audio', 'video', 'image']);
  const walkthrough = dataset().sources.map(source => {
    const actual = actualSources.find(item => item.id === prefix + source.preparedId);
    return {
      ...source, provenance: actual?.citation, libraryFile: fileKinds.has(actual?.kind),
      access: actual?.access || source.access
    };
  });
  const remaining = actualSources.filter(source =>
    !walkthrough.some(item => source.id === prefix + item.preparedId));
  return walkthrough.concat(remaining.map(source => {
    const example = source.examples?.[0];
    return {
      id: source.id.slice(prefix.length), title: source.title, kind: source.kind.replaceAll('-', ' '),
      locator: example?.citation?.startLine ? t('Source line {n}', {n: example.citation.startLine}) : t('Prepared source inventory'),
      quote: example?.quote, excerpt: example?.description || source.description,
      detail: t('The preview displays prepared source records. It does not run extraction.'),
      label: statusLabels[source.status] || t('Prepared source'), provenance: source.citation, status: source.status,
      libraryFile: fileKinds.has(source.kind), access: source.access
    };
  }));
}

function sourceFor(id) {
  return currentSources().find(item => item.id === id);
}

function observations() {
  const stored = prepared.get(locationView.dataset)?.knowledge?.observations || [];
  const current = [...session().ocrEvidence.values()].flatMap(run => run.observations || []);
  return [...new Map([...stored, ...current].map(item => [item.id,item])).values()];
}

function observationLocator(observation) {
  const {startLine, endLine, page, locator, startSeconds, endSeconds, speaker} = observation.citation || {};
  if (Number.isFinite(startSeconds)) {
    const time = seconds => Math.floor(seconds / 60) + ':' + String(Math.floor(seconds % 60)).padStart(2, '0');
    const range = Number.isFinite(endSeconds) ? time(startSeconds) + '–' + time(endSeconds) : time(startSeconds);
    return [speaker, range].filter(Boolean).join(' · ');
  }
  const line = !startLine ? '' : endLine > startLine ? t('Lines {start}–{end}', {start: startLine, end: endLine}) : t('Line {n}', {n: startLine});
  return [page == null ? locator : t('Page {n}', {n: page}), line].filter(Boolean).join(' · ') || t('Source passage');
}

function processingSourceId(source) {
  return session().ocrEvidence.get(source.id)?.sourceId ||
    locationView.dataset + ':source:' + (source.preparedId || source.id);
}

function renderShell() {
  const stage = steps.find(([key]) => key === locationView.stage);
  const rail = document.querySelector('#rail');
  const top = document.querySelector('#top');
  const name = dataset().name;
  rail.hidden = false;
  top.hidden = false;
  document.querySelector('.shell').classList.remove('catalog-open');
  main.classList.remove('dataset-page');
  rail.innerHTML = `<a href="#datasets" class="brand" aria-label="${html(t('Cloudera data sources'))}"><img class="brand-logo" src="cloudera-logo.png" alt="Cloudera" width="5132" height="626"></a>
    <div class="status-line${localRuntime ? ' ready' : ''}"><span class="connection-dot"></span><span>${t(localRuntime ? 'Local rehearsal' : 'Offline design preview')}</span></div>
    <nav aria-label="${html(t('Workflow'))}">${steps.filter(step => !step[3]).map(([key, title, glyph], index) => navItem(key, title, glyph, index + 1, stage[0])).join('')}
    ${locationView.dataset === 'financial-services' ? `<p class="nav-group">${t('Customer view')}</p>${steps.filter(step => step[3] === 'later').map(([key, title, glyph]) => navItem(key, title, glyph, '', stage[0])).join('')}` : ''}</nav>
    <label class="field workspace-choice">${t('Data source')}<select id="workspace-choice">${Object.entries(fixtures).filter(([id]) => permittedDataset(id)).map(([id, data]) => `<option value="${id}"${id === locationView.dataset ? ' selected' : ''}>${html(data.name)}</option>`).join('')}</select></label>`;
  top.innerHTML = `<a href="#datasets">${t('Data sources')}</a> <span>/</span> <strong>${html(name)}</strong> <span>/</span> <strong>${html(t(stage[1]))}</strong>${accountControl()}`;
}

function navItem(key, title, glyph, number, active) {
  if (readOnly() && key === 'review') return '';
  const state = stepStates.get(locationView.dataset)?.[key];
  return `<a class="nav-item${key === active ? ' active' : ''}" href="${hash(key)}"${key === active ? ' aria-current="step"' : ''} data-step="${key}"><span class="nav-icon">${icon(glyph)}</span><span class="nav-text">${t(title)}${state ? `<span class="step-state ${state}" data-step-state="${state}">${t(stepStateLabels[state])}</span>` : ''}</span><span class="nav-count">${number}</span></a>`;
}

// Step badges from live state. Sources: documents processed; Model: tables; Evidence and Concepts: review state.
async function loadStepStates(id) {
  if (!localRuntime || !window.V2Pipeline || stepStates.has(id)) return;
  stepStates.set(id, {});
  const workspace = prepared.get(id);
  const ctx = {dataset: id, request: callLocal};
  const documents = (workspace?.sources || []).filter(source => !['workbook', 'structured-model', 'mapping-reference'].includes(source.kind) && source.access !== 'withheld');
  const [status, history] = await Promise.all([window.V2Pipeline.status(ctx),
    readOnly() ? null : callLocal('/api/publish/history?' + new URLSearchParams({dataset: id})).catch(() => null)]);
  const tables = workspace?.knowledge?.structured?.datasets?.length || 0;
  const processed = status.sourcesProcessed || 0;
  // Done only when the latest release holds the current approved state: the read-only release preview says whether
  // publishing now would change anything (admin only; a 422 means there is nothing to publish).
  let current = null;
  if (history?.releases.length) {
    current = await callLocal('/api/publish/preview', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({dataset:id})})
      .then(preview => preview.unchanged, error => error.status === 422 ? true : null);
  }
  stepStates.set(id, {
    ingest: !processed ? 'empty' : processed < documents.length ? 'ready' : 'done',
    model: tables ? 'done' : 'empty', evidence: status.evidence, concepts: status.concepts,
    review: history ? (history.releases.length ? (current === false ? 'changes' : 'done') : status.concepts && status.concepts !== 'empty' ? 'ready' : 'empty') : undefined,
    ask: processed ? 'ready' : 'empty'
  });
  if (locationView?.dataset === id && locationView.stage !== 'datasets' && locationView.stage !== 'denied') renderShell();
}

function accountControl() {
  return (localRuntime ? `<button type="button" class="badge blue user-badge" data-action="account" aria-haspopup="dialog">${html(principal.displayName)}</button>`
    : `<span class="badge blue user-badge">${t('Offline admin preview')}</span>`) + languageSwitch();
}

// English / 中文: the choice is kept in this browser and applied by a page reload (as an account switch does).
function languageSwitch() {
  const choices = Object.entries(V2I18n.languages);
  return choices.length ? `<span class="lang-switch" role="group" aria-label="${html(t('Language'))}" data-lang-switch>${choices.map(([code, name]) =>
    `<button type="button" class="badge${code === V2I18n.lang ? ' blue' : ''}" lang="${code}" data-lang="${code}" aria-pressed="${code === V2I18n.lang}">${name}</button>`).join('')}</span>` : '';
}

function modelDefinitions() {
  const model = prepared.get(locationView.dataset)?.knowledge?.structured || {};
  return new Map([...(model.fields || []), ...(model.datasets || []), ...(model.metrics || [])].map(item => [item.id,item]));
}

function mappingEntries(evidence) {
  const definitions = modelDefinitions();
  const observations = new Map((evidence?.observations || []).filter(item =>
    item.access !== 'withheld' && item.observationRole !== 'context').map(item => [item.id,item]));
  return (evidence?.mappings || []).flatMap(mapping => {
    const observation = observations.get(mapping.observationId);
    if (!observation || mapping.access === 'withheld') return [];
    const targets = [...new Set([mapping.targetId, ...(mapping.candidateIds || [])])]
      .map(id => definitions.get(id)).filter(Boolean);
    return [{mapping, observation, targets}];
  }).sort((a,b) => Number(Boolean(b.targets.length)) - Number(Boolean(a.targets.length)));
}

function maskIdentifiers(value) {
  return String(value ?? '').replace(/\b[STFGM][0-9]{7}[A-Z]\b/g,
    id => id.slice(0,3) + '****' + id.slice(-2));
}

function showDialog(title, content, className = '') {
  dialog.className = className;
  document.querySelector('#dialog-body').innerHTML = `<h2 id="dialog-title">${html(title)}</h2>${content}`;
  if (!dialog.open) dialog.showModal();
}

function showAccounts() {
  if (!localRuntime || !accounts.length) return;
  const options = accounts.map(account => `<option value="${html(account.id)}"${principal?.id === account.id ? ' selected' : ''}>${html(account.displayName)}</option>`).join('');
  showDialog(t('Demo account'), `<p class="helper">${t('Select a predefined local demo identity.')}</p>
    ${principal ? fieldRows([[t('Current account'),principal.displayName], [t('Dataset access'),principal.datasets.map(id => prepared.get(id)?.title || id).join(', ')]]) : ''}
    <label class="field" for="demo-account">${t('Switch account')}<select id="demo-account">${principal ? '' : `<option value="">${t('Choose an account')}</option>`}${options}</select></label>
    <p class="helper first">${t('Switching accounts clears files and settings added in this tab.')}</p>
    <p class="note workspace-error" id="account-error" role="alert" hidden></p>
    <div class="actions account-actions">${btn(t('Switch account'), 'switch-account', 'primary')}</div>`);
}

async function switchAccount() {
  const selected = document.querySelector('#demo-account');
  if (!selected?.value || selected.value === principal?.id) return;
  const apply = dialog.querySelector('[data-action="switch-account"]');
  apply.disabled = true;
  selected.disabled = true;
  let next;
  try {
    next = await callLocal('/api/session', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({userId:selected.value, ...(!principal ? {recover:true} : {})})});
  } catch (error) {
    const alert = document.querySelector('#account-error');
    if (!alert) return;
    alert.hidden = false;
    alert.textContent = error.message;
    apply.disabled = false;
    selected.disabled = false;
    return;
  }
  accountChanges?.postMessage('account-changed');
  // stay on the same step and data source when the new account may open it (a demo switches reader and asks again)
  const account = next?.principal;
  // no view exists after an expired session ("Workspace unavailable"): then the catalog opens
  const stays = account && locationView?.dataset && account.datasets?.includes(locationView.dataset) && locationView.stage !== 'denied'
    && !(account.role !== 'admin' && locationView.stage === 'review');
  if (!stays) history.replaceState(null, '', location.pathname + location.search + '#datasets');
  reloadScopedWorkspace();
}

function clearScopedView() {
  unmountPage?.();
  unmountPage = null;
  knowledgeCanvas?.destroy();
  mountedIngest?.savedRequest?.abort();
  main.replaceChildren();
  for (const id of ['rail', 'top', 'dialog-body']) document.getElementById(id).replaceChildren();
  if (dialog.open) dialog.close();
}

function reloadScopedWorkspace() {
  releaseFileUrls();
  clearScopedView();
  location.reload();
}

async function callLocal(path, options) {
  const response = await fetch(path, V2I18n.withLang ? V2I18n.withLang(options) : options);   // the UI language (plan D3, D4)
  const result = await response.json();
  if (!response.ok) {
    const error = new Error(V2I18n.server(result.error) || t('The local workspace request failed.'));
    error.status = response.status;
    error.accounts = result.accounts;
    throw error;
  }
  return result;
}

function showAction(action) {
  const state = session();
  const {entity} = dataset();
  switch (action) {
    case 'ingest-run':
    case 'ingest-prepare':
      processIngest(action);
      return;
    case 'ingest-ocr-confirm':
      if (dialog.open) dialog.close();
      void submitDocumentRead('ocr');
      return;
    case 'ingest-form':
      void submitDocumentRead('xfa-fields');
      return;
    case 'physical-preview':
    case 'physical-confirm': {
      const host = document.querySelector('[data-physical]');
      if (host && !readOnly()) void physicalRequest(host.dataset.physical, action.split('-')[1]);
      return;
    }
    case 'ingest-remove':
      if (readOnly() || !mountedIngest?.source?.file) return;
      removeLocalFile(mountedIngest.source);
      return;
    case 'publish':
    case 'publish-confirm':
      if (!readOnly()) void releaseRequest(action.replace('publish-', '').replace('publish', 'preview'));
      return;
    case 'facts':
      showDialog(t('Scoped record evidence'), `<p class="helper">${html(entity.factSource)}</p>${fieldRows(entity.facts.concat(entity.evidence))}`);
      return;
    case 'answer':
      state.answer = true;
      render();
      document.querySelector('.answer').focus();
  }
}

const views = { ingest: renderIngest, knowledge: renderKnowledge, review: renderReview, entity: renderEntity,
  model: renderReactStep, evidence: renderReactStep, concepts: renderReactStep, ask: renderReactStep };
function mountReactPage() {
  const host = main.querySelector('#pf-root');
  if (!host) return;
  if (!window.V2Pipeline) { host.innerHTML = `<p class="workspace-error" role="alert">${t('The page bundle (pipeline.js) did not load.')}</p>`; return; }
  if (!localRuntime && locationView.stage !== 'model') {
    host.innerHTML = `<p class="empty">${t('This step reads the private database. Start the local workspace server to use it.')}</p>`;
    return;
  }
  unmountPage = window.V2Pipeline.mount(locationView.stage, host, {
    dataset: locationView.dataset, workspace: prepared.get(locationView.dataset), principal, readOnly: readOnly(),
    request: callLocal, detail: locationView.detail, observation: locationView.observation,
    href: (stage, suffix) => hash(stage, suffix),
    refreshSteps: () => { const id = locationView.dataset; stepStates.delete(id); void loadStepStates(id); }   // after a bulk review write
  });
}

const pageIntro = {
  model: 'The business vocabulary of the structured data: each table, what its columns mean, and how tables connect. Documents are matched against it.',
  evidence: 'Each document passage beside the model fields it is about. A fill-in form can also load rows into the model.',
  concepts: 'Business concepts found in the evidence, and the definitions they match.',
  ask: 'Answers keep database facts and document passages apart.'
};
function renderReactStep() {
  return heading(stageTitle(locationView.stage), `${dataset().name} · ${t(pageIntro[locationView.stage])}`) + '<div id="pf-root"></div>';
}

function render() {
  unmountPage?.();
  unmountPage = null;
  knowledgeCanvas?.destroy();
  knowledgeCanvas = null;
  if (mountedIngest?.tempUrl) URL.revokeObjectURL(mountedIngest.tempUrl);
  mountedIngest?.savedRequest?.abort();
  mountedIngest = null;
  if (locationView.stage === 'datasets' || locationView.stage === 'denied') {
    main.innerHTML = renderDatasetCatalog();
    const top = document.querySelector('#top');
    top.querySelector('.catalog-preview')?.remove();
    top.insertAdjacentHTML('beforeend', accountControl());
    if (locationView.stage === 'denied') {
      main.innerHTML = panel(t('Access unavailable'), `<p>${t('This account cannot open the requested dataset or workflow.')}</p><a class="button secondary" href="#datasets">${t('Back to data sources')}</a>`);
    }
    document.title = t('Data sources') + ' · Cloudera Semantic Workspace';
    document.querySelector('#announcer').textContent = t('Data source selection');
    return;
  }
  renderShell();
  // The badges read the dataset-wide links and catalog (several seconds on a full corpus). A source detail page,
  // Knowledge and Entity 360 do not wait on them, so their own reads are not queued behind that work.
  if (!['knowledge', 'entity'].includes(locationView.stage) && !(locationView.stage === 'ingest' && locationView.detail))
    void loadStepStates(locationView.dataset);
  main.innerHTML = views[locationView.stage]();
  if (reactSteps.has(locationView.stage)) mountReactPage();
  if (locationView.stage === 'knowledge') {
    if (locationView.detail === 'talk') {
      const scope = locationView.dataset;
      const sources = currentSources();
      mountTalkToData(main.querySelector('#talk-to-data'), {
        scope, available:localRuntime, canDraft:!readOnly(), request:callLocal,
        cite(item) {
          const source = sources.find(candidate => scope + ':source:' + (candidate.preparedId || candidate.id) === item.sourceId);
          if (!source || source.access === 'withheld') return '';
          const passage = item.id?.split(':').at(-1);
          const suffix = passage && /^[\w.-]+$/.test(passage) ? '/' + passage : '';
          return '#' + scope + '/ingest/' + source.id + suffix;
        }
      });
    } else mountGraph();
  }
  if (locationView.stage === 'ingest') {
    if (locationView.detail && locationView.detail !== 'structured') void mountIngest();
    if (!locationView.detail) { updateFileSearch(); void fillSourceResults(); }
  }
  const stage = stageTitle(locationView.stage);
  document.title = `${stage} · ${dataset().name} · Cloudera Semantic Workspace`;
  document.querySelector('#announcer').textContent = `${dataset().name} · ${stage}`;
}

function route(event) {
  const previous = locationView;
  locationView = parseRoute(location.hash.slice(1));
  if (locationView.stage === 'ingest' && locationView.detail) {
    const {source, mode} = ingestView();
    if (source) session().ingestMode = mode;
  }
  if (dialog.open) dialog.close();
  render();
  window.scrollTo(0, 0);
  if (event) {
    let destination = locationView.stage === 'datasets' && previous?.dataset
      ? main.querySelector(`[data-dataset="${previous.dataset}"] a[href$="/ingest"]`)
      : main;
    if (locationView.stage === 'ingest' && !locationView.detail && previous?.dataset === locationView.dataset) {
      const id = fileFocus || previous.detail;
      const file = [...main.querySelectorAll('[data-file-id]')].find(item => item.dataset.fileId === id);
      if (file && !file.closest('tr').hidden) destination = file;
    }
    const focusTarget = destination || main;
    main.classList.toggle('route-focus', focusTarget === main);
    focusTarget.focus({preventScroll: true});
  }
  fileFocus = null;
}

window.addEventListener('pagehide', event => {
  if (!event.persisted || localRuntime) releaseFileUrls();
  if (localRuntime && event.persisted) clearScopedView();
});
window.addEventListener('pageshow', event => {
  if (localRuntime && event.persisted) reloadScopedWorkspace();
});
accountChanges?.addEventListener('message', event => {
  if (event.data === 'account-changed') reloadScopedWorkspace();
});

document.addEventListener('click', event => {
  if (event.target.closest('.skip')) {
    event.preventDefault();
    main.classList.remove('route-focus');
    main.focus();
    return;
  }
  const row = event.target.closest('[data-file-row]');
  if (row && !event.target.closest('a') && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) {
    row.querySelector('a').click();
  }
  const control = event.target.closest('button');
  if (!control || control.disabled) return;
  if (control.hasAttribute('data-extraction-view')) {
    const block = control.closest('.ingest-result-block');
    block.querySelectorAll('[data-extraction-panel]').forEach(panel => {
      panel.hidden = panel.dataset.extractionPanel !== control.dataset.extractionView;
    });
    block.querySelectorAll('[data-extraction-view]').forEach(button => {
      button.setAttribute('aria-pressed', String(button === control));
    });
    return;
  }
  if (control.dataset.lang) return V2I18n.setLang(control.dataset.lang);
  if (control.dataset.action === 'account') return showAccounts();
  if (control.dataset.action === 'switch-account') return void switchAccount();
  if (!locationView?.dataset || locationView.stage === 'denied') return;
  if (control.dataset.action) showAction(control.dataset.action);
  if (control.dataset.ingestMode) {
    const mode = control.dataset.ingestMode;
    session().ingestMode = mode;
    session().ingestSearch = '';
    if (locationView.detail || location.hash !== hash('ingest')) location.hash = hash('ingest');
    else {
      render();
      document.querySelector(`[data-ingest-mode="${mode}"]`).focus();
    }
  }
  if (control.dataset.ingestTab && mountedIngest?.source) {
    const {source, info} = mountedIngest;
    settingsFor(source).tab = control.dataset.ingestTab;
    renderInspector(source, info);
    document.querySelector(`[data-ingest-tab="${control.dataset.ingestTab}"]`).focus();
  }
});

document.addEventListener('change', event => {
  const control = event.target;
  switch (control.id) {
    case 'workspace-choice':
      location.hash = control.value + '/' + locationView.stage +
        (['ontology', 'mappings', 'talk', 'structured'].includes(locationView.detail) ? '/' + locationView.detail : '');
      break;
    case 'workspace-file':
      selectFiles(control.files);
      break;
    case 'ingest-source':
      chooseIngestSource(ingestView().visible.find(source => source.id === control.value));
      break;
    case 'entity-revision':
      session().timeframe = control.value;
      render();
      document.querySelector('#entity-revision').focus();
      break;
  }
});

document.addEventListener('input', event => {
  const control = event.target;
  const key = control.dataset.setting;
  if (key && mountedIngest?.source && !readOnly()) {
    const setting = settingsFor(mountedIngest.source);
    setting[key] = control.type === 'checkbox' ? control.checked : key === 'prompt' ? control.value.slice(0, promptLimit) : control.value;
    if (key === 'pages') markPages(setting);
  }
  if (control.id === 'file-search') {
    session().ingestSearch = control.value;
    updateFileSearch();
  }
});

document.addEventListener('toggle', event => {
  if (event.target.matches?.('details.ingest-options') && mountedIngest?.source) settingsFor(mountedIngest.source).optionsOpen = event.target.open;
}, true);

document.addEventListener('dragover', event => {
  if (!event.target.closest('[data-intake]') || readOnly()) return;
  event.preventDefault();
  event.target.closest('[data-upload-dropzone]')?.classList.add('drag');
});
document.addEventListener('dragleave', event => {
  const dropzone = event.target.closest('[data-upload-dropzone]');
  if (dropzone && !dropzone.contains(event.relatedTarget)) dropzone.classList.remove('drag');
});
document.addEventListener('drop', event => {
  if (!event.target.closest('[data-intake]')) return;
  event.preventDefault();
  if (!readOnly()) selectFiles(event.dataTransfer.files);
});
// The fixed text of index.html (skip link, rail label, dialog Close button), in the current language.
function localizeStatic() {
  document.querySelector('.skip').textContent = t('Skip to content');
  document.querySelector('#rail').setAttribute('aria-label', t('Workspace navigation'));
  document.querySelector('#detail-dialog .dialog-footer button').textContent = t('Close');
}
async function start() {
  localizeStatic();
  if (localRuntime) {
    try {
      ({principal, accounts} = await callLocal('/api/session'));
      window.V2_WORKSPACE_DATA = await callLocal('/api/workspace');
    } catch (error) {
      if (error.status === 401 && Array.isArray(error.accounts)) accounts = error.accounts;
      const recovery = error.status === 401 && accounts.length
        ? btn(t('Choose demo account'), 'account', 'primary')
        : `<a class="button secondary" href="">${t('Reload workspace')}</a>`;
      main.innerHTML = `<h1>${t('Workspace unavailable')}</h1><p role="alert">${html(V2I18n.server(error.message))}</p><div class="account-actions">${recovery}</div>`;
      return;
    }
    if (!readOnly()) {
      try { ocrConnector = await callLocal('/api/ocr'); }
      catch { ocrConnector = {available:false, model:null}; }
    }
    await Promise.all(Object.entries(sessions).filter(([id]) => !readOnly() && principal.datasets.includes(id)).map(async ([id, state]) => {
      await loadDecisions(id);
    }));
  }
  prepared = new Map((window.V2_WORKSPACE_DATA?.datasets || []).map(item => [item.id, item]));
  steps = pipelineSteps;
  window.addEventListener('hashchange', route);
  route();
}
void start();
