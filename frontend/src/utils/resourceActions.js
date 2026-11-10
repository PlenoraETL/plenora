/**
 * resourceActions — i gestori delle azioni sugli Eseguibili, UNA volta per
 * la pagina di origine (pages/EseguibiliList) e per il pannello della home
 * per catene (pages/ProjectList, tabella in utils/nodeActions). 10/11/2026.
 * Il «Test» delle connessioni: utils/connectionTest.
 *
 * Le liste si aggiornano da sole: ogni scrittura invalida le risorse
 * condivise (utils/sharedInvalidations: eseguibili e dashboardGraph,
 * connections / arcgisConnections).
 */
import { deleteEseguibile, updateEseguibile } from '../api';
import { toastApiError } from './toastApiError';

/** Elimina un Eseguibile dopo la conferma. true = eliminato. */
export async function confirmDeleteEseguibile({ confirm, toast, item }) {
  const ok = await confirm({
    title: 'Elimina eseguibile',
    message: `Eliminare l'eseguibile "${item.name}"?`,
    confirmText: 'Elimina',
    variant: 'danger',
  });
  if (!ok) return false;
  try {
    await deleteEseguibile(item.id);
    toast.success('Eseguibile eliminato');
    return true;
  } catch (e) {
    toastApiError(toast, e, 'Errore eliminazione');
    return false;
  }
}

/** Il nome nuovo dell'Eseguibile, o null se non va salvato (vuoto lo dice il toast). */
export function eseguibileNameToSave(item, value, toast) {
  const name = String(value || '').trim();
  if (!name) {
    toast?.error('Il nome non può essere vuoto');
    return null;
  }
  return name === item.name ? null : name;
}

/** Salva il nome (PATCH dell'Eseguibile): la risposta del server. */
export const saveEseguibileName = (item, name) => updateEseguibile(item.id, { name });

/** Rinomina dal pannello: validazione, salvataggio, esito. */
export async function renameEseguibile({ toast, item, value }) {
  const name = eseguibileNameToSave(item, value, toast);
  if (!name) return false;
  try {
    await saveEseguibileName(item, name);
    toast.success('Eseguibile rinominato');
    return true;
  } catch (e) {
    toastApiError(toast, e, 'Errore rinomina');
    return false;
  }
}
