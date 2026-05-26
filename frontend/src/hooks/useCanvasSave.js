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
      // H-7 (2026-05-26): estrai dettaglio FastAPI/server per 422/409
      // invece di mostrare solo il generic errorMessage. Backend post
      // RAM refactor irrigidito alcuni contracts; senza detail l'utente
      // non sa quale nodo/campo correggere.
      const status = err?.response?.status;
      const serverDetail = err?.response?.data?.detail;
      let detailMsg = null;
      if (serverDetail) {
        // FastAPI puo' ritornare detail come stringa o lista (Pydantic
        // validation errors). Normalizziamo a stringa user-leggibile.
        if (typeof serverDetail === 'string') {
          detailMsg = serverDetail;
        } else if (Array.isArray(serverDetail)) {
          // [{loc: ['body', 'nodes', 0, ...], msg: '...', type: '...'}, ...]
          detailMsg = serverDetail
            .map((e) => {
              const loc = Array.isArray(e?.loc) ? e.loc.join('.') : '';
              return loc ? `${loc}: ${e.msg}` : e.msg;
            })
            .filter(Boolean)
            .join('; ');
        }
      }
      if (status === 422 && detailMsg) {
        toast.error(`Validazione fallita: ${detailMsg}`);
      } else if (status === 409 && detailMsg) {
        toast.error(`Conflitto: ${detailMsg}`);
      } else if (detailMsg) {
        toast.error(`${errorMessage}: ${detailMsg}`);
      } else {
        toast.error(errorMessage);
      }
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  }, [errorMessage, handleSaveQuiet, logPrefix, successMessage, toast]);

  return { saving, handleSaveCanvas };
}

export default useCanvasSave;
