/**
 * StepsView — vista "Passi" (Fase 4, M6): lo stesso canvas come elenco
 * lineare. Le corsie vengono dall'analisi condivisa (CanvasGraphContext,
 * utils/canvasGraph): ogni nodo del flusso sta in esattamente una corsia
 * (sorgente, unione, o dopo un bivio), nell'ordine del flusso.
 * Un clic seleziona il passo (stesso pannello laterale del canvas);
 * "＋ Aggiungi passo" apre la palette dopo l'ultimo passo prima dell'Output.
 */
import React, { useContext, useEffect, useMemo, useRef } from 'react';
import useCanvasStore from '../../store/useCanvasStore';
import { CanvasGraphContext } from './CanvasGraphContext';
import { nodeDescriptor, nodeIcon, nodeTitle } from './nodeDescriptors';
import './StepsView.css';

const LANE_TITLE = { source: 'Ramo da', join: 'Da più ingressi:', fork: 'Dopo il bivio:' };

export default function StepsView({ onAddAfter }) {
  const graph = useContext(CanvasGraphContext);
  const nodes = useCanvasStore(s => s.nodes);
  const focusNodeId = useCanvasStore(s => s.focusNodeId);
  const isReadOnly = useCanvasStore(s => s.isReadOnly);
  const onNodesChange = useCanvasStore(s => s.onNodesChange);
  const onEdgesChange = useCanvasStore(s => s.onEdgesChange);
  const byId = useMemo(() => new Map(nodes.map(n => [n.id, n])), [nodes]);
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
