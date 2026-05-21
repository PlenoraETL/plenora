import { useCallback, useRef, useState } from 'react';
import { useToast } from './useToast';

export function useCanvasSave(handleSaveQuiet, options = {}) {
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
    savingRef.current = true;
    setSaving(true);
    try {
      await handleSaveQuiet();
      toast.success(successMessage);
    } catch (err) {
      console.error(`${logPrefix} Errore salvataggio:`, err);
      toast.error(errorMessage);
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  }, [errorMessage, handleSaveQuiet, logPrefix, successMessage, toast]);

  return { saving, handleSaveCanvas };
}

export default useCanvasSave;
