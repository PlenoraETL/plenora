/**
 * sharedResources.js — le risorse condivise del client (UI-1, 06/11/2026;
 * audit C1, C2, C4, C6-C11, C17, C27, M-Estrai).
 *
 * Tabella dichiarativa: nome -> caricatore e freschezza. Ogni lista o
 * dettaglio letto da piu' pagine, nodi o componenti passa da qui (motore in
 * utils/sharedCache): una richiesta in volo per risorsa, dato fresco per
 * `freshMs`, poi stale-while-revalidate; le scritture che la invalidano
 * sono in utils/sharedInvalidations; il logout la svuota (resetSession).
 *
 * Il test tests/shared/dataPolling.contract.test.js verifica che le
 * funzioni API di queste risorse si chiamino SOLO da qui (fuori ci sono
 * solo le eccezioni dichiarate, con il motivo).
 *
 * `schemaSource: true` (conferma A-K7): la risorsa e' a monte delle mappe
 * /schemas degli editor (progetto sorgente, file, tabelle di una
 * connessione, connettori): una sua invalidazione cambia
 * schemaSourcesVersion e le mappe gia' calcolate non valgono piu'.
 *
 * Uso: nelle pagine hooks/useSharedResource (dati subito dalla cache,
 * ricarica quando la risorsa cambia); nei caricatori composti
 * `readShared(nome, [parametri], { force })` dentro useResource con
 * `shared: [nomi]`.
 */
// Le liste dall'indice di src/api (come le leggevano le pagine), i dettagli e
// gli altri dal loro modulo.
import { listProjects, listConnections, listApiConnections, listEseguibili } from '../api';
import { getProject } from '../api/projects';
import { getConnectionTables, listConnectionSets } from '../api/connections';
import { listFeatureConnections } from '../api/features';
import { getFiles, getFile } from '../api/files';
import { listDataSources } from '../api/datasources';
import { getTicketsCounts, getTicketsMeta } from '../api/tickets';
import { getBackupStatus } from '../api/adminBackups';
import { getDashboardCounts } from '../api/dashboard';
import { getLatestRunByProject } from '../api/logRuns';
import { defineSharedResources, readShared, peekShared, invalidateShared, invalidationCount } from './sharedCache';

const SECOND = 1000;
const MINUTE = 60 * SECOND;

export const SHARED_RESOURCES = Object.freeze({
  // Liste delle pagine principali (dashboard, gestori, editor, nodi).
  projects: { load: () => listProjects(), freshMs: 30 * SECOND },
  connections: { load: () => listConnections(), freshMs: MINUTE },
  arcgisConnections: { load: () => listFeatureConnections(), freshMs: MINUTE, schemaSource: true },
  // Parametro: i filtri ({ category, connection_type }), una voce per filtro.
  apiConnections: { load: (filters = {}) => listApiConnections(filters), freshMs: MINUTE, schemaSource: true },
  files: { load: () => getFiles(), freshMs: 30 * SECOND },
  datasources: { load: () => listDataSources(), freshMs: MINUTE, schemaSource: true },
  eseguibili: { load: () => listEseguibili(), freshMs: 30 * SECOND },
  connectionSets: { load: () => listConnectionSets(), freshMs: 30 * SECOND },
  // Conteggi della dashboard (C4): al posto di tre liste intere.
  dashboardCounts: { load: () => getDashboardCounts(), freshMs: 30 * SECOND },
  // Stato dell'ultima esecuzione di ogni progetto (dashboard, A-K3): nella
  // cache, cosi' l'arrivo delle altre liste non lo richiede di nuovo; la
  // fine di un task lo invalida (TASK_INVALIDATIONS).
  latestRunByProject: { load: () => getLatestRunByProject(), freshMs: 15 * SECOND },
  // Dettagli. Il progetto: breve, serve a non scaricarlo due volte di fila
  // (apertura da id -> editor, C6) e ai nodi sorgente (C17), che chiedono
  // un'eta' piu' lunga con maxAgeMs.
  project: { load: (id) => getProject(id), freshMs: 5 * SECOND, schemaSource: true },
  file: { load: (id) => getFile(id), freshMs: MINUTE, schemaSource: true },
  // Catalogo tabelle di una connessione (M-Estrai: 96 kB, fino a 2,4 s):
  // cambia solo con lo schema del DB; «Aggiorna» del catalogo forza.
  connectionTables: { load: (id) => getConnectionTables(id), freshMs: 10 * MINUTE, schemaSource: true },
  // Barra laterale e pagine dei ticket (C1, C2, C10).
  ticketsCounts: { load: () => getTicketsCounts(), freshMs: MINUTE },
  ticketsMeta: { load: () => getTicketsMeta(), freshMs: Infinity },
  // Stato del backup: letto da due risorse della stessa pagina (C11).
  backupStatus: { load: () => getBackupStatus(), freshMs: 3 * SECOND },
});

defineSharedResources(SHARED_RESOURCES);

/** Le risorse a monte delle mappe /schemas (flag `schemaSource`). */
export const SCHEMA_SOURCE_RESOURCES = Object.freeze(
  Object.entries(SHARED_RESOURCES).filter(([, def]) => def.schemaSource).map(([name]) => name),
);

/**
 * Versione delle sorgenti a monte di un editor (A-K7): cambia a ogni
 * invalidazione di una risorsa `schemaSource`, tranne quelle del progetto
 * aperto stesso (salvare il proprio canvas non cambia le sue sorgenti).
 */
export function schemaSourcesVersion(projectId = null) {
  return SCHEMA_SOURCE_RESOURCES
    .map((name) => invalidationCount(name, { except: name === 'project' && projectId ? [[projectId]] : [] }))
    .join('.');
}

export { readShared, peekShared, invalidateShared };
