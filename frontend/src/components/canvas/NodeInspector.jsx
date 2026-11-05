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
 *
 * Archi (Blocco A, raccolta b): ogni configurazione si modifica qui a
 * partire da una SELEZIONE, nodo o arco. Con `edgeInspector` (Estrai: i
 * join, { kind, icon, matches, entry, Panel }) un arco selezionato da solo
 * apre lo stesso guscio (intestazione, chiusura, "Elimina join" con
 * conferma, niente in sola lettura) con `Panel` come corpo; il pannello di
 * un nodo elenca i suoi archi con un link per aprirli.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import useCanvasStore from '../../store/useCanvasStore';
import { useConfirmOrDeny } from '../../context/ConfirmContext';
import { requestCanvasDeletion, useCanvasDeletion } from '../../utils/canvasDeletion';
import { derivedTitle, nodeDescriptor, nodeIcon, nodeTitle, titlePatch, userTitle } from './nodeDescriptors';
import BranchSection from './BranchSection';
import './NodeInspector.css';
import { term } from '../../utils/glossary';
import { useNodesContent } from '../../hooks/useColumns';
import { useReadOnlyLock } from '../common/readOnlySafe';

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

// Id dell'arco da ispezionare: esattamente un arco selezionato e nessun nodo.
const selectInspectedEdgeId = (s) => {
  if ((s.nodes || []).some(n => n.selected && n.type !== 'comment')) return null;
  let found = null;
  for (const e of s.edges || []) {
    if (!e.selected) continue;
    if (found) return null;
    found = e.id;
  }
  return found;
};

/** Guscio comune a nodi e archi: intestazione, chiusura, corpo, eliminazione. */
function InspectorFrame({ prefix, label, icon, kind, title, summary, onClose, switchButton, body, deleteLabel, onDelete }) {
  return (
    <aside className={`node-inspector ${prefix}-inspector`} aria-label={label}>
      <header className="node-inspector__head">
        <div className="node-inspector__kind">
          <span aria-hidden="true">{icon}</span> {kind}
        </div>
        <button type="button" className="node-inspector__close" onClick={onClose} aria-label="Chiudi pannello">×</button>
        {switchButton}
        {title}
        <div className="node-inspector__summary">{summary}</div>
      </header>
      <div className="node-inspector__body">{body}</div>
      {onDelete && (
        <footer className="node-inspector__foot">
          <button type="button" className="node-inspector__delete" onClick={onDelete}>{deleteLabel}</button>
        </footer>
      )}
    </aside>
  );
}

/**
 * Il form di un passo o di un arco (Fase 5, T5+T6, correzione dopo la
 * conferma). REGOLA della sola lettura nel pannello: si spengono SOLO i
 * controlli che modificano il canvas. `useReadOnlyLock` spegne davvero ogni
 * controllo nativo del form (input, select, textarea, button, compresi
 * FieldInput, SchemaFields, i sotto-componenti e quelli montati dopo) tranne
 * le AZIONI DI LETTURA dichiarate con `{...READ_ONLY_SAFE}`
 * (common/readOnlySafe: Cronologia esecuzioni, Valida/Anteprima/Esegui,
 * Ricarica/Espandi dell'anteprima, Riprova degli elenchi, Cerca colonna).
 * Prima un `<fieldset disabled>` spegneva anche queste. Fuori dal form
 * restano la navigazione del pannello (chiudi, vista, archi del passo) e i
 * rami, che decidono da soli.
 */
function ReadOnlyForm({ readOnly, children }) {
  const ref = useRef(null);
  useReadOnlyLock(ref, readOnly);
  return (
    <div ref={ref} className="node-inspector__form" data-readonly={readOnly ? 'true' : undefined}>
      {children}
    </div>
  );
}

/** Archi di un nodo (es. i join di una tabella), ognuno apribile nel pannello. */
function EdgeLinks({ nodeId, edgeInspector }) {
  const nodes = useNodesContent();
  const edges = useCanvasStore(s => s.edges);
  const selectEdge = useCanvasStore(s => s.selectEdge);
  const byId = new Map(nodes.map(n => [n.id, n]));
  const entries = edges
    .filter(e => (e.source === nodeId || e.target === nodeId) && edgeInspector.matches(e, byId))
    .map(e => edgeInspector.entry(e, byId, nodeId));
  if (entries.length === 0) return null;
  return (
    <section className="node-inspector__edges" aria-label={`${edgeInspector.kind} del passo`}>
      <div className="node-inspector__edges-title">{edgeInspector.kind}</div>
      {entries.map(entry => (
        <button key={entry.id} type="button" className={`node-inspector__edge node-inspector__edge--${entry.status}`}
          onClick={() => selectEdge(entry.id)}>
          <span aria-hidden="true">{entry.icon}</span> <strong>{entry.title}</strong>
          <span className="node-inspector__edge-summary">{entry.summary}</span>
        </button>
      ))}
    </section>
  );
}

