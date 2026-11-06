/**
 * taskRegistry.js — i task asincroni avviati in questa pagina e che cosa
 * cambia la loro fine (conferma UI 06/11/2026, A-K2, A-K5d).
 *
 * Un solo percorso per costruzione:
 *  - api/http, a OGNI risposta `{async: true, task_id}`, registra il task
 *    con la riga di TASK_INVALIDATIONS (utils/sharedInvalidations) della
 *    richiesta che l'ha avviato: risorse da invalidare alla fine e profilo
 *    di polling;
 *  - utils/taskPoller, quando un task finisce (successo, errore, annullato,
 *    perso, scaduto), chiama taskEnded(): le risorse si invalidano (cache
 *    condivisa e quindi mappe /schemas a valle). Un'esecuzione che ha
 *    avviato task di Carica (`carica.tasks`) li registra a sua volta: la
 *    loro fine invalida il catalogo tabelle della connessione di
 *    destinazione.
 * Nessuna pagina deve ricordarsi di invalidare dopo un'esecuzione.
 */
import { registerSessionReset } from './session';
import { invalidateShared } from './sharedCache';
import { taskEffects, caricaTaskInvalidations } from './sharedInvalidations';

// task_id -> { invalidates, profile }
const tasks = new Map();

/** Dopo la risposta di una richiesta (api/http): registra il task avviato. */
export function registerTaskLaunch(method, url, data) {
  if (!data || typeof data !== 'object' || !data.async || !data.task_id) return;
  const { invalidates, profile } = taskEffects(method, url);
  tasks.set(String(data.task_id), { invalidates, profile });
}

/** Il profilo di polling di un task registrato (null se non registrato). */
export function taskProfileName(taskId) {
  return tasks.get(String(taskId))?.profile || null;
}

/**
 * Fine di un task (utils/taskPoller): invalida quello che la riga dice e
 * registra i task di Carica avviati dall'esecuzione. Un task non registrato
 * (seguito da un'altra pagina, es. dopo una ricarica) non invalida niente:
 * la sua fine la vede chi l'ha avviato.
 */
export function taskEnded(taskId, outcome = null) {
  const id = String(taskId);
  const entry = tasks.get(id);
  tasks.delete(id);
  for (const child of outcome?.result?.carica?.tasks || []) {
    if (child?.task_id) tasks.set(String(child.task_id), { invalidates: caricaTaskInvalidations(child), profile: 'task' });
  }
  if (entry) invalidateShared(entry.invalidates);
}

registerSessionReset('taskRegistry', () => { tasks.clear(); });
