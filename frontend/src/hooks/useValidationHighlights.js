/**
 * useValidationHighlights — applica/rimuove outline amber ai nodi con
 * issue di validazione.
 *
 * Pre-fix la stessa useEffect era duplicata letteralmente in
 * ManipolaEditor (righe 313-330) e ArricchisciEditor (righe 314-330):
 * 18 righe identiche × 2 file. Modifica del color/shape richiedeva
 * edit sincronizzati.
 *
 * Lo store espone `validationHighlights` come array di nodeId. Quando
 * cambia, il hook patcha lo `style` dei nodi corrispondenti come dato
 * DERIVATO (`applyDerivedNodes`: non marca dirty). Quando si svuota, rimuove gli style con
 * `border.includes('#FBBF24')` per non disturbare style aggiunti per
 * altre ragioni.
 *
 * Uso:
 * ```jsx
 * const { applyDerivedNodes } = useCanvasCore(...);
 * useValidationHighlights(applyDerivedNodes);
 * ```
 */
import { useEffect } from 'react';
import useCanvasStore from '../store/useCanvasStore';

const HIGHLIGHT_BORDER = '2px solid #FBBF24';
const HIGHLIGHT_SHADOW = '0 0 8px rgba(251,191,36,0.25)';
const HIGHLIGHT_BORDER_SIGNATURE = '#FBBF24';

// Restituisce lo stesso array se nessun nodo cambia (niente re-render inutili).
const mapNodesIfChanged = (nds, fn) => {
  let changed = false;
  const next = nds.map(n => { const m = fn(n); if (m !== n) changed = true; return m; });
  return changed ? next : nds;
};

export function useValidationHighlights(applyDerivedNodes) {
  const validationHighlights = useCanvasStore(s => s.validationHighlights);

  useEffect(() => {
    if (!validationHighlights || validationHighlights.length === 0) {
      // Rimuovi eventuali highlight precedenti senza toccare style
      // aggiunti per altre ragioni.
      applyDerivedNodes(nds => mapNodesIfChanged(nds, n => {
        if (!n.style?.border?.includes(HIGHLIGHT_BORDER_SIGNATURE)) return n;
        const { border, boxShadow, ...restStyle } = n.style || {};
        return { ...n, style: Object.keys(restStyle).length ? restStyle : undefined };
      }));
      return;
    }
    const highlightSet = new Set(validationHighlights);
    applyDerivedNodes(nds => mapNodesIfChanged(nds, n => (
      highlightSet.has(n.id) && n.style?.border !== HIGHLIGHT_BORDER
        ? { ...n, style: { ...n.style, border: HIGHLIGHT_BORDER, boxShadow: HIGHLIGHT_SHADOW } }
        : n
    )));
  }, [validationHighlights, applyDerivedNodes]);
}

export default useValidationHighlights;
