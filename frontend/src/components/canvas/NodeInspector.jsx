/**
 * NodeInspector — pannello laterale della nuova interfaccia dei canvas
 * (Fase 4, M1). Mostra il form completo del nodo selezionato: e' lo stesso
 * componente che prima stava sul canvas (TableNode, TransformNode,
 * ResultNode, ...), cosi' la configurazione resta identica. Sul canvas il
 * nodo e' compatto (CompactNode).
 *
 * Gli <Handle> dei form sono NodeHandle: fuori dal canvas non disegnano
 * nulla. Il pannello vive dentro il ReactFlowProvider dell'editor, quindi
 * i form possono continuare a usare useReactFlow/useStore.
 */
import React, { useCallback } from 'react';
import useCanvasStore from '../../store/useCanvasStore';
import { nodeDescriptor, nodeIcon, nodeTitle } from './nodeDescriptors';
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

export default function NodeInspector({ fullNodeTypes, prefix = 'canvas' }) {
  const nodeId = useCanvasStore(selectInspectedId);
  const node = useCanvasStore(useCallback(s => (nodeId ? (s.nodes || []).find(n => n.id === nodeId) : null), [nodeId]));
  const onNodesChange = useCanvasStore(s => s.onNodesChange);
  const updateNodeData = useCanvasStore(s => s.updateNodeData);
  const isReadOnly = useCanvasStore(s => s.isReadOnly);

  if (!node) return null;
  const Full = fullNodeTypes[node.type];
  const descriptor = nodeDescriptor(node.type);
  const icon = nodeIcon(node.type, node.data);
  const close = () => onNodesChange([{ id: node.id, type: 'select', selected: false }]);

  return (
    <aside className={`node-inspector ${prefix}-inspector`} aria-label="Configurazione del passo">
      <header className="node-inspector__head">
        <div className="node-inspector__kind">
          <span aria-hidden="true">{icon}</span> {descriptor.kind(node.data || {})}
        </div>
        <button type="button" className="node-inspector__close" onClick={close} aria-label="Chiudi pannello">×</button>
        <input
          key={node.id}
          className="node-inspector__title"
          defaultValue={nodeTitle(node.type, node.data)}
          aria-label="Titolo del passo"
          disabled={isReadOnly}
          onBlur={e => {
            const next = e.target.value.trim();
            if (next !== (node.data?.title || '')) updateNodeData(node.id, { title: next || undefined });
          }}
          onKeyDown={e => { if (e.key === 'Enter') e.currentTarget.blur(); }}
        />
        <div className="node-inspector__summary">{descriptor.summary(node.data || {})}</div>
      </header>
      <div className="node-inspector__body">
        {Full
          ? <Full id={node.id} type={node.type} data={node.data || {}} selected inInspector />
          : <div className="node-inspector__empty">Nessuna configurazione per questo nodo.</div>}
      </div>
    </aside>
  );
}
