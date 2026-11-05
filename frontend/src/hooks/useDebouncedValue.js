/**
 * useDebouncedValue — il valore dopo `ms` di quiete (Fase 5 T2, R4.4).
 *
 * Serve come CHIAVE di hooks/useResource per le richieste che seguono la
 * digitazione (es. traduzione SQL del ColumnBuilder): la richiesta parte
 * dopo la pausa, e l'id di richiesta di useResource fa vincere l'ultima.
 * Confronto per contenuto (JSON): un oggetto nuovo uguale non riparte.
 */
import { useEffect, useState } from 'react';

export function useDebouncedValue(value, ms = 300) {
  const serialized = JSON.stringify(value ?? null);
  const [debounced, setDebounced] = useState(serialized);
  useEffect(() => {
    if (serialized === debounced) return undefined;
    const timer = setTimeout(() => setDebounced(serialized), ms);
    return () => clearTimeout(timer);
  }, [serialized, debounced, ms]);
  return { value: JSON.parse(debounced), settled: serialized === debounced };
}

export default useDebouncedValue;
