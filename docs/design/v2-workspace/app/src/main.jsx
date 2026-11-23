// Entry: window.V2Pipeline.mount(page, element, ctx) -> unmount. workspace.js owns routing, the shell and the session.
import {createRoot} from 'react-dom/client';
import '@xyflow/react/dist/style.css';
import './pipeline.css';
import Evidence from './Evidence.jsx';
import Concepts from './Concepts.jsx';
import Model, {TableNode} from './Model.jsx';
import Ask from './Ask.jsx';
import Entity360 from './Entity360.jsx';
import {PfNode} from './ui.jsx';
import {allLinks, getCatalog, forget, dataLabel, buildModel} from './lib.js';

const nodeTypes = {pfNode: PfNode, pfTable: TableNode};   // module level: an inline object remounts every node
const pages = {evidence: Evidence, concepts: Concepts, model: Model, ask: Ask, entity: Entity360};

window.V2Pipeline = {
  forget: dataset => forget(dataset + ':'),
  dataLabel,
  // Business label of a model definition id ("Credit card transactions › Card transaction amount"), for the shell pages.
  definitionLabel: (workspace, id) => buildModel(workspace).label(id),
  // sourceId -> {passages, linked}: passages read, and passages with at least one live link.
  async linkCounts(ctx) {
    const counts = new Map();
    for (const link of await allLinks(ctx)) {
      const item = counts.get(link.source_id) || {chunks: new Set(), linkedChunks: new Set()};
      item.chunks.add(link.chunk_id);
      if (link.definition_id && link.status !== 'rejected') item.linkedChunks.add(link.chunk_id);
      counts.set(link.source_id, item);
    }
    return new Map([...counts].map(([id, item]) => [id, {passages: item.chunks.size, linked: item.linkedChunks.size}]));
  },
  mount(page, element, ctx) {
    const Page = pages[page];
    const root = createRoot(element);
    root.render(<Page ctx={{...ctx, nodeTypes}} />);
    return () => root.unmount();
  },
  // Step states for the navigation badges: {model, evidence, concepts} -> 'empty' | 'ready' | 'review' | 'done'.
  async status(ctx) {
    const [links, catalog] = await Promise.all([allLinks(ctx).catch(() => null), getCatalog(ctx).catch(() => null)]);
    const result = {};
    if (links) result.evidence = !links.length ? 'empty' : links.some(link => link.status === 'proposed' && link.definition_id) ? 'review' : 'done';
    if (catalog) result.concepts = !catalog.concepts?.length ? (links?.length ? 'ready' : 'empty')
      : catalog.concepts.some(item => item.status === 'proposed' || item.status === 'candidate') ? 'review' : 'done';
    result.sourcesProcessed = links ? new Set(links.map(link => link.source_id)).size : null;
    return result;
  }
};
