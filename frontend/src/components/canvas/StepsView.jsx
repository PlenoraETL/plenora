/**
 * StepsView — vista "Passi" (Fase 4, M6): lo stesso canvas come elenco
 * lineare. Le corsie vengono dall'analisi condivisa (CanvasGraphContext,
 * utils/canvasGraph): ogni nodo del flusso sta in esattamente una corsia
 * (sorgente, unione, o dopo un bivio), nell'ordine del flusso.
 * Un clic seleziona il passo (stesso pannello laterale del canvas);
 * "＋ Aggiungi passo" apre la palette dopo l'ultimo passo prima dell'Output.
 * Con `edgeInspector` (Estrai: i join) sotto ogni passo compaiono gli archi
 * che arrivano a lui, una volta sola ciascuno: un clic li seleziona e li
 * apre nello stesso pannello.
 */
import React, { useContext, useEffect, useMemo, useRef } from 'react';
import useCanvasStore from '../../store/useCanvasStore';
import { CanvasGraphContext } from './CanvasGraphContext';
import { nodeDescriptor, nodeIcon, nodeTitle } from './nodeDescriptors';
import './StepsView.css';

const LANE_TITLE = { source: 'Ramo da', join: 'Da più ingressi:', fork: 'Dopo il bivio:' };

export default function StepsView({ onAddAfter, edgeInspector = null }) {
  const graph = useContext(CanvasGraphContext);
  const nodes = useCanvasStore(s => s.nodes);
  const edges = useCanvasStore(s => s.edges);
  const selectEdge = useCanvasStore(s => s.selectEdge);
  const focusNodeId = useCanvasStore(s => s.focusNodeId);
  const isReadOnly = useCanvasStore(s => s.isReadOnly);
  const onNodesChange = useCanvasStore(s => s.onNodesChange);
  const onEdgesChange = useCanvasStore(s => s.onEdgesChange);
  const byId = useMemo(() => new Map(nodes.map(n => [n.id, n])), [nodes]);
  // Archi da mostrare per passo: quelli che arrivano al passo.
  const edgesByTarget = useMemo(() => {
    const out = new Map();
    if (!edgeInspector) return out;
    for (const e of edges) {
      if (!edgeInspector.matches(e, byId)) continue;
      if (!out.has(e.target)) out.set(e.target, []);
      out.get(e.target).push({ ...edgeInspector.entry(e, byId, e.target), selected: Boolean(e.selected) });
    }
    return out;
  }, [edgeInspector, edges, byId]);
  const ref = useRef(null);

  // "Vedi in Passi": porta in vista il passo scelto nel canvas.
  useEffect(() => {
    if (!focusNodeId) return;
    ref.current?.querySelector(`[data-step-id="${CSS.escape(focusNodeId)}"]`)?.scrollIntoView?.({ block: 'center' });
  }, [focusNodeId]);

  // Selezione esclusiva: il passo cliccato, nessun altro nodo ne' edge.
  const select = (id) => {
    const { nodes: ns, edges: es } = useCanvasStore.getState();
    onNodesChange(ns.filter(n => Boolean(n.selected) !== (n.id === id)).map(n => ({ id: n.id, type: 'select', selected: n.id === id })));
    const selEdges = es.filter(e => e.selected).map(e => ({ id: e.id, type: 'select', selected: false }));
    if (selEdges.length) onEdgesChange(selEdges);
  };

  if (graph.lanes.length === 0) {
    const empty = nodes.length === 0;
    return (
      <div className="steps-view steps-view--empty" ref={ref} role="region" aria-label="Vista Passi">
        <p>{empty ? 'Il canvas è vuoto: aggiungi una sorgente per iniziare.' : 'Nessun passo da mostrare: il canvas ha solo note, input o un Output non collegato.'}</p>
        {empty && onAddAfter && !isReadOnly && (
          <button type="button" className="steps-lane__add" onClick={() => onAddAfter(null)}>＋ Aggiungi</button>
        )}
      </div>
    );
  }

  return (
    <div className="steps-view" ref={ref} role="region" aria-label="Vista Passi">
      {graph.lanes.map((lane) => {
        const head = byId.get(lane.headId);
        const titleId = `lane-${lane.id}`;
        return (
          <section key={lane.id} aria-labelledby={titleId} className={`steps-lane${lane.dangling ? ' steps-lane--dangling' : ''}`}>
            <h4 className="steps-lane__title" id={titleId}>
              <span aria-hidden="true">{head ? nodeIcon(head.type, head.data) : ''}</span>
              {LANE_TITLE[lane.kind]} {head ? nodeTitle(head.type, head.data) : ''}
              {lane.dangling && <span className="steps-lane__warn">non arriva all&apos;Output</span>}
            </h4>
            <ol className="steps-lane__list">
              {lane.stepIds.map((id, i) => {
                const n = byId.get(id);
                if (!n) return null;
                return (
                  <li key={id}>
                    <button
                      type="button"
                      data-step-id={id}
                      aria-current={n.selected ? 'step' : undefined}
                      className={`steps-step${n.selected ? ' steps-step--selected' : ''}${focusNodeId === id ? ' steps-step--focus' : ''}`}
                      onClick={() => select(id)}
                    >
                      <span className="steps-step__n">{i + 1}</span>
                      <span className="steps-step__icon" aria-hidden="true">{nodeIcon(n.type, n.data)}</span>
                      <span className="steps-step__title">{nodeTitle(n.type, n.data)}</span>
                      <span className="steps-step__summary">{nodeDescriptor(n.type).summary(n.data || {})}</span>
                    </button>
                    {edgesByTarget.has(id) && (
                      <ul className="steps-step__edges" aria-label={`${edgeInspector.kind} di ${nodeTitle(n.type, n.data)}`}>
                        {edgesByTarget.get(id).map(entry => (
                          <li key={entry.id}>
                            <button
                              type="button"
                              data-edge-id={entry.id}
                              aria-current={entry.selected ? 'step' : undefined}
                              className={`steps-edge steps-edge--${entry.status}${entry.selected ? ' steps-edge--selected' : ''}`}
                              onClick={() => selectEdge(entry.id)}
                            >
                              <span aria-hidden="true">{entry.icon}</span>
                              <span className="steps-edge__title">{entry.title}</span>
                              <span className="steps-edge__summary">{entry.summary}</span>
                            </button>
                          </li>
                        ))}
                      </ul>
                    )}
                  </li>
                );
              })}
            </ol>
            {onAddAfter && !isReadOnly && lane.addAfterId && (
              <button type="button" className="steps-lane__add" onClick={() => onAddAfter(lane.addAfterId)}>＋ Aggiungi passo</button>
            )}
          </section>
        );
      })}
    </div>
  );
}
