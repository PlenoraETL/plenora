/**
 * useUnsavedChangesGuard.js — Guard contro perdita modifiche non salvate
 *
 * Combina due livelli di protezione:
 *  - `beforeunload` per refresh/close tab/back del browser (prompt nativo)
 *  - `confirmNavigation()` per navigazioni in-app (es. handleBack)
 *
 * Entrambi i path leggono `isDirty` da `useCanvasStore` via getState() —
 * niente subscription, nessun re-render dell'editor su ogni flip dirty.
 *
 * Uso tipico in un editor:
 *   const { confirmNavigation } = useUnsavedChangesGuard();
 *   const handleBack = () => {
 *     if (!confirmNavigation()) return;
 *     navigate('/');
 *   };
 */
import { useCallback, useEffect } from 'react';
import useCanvasStore from '../store/useCanvasStore';

const UNSAVED_MESSAGE = 'Hai modifiche non salvate. Vuoi davvero uscire?';

export function useUnsavedChangesGuard() {
  useEffect(() => {
    const handler = (event) => {
      const { isDirty } = useCanvasStore.getState();
      if (!isDirty) return undefined;
      event.preventDefault();
      // I browser moderni ignorano la stringa custom (mostrano un messaggio
      // generico) ma `returnValue` resta il modo standard per attivare il
      // prompt nativo cross-browser.
      event.returnValue = UNSAVED_MESSAGE;
      return UNSAVED_MESSAGE;
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, []);

  const confirmNavigation = useCallback(() => {
    const { isDirty } = useCanvasStore.getState();
    if (!isDirty) return true;
    return window.confirm(UNSAVED_MESSAGE);
  }, []);

  return { confirmNavigation };
}

export default useUnsavedChangesGuard;
