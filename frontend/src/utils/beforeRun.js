/**
 * beforeRun — regola unica della finestra «Prima di avviare» (Blocco A,
 * regola 5; Fase 4, M7 "Esegui: un clic, finestra solo se serve").
 *
 * Per i tre editor c'e' UNA sola finestra prima dell'avvio
 * (editor/BeforeRunModal, aperta solo dal percorso unico editor/PipelineRunner).
 * Ha una sezione per ogni motivo; si apre se e solo se c'e' almeno un motivo,
 * altrimenti "▶ Esegui" parte con un clic:
 *
 * | motivo     | quando                                                      |
 * |------------|-------------------------------------------------------------|
 * | inputs     | input runtime da compilare (project_input non di tipo file) |
 * | staff      | opzioni dello staff (intermediate_mode, solo superadmin)    |
 * | estimate   | Arricchisci: la stima prevede chiamate API da confermare    |
 * |            | (chiamate > 0 o non conoscibili su almeno un nodo)          |
 * | blocking   | Arricchisci: la preparazione rifiuta il canvas o un nodo,   |
 * |            | oppure non e' stato possibile prepararlo                    |
 * | changed    | Arricchisci: l'avvio e' stato rifiutato (409                |
 * |            | preparation_changed): stima rifatta, motivo mostrato        |
 * | migrate    | un nodo Carica REST ha credenziali nel canvas da migrare    |
 * |            | (utils/restSecrets): Avvia spento finche' non si migra      |
 *
 * La stima di Arricchisci e' il preflight del server (POST /preflight) con
 * GLI STESSI input dell'esecuzione sul canvas salvato; l'impronta
 * (`preparation_fingerprint`) va all'avvio, che la verifica.
 *
 * Input impostati dal server («Utente che esegue», utils/runtimeInputKinds
 * source 'server'): la finestra li mostra in sola lettura con lo username di
 * chi e' collegato, ma non hanno valore, errore ne' payload: il browser non
 * li invia MAI (il server li rifiuterebbe), li mette il server.
 */
import { errorReason } from '../api/errors';
import { role } from './permissions';
import { isServerInjectedKind, runtimeInputCodec } from './runtimeInputKinds';
import { formatNumber } from './format';

export const BEFORE_RUN_REASONS = Object.freeze(['inputs', 'staff', 'estimate', 'blocking', 'changed', 'migrate']);

const PREPARATION_CHANGED = 'preparation_changed';

/** Avvio di Arricchisci rifiutato perche' la preparazione e' cambiata. */
export const isPreparationChanged = (err) =>
  err?.response?.status === 409 && errorReason(err) === PREPARATION_CHANGED;

/** Modalita' con la stima prima di avviare (preflight del server). */
export const needsPreflight = (mode) => mode === 'arricchisci';

const preflightNodes = (preflight) => (Array.isArray(preflight?.nodes) ? preflight.nodes : []);

/** Chiamate da confermare: piu' di zero, o non conoscibili, su almeno un nodo. */
const estimateNeedsConfirm = (preflight) =>
  preflightNodes(preflight).some(n => n?.api_calls == null || Number(n.api_calls) > 0);

/** La preparazione rifiuta il canvas o un nodo. */
export const preflightBlocks = (preflight) =>
  Boolean(preflight) && (preflight.valid === false || preflightNodes(preflight).some(n => n?.valid === false));

/**
 * Motivi per aprire la finestra (sottoinsieme ordinato di BEFORE_RUN_REASONS).
 * @param {{inputDefinitions?: Array, user?: Object, preflight?: Object|null,
 *          preflightError?: string|null, changedMessage?: string|null}} ctx
 */
export function beforeRunReasons({
  inputDefinitions = [], user = null, preflight = null, preflightError = null, changedMessage = null, migration = [],
} = {}) {
  const on = {
    migrate: (migration || []).length > 0,
    inputs: (inputDefinitions || []).length > 0,
    staff: role.isSuperadmin(user),
    estimate: estimateNeedsConfirm(preflight),
    blocking: Boolean(preflightError) || preflightBlocks(preflight),
    changed: Boolean(changedMessage),
  };
  return BEFORE_RUN_REASONS.filter(r => on[r]);
}

