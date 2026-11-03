/**
 * autoLayout — disposizione automatica dei nodi da sinistra a destra
 * (Fase 4, M2), con dagre. Le note (`comment`) restano dove sono.
 *
 * Funzione pura: restituisce i nodi con le nuove posizioni. Chi la usa la
 * applica come azione utente (editNodes): annullabile e salvata.
 */
import dagre from '@dagrejs/dagre';
import { isParameterBindingEdge } from '../../utils/runtimeBindingEdges';

const NODE_W = 232;
const NODE_H = 80;

export function autoLayout(nodes, edges) {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: 'LR', nodesep: 34, ranksep: 70, marginx: 20, marginy: 20 });
  g.setDefaultEdgeLabel(() => ({}));

  const laid = nodes.filter(n => n.type !== 'comment');
  const ids = new Set(laid.map(n => n.id));
  for (const n of laid) {
    g.setNode(n.id, { width: n.measured?.width || NODE_W, height: n.measured?.height || NODE_H });
  }
  // I binding degli input runtime non sono flusso di dati: non devono
  // allungare i ranghi del layout.
  for (const e of edges) {
    if (ids.has(e.source) && ids.has(e.target) && !isParameterBindingEdge(e)) g.setEdge(e.source, e.target);
  }
  dagre.layout(g);

  return nodes.map((n) => {
    if (!ids.has(n.id)) return n;
    const p = g.node(n.id);
    if (!p) return n;
    return { ...n, position: { x: Math.round(p.x - p.width / 2), y: Math.round(p.y - p.height / 2) } };
  });
}
