/**
 * sharedInvalidations.js — quali scritture invalidano quali risorse
 * condivise (UI-1, 06/11/2026). Tabella dichiarativa unica: api/http la
 * applica a OGNI risposta (riuscita o no: una scrittura fallita puo' aver
 * cambiato qualcosa) di POST/PUT/PATCH/DELETE.
 *
 * Regole:
 *  - vince la PRIMA riga il cui metodo e indirizzo corrispondono;
 *  - `invalidates`: nomi di utils/sharedResources, o funzione del match che
 *    restituisce nomi e [nome, ...parametri] (solo quella voce: salvare il
 *    canvas del progetto A non rilegge il progetto B);
 *  - `[]` = la scrittura non cambia nessuna lista condivisa (letture con
 *    POST, test, esecuzioni): il motivo e' nella riga;
 *  - una scrittura senza riga invalida TUTTO (scelta sicura) e il test
 *    tests/shared/dataPolling.contract.test.js fallisce: ogni scrittura di
 *    src/api ha la sua riga.
 *
 * TASK_INVALIDATIONS (conferma A-K2, A-K5d): le richieste che AVVIANO un
 * task asincrono (risposta `{async: true, task_id}`), quello che il task
 * cambia quando FINISCE (qualunque esito: anche fallito o perso puo' aver
 * scritto qualcosa) — contratto e freschezza dei progetti, mappe /schemas a
 * valle (la risorsa `project` e' `schemaSource`), stato dell'ultima
 * esecuzione, catalogo tabelle della connessione di Carica — e il profilo
 * di polling (utils/poller) con la sua durata massima. api/http registra il
 * task all'avvio, utils/taskPoller applica la riga alla fine
 * (utils/taskRegistry). Un avvio senza riga: alla fine invalida TUTTO.
 */

const ANY = /^(post|put|patch|delete)$/;

const ALL_LISTS = Object.freeze([
  'projects', 'project', 'connections', 'connectionTables', 'arcgisConnections', 'apiConnections',
  'files', 'file', 'datasources', 'eseguibili', 'connectionSets', 'dashboardCounts',
  'ticketsCounts', 'backupStatus', 'latestRunByProject', 'dashboardGraph',
]);

