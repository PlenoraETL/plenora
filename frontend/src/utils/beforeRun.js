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
 *
 * La stima di Arricchisci e' il preflight del server (POST /preflight) con
 * GLI STESSI input dell'esecuzione sul canvas salvato; l'impronta
 * (`preparation_fingerprint`) va all'avvio, che la verifica.
 */
import { errorReason } from '../api/errors';
import { role } from './permissions';

export const BEFORE_RUN_REASONS = Object.freeze(['inputs', 'staff', 'estimate', 'blocking', 'changed']);

export const PREPARATION_CHANGED = 'preparation_changed';

/** Avvio di Arricchisci rifiutato perche' la preparazione e' cambiata. */
export const isPreparationChanged = (err) =>
  err?.response?.status === 409 && errorReason(err) === PREPARATION_CHANGED;

/** Modalita' con la stima prima di avviare (preflight del server). */
export const needsPreflight = (mode) => mode === 'arricchisci';

const preflightNodes = (preflight) => (Array.isArray(preflight?.nodes) ? preflight.nodes : []);

/** Chiamate da confermare: piu' di zero, o non conoscibili, su almeno un nodo. */
export const estimateNeedsConfirm = (preflight) =>
  preflightNodes(preflight).some(n => n?.api_calls == null || Number(n.api_calls) > 0);

/** La preparazione rifiuta il canvas o un nodo. */
export const preflightBlocks = (preflight) =>
  Boolean(preflight) && (preflight.valid === false || preflightNodes(preflight).some(n => n?.valid === false));

/**
 * Motivi per aprire la finestra (sottoinsieme ordinato di BEFORE_RUN_REASONS).
 * @param {{inputDefinitions?: Array, user?: Object, preflight?: Object|null,
 *          preflightError?: string|null, changedMessage?: string|null}} ctx
 */
export function beforeRunReasons({ inputDefinitions = [], user = null, preflight = null, preflightError = null, changedMessage = null } = {}) {
  const on = {
    inputs: (inputDefinitions || []).length > 0,
    staff: role.isSuperadmin(user),
    estimate: estimateNeedsConfirm(preflight),
    blocking: Boolean(preflightError) || preflightBlocks(preflight),
    changed: Boolean(changedMessage),
  };
  return BEFORE_RUN_REASONS.filter(r => on[r]);
}

/** Valori iniziali degli input runtime: i default dichiarati. */
export const initialInputValues = (inputDefinitions = []) => Object.fromEntries(
  (inputDefinitions || []).map(d => [d.input_key, d.default_value ?? '']),
);

/** Tutti gli input obbligatori compilati. */
export const inputsComplete = (inputDefinitions = [], values = {}) => (inputDefinitions || []).every(d => (
  d.required === false || (values[d.input_key] !== '' && values[d.input_key] != null)
));

/** Input da mandare: solo quelli dichiarati (stesso payload di stima e avvio). */
export const runInputs = (inputDefinitions = [], values = {}) => Object.fromEntries(
  (inputDefinitions || []).map(d => [d.input_key, values[d.input_key] ?? '']),
);

/** Chiave degli input di una stima: la stima vale solo per quegli input. */
export const inputsKey = (inputs = {}) => JSON.stringify(inputs || {});

// Testi per l'utente dei campi del preflight (righe in ingresso).
export const INPUT_ROWS_STATUS_LABEL = Object.freeze({
  exact: 'esatte',
  estimated: 'stimate',
  unknown: 'non conoscibili',
});

/** Righe in ingresso di un nodo, come frase: "1.234 righe (esatte)". */
export function describeInputRows(node) {
  const status = node?.input_rows_status || (node?.input_rows == null ? 'unknown' : 'exact');
  if (status === 'unknown' || node?.input_rows == null) return 'righe in ingresso non conoscibili prima di avviare';
  const n = Number(node.input_rows).toLocaleString('it-IT');
  const label = INPUT_ROWS_STATUS_LABEL[status] || status;
  const sampled = node.sampled ? `, su un campione di ${Number(node.sampled_rows || 0).toLocaleString('it-IT')} righe` : '';
  return `${n} righe in ingresso (${label}${sampled})`;
}

/** Chiamate stimate di un nodo, come frase. */
export function describeApiCalls(node) {
  if (node?.api_calls == null) return 'chiamate non conoscibili prima di avviare';
  const n = Number(node.api_calls);
  return n === 1 ? '1 chiamata stimata' : `${n.toLocaleString('it-IT')} chiamate stimate`;
}
