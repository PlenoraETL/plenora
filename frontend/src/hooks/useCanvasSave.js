import { useCallback, useRef, useState } from 'react';
import { useToast } from './useToast';
import useCanvasStore from '../store/useCanvasStore';
import { hasUnsavedChanges } from '../store/saveState';
import { isCanvasVersionConflict } from '../utils/canvasVersion';
import { requestErrorMessage } from '../utils/errorMessages';

/**
 * "💾 Salva" e Ctrl+S. `save` e' serverActions.saveNow. Con niente da
 * salvare non chiama il server e lo dice (niente finto "salvato!").
 */
export function useCanvasSave(save, options = {}) {
  const {
    successMessage = 'Progetto salvato!',
    errorMessage = 'Errore durante il salvataggio',
    logPrefix = '[CanvasSave]',
  } = options;
  const toast = useToast();
  const savingRef = useRef(false);
  const [saving, setSaving] = useState(false);

  const handleSaveCanvas = useCallback(async () => {
    if (savingRef.current) return;
    if (!hasUnsavedChanges(useCanvasStore.getState()) && !useCanvasStore.getState().pendingSync) {
      toast.info?.('Tutto già salvato');
      return;
    }
    savingRef.current = true;
    setSaving(true);
    try {
      await save();
      toast.success(successMessage);
    } catch (err) {
      console.error(`${logPrefix} Errore salvataggio:`, err);
      // Versione superata: le scelte sono nell'avviso dell'editor
      // (SaveConflictNotice), qui solo che non e' stato salvato.
      if (err?.name === 'CanvasVersionConflictError' || isCanvasVersionConflict(err)) {
        toast.error('Non salvato: il progetto è stato salvato altrove dopo che l’hai aperto. Scegli come procedere nell’avviso in alto.');
        return;
      }
      // Il motivo (422 con i campi, 409, rete, ...) viene dalla
      // normalizzazione unica (utils/errorMessages).
      toast.error(`${errorMessage}: ${requestErrorMessage(err)}`);
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  }, [errorMessage, save, logPrefix, successMessage, toast]);

  return { saving, handleSaveCanvas };
}

export default useCanvasSave;
