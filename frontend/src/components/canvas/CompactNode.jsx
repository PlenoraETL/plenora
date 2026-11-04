/**
 * CompactNode — nodo compatto della nuova interfaccia dei canvas (Fase 4,
 * M1): titolo, riassunto leggibile e categoria. Il form completo sta nel
 * pannello laterale (NodeInspector), aperto selezionando il nodo.
 *
 * Handle = quelli standard del tipo (nodeDescriptors) UNITI a quelli gia'
 * usati dagli edge collegati: un canvas salvato con handle specifici
 * (binding `parameter:<id>`, `input-N` del Multi-Join, ...) resta collegato.
 */
import React, { memo, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { Handle, Position, useStore } from '@xyflow/react';
import useCanvasStore from '../../store/useCanvasStore';
import { fetchTransformRegistry, getCachedTransformRegistry } from '../../utils/transformRegistry';
import { derivedTitle, nodeDescriptor, nodeIcon, nodeTitle, titlePatch, userTitle } from './nodeDescriptors';
import { CanvasGraphContext } from './CanvasGraphContext';
import './CompactNode.css';

const POSITION = { left: Position.Left, right: Position.Right, top: Position.Top };

// Handle usati dagli edge collegati a questo nodo, come stringa stabile
// (il selettore restituisce una stringa: niente re-render a ogni drag).
const connectedHandlesKey = (id) => (state) => state.edges
  .filter(e => e.source === id || e.target === id)
  .map(e => (e.source === id ? `source|${e.sourceHandle || ''}` : `target|${e.targetHandle || ''}`))
  .sort()
  .join(';');

export function mergeHandles(defaults, connectedKey, nextFreeInput) {
  const byKey = new Map(defaults.map(h => [`${h.type}|${h.id || ''}`, h]));
  const connected = connectedKey ? connectedKey.split(';') : [];
  for (const k of connected) {
    if (byKey.has(k)) continue;
    const [type, id] = k.split('|');
    if (!id && [...byKey.keys()].some(x => x.startsWith(`${type}|`))) continue;
    const position = id.startsWith('parameter:') ? 'top' : (type === 'source' ? 'right' : 'left');
    byKey.set(k, { type, id: id || undefined, position });
  }
  if (nextFreeInput) {
    // Multi-Join: sempre un ingresso libero in piu' dopo quelli collegati.
    const used = connected.map(k => k.match(/^target\|input-(\d+)$/)).filter(Boolean).map(m => Number(m[1]));
    const next = used.length ? Math.max(...used) + 1 : 2;
    const key = `target|input-${next}`;
    if (!byKey.has(key)) byKey.set(key, { type: 'target', id: `input-${next}`, position: 'left' });
  }
  return [...byKey.values()];
}

function CompactNode({ id, type, data = {}, selected }) {
  const descriptor = nodeDescriptor(type);
  const connectedKey = useStore(connectedHandlesKey(id));
  const isReadOnly = useCanvasStore(s => s.isReadOnly);
  const updateNodeData = useCanvasStore(s => s.updateNodeData);
  const [, forceRegistry] = useState(getCachedTransformRegistry());
  const [editing, setEditing] = useState(false);
  // Esc annulla: il blur che segue la chiusura del campo non scrive.
  const cancelledRef = useRef(false);

  // Il nome leggibile dell'operazione arriva dal registry (caricato una volta).
  useEffect(() => {
    if (type !== 'transform' || getCachedTransformRegistry()) return undefined;
    let alive = true;
    fetchTransformRegistry().then(r => { if (alive) forceRegistry(r); });
    return () => { alive = false; };
  }, [type]);

  const handles = useMemo(
    () => mergeHandles(descriptor.defaultHandles(data), connectedKey, descriptor.nextFreeInput),
    [descriptor, data, connectedKey],
  );

  // La palette e' registrata da EditorShell (effetto, dopo il primo render
  // dei nodi): l'azione si legge al clic, non al render.
  const canAddAfter = !isReadOnly && handles.some(h => h.type === 'source' && h.position !== 'top');
  const openPalette = (nodeId) => useCanvasStore.getState().getEditorAction('openPalette')?.(nodeId);

  const title = nodeTitle(type, data);
  const icon = nodeIcon(type, data);
  const summary = descriptor.summary(data);
  const problem = data.access_denied ? (data.access_denied_reason || 'Accesso negato') : null;
  // Fase 4, M6: i dati di questo nodo non arrivano all'Output.
  const dangling = useContext(CanvasGraphContext).dangling.has(id);

  // Posizione verticale degli handle dello stesso lato, distribuiti.
  const sides = { left: [], right: [], top: [] };
  handles.forEach(h => sides[h.position || 'left'].push(h));

  // Stessa regola del pannello (nodeDescriptors.titlePatch): si scrive solo
  // un testo diverso dal titolo dell'utente e dal derivato; vuoto = derivato.
  const commitTitle = (value) => {
    setEditing(false);
    if (cancelledRef.current) return;
    const patch = titlePatch(type, data, value);
    if (patch) updateNodeData(id, patch);
  };

  return (
    <div className={`cnode cnode--${descriptor.category}${selected ? ' cnode--selected' : ''}`} data-testid="compact-node">
      {Object.entries(sides).flatMap(([side, list]) => list.map((h, i) => (
        <Handle
          key={`${h.type}-${h.id || 'default'}`}
          type={h.type}
          id={h.id}
          position={POSITION[side]}
          className={`cnode__handle${side === 'top' ? ' cnode__handle--param' : ''}`}
          style={side === 'top'
            ? { left: `${((i + 1) * 100) / (list.length + 1)}%` }
            : { top: `${((i + 1) * 100) / (list.length + 1)}%` }}
        />
      )))}
      <div className="cnode__row">
        <span className="cnode__icon" aria-hidden="true">{icon}</span>
        {editing ? (
          <input
            className="cnode__title-input nodrag"
            defaultValue={userTitle(type, data)}
            placeholder={derivedTitle(type, data)}
            aria-label="Titolo del passo"
            autoFocus
            onBlur={e => commitTitle(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter') e.currentTarget.blur();
              if (e.key === 'Escape') { cancelledRef.current = true; setEditing(false); }
            }}
          />
        ) : (
          <span
            className="cnode__title"
            title={isReadOnly ? title : `${title} — doppio clic per rinominare`}
            onDoubleClick={() => { if (!isReadOnly) { cancelledRef.current = false; setEditing(true); } }}
          >
            {title}
          </span>
        )}
        {problem && <span className="cnode__dot cnode__dot--error" title={problem} />}
        {!problem && dangling && <span className="cnode__dot cnode__dot--warn" title="Non collegato all'Output: i suoi dati non finiscono nel risultato" />}
      </div>
      <div className="cnode__summary" title={summary}>{summary || ' '}</div>
      <div className="cnode__kind">
        {descriptor.kind(data)}
        {problem ? <span className="cnode__problem"> · {problem}</span> : null}
        {!problem && dangling ? <span className="cnode__dangling"> · non collegato all&apos;Output</span> : null}
      </div>
      {canAddAfter && (
        <button
          type="button"
          className="cnode__plus nodrag"
          title="Aggiungi il passo successivo"
          aria-label="Aggiungi il passo successivo"
          onClick={(e) => { e.stopPropagation(); openPalette(id); }}
        >
          ＋
        </button>
      )}
    </div>
  );
}

export default memo(CompactNode);
