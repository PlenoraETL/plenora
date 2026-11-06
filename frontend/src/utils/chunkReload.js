/**
 * chunkReload — ripresa dopo un deploy, SOLO su una navigazione vera.
 *
 * Una pagina aperta prima del deploy conosce i file della build vecchia:
 * aprendo una pagina importa `EstraiEditor-<hash vecchio>.js`, che non
 * esiste piu' -> "Failed to fetch dynamically imported module". La pagina si
 * ricarica (una volta; se l'errore si ripete entro 30 s il file manca davvero
 * e vale l'ErrorBoundary; senza storage niente ricarica, per non entrare in
 * un ciclo).
 *
 * Regola (UI conferma C-K1, V13). Prima un gestore globale di
 * `vite:preloadError` ricaricava a OGNI import fallito: bastava il prefetch
 * di un editor al passaggio del mouse (o della freccia in Ctrl+K) e, dentro
 * un editor con modifiche, compariva la domanda nativa del browser. Ora:
 *  - nessun gestore globale: un import fallito torna a chi lo ha chiesto. Il
 *    prefetch (utils/editorChunks) lo ignora, i caricamenti al bisogno
 *    (dagre, js-yaml, Sentry) mostrano il loro errore;
 *  - ricarica SOLO il caricatore di una pagina (`lazyPage`, le rotte di
 *    App.jsx), cioe' una navigazione vera;
 *  - mai senza la domanda delle modifiche non salvate: se un editor ha
 *    qualcosa da perdere si ricarica solo se l'uscita e' gia' stata
 *    confermata (NavigationGuard / useSignOut, utils/leaveGuard), e il
 *    prompt nativo non si ripete. Altrimenti l'errore arriva all'ErrorBoundary.
 * Unico `location.reload()` dell'app per un deploy
 * (tests/shared/previewAndReload.contract.test.js).
 */
import React from 'react';
import { mustAskBeforeLeaving } from './leaveGuard';

const RELOAD_KEY = 'plenora:chunk-reload-at';
const WINDOW_MS = 30_000;

/**
 * Ricarica la pagina dopo il caricatore fallito di una pagina, se si puo'.
 * @returns {boolean} true = ricarica partita.
 */
export function reloadForNewBuild(win = window) {
  if (mustAskBeforeLeaving()) return false;
  try {
    const last = Number(win.sessionStorage.getItem(RELOAD_KEY)) || 0;
    if (Date.now() - last < WINDOW_MS) return false;
    win.sessionStorage.setItem(RELOAD_KEY, String(Date.now()));
  } catch {
    return false;
  }
  win.location.reload();
  return true;
}

/**
 * Pagina caricata al bisogno (React.lazy) che, se il suo file manca dopo un
 * deploy, ricarica secondo la regola sopra. Durante la ricarica resta il
 * caricamento (promessa che non si risolve), mai la schermata di errore.
 */
export function lazyPage(loader, win = window) {
  return React.lazy(() => loader().catch((err) => {
    if (reloadForNewBuild(win)) return new Promise(() => {});
    throw err;
  }));
}
