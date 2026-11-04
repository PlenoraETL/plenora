/**
 * NodeInspector — pannello laterale della nuova interfaccia dei canvas
 * (Fase 4, M1). Mostra il form completo del nodo selezionato: e' lo stesso
 * componente che prima stava sul canvas (TableNode, TransformNode,
 * ResultNode, ...), cosi' la configurazione resta identica. Sul canvas il
 * nodo e' compatto (CompactNode).
 *
 * Regola 6 del Blocco A: il guscio possiede intestazione, chiusura ed
 * eliminazione. Titolo e icona vengono solo da nodeDescriptors; i form
 * completi non disegnano un'intestazione propria ne' un × di eliminazione.
 * C'e' un solo "Elimina passo", con conferma, e non in sola lettura.
 *
 * Regola 7: il campo titolo e' controllato e mostra solo il testo
 * dell'utente; il titolo derivato e' il segnaposto (nodeDescriptors.titlePatch).
 *
 * Gli <Handle> dei form sono NodeHandle: fuori dal canvas non disegnano
 * nulla. Il pannello vive dentro il ReactFlowProvider dell'editor, quindi
 * i form possono continuare a usare useReactFlow/useStore.
 */
import React, { useCallback, useEffect, useState } from 'react';
import useCanvasStore from '../../store/useCanvasStore';
import { useConfirmOrNative } from '../../context/ConfirmContext';
import { derivedTitle, nodeDescriptor, nodeIcon, nodeTitle, titlePatch, userTitle } from './nodeDescriptors';
import BranchSection from './BranchSection';
import './NodeInspector.css';

// Id del nodo da ispezionare: esattamente un nodo selezionato, note escluse.
const selectInspectedId = (s) => {
  let found = null;
  for (const n of s.nodes || []) {
    if (!n.selected || n.type === 'comment') continue;
    if (found) return null;
    found = n.id;
  }
  return found;
};

/**
 * Campo titolo controllato: la bozza segue `data.title` (undo, ripristino,
 * conversioni) finche' l'utente non scrive; al blur o con Invio si scrive
 * solo un testo diverso dal titolo dell'utente e dal derivato.
 */
function InspectorTitle({ node, disabled }) {
  const updateNodeData = useCanvasStore(s => s.updateNodeData);
  const stored = userTitle(node.type, node.data);
  const [draft, setDraft] = useState(stored);
  useEffect(() => { setDraft(stored); }, [stored, node.id]);

  const commit = () => {
    const patch = titlePatch(node.type, node.data, draft);
    if (patch) updateNodeData(node.id, patch);
    else setDraft(stored);
  };

  return (
    <input
      className="node-inspector__title"
      value={draft}
      placeholder={derivedTitle(node.type, node.data)}
      aria-label="Titolo del passo"
      disabled={disabled}
      onChange={e => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={e => {
        if (e.key === 'Enter') e.currentTarget.blur();
        if (e.key === 'Escape') { setDraft(stored); e.currentTarget.blur(); }
      }}
    />
  );
}

export default function NodeInspector({ fullNodeTypes, prefix = 'canvas', branchActions }) {
  const nodeId = useCanvasStore(selectInspectedId);
  const node = useCanvasStore(useCallback(s => (nodeId ? (s.nodes || []).find(n => n.id === nodeId) : null), [nodeId]));
  const onNodesChange = useCanvasStore(s => s.onNodesChange);
  const isReadOnly = useCanvasStore(s => s.isReadOnly);
  const canvasView = useCanvasStore(s => s.canvasView);
  const setCanvasView = useCanvasStore(s => s.setCanvasView);
  const confirm = useConfirmOrNative();

  if (!node) return null;
  const Full = fullNodeTypes[node.type];
  const descriptor = nodeDescriptor(node.type);
  const icon = nodeIcon(node.type, node.data);
  const close = () => onNodesChange([{ id: node.id, type: 'select', selected: false }]);

  // L'unica eliminazione del pannello, per ogni tipo (Output compreso).
  const deleteStep = async () => {
    const ok = await confirm({
      title: 'Elimina passo',
      message: `Eliminare «${nodeTitle(node.type, node.data)}»? I collegamenti del passo vengono rimossi.`,
      confirmText: 'Elimina',
      variant: 'danger',
    });
    if (ok) useCanvasStore.getState().deleteNode(node.id);
  };

  return (
    <aside className={`node-inspector ${prefix}-inspector`} aria-label="Configurazione del passo">
      <header className="node-inspector__head">
        <div className="node-inspector__kind">
          <span aria-hidden="true">{icon}</span> {descriptor.kind(node.data || {})}
        </div>
        <button type="button" className="node-inspector__close" onClick={close} aria-label="Chiudi pannello">×</button>
        <button
          type="button"
          className="node-inspector__switch"
          title="Mostra questo passo nell'altra vista"
          onClick={() => setCanvasView(canvasView === 'canvas' ? 'steps' : 'canvas', node.id)}
        >
          <span aria-hidden="true">{canvasView === 'canvas' ? '☰ ' : '◇ '}</span>
          {canvasView === 'canvas' ? 'Vedi in Passi' : 'Vedi nel canvas'}
        </button>
        <InspectorTitle node={node} disabled={isReadOnly} />
        <div className="node-inspector__summary">{descriptor.summary(node.data || {})}</div>
      </header>
      <div className="node-inspector__body">
        <BranchSection key={`branch-${node.id}`} node={node} branchActions={branchActions} />
        {/* key per nodo: bozze, ricerche e fuoco di un form non passano al
            nodo selezionato dopo. */}
        {Full
          ? <Full key={node.id} id={node.id} type={node.type} data={node.data || {}} />
          : <div className="node-inspector__empty">Nessuna configurazione per questo nodo.</div>}
      </div>
      {!isReadOnly && (
        <footer className="node-inspector__foot">
          <button type="button" className="node-inspector__delete" onClick={deleteStep}>Elimina passo</button>
        </footer>
      )}
    </aside>
  );
}
