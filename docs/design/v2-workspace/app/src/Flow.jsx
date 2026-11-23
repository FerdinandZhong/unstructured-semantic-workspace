// One React Flow canvas with dagre layout. Data owns the graph; React Flow only projects it (drag moves stay local).
import {useEffect, useMemo, useRef} from 'react';
import {ReactFlow, ReactFlowProvider, Background, Controls, MarkerType, BaseEdge, useNodesState, useEdgesState, useReactFlow} from '@xyflow/react';
import dagre from '@dagrejs/dagre';
import {t} from './lib.js';

export function layout(nodes, edges, {direction = 'LR', ranksep = 90, nodesep = 36} = {}, routes = null) {
  const graph = new dagre.graphlib.Graph();
  graph.setGraph({rankdir: direction, ranksep, nodesep, marginx: 20, marginy: 20});
  graph.setDefaultEdgeLabel(() => ({}));
  nodes.forEach(node => graph.setNode(node.id, {width: node.width, height: node.height}));
  edges.forEach(edge => graph.setEdge(edge.source, edge.target));
  dagre.layout(graph);
  if (routes) edges.forEach(edge => { routes[edge.id] = (graph.edge(edge.source, edge.target)?.points || []).slice(1, -1); });
  return nodes.map(node => {
    const at = graph.node(node.id);
    return {...node, position: {x: at.x - node.width / 2, y: at.y - node.height / 2},
      style: {width: node.width, height: node.height}};
  });
}

// A line through the bend points dagre routed between the ranks, so it runs around the nodes rather than behind them.
// The ends follow the handles, so a dragged node keeps its lines attached.
function RoutedEdge({id, sourceX, sourceY, targetX, targetY, data, markerEnd, style, interactionWidth}) {
  const points = [{x: sourceX, y: sourceY}, ...(data?.points || []), {x: targetX, y: targetY}];
  let path = `M ${points[0].x},${points[0].y}`;
  for (let i = 1; i < points.length - 1; i++) {
    const mid = {x: (points[i].x + points[i + 1].x) / 2, y: (points[i].y + points[i + 1].y) / 2};
    path += ` Q ${points[i].x},${points[i].y} ${mid.x},${mid.y}`;
  }
  path += ` L ${targetX},${targetY}`;
  return <BaseEdge id={id} path={path} markerEnd={markerEnd} style={style} interactionWidth={interactionWidth} />;
}
const edgeTypes = {routed: RoutedEdge};

function Canvas({nodes, edges, nodeTypes, onSelect, onSelectEdge, routed = false, direction, nodesep, label, className = ''}) {
  // Nodes that arrive with a position keep it (a clustered layout); otherwise dagre places them.
  const [laid, routes] = useMemo(() => {
    const routes = routed ? {} : null;
    return [nodes.every(node => node.position) ? nodes.map(node => ({...node, style: {width: node.width, height: node.height}}))
      : layout(nodes, edges, {direction, nodesep}, routes), routes];
  }, [nodes, edges, direction, nodesep, routed]);
  const styled = useMemo(() => edges.map(edge => ({
    ...edge, markerEnd: {type: MarkerType.ArrowClosed, width: 16, height: 16},
    labelBgPadding: [4, 2], labelBgBorderRadius: 3, interactionWidth: 14,
    ...(routes?.[edge.id] ? {type: 'routed', data: {...edge.data, points: routes[edge.id]}} : {})
  })), [edges, routes]);
  const [rfNodes, setNodes, onNodesChange] = useNodesState(laid);
  const [rfEdges, setEdges, onEdgesChange] = useEdgesState(styled);
  const flow = useReactFlow();
  const box = useRef(null);
  // The library writes the English label of its own control buttons; set them in the page language once mounted.
  useEffect(() => {
    const label = (selector, text) => box.current?.querySelectorAll(selector).forEach(el => { el.setAttribute('title', text); el.setAttribute('aria-label', text); });
    label('.react-flow__controls-zoomin', t('zoom in')); label('.react-flow__controls-zoomout', t('zoom out')); label('.react-flow__controls-fitview', t('fit view'));
    box.current?.querySelector('.react-flow__controls')?.setAttribute('aria-label', t('React Flow controls'));
  }, []);
  useEffect(() => { setNodes(laid); setEdges(styled); const timer = setTimeout(() => flow.fitView({padding: 0.12, maxZoom: 1}), 60); return () => clearTimeout(timer); }, [laid, styled]);
  return <div className={'pf-canvas ' + className} role="region" aria-label={label} ref={box}>
    <ReactFlow nodes={rfNodes} edges={rfEdges} nodeTypes={nodeTypes} edgeTypes={edgeTypes} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
      onNodeClick={(_, node) => onSelect?.(node)} onEdgeClick={onSelectEdge ? (_, edge) => onSelectEdge(edge) : undefined} fitView fitViewOptions={{padding: 0.12, maxZoom: 1}} minZoom={0.15} maxZoom={2} nodesConnectable={false}
      proOptions={{hideAttribution: true}}>
      <Background gap={20} size={1} color="#e3e7ef" />
      <Controls showInteractive={false} position="bottom-right" />
    </ReactFlow>
  </div>;
}

export default function Flow(props) {
  return <ReactFlowProvider><Canvas {...props} /></ReactFlowProvider>;
}
