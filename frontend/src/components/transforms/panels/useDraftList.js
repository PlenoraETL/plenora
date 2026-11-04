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
 *
 * `update(next)` accetta la lista nuova oppure una funzione
 * `(correnti) => nuova`: la funzione riceve le righe CORRENTI al momento
 * della chiamata (non quelle di un render passato), cosi' un risultato che
 * arriva dopo un'attesa (es. «Riempi con i valori presenti») si applica
 * alle modifiche fatte nel frattempo invece di cancellarle.
 *
 * REGOLA (F3, seconda correzione): tutto lo stato che `update` legge viene
 * da ref CORRENTI, mai dalla chiusura del render in cui e' stata presa
 * (un `update` preso al clic e chiamato dopo un'attesa vale quanto uno
 * fresco): righe correnti, chiave dell'ultimo salvato (scritto da qui o
 * arrivato da fuori) e `validate`/`toSaved`/`write` dell'ultimo render.
 * Nessuna scrittura se il salvato non cambia rispetto all'ultimo salvato.
 */
import { useEffect, useRef, useState } from 'react';

export default function useDraftList(saved, { validate, toSaved, write }) {
  const [rows, setRows] = useState(saved);
  // Righe correnti, aggiornate a ogni scrittura: base di `update(fn)`.
  const current = useRef(saved);
  const [error, setError] = useState(null);
  // Confronto sempre in forma normalizzata (toSaved), da entrambe le parti.
  const savedKey = JSON.stringify(toSaved(saved));
  // Chiave dell'ultimo salvato: scritto da qui o arrivato da fuori.
  const lastSeen = useRef(savedKey);
  // Opzioni dell'ultimo render.
  const options = useRef({ validate, toSaved, write });
  options.current = { validate, toSaved, write };

  useEffect(() => {
    if (savedKey !== lastSeen.current) {
      lastSeen.current = savedKey;
      current.current = saved;
      setRows(saved);
      setError(null);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [savedKey]);

  const update = (nextOrFn) => {
    const opts = options.current;
    const next = typeof nextOrFn === 'function' ? nextOrFn(current.current) : nextOrFn;
    current.current = next;
    setRows(next);
    const problem = opts.validate(next);
    setError(problem);
    if (problem) return;
    const out = opts.toSaved(next);
    const key = JSON.stringify(out);
    if (key === lastSeen.current) return;
    lastSeen.current = key;
    opts.write(out);
  };

  return [rows, update, error];
}
