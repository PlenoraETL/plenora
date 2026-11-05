/**
 * useStoreDraft — bozza di un campo del nodo che vive SOLO mentre lo si
 * modifica (Fase 5, T6, R1.9).
 *
 * Regola: il valore mostrato e' quello dello store; la bozza esiste solo tra
 * `begin()` e `commit()`/`cancel()`, e parte dal valore dello store di quel
 * momento. Se lo store cambia sotto la bozza (Ctrl+Z, Ctrl+Y, ripristino di
 * una versione, ricaricamento) la bozza si scarta da sola: il campo torna a
 * mostrare lo store e `commit()` non riscrive il valore vecchio.
 *
 * Nessun nodo del canvas tiene copie locali di `data` fuori da qui: lo
 * verifica tests/shared/nodeLocalState.contract.test.jsx.
 *
 *   const alias = useStoreDraft(data.alias || '');
 *   alias.editing  alias.value  alias.begin()  alias.change(v)
 *   alias.commit(v => updateNodeData(id, { alias: v }))  alias.cancel()
 */
import { useCallback, useEffect, useState } from 'react';

const same = (a, b) => a === b || JSON.stringify(a) === JSON.stringify(b);

export function useStoreDraft(storeValue) {
  const [draft, setDraft] = useState(null);
  // Bozza valida solo se lo store e' ancora quello da cui e' partita.
  const editing = draft !== null && same(draft.base, storeValue);
  const value = editing ? draft.value : storeValue;

  // Lo store e' cambiato sotto la bozza: si scarta (anche se poi tornasse
  // uguale con un Ctrl+Y, la bozza vecchia non riappare).
  useEffect(() => {
    if (draft !== null && !same(draft.base, storeValue)) setDraft(null);
  }, [draft, storeValue]);

  const begin = useCallback(() => setDraft({ base: storeValue, value: storeValue }), [storeValue]);
  /** Inizia da un valore noto dello store (es. un elemento appena scelto). */
  const beginWith = useCallback((base) => setDraft({ base, value: base }), []);
  const change = useCallback((next) => setDraft(d => ({
    base: d && same(d.base, storeValue) ? d.base : storeValue,
    value: next,
  })), [storeValue]);
  const cancel = useCallback(() => setDraft(null), []);
  const commit = useCallback((write) => {
    if (editing && !same(draft.value, storeValue)) write(draft.value);
    setDraft(null);
  }, [editing, draft, storeValue]);

  return { editing, value, begin, beginWith, change, cancel, commit };
}

export default useStoreDraft;