/** Input che il client compila (non quelli impostati dal server). */
const clientInputDefinitions = (inputDefinitions = []) =>
  (inputDefinitions || []).filter(d => !isServerInjectedKind(d.input_kind ?? d.kind));

/** Valori iniziali degli input runtime: i default dichiarati. */
export const initialInputValues = (inputDefinitions = []) => Object.fromEntries(
  clientInputDefinitions(inputDefinitions).map(d => [d.input_key, d.default_value ?? '']),
);

const blank = (v) => v === '' || v == null;

/**
 * Errore di ogni input (Fase 5 T5, R8.10): obbligatorio vuoto, oppure il
 * valore non passa il convertitore del suo tipo (utils/runtimeInputKinds,
 * stessa regola del backend). `{ [input_key]: messaggio }`.
 */
export function runInputErrors(inputDefinitions = [], values = {}) {
  const errors = {};
  clientInputDefinitions(inputDefinitions).forEach((d) => {
    const raw = values[d.input_key];
    if (blank(raw)) {
      if (d.required !== false) errors[d.input_key] = 'Campo obbligatorio';
      return;
    }
    const { error } = runtimeInputCodec(d).parse(raw);
    if (error) errors[d.input_key] = error;
  });
  return errors;
}

/** Tutti gli input compilati e validi. */
export const inputsComplete = (inputDefinitions = [], values = {}) =>
  Object.keys(runInputErrors(inputDefinitions, values)).length === 0;

/**
 * Input da mandare: solo quelli dichiarati (stesso payload di stima e avvio),
 * col valore CONVERTITO (42, true; non "42", "true").
 * `omitEmptyOptional` (Eseguibili): un input facoltativo vuoto non si manda,
 * cosi' vale il suo default dichiarato.
 */
export const runInputs = (inputDefinitions = [], values = {}, { omitEmptyOptional = false } = {}) => Object.fromEntries(
  clientInputDefinitions(inputDefinitions)
    .filter(d => !omitEmptyOptional || d.required !== false || !blank(values[d.input_key]))
    .map((d) => {
      const raw = values[d.input_key];
      if (blank(raw)) return [d.input_key, raw ?? ''];
      const { value, error } = runtimeInputCodec(d).parse(raw);
      return [d.input_key, error ? raw : value];
    }),
);

/** Chiave degli input di una stima: la stima vale solo per quegli input. */
export const inputsKey = (inputs = {}) => JSON.stringify(inputs || {});

// Testi per l'utente dei campi del preflight (righe in ingresso).
const INPUT_ROWS_STATUS_LABEL = Object.freeze({
  exact: 'esatte',
  estimated: 'stimate',
  unknown: 'non conoscibili',
});

/** Righe in ingresso di un nodo, come frase: "1.234 righe (esatte)". */
export function describeInputRows(node) {
  const status = node?.input_rows_status || (node?.input_rows == null ? 'unknown' : 'exact');
  if (status === 'unknown' || node?.input_rows == null) return 'righe in ingresso non conoscibili prima di avviare';
  const n = formatNumber(node.input_rows);
  const label = INPUT_ROWS_STATUS_LABEL[status] || status;
  const sampled = node.sampled ? `, su un campione di ${formatNumber(node.sampled_rows || 0)} righe` : '';
  return `${n} righe in ingresso (${label}${sampled})`;
}

/** Chiamate stimate di un nodo, come frase. */
export function describeApiCalls(node) {
  if (node?.api_calls == null) return 'chiamate non conoscibili prima di avviare';
  const n = Number(node.api_calls);
  return n === 1 ? '1 chiamata stimata' : `${formatNumber(n)} chiamate stimate`;
}
