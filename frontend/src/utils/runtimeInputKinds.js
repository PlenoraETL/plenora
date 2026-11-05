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
import * as codecs from './fieldCodecs';

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

/**
 * Convertitore del valore di un input runtime (Fase 5 T5, R8.10): la stessa
 * regola del backend (eseguibili/runtime._coerce_scalar): tipo, lunghezze,
 * espressione regolare (corrispondenza completa), minimo/massimo, opzioni.
 * Il valore inviato e' quello convertito (42, true), non il testo ("42").
 * Il vuoto vale `null`: obbligatorio o default lo decide chi chiama.
 */
export function runtimeInputCodec(definition = {}) {
  const kind = String(definition.input_kind || definition.kind || 'text').toLowerCase();
  const v = definition.validation || {};
  const num = { min: v.minimum ?? undefined, max: v.maximum ?? undefined, optional: true };
  switch (kind) {
    case 'integer': return codecs.integer(num);
    case 'number': return codecs.decimal(num);
    case 'boolean': return codecs.boolean({ optional: true });
    case 'select': return codecs.oneOf(v.options || [], { optional: true });
    case 'date': return codecs.text({ pattern: /^\d{4}-\d{2}-\d{2}$/, patternMessage: 'Data nel formato AAAA-MM-GG', empty: null });
    case 'datetime': return codecs.text({
      pattern: /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})?$/,
      patternMessage: 'Data e ora non valide',
      empty: null,
    });
    case 'text': return codecs.text({
      trim: false,
      minLength: v.min_length ?? undefined,
      maxLength: v.max_length ?? undefined,
      pattern: v.pattern || undefined,
      patternMessage: 'Il valore non rispetta il formato richiesto',
      empty: null,
    });
    default: return codecs.text({ trim: false, empty: null });
  }
}
