function savedEvidence(source) {
  if (!source || source.file || source.access === 'withheld' || locationView.observation) return '';
  const knowledge = prepared.get(locationView.dataset)?.knowledge;
  const saved = mountedIngest?.source?.id === source.id ? mountedIngest.saved : null;
  if (!saved && mountedIngest?.savedPending) return `<p class="helper" role="status">${t('Checking saved results…')}</p>`;
  const retained = saved?.observations || (knowledge?.observations || []).filter(item =>
    item.sourceId === processingSourceId(source) && item.citation?.kind);
  if (!retained.length && !saved?.fields?.length && !saved?.tables?.length &&
      !saved?.documentPages?.length && !saved?.sourcePages?.length) return '';
  const evidence = saved || {
    observations:retained,
    passages:(knowledge?.passages || []).filter(item => item.sourceId === processingSourceId(source)),
    mappings:(knowledge?.mappings || []).filter(item => item.sourceId === processingSourceId(source))
  };
  return renderExtraction(evidence, evidence, t('Saved results'));
}

function selectedEvidence(source) {
  if (source?.access === 'withheld') return source;
  if (!source || !locationView.observation) return source;
  const observationId = locationView.dataset + ':observation:' + locationView.observation;
  const sourceId = processingSourceId(source);
  const observation = observations().find(item => item.id === observationId && item.sourceId === sourceId);
  return observation ? {
    ...source, quote: observation.quote, locator: observationLocator(observation),
    detail: observation.description, provenance: observation.citation
  } : null;
}

function sourceEvidence(source) {
  if (source.access === 'withheld') return `<p class="note">${t('This account cannot view the original or cited passages.')}</p>`;
  return `<div class="quote"><strong>${html(source.locator)}</strong>${source.quote
    ? `<blockquote>${html(source.quote)}</blockquote>`
    : `<p>${html(source.excerpt)}</p>`}</div><p class="helper">${html(source.detail)}</p>${source.provenance?.path || source.provenance?.manifest
    ? fieldRows([[t('Prepared source'), source.provenance.path || source.provenance.manifest], ...(source.provenance.section ? [[t('Section'), source.provenance.section]] : [])])
    : ''}`;
}

const mediaExtensions = {
  wav:'audio/wav', mp3:'audio/mpeg', m4a:'audio/mp4', ogg:'audio/ogg', flac:'audio/flac',
  mp4:'video/mp4', mov:'video/quicktime', webm:'video/webm', mkv:'video/x-matroska'
};
const documentExtensions = {
  pdf:'application/pdf', png:'image/png', jpg:'image/jpeg', jpeg:'image/jpeg', webp:'image/webp',
  gif:'image/gif', bmp:'image/bmp', svg:'image/svg+xml', txt:'text/plain', md:'text/markdown',
  csv:'text/csv', json:'application/json'
};

function fileMime(file) {
  const extension = file.name.split('.').at(-1).toLowerCase();
  return mediaExtensions[extension] || documentExtensions[extension] || file.type || 'application/octet-stream';
}

function ingestMode(source) {
  return source?.kind?.startsWith('audio') || source?.kind?.startsWith('video') ||
    /^(audio|video)\//.test(source?.mimeType || '') ? 'media' : 'document';
}

function ingestView() {
  const state = session();
  const sources = currentSources().concat(readOnly() ? [] : state.files);
  const requested = sources.find(item => item.id === locationView.detail);
  const mode = requested ? ingestMode(requested) : state.ingestMode;
  // "All sources" lists everything that is processed; the workbook (it simulates the structured database), model definitions and crosswalks are not sources.
  // A walkthrough entry with no prepared record is sample text, not a source, and is not listed.
  const recordOf = item => prepared.get(locationView.dataset)?.sources.find(record =>
    record.id === locationView.dataset + ':source:' + (item.preparedId || item.id));
  const listed = item => mode === 'all' ? Boolean(item.file) || (Boolean(recordOf(item)) && !/^(workbook|structured-model|mapping-reference)$/.test(recordOf(item).kind))
    : ingestMode(item) === mode && (item.libraryFile || item.file);
  const visible = sources.filter(item => listed(item) || item === requested);
  return {mode, visible, source:requested};
}

function settingsFor(source) {
  if (!source) return null;
  const stored = session().ingestSettings;
  if (!stored.has(source.id)) stored.set(source.id, {
    pages:'', response:'text', images:true, header:false, footer:false, boxes:false, definitionGuided:true,
    tables:'markdown', language:'auto', timestamps:true, prompt:'', output:'', tab:'configure'
  });
  return stored.get(source.id);
}

function ingestToolbar(detail = false) {
  return `<div class="ingest-toolbar">
      <div><h1 id="ingest-title">${html(stageTitle('ingest'))}</h1><p class="helper first">${html(dataset().name)}</p></div>
      <div class="ingest-top-actions">
        ${detail ? link(t('← Back to files'), hash('ingest')) : ''}
        ${readOnly() || !detail ? '' : `<input id="workspace-file" type="file" multiple class="visually-hidden" aria-label="${html(t('Add files'))}"><label class="secondary button ingest-add" for="workspace-file">${t('+ Add files')}</label>`}
        ${link(t('Next: Model →'), hash('model'), 'secondary')}
      </div>
    </div>`;
}

function ingestModeTabs(mode) {
  return `<div class="tabs" aria-label="${html(t('Source type'))}">
    ${[['all','All sources'], ['document','Documents'], ['media','Audio & video']].map(([key,label]) => `<button type="button" class="tab" data-ingest-mode="${key}" aria-pressed="${mode === key}">${t(label)}</button>`).join('')}
    <a class="tab${mode === 'structured' ? ' selected-tab' : ''}" href="${hash('ingest','structured')}"${mode === 'structured' ? ' aria-current="page"' : ''}>${t('Structured data')}</a>
  </div>`;
}

function workbookSource(id = locationView.dataset) {
  return prepared.get(id)?.sources.find(source => source.kind === 'workbook' && source.access !== 'withheld');
}

function renderStructured() {
  const structured = prepared.get(locationView.dataset)?.knowledge?.structured;
  const tables = structured?.datasets || [];
  const selected = tables.find(table => encodeURIComponent(table.name) === locationView.observation);
  const showSchema = Boolean(locationView.observation);
  const workbook = workbookSource();
  const inventory = workbook?.structuredImport || structured?.structuredImport;
  const fromDocuments = new Set((inventory?.fromDocuments || []).map(table => table.id));
  const counts = new Map((inventory?.tables || []).map(table => [table.id, table]));
  const messages = session().workbookMessages;
  const fields = selected ? (structured.fields || []).filter(field => field.name.startsWith(selected.name + '/')) : [];
  const relationships = selected ? (structured.relationships || []).filter(join => join.from === selected.name || join.to === selected.name) : [];
  const selectedImport = selected && counts.get(selected.id);
  const summary = inventory?.status === 'available'
    ? t('{available} of {total} prepared tables have synthetic rows.', {available: inventory.availableTableCount, total: inventory.tableCount})
    : inventory || workbook ? (inventory?.reason ? t('Prepared row counts are unavailable ({reason}). The table definitions remain available.', {reason: V2I18n.server(inventory.reason)}) : t('Prepared row counts are unavailable. The table definitions remain available.')) : t('Model definitions are available. Row counts are unavailable in this view.');
  return `<section class="panel ingest-workspace structured-workspace" data-intake aria-labelledby="ingest-title">
    ${ingestToolbar()}
    <div class="ingest-sourcebar">${ingestModeTabs('structured')}</div>
    <div class="structured-intro"><h2>${html(t('{ns} · Structured database (simulated)', {ns: structured?.namespace || t('Structured model')}))}</h2><p>${html(summary)}</p>
      ${messages.length ? `<ul class="note structured-messages" role="status">${messages.map(message => `<li>${html(message)}</li>`).join('')}</ul>` : ''}
    </div>
    ${!showSchema ? `<div class="structured-summary">
      ${fieldRows([[t('Semantic model'), structured?.namespace || t('Unavailable')], [t('Tables'), tables.length],
        [t('Fields'), structured?.fields?.length || 0], [t('Relationships'), structured?.relationships?.length || 0],
        [t('Metrics'), tables.flatMap(table => table.metrics || []).length]])}
      <p class="helper">${t('The Model step shows the tables, relationships and metrics as a diagram.')}</p>
      ${tables.length ? link(t('Inspect table schema →'), hash('ingest','structured/' + encodeURIComponent(tables[0].name)), 'primary') : ''}
    </div>` : `<div class="structured-detail-nav">${link(t('← Semantic layer summary'), hash('ingest','structured'))}</div>
    <div class="structured-layout">
      <div class="table-wrap structured-tables"><table aria-label="${html(t('Structured tables'))}"><thead><tr><th scope="col">${t('Table')}</th><th scope="col">${t('Prepared rows')}</th></tr></thead><tbody>
        ${tables.map(table => {
          const item = counts.get(table.id);
          return `<tr${table.id === selected?.id ? ' class="current-table"' : ''}><td><a href="${hash('ingest','structured/' + encodeURIComponent(table.name))}"${table.id === selected?.id ? ' aria-current="location"' : ''}>${html(table.title)}</a></td>
          <td>${item?.status === 'available' && typeof item.rowCount === 'number' ? html(item.rowCount.toLocaleString()) : fromDocuments.has(table.id) ? t('Filled from documents') : t('Unavailable')}</td></tr>`;
        }).join('')}
      </tbody></table></div>
      <section class="structured-detail">${selected ? `<h2>${html(selected.title)}</h2><p class="helper first">${html(selected.description)}</p>
        ${fieldRows([[t('Primary key'), (selected.primary_key || []).join(', ') || t('No key declared')], [t('Fields'), fields.length], [t('Declared relationships'), relationships.length],
          ...(selectedImport?.filename ? [[t('Prepared file'), selectedImport.filename]] : [])])}
        <div class="table-wrap"><table aria-label="${html(t('Table fields'))}"><thead><tr><th scope="col">${t('Field')}</th><th scope="col">${t('Datatype')}</th><th scope="col">${t('Definition')}</th></tr></thead><tbody>
          ${fields.map(field => {
            const name = field.name.slice(selected.name.length + 1);
            return `<tr><td>${html(name)}${selected.primary_key?.includes(name) ? t(' · Key') : ''}</td><td>${html(field.datatype || t('Unspecified'))}</td><td>${html(field.description)}</td></tr>`;
          }).join('')}
        </tbody></table></div>
        ${selected.metrics?.length ? `<section class="structured-metrics"><h3>${t('Model metrics')}</h3>${fieldRows(selected.metrics.map(metric => [
          metric.label && metric.label !== metric.name ? `${metric.label} (${metric.name})` : metric.name, metric.description
        ]))}</section>` : ''}
        ${relationships.length ? `<details><summary>${t('View declared relationships')}</summary>${fieldRows(relationships.map(join => [join.title, `${join.from} (${join.from_columns.join(', ')}) → ${join.to} (${join.to_columns.join(', ')})`]))}</details>` : ''}
      ` : `<p class="empty">${t('This table is unavailable in the selected dataset.')}</p>`}</section>
    </div>`}</section>`;
}

