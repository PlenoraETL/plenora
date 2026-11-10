/**
 * dashboardActions — i gestori della dashboard Grafana di un Eseguibile,
 * UNA volta per la pagina Eseguibili, il pannello della home
 * (pages/ProjectList, tabella utils/nodeActions) e la finestra della
 * dashboard (components/eseguibili/DashboardLauncher). 11/11/2026.
 *
 * Il permesso (can.manageEseguibileDashboard) lo applica chi li offre: le
 * voci vengono dalla tabella NODE_ACTIONS. Le liste si aggiornano da sole
 * (utils/sharedInvalidations: ogni scrittura su /eseguibili invalida
 * `eseguibili` e `dashboardGraph`).
 */
import {
  createEseguibileDashboard, deleteEseguibileDashboard, getEseguibileDashboardEditLink, openGrafanaSession,
} from '../api';
import { safeGrafanaPath, toastDashboardWarnings } from './eseguibileDashboard';
import { toastApiError } from './toastApiError';

/** «📊 Crea dashboard»: la dashboard di partenza dalle colonne del risultato. true = creata. */
export async function createDashboard({ toast, item }) {
  try {
    const info = await createEseguibileDashboard(item.id);
    toast.success(`Dashboard di "${item.name}" creata: aprila da «Modifica in Grafana»`);
    toastDashboardWarnings(toast, info?.dashboard_warnings);
    return true;
  } catch (e) {
    toastApiError(toast, e, 'Dashboard non creata');
    return false;
  }
}

/** «Elimina dashboard» dopo la conferma. true = eliminata. */
export async function deleteDashboard({ confirm, toast, item }) {
  const ok = await confirm({
    title: 'Elimina dashboard',
    message: `Eliminare la dashboard Grafana di "${item.name}"? L'Eseguibile resta; la dashboard si potrà ricreare, ma le modifiche fatte in Grafana si perdono.`,
    confirmText: 'Elimina',
    variant: 'danger',
  });
  if (!ok) return false;
  try {
    await deleteEseguibileDashboard(item.id);
    toast.success('Dashboard eliminata');
    return true;
  } catch (e) {
    toastApiError(toast, e, 'Dashboard non eliminata');
    return false;
  }
}

/**
 * I gestori delle voci della tabella NODE_ACTIONS.eseguibile
 * (createDashboard, editDashboard, deleteDashboard), uguali per la pagina
 * Eseguibili e il pannello della home. Chi li passa li da' solo a chi puo'
 * (RULES.manageEseguibileDashboard); quale voce si vede (con o senza
 * dashboard) lo decide la tabella. `onChanged`: dopo crea/elimina.
 */
export function dashboardHandlers({ toast, confirm, item, onChanged = null }) {
  const after = (done) => { if (done) onChanged?.(); return done; };
  return {
    createDashboard: async () => after(await createDashboard({ toast, item })),
    editDashboard: () => openDashboardEditor({ toast, item }),
    deleteDashboard: async () => after(await deleteDashboard({ confirm, toast, item })),
  };
}

/**
 * «📊 Modifica in Grafana»: link di modifica (il server sincronizza team e
 * cartelle), poi la sessione per /grafana, poi la dashboard in una nuova
 * scheda. La scheda si apre SUBITO al clic (dopo un'attesa il browser la
 * bloccherebbe) e si indirizza quando il link c'e'; se il browser la blocca
 * comunque, la dashboard si apre in questa finestra. `taskId`: i dati di
 * un'esecuzione di chi modifica (finestra della dashboard). true = aperta.
 */
export async function openDashboardEditor({ toast, item, taskId = null }) {
  let tab = null;
  try {
    tab = window.open('', '_blank');
  } catch {
    tab = null;
  }
  try {
    const info = await getEseguibileDashboardEditLink(item.id, { taskId });
    const path = safeGrafanaPath(info?.edit_path);
    if (!path) throw new Error('Il server ha indicato un indirizzo della dashboard non valido.');
    await openGrafanaSession();
    toastDashboardWarnings(toast, info?.dashboard_warnings);
    if (tab && !tab.closed) {
      tab.opener = null;
      tab.location.replace(path);
    } else {
      window.location.assign(path);
    }
    return true;
  } catch (e) {
    try { tab?.close(); } catch { /* scheda gia' chiusa */ }
    toastApiError(toast, e, 'Dashboard non aperta');
    return false;
  }
}
