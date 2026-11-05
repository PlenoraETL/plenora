/**
 * useBusy.js — azioni in corso PER ELEMENTO (Fase 5 T2, audit R9.10, R9.11,
 * R9.16).
 *
 *   const busy = useBusy();
 *   <button disabled={busy.isBusy(id)} onClick={() => busy.run(id, () => testConnection(id))}>
 *
 * Regole:
 *  - l'elemento A e l'elemento B sono indipendenti: la fine di A non
 *    riabilita B (niente "id singolo" azzerato da chi finisce prima);
 *  - un secondo clic sullo stesso elemento mentre la sua azione gira non
 *    parte (run restituisce `{ skipped: true }`);
 *  - nessuno stato scritto dopo lo smontaggio.
 *
 * Gli errori restano a chi chiama: run() li rilancia.
 */
import { useCallback, useRef, useState } from 'react';
import { useMountedRef } from './useMountedRef';

export function useBusy() {
  const keysRef = useRef(new Set());
  const [keys, setKeys] = useState(() => new Set());
  const mountedRef = useMountedRef();

  const publish = useCallback(() => {
    if (mountedRef.current) setKeys(new Set(keysRef.current));
  }, [mountedRef]);

  const run = useCallback(async (key, fn) => {
    if (keysRef.current.has(key)) return { skipped: true };
    keysRef.current.add(key);
    publish();
    try {
      return { value: await fn() };
    } finally {
      keysRef.current.delete(key);
      publish();
    }
  }, [publish]);

  const isBusy = useCallback((key) => keys.has(key), [keys]);

  return { run, isBusy, any: keys.size > 0, keys };
}

export default useBusy;
