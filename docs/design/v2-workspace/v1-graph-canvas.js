/* Knowledge graph adapter for the Helios canvas. Read-only; all text reaches the DOM through textContent inside the vendor canvas.
   build() and path() are pure, so a caller can pass a filtered graph (for a neighbourhood view) and node can check them. */
(() => {
  'use strict';
  const STEP_X = 300, STEP_Y = 100;
  const KIND = {Company: 'party', Person: 'person', RiskSignal: 'risk'};
  const NAME = {kyc: 'KYC memo', sar: 'SAR', adverse: 'Adverse media', doc: 'Document'};
  const LEGEND = [['kyc', 'KYC memo'], ['adverse', 'Adverse media'], ['doc', 'Document'], ['party', 'Company'], ['person', 'Person'], ['sar', 'SAR'], ['risk', 'Risk signal'], ['record', 'Customer record'], ['fact', 'From SQL'], ['mapping', 'Business mapping'], ['semantic', 'Business definition']];
  const GLYPH = {kyc: 'K', adverse: 'A', sar: 'S', doc: 'D', party: 'C', person: 'P', risk: '!', record: 'R', fact: '\u03a3', mapping: 'M', semantic: 'L', view: 'V'};
  const TYPE = {Company: 'Company', Person: 'Person', RiskSignal: 'Risk signal'};
  const docKind = name => /^kyc\d+_/i.test(name) ? 'kyc' : /^sar\d+_/i.test(name) ? 'sar' : /^adverse\d+_/i.test(name) ? 'adverse' : 'doc';
  const byLabel = (a, b) => String(a.label).localeCompare(String(b.label));

  /* Columns: documents | companies | people | SAR documents, risk signals and other entities | Enterprise data (added below). */
  function layout(nodes, edges) {
    const pos = {}, rows = new Map(), rank = {kyc: 0, adverse: 1, doc: 2};
    const place = (list, x) => list.forEach((n, i) => { pos[n.id] = {x, y: 20 + i * STEP_Y}; rows.set(n.id, i); });
    const docs = nodes.filter(n => n.raw.type === 'Document');
    place(docs.filter(n => n.kind !== 'sar').sort((a, b) => rank[a.kind] - rank[b.kind] || byLabel(a, b)), 0);
    const sql = n => n.kind === 'record' || n.kind === 'fact';
    const others = nodes.filter(n => n.raw.type !== 'Document' && !['party', 'person', 'semantic', 'view', 'mapping'].includes(n.kind) && !sql(n));
    place([...docs.filter(n => n.kind === 'sar').sort(byLabel), ...others.sort(byLabel)], 3 * STEP_X);
    const bary = n => {
      const near = edges.flatMap(e => e.source === n.id ? [e.target] : e.target === n.id ? [e.source] : []).map(id => rows.get(id)).filter(Number.isFinite);
      return near.length ? near.reduce((a, b) => a + b, 0) / near.length : 1e6;
    };
    // Companies, then people (whose links to companies now count), each ordered by the mean row of their neighbours.
    [['party', STEP_X], ['person', 2 * STEP_X]].forEach(([kind, x]) => {
      const middle = nodes.filter(n => n.kind === kind).map(n => ({n, b: bary(n)})).sort((a, b) => a.b - b.b || byLabel(a.n, b.n));
      // People and the right-hand column start below the last company row, so a company-to-record line never crosses their cards.
      const below = kind === 'person' ? Math.max(-1e9, ...nodes.filter(c => c.kind === 'party').map(c => pos[c.id].y)) : 0;
      if (kind === 'person') {
        const col3 = nodes.filter(c => pos[c.id] && pos[c.id].x === 3 * STEP_X), top = Math.min(...col3.map(c => pos[c.id].y));
        if (col3.length && top < below + STEP_Y) for (const c of col3) pos[c.id].y += below + STEP_Y - top;
      }
      let last = kind === 'person' ? Math.max(-STEP_Y + 20, below) : -STEP_Y + 20;
      for (const {n, b} of middle) { const y = Math.max(last + STEP_Y, b >= 1e6 ? 0 : 20 + b * STEP_Y); pos[n.id] = {x, y}; rows.set(n.id, (y - 20) / STEP_Y); last = y; }
    });
    // SQL records can reference documents, companies or people. Place every record explicitly.
    let free = 20;
    const records = nodes.filter(n => n.kind === 'record');
    const referenceRow = n => Math.min(...edges.filter(e => e.target === n.id).map(e => pos[e.source]?.y ?? 1e6));
    for (const rec of records.sort((a, b) => referenceRow(a) - referenceRow(b) || byLabel(a, b))) {
      let y = Math.max(free, Math.min(referenceRow(rec), 1e5));
      for (const n of [rec, ...nodes.filter(f => f.kind === 'fact' && f.raw.company === rec.raw.company && rec.raw.company)]) {
        pos[n.id] = {x: 4 * STEP_X, y}; y += STEP_Y;
      }
      free = y;
    }
    place(nodes.filter(n => n.kind === 'mapping'), 5 * STEP_X);
    // Sixth column, Semantic layer: one view box per Explore with its field boxes beneath, in the order of the facts they define.
    const row = n => Math.min(...edges.filter(e => e.target === n.id).map(e => pos[e.source]?.y ?? 1e6));
    let y = 20;
    for (const v of nodes.filter(n => n.kind === 'view').sort((a, b) => row(a) - row(b))) {
      pos[v.id] = {x: 6 * STEP_X, y}; y += STEP_Y;
      for (const f of nodes.filter(n => n.kind === 'semantic' && n.raw.explore === v.raw.explore).sort((a, b) => row(a) - row(b))) { pos[f.id] = {x: 6 * STEP_X, y}; y += STEP_Y; }
    }
    const columns = [...new Set(Object.values(pos).map(p => p.x))].sort((a,b) => a-b);
    for (const p of Object.values(pos)) p.x = columns.indexOf(p.x) * STEP_X;
    return pos;
  }

  const ACR = /^(sgd|usd|pd|id|kyc|aml|rm|uen|fx|sar)$/i;
  const prettify = label => String(label || '').split(/\s+/).map(w => ACR.test(w) ? w.toUpperCase() : w).join(' ');
  const semanticTitle = d => /^pd\b/i.test(d.label || '') && d.description
    ? d.description.split(/[.;]/)[0] : prettify(d.label || d.field);
  const EXACT = 'exact', SIMILAR = 'similar \u2014 not equal';
  /* semantics: the GET /api/semantics body. Only status "ok" adds boxes. A fact with a governed field gets a Semantic layer box
     (exact: solid line, similar: dashed amber line) under one view box per Explore; a fact with none gets a tag instead. */
  function addSemantics(nodes, edges, sem, definitionKeys = []) {
    if (!sem || sem.status !== 'ok') return;
    const edge = (id, source, target, kind, label) => edges.push({id, source, target, kind, label, raw: {id, source, target, kind, status: 'accepted', chunk_ids: []}});
    function add(key, f = null) {
      const d = sem.definitions?.[key]; if (!d) return;
      if (f) f.raw.sem = d;
      if (!['exact', 'similar'].includes(d.match) || !d.explore || !d.field) { if (f) f.tags.push('No governed definition'); return; }
      const ex = sem.explores?.[d.explore] || {}, fid = 'sem:' + d.field, vid = 'view:' + d.explore, exact = d.match === 'exact';
      if (!nodes.some(n => n.id === fid)) {
        nodes.push({id: fid, kind: 'semantic', label: semanticTitle(d), data_type: 'Business definition', description: d.description || '', tags: [exact ? EXACT : SIMILAR],
          raw: {id: fid, type: 'SemanticField', keys: [], explore: d.explore, field: d.field, label: prettify(d.label), description: d.description || '', note: d.note || '', match: d.match, table: ex.base_view && (ex.fields?.[d.field]?.view || d.field.split('.')[0]) === ex.base_view ? ex.table || '' : '', exploreLabel: ex.label || d.explore}});
      }
      const keys = nodes.find(n => n.id === fid).raw.keys;
      if (!keys.includes(key)) keys.push(key);
      if (!nodes.some(n => n.id === vid)) {
        nodes.push({id: vid, kind: 'view', label: prettify(ex.label || d.explore), data_type: 'Semantic model', description: ex.description || '', tags: ['Semantic model ' + (sem.model || 'bank_trino')],
          raw: {id: vid, type: 'SemanticView', explore: d.explore, label: prettify(ex.label || d.explore), description: ex.description || '', table: ex.table || ''}});
      }
      if (!edges.some(e => e.id === 'in:' + vid + ':' + fid)) edge('in:' + vid + ':' + fid, fid, vid, 'in_view', 'in view');
      if (f) edge('def:' + f.id, f.id, fid, 'defined_by' + (exact ? '' : ' similar'), exact ? 'exact' : 'similar');
    }
    // Reviewed mappings describe available business meaning, never customer values or source lineage.
    for (const r of nodes.filter(n => n.kind === 'record' && n.raw.source === 'c360_banking')) for (const [key, d] of Object.entries(sem.definitions || {})) {
      if (!['exact', 'similar'].includes(d.match)) continue;
      if (nodes.some(n => n.kind === 'fact' && n.raw.company === r.raw.company && n.raw.key === key)) continue;
      const id = 'mapping:business-schema:' + key;
      const anchor = {id, kind: 'mapping', label: key.replace(/_/g, ' '), data_type: 'Attribute / metric mapping', description: 'Shared business schema · no customer value', tags: ['Reviewed mapping'], raw: {id, type: 'SemanticMapping', key}};
      if (!nodes.some(n => n.id === id)) { nodes.push(anchor); add(key, anchor); }
      edge('maps:' + r.id + ':' + key, r.id, id, 'maps_attribute', 'schema mapping · metadata only');
    }
    for (const f of nodes.filter(n => n.kind === 'fact')) add(f.raw.key, f);
    // A question can cite meaning without citing a SQL fact. Such a field has only its Explore link.
    for (const key of definitionKeys) add(key);
  }

  /* graph: the GET /api/graph body. Chunk nodes are not drawn: a document box stands for its chunks, and
     "mentions" edges from chunks are folded into one edge per document and entity. Rejected edges are dropped. */
  function build(graph, enterprise = {}, semantics = null, definitionKeys = []) {
    const chunks = new Map(), docs = new Map(), nodes = [], edges = [];
    for (const n of graph.nodes || []) if (n.type === 'Chunk') {
      chunks.set(n.chunk_id, n);
      const doc = docs.get(n.document_id) || {id: 'doc:' + n.document_id, name: n.filename, type: 'Document', chunks: []};
      doc.chunks.push(n); docs.set(n.document_id, doc);
    }
    for (const d of docs.values()) {
      const kind = docKind(d.name);
      nodes.push({id: d.id, kind, label: d.name, data_type: NAME[kind], description: d.chunks.length + (d.chunks.length === 1 ? ' passage' : ' passages'), tags: [NAME[kind]], raw: {...d, kind}});
    }
    const ids = new Set(nodes.map(n => n.id));
    for (const n of graph.nodes || []) if (n.type !== 'Chunk') {
      ids.add(n.id);
      nodes.push({id: n.id, kind: KIND[n.type] || 'other', label: n.name, data_type: n.type, description: '', tags: [(TYPE[n.type] || n.type).toLowerCase()], raw: n});
    }
    const mention = new Map();
    for (const e of graph.edges || []) {
      if (e.status === 'rejected') continue;
      if (e.kind === 'mentions') {
        const chunk = chunks.get(e.chunk_id);
        if (!chunk || !ids.has(e.target)) continue;
        const id = 'mention:' + chunk.document_id + '>' + e.target, old = mention.get(id);
        if (old) old.raw.chunk_ids.push(e.chunk_id);
        else mention.set(id, {id, source: 'doc:' + chunk.document_id, target: e.target, kind: 'mentions', label: 'mentions',
          raw: {id, source: 'doc:' + chunk.document_id, target: e.target, kind: 'mentions', status: 'accepted', chunk_ids: [e.chunk_id]}});
      } else if (ids.has(e.source) && ids.has(e.target)) {
        // The vendor canvas writes the kind into the edge's class list, so " proposed" becomes a second class (dashed in CSS).
        edges.push({id: e.id, source: e.source, target: e.target, kind: e.kind + (e.status === 'proposed' ? ' proposed' : ''),
          label: e.kind.replace(/_/g, ' '), raw: {...e, chunk_ids: (e.evidence || []).map(x => x.chunk_id)}});
      }
    }
    edges.push(...mention.values());
    // enterprise: {companyId: {record: true, facts?: [{key, title, value}]}}. Boxes appear only for companies in the graph.
    for (const [cid, ent] of Object.entries(enterprise || {})) {
      if (!ent || !ent.record || !ids.has(cid)) continue;
      const rid = 'rec:' + cid;
      nodes.push({id: rid, kind: 'record', label: nodes.find(c => c.id === cid).label, data_type: 'Enterprise record', description: (ent.source || 'c360_banking') + ' record', tags: ['From SQL'], raw: {id: rid, type: 'EnterpriseRecord', company: cid, customer_id: ent.customer_id, source: ent.source || 'c360_banking'}});
      edges.push({id: 'matched:' + cid, source: cid, target: rid, kind: 'matched_record', label: 'matched record', raw: {id: 'matched:' + cid, source: cid, target: rid, kind: 'matched_record', status: 'accepted', chunk_ids: []}});
      for (const f of ent.facts || []) {
        const fid = 'fact:' + cid + ':' + f.key;
        nodes.push({id: fid, kind: 'fact', label: f.title, data_type: 'SQL fact', description: f.value, tags: ['From SQL'], raw: {id: fid, type: 'EnterpriseFact', company: cid, customer_id: ent.customer_id, key: f.key, title: f.title, value: f.value}});
        edges.push({id: 'has:' + fid, source: rid, target: fid, kind: 'has_fact', label: 'has fact', raw: {id: 'has:' + fid, source: rid, target: fid, kind: 'has_fact', status: 'accepted', chunk_ids: []}});
      }
    }
    for (const ref of graph.record_links || []) {
      const source = 'doc:' + ref.document_id;
      if (!ids.has(source) || !['supported_reference', 'name_reference_review'].includes(ref.status) || !chunks.has(ref.chunk_id)) continue;
      const id = 'sqlref:' + ref.id, provisional = ref.status === 'name_reference_review';
      nodes.push({id, kind: 'record', label: ref.label || ref.candidate_id, data_type: 'SQL record reference',
        description: 'c360_mock_source · ' + ref.candidate_id, tags: ['From SQL', provisional ? 'Possible reference · review' : 'Record reference'],
        raw: {...ref, id, link_id: ref.id, type: 'SQLRecordReference', source: 'c360_mock_source'}});
      edges.push({id: 'references:' + ref.id, source, target: id, kind: 'references_sql_record' + (provisional ? ' proposed' : ''), label: provisional ? 'possible SQL reference · review' : 'references SQL record',
        raw: {...ref, source, target: id, kind: 'references_sql_record', chunk_ids: [ref.chunk_id]}});
    }
    addSemantics(nodes, edges, semantics, definitionKeys);
    for (const n of nodes) if (n.raw.type !== 'Document' && n.kind !== 'record' && n.kind !== 'fact' && n.kind !== 'semantic' && n.kind !== 'view' && n.kind !== 'mapping') {
      const k = new Set(edges.filter(e => e.kind === 'mentions' && e.target === n.id).map(e => e.source)).size;
      n.description = (TYPE[n.raw.type] || n.raw.type) + ' \u00b7 ' + k + (k === 1 ? ' document' : ' documents');
    }
    const proposed = new Set(edges.filter(e => e.kind.endsWith(' proposed')).flatMap(e => [e.source, e.target]));
    for (const n of nodes) if (proposed.has(n.id)) n.tags.push('proposed');
    return {nodes, edges, positions: layout(nodes, edges), chunks};
  }

  /* Answer paths include documents, linked entities, cited SQL facts and cited definitions.
     Definition-only citations never add a company record or a SQL fact edge. */
  function path(built, evidence, facts = [], definitions = []) {
    const cited = new Set(evidence.map(e => e.id)), nodes = new Set(), edges = new Set();
    for (const e of evidence) if (built.nodes.some(n => n.id === 'doc:' + e.document_id)) nodes.add('doc:' + e.document_id);
    for (const e of built.edges) if (e.kind === 'mentions' && e.raw.chunk_ids.some(c => cited.has(c))) { edges.add(e.id); nodes.add(e.source); nodes.add(e.target); }
    for (const f of facts) for (const id of [f.entity_id, 'rec:' + f.entity_id, `fact:${f.entity_id}:${f.key}`]) if (built.nodes.some(n => n.id === id)) nodes.add(id);
    const keys = new Set(definitions.map(d => d.key));
    for (const n of built.nodes) if (n.kind === 'semantic' && n.raw.keys?.some(k => keys.has(k))) nodes.add(n.id);
    for (const e of built.edges) if (e.kind === 'in_view' && nodes.has(e.source)) nodes.add(e.target);
    for (const e of built.edges) if (e.kind !== 'mentions' && nodes.has(e.source) && nodes.has(e.target)) edges.add(e.id);
    return {nodes: [...nodes], edges: [...edges]};
  }

  /* One-hop view of a company from the GET /api/graph body: its direct links, plus risk signals reached through a linked person,
     the documents that mention any of those, and the links between them. Optional evidenceIds restricts source chunks to profile evidence.
     Rejected edges are ignored. Returns a graph for build(). */
  function neighbourhood(graph, companyId, evidenceIds = null) {
    const type = new Map((graph.nodes || []).map(n => [n.id, n.type]));
    if (!['Company', 'Person'].includes(type.get(companyId))) return {nodes: [], edges: []};
    const live = (graph.edges || []).filter(e => e.status !== 'rejected'), rel = live.filter(e => e.kind !== 'mentions');
    const other = (e, id) => e.source === id ? e.target : e.target === id ? e.source : null;
    const keep = new Set([companyId]);
    for (const e of rel) { const o = other(e, companyId); if (o) keep.add(o); }
    for (const p of [...keep].filter(id => type.get(id) === 'Person'))
      for (const e of rel) { const o = other(e, p); if (o && type.get(o) === 'RiskSignal') keep.add(o); }
    // Shared risk labels cannot pull another company's documents into this company's view.
    const supporting = new Set(rel.filter(e => keep.has(e.source) && keep.has(e.target)).flatMap(e => (e.evidence || []).map(item => item.chunk_id)));
    const mentions = live.filter(e => e.kind === 'mentions' && keep.has(e.target) && (evidenceIds ? evidenceIds.has(e.chunk_id) : type.get(e.target) !== 'RiskSignal' || supporting.has(e.chunk_id))), chunkIds = new Set(mentions.map(e => e.chunk_id));
    return {nodes: (graph.nodes || []).filter(n => n.type === 'Chunk' ? chunkIds.has(n.chunk_id) : keep.has(n.id)),
      edges: [...rel.filter(e => keep.has(e.source) && keep.has(e.target)), ...mentions],
      record_links: (graph.record_links || []).filter(r => chunkIds.has(r.chunk_id))};
  }

  /* The vendor canvas draws plain SVG cards (title, type line, detail line) and bare edge text. This pass, run after every vendor render,
     adds what the Semantic Hub style needs: a coloured type chip, pill tags and edge-label pills. It only creates SVG elements and sets textContent. */
  const NS = 'http://www.w3.org/2000/svg';
  const mk = (tag, attrs, text) => { const e = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); if (text !== undefined) e.textContent = text; return e; };
  const clip = (t, n) => { const s = String(t); return s.length > n ? s.slice(0, n - 1) + '\u2026' : s; };
  function decorate(svg, meta) {
    for (const g of svg.querySelectorAll('.canvas-node:not([data-gc])')) {
      g.setAttribute('data-gc', '1');
      const m = meta.get(g.getAttribute('data-node-id')); if (!m) continue;
      g.classList.toggle('gc-stale', m.raw?.status === 'stale');
      const [title, , detail] = g.querySelectorAll(':scope > text');
      if (title) {
        title.setAttribute('x', 44); title.setAttribute('y', 24); title.textContent = m.label;
        // Measure the rendered font instead of assuming each character has equal width.
        const full = String(m.label); let end = full.length;
        while (title.getComputedTextLength() > 174 && end > 0) title.textContent = full.slice(0, --end) + '…';
        const clipId = 'gc-title-' + Math.random().toString(36).slice(2);
        const clipping = mk('clipPath', {id: clipId}); clipping.append(mk('rect', {x: 44, y: 8, width: 174, height: 22}));
        g.append(clipping, mk('title', {}, full)); title.setAttribute('clip-path', `url(#${clipId})`);
      }
      if (detail) { detail.setAttribute('x', 44); detail.setAttribute('y', 40); detail.textContent = clip(detail.textContent, 30); }
      const items = [mk('rect', {x: 12, y: 11, width: 24, height: 24, rx: 7, class: 'gc-chip'}), mk('text', {x: 24, y: 28, 'text-anchor': 'middle', class: 'gc-glyph'}, GLYPH[m.kind] || '?')];
      let cursor = 12;
      for (const tag of (m.tags || []).slice(0, 3)) {
        const word = clip(window.t(tag), 24), w = word.length * 5.6 + 14, sql = tag === 'From SQL';
        items.push(mk('rect', {x: cursor, y: 52, width: w, height: 16, rx: 8, class: 'gc-tag' + (sql ? ' sql' : tag === 'proposed' ? ' proposed' : tag === SIMILAR || tag === 'No governed definition' ? ' warn' : '')}),
          mk('text', {x: cursor + w / 2, y: 63.3, 'text-anchor': 'middle', class: 'gc-tag-text'}, word));
        cursor += w + 6;
      }
      g.append(...items);
    }
    for (const t of svg.querySelectorAll('.canvas-edge-label:not([data-gc])')) {
      t.setAttribute('data-gc', '1');
      const x = +t.getAttribute('x'), y = +t.getAttribute('y') - 15, w = t.textContent.length * 5.4 + 16;
      t.setAttribute('y', y + 4.5);
      t.before(mk('rect', {x: x - w / 2, y: y - 9, width: w, height: 18, rx: 9, class: 'gc-edge-pill'}));
    }
  }

  function mount(root, {onSelect, label = t('Knowledge graph'), statusNote = null, searchPlaceholder = null} = {}) {
    let handle = null, signature = null, hl = {nodes: [], edges: []}, watcher = null, stageObserver = null, stageFitFrame = 0;
    const meta = new Map();
    const size = () => {
      root.style.height = (root.id === 'graph-canvas' ? 660 : 520) + 'px';
    };
    // Keep Fit, zoom and pan on the same native camera state.
    const fit = () => [...root.querySelectorAll('.canvas-toolbar button')].find(b => b.textContent === t('Fit'))?.click();
    let resizeFrame = 0;
    const resize = () => {
      size(); cancelAnimationFrame(resizeFrame);
      resizeFrame = requestAnimationFrame(() => { if (handle) fit(); });
    };
    let observedWidth = 0;
    const observer = new ResizeObserver(entries => {
      const width = entries[0]?.contentRect.width || 0;
      if (!width || width === observedWidth) return;
      observedWidth = width; resize();
    });
    observer.observe(root);
    const positions = {};
    function chrome() {
      // Keep navigation and status outside the drawing area so they never cover evidence cards.
      const stage = root.querySelector('.canvas-stage'), navigation = root.querySelector('.canvas-toolbar');
      navigation.setAttribute('aria-label', t('Graph navigation'));
      const minimap = stage.querySelector(':scope > svg:nth-of-type(2)'), zoom = stage.querySelector('.canvas-zoom'), status = stage.querySelector('.canvas-status');
      if (zoom) navigation.append(zoom);
      if (minimap) minimap.style.display = 'none';
      if (status) root.append(status);
      root.querySelector('.canvas-legend').replaceChildren(...LEGEND.filter(([k]) => [...meta.values()].some(n => n.kind === k)).map(([k, t]) => {
        const s = document.createElement('span'); s.className = 'graph-key';
        const i = document.createElement('i'); i.className = 'k-' + k; s.append(i, window.t(t)); return s;
      }));
      for (const b of root.querySelectorAll('.canvas-toolbar button')) { if (b.textContent === t('View options')) b.hidden = true; }  // nothing to configure in a read-only view
      root.querySelector('input[type=search]').placeholder = searchPlaceholder || ([...meta.keys()].some(id => id.startsWith('model:')) ? t('Find semantic view or field') : t('Find document, company, mapping or definition'));
      const surface = root.querySelector('.canvas-stage>svg');
      surface.setAttribute('aria-label', label);
      // Fit through native controls after the stage changes size.
      stageObserver?.disconnect(); cancelAnimationFrame(stageFitFrame);
      stageObserver = new ResizeObserver(() => {
        cancelAnimationFrame(stageFitFrame);
        stageFitFrame = requestAnimationFrame(() => { if (handle) fit(); });
      });
      stageObserver.observe(root.querySelector('.canvas-stage'));
      watcher?.disconnect(); watcher = new MutationObserver(() => decorate(surface, meta)); watcher.observe(surface, {childList: true, subtree: true});
    }
    return {
      set(built) {
        meta.clear(); for (const n of built.nodes) meta.set(n.id, n);
        hl = {nodes: [], edges: []};  // new graph data: drop the previous answer's highlight
        const next = built.nodes.map(n => n.id).sort().join('|');
        for (const id of Object.keys(positions)) if (!(id in built.positions)) delete positions[id];
        Object.assign(positions, built.positions);
        // Reserve a stable drawing surface while data and definitions load.
        size(); window.addEventListener('resize', resize);
        if (next !== signature) {
          handle?.destroy(); signature = next;
          handle = window.HeliosCanvas.mount(root, {readOnly: true, nodePositions: positions, statusNote: statusNote || t(built.nodes.some(n => n.id.startsWith('model:')) ? 'Semantic model metadata only. Reviewed mappings describe business meaning; no customer values.' : 'Links proposed by the graph agent need review. Customer records show their SQL source.'), onSelect: o => onSelect?.(o)});
          chrome();
        }
        handle.update({nodes: built.nodes, edges: built.edges, scope: 'knowledge'});
        handle.highlight(hl.nodes, hl.edges);
        decorate(root.querySelector('.canvas-stage>svg'), meta);
        this.fit();
      },
      // Written against helios-canvas copied 8 Nov 2026 (toolbar button labelled "Fit"); recheck here after a vendor update.
      // The vendor canvas stops refitting once a box is selected, so click its own Fit button to show the whole graph.
      fit,
      highlight(next) { hl = next || {nodes: [], edges: []}; handle?.highlight(hl.nodes, hl.edges); this.fit(); },
      destroy() { window.removeEventListener('resize', resize); observer.disconnect(); stageObserver?.disconnect(); cancelAnimationFrame(stageFitFrame); cancelAnimationFrame(resizeFrame); watcher?.disconnect(); handle?.destroy(); handle = null; signature = null; },
    };
  }

  function modelGraph(sem, explore, view = '', query = '', allFields = false) {
    const ex = sem?.status === 'ok' && sem.explores?.[explore];
    const nodes = [], edges = [], positions = {};
    const add = (id, label, kind, raw, x, y, tags = []) => {
      nodes.push({id, label, kind, raw, tags, data_type: kind === 'semantic' ? raw.fieldKind || raw.type : raw.modelRoot ? 'Semantic model' : 'Semantic view', description: raw.description || ''}); positions[id] = {x, y};
    };
    if (!ex) return {nodes, edges, positions, chunks: new Map()};
    add('model:' + explore, ex.label || explore, 'view', {type: 'SemanticView', modelRoot: true, explore, label: ex.label, description: ex.description, table: ex.table}, 0, 20);
    const fields = Object.values(ex.fields || {}), views = [...new Set([...(ex.base_view ? [ex.base_view] : []), ...fields.map(f => (f.view || f.name.split('.')[0])), ...(ex.joins || []).map(j => j.name).filter(Boolean)])];
    const mapped = new Map(Object.values(sem.definitions || {}).filter(d => ['exact', 'similar'].includes(d.match)).map(d => [d.field, d]));
    for (const [i, v] of views.entries()) {
      const id = 'table:' + v;
      add(id, v, 'view', {type: 'SemanticView', explore, label: v, table: v === ex.base_view ? ex.table : '', description: 'View from live model metadata'}, 250, 20 + i * 92);
      edges.push({id: 'contains:' + v, source: 'model:' + explore, target: id, kind: 'in_view', label: 'contains view', raw: {chunk_ids: []}});
    }
    let row = 0;
    for (const f of fields.filter(f => (!view || (f.view || f.name.split('.')[0]) === view) && (allFields || query || mapped.has(f.name)) && (!query || (f.name + ' ' + f.label + ' ' + f.description).toLowerCase().includes(query.toLowerCase())))) {
      const id = 'field:' + f.name;
      add(id, f.label || f.name, 'semantic', {...f, fieldKind: f.type, type: 'SemanticField', field: f.name, explore, table: (f.view || f.name.split('.')[0]) === ex.base_view ? ex.table : '', match: mapped.get(f.name)?.match || 'unmapped', note: mapped.get(f.name)?.note || ''}, 500, 20 + row++ * 92, mapped.has(f.name) ? ['Reviewed mapping'] : []);
      edges.push({id: 'field-in:' + f.name, source: id, target: 'table:' + (f.view || f.name.split('.')[0]), kind: 'in_view', label: f.type, raw: {chunk_ids: []}});
    }
    for (const [i, j] of (ex.joins || []).entries()) {
      const source = 'table:' + ex.base_view, target = 'table:' + j.name;
      if (nodes.some(n => n.id === source) && nodes.some(n => n.id === target)) edges.push({id: 'join:' + i, source, target, kind: 'model_join', label: j.relationship || j.type || 'join', raw: {...j, chunk_ids: []}});
    }
    return {nodes, edges, positions, chunks: new Map()};
  }
  const api = {modelGraph, build, path, layout, docKind, neighbourhood, mount, prettify};
  if (typeof module !== 'undefined') module.exports = api; else window.GraphCanvas = api;
})();
