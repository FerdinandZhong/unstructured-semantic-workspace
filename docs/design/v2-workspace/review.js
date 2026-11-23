// Publication is explicit and never gated on a decision. Evidence decisions are optional.
function decisionRows() {
  const {knowledge} = prepared.get(locationView.dataset);
  const observations = new Map((knowledge?.observations || []).map(item => [item.id, item]));
  const mappings = new Map((knowledge?.mappings || []).map(item => [item.id, item]));
  return session().decisions.map(item => {
    const observationId = mappings.get(item.mappingId)?.observationId;
    const title = observations.get(observationId)?.title || item.mappingId.split(':').at(-1);
    const route = observationId ? hash('knowledge', 'mappings/' + observationId.split(':').at(-1)) : hash('knowledge');
    return `<li><a href="${html(route)}">${html(ocrSourceText(title))}</a> · ${html(t(decisionLabels[item.decision]))}
      <span class="helper">${html(item.note)}</span></li>`;
  }).join('');
}

// A real publish writes the approved catalog and the saved mapping corrections as a versioned Ossie export.
function releaseState() {
  return session().release ||= {history: null, loading: false, preview: null, message: '', error: '', busy: false};
}

const postJson = body => ({method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});

async function loadReleaseHistory() {
  const state = releaseState();
  const id = locationView.dataset;
  if (state.history !== null || state.loading || !localRuntime) return;
  state.loading = true;
  try {
    state.history = (await callLocal('/api/publish/history?' + new URLSearchParams({dataset: id}))).releases;
  } catch (error) {
    state.history = [];
    state.error = t('The publication history could not be loaded. {message}', {message: V2I18n.server(error.message)});
  }
  state.loading = false;
  if (locationView.stage === 'review' && locationView.dataset === id) render();
}

async function releaseRequest(action) {
  const state = releaseState();
  const dataset = locationView.dataset;
  const body = action === 'confirm' ? {dataset, fingerprint: state.preview.fingerprint} : {dataset};
  state.busy = true;
  state.error = state.message = '';
  render();
  try {
    const result = await callLocal('/api/publish/' + action, postJson(body));
    if (action === 'preview') {
      state.preview = result;
      // concept labels for the change list; without them the list falls back to the short id
      const catalog = await callLocal('/api/catalog?' + new URLSearchParams({dataset})).catch(() => null);
      state.conceptLabels = new Map((catalog?.concepts || []).map(item => [item.id, item.label]));
    }
    else {
      state.preview = null;
      state.history = null;
      state.message = V2I18n.server(result.message);
      stepStates.delete(dataset);          // the Publish step state follows the new release
      void loadStepStates(dataset);
    }
  } catch (error) {
    state.preview = null;
    state.error = V2I18n.server(error.message) || t('The release request failed.');
  }
  state.busy = false;
  render();
}

// Business names for the items of a release diff: a concept by its label, an evidence-link correction ("chunk | definition")
// and any definition id by the model's label. The raw id stays as a tooltip.
function changeName(kind, raw) {
  const workspace = prepared.get(locationView.dataset);
  const definition = id => window.V2Pipeline?.definitionLabel(workspace, id) || id.split('/').slice(-2).join(' › ');
  let name = raw;
  if (kind === 'concept') name = releaseState().conceptLabels?.get(raw) || raw.split(':').at(-1).replaceAll('-', ' ');
  else if (raw.includes(':definition:')) {
    const id = raw.split(' | ').find(part => part.includes(':definition:'));
    name = id ? definition(id.trim()) : raw;
  } else name = raw.split(':').at(-1);
  return `<span title="${html(raw)}">${html(name)}</span>`;
}

function changeList(label, change, kind) {
  const parts = [['added', '{n} added ({items})'], ['removed', '{n} removed ({items})'], ['changed', '{n} changed ({items})']].filter(([key]) => change[key].length)
    .map(([key, text]) => t(text, {n: change[key].length, items: change[key].map(item => changeName(kind, item)).join(', ')}));
  return `<li>${t(label) + t(': ') + (parts.length ? parts.join(t('; ')) : t('no change'))}</li>`;
}

