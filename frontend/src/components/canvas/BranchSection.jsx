/**
 * BranchSection — rami nel pannello laterale (Fase 4, M6):
 *  - passo scollegato (i suoi dati non arrivano all'Output): avviso; se e'
 *    l'ultimo del ramo, "Collega" a un passo che arriva all'Output; se no,
 *    "Vai all'ultimo passo";
 *  - sorgente con dei passi: "Duplica ramo" (gli stessi passi su un'altra
 *    sorgente).
 * Legge l'analisi condivisa (CanvasGraphContext); le azioni vere sono
 * dell'editor (canvasConfig.branchActions). Senza azione, spiega come fare.
 */
import React, { useContext, useMemo, useState } from 'react';
import useCanvasStore from '../../store/useCanvasStore';
import { branchSteps, nodeRole } from '../../utils/canvasGraph';
import { CanvasGraphContext } from './CanvasGraphContext';
import { nodeTitle } from './nodeDescriptors';
import '../transforms/panels/panels.css';

const titleOf = (n) => (n ? nodeTitle(n.type, n.data) : '');

export default function BranchSection({ node, branchActions = {} }) {
  const graph = useContext(CanvasGraphContext);
  const isReadOnly = useCanvasStore(s => s.isReadOnly);
  const onNodesChange = useCanvasStore(s => s.onNodesChange);
  const { connectBranch, connectModes = [['join', 'Unisci con (Join)']], duplicateBranch } = branchActions;
  const dangling = graph.dangling.has(node.id);
  const end = graph.isEnd(node.id);
  const lane = graph.laneOf.get(node.id);
  // Titoli dei nodi: le etichette seguono anche le rinomine (non solo la struttura).
  const titlesKey = useCanvasStore(s => s.nodes.map(n => `${n.id}:${titleOf(n)}`).join('|'));

  // Calcoli sul grafo solo quando cambia la struttura (graph.key), non a ogni drag.
  const derived = useMemo(() => {
    const { nodes, edges } = useCanvasStore.getState();
    const byId = new Map(nodes.map(n => [n.id, n]));
    const laneName = (id) => titleOf(byId.get(graph.laneOf.get(id)?.headId));
    return {
      branchName: lane ? titleOf(byId.get(lane.headId)) : titleOf(node),
      // Mai un nodo a monte del ramo: ricollegarlo creerebbe un ciclo.
      targets: (() => {
        const upstream = graph.ancestorsOf(node.id);
        return nodes
          .filter(n => graph.reaching.has(n.id) && n.id !== node.id && !upstream.has(n.id) && ['source', 'step'].includes(nodeRole(n)))
          // "passo · ramo", senza ripetere il nome quando il passo apre la corsia.
          // (anche quando il titolo lo contiene gia', es. i passi copiati).
          .map(n => {
            const t = titleOf(n);
            const lane = laneName(n.id);
            return { id: n.id, label: lane && t !== lane && !t.endsWith(` · ${lane}`) ? `${t} · ${lane}` : t };
          });
      })(),
      ends: graph.danglingEndsFrom(node.id).filter(id => id !== node.id).map(id => ({ id, label: titleOf(byId.get(id)) })),
      hasOutput: nodes.some(n => nodeRole(n) === 'output'),
      anyReaching: nodes.some(n => graph.reaching.has(n.id) && ['source', 'step'].includes(nodeRole(n))),
      steps: nodeRole(node) === 'source' ? branchSteps(nodes, edges, node.id) : [],
      otherSources: nodes.filter(n => nodeRole(n) === 'source' && n.id !== node.id)
        .map(n => ({ id: n.id, label: titleOf(n) })),
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph.key, node.id, titlesKey]);

  const [mode, setMode] = useState(connectModes[0]?.[0]);
  const [target, setTarget] = useState('');
  const [other, setOther] = useState('');
  const validTarget = derived.targets.some(t => t.id === target) ? target : '';
  const validOther = derived.otherSources.some(s => s.id === other) ? other : '';
  const select = (id) => {
    const { nodes } = useCanvasStore.getState();
    onNodesChange(nodes.filter(n => n.selected !== (n.id === id)).map(n => ({ id: n.id, type: 'select', selected: n.id === id })));
  };

  const parts = [];
  if (dangling) {
    let action = null;
    if (!end) {
      // Le estremita' del ramo, anche oltre un bivio: da li' si collega.
      action = derived.ends.length > 0 ? (
        <div className="ppanel__row" style={{ marginTop: 8, flexWrap: 'wrap' }}>
          {derived.ends.map(end => (
            <button key={end.id} type="button" className="ppanel__btn" onClick={() => select(end.id)}>
              {derived.ends.length === 1 ? "Vai all'ultimo passo" : `Vai a «${end.label}»`}
            </button>
          ))}
        </div>
      ) : null;
    } else if (isReadOnly) {
      action = null;
    } else if (!connectBranch) {
      action = <div className="ppanel__hint">Collegalo trascinando dalla sua uscita a un passo che arriva all&apos;Output.</div>;
    } else if (derived.targets.length === 0) {
      action = (
        <div className="ppanel__hint">
          {!derived.hasOutput
            ? 'Aggiungi un Output, poi collega i rami.'
            : !derived.anyReaching
              ? "Nessun passo arriva ancora all'Output: collega prima il ramo principale all'Output."
              : "I passi che arrivano all'Output sono tutti a monte di questo ramo: collegalo trascinando dalla sua uscita all'Output o a un'unione."}
        </div>
      );
    } else {
      action = (
        <div className="branch-connect">
          {connectModes.length > 1 && (
            <select className="ppanel__input" value={mode} onChange={e => setMode(e.target.value)} aria-label="Come collegare">
              {connectModes.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          )}
          <select className="ppanel__input" value={validTarget} onChange={e => setTarget(e.target.value)} aria-label="Passo a cui collegare">
            <option value="">— scegli un passo —</option>
            {derived.targets.map(t => <option key={t.id} value={t.id}>{t.label}</option>)}
          </select>
          <button type="button" className="ppanel__btn ppanel__btn--primary" disabled={!validTarget}
            onClick={() => connectBranch(node.id, validTarget, connectModes.length > 1 ? mode : connectModes[0][0])}>Collega</button>
        </div>
      );
    }
    parts.push(
      <div key="dangling" className="ppanel__note branch-warn" role="status">
        {end
          ? <>Il ramo <b>{derived.branchName}</b> non arriva all&apos;Output: i suoi dati non finiscono nel risultato.</>
          : <>Questo passo fa parte del ramo <b>{derived.branchName}</b>, che non arriva all&apos;Output. Si collega dal suo ultimo passo.</>}
        {action}
      </div>,
    );
  }
  if (duplicateBranch && !isReadOnly && derived.steps.length > 0) {
    parts.push(
      <div key="duplicate" className="ppanel__field branch-dup">
        <span className="ppanel__label">Applica gli stessi passi a un&apos;altra sorgente</span>
        <div className="ppanel__hint">
          Il ramo che parte da qui ha <b>{derived.steps.length} {derived.steps.length === 1 ? 'passo' : 'passi'}</b>.
        </div>
        {derived.otherSources.length > 0 ? (
          <div className="ppanel__row">
            <select className="ppanel__input" value={validOther} onChange={e => setOther(e.target.value)} aria-label="Sorgente su cui duplicare">
              <option value="">— scegli la sorgente —</option>
              {derived.otherSources.map(s => <option key={s.id} value={s.id}>{s.label}</option>)}
            </select>
            <button type="button" className="ppanel__btn" disabled={!validOther} onClick={() => duplicateBranch(node.id, validOther)}>Duplica ramo</button>
          </div>
        ) : (
          <div className="ppanel__hint">Aggiungi un&apos;altra sorgente per duplicare il ramo.</div>
        )}
      </div>,
    );
  }
  return parts.length ? <div className="branch-section">{parts}</div> : null;
}
