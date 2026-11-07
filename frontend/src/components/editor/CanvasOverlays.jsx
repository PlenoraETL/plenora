/**
 * CanvasOverlays — cio' che il guscio dell'editor mette sopra il canvas per
 * i comandi comuni (UI-3, 06/11/2026), uguale nei tre editor:
 *
 *  - U11: canvas vuoto con la guida di cosa fare per primo («＋ Aggiungi»
 *    apre la palette con le sorgenti, «Guida» la guida dell'editor);
 *  - U27: tastiera dei comandi del canvas (tabella unica canvasCommands:
 *    Ctrl+A, Ctrl+C, Ctrl+V, Ctrl+D, Ctrl+F, C) e le due finestre di scelta
 *    «Cerca nel canvas» e «Collega a…» (la stessa palette dei passi);
 *  - U30: guida dell'editor, aperta sulla sezione del nodo selezionato.
 *
 * I tasti valgono solo con il fuoco sul canvas (o su nessun controllo),
 * mai mentre si scrive, con testo selezionato o sotto una finestra aperta
 * (regola unica delle scorciatoie, utils/dialogStack).
 */
import React, { useEffect, useMemo } from 'react';
import useCanvasStore from '../../store/useCanvasStore';
import { useCanvasDerived } from '../../hooks/useCanvasDerived';
import { useOptionalToast } from '../../context/ToastContext';
import { isShortcutBlocked, isTypingTarget } from '../../utils/dialogStack';
import NodePalette from '../canvas/NodePalette';
import GuideModal from '../common/GuideModal';
import EmptyState from '../common/EmptyState';
import { nodeDescriptor, nodeIcon, nodeTitle } from '../canvas/nodeDescriptors';
import { EDITOR_GUIDES, EMPTY_CANVAS_HINTS, guideSectionFor } from '../../utils/guides/editorGuides';
import { canvasKeyDecision, commandById, commandForKey, runCanvasCommand, useCanvasUi } from './canvasCommands';
import './CanvasOverlays.css';

// Canvas caricato e vuoto (UI-2: dal grafo solo con useCanvasDerived).
const emptyLoadedCanvas = (s) => (s.nodes || []).length === 0 && s.graphLoaded !== false;

const onFlowTarget = (t) => Boolean(t?.closest?.('.react-flow'));
const onCanvasTarget = (t) => t === document.body || t === document.documentElement || onFlowTarget(t);
const hasTextSelection = () => {
  try { return Boolean(window.getSelection?.()?.toString()); } catch { return false; }
};

// Il primo ingresso libero del nodo di arrivo (multi-ingresso: input-0, input-1, ...).
function freeTargetHandle(target, edges) {
  const ins = nodeDescriptor(target.type).defaultHandles(target.data || {}).filter(h => h.type === 'target' && !String(h.id || '').startsWith('parameter:'));
  if (!ins.length) return undefined;
  const used = new Set(edges.filter(e => e.target === target.id).map(e => e.targetHandle ?? null));
  return (ins.find(h => !used.has(h.id ?? null)) || ins[0]).id ?? null;
}
const sourceHandleOf = (node) => nodeDescriptor(node.type).defaultHandles(node.data || {}).find(h => h.type === 'source')?.id ?? null;

// Collaudo live L6: seconda riga = il riassunto del nodo, lo stesso del
// canvas (condizione, alias, nome del file...): due passi dello stesso tipo
// si distinguono; senza riassunto, il tipo.
export const nodeItem = (n) => {
  const d = nodeDescriptor(n.type);
  return {
    id: n.id, kind: 'node', label: nodeTitle(n.type, n.data), icon: nodeIcon(n.type, n.data),
    desc: d.summary?.(n.data || {}) || d.kind?.(n.data || {}) || '',
  };
};