function renderFileLibrary() {
  const {mode, visible} = ingestView();
  const query = session().ingestSearch;
  const recent = session().newFiles;
  const ordered = visible.filter(item => recent.includes(item.id)).concat(visible.filter(item => !recent.includes(item.id)));
  const rows = ordered.map(item => {
    const added = recent.includes(item.id);
    const status = item.file ? t('Local to this tab') : item.access === 'withheld' ? t('Source withheld') : t(item.label);
    const record = prepared.get(locationView.dataset)?.sources.find(source => source.id === locationView.dataset + ':source:' + (item.preparedId || item.id));
    const label = item.file ? 'Local file' : window.V2Pipeline?.dataLabel(record || item) || '';   // English: compared below; translated where shown
    return `<tr data-file-row="${html(item.id)}"${added ? ' class="file-added"' : ''}>
      <td><a class="file-open" data-file-id="${html(item.id)}" href="${hash('ingest', item.id)}">${icon('doc')}<span>${html(item.title)}</span></a></td>
      <td>${html(kindText(item.kind))}</td><td>${badge(t(label), label === 'Synthetic' ? 'blue' : '')}</td>
      <td>${badge(status, item.file ? 'blue' : '')}${added ? ` ${badge(t('Added'), 'blue')}` : ''}</td>
      <td data-result-for="${html(record?.id || '')}">${item.access === 'withheld' ? '' : record?.kind === 'workbook' ? html(counted(record.structuredImport?.tableCount ?? 0, 'table')) : /^(structured-model|mapping-reference)$/.test(record?.kind) ? `<span class="helper">${t('Definitions only')}</span>` : record && localRuntime ? `<span class="helper" data-pending>${t('Checking…')}</span>` : ''}</td>
    </tr>`;
  }).join('');
  return `<section class="panel ingest-workspace file-library" data-intake aria-labelledby="ingest-title">
    ${ingestToolbar()}
    <div class="file-library-layout${readOnly() ? '' : ' has-upload'}"><div class="file-library-list">
    <div class="ingest-sourcebar">${ingestModeTabs(mode)}
      <label class="search">${icon('search')}<input type="search" id="file-search" placeholder="${html(t('Search files'))}" aria-label="${html(t('Search files'))}" value="${html(query)}"></label>
    </div>
    <p class="visually-hidden" id="file-added-status" role="status">${recent.length ? html(t(recent.length === 1 ? '{n} file added.' : '{n} files added.', {n: recent.length})) : ''}</p>
    <div class="table-wrap"><table class="file-table" aria-label="${html(t('Files'))}">
      <thead><tr><th scope="col">${t('Name')}</th><th scope="col">${t('Kind')}</th><th scope="col">${t('Data')}</th><th scope="col">${t('State')}</th><th scope="col">${t('Result')}</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>
    <p class="empty file-library-empty" id="file-list-empty" hidden></p></div>
    ${readOnly() ? '' : `<aside class="file-library-upload" aria-labelledby="upload-title">
      <h2 id="upload-title">${t('Add files')}</h2>
      <input id="workspace-file" type="file" multiple class="visually-hidden" aria-label="${html(t('Add files'))}">
      <label class="dropzone ingest-dropzone" for="workspace-file" data-upload-dropzone>
        <span class="upload-arrow" aria-hidden="true">↑</span><strong>${t('Choose files or drop them here')}</strong>
        <span>${t('PDF, documents, images, text, audio and video. Excel opens in Structured data.')}</span>
      </label>
    </aside>`}
    </div>
  </section>`;
}

// Result counts per source from the stored evidence links. A source with no link row is not processed yet.
async function fillSourceResults() {
  const id = locationView.dataset;
  if (!localRuntime || !window.V2Pipeline) return;
  let counts;
  try { counts = await window.V2Pipeline.linkCounts({dataset:id, request:callLocal}); } catch { return; }
  if (locationView.dataset !== id) return;
  main.querySelectorAll('[data-result-for]').forEach(cell => {
    if (!cell.querySelector('[data-pending]')) return;
    const count = counts.get(cell.dataset.resultFor);
    cell.innerHTML = count ? `<a href="${hash('evidence', cell.dataset.resultFor.split(':source:').at(-1))}">${t(count.passages === 1 ? '{passages} passage · {linked} linked' : '{passages} passages · {linked} linked', {passages: count.passages, linked: count.linked})}</a>`
      : `<span class="helper">${t('Not processed')}</span>`;
  });
}

function renderIngest() {
  if (locationView.detail === 'structured') return renderStructured();
  if (!locationView.detail) return renderFileLibrary();
  const {mode, visible, source} = ingestView();
  const missing = locationView.detail.startsWith('local-') && !readOnly()
    ? t('This local file is no longer available in this tab. Add it again from Files.')
    : t('This file is unavailable for this dataset or role.');
  const selection = visible.map(item => `<option value="${html(item.id)}"${item.id === source?.id ? ' selected' : ''}>${html(item.title)}</option>`).join('');
  return `<section class="panel ingest-workspace" aria-labelledby="ingest-title">
    ${ingestToolbar(true)}
    <div class="ingest-sourcebar">
      ${ingestModeTabs(mode)}
      <label class="ingest-source-choice">${t('Files')}<select id="ingest-source">${source ? '' : `<option value="">${t('Select a source')}</option>`}${selection}</select></label>
    </div>
    <div class="ingest-split">
      <section class="ingest-original" data-intake aria-labelledby="original-title">
        <div class="ingest-pane-head"><div><h2 id="original-title">${source ? html(source.title) : t('Original')}</h2><p class="helper first">${source ? html(source.file ? t('Local to this tab') : kindRaw(source.kind)) : ''}</p></div><div class="ingest-original-actions"><div id="original-download"></div>${source?.file && !readOnly() ? btn(t('Remove file'), 'ingest-remove') : ''}</div></div>
        ${source?.status === 'xfa_render_required' ? `<p class="ingest-xfa">${t('This XFA form requires an XFA-compatible PDF viewer. Use Download original if the browser shows a placeholder.')}</p>` : ''}
        <div id="original-content" class="original-content">${source ? `<p class="empty">${t('Loading original…')}</p>` : `<p class="empty">${missing}</p>`}</div>
      </section>
      <aside class="ingest-inspector side-panel" aria-label="${html(t('File processing'))}">
        <div id="ingest-inspector"></div>
      </aside>
    </div>
  </section>`;
}

async function getOriginal(id, source) {
  if (source.access === 'withheld') return {status:'withheld'};
  if (source.file) return {status:'available', filename:source.file.name, mimeType:source.mimeType, bytes:source.file.size, canPreview:true, originalUrl:source.url};
  if (!localRuntime) return {status:'offline'};
  const sourceId = `${id}:source:${source.preparedId || source.id}`;
  if (!prepared.get(id)?.sources?.some(item => item.id === sourceId)) return {status:'unsupported'};
  const params = new URLSearchParams({dataset:id, sourceId});
  return callLocal('/api/original-info?' + params);
}

