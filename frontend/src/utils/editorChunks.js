/**
 * editorChunks — UNICA tabella dei caricatori dei tre editor (UI-4, V13).
 *
 * App.jsx crea le pagine lazy da qui; la dashboard chiama
 * `prefetchProjectEditor` al passaggio (mouse o fuoco) su un progetto: il
 * chunk dell'editor parte prima del clic e l'apertura non mostra il
 * «Caricamento...» a tutto schermo. Stesso `import()` = stesso modulo: il
 * browser lo scarica una volta sola.
 */
export const EDITOR_LOADERS = Object.freeze({
  estrai: () => import('../pages/EstraiEditor'),
  manipola: () => import('../pages/ManipolaEditor'),
  arricchisci: () => import('../pages/ArricchisciEditor'),
});

const prefetched = new Set();

/** Scarica in anticipo l'editor del tipo di progetto (una volta; tipo ignoto = Estrai, come le rotte). */
export function prefetchProjectEditor(projectType) {
  const type = EDITOR_LOADERS[projectType] ? projectType : 'estrai';
  if (prefetched.has(type)) return;
  prefetched.add(type);
  // Un errore qui non conta: il clic rifara' l'import (e chunkReload decide).
  EDITOR_LOADERS[type]().catch(() => { prefetched.delete(type); });
}