function EdgeInspector({ edgeId, edgeInspector, prefix }) {
  const edge = useCanvasStore(useCallback(s => (s.edges || []).find(e => e.id === edgeId) || null, [edgeId]));
  const nodes = useNodesContent();
  const onEdgesChange = useCanvasStore(s => s.onEdgesChange);
  const isReadOnly = useCanvasStore(s => s.isReadOnly);
  const { canDelete } = useCanvasDeletion();
  const confirm = useConfirmOrDeny();
  const byId = new Map(nodes.map(n => [n.id, n]));
  if (!edge || !edgeInspector.matches(edge, byId)) return null;
  const entry = edgeInspector.entry(edge, byId);
  const { Panel } = edgeInspector;
  const what = edgeInspector.kind;
  // Regola unica delle cancellazioni (utils/canvasDeletion).
  const deleteEdge = () => requestCanvasDeletion({
    confirm,
    edges: [edge],
    dialog: {
      title: `Elimina ${what}`,
      message: `Eliminare «${entry.title}»? Le tabelle restano nel canvas.`,
      confirmText: 'Elimina',
      variant: 'danger',
    },
    perform: () => useCanvasStore.getState().removeEdges(edge.id),
  });
  return (
    <InspectorFrame
      prefix={prefix}
      label={`Configurazione: ${edgeInspector.kind}`}
      icon={edgeInspector.icon}
      kind={edgeInspector.kind}
      title={<div className="node-inspector__title node-inspector__title--static">{entry.title}</div>}
      summary={entry.summary}
      onClose={() => onEdgesChange([{ id: edge.id, type: 'select', selected: false }])}
      body={(
        <ReadOnlyForm readOnly={isReadOnly}>
          <Panel key={edge.id} edge={edge} sourceNode={byId.get(edge.source)} targetNode={byId.get(edge.target)} disabled={isReadOnly} />
        </ReadOnlyForm>
      )}
      deleteLabel={`Elimina ${what}`}
      onDelete={canDelete ? deleteEdge : null}
    />
  );
}

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

  // Esc: si esce dal campo SENZA scrivere (il blur legge la bozza vecchia).
  const cancelledRef = useRef(false);
  const commit = () => {
    if (cancelledRef.current) { cancelledRef.current = false; setDraft(stored); return; }
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
        if (e.key === 'Escape') { cancelledRef.current = true; e.currentTarget.blur(); }
      }}
    />
  );
}

export default function NodeInspector({ fullNodeTypes, prefix = 'canvas', branchActions, edgeInspector = null }) {
  const nodeId = useCanvasStore(selectInspectedId);
  const edgeId = useCanvasStore(selectInspectedEdgeId);
  const node = useCanvasStore(useCallback(s => (nodeId ? (s.nodes || []).find(n => n.id === nodeId) : null), [nodeId]));
  const onNodesChange = useCanvasStore(s => s.onNodesChange);
  const isReadOnly = useCanvasStore(s => s.isReadOnly);
  const canvasView = useCanvasStore(s => s.canvasView);
  const setCanvasView = useCanvasStore(s => s.setCanvasView);
  const { canDelete } = useCanvasDeletion();
  const confirm = useConfirmOrDeny();

  if (!node) {
    return edgeInspector && edgeId ? <EdgeInspector edgeId={edgeId} edgeInspector={edgeInspector} prefix={prefix} /> : null;
  }
  const Full = fullNodeTypes[node.type];
  const descriptor = nodeDescriptor(node.type);
  const icon = nodeIcon(node.type, node.data);
  const close = () => onNodesChange([{ id: node.id, type: 'select', selected: false }]);

  // L'unica eliminazione del pannello, per ogni tipo (Output compreso).
  const deleteStep = () => requestCanvasDeletion({
    confirm,
    nodes: [node],
    dialog: {
      title: term('delete_step').name,
      message: `Eliminare «${nodeTitle(node.type, node.data)}»? I collegamenti del passo vengono rimossi.`,
      confirmText: 'Elimina',
      variant: 'danger',
    },
    perform: () => useCanvasStore.getState().deleteNode(node.id),
  });

  return (
    <InspectorFrame
      prefix={prefix}
      label="Configurazione del passo"
      icon={icon}
      kind={descriptor.kind(node.data || {})}
      onClose={close}
      switchButton={(
        <button
          type="button"
          className="node-inspector__switch"
          title="Mostra questo passo nell'altra vista"
          onClick={() => setCanvasView(canvasView === 'canvas' ? 'steps' : 'canvas', node.id)}
        >
          <span aria-hidden="true">{canvasView === 'canvas' ? '☰ ' : '◇ '}</span>
          {canvasView === 'canvas' ? 'Vedi in Passi' : 'Vedi nel canvas'}
        </button>
      )}
      title={<InspectorTitle node={node} disabled={isReadOnly} />}
      summary={descriptor.summary(node.data || {})}
      body={(
        <>
          <BranchSection key={`branch-${node.id}`} node={node} branchActions={branchActions} />
          {edgeInspector && <EdgeLinks nodeId={node.id} edgeInspector={edgeInspector} />}
          {/* key per nodo: bozze, ricerche e fuoco di un form non passano al
              nodo selezionato dopo. */}
          {Full
            ? (
              <ReadOnlyForm readOnly={isReadOnly}>
                <Full key={node.id} id={node.id} type={node.type} data={node.data || {}} />
              </ReadOnlyForm>
            )
            : <div className="node-inspector__empty">Nessuna configurazione per questo nodo.</div>}
        </>
      )}
      deleteLabel={term('delete_step').name}
      onDelete={canDelete ? deleteStep : null}
    />
  );
}
