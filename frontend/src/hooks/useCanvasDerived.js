/**
 * useCanvasDerived — UNICO modo per un componente di leggere qualcosa di
 * calcolato dal grafo del canvas (UI-2, 06/11/2026; V4, V5, V6).
 *
 *   const hasOutput = useCanvasDerived(s => s.nodes.some(isOutput));
 *   const node = useCanvasDerived(s => pickNode(s, id), { on: ['content', 'selection'], deps: [id] });
 *
 * Il calcolo gira solo quando sale uno dei contatori scelti (store/
 * graphRevisions: 'structure' | 'content' | 'layout' | 'selection'; di
 * default il contenuto) o cambiano `deps`; a ogni altro aggiornamento dello
 * store (un fotogramma di trascinamento) il selettore restituisce il valore
 * gia' calcolato in tempo costante. Il risultato e' confrontato in modo
 * superficiale (oggetti e array a un livello): il componente si ridisegna
 * solo se il valore cambia davvero.
 *
 * Un test statico (tests/shared/canvasRender.contract) vieta nei componenti
 * i selettori sul grafo che scorrono o serializzano `nodes`/`edges` a ogni
 * aggiornamento e l'iscrizione a `s.nodes`/`s.edges` interi.
 */
import { useMemo, useRef } from 'react';
import useCanvasStore from '../store/useCanvasStore';
import { REVISION_FIELDS } from '../store/graphRevisions';

/** Uguaglianza superficiale: valori, array e oggetti piani a un livello. */
function shallowEqual(a, b) {
  if (Object.is(a, b)) return true;
  if (!a || !b || typeof a !== 'object' || typeof b !== 'object') return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  if (Array.isArray(a)) return a.length === b.length && a.every((v, i) => Object.is(v, b[i]));
  const ka = Object.keys(a);
  const kb = Object.keys(b);
  return ka.length === kb.length && ka.every(k => Object.prototype.hasOwnProperty.call(b, k) && Object.is(a[k], b[k]));
}

const fieldsOf = (on) => [].concat(on).map((level) => {
  const field = REVISION_FIELDS[level];
  if (!field) throw new Error(`useCanvasDerived: livello sconosciuto «${level}»`);
  return field;
});

/**
 * Selettore memorizzato sui contatori (anche fuori da React, es. nei test):
 * `select(state)` ricalcola `compute(state)` solo se i contatori di `on`
 * sono cambiati dall'ultima volta.
 */
function createDerivedSelector(compute, { on = 'content', equal = shallowEqual, store = null } = {}) {
  const fields = fieldsOf(on);
  let last = null;
  return (state) => {
    const s = state || store?.getState();
    const revs = fields.map(f => s[f]);
    if (last && last.state === s) return last.value;
    if (last && revs.every((r, i) => r === last.revs[i]) && last.projectId === s.projectId) {
      last = { ...last, state: s };
      return last.value;
    }
    const next = compute(s);
    const value = last && equal(last.value, next) ? last.value : next;
    last = { revs, value, state: s, projectId: s.projectId };
    return value;
  };
}

export function useCanvasDerived(compute, { on = 'content', deps = [] } = {}) {
  const computeRef = useRef(compute);
  computeRef.current = compute;
  const key = [].concat(on).join(',');
  // Un selettore nuovo solo quando cambiano livelli o dipendenze. `compute`
  // legge solo il grafo (nodes, edges, projectId): gli altri campi dello
  // store non fanno ricalcolare.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const select = useMemo(() => createDerivedSelector(s => computeRef.current(s), { on }), [key, ...deps]);
  return useCanvasStore(select);
}

export default useCanvasDerived;
