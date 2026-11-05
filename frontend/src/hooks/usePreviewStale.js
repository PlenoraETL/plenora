/**
 * usePreviewStale — un'anteprima calcolata prima è ancora attuale? (Fase 5
 * T7, P27). Regola unica per l'anteprima del passo (StepPreview) e quella
 * dentro l'Output (ResultInlinePreview): `shownKey` è l'impronta del
 * contenuto del canvas (utils/canvasContentKey) quando l'anteprima è
 * partita; a ogni modifica del canvas, con un piccolo ritardo (niente
 * serializzazione a ogni frame di trascinamento), si confronta con quella
 * attuale.
 */
import { useEffect, useState } from 'react';
import useCanvasStore from '../store/useCanvasStore';
import { canvasContentKey } from '../utils/canvasContentKey';

const STALE_CHECK_MS = 200;

export const currentCanvasContentKey = () => {
  const { nodes, edges } = useCanvasStore.getState();
  return canvasContentKey(nodes, edges);
};

export function usePreviewStale(shownKey) {
  const [stale, setStale] = useState(false);
  useEffect(() => {
    setStale(false);
    if (!shownKey) return undefined;
    let timer = null;
    const unsubscribe = useCanvasStore.subscribe((s, prev) => {
      if (s.nodes === prev.nodes && s.edges === prev.edges) return;
      clearTimeout(timer);
      timer = setTimeout(() => setStale(currentCanvasContentKey() !== shownKey), STALE_CHECK_MS);
    });
    return () => { clearTimeout(timer); unsubscribe(); };
  }, [shownKey]);
  return stale;
}
