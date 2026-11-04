/**
 * canvasVersion — salvataggio condizionato alla versione (Blocco A, regola 8).
 *
 * REGOLA
 *  - La scheda lavora su una versione del canvas: il token opaco
 *    `canvas_version` letto dal GET del progetto (store.loadGraph) e poi
 *    dalla risposta di OGNI scrittura (PUT canvas, ripristino di una
 *    versione). Il token cambia solo quando cambia il canvas persistito:
 *    Valida, invalidazioni e metadati non lo toccano (nessun falso conflitto).
 *  - Ogni scrittura manda il token come `base_canvas_version`; senza token
 *    (server vecchio) la scrittura resta quella compatibile, senza controllo.
 *  - Progetto, canvas e base di una scrittura vengono dallo STESSO snapshot,
 *    preso prima di ogni attesa (colonne derivate, salvataggio in volo):
 *    un cambio di progetto durante l'attesa non manda mai il canvas di A con
 *    il token di B.
 *  - Risposta senza token (`tokenAfterWrite`, regola sicura): si rilegge il
 *    progetto (GET). Se il GET non ha token il server non versiona: scrittura
 *    compatibile, come all'apertura. Se ce l'ha, lo si adotta SOLO se il GET
 *    descrive la stessa scrittura (stesso `updated_at` della risposta);
 *    altrimenti (o se il GET fallisce) si tiene il token precedente: e'
 *    superato, quindi la scrittura dopo da' un 409 visibile con le scelte,
 *    mai una sovrascrittura silenziosa ne' un controllo spento in silenzio.
 *  - Un'unica funzione scrive (store/useCanvasStore, writeConditioned): il
 *    salvataggio di Salva, Esegui, Valida, Anteprima e il ripristino.
 *  - 409 `canvas_version_conflict`: niente sovrascrittura. L'utente sceglie
 *    (editor/SaveConflictNotice, utils/serverActions):
 *      1. ricaricare la versione del server, tenendo le proprie modifiche
 *         come copia di sicurezza della scheda (utils/localDrafts);
 *      2. sovrascrivere consapevolmente: la stessa scrittura senza base.
 *
 * TABELLA DELLE TRANSIZIONI (test in tests/components/CanvasVersion.contract.test.jsx)
 * | evento                         | base mandata         | dopo                                   |
 * |--------------------------------|----------------------|----------------------------------------|
 * | apertura                       | -                    | token := GET.canvas_version (o null)   |
 * | salvataggio riuscito           | token                | token := risposta.canvas_version       |
 * | salvataggio dopo salvataggio   | token della risposta | nessun falso conflitto                 |
 * | Valida / Anteprima             | (salvano se serve)   | token invariato                        |
 * | salvataggio superato (409)     | token vecchio        | saveConflict; canvas del server intatto|
 * | "Sovrascrivi"                  | nessuna              | token := risposta; conflitto chiuso    |
 * | "Ricarica la versione del server" | -                 | copia della scheda scritta, ricarica   |
 * | ripristino di una versione     | token                | token := risposta; ricarica l'editor   |
 * | ripristino superato (409)      | token vecchio        | saveConflict (kind restore)            |
 * | progetto senza canvas_version  | nessuna              | scrittura compatibile                  |
 * | cambio progetto durante il salvataggio | token dello snapshot | PUT del progetto A con il token di A |
 * | risposta senza token, GET della stessa scrittura | token | token := GET.canvas_version          |
 * | risposta senza token, GET non dimostra la scrittura | token | token precedente: 409 visibile dopo |
 */
import { errorField, errorReason } from '../api/errors';

export const CANVAS_VERSION_CONFLICT = 'canvas_version_conflict';

/**
 * Versione del server di un progetto letto con GET /projects/{id}: terzo
 * argomento di store.loadGraph in tutti gli editor (test di enumerazione).
 */
export const serverVersionOf = (project) => ({
  canvasVersion: project?.canvas_version || null,
  updatedAt: project?.metadata?.updated_at || null,
});

/**
 * Token dopo una scrittura riuscita (regola sicura, vedi REGOLA).
 * @param {{response: Object, previous: string|null, readProject: () => Promise<Object>}} args
 * @returns {Promise<string|null>}
 */
export async function tokenAfterWrite({ response, previous = null, readProject }) {
  if (response?.canvas_version) return response.canvas_version;
  let project;
  try {
    project = await readProject();
  } catch {
    return previous;
  }
  const token = project?.canvas_version || null;
  if (!token) return null;
  const writtenAt = response?.updated_at;
  const serverAt = project?.metadata?.updated_at;
  return writtenAt && serverAt && String(writtenAt) === String(serverAt) ? token : previous;
}

export const isCanvasVersionConflict = (err) =>
  err?.response?.status === 409 && errorReason(err) === CANVAS_VERSION_CONFLICT;

/** Errore di una scrittura rifiutata: messaggio per l'utente, token del server. */
export class CanvasVersionConflictError extends Error {
  constructor(cause) {
    super("il progetto è stato salvato da un'altra scheda o da un altro utente dopo che l'hai aperto: le tue modifiche non sono state scritte");
    this.name = 'CanvasVersionConflictError';
    this.reason = CANVAS_VERSION_CONFLICT;
    this.cause = cause;
    this.response = cause?.response;
    this.serverVersion = errorField(cause, 'canvas_version') || null;
    this.serverUpdatedAt = errorField(cause, 'updated_at') || null;
  }
}
