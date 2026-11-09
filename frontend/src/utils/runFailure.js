/**
 * runFailure — cosa dire e cosa offrire quando un'esecuzione fallisce
 * (UI-3, U16). Prima la finestra diceva solo il messaggio: niente passo
 * fallito, niente registro, niente segnalazione.
 *
 * Una regola per i tre editor e per gli Eseguibili:
 *  - failedStepOf: il passo che ha fallito. Prima l'id che il backend mette
 *    nell'esito (`error_node_id`: Arricchisci; Manipola dalla stessa data),
 *    poi un id o un titolo di nodo citato nel messaggio. null se non si sa:
 *    mai un passo indovinato a caso.
 *  - runLogPath: il registro delle esecuzioni filtrato sul progetto e sui
 *    fallimenti (LogViewer, scheda Esecuzioni).
 *  - failureTicketDraft: il ticket precompilato (progetto, esecuzione,
 *    passo, messaggio) per «Segnala».
 */
import { nodeTitle } from '../components/canvas/nodeDescriptors';
import { humanizeError } from './errorMessages';
import { citedStep, withStepTitles } from './stepTitles';
import { can } from './permissions';
import { formatNumber } from './format';

/*
 * Conferma UI (B-K6), tre regole in piu':
 *  - editorStepsFor: i passi in cui cercare quello fallito sono SOLO quelli
 *    dell'editor aperto sullo STESSO progetto dell'esecuzione (fuori
 *    dall'editor, o con lo store di un altro progetto: nessuno);
 *  - failureActions: «Apri il registro» e «Segnala» solo a chi li puo' usare
 *    (can.readRunLog, can.createTicket: l'esecutore no, il backend
 *    risponderebbe 403); «Vai al passo» solo con un passo trovato;
 *  - failureMessage: il messaggio del ticket e' quello mostrato nella
 *    finestra (humanizeError), mai il testo grezzo del backend.
 */

/** Nodi dell'editor per l'esecuzione di `projectId` (store del canvas), o []. */
export function editorStepsFor(projectId, canvasState) {
  if (projectId == null || canvasState?.projectId == null) return [];
  return String(canvasState.projectId) === String(projectId) ? (canvasState.nodes || []) : [];
}

/** Azioni offerte per un'esecuzione fallita. */
export function failureActions(user, { step = null } = {}) {
  return { goToStep: Boolean(step), openLog: can.readRunLog(user), report: can.createTicket(user) };
}

/**
 * Il messaggio di un'esecuzione fallita, come lo mostra la finestra: i passi
 * del canvas aperto col loro titolo, mai l'id (collaudo live L4).
 */
export const failureMessage = (result, nodes = []) => withStepTitles(humanizeError(result).message, nodes);

const quoted = (text) => [...String(text || '').matchAll(/[«"'“]([^»"'”]{2,80})[»"'”]/g)].map(m => m[1].trim());

/**
 * Il testo di un'esecuzione riuscita (collaudo live L4): dai dati strutturati
 * dell'esito (righe, file), numeri formattati qui; mai il messaggio del
 * backend («Salvato 238044 righe in …»).
 */
// Residui collaudo live M6: i campi con cui i tre esecutori dicono righe e
// file (Arricchisci rest_file: `output_filename`, senza righe), il primo
// presente vince. Un esito senza righe dice comunque il file.
const SUCCESS_FIELDS = Object.freeze({
  rows: Object.freeze(['rows_count', 'rows_processed']),
  file: Object.freeze(['output_file', 'output_filename']),
});
const firstOf = (result, keys) => keys.map(k => result?.[k]).find(v => v != null && v !== '') ?? null;

export function runSuccessText(result) {
  // Un file restituito cosi' com'e' (`output_type: 'file'`) non ha righe contate.
  const rows = result?.output_type === 'file' ? null : firstOf(result, SUCCESS_FIELDS.rows);
  const file = firstOf(result, SUCCESS_FIELDS.file);
  if (rows == null) return file ? `Risultato salvato in «${file}».` : 'Operazione eseguita con successo';
  const what = Number(rows) === 1 ? '1 riga salvata' : `${formatNumber(rows)} righe salvate`;
  return file ? `${what} in «${file}».` : `${what}.`;
}

/** { id, title } del passo fallito, o null. */
export function failedStepOf(result, nodes = []) {
  if (!result || result.success) return null;
  const list = (nodes || []).filter(n => n && n.type !== 'comment');
  const byId = (id) => list.find(n => n.id === id);
  const declared = result.error_node_id || result.result?.error_node_id || null;
  let node = declared ? byId(String(declared)) : null;
  const message = String(result.message || result.error || '');
  if (!node && message) node = citedStep(message, list);
  if (!node && message) {
    const names = quoted(message);
    node = list.find(n => names.includes(nodeTitle(n.type, n.data))) || null;
  }
  return node ? { id: node.id, title: nodeTitle(node.type, node.data) } : null;
}

/** Registro delle esecuzioni filtrato (scheda Esecuzioni del LogViewer). */
export function runLogPath({ projectId = null, status = 'failure' } = {}) {
  const params = new URLSearchParams();
  params.set('tab', 'runs');
  if (projectId) params.set('project', String(projectId));
  if (status) params.set('status', status);
  return `/logs?${params.toString()}`;
}

/** Ticket precompilato per «Segnala»: titolo, descrizione e categoria. */
export function failureTicketDraft({ projectName = '', projectId = null, projectType = '', result = null, step = null }) {
  const message = failureMessage(result);
  const lines = [
    `Progetto: ${projectName || '—'}${projectId ? ` (id ${projectId})` : ''}${projectType ? `, ${projectType}` : ''}`,
    `Esecuzione: ${result?.task_id || '—'}`,
    step ? `Passo fallito: ${step.title} (${step.id})` : null,
    `Messaggio: ${message}`,
    result?.code ? `Codice: ${result.code}` : null,
    '',
    'Cosa stavo facendo:',
  ].filter(l => l !== null);
  return {
    title: `Esecuzione non riuscita: ${projectName || 'progetto'}`.slice(0, 200),
    description: lines.join('\n'),
    category: 'pipeline',
    severity: 'medium',
  };
}