function releasePreview(preview) {
  const {counts, changes, lint, unchanged} = preview;
  return `<div class="note" data-release-preview>
    <p>${t('{preview} from catalog {catalog}.', {preview: `<strong>${t('Preview of version {version}', {version: html(preview.nextVersion)})}</strong>`, catalog: html(preview.catalogVersion || t('none'))})}
    ${t('{concepts}, {links}, {evidence}, {mapping} and {evidenceLinks}.', {concepts: html(counted(counts.concepts, 'concepts', 'concepts')), links: html(counted(counts.links, 'definition links', 'definition links')), evidence: html(counted(counts.evidenceRefs, 'evidence references', 'evidence references')),
      mapping: html(counted(counts.mappingCorrections, 'mapping corrections', 'mapping corrections')), evidenceLinks: html(counted(counts.evidenceLinkCorrections ?? 0, 'evidence-link corrections', 'evidence-link corrections'))})}</p>
    ${unchanged ? `<p>${t('This content is already published. Confirming writes nothing.')}</p>` : ''}
    ${changes ? `<ul class="release-changes">${changeList('Concepts', changes.concepts, 'concept')}${changeList('Mapping corrections', changes.corrections, 'correction')}${changes.evidenceLinks ? changeList('Evidence-link corrections', changes.evidenceLinks, 'link') : ''}</ul>` : ''}
    <p>${t(lint.ok ? 'The export passes the Ossie lint.' : 'The export fails the Ossie lint and cannot be published.')}</p>
    ${lint.ok ? '' : `<ul>${lint.errors.map(item => `<li>${html(V2I18n.server(item))}</li>`).join('')}</ul>`}</div>`;
}

function releaseHistory(history) {
  if (history === null) return `<p class="helper">${t('Loading the publication history.')}</p>`;
  if (!history.length) return `<p>${t('No release published yet.')}</p>`;
  const dataset = locationView.dataset;
  return `<div class="receipt">${history.map(item => `<div><span class="receipt-mark">${html(item.version)}</span>
    <strong>${html(counted(item.counts.concepts, 'concept'))}, ${html(counted(item.counts.mappingCorrections + (item.counts.evidenceLinkCorrections ?? 0), 'correction'))}</strong>
    <span class="helper">${html(item.publishedAt.slice(0, 16).replace('T', ' '))} · ${html(item.principalId)} · ${html(t('catalog {catalog}', {catalog: item.catalogVersion || t('none')}))} · ${html(item.contentSha256.slice(0, 12))}</span>
    <a class="text-link" download="${html(dataset)}-v${html(item.version)}.ossie.yaml" href="/api/publish/release?${new URLSearchParams({dataset, version: item.version})}">${t('Download YAML')}</a></div>`).join('')}</div>`;
}

function renderReview() {
  const {decisions} = session();
  const release = releaseState();
  void loadReleaseHistory();
  const {knowledge} = prepared.get(locationView.dataset);
  const corrections = (knowledge?.mappings || []).filter(item => item.correctionAction &&
    item.correctionAction !== 'restore' && item.access !== 'withheld');
  const ready = release.preview?.lint.ok && release.preview.fingerprint;
  const latest = release.history?.[0];
  return heading(stageTitle('review'), t('Preview the changes since the last release, check them, then publish a versioned release.'),
    link(t('Next: Ask →'), hash('ask'))) +
    `<div class="grid review-grid">${panel(t('Mapping corrections'), `<p>${corrections.length
      ? html(t(corrections.length === 1 ? '{n} saved correction' : '{n} saved corrections', {n: corrections.length}))
      : t('No mapping corrections saved.')}</p>
      <p class="helper">${t('Saved definition-mapping corrections. Links between passages and table fields are corrected on the Evidence step.')}</p>
      ${link(t('Open Evidence →'), hash('evidence'), 'text-link')}`)}
    ${panel(t('Release'), `<h3>${html(t('{name} release', {name: dataset().name}))}</h3>
      <p class="helper first">${t('A release writes the approved concept catalog and the saved mapping corrections as a new Ossie version in the private database. Candidate concepts and rejected items are left out.')}</p>
      ${decisions.length ? `<ul class="decision-list">${decisionRows()}</ul>` : ''}
      ${release.error ? `<p class="note workspace-error" role="alert">${html(release.error)}</p>` : ''}
      ${release.message ? `<p class="note" role="status">${html(release.message)}</p>` : ''}
      ${release.preview ? releasePreview(release.preview) : ''}
      <div class="review-actions">${btn(t('Preview release'), 'publish', ready ? 'secondary' : 'primary', readOnly() || release.busy)}
      ${ready ? btn(t('Confirm publish'), 'publish-confirm', 'primary', readOnly() || release.busy) : ''}</div>`,
        '', badge(latest ? t('Latest release: version {version}', {version: latest.version}) : t('Not published'), 'blue'))}</div>
    ${panel(t('Publication history'), releaseHistory(release.history))}`;
}

async function loadDecisions(id) {
  const state = sessions[id];
  if (!window.V2_WORKSPACE_DATA.reviewAvailable) {
    state.error = t('Local review storage is unavailable.');
    return;
  }
  try {
    state.decisions = (await callLocal('/api/review?' + new URLSearchParams({dataset:id}))).receipts;
  } catch (error) {
    state.error = t('The saved decisions could not be loaded. {message}', {message: V2I18n.server(error.message)});
  }
}
