/**
 * backendValidationResult.js — adatta la risposta di POST /projects/{id}/validate
 * (Epic Canvas Validation §12) al `CanvasValidationResult` consumato dal FE
 * (ValidationModal, badge, highlight).
 *
 * Il backend è la SINGLE SOURCE OF TRUTH della validazione (i validate*Canvas.js
 * per-famiglia sono stati rimossi). Issue backend: `{node_id, severity, code,
 * message, suggestion}`. Issue FE: `{id, severity, message, nodeId, source}`.
 */

import { buildResult, createIssue } from './canvasValidationContract';
import { requestErrorMessage, asSentence } from './errorMessages';
import { projectRunBlockReason } from './runtimeInputKinds';
import { inputKindLabel } from './glossary';

/**
 * Mappa un singolo issue backend nel ValidationIssue FE.
 * @param {{node_id?: string, severity: string, code: string, message: string}} bi
 */
function backendIssueToFE(bi) {
  return createIssue({
    id: bi.code,
    severity: bi.severity,
    message: bi.message,
    nodeId: bi.node_id || undefined,
    source: 'backend',
  });
}

/**
 * Converte `{valid, has_errors, issues:[...]}` in `CanvasValidationResult`.
 * `ok`/`bySeverity` sono ricomputati da `buildResult` (coerenti con has_errors).
 * @param {{issues?: Array}} data
 * @returns {import('./canvasValidationContract').CanvasValidationResult}
 */
export function backendReportToResult(data) {
  const issues = Array.isArray(data?.issues) ? data.issues.map(backendIssueToFE) : [];
  return buildResult(issues);
}

/**
 * Risultato fallback quando la richiesta di validazione fallisce (rete/server):
 * un solo issue error, così il modal mostra il problema invece di un finto "ok".
 * @param {string} [message]
 * @returns {import('./canvasValidationContract').CanvasValidationResult}
 */
export function validationRequestFailedResult(message) {
  return buildResult([createIssue({
    id: 'VALIDATION_REQUEST_FAILED',
    severity: 'error',
    message: message || 'Validazione non riuscita: errore di comunicazione col server.',
    source: 'backend',
  })]);
}

/**
 * Esecuzione bloccata prima di salvare: nodi con risorse non accessibili al
 * gruppo (access_denied). Un issue per nodo, cosi' l'evidenziazione e il
 * "vai al nodo" del modal funzionano come per la validazione.
 * @param {{nodeIds: string[]}} denied da describeAccessDeniedNodes
 */
export function accessDeniedResult(denied) {
  return buildResult((denied?.nodeIds || []).map(nodeId => createIssue({
    id: 'ACCESS_DENIED',
    severity: 'error',
    message: 'Esecuzione bloccata: questo nodo usa una risorsa non accessibile al gruppo. Rimuovilo o sostituiscilo.',
    nodeId,
    source: 'frontend',
  })));
}

/** Il salvataggio prima di validare/eseguire non e' riuscito. */
export function saveFailedResult(err) {
  return buildResult([createIssue({
    id: 'SAVE_FAILED',
    severity: 'error',
    message: `Salvataggio non riuscito: ${asSentence(requestErrorMessage(err))} Niente è stato validato o eseguito.`,
    source: 'frontend',
  })]);
}

/**
 * Input runtime che bloccano «Esegui» del progetto (utils/runtimeInputKinds,
 * projectRunBlockReason): un tipo che questa modalita' non esegue, oppure un
 * tipo impostato dal server («Utente che esegue») per chi non puo' fare
 * l'esecuzione di prova (viewer: can.testRunProject falso).
 */
export function unsupportedInputsResult(nodes, mode) {
  return buildResult((nodes || []).map((n) => {
    const name = n.data?.label || n.data?.input_key || n.id;
    const server = projectRunBlockReason(mode, n.data?.input_kind) === 'server';
    return createIssue({
      id: server ? 'RUNTIME_IDENTITY_REQUIRED' : 'RUNTIME_INPUT_KIND_UNSUPPORTED',
      severity: 'error',
      message: server
        ? `L'input «${name}» è «${inputKindLabel('current_user')}»: prende il nome dell'utente che avvia un Eseguibile. Dall'editor si può fare solo un'esecuzione di prova, riservata a chi può modificare il progetto. Per eseguirlo con il tuo utente aprilo come Eseguibile.`
        : `L'input «${name}» è di tipo connessione: questo tipo è disponibile solo nei progetti Estrai. Cambialo o rimuovilo.`,
      nodeId: n.id,
      source: 'frontend',
    });
  }));
}
