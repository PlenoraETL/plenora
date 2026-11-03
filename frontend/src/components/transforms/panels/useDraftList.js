/**
 * useDraftList — elenco modificabile nel pannello con UNA regola di
 * scrittura: la configurazione si aggiorna solo quando l'intera lista e'
 * valida (`validate(rows)` restituisce null). Altrimenti resta l'ultima
 * versione valida e l'hook restituisce l'errore da mostrare.
 *
 *  - `saved`: elenco come sta nella configurazione (gia' risolto);
 *  - `toSaved(rows)`: elenco da scrivere (normalizzato);
 *  - `write(list)`: scrittura.
 *
 * Le bozze vivono nel componente; NodeInspector monta il form con
 * key={node.id}, quindi non passano da un nodo all'altro. Se la
 * configurazione cambia da fuori (annulla, ripristino versione) le righe
 * ripartono da quella; i cambi scritti da qui non le toccano.
 */
import { useEffect, useRef, useState } from 'react';

export default function useDraftList(saved, { validate, toSaved, write }) {
  const [rows, setRows] = useState(saved);
  const [error, setError] = useState(null);
  // Confronto sempre in forma normalizzata (toSaved), da entrambe le parti.
  const savedKey = JSON.stringify(toSaved(saved));
  const lastSeen = useRef(savedKey);

  useEffect(() => {
    if (savedKey !== lastSeen.current) {
      lastSeen.current = savedKey;
      setRows(saved);
      setError(null);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [savedKey]);

  const update = (next) => {
    setRows(next);
    const problem = validate(next);
    setError(problem);
    if (problem) return;
    const out = toSaved(next);
    const key = JSON.stringify(out);
    if (key === savedKey) return;
    lastSeen.current = key;
    write(out);
  };

  return [rows, update, error];
}
