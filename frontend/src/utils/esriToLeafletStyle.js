/**
 * esriToLeafletStyle.js — Converte renderer Esri drawingInfo in funzioni
 * style di Leaflet.
 *
 * Supporta i casi prodotti dal servizio imdf.plenora.it:
 *   - SimpleRenderer: tutti i feature con stessi simbolo (simple)
 *   - UniqueValueRenderer: simbolo per valore di una colonna (uniqueValue)
 *
 * Ritorna una funzione che prende una feature GeoJSON e ritorna le opzioni
 * di stile Leaflet (color, fillColor, weight, fillOpacity, opacity, ...).
 *
 * Esempio uso:
 *   const styleFn = makeLeafletStyle(renderers.Locale);
 *   L.geoJSON(unitGeojson, { style: styleFn });
 */

/**
 * Converte un colore Esri [r, g, b, alpha] in un dict {color, opacity}.
 * Esri usa alpha 0-255, Leaflet usa opacity 0-1.
 */
function esriColorToLeaflet(c) {
  if (!Array.isArray(c) || c.length < 3) {
    return { color: '#888', opacity: 1 };
  }
  const [r, g, b, a = 255] = c;
  const hex = '#' + [r, g, b].map(v => Math.max(0, Math.min(255, v)).toString(16).padStart(2, '0')).join('');
  return { color: hex, opacity: a / 255 };
}

/**
 * Converte un singolo simbolo Esri (esriSFS, esriSLS, esriPMS) in
 * opzioni di stile Leaflet (per polygon/line/marker).
 */
function esriSymbolToLeaflet(symbol) {
  if (!symbol || typeof symbol !== 'object') {
    return { color: '#888', weight: 1, opacity: 1, fillOpacity: 0.3, fillColor: '#888' };
  }
  const t = symbol.type;

  // SimpleFillSymbol: usato per i poligoni (Locale, Livello, Localita, ecc.)
  if (t === 'esriSFS') {
    const fill = esriColorToLeaflet(symbol.color);
    const out  = symbol.outline ? esriColorToLeaflet(symbol.outline.color) : { color: '#444', opacity: 1 };
    return {
      color:       out.color,
      opacity:     out.opacity,
      weight:      symbol.outline?.width || 1,
      fillColor:   fill.color,
      fillOpacity: fill.opacity,
    };
  }

  // SimpleLineSymbol: usato per le linee (Details — A_MURI, A_PORTE, ecc.)
  if (t === 'esriSLS') {
    const c = esriColorToLeaflet(symbol.color);
    return {
      color:   c.color,
      opacity: c.opacity,
      weight:  symbol.width || 1,
    };
  }

  // SimpleMarkerSymbol: punti
  if (t === 'esriSMS') {
    const fill = esriColorToLeaflet(symbol.color);
    const out  = symbol.outline ? esriColorToLeaflet(symbol.outline.color) : { color: '#444', opacity: 1 };
    return {
      color:       out.color,
      opacity:     out.opacity,
      weight:      symbol.outline?.width || 1,
      fillColor:   fill.color,
      fillOpacity: fill.opacity,
      radius:      (symbol.size || 6) / 2,
    };
  }

  // Fallback
  return { color: '#888', weight: 1, opacity: 1, fillOpacity: 0.3, fillColor: '#888' };
}

/**
 * Crea una funzione style di Leaflet a partire da un drawingInfo Esri.
 *
 * @param {object} drawingInfo - Oggetto come generato da renderers.json (con `renderer`)
 *                               oppure direttamente l'oggetto `renderer`.
 * @returns {function} (feature) => leafletStyle
 */
export function makeLeafletStyle(drawingInfo) {
  if (!drawingInfo) {
    return () => ({ color: '#3388ff', weight: 2, opacity: 1, fillOpacity: 0.2, fillColor: '#3388ff' });
  }

  // Il file renderers.json del servizio IMDF wraps il renderer dentro `renderer`
  const renderer = drawingInfo.renderer || drawingInfo;

  if (renderer.type === 'simple') {
    const style = esriSymbolToLeaflet(renderer.symbol);
    return () => style;
  }

  if (renderer.type === 'uniqueValue') {
    const field         = renderer.field1;
    const defaultStyle  = esriSymbolToLeaflet(renderer.defaultSymbol);
    const valueMap      = {};
    (renderer.uniqueValueInfos || []).forEach(uvi => {
      valueMap[String(uvi.value)] = esriSymbolToLeaflet(uvi.symbol);
    });
    return (feature) => {
      if (!feature || !feature.properties) return defaultStyle;
      const v = feature.properties[field];
      if (v == null) return defaultStyle;
      return valueMap[String(v)] || defaultStyle;
    };
  }

  // Tipo non gestito: stile default
  return () => ({ color: '#3388ff', weight: 2, opacity: 1, fillOpacity: 0.2, fillColor: '#3388ff' });
}