export default function CanvasOverlays({ mode, onConnect, onOpenPalette, canAdd }) {
  const toast = useOptionalToast();
  const picker = useCanvasUi(s => s.picker);
  const help = useCanvasUi(s => s.help);
  const isEmpty = useCanvasDerived(emptyLoadedCanvas, { on: 'structure' });
  const isReadOnly = useCanvasStore(s => s.isReadOnly);

  // Una sola regola dei tasti per tutti i comandi della tabella.
  useEffect(() => {
    const onKey = (e) => {
      if (e.defaultPrevented) return;
      const id = commandForKey(e);
      if (!id) return;
      const t = e.target;
      if (!onCanvasTarget(t) || isTypingTarget(t) || isShortcutBlocked(e)) return;
      if ((id === 'copy' || id === 'select_all') && hasTextSelection()) return;
      // B-K7: il tasto si prende solo se il comando parte (canvasKeyDecision).
      const decision = canvasKeyDecision(id, useCanvasStore.getState(), { onFlow: onFlowTarget(t) });
      if (!decision.take) {
        if (decision.warn && toast) toast.warning(decision.warn);
        return;
      }
      e.preventDefault();
      const res = runCanvasCommand(id);
      if (res.message && toast) (res.ok ? toast.success : toast.warning)(res.message);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [toast]);

  // Voci delle due finestre di scelta, lette all'apertura.
  const pickerGroups = useMemo(() => {
    if (!picker) return [];
    const st = useCanvasStore.getState();
    const nodes = (st.nodes || []).filter(n => n.type !== 'comment');
    if (picker === 'search') return [{ label: 'Nodi del canvas', items: nodes.map(nodeItem), limit: 200 }];
    const from = nodes.find(n => n.selected);
    if (!from) return [];
    const linked = new Set((st.edges || []).filter(e => e.source === from.id).map(e => e.target));
    return [{
      label: `Collega «${nodeTitle(from.type, from.data)}» a`,
      items: nodes.filter(n => n.id !== from.id && !linked.has(n.id) && freeTargetHandle(n, st.edges || []) !== undefined).map(nodeItem),
      limit: 200,
    }];
  }, [picker]);

  const onPickerPick = (item) => {
    const st = useCanvasStore.getState();
    if (picker === 'search') {
      // Stesso meccanismo di «Vai al passo» (store.setCanvasView + focusNodeId).
      const changes = st.nodes.filter(n => Boolean(n.selected) !== (n.id === item.id)).map(n => ({ id: n.id, type: 'select', selected: n.id === item.id }));
      if (changes.length) st.onNodesChange(changes);
      st.setCanvasView(st.canvasView, item.id);
      return;
    }
    const from = st.nodes.find(n => n.selected && n.type !== 'comment');
    const to = st.nodes.find(n => n.id === item.id);
    if (!from || !to || !onConnect) return;
    onConnect({ source: from.id, sourceHandle: sourceHandleOf(from), target: to.id, targetHandle: freeTargetHandle(to, st.edges || []) });
  };

  const guide = EDITOR_GUIDES[mode] || null;
  const helpSection = help ? (guideSectionFor(guide, help.section) || null) : null;
  const searching = picker === 'search';

  return (
    <>
      {isEmpty && !isReadOnly && (
        <div className="canvas-empty-guide">
          <EmptyState
            icon="✦"
            title="Il canvas è vuoto"
            text={EMPTY_CANVAS_HINTS[mode] || ''}
            action={(
              <>
                {canAdd && <button type="button" className="etb-btn etb-btn--primary" onClick={() => onOpenPalette?.()}>＋ Aggiungi</button>}
                {guide && <button type="button" className="etb-btn" onClick={() => useCanvasUi.getState().openHelp(null)}>📘 Guida</button>}
              </>
            )}
          />
        </div>
      )}
      <NodePalette
        open={Boolean(picker)}
        groups={pickerGroups}
        onPick={onPickerPick}
        onClose={() => useCanvasUi.getState().closePicker()}
        placeholder={searching ? 'Cerca un nodo per nome o tipo…' : 'Cerca il nodo da collegare…'}
        ariaLabel={searching ? commandById('search').name : commandById('connect').name}
        searchLabel={searching ? 'Cerca un nodo' : 'Cerca il nodo da collegare'}
        pickVerb={searching ? 'vai al nodo' : 'collega'}
      />
      <GuideModal guide={guide} isOpen={Boolean(help)} section={helpSection} onClose={() => useCanvasUi.getState().closeHelp()} />
    </>
  );
}
