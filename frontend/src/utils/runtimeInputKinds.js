/**
 * Tipi di input runtime (nodo «Input di progetto»): UNA tabella, copia della
 * tabella del backend (app/shared/execution/runtime_input_kinds.py). Un test
 * (tests/shared/runtimeInputKinds.contract.test.js) la confronta con il
 * contratto generato dal backend (backend/tests2/contracts/runtime_input_kinds.json).
 *
 * Per ogni tipo:
 *  - value: come entra nella catena ('file' = sorgente dati, 'scalar' =
 *    parametro di un nodo, 'connection' = connessione del progetto Estrai);
 *  - modes: i tipi di progetto in cui si puo' dichiarare;
 *  - source: chi fornisce il valore. 'client' = l'utente lo scrive in «Prima
 *    di avviare»; 'server' = lo mette SOLO il server con l'utente autenticato
 *    che avvia («Utente che esegue»): il browser non lo invia mai, la
 *    finestra lo mostra in sola lettura.
 *  - source_modes: dove un input 'scalar' puo' anche entrare COME SORGENTE
 *    (as_source: tabella di una riga, colonna source_column); il file e'
 *    sempre una sorgente.
 *
 * Il ruolo di un nodo (sorgente, parametro, connessione) si legge solo da
 * projectInputRole: maniglia, archi di collegamento, grafo, elenchi degli
 * input collegabili.
 *
 * Nel percorso PROGETTO (Esegui dall'editor):
 *  - i valori scalari del client valgono ovunque;
 *  - la connessione solo in Estrai;
 *  - il file usa quello gia' nel canvas (il caricamento vale negli Eseguibili);
 *  - un tipo 'server' blocca: dall'editor non c'e' un utente che esegue
 *    (si pubblica come Eseguibile; anteprima e Valida usano lo username di prova).
 */
import * as codecs from './fieldCodecs';

const ALL = Object.freeze(['estrai', 'manipola', 'arricchisci']);
const NONE = Object.freeze([]);
const kind = (name, value, { modes = ALL, source = 'client', sourceModes = NONE } = {}) => Object.freeze({
  kind: name, value, modes, source, source_modes: sourceModes,
});

export const RUNTIME_INPUT_KINDS = Object.freeze([
  kind('file', 'file', { sourceModes: ALL }),
  kind('text', 'scalar'),
  kind('integer', 'scalar'),
  kind('number', 'scalar'),
  kind('boolean', 'scalar'),
  kind('date', 'scalar'),
  kind('datetime', 'scalar'),
  kind('select', 'scalar'),
  kind('connection', 'connection', { modes: Object.freeze(['estrai']) }),
  kind('current_user', 'scalar', { source: 'server', sourceModes: Object.freeze(['manipola']) }),
]);

const BY_KIND = Object.freeze(Object.fromEntries(RUNTIME_INPUT_KINDS.map(k => [k.kind, k])));

/** Spec del tipo (senza tipo = file, come il backend). */
export const runtimeInputKindSpec = (value) => BY_KIND[String(value || 'file').toLowerCase()] || null;

/** Il valore lo mette il server («Utente che esegue»): mai inviato dal browser. */
export const isServerInjectedKind = (value) => runtimeInputKindSpec(value)?.source === 'server';

/** Il tipo puo' essere una sorgente (tabella di una riga) in questa modalita'. */
export const canBeSource = (mode, value) => {
  const spec = runtimeInputKindSpec(value);
  return Boolean(spec && spec.value === 'scalar' && spec.source_modes.includes(mode));
};

/**
 * Ruolo nel canvas di un nodo «Input di progetto» (stessa regola del backend,
 * runtime_input_kinds.project_input_role): 'source' | 'parameter' |
 * 'connection'; null per gli altri nodi.
 */
export function projectInputRole(node) {
  if (node?.type !== 'project_input') return null;
  const spec = runtimeInputKindSpec(node.data?.input_kind);
  if (!spec) return 'parameter';
  if (spec.value === 'file') return 'source';
  if (spec.value === 'connection') return 'connection';
  if (spec.source_modes.length && node.data?.as_source) return 'source';
  return 'parameter';
}

/** Maniglia in uscita del nodo: dati ('output'), parametri ('parameter') o nessuna. */
export const projectInputHandle = (data) => ({
  source: 'output', parameter: 'parameter', connection: null,
})[projectInputRole({ type: 'project_input', data })];

/** Tipi dichiarabili per modalita' (derivati dalla tabella). */
export const RUNTIME_INPUT_KINDS_BY_MODE = Object.freeze(Object.fromEntries(ALL.map(mode => [
  mode, Object.freeze(RUNTIME_INPUT_KINDS.filter(k => k.modes.includes(mode)).map(k => k.kind)),
])));

export const acceptsInputKind = (mode, value) => {
  const allowed = RUNTIME_INPUT_KINDS_BY_MODE[mode];
  return !allowed || allowed.includes(String(value || 'file').toLowerCase());
};

/** Motivo per cui un input blocca «Esegui» del progetto ('mode' | 'server' | null). */
export const projectRunBlockReason = (mode, value) => {
  if (!acceptsInputKind(mode, value)) return 'mode';
  if (isServerInjectedKind(value)) return 'server';
  return null;
};

/** Nodi project_input che bloccano «Esegui» del progetto in questa modalita'. */
export const unsupportedInputNodes = (nodes, mode) => (nodes || []).filter(n =>
  n?.type === 'project_input' && projectRunBlockReason(mode, n.data?.input_kind) !== null);

/**
 * Convertitore del valore di un input runtime (Fase 5 T5, R8.10): la stessa
 * regola del backend (eseguibili/runtime._coerce_scalar): tipo, lunghezze,
 * espressione regolare (corrispondenza completa), minimo/massimo, opzioni.
 * Il valore inviato e' quello convertito (42, true), non il testo ("42").
 * Il vuoto vale `null`: obbligatorio o default lo decide chi chiama.
 */
export function runtimeInputCodec(definition = {}) {
  const k = String(definition.input_kind || definition.kind || 'text').toLowerCase();
  const v = definition.validation || {};
  const num = { min: v.minimum ?? undefined, max: v.maximum ?? undefined, optional: true };
  switch (k) {
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