function canRunOcr(source, info) {
  return localRuntime && !readOnly() && ocrConnector.available && source?.status !== 'xfa_render_required' &&
    info?.status === 'available' && ['application/pdf','image/png','image/jpeg'].includes(info.mimeType) &&
    info.bytes <= 12 * 1024 * 1024;
}

function canTranscribe(source, info) {
  return localRuntime && !readOnly() && ocrConnector.transcription?.available && !source?.file &&
    info?.status === 'available' && /^(audio|video)\//.test(info.mimeType || '') && info.bytes <= 25 * 1024 * 1024;
}

function canReadForm(source, info) {
  return localRuntime && !readOnly() && source?.status === 'xfa_render_required' && info?.status === 'available';
}

function canReadPinnedText(source, info) {
  return localRuntime && !readOnly() && source && !source.file && displayType(info) === 'text';
}

function ocrOptions(setting) {
  return {
    ...(setting.pages.trim() ? {pages:setting.pages.trim()} : {}),
    includeImageBase64:setting.images, extractHeader:setting.header, extractFooter:setting.footer,
    tableFormat:setting.tables === 'html' ? 'html' : 'markdown',
    ...(setting.definitionGuided ? {definitionGuided:true} : {}),
    ...(setting.prompt.trim() ? {annotationPrompt:setting.prompt.trim()} : {})
  };
}

function tableReferenceOnly(value) {
  const lines = String(value ?? '').split('\n').map(line => line.trim()).filter(Boolean);
  return lines.length > 0 && lines.every(line =>
    /^(?:\[tbl-[^\]]+\]\(tbl-[^)]+\)|tbl-[\w.-]+\.(?:html|md))$/i.test(line));
}

function fieldLocation(source) {
  const location = observationLocator({citation:source});
  return source?.tableId ? t('{location} · Table {id}', {location, id: source.tableId}) : location;
}

function uniqueFields(fields) {
  const groups = new Map();
  for (const item of fields || []) {
    if (item.value == null || item.value === '') continue;
    const key = JSON.stringify([item.type, item.value]);
    if (!groups.has(key)) groups.set(key, {type:item.type, value:item.value, locations:new Map(), labels:new Set()});
    const group = groups.get(key);
    if (item.label && item.label.toLowerCase() !== String(item.type).toLowerCase()) group.labels.add(item.label);
    const location = fieldLocation(item.source);
    const label = item.label || '';
    group.locations.set(JSON.stringify([location, label]), {location, label});
  }
  return [...groups.values()];
}

function renderFields(fields, origin) {
  return `<section class="result-fields">${origin === 'pinned_pdf_text_layer'
    ? `<p class="helper">${t('Recovered from the original PDF text.')}</p>` : ''}
    <div class="table-wrap"><table aria-label="${html(t('Extracted fields'))}"><thead><tr><th>${t('Type')}</th><th>${t('Value')}</th><th>${t('Location')}</th></tr></thead><tbody>
    ${fields.map(field => `<tr><td>${html(field.type ? String(field.type).replaceAll('_', ' ') : t('Field'))}
      ${[...field.labels].map(label => `<small>${html(label)}</small>`).join('')}</td>
      <td>${html(maskIdentifiers(field.value))}</td><td>${[...new Set([...field.locations.values()].map(ref => field.labels.size > 1 && ref.label ? ref.label + ' · ' + ref.location : ref.location))].map(html).join('<br>')}</td></tr>`).join('')}
    </tbody></table></div></section>`;
}

function mappingStatusOptions(selected) {
  return [['all','All evidence'], ['mapped','Mapped automatically'], ['suggested','With suggestions'], ['ambiguous','Ambiguous'], ['unmapped','Unmatched']]
    .map(([id,label]) => `<option value="${id}"${selected === id ? ' selected' : ''}>${t(label)}</option>`).join('');
}

function renderDefinitionLink(evidence) {
  const entries = mappingEntries(evidence);
  const observation = entries[0]?.observation || evidence?.observations?.find(item => item.observationRole !== 'context');
  const definitions = new Set(entries.flatMap(item => item.targets.map(target => target.id)));
  const labeling = evidence?.labeling;
  if (!observation && !labeling) return '';
  const linked = new Set(entries.filter(({mapping}) => ['mapped','corrected'].includes(mapping.status))
    .flatMap(item => item.targets.map(target => target.id)));
  const suggested = [...definitions].filter(id => !linked.has(id)).length;
  const summary = [linked.size && t(linked.size === 1 ? '{n} linked definition' : '{n} linked definitions', {n: linked.size}),
    suggested && t(suggested === 1 ? '{n} suggested definition' : '{n} suggested definitions', {n: suggested})].filter(Boolean);
  const label = summary.join(' · ') || t('No definition linked');
  const status = labeling?.status === 'degraded' ? t('AI mapping unavailable') :
    labeling?.status === 'partial' ? t('AI mapping partially completed') : '';
  const destination = observation ? hash('knowledge','mappings/' + observation.id.split(':').at(-1)) : hash('knowledge');
  return `<footer class="result-mapping"><span>${html([label, status].filter(Boolean).join(' · '))}</span>${link(t('Open in Knowledge →'), destination, 'text-link')}</footer>`;
}

function markdownCells(line) {
  const cells = line.trim().split(/(?<!\\)\|/);
  if (cells.length < 2) return null;
  if (cells[0] === '') cells.shift();
  if (cells.at(-1) === '') cells.pop();
  return cells.length ? cells.map(cell => cell.trim().replaceAll('\\|','|')) : null;
}

const markdownSeparator = cells => cells?.every(cell => /^:?-{3,}:?$/.test(cell));
const framedMarkdownRow = line => /^\s*\|.*\|\s*$/.test(line);

function attachmentRows(table) {
  if (table.unparsed) return null;
  if (Array.isArray(table.rows) && table.rows.length) {
    return {rows:table.rows, headerRows:table.headerRows || 0, format:table.format};
  }
  if (typeof table.content !== 'string') return null;
  if (/<table\b/i.test(table.content)) {
    const template = document.createElement('template');
    template.innerHTML = table.content;
    const source = template.content.querySelector('table');
    if (!source?.rows.length) return null;
    return {rows:Array.from(source.rows, row => Array.from(row.cells, cell => cell.textContent)),
      headerRows:source.tHead?.rows.length || 0,
      caption:source.caption?.textContent};
  }
  const rows = [];
  let headerRows = 0;
  for (const line of table.content.split('\n')) {
    if (!line.trim()) continue;
    const cells = markdownCells(line);
    if (!cells || (cells.length === 1 && !framedMarkdownRow(line)) ||
        (rows.length && cells.length !== rows[0].length)) return null;
    if (markdownSeparator(cells)) {
      if (rows.length !== 1 || headerRows) return null;
      headerRows = 1;
    } else rows.push(cells);
  }
  return rows.length ? {rows, headerRows, format:'markdown'} : null;
}

function ocrCell(cell, format) {
  return format === 'markdown' ? inlineDocumentText(cell) : html(maskIdentifiers(cell));
}

function ocrTableRows(parsed) {
  const rows = (values, tag) => values.map(row => `<tr>${row.map(cell =>
    `<${tag}>${ocrCell(cell, parsed.format)}</${tag}>`).join('')}</tr>`).join('');
  return `${parsed.headerRows ? `<thead>${rows(parsed.rows.slice(0,parsed.headerRows), 'th')}</thead>` : ''}
    <tbody>${rows(parsed.rows.slice(parsed.headerRows), 'td')}</tbody>`;
}

function renderResultTable(table, page, ordinal, heading = '') {
  const parsed = attachmentRows(table);
  const caption = table.caption || parsed?.caption || heading ||
    (page == null ? t('Table {n}', {n: ordinal}) : t('Page {page} · Table {n}', {page, n: ordinal}));
  const locator = [table.locator || (page == null ? '' : t('Page {n}', {n: page})), table.id].filter(Boolean).join(' · ');
  if (!parsed) {
    return `<section class="ocr-table-unreadable"><h4>${html(maskIdentifiers(caption))}</h4>
      <p class="helper first">${t('{locator} · The extracted table has no readable cell structure.', {locator: html(maskIdentifiers(locator))})}</p>
      ${table.content ? ocrSourcePre(table.content) : ''}</section>`;
  }
  return `<div class="table-wrap ocr-document-table"><table class="ocr-data-table">
    <caption>${html(maskIdentifiers(caption))}${locator ? `<small>${html(maskIdentifiers(locator))}</small>` : ''}</caption>
    ${ocrTableRows(parsed)}
  </table></div>`;
}

