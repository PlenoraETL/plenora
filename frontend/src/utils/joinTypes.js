/**
 * joinTypes — catalogo UNICO dei tipi di Join dei tre editor (UI-3, U21).
 *
 * Prima ogni pannello aveva la sua lista: «INNER — Solo le righe…» nel join
 * di Estrai, «Solo righe con corrispondenza (Inner)» nel Join di Manipola,
 * «Inner (solo corrispondenze)» nel Multi-Join; e il tipo di un Join nuovo
 * era Inner dalla palette e Left da «Collega». Ora:
 *
 *  - ogni tipo ha UNA etichetta e una descrizione, uguali ovunque;
 *  - `values` dice come si chiama per ogni motore (il backend usa nomi
 *    diversi: `full` nel SQL di Estrai, `outer` in Manipola) e in quali
 *    motori esiste;
 *  - DEFAULT_JOIN_TYPE e' il tipo di OGNI Join nuovo (palette, «Collega»,
 *    trascinamento, Multi-Join): Left, che non perde righe di sinistra;
 *  - MISSING e' il tipo che il backend usa quando un Join salvato non lo
 *    dice: la lettura di un progetto esistente resta quella di prima.
 *
 * Il test tests/shared/joinTypes.contract.test.js vieta elenchi di tipi
 * scritti a mano negli editor e confronta i valori con il backend.
 */

const JOIN_ENGINES = ['estrai', 'manipola', 'multi_join'];

export const JOIN_TYPES = Object.freeze([
  {
    id: 'left', label: 'Tieni tutte le righe di sinistra', short: 'Left',
    description: 'Tutte le righe di sinistra, con le corrispondenze di destra',
    values: { estrai: 'left', manipola: 'left', multi_join: 'left' },
  },
  {
    id: 'inner', label: 'Solo righe con corrispondenza', short: 'Inner',
    description: 'Solo le righe che trovano una corrispondenza in entrambi i lati',
    values: { estrai: 'inner', manipola: 'inner', multi_join: 'inner' },
  },
  {
    id: 'right', label: 'Tieni tutte le righe di destra', short: 'Right',
    description: 'Tutte le righe di destra, con le corrispondenze di sinistra',
    values: { estrai: 'right', manipola: 'right' },
  },
  {
    id: 'full', label: 'Tutte le righe dei due lati', short: 'Full',
    description: 'Tutte le righe di entrambi i lati, abbinate dove corrispondono',
    values: { estrai: 'full', manipola: 'outer', multi_join: 'outer' },
  },
]);

/** Tipo (id del catalogo) di ogni Join nuovo. */
const DEFAULT_JOIN_TYPE = 'left';

/** Tipo usato dal backend quando un Join salvato non lo dice (lettura invariata). */
export const MISSING_JOIN_TYPE = Object.freeze({ estrai: 'left', manipola: 'inner', multi_join: 'inner' });

const forEngine = (engine) => {
  if (!JOIN_ENGINES.includes(engine)) throw new Error(`[joinTypes] motore sconosciuto: ${engine}`);
  return JOIN_TYPES.filter(t => t.values[engine]);
};

/** Valore del tipo di un Join nuovo per un motore. */
export const newJoinType = (engine) => JOIN_TYPES.find(t => t.id === DEFAULT_JOIN_TYPE).values[engine];

/** Valore effettivo di un Join salvato (assente = quello del backend). */
export const readJoinType = (engine, value) => value || MISSING_JOIN_TYPE[engine];

/** Opzioni [valore, etichetta] per un menu a tendina. */
export const joinTypeOptions = (engine) => forEngine(engine).map(t => [t.values[engine], `${t.label} (${t.short})`]);

/** Tipo del catalogo per un valore di un motore (null se sconosciuto). */
const joinTypeOf = (engine, value) => forEngine(engine).find(t => t.values[engine] === readJoinType(engine, value)) || null;

/** Nome breve mostrato nei riepiloghi («Left Join»); un valore sconosciuto si mostra com'e'. */
export function joinTypeName(engine, value) {
  const t = joinTypeOf(engine, value);
  return t ? `${t.short} Join` : `${String(readJoinType(engine, value)).toUpperCase()} JOIN`;
}