export const WRITE_INVALIDATIONS = Object.freeze([
  // ── Letture con POST e azioni che non toccano liste condivise ──────────
  { method: /^post$/, url: /^\/projects\/[^/]+\/(schemas|query-preview|validate|preflight|test-run\/preflight)$/, invalidates: [], why: 'calcolo sul canvas, nessuna scrittura' },
  { method: /^post$/, url: /^\/projects\/[^/]+\/nodes\/[^/]+\/distinct$/, invalidates: [], why: 'valori distinti di una colonna' },
  { method: /^post$/, url: /^\/projects\/[^/]+\/(execute|enrich-async|test-run|test-run\/enrich)$/, invalidates: [], why: 'avvio di un\'esecuzione: quello che cambia lo invalida la sua fine (TASK_INVALIDATIONS)' },
  { method: /^post$/, url: /^\/projects\/admin\/cleanup-orphans$/, invalidates: [], why: 'pulizia degli oggetti orfani nello storage' },
  { method: /^post$/, url: /^\/api-connections\/(test|fetch-openapi-schema)$/, invalidates: [], why: 'prova di una connessione non salvata' },
  { method: /^post$/, url: /^\/carica\/(auto-mapping|check-conflicts)$/, invalidates: [], why: 'calcolo della mappatura' },
  { method: /^post$/, url: /^\/files\/preview-temp$/, invalidates: [], why: 'anteprima di un file non salvato' },
  { method: /^post$/, url: /^\/functions\/(translate|preview-formula)$/, invalidates: [], why: 'traduzione/anteprima di una formula' },
  { method: /^post$/, url: /^\/eseguibili\/[^/]+\/(analyze-upload|preflight|execute|runs)$/, invalidates: [], why: 'analisi e avvio di un eseguibile (la fine: TASK_INVALIDATIONS)' },
  { method: /^post$/, url: /^\/auth\/(login|logout|change-password)$/, invalidates: [], why: 'sessione: il logout svuota comunque tutto (resetSession)' },
  { method: /^delete$/, url: /^\/auth\/groups\/[^/]+$/, invalidates: ALL_LISTS, why: 'gruppo eliminato a cascata con le sue risorse (progetti, connessioni, file...)' },
  { method: ANY, url: /^\/auth\/(users|groups)(\/|$)/, invalidates: [], why: 'utenti e gruppi: non sono liste condivise; il cambio del PROPRIO ruolo o gruppo lo rileva /auth/me (context/AuthContext)' },
  { method: ANY, url: /^\/tasks\//, invalidates: [], why: 'annullamento e pulizia di un\'esecuzione in corso' },
  { method: /^post$/, url: /^\/runs\/[^/]+\/pin$/, invalidates: [], why: 'esecuzione fissata nello storico' },
  { method: ANY, url: /^\/admin\/object-storage(\/|$)/, invalidates: [], why: 'oggetti dello storage' },
  { method: /^post$/, url: /^\/admin\/task-runtime\//, invalidates: [], why: 'risoluzione di un Carica dall\'esito incerto' },

  // ── Scritture delle liste condivise ──────────────────────────────────────
  { method: /^delete$/, url: /^\/projects\/([^/]+)$/, invalidates: (m) => ['projects', ['project', m[1]], 'eseguibili', 'dashboardCounts', 'dashboardGraph'], why: 'eliminazione a cascata (eseguibili, pianificazioni)' },
  { method: ANY, url: /^\/projects\/([^/]+)(\/|$)/, invalidates: (m) => ['projects', ['project', m[1]], 'dashboardGraph'], why: 'canvas, metadati, versioni, clone: anche i collegamenti della home' },
  { method: /^post$/, url: /^\/projects$/, invalidates: ['projects', 'dashboardGraph'], why: 'nuovo progetto' },
  { method: /^(put|delete)$/, url: /^\/connections\/([^/]+)$/, invalidates: (m) => ['connections', ['connectionTables', m[1]]], why: 'connessione modificata o eliminata: anche le sue tabelle' },
  { method: ANY, url: /^\/connections(\/|$)/, invalidates: ['connections'], why: 'nuova connessione o esito del test' },
  { method: ANY, url: /^\/connection-sets(\/|$)/, invalidates: ['connectionSets'], why: 'set di connessioni' },
  { method: ANY, url: /^\/features\/connections(\/|$)/, invalidates: ['arcgisConnections'], why: 'connessioni ArcGIS ed esito del test' },
  { method: ANY, url: /^\/api-connections(\/|$)/, invalidates: ['apiConnections', 'dashboardCounts'], why: 'connessioni API ed esito del test' },
  { method: ANY, url: /^\/datasources(\/|$)/, invalidates: ['datasources', 'dashboardCounts'], why: 'data source ed esito del test' },
  { method: ANY, url: /^\/eseguibili(\/|$)/, invalidates: ['eseguibili', 'dashboardGraph'], why: 'eseguibili e loro versioni (anche nella home)' },
  { method: /^post$/, url: /^\/files\/(upload|upload-multi-layer)$/, invalidates: ['files'], why: 'file caricati' },
  { method: /^(put|delete)$/, url: /^\/files\/([^/]+)$/, invalidates: (m) => ['files', ['file', m[1]]], why: 'file modificato o eliminato' },
  { method: ANY, url: /^\/schedules(\/|$)/, invalidates: ['dashboardCounts', 'dashboardGraph'], why: 'conteggio delle pianificazioni nella dashboard e nella home' },
  { method: ANY, url: /^\/shares\//, invalidates: ALL_LISTS, why: 'la condivisione cambia cosa vede ogni lista' },
  { method: ANY, url: /^\/tickets(\/|$)/, invalidates: ['ticketsCounts'], why: 'contatore dei ticket aperti' },
  { method: /^post$/, url: /^\/admin\/backups\/trigger$/, invalidates: ['backupStatus'], why: 'backup avviato' },
]);

// Profili di polling dei task (nomi di POLL_PROFILES in utils/poller).
const TASK = 'task';
const ENRICH_TASK = 'enrichTask';

// Fine di un'esecuzione del progetto P: il contratto di P e la freschezza
// dei progetti a valle (annotata nei loro nodi da GET /projects/{id}) sono
// cambiati: tutte le voci `project` (chi sta a valle non si sa qui), quindi
// anche le mappe /schemas degli editor aperti; e lo stato delle esecuzioni.
const EXECUTION_DONE = Object.freeze(['project', 'latestRunByProject', 'dashboardGraph']);

export const TASK_INVALIDATIONS = Object.freeze([
  { method: /^post$/, url: /^\/projects\/[^/]+\/(execute|test-run)$/, profile: TASK, invalidates: EXECUTION_DONE, why: 'esecuzione (anche di prova): contratto, freschezza a valle, ultima esecuzione' },
  { method: /^post$/, url: /^\/projects\/[^/]+\/(enrich-async|test-run\/enrich)$/, profile: ENRICH_TASK, invalidates: EXECUTION_DONE, why: 'Arricchisci: come un\'esecuzione; nessun tetto di durata lato server (chiamate API a ritmo limitato)' },
  { method: /^get$/, url: /^\/projects\/([^/]+)\/preview$/, profile: TASK, invalidates: (m) => [['project', m[1]]], why: 'anteprima: puo\' aggiornare le colonne note del progetto' },
  { method: /^post$/, url: /^\/eseguibili\/[^/]+\/(execute|runs)$/, profile: TASK, invalidates: ['latestRunByProject', 'dashboardGraph'], why: 'eseguibile: istantanea congelata, scrive solo l\'esecuzione (ultima esecuzione anche nella home)' },
]);

/**
 * I task di Carica partono dall'esecuzione (risultato `carica.tasks`, ognuno
 * con il `connection_id` della destinazione): la loro fine cambia le
 * tabelle di quella connessione (anche nuove) e l'ultima esecuzione.
 */
export function caricaTaskInvalidations(task) {
  const connectionId = task?.connection_id;
  return [connectionId ? ['connectionTables', connectionId] : 'connectionTables', 'latestRunByProject', 'dashboardGraph'];
}

/** Toglie origine, query e barra finale: `/projects/abc/canvas`. */
function writePath(url) {
  let path = String(url || '');
  path = path.replace(/^[a-z]+:\/\/[^/]+/i, '').replace(/[?#].*$/, '');
  path = path.replace(/^\/api(?=\/)/, '');
  if (!path.startsWith('/')) path = `/${path}`;
  return path.length > 1 ? path.replace(/\/+$/, '') : path;
}

/** La riga che vale per questa scrittura, o null. */
export function writeRule(method, url) {
  const m = String(method || '').toLowerCase();
  if (!ANY.test(m)) return null;
  const path = writePath(url);
  for (const rule of WRITE_INVALIDATIONS) {
    if (!rule.method.test(m)) continue;
    const match = rule.url.exec(path);
    if (match) return { rule, match };
  }
  return null;
}

/** Le risorse da invalidare dopo questa scrittura ([] per le letture). */
export function invalidationsForWrite(method, url) {
  const m = String(method || '').toLowerCase();
  if (!ANY.test(m)) return [];
  const found = writeRule(m, url);
  if (!found) return [...ALL_LISTS];
  const { rule, match } = found;
  return typeof rule.invalidates === 'function' ? rule.invalidates(match) : [...rule.invalidates];
}

/** La riga di TASK_INVALIDATIONS della richiesta che ha avviato un task, o null. */
function taskRule(method, url) {
  const m = String(method || '').toLowerCase();
  const path = writePath(url);
  for (const rule of TASK_INVALIDATIONS) {
    if (!rule.method.test(m)) continue;
    const match = rule.url.exec(path);
    if (match) return { rule, match };
  }
  return null;
}

/**
 * Che cosa invalida la fine di un task avviato da questa richiesta e con
 * quale profilo si segue: { invalidates, profile, known }. Senza riga: tutto.
 */
export function taskEffects(method, url) {
  const found = taskRule(method, url);
  if (!found) return { invalidates: [...ALL_LISTS], profile: TASK, known: false };
  const { rule, match } = found;
  const invalidates = typeof rule.invalidates === 'function' ? rule.invalidates(match) : [...rule.invalidates];
  return { invalidates, profile: rule.profile, known: true };
}

export { ALL_LISTS as SHARED_LIST_NAMES };
