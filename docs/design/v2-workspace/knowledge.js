const passageTypes = {table:'Table', article:'Article', transcript:'Transcript'};

function mappingGraph() {
  const knowledge = prepared.get(locationView.dataset)?.knowledge || {};
  const runs = [...session().ocrEvidence.values()];
  const replaced = new Set(runs.map(run => run.sourceId));
  const sources = new Map([...currentSources(), ...session().files].filter(item => item.access !== 'withheld')
    .map(item => [processingSourceId(item), item]));
  const retained = (knowledge.observations || []).filter(item => !item.citation?.kind || !replaced.has(item.sourceId));
  const observations = [...retained, ...runs.flatMap(run => run.observations || [])]
    .filter(item => item.access !== 'withheld' && item.observationRole !== 'context' && sources.has(item.sourceId));
  const sourceObservations = new Map(observations.map(item => [item.id, item]));
  const mappings = [...new Map([...runs.flatMap(run => run.mappings || []), ...(knowledge.mappings || [])]
    .map(item => [item.id, item])).values()];
  const entries = new Map();
  for (const entry of mappingEntries({observations, mappings})) {
    const id = entry.observation.id;
    if (!entries.has(id)) entries.set(id, []);
    entries.get(id).push(entry);
  }
  const supplied = [...(knowledge.passages || []).filter(item => !replaced.has(item.sourceId)),
    ...runs.flatMap(run => run.passages || [])];
  const covered = new Set();
  const passages = [];
  for (const item of supplied) {
    if (item.access === 'withheld' || !sources.has(item.sourceId)) continue;
    const evidence = (item.observationIds || []).map(id => sourceObservations.get(id))
      .filter(record => record?.sourceId === item.sourceId);
    for (const record of evidence) covered.add(record.id);
    passages.push({...item, evidence});
  }
  for (const [sourceId, source] of sources) {
    const evidence = observations.filter(item => item.sourceId === sourceId && !covered.has(item.id) && item.quote);
    const kind = /audio|video/i.test(source.kind) ? 'transcript' : 'article';
    for (let start = 0; start < evidence.length; start += 4) {
      const group = evidence.slice(start, start + 4);
      passages.push({id:group[0].id, sourceId, kind, title:group[0].title || source.title,
        text:group.map(item => item.quote).join('\n\n'), citation:group[0].citation, evidence:group});
    }
  }
  const searchable = passages.map(passage => ({...passage, searchableText:
    ocrSourceText([passage.title, passage.text, ...(passage.rows || []).flat()].join('\n')).toLowerCase()}));
  return {passages:searchable, sources, entries};
}

