/**
 * useDownload.js — un download con stato ed errore (Fase 5 T2, audit R9.14,
 * R9-N3). Ogni download delle pagine passa da qui: la promessa rifiutata
 * (410 risultato scaduto, 404, rete) diventa un messaggio, mai un errore
 * perso; un secondo clic durante il download dello stesso elemento non
 * parte (hooks/useBusy).
 *
 *   const dl = useDownload();
 *   <button disabled={dl.isBusy(id)} onClick={() => dl.run(id, () => downloadX(id))}>
 *   {dl.error && <p role="alert">{dl.error}</p>}
 *
 * `onError(message, err)` (es. toast) sostituisce l'errore in pagina.
 */
import { useCallback, useRef, useState } from 'react';
import { requestErrorMessage } from '../utils/errorMessages';
import { useBusy } from './useBusy';
import { useMountedRef } from './useMountedRef';

export function useDownload({ onError = null } = {}) {
  const busy = useBusy();
  const { run: runBusy } = busy;
  const [error, setError] = useState(null);
  const mountedRef = useMountedRef();
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;

  const run = useCallback(async (key, fn) => {
    setError(null);
    try {
      const r = await runBusy(key ?? 'download', fn);
      return !r.skipped;
    } catch (err) {
      console.warn('[useDownload] Download non riuscito:', err);
      const message = requestErrorMessage(err);
      if (mountedRef.current) {
        if (onErrorRef.current) onErrorRef.current(message, err);
        else setError(message);
      }
      return false;
    }
  }, [runBusy, mountedRef]);

  const clearError = useCallback(() => setError(null), []);

  return { run, isBusy: (key) => busy.isBusy(key ?? 'download'), busy: busy.any, error, clearError };
}

export default useDownload;
