/**
 * chunkReload — ripresa dopo un deploy.
 *
 * Una pagina aperta prima del deploy conosce i file della build vecchia:
 * aprendo un editor importa `EstraiEditor-<hash vecchio>.js`, che non
 * esiste piu' -> "Failed to fetch dynamically imported module" e schermata
 * di errore critico. Vite segnala OGNI import dinamico fallito con l'evento
 * `vite:preloadError`: un solo gestore copre tutti i React.lazy dell'app.
 * Si ricarica la pagina una volta; se l'errore si ripete entro 30 s (il file
 * manca davvero) lo si lascia arrivare all'ErrorBoundary. Al massimo un
 * ricaricamento ogni 30 s, e nessuno se lo storage non e' disponibile.
 */
const RELOAD_KEY = 'plenora:chunk-reload-at';
const WINDOW_MS = 30_000;

export function installChunkReload(win = window) {
  // Si ricarica solo se il segno "gia' ricaricato" si puo' scrivere: senza
  // storage non c'e' modo di evitare un loop, quindi vale l'ErrorBoundary.
  const onPreloadError = (event) => {
    try {
      const last = Number(win.sessionStorage.getItem(RELOAD_KEY)) || 0;
      if (Date.now() - last < WINDOW_MS) return;
      win.sessionStorage.setItem(RELOAD_KEY, String(Date.now()));
    } catch {
      return;
    }
    event.preventDefault();
    win.location.reload();
  };
  win.addEventListener('vite:preloadError', onPreloadError);
  return () => win.removeEventListener('vite:preloadError', onPreloadError);
}
