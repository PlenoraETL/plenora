/**
 * useNow — l'ora corrente aggiornata ogni `intervalMs` (Fase 5 T2, R4.16).
 *
 * Serve ai contatori di tempo trascorso: lo usa il SOLO componente che
 * mostra il tempo (es. un'etichetta "12s"), cosi' il tick non ridisegna
 * l'intera pagina. `enabled: false` = nessun timer.
 */
import { useEffect, useState } from 'react';

export function useNow(intervalMs = 1000, enabled = true) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!enabled) return undefined;
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs, enabled]);
  return now;
}

export default useNow;
