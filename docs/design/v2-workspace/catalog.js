// The catalog follows the Semantic Hub /v2 workspace-card layout.
(() => {
  const text = (value) => String(value).replace(/[&<>"']/g, ch =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch]);
  const shape = {
    grid: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    database: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/>'
  };
  const glyph = (id) => `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true">${shape[id]}</svg>`;
  const typeNames = {
    workbook: 'Workbook', 'structured-model': 'Structured model', 'mapping-reference': 'Mapping reference',
    'adverse-media': 'Adverse-media report', 'synthetic-contract': 'Synthetic contract',
    audio: 'Audio recording', pdf: 'PDF document'
  };
  const typeName = kind => t(typeNames[kind] || kind.replaceAll('-', ' '));

  function card(dataset) {
    const { catalog } = dataset;
    const href = '#' + encodeURIComponent(dataset.id) + '/ingest';
    return `<article class="workspace-card" data-dataset="${text(dataset.id)}">
      <div class="card-top"><span class="card-icon">${glyph('database')}</span><span class="catalog-tag">${t('Candidate model')}</span></div>
      <div><h2>${text(dataset.title)}</h2><p>${text(dataset.description)}</p></div>
      <div class="card-model"><small>${t('Semantic model')}</small><strong>${text(catalog.namespace)}</strong></div>
      <div class="card-meta"><span>${t('{n} tables', {n: `<b>${catalog.tableCount}</b>`})}</span><span>${t('{n} fields', {n: `<b>${catalog.fieldCount}</b>`})}</span><span>${t('{n} joins', {n: `<b>${catalog.relationshipCount}</b>`})}</span></div>
      <div class="card-footer"><button type="button" class="button secondary source-details" aria-expanded="false" aria-controls="catalog-info-${text(dataset.id)}">${t('Details')}</button><a class="button primary open-workspace" href="${href}" aria-label="${text(t('Open {title} workspace', {title: dataset.title}))}">${t('Open workspace →')}</a></div>
      <div class="source-inventory" id="catalog-info-${text(dataset.id)}" hidden><strong>${t('Source contents')}</strong><ul>${catalog.highlights.map(item => `<li>${text(item)}</li>`).join('')}</ul></div>
    </article>`;
  }

  window.renderDatasetCatalog = () => {
    const { datasets } = window.V2_WORKSPACE_DATA;
    const rail = document.getElementById('rail');
    const top = document.getElementById('top');
    document.querySelector('.shell').classList.add('catalog-open');
    document.getElementById('workspace-main').classList.add('dataset-page');
    rail.hidden = top.hidden = false;
    rail.innerHTML = `<a class="brand" href="#datasets" aria-label="${t('Datasets home')}"><img class="brand-logo" src="cloudera-logo.png" alt="Cloudera"></a>
      <nav aria-label="${t('Workspace navigation')}"><a class="nav-item active" href="#datasets" aria-current="page"><span class="nav-icon">${glyph('grid')}</span>${t('Datasets')}</a></nav>`;
    const hosted = document.querySelector('meta[name="workspace-runtime"]')?.content === 'local';
    top.innerHTML = `<span>${t('Workspace')}</span><span aria-hidden="true">/</span><strong>${t('Datasets')}</strong><span class="catalog-preview">${t(hosted ? 'Local workspace' : 'Offline preview')}</span>`;
    const kinds = [...new Set(datasets.flatMap(d => d.sources.map(s => s.kind)))].sort();
    return `<section id="dataset-catalog" aria-labelledby="catalog-title">
      <div class="heading"><h1 id="catalog-title">${t('Datasets')}</h1><p>${t('Choose a data source to open its ingestion workspace.')}</p></div>
      <div class="hub-summary"><div class="summary-tile"><strong>${datasets.length}</strong><span>${t('Data sources')}</span></div><div class="summary-tile"><strong>${datasets.length}</strong><span>${t('Candidate models')}</span></div><div class="summary-tile"><strong>${datasets.reduce((sum, d) => sum + d.catalog.tableCount, 0)}</strong><span>${t('Structured tables')}</span></div></div>
      <div class="catalog-toolbar" role="search" aria-label="${t('Filter datasets')}">
        <label>${t('Find a source or model')}<input type="search" id="dataset-search" placeholder="${t('Search by name or model')}"></label>
        <label>${t('Source type')}<select id="dataset-type"><option value="">${t('All source types')}</option>${kinds.map(k => `<option value="${text(k)}">${text(typeName(k))}</option>`).join('')}</select></label>
      </div>
      <div class="section-heading"><h2>${t('Available data sources')}</h2><span id="dataset-count" role="status">${counted(datasets.length, 'dataset')}</span></div>
      <div class="workspace-cards">${datasets.map(card).join('')}</div>
      <p id="dataset-empty" class="empty" hidden>${t('No matching datasets.')} <button type="button" class="text-link" id="clear-catalog">${t('Clear filters')}</button></p>
    </section>`;
  };

  function filterCatalog() {
    const catalog = document.getElementById('dataset-catalog');
    if (!catalog) return;
    const term = catalog.querySelector('#dataset-search').value.trim().toLowerCase();
    const kind = catalog.querySelector('#dataset-type').value;
    const records = window.V2_WORKSPACE_DATA.datasets;
    let count = 0;
    for (const node of catalog.querySelectorAll('[data-dataset]')) {
      const data = records.find(d => d.id === node.dataset.dataset);
      const names = [data.title, data.description, data.catalog.namespace, ...data.sources.map(s => s.title)];
      node.hidden = !(names.some(name => name.toLowerCase().includes(term)) && (!kind || data.sources.some(s => s.kind === kind)));
      if (!node.hidden) count++;
    }
    catalog.querySelector('#dataset-count').textContent = counted(count, 'dataset');
    catalog.querySelector('#dataset-empty').hidden = count > 0;
  }

  document.addEventListener('input', event => {
    if (event.target.id === 'dataset-search') filterCatalog();
  });
  document.addEventListener('change', event => {
    if (event.target.id === 'dataset-type') filterCatalog();
  });
  document.addEventListener('click', event => {
    const details = event.target.closest('.source-details');
    if (details) {
      const panel = document.getElementById(details.getAttribute('aria-controls'));
      panel.hidden = !panel.hidden;
      details.setAttribute('aria-expanded', String(!panel.hidden));
    }
    if (event.target.closest('#clear-catalog')) {
      const search = document.getElementById('dataset-search');
      search.value = '';
      document.getElementById('dataset-type').value = '';
      filterCatalog();
      search.focus();
    }
  });
})();