function inlineDocumentText(value) {
  const masked = maskIdentifiers(value);
  const plain = masked.replace(/([*_\`]+)([^*_\`]+)\1/g, '$2');
  if (maskIdentifiers(plain) !== plain) return html(maskIdentifiers(plain));
  return html(masked).split(/(`[^`]+\`|\*\*[^*]+\*\*|__[^_]+__|\*[^*]+\*|_[^_]+_)/g).map((part, index) => {
    if (index % 2 === 0) return part;
    if (part.startsWith('`')) return `<code>${part.slice(1,-1)}</code>`;
    if (part.startsWith('**') || part.startsWith('__')) return `<strong>${part.slice(2,-2)}</strong>`;
    return `<em>${part.slice(1,-1)}</em>`;
  }).join('');
}

function tableReference(line) {
  return /^\s*\[[^\]]+\]\((tbl-[^)]+)\)\s*$/i.exec(line)?.[1] ||
    /^\s*(tbl-[\w.-]+\.(?:html|md))\s*$/i.exec(line)?.[1];
}

function ocrSourceText(value) {
  const decoder = document.createElement('template');
  const decoded = String(value ?? '').replace(/&(?:#\d{1,8}|#x[\da-f]{1,8}|[a-z][a-z\d]{1,31});/gi, entity => {
    decoder.innerHTML = entity;
    return decoder.content.textContent;
  });
  // Mask identifiers split across provider markup while keeping other markup as text.
  const separator = '(?:<[^<>\\n]{0,160}>|[*_`])*';
  const splitIdentifier = new RegExp('\\b[STFGM](?:' + separator + '[0-9]){7}' + separator + '[A-Z]\\b', 'gi');
  return maskIdentifiers(decoded.replace(splitIdentifier, match =>
    maskIdentifiers(match.replace(/<[^<>\n]+>|[*_`]/g, ''))));
}

const ocrSourcePre = value => `<pre class="ingest-output ocr-source-lines" tabindex="0"><span>${html(ocrSourceText(value))}</span></pre>`;

function renderDocumentMarkdown(markdown, context) {
  const lines = String(markdown ?? '').split('\n');
  const blocks = [];
  let text = [];
  const flush = () => {
    if (context.ocr && text.length > 1) {
      blocks.push(`<p class="ocr-text-lines">${inlineDocumentText(text.join('\n'))}</p>`);
    } else if (text.length) {
      blocks.push(`<p>${inlineDocumentText(text.join(' '))}</p>`);
    }
    text = [];
  };
  const marker = line => /^\s*(?:([-+*])|(\d+)\.)\s+(.+)$/.exec(line);
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const heading = /^(#{1,6})\s+(.+)$/.exec(line.trim());
    const cells = markdownCells(line);
    const nextLine = lines[i + 1] || '';
    const nextCells = markdownCells(nextLine);
    const hasHeader = Boolean(cells?.length && nextCells?.length === cells.length && markdownSeparator(nextCells));
    let framedEnd = i;
    if (!hasHeader && framedMarkdownRow(line)) {
      while (framedEnd + 1 < lines.length && framedMarkdownRow(lines[framedEnd + 1])) framedEnd++;
    }
    const framed = framedEnd > i && cells?.length >= 2;
    const malformed = framedEnd > i && (!framed || lines.slice(i, framedEnd + 1).some(row =>
      markdownCells(row)?.length !== cells.length || markdownSeparator(markdownCells(row))));
    const reference = tableReference(line);
    const item = marker(line);
    if (!line.trim()) flush();
    else if (reference) {
      flush();
      blocks.push(context.renderTable(reference));
    } else if (heading) {
      flush();
      context.heading = heading[2].replace(/\s+#+\s*$/, '');
      const level = Math.min(6, 3 + heading[1].length);
      blocks.push(`<h${level}>${inlineDocumentText(context.heading)}</h${level}>`);
    } else if (malformed && context.ocr) {
      flush();
      blocks.push(`<div class="ocr-unparsed-section"><p class="helper first">${t('Table layout could not be recovered. OCR lines follow.')}</p>${
        ocrSourcePre(lines.slice(i, framedEnd + 1).join('\n'))}</div>`);
      i = framedEnd;
    } else if (hasHeader || (framed && !malformed)) {
      flush();
      const firstLine = i + 1;
      const rows = [cells];
      if (hasHeader) i++;
      while (i + 1 < lines.length) {
        const next = markdownCells(lines[i + 1]);
        if (!next || markdownSeparator(next) ||
            (!hasHeader && (next.length !== cells.length || !framedMarkdownRow(lines[i + 1])))) break;
        rows.push(next);
        i++;
      }
      blocks.push(context.inlineTable({rows, headerRows:hasHeader ? 1 : 0, format:'markdown', locator:t('Line {n}', {n: firstLine})}));
    } else if (/^\s*<table\b/i.test(line)) {
      flush();
      const content = [line];
      while (!/<\/table>/i.test(content.at(-1)) && i + 1 < lines.length) content.push(lines[++i]);
      blocks.push(context.inlineTable({content:content.join('\n')}));
    } else if (item) {
      flush();
      const ordered = Boolean(item[2]);
      const tag = ordered ? 'ol' : 'ul';
      const entries = [item[3]];
      while (i + 1 < lines.length) {
        const next = marker(lines[i + 1]);
        if (!next || Boolean(next[2]) !== ordered) break;
        entries.push(next[3]);
        i++;
      }
      const start = ordered && Number(item[2]) > 1 ? ` start="${html(item[2])}"` : '';
      blocks.push(`<${tag}${start}>${entries.map(entry => `<li>${inlineDocumentText(entry)}</li>`).join('')}</${tag}>`);
    } else if (/^\s*```/.test(line)) {
      flush();
      const code = [];
      while (i + 1 < lines.length && !/^\s*```/.test(lines[i + 1])) code.push(lines[++i]);
      if (i + 1 < lines.length) i++;
      blocks.push(`<pre class="ingest-output">${html(maskIdentifiers(code.join('\n')))}</pre>`);
    } else text.push(context.ocr ? line : line.trim());
  }
  flush();
  return blocks.join('');
}

function renderResultDocument(result) {
  const kind = result.observations?.[0]?.citation?.kind;
  const isForm = result.model === 'xfa-datasets' || kind === 'xfa_fields';
  const isText = result.model === 'utf8-text' || kind === 'native_text';
  const rawPages = result.pages || [];
  const sourcePages = new Map((result.sourcePages || []).map(page => [page.page, page]));
  const tables = result.tables?.length ? result.tables : rawPages.flatMap(page =>
    (page.tables || []).map((table, index) => ({...table, page:Number(page.index) + 1, id:table.id || 'table-' + (index + 1)})));
  const orderedPages = new Map((result.documentPages || []).map(page => [page.page,page]));
  const pageNumbers = [...new Set([...orderedPages.keys(), ...sourcePages.keys(), ...rawPages.map(page => Number(page.index) + 1),
    ...tables.map(table => table.page)])].sort((a,b) => a - b);
  const articles = pageNumbers.map(page => {
    const ordered = orderedPages.get(page);
    const raw = rawPages.find(item => Number(item.index) + 1 === page);
    const pageTables = tables.filter(table => table.page === page);
    const ocr = !isForm && !isText;
    const source = sourcePages.get(page);
    const sourceText = raw?.markdown ?? source?.text ?? ordered?.blocks
      .filter(block => block.kind === 'markdown').map(block => block.markdown).join('\n\n');
    const sourceAttachments = raw?.tables?.flatMap((table, index) => {
      if (typeof table?.content !== 'string') return [];
      const id = table.id || 'detached-' + (index + 1);
      return [{id, text:table.content, parsed:source?.attachments?.find(item => item.id === id)?.parsed}];
    }) ?? source?.attachments ?? [];
    const readableAttachments = sourceAttachments.filter(item => item.text?.trim());
    const unreadable = readableAttachments.filter(item => item.parsed === false ||
      (item.parsed == null && !pageTables.some(table => table.id === item.id && attachmentRows(table))));
    const used = new Set();
    let ordinal = 0;
    const context = {
      heading:'',
      ocr,
      renderTable(id) {
        if (used.has(id)) return '';
        used.add(id);
        const table = pageTables.find(item => item.id === id);
        if (!table) {
          const attachment = unreadable.find(item => item.id === id);
          if (attachment) return renderResultTable({id, content:attachment.text, unparsed:true}, page, ++ordinal, context.heading);
          return `<p class="note">${t('The original referenced table {id}. This result has no readable cells for it.', {id: html(ocrSourceText(id))})}</p>`;
        }
        return renderResultTable(table, page, ++ordinal, context.heading);
      },
      inlineTable(table) {
        const rows = attachmentRows(table)?.rows;
        const match = rows && pageTables.find(item => !used.has(item.id) &&
          JSON.stringify(attachmentRows(item)?.rows) === JSON.stringify(rows));
        if (match) return context.renderTable(match.id);
        return renderResultTable(table, page, ++ordinal, context.heading);
      }
    };
    const text = ordered ? ordered.blocks.map(block => {
      if (block.kind === 'table' || block.kind === 'missing_table') return context.renderTable(block.tableId);
      if (block.kind === 'markdown') return renderDocumentMarkdown(block.markdown, context);
      return '';
    }).join('') : renderDocumentMarkdown(raw?.markdown ?? source?.text, context);
    const remaining = pageTables.filter(table => !used.has(table.id));
    const remainingUnreadable = ocr ? unreadable.filter(item => !used.has(item.id) &&
      !remaining.some(table => table.id === item.id)) : [];
    const clipped = !raw && source?.truncated;
    const details = ocr && (sourceText?.trim() || readableAttachments.length || clipped) ? `<details class="ocr-source-details">
      <summary>${t('OCR source text')}</summary>
      ${source?.kind === 'retained_fragments' || (!source && !raw) ?
        `<p class="helper">${t('These retained OCR excerpts may omit parts of the response.')}</p>` : ''}
      ${clipped ? `<p class="helper">${t('Some source text was omitted because it exceeded the capture limit.')}</p>` : ''}
      ${sourceText?.trim() ? ocrSourcePre(sourceText) : ''}
      ${readableAttachments.map(item => `<p class="helper">${t('Attachment {id}', {id: html(ocrSourceText(item.id))})}</p>${ocrSourcePre(item.text)}`).join('')}
    </details>` : '';
    const images = (raw?.images || []).filter(entry =>
      entry && /^data:image\/(?:png|jpeg|gif|webp);base64,[A-Za-z0-9+/=]+$/.test(entry.image_base64 || ''));
    if (!text && !remaining.length && !remainingUnreadable.length && !details && !images.length && !raw?.header && !raw?.footer) return '';
    return `<article class="ocr-document-page"><h4>${isForm ? t('Embedded form data') : isText ? t('Original text') : page == null ? t('Retained content') : t('Page {n}', {n: html(page)})}</h4>
      ${ordered?.order === 'retained_fragments' ? `<p class="helper first">${t('These are retained excerpts. Their full source order is unavailable.')}</p>` : ''}
      ${raw?.header ? `<div class="ocr-page-note"><strong>${t('Header')}</strong><p>${html(maskIdentifiers(raw.header))}</p></div>` : ''}
      <div class="ocr-document-body">${text}</div>
      ${remaining.length ? `<p class="helper">${t('The result has no recorded text position for these tables.')}</p>
        ${remaining.map(table => renderResultTable(table,page,++ordinal)).join('')}` : ''}
      ${remainingUnreadable.map(item =>
        renderResultTable({id:item.id, content:item.text, unparsed:true}, page, ++ordinal)).join('')}
      ${images.map(entry => `<img src="${html(entry.image_base64)}" alt="${html(entry.id || t('Extracted image'))}">`).join('')}
      ${raw?.footer ? `<div class="ocr-page-note"><strong>${t('Footer')}</strong><p>${html(maskIdentifiers(raw.footer))}</p></div>` : ''}
      ${details}
    </article>`;
  }).join('');
  return articles ? `<section class="ocr-document"><h3>${t('Extracted document')}</h3>${articles}</section>` : '';
}

// A transcript read as turns: consecutive segments of one detected speaker, with the time they cover.
function speakerTurns(observations) {
  const segments = (observations || []).filter(item => item.citation?.kind === 'transcript' && item.observationRole !== 'context')
    .sort((a, b) => (a.citation.startLine ?? 0) - (b.citation.startLine ?? 0));
  if (!segments.some(item => item.citation.speaker)) return '';
  const clock = seconds => seconds == null ? '' : `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;
  const turns = [];
  for (const item of segments) {
    const speaker = item.citation.speaker || 'Unlabelled speaker', last = turns.at(-1);
    if (last && last.speaker === speaker) { last.lines.push(item.quote); last.end = item.citation.endSeconds ?? last.end; }
    else turns.push({speaker, lines:[item.quote], start:item.citation.startSeconds, end:item.citation.endSeconds});
  }
  const names = [...new Set(turns.map(turn => turn.speaker))];
  const label = speaker => /^speaker_\d+$/.test(speaker) ? t('Speaker {n}', {n: speaker.split('_')[1]}) : speaker === 'Unlabelled speaker' ? t('Unlabelled speaker') : speaker;
  return `<section class="speaker-transcript" aria-label="${html(t('Transcript by speaker'))}">
    <p class="helper first">${t(names.length === 1 ? '{n} detected speaker · {turns} turns. Speaker labels come from the transcription model; they are not identities.' : '{n} detected speakers · {turns} turns. Speaker labels come from the transcription model; they are not identities.', {n: names.length, turns: turns.length})}</p>
    <ol>${turns.map(turn => `<li data-speaker="${names.indexOf(turn.speaker) % 4}"><div class="speaker-head"><strong>${html(label(turn.speaker))}</strong>
      <span class="helper">${html(clock(turn.start))}${turn.end != null ? '–' + html(clock(turn.end)) : ''}</span></div>
      <p>${html(maskIdentifiers(turn.lines.join(' ')))}</p></li>`).join('')}</ol></section>`;
}

function renderExtraction(result, evidence, title, note = '') {
  const fields = uniqueFields(result.fields);
  let document = speakerTurns(evidence?.observations) || renderResultDocument(result);
  if (!document) {
    const passages = evidence?.passages || [];
    if (passages.length) {
      document = passages.map(item => `<article class="result-passage">${graphPassageText(item)}</article>`).join('');
    } else {
      const excerpts = (evidence?.observations || []).filter(item => item.observationRole !== 'context' && !tableReferenceOnly(item.quote));
      if (excerpts.length) document = `<div class="result-passage">${html(maskIdentifiers(excerpts.map(item => item.quote).join('\n')))}</div>`;
    }
  }
  const knownTables = new Set((result.tables || []).map(item => `${item.page}/${item.id}`));
  const missingTable = !result.documentPages?.length && (evidence?.observations || []).some(item =>
    tableReferenceOnly(item.quote) && item.quote.split('\n').some(line =>
      tableReference(line) && !knownTables.has(`${item.citation?.page}/${tableReference(line)}`)));
  const canSwitch = Boolean(document && fields.length);
  const views = canSwitch ? `<div class="extraction-views" role="group" aria-label="${html(t('Extraction view'))}">
      <button type="button" data-extraction-view="content" aria-pressed="true">${t('Content')}</button>
      <button type="button" data-extraction-view="fields" aria-pressed="false">${t('Fields ({n})', {n: fields.length})}</button></div>` : '';
  return `<section class="ingest-result-block"><div class="extraction-heading"><h3>${html(title)}</h3>${views}</div>
    ${document ? `<div data-extraction-panel="content">${document}</div>` : ''}
    ${fields.length ? `<div data-extraction-panel="fields"${canSwitch ? ' hidden' : ''}>${renderFields(fields, result.fieldOrigin)}</div>` : ''}
    ${missingTable ? `<p class="note">${fields.length
      ? t('The saved result has table references without readable cells. Recovered values are available in Fields.') : t('The saved result has table references without readable cells. An admin can rerun extraction to recover the readable tables.')}</p>` : ''}
    ${!document && !fields.length && !missingTable ? `<p class="note">${t('The response contains no readable content.')}</p>` : ''}
    ${note}${renderDefinitionLink(evidence)}</section>`;
}

function renderOcrResult(result) {
  const title = result.model === 'xfa-datasets' ? t('Form results') :
    result.model === 'utf8-text' ? t('Text results')
    : /^(audio|video)\//.test(result.source?.mimeType || '') ? t('Transcript results') : t('Mistral OCR results');
  const annotation = result.annotation?.findings || [];
  const notes = [
    annotation.length && !result.fields?.length ? `<details><summary>${t('Document annotations')}</summary>${fieldRows(annotation.map(item => [item.label,item.value]))}</details>` : '',
    result.evidenceStatus === 'ephemeral' ? `<p class="helper">${t('Available in this browser tab.')}</p>` : '',
    result.evidenceError ? `<p class="note workspace-error" role="alert">${html(V2I18n.server(result.evidenceError))}</p>` : ''
  ].join('');
  return renderExtraction(result, result.evidence, title, notes);
}

function displayType(info) {
  if (info?.status !== 'available') return '';
  const mime = info.mimeType || '';
  if (/^(audio|video|image)\//.test(mime)) return mime.split('/')[0];
  if (mime === 'application/pdf') return 'pdf';
  if (mime.startsWith('text/') || mime === 'application/json') return 'text';
  return 'download';
}

function metadataPreview(source, info, originalText) {
  const missing = {
    withheld: t('This account cannot view the original or cited passages.'),
    offline: t('Original files from the source library require the local rehearsal server. You can open a file from your device here.'),
    missing: t('The prepared source record is available. Its original file is missing.'),
    changed: t('The original differs from the prepared source record. Restore the pinned file before opening it.'),
    unsupported: t('This source record has no original file to open.')
  };
  if (info.status !== 'available') return `<p class="empty">${html(missing[info.status] || V2I18n.server(info.error) || t('The original could not be opened.'))}</p>`;
  const url = html(info.originalUrl);
  switch (displayType(info)) {
    case 'pdf':
      return `<object class="original-pdf" data="${url}" type="application/pdf" aria-label="${html(t('PDF original for {title}', {title: source.title}))}"><p class="empty">${t('Your browser cannot display this PDF. Use Download original.')}</p></object>`;
    case 'image':
      return `<img class="original-image" src="${url}" alt="${html(t('Original image from {title}', {title: source.title}))}">`;
    case 'text':
      return `<pre class="original-text" tabindex="0" aria-label="${html(t('Complete original text'))}">${html(originalText)}</pre>`;
    case 'audio':
      return `<audio src="${url}" controls preload="metadata" aria-label="${html(t('Play {title}', {title: source.title}))}"></audio>`;
    case 'video':
      return `<video src="${url}" controls preload="metadata" aria-label="${html(t('Play {title}', {title: source.title}))}"></video>`;
    default:
      return `<p class="empty">${t('A browser preview is unavailable for this format. Use Download original to open the file.')}</p>`;
  }
}

const optionControl = (label, name, options, setting) => `<label class="ingest-option"><span>${t(label)}</span><select data-setting="${name}">${options.map(([key, text]) => `<option value="${key}"${setting[name] === key ? ' selected' : ''}>${t(text)}</option>`).join('')}</select></label>`;
const checkControl = (label, name, value) => `<label class="ingest-check"><input type="checkbox" data-setting="${name}"${value ? ' checked' : ''}>${t(label)}</label>`;
const isPageSelection = value => !value.trim() || value.split(',').every(part => {
  const match = /^\s*([1-9]\d*)(?:\s*-\s*([1-9]\d*))?\s*$/.exec(part);
  return match && Number.isSafeInteger(Number(match[1])) &&
    (!match[2] || Number.isSafeInteger(Number(match[2])) && Number(match[2]) >= Number(match[1]));
});

function renderInspector(source, info) {
  const target = document.querySelector('#ingest-inspector');
  if (readOnly()) {
    const evidence = selectedEvidence(source);
    const retained = savedEvidence(source);
    target.innerHTML = `<div class="ingest-pane-head"><h2>${t('Source evidence')}</h2></div><div class="ingest-results">${retained || (evidence
      ? sourceEvidence(evidence)
      : `<p class="empty">${t('No permitted passage is available.')}</p>`)}</div>`;
    return;
  }
  const type = displayType(info);
  const setting = settingsFor(source);
  const selected = selectedEvidence(source);
  const currentTab = setting?.tab || 'configure';
  const media = ingestMode(source) === 'media';
  const available = info?.status === 'available';
  const previewRequest = available && ['pdf','image','audio','video'].includes(type);
  const pageField = type === 'pdf';
  const active = Boolean(source) && !readOnly() && !setting?.running;
  const liveOcr = canRunOcr(source, info);
  const liveMedia = canTranscribe(source, info);
  const localForm = canReadForm(source, info);
  const pinnedText = canReadPinnedText(source, info);
  const processor = type === 'text' ? t('Local text reader') : media ? (liveMedia ? ocrConnector.transcription.model || 'Mistral transcription' : t('Transcription connector unavailable'))
    : liveOcr ? ocrConnector.model || 'Mistral OCR' : t('OCR connector unavailable');
  const disclaimer = liveMedia ? t('Running transcription sends this audio to Mistral. Audio and video files are limited to 25 MiB.')
    : type === 'text' ? (pinnedText ? t('Read the text and map its relevant passages using Mistral.') : t('Read the selected text file in this browser.'))
    : liveOcr ? t('Running OCR sends this original and the displayed request options to Mistral.')
    : ocrConnector.available && ['pdf','image'].includes(type) ? t('OCR accepts PDF, PNG or JPEG files up to 12 MiB.')
    : t('Processing requires a connected provider. You can inspect a request without sending it.');
  const prompt = setting ? `<label class="field ingest-prompt">${t('Extraction instructions')} <span class="small muted">${t('Optional · Up to 2,000 characters')}</span>
      <textarea data-setting="prompt" maxlength="${promptLimit}" rows="3"${readOnly() ? ' readonly' : ''}>${html(setting.prompt)}</textarea>
    </label>` : '';
  // One disclosure holds every per-file processing setting. Defaults apply while it stays closed.
  const options = body => `<details class="ingest-options" data-ingest-options${setting?.optionsOpen ? ' open' : ''}><summary>${t('Processing settings')}</summary>${body}</details>`;
  const settings = (setting?.running ? ingestProgress(setting, localForm ? 'form' : media ? 'media' : liveOcr ? 'ocr' : 'text') : '') +
    (!source ? `<p class="empty">${t('Select a file to configure processing.')}</p>` : localForm
    ? `<div class="ingest-processing"><p class="helper first">${t('Read the populated form fields. Run also sends relevant excerpts to Mistral for definition mapping.')}</p><div class="actions">${btn(t(setting.running ? 'Reading…' : 'Read form fields'),'ingest-form','primary',!active)}</div></div>`
    : type === 'download' || !available
    ? `${info ? options(prompt) : ''}<div class="ingest-processing"><p class="helper first">${!info ? t('Checking the original…') : type === 'download' ? t('A processor is unavailable for this original format.') : t('Processing requires an available original.')}</p><div class="actions">${btn(t('Run'),'ingest-run','primary',true)}</div></div>` : `
    ${options(`<fieldset class="ingest-settings"${active ? '' : ' disabled'}><legend class="visually-hidden">${t('Processing settings')}</legend>
    <div class="ingest-option"><span>${t('Processor')}</span><strong>${processor}</strong></div>
    ${liveOcr || pinnedText ? '' : optionControl('Response format','response',[['text','Plain text'],['json','JSON record']],setting)}
    ${liveMedia ? '' : media ? optionControl('Language','language',[['auto','Detect automatically'],['en','English'],['zh','Chinese'],['ms','Malay']],setting) +
      `<div class="ingest-option"><span>${t('Transcript')}</span>${checkControl('Include timestamps','timestamps',setting.timestamps)}</div>`
      : `${pageField ? `<label class="ingest-option"><span>${t('Pages')} <small>${t('Blank uses all pages')}</small></span><input data-setting="pages" aria-describedby="pages-error" maxlength="100" placeholder="${html(t('e.g. 1-4, 8'))}" value="${html(setting.pages)}"></label><p id="pages-error" class="ingest-error" hidden>${t('Use positive page numbers or ranges, such as 1-4,8.')}</p>` : ''}
        ${type === 'text' ? '' : `<div class="ingest-option"><span>${t('Extract')}</span><div class="ingest-checks">${[['Images','images'], ['Header','header'], ['Footer','footer'], ...(!liveOcr ? [['Bounding boxes','boxes']] : [])].map(([label,key]) => checkControl(label,key,setting[key])).join('')}</div></div>
        ${optionControl('Extract tables','tables',liveOcr ? [['markdown','Markdown'],['html','HTML']] : [['markdown','Inline Markdown'],['json','JSON records'],['omit','Skip tables']],setting)}`}`}
    ${liveOcr ? `<div class="ingest-option"><span>${t('Use definitions')} <small>${t('Include permitted semantic definitions in document annotation')}</small></span>${checkControl('Guide annotation','definitionGuided',setting.definitionGuided)}</div>` : ''}
    ${pinnedText ? '' : prompt}</fieldset>`)}<div class="ingest-processing"><p class="helper first">${disclaimer}</p><div class="actions">
      ${previewRequest ? btn(t('Prepare request'),'ingest-prepare','secondary',!active) : ''}
      ${btn(t(setting.running ? 'Processing…' : liveMedia ? 'Transcribe' : liveOcr ? 'Run OCR' : pinnedText ? 'Read text' : 'Run'),'ingest-run','primary',!active || (type !== 'text' && !liveOcr && !liveMedia))}</div></div>`);
  const retained = !setting?.ocrResult ? savedEvidence(source) : '';
  const evidence = selected && (!source?.file || locationView.observation) &&
    (locationView.observation || (!setting?.ocrResult && !retained))
    ? `<section class="ingest-result-block"><h3>${t(locationView.observation ? 'Selected evidence' : 'Prepared evidence')}</h3>${sourceEvidence(selected)}</section>` : '';
  const result = setting?.ocrError ? `<p class="note workspace-error" role="alert">${html(setting.ocrError)}</p>`
    : setting?.ocrResult && !locationView.observation ? (setting.seconds != null ? `<p class="helper" data-ingest-seconds>${t('Finished in {n} s.', {n: setting.seconds})}</p>` : '') + renderOcrResult(setting.ocrResult)
    : setting?.output ? `<section class="ingest-result-block"><h3>${html(setting.output.title)}</h3><pre class="ingest-output" tabindex="0">${html(setting.output.text)}</pre></section>` : '';
  target.innerHTML = `<div class="ingest-pane-head ingest-inspector-tabs" aria-label="${html(t('File processing view'))}">
      ${[['configure','Configure'],['results','Results']].map(([key,label]) => `<button type="button" data-ingest-tab="${key}" aria-pressed="${currentTab === key}"${!source && key === 'results' ? ' disabled' : ''}>${t(label)}</button>`).join('')}
    </div>${currentTab === 'results' ? `<div class="ingest-results">${result}${retained}${evidence || (!result && !retained ? `<p class="empty">${t('Processing results appear here when a run completes.')}</p>` : '')}${localForm && (setting?.ocrResult || retained) ? `<section class="ingest-result-block" data-physical="${html(locationView.dataset + ':source:' + (source.preparedId || source.id))}"></section>` : ''}</div>` : settings}`;
  if (currentTab === 'results') mountPhysical();
  if (pageField && !localForm && currentTab === 'configure') markPages(setting);
}

function markPages(setting) {
  const input = document.querySelector('[data-setting="pages"]');
  const message = document.querySelector('#pages-error');
  if (!input) return;
  const valid = isPageSelection(setting.pages);
  input.setAttribute('aria-invalid', String(!valid));
  message.hidden = valid;
  const prepare = document.querySelector('[data-action="ingest-prepare"]');
  if (prepare) prepare.disabled = readOnly() || setting.running || !valid;
  const run = document.querySelector('[data-action="ingest-run"]');
  if (run && canRunOcr(mountedIngest?.source, mountedIngest?.info)) run.disabled = setting.running || !valid;
}

let mountedIngest;

// Output C. Physical mapping is template-bound: it works only for a document type with a predefined table.
const physicalViews = new Map();

function renderPhysical(view) {
  const head = `<h3>${t('Physical mapping')}</h3><p class="helper">${t('Physical mapping works only for document types with a predefined table.')}</p>`;
  if (!view) return head + `<p class="helper" role="status">${t('Checking for a predefined table…')}</p>`;
  if (view.error) return head + `<p class="note workspace-error" role="alert">${html(view.error)}</p>`;
  if (!view.writeAvailable) return head + `<p class="empty">${t('This document type has no predefined table. No write action is offered.')}</p>`;
  const cells = view.rows.flatMap(row => row.cells.map(cell => ({row, cell})));
  const pending = view.rows.some(row => row.status === 'previewed');
  return head + `<p class="helper">${t('Rows go to table {table} in the private V2 PostgreSQL, only after you confirm a preview. A cell that fails its type or unit check is rejected and not written.', {table: `<strong>${html(view.table)}</strong>`})}</p>
    ${cells.length ? `<div class="table-wrap"><table aria-label="${html(t('Physical mapping preview'))}"><thead><tr><th>${t('Row')}</th><th>${t('Column')}</th><th>${t('Value')}</th><th>${t('Cell')}</th><th>${t('Source span')}</th></tr></thead><tbody>
      ${cells.map(({row, cell}) => `<tr><td>${html(row.rowKey)} <small>${html(V2I18n.server(row.status))}</small></td><td>${html(cell.column)}</td>
        <td>${html(maskIdentifiers(cell.value))}</td><td>${html(V2I18n.server(cell.status))}${cell.reason ? `<small>${html(V2I18n.server(cell.reason))}</small>` : ''}</td>
        <td><small>${html(maskIdentifiers(cell.quote))}</small></td></tr>`).join('')}</tbody></table></div>` : `<p class="empty">${t('No rows yet. Preview the rows to see them before anything is written.')}</p>`}
    <div class="actions">${btn(t('Preview rows'), 'physical-preview', 'secondary', readOnly())}${btn(t('Confirm write'), 'physical-confirm', 'primary', readOnly() || !pending)}</div>`;
}

function paintPhysical() {
  document.querySelectorAll('[data-physical]').forEach(host => { host.innerHTML = renderPhysical(physicalViews.get(host.dataset.physical)); });
}

async function physicalRequest(sourceId, action) {
  const dataset = locationView.dataset;
  try {
    physicalViews.set(sourceId, action
      ? await callLocal('/api/physical-rows/' + action, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({dataset, sourceId})})
      : await callLocal('/api/physical-rows?' + new URLSearchParams({dataset, sourceId})));
  } catch (error) {
    physicalViews.set(sourceId, {error: V2I18n.server(error.message)});
  }
  paintPhysical();
}

function mountPhysical() {
  paintPhysical();
  document.querySelectorAll('[data-physical]').forEach(host => {
    if (!physicalViews.has(host.dataset.physical)) void physicalRequest(host.dataset.physical, null);
  });
}

async function loadSavedResult(task, id) {
  const {source, original} = task;
  if (!localRuntime || !source || source.file || source.access === 'withheld') return;
  const fullId = id + ':source:' + (source.preparedId || source.id);
  if (!prepared.get(id)?.sources.some(item => item.id === fullId)) return;
  task.savedRequest = new AbortController();
  task.savedPending = true;
  try {
    const params = new URLSearchParams({dataset:id, sourceId:fullId});
    const response = await callLocal('/api/mapped-evidence?' + params, {signal:task.savedRequest.signal});
    if (mountedIngest === task && original.isConnected && response.evidence?.sourceId === fullId) task.saved = response.evidence;
  } catch {
    // Prepared citations can remain available if the saved-results request fails.
  } finally {
    task.savedPending = false;
    if (mountedIngest === task && original.isConnected &&
        (readOnly() || settingsFor(source).tab === 'results')) renderInspector(source, task.info);
  }
}

async function mountIngest() {
  const {source} = ingestView();
  const original = document.querySelector('#original-content');
  const id = locationView.dataset;
  mountedIngest = {source, info:null, text:null, original};
  const task = mountedIngest;
  if (source && locationView.observation) settingsFor(source).tab = 'results';
  void loadSavedResult(task, id);
  renderInspector(source, null);
  if (!source) return;
  try {
    task.info = await getOriginal(id, source);
    const type = displayType(task.info);
    if (type === 'text') {
      if (source.file) task.text = await source.file.text();
      else {
        const response = await fetch(task.info.originalUrl);
        if (!response.ok) throw new Error(t('The pinned original could not be read.'));
        task.text = await response.text();
      }
    } else if (type === 'pdf' && !source.file) {
      const response = await fetch(task.info.originalUrl);
      if (!response.ok || response.headers.get('content-type')?.split(';')[0] !== 'application/pdf') {
        throw new Error(t('The pinned PDF could not be read. Reload the source to retry.'));
      }
      const pdf = await response.blob();
      if (!original.isConnected) return;
      task.tempUrl = URL.createObjectURL(pdf);
      task.info = {...task.info, originalUrl:task.tempUrl};
    }
  } catch (error) {
    task.info = {status:'error', error:error.message};
  }
  if (!original.isConnected) return;
  original.innerHTML = metadataPreview(source, task.info, task.text);
  const media = original.querySelector('audio, video');
  const start = selectedEvidence(source)?.provenance?.startSeconds;
  if (media && Number.isFinite(start)) {
    const seek = () => { media.currentTime = Math.min(start, Number.isFinite(media.duration) ? media.duration : start); };
    if (media.readyState >= 1) seek();
    else media.addEventListener('loadedmetadata', seek, {once:true});
  }
  if (task.info.status === 'available') {
    document.querySelector('#original-download').innerHTML = `<a class="button secondary" href="${html(task.info.originalUrl)}" download="${html(task.info.filename)}">${t('Download original')}</a>`;
    original.querySelector('img, audio, video')?.addEventListener('error', () => {
      original.innerHTML = `<p class="empty">${t('This browser could not open the original. Use Download original.')}</p>`;
    }, {once:true});
  }
  renderInspector(source, task.info);
}

function updateFileSearch() {
  const query = session().ingestSearch.trim().toLowerCase();
  const rows = [...main.querySelectorAll('[data-file-row]')];
  rows.forEach(row => { row.hidden = !row.textContent.toLowerCase().includes(query); });
  const empty = main.querySelector('#file-list-empty');
  empty.hidden = rows.some(row => !row.hidden);
  empty.textContent = query ? t('No files match your search.') : t('No files in this source type.');
}

function isWorkbook(file) {
  return /\.xlsx$/i.test(file.name) ||
    file.type === 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
}

async function describeWorkbook(file, workbook) {
  const expected = workbook?.citation?.sha256;
  if (expected && window.crypto?.subtle && file.size <= 64 * 1024 * 1024) {
    try {
      const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer());
      const actual = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
      if (actual === expected) {
        return workbook.structuredImport?.status === 'available'
          ? t('{name} matches the prepared demo workbook. Its synthetic tables are listed below.', {name: file.name})
          : t('{name} matches the prepared demo workbook. Its definitions are available, but row counts require the prepared local files.', {name: file.name});
      }
    } catch {
      return t('{name} could not be verified. Choose the prepared demo workbook again.', {name: file.name});
    }
  }
  return t('{name} has no verified import for this dataset. Prepare it as a separate structured source before adding it here.', {name: file.name});
}

async function selectFiles(files) {
  if (readOnly() || !files.length) return;
  const state = session();
  const batch = [...files];
  const selectedView = locationView;
  const version = ++state.selectionVersion;
  state.workbookMessages = [];
  const workbooks = batch.filter(isWorkbook);
  const added = batch.filter(file => !isWorkbook(file)).map(file => {
    const mimeType = fileMime(file);
    const bytes = file.type === mimeType ? file : new Blob([file], {type:mimeType});
    return {
      id: 'local-' + state.nextFileId++, title:file.name,
      kind:'Local file · ' + mimeType, mimeType, file, url:URL.createObjectURL(bytes)
    };
  });
  state.files.push(...added);
  if (added.length) state.ingestMode = ingestMode(added[0]);
  state.ingestSearch = '';
  state.newFiles = added.map(file => file.id);
  if (workbooks.length) {
    const messages = await Promise.all(workbooks.map(file => describeWorkbook(file, workbookSource(selectedView.dataset))));
    if (state.selectionVersion !== version) return;
    state.workbookMessages = messages;
    if (added.length) state.workbookMessages.push(t('The other selected files are available in the file library.'));
    if (locationView !== selectedView) return;
    if (locationView.detail === 'structured') render();
    else location.hash = hash('ingest', 'structured');
    return;
  }
  if (locationView.detail) {
    fileFocus = added[0].id;
    location.hash = hash('ingest');
  } else {
    render();
    main.querySelector('.file-added .file-open').focus();
  }
}

function removeLocalFile(source) {
  const state = session();
  const mode = ingestMode(source);
  URL.revokeObjectURL(source.url);
  state.files = state.files.filter(file => file.id !== source.id);
  state.ingestSettings.delete(source.id);
  state.ocrEvidence.delete(source.id);
  state.newFiles = state.newFiles.filter(id => id !== source.id);
  state.ingestMode = mode;
  state.ingestSearch = '';
  fileFocus = state.files.find(file => ingestMode(file) === mode)?.id;
  location.hash = hash('ingest');
}

function chooseIngestSource(source) {
  if (!source) return;
  const mode = ingestMode(source);
  session().ingestMode = mode;
  location.hash = hash('ingest', source.id);
}

// Processing is one server request. The steps it runs are named; the server reports only the final result.
const ingestSteps = {
  form: 'Read the form fields, split the text into passages, link passages to table fields (Mistral), then store the links.',
  media: 'Send the audio to Mistral, split the transcript into passages, link passages to table fields, then store the links.',
  ocr: 'Send the original to Mistral OCR, split the text into passages, link passages to table fields, then store the links.',
  text: 'Read the text, split it into passages, link passages to table fields (Mistral), then store the links.'
};
function ingestProgress(setting, kind) {
  return `<div class="ingest-progress" role="status" data-ingest-progress><span class="pf-spinner" aria-hidden="true"></span>
    <div><strong>${t('Processing · {elapsed}', {elapsed: `<span data-elapsed-since="${setting.startedAt || Date.now()}">0 ${t('s')}</span>`})}</strong>
    <p class="helper">${t('{steps} This usually takes one to two minutes.', {steps: t(ingestSteps[kind])})}</p></div></div>`;
}
setInterval(() => document.querySelectorAll('[data-elapsed-since]').forEach(item => {
  item.textContent = Math.max(0, Math.round((Date.now() - Number(item.dataset.elapsedSince)) / 1000)) + ' ' + t('s');
}), 1000);

function processIngest(action) {
  if (readOnly() || !mountedIngest?.original.isConnected) return;
  const {source, info, text} = mountedIngest;
  if (!source) return;
  const type = displayType(info);
  const setting = settingsFor(source);
  if (action === 'ingest-run' && canReadPinnedText(source, info)) {
    void submitDocumentRead('text');
    return;
  }
  if (action === 'ingest-run' && canTranscribe(source, info) && !setting.running) {
    showDialog(t('Send this audio to Mistral'), `<p class="helper">${t('Mistral receives the original shown below and returns a timestamped transcript.')}</p>
      ${fieldRows([[t('Original'),info.filename], [t('Model'),ocrConnector.transcription.model || 'Mistral transcription'], [t('Dataset'),dataset().name]])}
      <div class="actions account-actions">${btn(t('Send to Mistral'), 'ingest-ocr-confirm', 'primary')}</div>`);
    return;
  }
  if (action === 'ingest-run' && canRunOcr(source, info) && isPageSelection(setting.pages) && !setting.running) {
    showDialog(t('Send this original to Mistral OCR'), `<p class="helper">${t('Mistral receives the original shown below and these options.')}</p>
      ${fieldRows([[t('Original'),info.filename], [t('Model'),ocrConnector.model || 'Mistral OCR'], [t('Dataset'),dataset().name]])}
      <pre class="ingest-output">${html(JSON.stringify(ocrOptions(setting), null, 2))}</pre>
      <div class="actions account-actions">${btn(t('Send to Mistral'), 'ingest-ocr-confirm', 'primary')}</div>`);
    return;
  }
  if (action === 'ingest-run' && type === 'text' && typeof text === 'string') {
    setting.output = {
      title:t('Read from original text'),
      text: setting.response === 'json' ? JSON.stringify({filename:info.filename, text}, null, 2) : text
    };
  } else if (action === 'ingest-prepare' && ['pdf','image','audio','video'].includes(type) && isPageSelection(setting.pages)) {
    const options = ingestMode(source) === 'media'
      ? {language:setting.language, includeTimestamps:setting.timestamps}
      : { ...(type === 'pdf' ? {pages:setting.pages || 'all'} : {}),
          extract:{images:setting.images, header:setting.header, footer:setting.footer, boundingBoxes:setting.boxes, tables:setting.tables} };
    setting.output = {
      title:t('Prepared request · Not sent'),
      text:JSON.stringify(canRunOcr(source, info) ? {filename:info.filename, mimeType:info.mimeType, options:ocrOptions(setting)}
        : {filename:info.filename, mimeType:info.mimeType, responseFormat:setting.response,
        ...(setting.prompt ? {prompt:setting.prompt} : {}), ...options}, null, 2)
    };
  } else return;
  setting.ocrResult = null;
  setting.ocrError = '';
  setting.tab = 'results';
  renderInspector(source, info);
  document.querySelector('[data-ingest-tab="results"]').focus();
}

function base64File(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',')[1]);
    reader.onerror = () => reject(new Error(t('The local file could not be read.')));
    reader.readAsDataURL(file);
  });
}

async function submitDocumentRead(mode) {
  const task = mountedIngest;
  if (!task?.original.isConnected || readOnly()) return;
  const {source, info} = task;
  const state = session(), selectedDataset = locationView.dataset;
  const setting = settingsFor(source);
  if (!setting || setting.running) return;
  const media = mode === 'ocr' && canTranscribe(source, info);
  const isOcr = mode === 'ocr' && !media;
  // Media sends the same pinned-source request; the server picks the transcription connector.
  if (!media && (isOcr ? !canRunOcr(source, info) || !isPageSelection(setting.pages)
    : mode === 'text' ? !canReadPinnedText(source, info) : !canReadForm(source, info))) return;
  const request = {dataset:selectedDataset, mode, ...(isOcr ? {options:ocrOptions(setting)} : {})};
  setting.running = true;
  setting.startedAt = Date.now();
  setting.ocrError = '';
  setting.ocrResult = null;
  setting.output = null;
  renderInspector(source, info);
  try {
    if (source.file) request.file = {name:source.file.name, mimeType:info.mimeType, base64:await base64File(source.file)};
    else request.sourceId = selectedDataset + ':source:' + (source.preparedId || source.id);
    const result = await callLocal('/api/ocr', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(request)});
    if (result.status !== 'completed') throw new Error(t('The processor did not return a completed result.'));
    setting.ocrResult = result;
    setting.seconds = Math.round((Date.now() - setting.startedAt) / 1000);
    physicalViews.delete(request.sourceId);
    window.V2Pipeline?.forget(selectedDataset);
    stepStates.delete(selectedDataset);
    if (result.evidence && (!source.file || state.files.includes(source))) state.ocrEvidence.set(source.id, result.evidence);
    if (!source.file && result.evidenceStatus === 'saved' && locationView.dataset === selectedDataset) {
      try { await refreshKnowledge(selectedDataset); }
      catch { /* Saved results stay visible if the graph refresh is temporarily unavailable. */ }
    }
  } catch (error) {
    setting.ocrError = error.message;
  } finally {
    setting.running = false;
    setting.tab = 'results';
    if (task.original.isConnected) {
      renderInspector(source, info);
      document.querySelector('[data-ingest-tab="results"]').focus();
    }
  }
}

function releaseFileUrls() {
  Object.values(sessions).forEach(state => state.files.forEach(item => URL.revokeObjectURL(item.url)));
  if (mountedIngest?.tempUrl) URL.revokeObjectURL(mountedIngest.tempUrl);
}