function graphPassageText(passage) {
  const rows = passage.rows || [];
  if (passage.kind === 'table' && rows.length) {
    return `<div class="table-wrap graph-passage-values" tabindex="0"><table aria-label="${html(ocrSourceText(passage.title))}"><tbody>${rows.map(row =>
      `<tr>${row.map((cell, index) => index === 0 ? `<th scope="row">${html(ocrSourceText(cell))}</th>` :
        `<td>${html(ocrSourceText(cell))}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  }
  return `<div class="graph-passage-text" tabindex="0">${html(ocrSourceText(passage.text))}</div>`;
}

async function refreshKnowledge(scope) {
  const account = principal;
  const snapshot = await callLocal('/api/workspace');
  if (account !== principal) throw new Error(t('The active account changed.'));
  const workspace = snapshot.datasets.find(item => item.id === scope);
  if (!workspace) throw new Error(t('This dataset is unavailable.'));
  prepared.set(scope, workspace);
}

// The one edit surface. Every human edit opens here, from a selected node or the list.
// Extension points: push {id, render(context) -> html} to add a section to this same dialog.
// Built: Evidence link and Concept sections. Planned, not built: entity link accept or reject, extracted field value.
const editorSections = [];
const stamp = value => String(value || '').replace('T', ' ').slice(0, 16);

// Evidence link (Output A) and Concept (Output B) sections. Both save through POST /api/catalog-correction, never over the pipeline record.
const catalogRelations = ['states_value_of', 'is_about', 'none'];
const catalogPredicates = ['exactMatch', 'closeMatch', 'broadMatch', 'narrowMatch', 'relatedMatch'];
const catalogStatuses = ['proposed', 'corrected', 'rejected'];
const catalogPost = body => callLocal('/api/catalog-correction', {
  method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
const catalogOptions = (values, current) => values.map(value =>
  `<option value="${html(value)}"${value === current ? ' selected' : ''}>${html(V2I18n.server(value))}</option>`).join('');
const catalogTail = id => id ? String(id).split('/').slice(-2).join('.') : t('No definition');
const rawDefinition = link => link.notes?.originalDefinitionId ?? link.definition_id;

editorSections.push({
  id:'evidence-link',
  render: () => `<h4>${t('Evidence link')} <span class="small muted">${t('Stored link of this source')}</span></h4><div data-body><p class="helper">${t('Loading the stored links…')}</p></div>`,
  async mount({passage, scope, choices}, section) {
    const body = section.querySelector('[data-body]');
    let links;
    try {
      links = (await callLocal('/api/evidence-links?' + new URLSearchParams({dataset:scope, sourceId:passage.sourceId}))).links;
    } catch (error) { body.textContent = V2I18n.server(error.message); return; }
    if (!section.isConnected) return;
    if (!links.length) { body.innerHTML = `<p class="helper">${t('No stored evidence link for this source.')}</p>`; return; }
    const quotes = [passage.text, ...passage.evidence.map(item => item.quote)].filter(Boolean).map(ocrSourceText);
    const own = links.findIndex(link => quotes.some(text => text.includes(link.span.text) || link.span.text.includes(text)));
    body.innerHTML = `<label class="field">${t('Link')}<select data-link>${links.map((link, index) =>
        `<option value="${index}"${index === Math.max(own, 0) ? ' selected' : ''}>${html(ocrSourceText(link.span.text).slice(0, 60))} → ${html(catalogTail(link.definition_id))}</option>`).join('')}</select></label>
      <label class="field">${t('Definition')}<select data-definition><option value="">${t('No definition')}</option>${choices.map(item =>
        `<option value="${html(item.id)}">${html(ocrSourceText(item.title || item.name || item.id))}</option>`).join('')}</select></label>
      <label class="field">${t('Relation')}<select data-relation>${catalogOptions(catalogRelations)}</select></label>
      <label class="field">${t('Stated value')}<input data-value maxlength="200"></label>
      <label class="field">${t('Unit')}<input data-unit maxlength="200"></label>
      <label class="field">${t('Period')}<input data-period maxlength="200"></label>
      <button type="button" class="secondary" data-save-link>${t('Save evidence link')}</button>
      <p class="helper" role="status" data-link-status></p>`;
    const field = name => body.querySelector(`[data-${name}]`);
    const current = () => links[Number(field('link').value)];
    const fill = () => {
      const link = current();
      field('definition').value = link.definition_id || '';
      field('relation').value = link.relation;
      field('value').value = link.stated_value || '';
      field('unit').value = link.unit || '';
      field('period').value = link.period || '';
      field('link-status').textContent = link.status === 'corrected'
        ? t('Corrected by {actor} · {time} UTC', {actor: link.actor, time: stamp(link.recorded_at)}) : t('Status: {status}', {status: V2I18n.server(link.status)});
    };
    field('link').addEventListener('change', fill);
    fill();
    field('save-link').addEventListener('click', async event => {
      event.preventDefault();
      const link = current();
      const patch = {relation:field('relation').value, statedValue:field('value').value.trim() || null,
        unit:field('unit').value.trim() || null, period:field('period').value.trim() || null};
      if ((field('definition').value || null) !== (link.definition_id || null)) patch.definitionId = field('definition').value || null;
      field('save-link').disabled = true;
      try {
        const saved = await catalogPost({dataset:scope, kind:'evidence_link', chunkId:link.chunk_id,
          definitionId:rawDefinition(link) ?? null, patch});
        links = (await callLocal('/api/evidence-links?' + new URLSearchParams({dataset:scope, sourceId:passage.sourceId}))).links;
        field('link-status').textContent = t('Saved · revision {revision}', {revision: saved.revision});
        const index = field('link').value;
        field('link').innerHTML = links.map((item, i) =>
          `<option value="${i}"${String(i) === index ? ' selected' : ''}>${html(ocrSourceText(item.span.text).slice(0, 60))} → ${html(catalogTail(item.definition_id))}</option>`).join('');
        const keep = field('link-status').textContent;
        fill();
        field('link-status').textContent = keep;
      } catch (error) { field('link-status').textContent = V2I18n.server(error.message); }
      finally { field('save-link').disabled = false; }
    });
  }
});

editorSections.push({
  id:'concept',
  render: () => `<h4>${t('Concept')} <span class="small muted">${t('Catalog concepts that cite this source')}</span></h4><div data-body><p class="helper">${t('Loading the catalog…')}</p></div>`,
  async mount({passage, scope}, section) {
    const body = section.querySelector('[data-body]');
    let concepts;
    const load = async () => {
      const catalog = await callLocal('/api/catalog?' + new URLSearchParams({dataset:scope}));
      concepts = (catalog.concepts || []).filter(item => item.evidenceDetail.some(row => row.sourceId === passage.sourceId));
    };
    try { await load(); } catch (error) { body.textContent = V2I18n.server(error.message); return; }
    if (!section.isConnected) return;
    if (!concepts.length) { body.innerHTML = `<p class="helper">${t('No catalog concept cites this source.')}</p>`; return; }
    let selectedId = concepts[0].id, removed = new Set(), notice = '';
    const draw = () => {
      const concept = concepts.find(item => item.id === selectedId);
      body.innerHTML = `<label class="field">${t('Concept')}<select data-concept>${concepts.map(item =>
          `<option value="${html(item.id)}"${item.id === selectedId ? ' selected' : ''}>${html(item.label)}</option>`).join('')}</select></label>
        <label class="field">${t('Label')}<input data-label maxlength="2000" value="${html(concept.label)}"></label>
        <label class="field">${t('Definition')}<textarea data-concept-definition rows="2" maxlength="2000">${html(concept.definition)}</textarea></label>
        <label class="field">${t('Status')}<select data-concept-status>${catalogOptions(catalogStatuses, concept.status)}</select></label>
        <p class="helper">${t('Origin {origin} · {n} evidence chunks', {origin: html(concept.origin), n: concept.evidenceDetail.length})}</p>
        <ul class="concept-links">${concept.links.map((link, index) => {
          const gone = removed.has(index) || link.status === 'rejected';
          return `<li data-concept-link="${index}"><span>${html(catalogTail(link.definition_id))}${gone ? t(' (removed)') : ''}</span>
            <select data-predicate aria-label="${html(t('Predicate'))}">${catalogOptions(catalogPredicates, link.predicate)}</select>
            <button type="button" class="secondary" data-toggle-link="${index}">${t(gone ? 'Restore' : 'Remove')}</button></li>`;
        }).join('')}</ul>
        <button type="button" class="secondary" data-save-concept>${t('Save concept')}</button>
        <p class="helper" role="status" data-concept-notice>${html(notice)}</p>`;
    };
    draw();
    body.addEventListener('change', event => {
      if (event.target.matches('[data-concept]')) { selectedId = event.target.value; removed = new Set(); notice = ''; draw(); }
    });
    body.addEventListener('click', async event => {
      const toggle = event.target.closest('[data-toggle-link]');
      const concept = concepts.find(item => item.id === selectedId);
      const read = name => body.querySelector(`[data-${name}]`);
      if (toggle) {
        event.preventDefault();
        Object.assign(concept, {label:read('label').value, definition:read('concept-definition').value, status:read('concept-status').value});
        body.querySelectorAll('[data-concept-link]').forEach((row, i) => {
          concept.links[i] = {...concept.links[i], predicate:row.querySelector('[data-predicate]').value};
        });
        const index = Number(toggle.dataset.toggleLink);
        const gone = removed.has(index) || concept.links[index].status === 'rejected';
        concept.links[index] = {...concept.links[index], status:gone ? 'proposed' : 'rejected'};
        removed.delete(index);
        draw();
        return;
      }
      if (!event.target.closest('[data-save-concept]')) return;
      event.preventDefault();
      const before = (await callLocal('/api/catalog?' + new URLSearchParams({dataset:scope}))).concepts.find(item => item.id === concept.id);
      const patch = {};
      if (read('label').value.trim() !== before.label) patch.label = read('label').value.trim();
      if (read('concept-definition').value.trim() !== before.definition) patch.definition = read('concept-definition').value.trim();
      if (read('concept-status').value !== before.status) patch.status = read('concept-status').value;
      const posts = [];
      const base = {dataset:scope};
      if (Object.keys(patch).length) posts.push({...base, kind:'concept', conceptId:concept.id, patch});
      body.querySelectorAll('[data-concept-link]').forEach((row, index) => {
        const was = before.links[index], now = concept.links[index];  // `now.status` carries the Remove toggle
        const change = {};
        const predicate = row.querySelector('[data-predicate]').value;
        if (predicate !== was.predicate) change.predicate = predicate;
        if (now.status !== was.status) change.status = now.status;
        if (Object.keys(change).length) posts.push({...base, kind:'concept_link', conceptId:concept.id, definitionId:was.definition_id, patch:change});
      });
      if (!posts.length) { notice = t('Nothing to save.'); draw(); return; }
      read('save-concept').disabled = true;
      try {
        let revision;
        for (const post of posts) revision = (await catalogPost(post)).revision;
        await load();
        removed = new Set();
        notice = t('Saved · revision {revision}', {revision});
      } catch (error) { notice = V2I18n.server(error.message); }
      draw();
    });
  }
});

function openMappingEditor(passage, entries, definition, preferred, refresh) {
  const scope = locationView.dataset;
  const pick = map => passage.evidence.flatMap(item =>
    (map.get(item.id) || []).filter(({mapping}) => typeof mapping.correctionRevision === 'string'));
  let writable = pick(entries);
  const editable = !readOnly() && writable.length > 0;
  const preferredIndex = writable.findIndex(({observation}) => observation.id.split(':').at(-1) === preferred);
  const definitionIndex = writable.findIndex(({targets}) => targets.some(item => item.id === definition?.id));
  const initial = preferredIndex >= 0 ? preferredIndex : Math.max(definitionIndex, 0);
  const definitionName = item => ocrSourceText(item?.title || item?.name || '');
  const definitions = modelDefinitions();
  const model = prepared.get(scope)?.knowledge?.structured || {};
  const choices = [...(model.fields || []), ...(model.metrics || [])];
  const assertionOptions = selectedId => writable.map(({observation}, i) =>
    `<option value="${i}"${observation.id === selectedId ? ' selected' : ''}>${html(ocrSourceText(observation.title || observation.quote))}</option>`).join('');
  const editor = editable ? `<form class="mapping-form">
    <section class="editor-section" data-editor-section="mapping"><h4>${t('Mapping')}</h4>
      <label class="field">${t('Assertion')}<select name="assertion">${assertionOptions(writable[initial].observation.id)}</select></label>
      <blockquote class="quote" data-assertion-text></blockquote>
      <dl class="kv mapping-value">
        <div><dt>${t('Current mapping')}</dt><dd data-current-mapping></dd></div>
        <div data-original-row hidden><dt>${t('Original mapping')}</dt><dd data-original-mapping></dd></div>
        <div data-change-row hidden><dt>${t('Last change')}</dt><dd data-change></dd></div>
        <div data-change-note-row hidden><dt>${t('Change note')}</dt><dd data-change-note></dd></div>
      </dl>
      <label class="field">${t('Correction')}<select name="correction"></select></label>
      <label class="field" data-target-choice>${t('New definition')}<select name="target" required>
        <option value="">${t('Choose a definition')}</option>
        ${choices.map(item => `<option value="${html(item.id)}">${html(definitionName(item))}</option>`).join('')}
      </select></label>
      <label class="field">${t('Note')} <span class="small muted">${t('Optional · saved with a correction')}</span>
        <textarea name="note" rows="2" maxlength="480"></textarea></label>
    </section>
    <section class="editor-section" data-editor-section="decision"><h4>${t('Evidence decision')} <span class="small muted">${t('Optional')}</span></h4>
      <div data-decision-body></div></section>
    ${editorSections.map(item => `<section class="editor-section" data-editor-section="${html(item.id)}">${item.render({passage, scope})}</section>`).join('')}
    <p class="workspace-error" role="alert" hidden></p>
    <button type="submit" class="button primary">${t('Save changes')}</button>
  </form>` : '';
  const current = [...new Set(passage.evidence.flatMap(item => (entries.get(item.id) || []).flatMap(({targets}) => targets))
    .concat(definition ? [definition] : []))].map(definitionName).join(', ');
  showDialog(t(editable ? 'Mapping editor' : 'Source passage'), `<div class="mapping-dialog-body">
    <section class="mapping-editor-evidence"><h3>${html(ocrSourceText(passage.title))}</h3>
      <p class="helper">${html(observationLocator(passage))}</p>${graphPassageText(passage)}
      ${editable ? '' : fieldRows([[t('Current mapping'), current || t('Unmatched')]])}</section>
    ${editor ? `<div>${editor}</div>` : ''}</div>`, 'mapping-dialog');
  if (!editable) return;
  const form = dialog.querySelector('.mapping-form');
  editorSections.forEach(item => item.mount?.({passage, scope, choices},
    form.querySelector(`[data-editor-section="${item.id}"]`)));
  const {assertion, correction, target, note} = form.elements;
  const save = form.querySelector('[type=submit]');
  const alert = form.querySelector('[role=alert]');
  const decisionBody = form.querySelector('[data-decision-body]');
  const selected = () => writable[Number(assertion.value)];
  const reviews = new Map();
  const draft = {decision:'', note:''};
  let busy = false;
  const describe = ({targets}) => targets.map(definitionName).join(', ') || t('Unmatched');
  const correctionReady = () => Boolean(correction.value) && (correction.value !== 'replace' || Boolean(target.value));
  const decisionState = () => reviews.get(selected().mapping.id);
  const decisionOpen = () => decisionState()?.status === 'ready' && !decisionState().receipt;
  const decisionReady = () => decisionOpen() && Boolean(draft.decision) && Boolean(draft.note.trim());
  const sync = () => {
    form.querySelector('[data-target-choice]').hidden = target.disabled = correction.value !== 'replace';
    const incomplete = decisionOpen() && Boolean(draft.decision) !== Boolean(draft.note.trim());
    save.disabled = busy || incomplete || !(correctionReady() || decisionReady());
  };
  const renderDecision = () => {
    const state = decisionState();
    if (!state || state.status === 'loading') decisionBody.innerHTML = `<p class="helper first">${t('Loading the saved decision…')}</p>`;
    else if (state.status === 'unavailable') decisionBody.innerHTML = `<p class="helper first">${html(state.reason)}</p>`;
    else if (state.receipt) decisionBody.innerHTML = fieldRows([[t('Decision'), t(decisionLabels[state.receipt.decision])],
        [t('Decision note'), state.receipt.note]]) + `<p class="helper">${t('A saved decision is final for this evidence revision.')}</p>` +
        (draft.note ? fieldRows([[t('Your unsaved note'), draft.note]]) : '');
    else {
      decisionBody.innerHTML = `<label class="field first">${t('Evidence decision')}<select name="decision">
        ${[['', 'No decision'], ...Object.entries(decisionLabels)].map(([value, label]) => `<option value="${value}"${value === draft.decision ? ' selected' : ''}>${t(label)}</option>`).join('')}
      </select></label>
      <label class="field">${t('Decision rationale')}<textarea name="rationale" rows="2" maxlength="480">${html(draft.note)}</textarea></label>`;
    }
    sync();
  };
  const loadDecision = async () => {
    const {mapping} = selected();
    if (reviews.has(mapping.id) && reviews.get(mapping.id).status !== 'loading') return renderDecision();
    reviews.set(mapping.id, {status:'loading'});
    renderDecision();
    let state;
    if (!window.V2_WORKSPACE_DATA.reviewAvailable) state = {status:'unavailable', reason:t('Decision storage is unavailable.')};
    else {
      try {
        const saved = await callLocal('/api/review?' + new URLSearchParams({dataset:scope, mappingId:mapping.id}));
        state = {status:'ready', receipt:saved.receipt, revision:saved.evidenceRevision};
      } catch (error) {
        state = {status:'unavailable', reason: error.status === 400 ? t('A decision cannot be recorded for this extracted mapping.')
          : t('The saved decision could not be loaded. {message}', {message: V2I18n.server(error.message)})};
      }
    }
    reviews.set(mapping.id, state);
    if (dialog.open && form.isConnected && selected().mapping.id === mapping.id) renderDecision();
  };
  const showAssertion = (keepChoice = false) => {
    const {mapping, observation, targets} = selected();
    const previous = correction.value;
    form.querySelector('[data-assertion-text]').textContent = ocrSourceText(observation.quote);
    form.querySelector('[data-current-mapping]').textContent = describe({targets});
    const changed = ['replace','remove'].includes(mapping.correctionAction);
    form.querySelector('[data-original-row]').hidden = !changed;
    const originalDefinition = definitionName(definitions.get(mapping.originalTargetId));
    const originalCandidates = (mapping.originalCandidateIds || [])
      .map(id => definitionName(definitions.get(id))).filter(Boolean).join(', ');
    form.querySelector('[data-original-mapping]').textContent = mapping.originalStatus === 'ambiguous'
      ? (originalCandidates ? t('Ambiguous ({items})', {items: originalCandidates}) : t('Ambiguous'))
      : originalDefinition || t('Unmatched');
    const actioned = mapping.correctionAction && mapping.correctionActor;
    form.querySelector('[data-change-row]').hidden = !actioned;
    form.querySelector('[data-change]').textContent = actioned ? t('{actor} · {time} UTC', {actor: mapping.correctionActor, time: stamp(mapping.correctionAt)}) : '';
    form.querySelector('[data-change-note-row]').hidden = !(actioned && mapping.correctionNote);
    form.querySelector('[data-change-note]').textContent = mapping.correctionNote || '';
    correction.innerHTML = `<option value="">${t('Choose an action')}</option><option value="replace">${t('Set a definition')}</option>` +
      (targets.length ? `<option value="remove">${t('Remove mapping')}</option>` : '') +
      (changed ? `<option value="restore">${t('Restore original mapping')}</option>` : '');
    correction.value = keepChoice ? previous : '';
    if (!keepChoice) { target.value = ''; note.value = ''; }
    void loadDecision();
    sync();
  };
  assertion.addEventListener('change', () => showAssertion());
  correction.addEventListener('change', sync);
  target.addEventListener('change', sync);
  decisionBody.addEventListener('input', event => {
    if (event.target.name === 'decision') draft.decision = event.target.value;
    if (event.target.name === 'rationale') draft.note = event.target.value;
    sync();
  });
  const failure = (text, reload = false) => {
    alert.hidden = false;
    alert.textContent = text;
    if (!reload) return;
    alert.append(' ');
    const link = document.createElement('a');
    link.href = location.href;
    link.textContent = t('Reload workspace');
    link.addEventListener('click', event => { event.preventDefault(); location.reload(); });
    alert.append(link);
  };
  const lock = value => {
    busy = value;
    assertion.disabled = correction.disabled = note.disabled = value;
    decisionBody.querySelectorAll('select, textarea').forEach(item => { item.disabled = value; });
    sync();
  };
  let previousRequest;
  let requestId;
  let correctionSaved = false;
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (save.disabled || readOnly()) return;
    alert.hidden = true;
    lock(true);
    const {observation} = selected();
    let failed = false;
    try {
      if (correctionReady() && !correctionSaved) {
        const {mapping} = selected();
        const change = {dataset:scope, sourceId:passage.sourceId, observationId:observation.id,
          action:correction.value, targetId:target.disabled ? null : target.value,
          correctionRevision:mapping.correctionRevision, ...(note.value.trim() ? {note:note.value.trim()} : {})};
        const serialized = JSON.stringify(change);
        if (serialized !== previousRequest) {
          previousRequest = serialized;
          requestId = crypto.randomUUID();
        }
        const before = describe(selected());
        try {
          await callLocal('/api/mapping-correction', {method:'POST', headers:{'Content-Type':'application/json'},
            body:JSON.stringify({...change, requestId})});
          correctionSaved = true;
        } catch (error) {
          failed = true;
          if (error.status !== 409) failure(V2I18n.server(error.message));
          else {
            // Stale revision. Keep the choice and note, show what changed, and let the user re-apply.
            try {
              entries = await refresh(observation.id);
              writable = pick(entries);
              const index = writable.findIndex(item => item.observation.id === observation.id);
              if (index < 0) throw new Error(t('The cited evidence is no longer editable.'));
              assertion.innerHTML = assertionOptions(observation.id);
              assertion.value = String(index);
              showAssertion(true);
              const latest = writable[index];
              failure(latest.mapping.correctionActor
                ? t('This mapping changed while you were editing. It now reads "{now}" (was "{before}"), changed by {actor} at {time} UTC. Your choice and note are kept. Save again to apply them to the latest mapping.', {now: describe(latest), before, actor: latest.mapping.correctionActor, time: stamp(latest.mapping.correctionAt)})
                : t('This mapping changed while you were editing. It now reads "{now}" (was "{before}"). Your choice and note are kept. Save again to apply them to the latest mapping.', {now: describe(latest), before}));
            } catch {
              failure(t('Mapping changed. Reload the workspace to see the latest mapping.'), true);
            }
          }
        }
      }
      if (!failed && decisionReady()) {
        const {mapping} = selected();
        const state = decisionState();
        try {
          const receipt = await callLocal('/api/review', {method:'POST', headers:{'Content-Type':'application/json'},
            body:JSON.stringify({dataset:scope, mappingId:mapping.id, decision:draft.decision, note:draft.note,
              evidenceRevision:state.revision})});
          reviews.set(mapping.id, {...state, receipt});
          const list = session().decisions;
          session().decisions = [...list.filter(item => item.mappingId !== receipt.mappingId), receipt];
          draft.decision = draft.note = '';
        } catch (error) {
          failed = true;
          let text = t('The decision could not be saved. {message}', {message: V2I18n.server(error.message)});
          if (error.status === 409) {
            try {
              const latest = await callLocal('/api/review?' + new URLSearchParams({dataset:scope, mappingId:mapping.id}));
              reviews.set(mapping.id, {status:'ready', receipt:latest.receipt, revision:latest.evidenceRevision});
              text += ' ' + (latest.receipt ? t('The saved decision is shown. Your note is kept below it.')
                : t('The evidence changed. Your decision is kept. Save again to apply it.'));
            } catch { text += ' ' + t('Reload the workspace to check the saved decision.'); }
          }
          failure(text);
        }
      }
      if (correctionSaved && failed) {
        // The correction already landed. Show the new mapping and keep the unsaved decision.
        entries = await refresh(observation.id);
        writable = pick(entries);
        assertion.innerHTML = assertionOptions(observation.id);
        assertion.value = String(Math.max(0, writable.findIndex(item => item.observation.id === observation.id)));
        correctionSaved = false;
        showAssertion();
      }
    } catch (error) {
      failed = true;
      failure(correctionSaved ? t('Correction saved. Reload the workspace to see it.') : V2I18n.server(error.message), true);
    }
    if (!failed) {
      try { await refresh(observation.id); } catch { failure(t('Changes saved. Reload the workspace to see them.'), true); lock(false); return; }
      if (dialog.open && form.isConnected) {
        dialog.close();
        document.querySelector('[data-open-mapping]')?.focus();
      }
      if (locationView.dataset === scope) document.querySelector('#announcer').textContent = t('Changes saved.');
      return;
    }
    if (form.isConnected) { lock(false); renderDecision(); }
  });
  showAssertion();
  assertion.focus();
}

function mountMappingGraph(root) {
  let {passages, sources, entries} = mappingGraph();
  const inspector = document.querySelector('#graph-detail');
  const definitions = document.querySelector('#graph-definitions');
  const filters = document.querySelector('#graph-filters');
  const sourcePicker = filters.elements.source;
  const initialEvidence = locationView.observation;
  const initial = passages.find(passage => passage.evidence.some(item => item.id.split(':').at(-1) === initialEvidence));
  let sourceId = initial?.sourceId || passages.find(passage => passage.evidence.some(item =>
    entries.get(item.id)?.some(entry => entry.targets.length)))?.sourceId;
  let selected = '';
  let passageIndex = 0;
  let connections = [];
  let filtered = [];
  let activePassages = [];
  let activeDefinition = null;
  let preferredEvidence = initialEvidence;
  let expanded = Boolean(initial);
  const titleFor = source => ocrSourceText(source.title || source.name);
  const typeOptions = Object.entries(passageTypes).filter(([kind]) => passages.some(passage => passage.kind === kind));
  filters.elements.type.insertAdjacentHTML('beforeend', typeOptions.map(([kind,label]) =>
    `<option value="${kind}">${t(label)}</option>`).join(''));

  const inspect = (choice, preferred = null) => {
    selected = choice;
    preferredEvidence = preferred;
    activeDefinition = connections.find(item => item.definition.id === choice) || null;
    activePassages = activeDefinition ? activeDefinition.passages : filtered;
    const selectedPassage = activePassages.findIndex(passage => passage.evidence.some(item => item.id.split(':').at(-1) === preferred));
    passageIndex = Math.max(0, selectedPassage);
    renderPassage();
  };

  const renderPassage = () => {
    const source = sources.get(sourceId);
    const passage = activePassages[passageIndex];
    const evidence = passage?.evidence.find(item => item.id.split(':').at(-1) === preferredEvidence) ||
      passage?.evidence.find(item => !activeDefinition || entries.get(item.id)?.some(entry =>
        entry.targets.some(target => target.id === activeDefinition.definition.id))) || passage?.evidence[0];
    const sourceLink = source ? hash('ingest', source.id +
      (evidence ? '/' + evidence.id.split(':').at(-1) : '')) : '';
    const definition = activeDefinition?.definition;
    inspector.innerHTML = source ? `<h3>${html(definition?.title || definition?.name || titleFor(source))}</h3>
      ${definition ? `<p class="helper">${html(titleFor(source))}</p>` : ''}
      ${passage ? `<button type="button" class="secondary mapping-open" data-open-mapping>${t(readOnly() || !passage.evidence.some(item => entries.get(item.id)?.some(entry => entry.mapping.correctionRevision)) ? 'Open passage' : 'Edit mapping')}</button>
      <details class="mapping-disclosure"${expanded ? ' open' : ''}><summary>${t('Supporting evidence')}</summary>
      ${definition ? `<p>${html(ocrSourceText(definition.description))}</p>` : ''}
      <section class="graph-passage"><p class="mini-label">${html(t(passageTypes[passage.kind] || 'Source passage'))}</p>
        <h4>${html(ocrSourceText(passage.title) || titleFor(source))}</h4>${graphPassageText(passage)}
        <p class="helper">${html(observationLocator(passage.citation ? passage : evidence || passage))}</p>
        <div class="actions"><a href="${html(sourceLink)}">${t('Inspect cited source →')}</a></div></section>
      ${activePassages.length > 1 ? `<div class="graph-passage-nav" aria-label="${html(t('Evidence passages'))}">
        <button type="button" class="secondary" data-passage-step="-1"${passageIndex === 0 ? ' disabled' : ''}>${t('Previous')}</button>
        <span>${t('Passage {n} of {total}', {n: passageIndex + 1, total: activePassages.length})}</span>
        <button type="button" class="secondary" data-passage-step="1"${passageIndex + 1 === activePassages.length ? ' disabled' : ''}>${t('Next')}</button>
      </div>` : ''}</details>` : `<p>${t('No supporting passages match these filters.')}</p>`}` : `<p>${t('Select a source to inspect its grouped passages.')}</p>`;
    const disclosure = inspector.querySelector('details');
    disclosure?.addEventListener('toggle', () => { if (disclosure.isConnected) expanded = disclosure.open; });
    definitions.querySelectorAll('[data-item]').forEach(button => {
      button.setAttribute('aria-pressed', String(button.dataset.item === selected));
    });
  };

  knowledgeCanvas = window.GraphCanvas.mount(root, {
    label:t('Evidence-to-definition mappings'), statusNote:t('Select a document or definition to inspect its evidence.'),
    searchPlaceholder:t('Find a document or definition'),
    onSelect(choice) {
      const id = choice.target || choice.id;
      inspect(connections.some(item => item.definition.id === id) ? id : sourceId);
    }
  });

  function update() {
    const type = filters.elements.type.value;
    const query = filters.elements.query.value.trim().toLowerCase();
    const status = filters.elements.status.value;
    const typed = passages.filter(passage => !type || passage.kind === type);
    const candidates = typed.flatMap(passage => {
      let matching = passage.evidence.flatMap(item => entries.get(item.id) || []).filter(({mapping, targets}) =>
        status === 'all' || (status === 'suggested' ? targets.length :
          status === 'corrected' ? ['replace','remove'].includes(mapping.correctionAction) : mapping.status === status));
      if (status !== 'all' && !matching.length && !(status === 'unmapped' &&
          !passage.evidence.some(item => entries.has(item.id)))) return [];
      const hasQuery = value => String(value || '').toLowerCase().includes(query);
      if (query && !passage.searchableText.includes(query)) {
        matching = matching.filter(({targets}) => targets.some(target => hasQuery(target.title || target.name)));
        if (!matching.length) return [];
      }
      return [{passage, matching}];
    });
    const candidatesBySource = candidates.length ? candidates.map(({passage}) => passage) : typed;
    const availableSources = [...new Set(candidatesBySource.map(passage => passage.sourceId))];
    if (!availableSources.includes(sourceId)) sourceId = availableSources[0];
    sourcePicker.innerHTML = availableSources.length ? availableSources.map(id =>
      `<option value="${html(id)}"${id === sourceId ? ' selected' : ''}>${html(titleFor(sources.get(id)))}</option>`).join('') :
      `<option>${t('No available sources')}</option>`;
    const source = sources.get(sourceId);
    const targetLinks = new Map();
    filtered = candidates.filter(({passage}) => passage.sourceId === sourceId).map(({passage, matching}) => {
      for (const {mapping, targets} of matching) for (const definition of targets) {
        if (!targetLinks.has(definition.id)) targetLinks.set(definition.id, {definition, passages:[], statuses:new Set()});
        const target = targetLinks.get(definition.id);
        if (!target.passages.includes(passage)) target.passages.push(passage);
        target.statuses.add(mapping.status);
      }
      return passage;
    });
    connections = [...targetLinks.values()];
    const nodes = source && filtered.length ? [
      {id:sourceId, kind:'doc', label:titleFor(source), description:t('{n} supporting passages', {n: filtered.length})},
      ...connections.map(({definition, passages}) => ({id:definition.id, kind:'semantic',
        label:definition.title || definition.name, description:t('{n} supporting passages', {n: passages.length})}))
    ] : [];
    const edges = connections.map(({definition, statuses}) => ({id:sourceId + '>' + definition.id, source:sourceId,
      target:definition.id, kind:'defined_by', label:t(statuses.has('ambiguous') ? 'ambiguous' :
        statuses.has('corrected') ? 'corrected' : statuses.has('mapped') ? 'mapped' : 'suggested')}));
    const positions = Object.fromEntries(nodes.map((node, index) => [node.id, index === 0 ? {x:0,y:0} :
      {x:360,y:(index - (nodes.length / 2)) * 84}]));
    knowledgeCanvas.set({nodes, edges, positions});
    definitions.innerHTML = nodes.length ? nodes.map(node =>
      `<button class="definition-entry" data-item="${html(node.id)}" type="button"><strong>${html(node.label)}</strong>
      <small>${html(node.description)}</small></button>`).join('') : `<p class="empty">${t('No supporting passages match these filters.')}</p>`;
    inspect(nodes.some(node => node.id === selected) ? selected : sourceId, preferredEvidence);
  }

  sourcePicker.addEventListener('change', () => {
    sourceId = sourcePicker.value;
    selected = '';
    preferredEvidence = null;
    update();
  });
  filters.addEventListener('submit', event => event.preventDefault());
  filters.elements.type.addEventListener('change', update);
  filters.elements.status.addEventListener('change', update);
  filters.elements.query.addEventListener('input', update);
  definitions.addEventListener('click', event => {
    const id = event.target.closest('[data-item]')?.dataset.item;
    if (!id) return;
    inspect(id);
    knowledgeCanvas.highlight({nodes:[id],edges:[]});
  });
  // Double-click opens the editor for the selection. The canvas redraws on pointerdown, so a native dblclick never
  // reaches a node. Detect two quick presses on one node, and keep the native event for edges.
  const openSelection = () => inspector.querySelector('[data-open-mapping]')?.click();
  let lastPress = {id:'', at:0};
  root.addEventListener('pointerdown', event => {
    const hit = event.target.closest?.('[data-node-id], [data-edge-id]');
    const id = hit?.dataset.nodeId || hit?.dataset.edgeId;
    if (!id || event.button !== 0) return;
    const double = id === lastPress.id && event.timeStamp - lastPress.at < 450;
    lastPress = {id, at: double ? 0 : event.timeStamp};
    if (double) setTimeout(openSelection);
  }, true);
  root.addEventListener('dblclick', event => {
    if (event.target.closest?.('[data-edge-id]')) openSelection();
  });
  inspector.addEventListener('click', event => {
    const opener = event.target.closest('[data-open-mapping]');
    if (opener) {
      if (dialog.open) return;
      const passage = activePassages[passageIndex];
      const scope = locationView.dataset;
      openMappingEditor(passage, entries, activeDefinition?.definition, preferredEvidence, async observationId => {
        await refreshKnowledge(scope);
        if (!root.isConnected) return entries;
        ({passages, sources, entries} = mappingGraph());
        preferredEvidence = observationId.split(':').at(-1);
        update();
        return entries;
      });
      return;
    }
    const button = event.target.closest('[data-passage-step]');
    if (!button) return;
    const step = Number(button.dataset.passageStep);
    passageIndex += step;
    preferredEvidence = null;
    renderPassage();
    (inspector.querySelector(`[data-passage-step="${step}"]:not(:disabled)`) ||
      inspector.querySelector('[data-passage-step]:not(:disabled)'))?.focus();
  });
  update();
}

function knowledgeNavigation() {
  const tabs = [
    ['', 'Definition mappings'], ['ontology', 'Ontology'], ['talk', 'Talk to Data']
  ];
  return `<nav class="action-row" aria-label="${html(t('Knowledge views'))}">${tabs.map(([key, title]) => {
    const active = key === (['ontology', 'talk'].includes(locationView.detail) ? locationView.detail : '');
    return `<a class="button ${active ? 'primary' : 'secondary'}" href="${hash('knowledge', key)}"${active ? ' aria-current="page"' : ''}>${t(title)}</a>`;
  }).join('')}</nav>`;
}

function renderKnowledge() {
  const ontology = locationView.detail === 'ontology';
  if (locationView.detail === 'talk') {
    return heading(stageTitle('knowledge'), t('{name} · Talk to Data', {name: dataset().name})) +
      `<div id="knowledge-workspace-tools">${knowledgeNavigation()}</div>` + renderTalkToData(localRuntime);
  }
  return heading(stageTitle('knowledge'), t(ontology ? '{name} · Shared definitions' : '{name} · Evidence and semantic definitions', {name: dataset().name}), readOnly() ? '' : link(t('Next: {step} →', {step: html(stageTitle('review'))}), hash('review'), 'primary')) +
    panel(t(ontology ? 'Domain ontology' : 'Evidence-to-definition mappings'), `<div id="knowledge-workspace-tools">${knowledgeNavigation()}
    ${ontology ? `<span class="small muted">${t('Definition metadata · Draft review')}</span>` : ''}</div>
    ${ontology && !readOnly() ? `<div id="catalog-build" class="catalog-build"><button type="button" data-build-preview>${t('Build concept catalog')}</button><div data-build-result role="status" aria-live="polite"></div></div>` : ''}
    ${ontology ? '' : `<form class="evidence-filters" id="graph-filters" role="search" aria-label="${html(t('Filter supporting passages'))}">
      <label>${t('Source file')}<select name="source"></select></label>
      <label>${t('Content type')}<select name="type"><option value="">${t('All types')}</option></select></label>
      <label>${t('Mapping status')}<select name="status">${mappingStatusOptions('all')}<option value="corrected">${t('Corrected')}</option></select></label>
      <label>${t('Find passages')}<input type="search" name="query" placeholder="${html(t('Search values, text or definitions'))}"></label>
    </form>`}
    ${ontology ? `<div id="graph-live"><p class="helper" role="status">${t('Loading the graph from the database…')}</p></div>` : `<div id="graph-stage" class="graph-stage"><div id="graph-canvas" class="graph-canvas"></div><aside class="knowledge-rail has-selection">
    <section class="definition-catalog"><h3>${t('Document and definitions')}</h3><div id="graph-definitions"></div></section>
    <section class="graph-inspector inspector mapping-inspector" id="graph-detail" aria-label="${html(t('Selected item'))}"></section></aside></div>`}`);
}

// The database graph: nodes and edges come from GET /api/graph (private PostgreSQL rows). No fixture arrays.
const graphColumns = {source:0, chunk:1, entity:2, definition:3, concept:4};
const graphKinds = {source:'doc', chunk:'mapping', entity:'party', definition:'semantic', concept:'view'};

// Admin build of the concept catalog: preview the proposed catalog, then confirm that exact version.
function mountCatalogBuild() {
  const root = document.querySelector('#catalog-build');
  if (!root) return;
  const out = root.querySelector('[data-build-result]'), dataset = locationView.dataset;
  const post = (action, body) => callLocal('/api/catalog/build/' + action, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  const fail = error => { out.innerHTML = `<p class="workspace-error" role="alert">${html(V2I18n.server(error.message))}</p>`; };
  root.querySelector('[data-build-preview]').addEventListener('click', async event => {
    event.currentTarget.disabled = true;
    out.innerHTML = `<p class="helper">${t('Building a preview. Nothing is written yet.')}</p>`;
    try {
      const preview = await post('preview', {dataset});
      const {concepts, links, byStatus} = preview.counts;
      out.innerHTML = `<p data-build-counts>${t('{concepts}, {links} ·', {concepts: counted(concepts, 'concept'), links: counted(links, 'definition link')})}
        ${t('{statuses} · version {version}', {statuses: Object.entries(byStatus).map(([status, n]) => `${n} ${html(V2I18n.server(status))}`).join(', ') || t('none'), version: html(preview.version)})}</p>
        <ul class="concept-links">${preview.concepts.slice(0, 20).map(c => `<li><strong>${html(c.label)}</strong> <small>${html(c.origin)} · ${html(V2I18n.server(c.status))}</small></li>`).join('')}</ul>
        <button type="button" class="primary" data-build-confirm>${t('Write catalog {version}', {version: html(preview.version)})}</button>`;
      out.querySelector('[data-build-confirm]').addEventListener('click', async confirm => {
        confirm.currentTarget.disabled = true;
        try {
          const done = await post('confirm', {dataset, version:preview.version});
          out.innerHTML = `<p data-build-done>${t('Catalog {version} written ({count}).', {version: html(done.version), count: counted(done.written, 'concept')})}</p>`;
          knowledgeCanvas?.destroy();
          knowledgeCanvas = null;
          mountDatabaseGraph();
        } catch (error) { fail(error); }
      });
    } catch (error) { fail(error); }
    event.currentTarget.disabled = false;
  });
}

async function mountDatabaseGraph() {
  const live = document.querySelector('#graph-live');
  if (!live) return;
  const scope = locationView.dataset;
  let graph;
  try {
    graph = await callLocal('/api/graph?' + new URLSearchParams({dataset:scope}));
  } catch (error) {
    if (live.isConnected) live.innerHTML = `<p class="workspace-error" role="alert">${html(V2I18n.server(error.message))}</p>`;
    return;
  }
  if (!live.isConnected || locationView.dataset !== scope) return;
  if (graph.empty) {
    live.innerHTML = `<p class="empty" data-graph-empty>${html(V2I18n.server(graph.note) || t('Nothing is ingested for this dataset yet.'))}</p>`;
    return;
  }
  const rows = {};
  const nodes = graph.nodes.map(item => ({id:item.id, kind:graphKinds[item.kind] || 'doc', label:ocrSourceText(item.label),
    description:t(item.kind), data_type:item.kind}));
  const positions = Object.fromEntries(graph.nodes.map(item => {
    const row = rows[item.kind] = (rows[item.kind] ?? -1) + 1;
    return [item.id, {x:(graphColumns[item.kind] ?? 0) * 330, y:row * 84}];
  }));
  const edges = graph.edges.map(item => ({id:`${item.source}>${item.target}:${item.relation}`, source:item.source, target:item.target,
    kind:item.relation + (item.status === 'proposed' ? ' proposed' : ''), label:item.relation.replace(/_/g, ' ')}));
  const listed = ['concept', 'definition'].flatMap(kind => graph.nodes.filter(item => item.kind === kind)).slice(0, 200);
  live.innerHTML = `<div id="graph-stage" class="graph-stage"><div id="graph-canvas" class="graph-canvas"></div><aside class="knowledge-rail has-selection">
    <section class="definition-catalog"><h3>${t('Concepts and definitions')}</h3><div id="graph-definitions">
    ${listed.map(item => `<button class="definition-entry" type="button" data-item="${html(item.id)}"><strong>${html(ocrSourceText(item.label))}</strong><small>${html(t(item.kind))}${item.origin ? ' · ' + html(item.origin) : ''}</small></button>`).join('')}
    </div></section><section class="graph-inspector inspector" id="graph-detail" aria-label="${html(t('Selected item'))}"></section></aside></div>`;
  const inspector = document.querySelector('#graph-detail');
  const labels = [...document.querySelectorAll('#graph-definitions [data-item]')];
  const byId = new Map(graph.nodes.map(item => [item.id, item]));
  const inspect = choice => {
    const item = byId.get(choice.id || choice.source);
    if (!item) return;
    const related = graph.edges.filter(edge => edge.source === item.id || edge.target === item.id).slice(0, 12);
    inspector.innerHTML = `<h3>${html(ocrSourceText(item.label))}</h3>
      ${fieldRows([[t('Kind'), t(item.kind)], ...(item.origin ? [[t('Origin'), item.origin]] : []), ...(item.status ? [[t('Status'), V2I18n.server(item.status)]] : [])])}
      ${related.length ? `<ul>${related.map(edge => `<li>${html(edge.relation.replace(/_/g, ' '))} · ${html(ocrSourceText(
        byId.get(edge.source === item.id ? edge.target : edge.source)?.label || ''))}</li>`).join('')}</ul>` : ''}
      ${item.kind === 'concept' ? `<p class="helper">${t('Edit a concept from a supporting passage in Definition mappings.')}</p>` : ''}`;
    labels.forEach(label => label.setAttribute('aria-pressed', String(label.dataset.item === item.id)));
  };
  knowledgeCanvas = window.GraphCanvas.mount(document.querySelector('#graph-canvas'), {
    onSelect: inspect, label:t('Database graph'), statusNote:t('Rows of the private PostgreSQL store'),
    searchPlaceholder:t('Find a concept or definition')
  });
  knowledgeCanvas.set({nodes, edges, positions});
  labels.forEach(label => label.addEventListener('click', () => {
    inspect({id:label.dataset.item});
    knowledgeCanvas.highlight({nodes:[label.dataset.item], edges:[]});
  }));
  if (listed.length) inspect({id:listed[0].id});
}

function mountGraph() {
  if (locationView.detail === 'ontology') { mountCatalogBuild(); return mountDatabaseGraph(); }
  const root = document.querySelector('#graph-canvas');
  if (!root) return;
  return mountMappingGraph(root);
}
