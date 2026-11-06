/**
 * serverActions — UNICO punto da cui il canvas dell'editor arriva al server
 * (Fase 4, M7, salvataggio esplicito).
 *
 * Quello che gira (Scheduler, Eseguibili) e' l'ultima versione SALVATA,
 * quindi il canvas si salva solo quando l'utente lo chiede:
 *   - saveNow()        "💾 Salva" e Ctrl+S;
 *   - beforeServerAction(azione)  Valida, Anteprima, «Mostra i dati»,
 *                      «Aggiorna», Esegui: leggono la versione salvata; con
 *                      modifiche non salvate il pulsante dice «Salva e …»
 *                      (serverActionLabel) e si salva prima (regola unica,
 *                      UI-3 U15; saveForRun e beforePreview la usano);
 *   - restoreVersion(id)  ripristino di una versione (finestra Versioni).
 * Ogni scrittura e' condizionata alla versione della scheda (Blocco A,
 * regola 8, utils/canvasVersion): su un conflitto l'utente sceglie
 *   - reloadServerVersion()  ricarica la versione del server, tenendo le
 *                            modifiche come copia di sicurezza della scheda;
 *   - overwriteServer()      sovrascrive consapevolmente (senza base).
 * Nessun altro modulo chiama handleSaveQuiet, restoreVersionQuiet o
 * overwriteAfterConflict (test di enumerazione).
 */
import useCanvasStore from '../store/useCanvasStore';
import { hasUnsavedChanges } from '../store/saveState';
import { keepTabCopy, serverCanvasReplaced } from './localDrafts';

// Ricarica dell'editor (sostituibile nei test).
export const pageReload = { run: () => window.location.reload() };

const save = () => useCanvasStore.getState().handleSaveQuiet();

export const saveNow = () => save();

/*
 * REGOLA UNICA «salva prima di un'azione sul server» (UI-3, U15).
 *
 * Prima Esegui salvava senza chiedere, Valida e Anteprima chiedevano
 * «Salva e continua», l'anteprima del passo diceva «Salva e …» sul
 * pulsante: tre regole per la stessa cosa. Ora ogni azione che legge il
 * canvas SALVATO e' in SERVER_ACTIONS e segue una sola regola: con
 * modifiche non salvate il suo pulsante dice «Salva e <verbo>»
 * (serverActionLabel) e premendolo si salva e poi si procede
 * (beforeServerAction); senza modifiche il pulsante ha il suo nome e non
 * si salva niente. Nessuna domanda in piu': il pulsante ha gia' detto cosa
 * fa. Il test tests/components/SaveRules.contract.test.jsx enumera le
 * azioni e i loro pulsanti.
 */
export const SERVER_ACTIONS = Object.freeze({
  validate: { name: 'Valida', verb: 'valida' },
  preview: { name: 'Anteprima', verb: 'mostra l\'anteprima' },
  step_data: { name: 'Mostra i dati', verb: 'mostra i dati' },
  refresh: { name: 'Aggiorna', verb: 'aggiorna' },
  run: { name: 'Esegui', verb: 'esegui' },
});

const actionOf = (kind) => {
  const a = SERVER_ACTIONS[kind];
  if (!a) throw new Error(`[serverActions] azione sul server non dichiarata: ${kind}`);
  return a;
};

/** Nome del pulsante di un'azione: «Salva e <verbo>» con modifiche non salvate. */
export function serverActionLabel(kind, unsaved) {
  const a = actionOf(kind);
  return unsaved ? `Salva ${/^e/i.test(a.verb) ? 'ed' : 'e'} ${a.verb}` : a.name;
}

/**
 * Prima di un'azione sul server: salva se ci sono modifiche (il pulsante lo
 * diceva). Un salvataggio fallito si propaga (chi chiama mostra l'errore).
 * @returns {Promise<boolean>} true = si puo' procedere
 */
export async function beforeServerAction(kind) {
  actionOf(kind);
  if (!hasUnsavedChanges(useCanvasStore.getState())) return true;
  await save();
  return true;
}

/** Prima di Esegui (useExecution.onBeforeExecute): stessa regola. */
export const saveForRun = () => beforeServerAction('run');

/** Prima di un'anteprima (usePreview): stessa regola, funzione stabile. */
export const beforePreview = () => beforeServerAction('preview');

/**
 * Il canvas del server e' stato sostituito da questa scheda (ripristino di
 * una versione): la copia di questa scheda non vale piu', le altre schede
 * vengono avvisate, l'editor si ricarica dal percorso normale di apertura.
 */
function afterServerReplaced(projectId) {
  useCanvasStore.getState().markClean();
  serverCanvasReplaced(projectId);
  setTimeout(() => pageReload.run(), 600);
}

/** Ripristino di una versione: scrittura condizionata, poi si ricarica. */
export async function restoreVersion(versionId) {
  const st = useCanvasStore.getState();
  const projectId = st.projectId;
  const response = await st.restoreVersionQuiet(versionId);
  if (response) afterServerReplaced(projectId);
  return response;
}

/** Conflitto di versione: "Sovrascrivi" = la scrittura rifiutata senza base. */
export async function overwriteServer() {
  const st = useCanvasStore.getState();
  const projectId = st.projectId;
  const done = await st.overwriteAfterConflict();
  if (done?.kind === 'restore' && done.response) afterServerReplaced(projectId);
  return done;
}

/**
 * Conflitto di versione: "Ricarica la versione del server". Le modifiche
 * della scheda restano come sua copia di sicurezza (offerta al ricaricamento).
 * @returns {{ok: boolean, reason?: string}} esito della copia: senza copia
 *   non si ricarica (le modifiche andrebbero perse).
 */
export function reloadServerVersion() {
  const st = useCanvasStore.getState();
  if (hasUnsavedChanges(st)) {
    const res = keepTabCopy(st.projectId, { nodes: st.nodes, edges: st.edges, baseUpdatedAt: st.lastSavedServerAt || null });
    if (!res.ok) return res;
  }
  // La copia e' scritta: uscire non deve piu' chiedere delle modifiche.
  st.markClean();
  pageReload.run();
  return { ok: true };
}
