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
 */

const ANY = /^(post|put|patch|delete)$/;

const ALL_LISTS = Object.freeze([
  'projects', 'project', 'connections', 'connectionTables', 'arcgisConnections', 'apiConnections',
  'files', 'file', 'datasources', 'eseguibili', 'connectionSets', 'dashboardCounts',
  'ticketsCounts', 'backupStatus',
]);

export const WRITE_INVALIDATIONS = Object.freeze([
  // ── Letture con POST e azioni che non toccano liste condivise ──────────
  { method: /^post$/, url: /^\/projects\/[^/]+\/(schemas|query-preview|validate|preflight|test-run\/preflight)$/, invalidates: [], why: 'calcolo sul canvas, nessuna scrittura' },
  { method: /^post$/, url: /^\/projects\/[^/]+\/nodes\/[^/]+\/distinct$/, invalidates: [], why: 'valori distinti di una colonna' },
  { method: /^post$/, url: /^\/projects\/[^/]+\/(execute|enrich-async|test-run|test-run\/enrich)$/, invalidates: [], why: 'esecuzione: scrive esecuzioni e risultati, non le liste' },
  { method: /^post$/, url: /^\/projects\/admin\/cleanup-orphans$/, invalidates: [], why: 'pulizia degli oggetti orfani nello storage' },
  { method: /^post$/, url: /^\/api-connections\/(test|fetch-openapi-schema)$/, invalidates: [], why: 'prova di una connessione non salvata' },
  { method: /^post$/, url: /^\/carica\/(auto-mapping|check-conflicts)$/, invalidates: [], why: 'calcolo della mappatura' },
  { method: /^post$/, url: /^\/files\/preview-temp$/, invalidates: [], why: 'anteprima di un file non salvato' },
  { method: /^post$/, url: /^\/functions\/(translate|preview-formula)$/, invalidates: [], why: 'traduzione/anteprima di una formula' },
  { method: /^post$/, url: /^\/eseguibili\/[^/]+\/(analyze-upload|preflight|execute|runs)$/, invalidates: [], why: 'analisi ed esecuzione di un eseguibile' },
  { method: /^post$/, url: /^\/auth\/(login|logout|change-password)$/, invalidates: [], why: 'sessione: il logout svuota comunque tutto (resetSession)' },
  { method: ANY, url: /^\/auth\/(users|groups)(\/|$)/, invalidates: [], why: 'utenti e gruppi: non sono liste condivise' },
  { method: ANY, url: /^\/tasks\//, invalidates: [], why: 'annullamento e pulizia di un\'esecuzione in corso' },
  { method: /^post$/, url: /^\/runs\/[^/]+\/pin$/, invalidates: [], why: 'esecuzione fissata nello storico' },
  { method: ANY, url: /^\/admin\/object-storage(\/|$)/, invalidates: [], why: 'oggetti dello storage' },
  { method: /^post$/, url: /^\/admin\/task-runtime\//, invalidates: [], why: 'risoluzione di un Carica dall\'esito incerto' },

  // ── Scritture delle liste condivise ──────────────────────────────────────
  { method: /^delete$/, url: /^\/projects\/([^/]+)$/, invalidates: (m) => ['projects', ['project', m[1]], 'eseguibili', 'dashboardCounts'], why: 'eliminazione a cascata (eseguibili, pianificazioni)' },
  { method: ANY, url: /^\/projects\/([^/]+)(\/|$)/, invalidates: (m) => ['projects', ['project', m[1]]], why: 'canvas, metadati, versioni, clone' },
  { method: /^post$/, url: /^\/projects$/, invalidates: ['projects'], why: 'nuovo progetto' },
  { method: /^(put|delete)$/, url: /^\/connections\/([^/]+)$/, invalidates: (m) => ['connections', ['connectionTables', m[1]]], why: 'connessione modificata o eliminata: anche le sue tabelle' },
  { method: ANY, url: /^\/connections(\/|$)/, invalidates: ['connections'], why: 'nuova connessione o esito del test' },
  { method: ANY, url: /^\/connection-sets(\/|$)/, invalidates: ['connectionSets'], why: 'set di connessioni' },
  { method: ANY, url: /^\/features\/connections(\/|$)/, invalidates: ['arcgisConnections'], why: 'connessioni ArcGIS ed esito del test' },
  { method: ANY, url: /^\/api-connections(\/|$)/, invalidates: ['apiConnections', 'dashboardCounts'], why: 'connessioni API ed esito del test' },
  { method: ANY, url: /^\/datasources(\/|$)/, invalidates: ['datasources', 'dashboardCounts'], why: 'data source ed esito del test' },
  { method: ANY, url: /^\/eseguibili(\/|$)/, invalidates: ['eseguibili'], why: 'eseguibili e loro versioni' },
  { method: /^post$/, url: /^\/files\/(upload|upload-multi-layer)$/, invalidates: ['files'], why: 'file caricati' },
  { method: /^(put|delete)$/, url: /^\/files\/([^/]+)$/, invalidates: (m) => ['files', ['file', m[1]]], why: 'file modificato o eliminato' },
  { method: ANY, url: /^\/schedules(\/|$)/, invalidates: ['dashboardCounts'], why: 'conteggio delle pianificazioni nella dashboard' },
  { method: ANY, url: /^\/shares\//, invalidates: ALL_LISTS, why: 'la condivisione cambia cosa vede ogni lista' },
  { method: ANY, url: /^\/tickets(\/|$)/, invalidates: ['ticketsCounts'], why: 'contatore dei ticket aperti' },
  { method: /^post$/, url: /^\/admin\/backups\/trigger$/, invalidates: ['backupStatus'], why: 'backup avviato' },
]);

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

export { ALL_LISTS as SHARED_LIST_NAMES };
