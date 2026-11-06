/**
 * knownProjects — l'ultima lista dei progetti scaricata in questa sessione
 * (UI-4, U23): la palette Ctrl+K cerca anche i progetti senza una
 * richiesta in piu'. Ogni `listProjects` (dashboard, editor, log,
 * Eseguibili) la aggiorna; si tengono solo id, nome e tipo. Dati
 * dell'utente: si svuota a fine sessione (utils/session).
 */
import { registerSessionReset } from './session';

let known = [];

/** Ricorda i progetti di una lista appena scaricata. */
export function rememberProjects(projects) {
  if (!Array.isArray(projects)) return;
  known = projects
    .filter(p => p && p.id != null)
    .map(p => ({ id: p.id, name: String(p.name || ''), project_type: p.project_type }));
}

/** I progetti noti (vuoto finche' nessuna lista e' stata scaricata). */
export const knownProjects = () => known;

registerSessionReset('knownProjects', () => { known = []; });
