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
 *
 * Anche se la pagina che l'ha avviato si smonta (conferma finale F5): ogni
 * poller di taskPoller che segue un task lo dichiara (`watchTask`); quando
 * l'ultimo smette prima della fine (uscita dall'editor durante
 * l'esecuzione) il task resta nel registro SENZA osservatori e lo segue il
 * poller di fondo di taskPoller (profilo `taskBackground`, leggero), finche'
 * non finisce: allora taskEnded() invalida e toglie la voce. Un task
 * registrato che nessuno segue (risposta arrivata a pagina gia' smontata)
 * finisce nello stesso poller di fondo.
 */
import { registerSessionReset } from './session';
import { invalidateShared } from './sharedCache';
import { taskEffects, caricaTaskInvalidations } from './sharedInvalidations';

// task_id -> { invalidates, profile, watchers, getStatus }
const tasks = new Map();
// Chi segue i task rimasti senza osservatori (utils/taskPoller).
let orphanListener = null;

const notifyOrphans = () => { if (orphanListener && orphanTaskIds().length) orphanListener(); };

/** Dopo la risposta di una richiesta (api/http): registra il task avviato. */
export function registerTaskLaunch(method, url, data) {
  if (!data || typeof data !== 'object' || !data.async || !data.task_id) return;
  const { invalidates, profile } = taskEffects(method, url);
  const id = String(data.task_id);
  const prev = tasks.get(id);
  tasks.set(id, { invalidates, profile, watchers: prev?.watchers || 0, getStatus: prev?.getStatus || null });
  notifyOrphans();
}

/**
 * Un poller segue il task (utils/taskPoller). Restituisce la funzione da
 * chiamare quando smette: se era l'ultimo e il task non e' finito, il task
 * passa al poller di fondo. `getStatus` e' quello che il poller usava.
 */
export function watchTask(taskId, getStatus = null) {
  const id = String(taskId);
  const entry = tasks.get(id);
  if (!entry) return () => {};
  entry.watchers += 1;
  if (getStatus) entry.getStatus = getStatus;
  let released = false;
  return () => {
    if (released) return;
    released = true;
    const current = tasks.get(id);
    if (!current) return;
    current.watchers = Math.max(0, current.watchers - 1);
    notifyOrphans();
  };
}

/** Task registrati, non finiti, che nessuna pagina sta seguendo. */
export function orphanTaskIds() {
  return [...tasks.entries()].filter(([, e]) => e.watchers === 0).map(([id]) => id);
}

/** Il getStatus dell'ultimo poller che ha seguito il task (null = quello dell'API). */
export const orphanStatusReader = (taskId) => tasks.get(String(taskId))?.getStatus || null;

/** taskPoller si registra qui per seguire i task rimasti senza osservatori. */
export function onOrphanTasks(listener) {
  orphanListener = listener;
  return () => { if (orphanListener === listener) orphanListener = null; };
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
    if (child?.task_id) {
      tasks.set(String(child.task_id), {
        invalidates: caricaTaskInvalidations(child), profile: 'task', watchers: 0, getStatus: entry?.getStatus || null,
      });
    }
  }
  if (entry) invalidateShared(entry.invalidates);
  notifyOrphans();
}

registerSessionReset('taskRegistry', () => { tasks.clear(); });
