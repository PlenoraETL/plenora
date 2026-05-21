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
 * cambia, il hook patcha lo `style` dei nodi corrispondenti via
 * `setNodes`. Quando si svuota, rimuove gli style con
 * `border.includes('#FBBF24')` per non disturbare style aggiunti per
 * altre ragioni.
 *
 * Uso:
 * ```jsx
 * const { nodes, edges, setNodes } = useCanvasCore(...);
 * useValidationHighlights(setNodes);
 * ```
 */
import { useEffect } from 'react';
import useCanvasStore from '../store/useCanvasStore';

const HIGHLIGHT_BORDER = '2px solid #FBBF24';
const HIGHLIGHT_SHADOW = '0 0 8px rgba(251,191,36,0.25)';
const HIGHLIGHT_BORDER_SIGNATURE = '#FBBF24';

export function useValidationHighlights(setNodes) {
  const validationHighlights = useCanvasStore(s => s.validationHighlights);

  useEffect(() => {
    if (!validationHighlights || validationHighlights.length === 0) {
      // Rimuovi eventuali highlight precedenti senza toccare style
      // aggiunti per altre ragioni.
      setNodes(nds => nds.map(n => {
        if (!n.style?.border?.includes(HIGHLIGHT_BORDER_SIGNATURE)) return n;
        const { border, boxShadow, ...restStyle } = n.style || {};
        return { ...n, style: Object.keys(restStyle).length ? restStyle : undefined };
      }));
      return;
    }
    const highlightSet = new Set(validationHighlights);
    setNodes(nds => nds.map(n => ({
      ...n,
      style: highlightSet.has(n.id)
        ? { ...n.style, border: HIGHLIGHT_BORDER, boxShadow: HIGHLIGHT_SHADOW }
        : n.style,
    })));
  }, [validationHighlights, setNodes]);
}

export default useValidationHighlights;
