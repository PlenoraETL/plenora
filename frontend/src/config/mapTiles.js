/**
 * Sfondi della mappa dell'anteprima (Leaflet, PreviewModal): le UNICHE
 * risorse esterne che il frontend carica a runtime (P31).
 *
 * Le tile sono immagini: la CSP le ammette in img-src tramite
 * FRONTEND_MAP_TILE_ORIGINS (backend/app/config.py), il cui default copre
 * questi tre server. Cambiando un server qui va aggiornato quel default: lo
 * verifica tests/security/externalUrls.test.js.
 */
export const TILE_URLS = Object.freeze({
  dark: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
  osm:  'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
  sat:  'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
});
