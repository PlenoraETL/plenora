/**
 * Tipi di input runtime accettati nel percorso PROGETTO (esecuzione
 * dall'editor), per modalita'. Copia della regola del backend
 * (orchestrator/runtime_inputs.py + manifest, Fase 4 M7):
 *  - i valori scalari valgono ovunque;
 *  - la connessione solo in Estrai (Manipola e Arricchisci la rifiutano);
 *  - il file usa quello gia' nel canvas (il caricamento vale negli Eseguibili).
 * Una sola tabella: la usano il pannello dell'input (tipi proponibili) e il
 * controllo prima di "Esegui".
 */
const SCALARS = ['text', 'integer', 'number', 'boolean', 'date', 'datetime', 'select'];

export const RUNTIME_INPUT_KINDS_BY_MODE = Object.freeze({
  estrai: Object.freeze(['file', ...SCALARS, 'connection']),
  manipola: Object.freeze(['file', ...SCALARS]),
  arricchisci: Object.freeze(['file', ...SCALARS]),
});

export const acceptsInputKind = (mode, kind) => {
  const allowed = RUNTIME_INPUT_KINDS_BY_MODE[mode];
  return !allowed || allowed.includes(String(kind || 'file').toLowerCase());
};

/** Nodi project_input con un tipo che questa modalita' non accetta. */
export const unsupportedInputNodes = (nodes, mode) => (nodes || []).filter(n =>
  n?.type === 'project_input' && !acceptsInputKind(mode, n.data?.input_kind));