/**
 * Mapping nome layer IMDF → nome layer renderers.json:
 *   unit   → Locale
 *   level  → Livello
 *   building → OperaPrincipale
 *   venue  → Localita
 *   (Details non e' un geojson IMDF, e' nel GDB)
 */
export const IMDF_LAYER_TO_RENDERER = {
  unit:     'Locale',
  level:    'Livello',
  building: 'OperaPrincipale',
  venue:    'Localita',
  opening:  null, // nessun renderer specifico, default
};

/**
 * Helper: dato un nome layer IMDF (es. 'unit') e l'oggetto renderers.json
 * completo, ritorna la funzione style Leaflet appropriata.
 */
export function getLeafletStyleForImdfLayer(layerName, renderersJson) {
  if (!renderersJson) return makeLeafletStyle(null);
  const rendererKey = IMDF_LAYER_TO_RENDERER[layerName];
  if (!rendererKey) return makeLeafletStyle(null);
  return makeLeafletStyle(renderersJson[rendererKey]);
}

/**
 * Converte un colore Esri [r,g,b,a 0-255] in un array RGBA per deck.gl (0-255).
 * Deck.gl accetta [r,g,b,a] con a 0-255.
 */
function esriColorToRgba(c, fallback = [150, 150, 150, 200]) {
  if (!Array.isArray(c) || c.length < 3) return fallback;
  const [r, g, b, a = 255] = c;
  return [r, g, b, a];
}

/**
 * Crea una funzione `(feature) => [r,g,b,a]` per deck.gl a partire da un
 * drawingInfo Esri. Legge il field da `properties[field]` e, se non
 * presente, da `properties.extension[field]` (Apple IMDF custom fields).
 */
export function makeDeckColorFn(drawingInfo, opts = {}) {
  const alpha       = opts.alpha ?? 200;
  const defaultRgba = opts.fallback || [120, 120, 120, alpha];
  if (!drawingInfo) return () => defaultRgba;
  const renderer = drawingInfo.renderer || drawingInfo;

  if (renderer.type === 'simple') {
    const c = esriColorToRgba(renderer.symbol?.color, defaultRgba);
    return () => [c[0], c[1], c[2], alpha];
  }

  if (renderer.type === 'uniqueValue') {
    const field = renderer.field1;
    const defC  = esriColorToRgba(renderer.defaultSymbol?.color, defaultRgba);
    const map   = {};
    (renderer.uniqueValueInfos || []).forEach(uvi => {
      map[String(uvi.value)] = esriColorToRgba(uvi.symbol?.color, defC);
    });
    return (feature) => {
      const p = feature?.properties || feature || {};
      let v = p[field];
      if (v == null && p.extension && typeof p.extension === 'object') v = p.extension[field];
      const c = v == null ? defC : (map[String(v)] || defC);
      return [c[0], c[1], c[2], alpha];
    };
  }

  return () => defaultRgba;
}

/**
 * Helper per layer IMDF → funzione color per deck.gl.
 */
export function getDeckColorForImdfLayer(layerName, renderersJson, opts) {
  if (!renderersJson) return makeDeckColorFn(null, opts);
  const key = IMDF_LAYER_TO_RENDERER[layerName];
  if (!key) return makeDeckColorFn(null, opts);
  return makeDeckColorFn(renderersJson[key], opts);
}

/**
 * Palette fallback per Apple IMDF `category` (quando non c'è renderer).
 * Copre le categorie piu' comuni; altre finiscono in DEFAULT.
 */
export const IMDF_CATEGORY_COLORS = {
  room:         [90,  130, 180],
  office:       [70,  110, 170],
  auditorium:   [150, 105, 200],
  conferenceroom: [120, 90, 180],
  restroom:     [55,  165, 205],
  elevator:     [155, 75,  205],
  stairs:       [185, 145, 195],
  escalator:    [175, 135, 185],
  walkway:      [130, 130, 130],
  corridor:     [130, 130, 130],
  lobby:        [70,  205, 120],
  entrance:     [90,  185, 90 ],
  platform:     [160, 205, 70 ],
  waitingroom:  [160, 205, 70 ],
  ticketing:    [235, 185, 35 ],
  retail:       [250, 120, 0  ],
  restaurant:   [250, 120, 0  ],
  storage:      [145, 115, 75 ],
  mechanicalroom: [85, 85,  85],
  electricalroom: [85, 85,  105],
  parking:      [110, 110, 110],
  unspecified:  [115, 115, 115],
};
