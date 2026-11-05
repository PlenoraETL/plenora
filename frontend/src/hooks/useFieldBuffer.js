/**
 * useFieldBuffer — il buffer locale di un campo (Fase 5 T5, R8.1-R8.3, N4).
 *
 * Il campo tiene il TESTO mentre si scrive; il valore passa dal convertitore
 * del suo tipo (utils/fieldCodecs) e si consegna al padre (`onCommit`):
 *   - `commitOn: 'change'` (default): a ogni testo VALIDO; un testo non valido
 *     non si consegna e l'errore si vede (dopo il primo blur, o quando il form
 *     lo chiede con `scope.reveal()`);
 *   - `commitOn: 'blur'`: solo al blur o con Invio (campi il cui commit
 *     ristruttura i dati, per esempio la chiave di una mappatura).
 *
 * Il testo non si riscrive mai a ogni tasto: si risincronizza dalle props solo
 * quando il valore cambia DA FUORI (undo, un altro pannello, reset del form),
 * cioe' quando e' diverso da quello che il campo ha consegnato per ultimo.
 * Cosi' "file_" resta "file_" anche se il valore consegnato e' "file", e la
 * virgola appena digitata in un elenco resta. Al blur il testo valido si
 * mostra nella forma normalizzata (quella salvata).
 *
 * Un campo con testo non valido si registra nello scope del form
 * (FieldScope): Avanti/Salva lo controllano prima di procedere.
 */
import { createContext, useContext, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';

export const FieldScopeContext = createContext(null);

/**
 * Scope dei campi di un form: chi ha un testo non valido. `check()` lo dice
 * e rende visibili gli errori; il componente passa lo scope a <FieldScope>.
 */
export function useFieldScope() {
  const errors = useRef(new Map());
  const [revealed, setRevealed] = useState(false);
  return useMemo(() => ({
    revealed,
    set(id, error) {
      if (error) errors.current.set(id, error);
      else errors.current.delete(id);
    },
    remove(id) { errors.current.delete(id); },
    hasErrors: () => errors.current.size > 0,
    firstError: () => errors.current.values().next().value || null,
    reveal: () => setRevealed(true),
    /** true se tutti i campi sono validi; altrimenti mostra gli errori. */
    check() {
      if (errors.current.size === 0) return true;
      setRevealed(true);
      return false;
    },
  }), [revealed]);
}

export function useFieldBuffer({ value, codec, onCommit, commitOn = 'change', validate }) {
  const external = codec.format(value);
  // seen = ultimo valore visto dalle props; base = ultimo valore consegnato.
  const [state, setState] = useState(() => ({ text: external, base: external, seen: external, touched: false }));
  const scope = useContext(FieldScopeContext);
  const fieldId = useId();
  const idRef = useRef(fieldId);

  // Valore cambiato da fuori (diverso da quello consegnato): vince. Stato
  // derivato dalle props, senza effetto; il ritorno del proprio commit no.
  let current = state;
  if (external !== state.seen) {
    current = external === state.base
      ? { ...state, seen: external }
      : { text: external, base: external, seen: external, touched: false };
    setState(current);
  }

  const parsed = codec.parse(current.text);
  const error = parsed.error || (validate ? validate(parsed.value, current.text) : null) || null;

  useLayoutEffect(() => {
    if (!scope) return undefined;
    scope.set(idRef.current, error);
    return undefined;
  }, [scope, error]);
  useLayoutEffect(() => () => scope?.remove(idRef.current), [scope]);

  const deliver = (result, nextText, extra = {}) => {
    const formatted = codec.format(result.value);
    setState(s => ({ ...s, ...extra, text: nextText ?? s.text, base: formatted }));
    if (formatted !== external) onCommit?.(result.value);
  };

  const validNow = (text) => {
    const r = codec.parse(text);
    if (r.error) return null;
    if (validate && validate(r.value, text)) return null;
    return r;
  };

  const onChange = (eventOrText) => {
    const text = typeof eventOrText === 'string' ? eventOrText : eventOrText?.target?.value ?? '';
    if (commitOn === 'change') {
      const r = validNow(text);
      if (r) { deliver(r, text); return; }
    }
    setState(s => ({ ...s, text }));
  };

  const commit = () => {
    const r = validNow(current.text);
    if (!r) { setState(s => ({ ...s, touched: true })); return false; }
    deliver(r, codec.format(r.value), { touched: true });
    return true;
  };

  const onKeyDown = (event) => {
    if (event.key === 'Enter' && event.target?.tagName !== 'TEXTAREA') commit();
  };

  const showError = Boolean(error) && (current.touched || Boolean(scope?.revealed));
  return {
    text: current.text,
    error,
    showError,
    commit,
    inputProps: { value: current.text, onChange, onBlur: commit, onKeyDown },
  };
}
