/**
 * geometry.js — l'unico lettore di geometrie del frontend (Fase 5, P7).
 *
 * Regola: ogni valore di una colonna geometrica passa da `parseGeometry`, che
 * restituisce una geometria GeoJSON in 2D (`{ type, coordinates }` o
 * `{ type: 'GeometryCollection', geometries }`) oppure `null` se il valore non
 * è leggibile. Le forme lette sono:
 *  - WKT ed EWKT: prefisso `SRID=xxxx;`, dimensioni Z, M, ZM (anche scritte
 *    attaccate, `POINTZ`, e implicite, `POINT (1 2 3)`), EMPTY, MULTIPOINT con
 *    e senza parentesi interne, GEOMETRYCOLLECTION annidate;
 *  - WKB ed EWKB esadecimali (anche col prefisso `\x` di PostgreSQL): ordine
 *    dei byte per ogni sotto-geometria, codici ISO (1000/2000/3000) e flag
 *    EWKB Z/M/SRID;
 *  - GeoJSON (oggetto o testo), anche dentro una Feature;
 *  - geometrie Esri JSON di ArcGIS (oggetto o testo, senza `type`): point
 *    `{x, y}`, multipoint `{points}`, polyline `{paths}`, polygon `{rings}`
 *    (regole in `fromEsriJson`); le curve (`curvePaths`, `curveRings`) non
 *    sono lette.
 * Z e M si scartano: la mappa disegna x e y. Una coordinata non finita, un
 * conteggio che eccede i byte, byte in coda o un tipo sconosciuto rendono il
 * valore non leggibile (mai una geometria parziale che rompa la mappa).
 *
 * `geometryType` legge il solo tipo DICHIARATO (parola WKT, intestazione WKB,
 * `type` GeoJSON, chiavi Esri) con le stesse funzioni di classificazione del
 * lettore (`classify`, `wktTypeOf`, `wkbHeader`, `objectTypeOf`), senza
 * leggere le coordinate: serve ai conteggi su molte righe (statistiche).
 * Coincide con `geometryLabel` su ogni valore leggibile; un valore con
 * intestazione valida e corpo rotto conta col suo tipo dichiarato (solo
 * `geometryLabel`/`parseGeometry` leggono tutto).
 *
 * Il corpus condiviso col backend (shapely) è in
 * backend/tests2/contracts/geometry_cases.json; i casi Esri JSON (che shapely
 * non legge) sono scritti a mano in frontend/tests/shared/geometry.contract.test.js.
 */

export const UNREADABLE_GEOMETRY = 'non leggibile';

const TYPES = ['Point', 'LineString', 'Polygon', 'MultiPoint', 'MultiLineString', 'MultiPolygon', 'GeometryCollection'];
const WKT_TYPE = Object.fromEntries(TYPES.map(t => [t.toUpperCase(), t]));
const WKB_TYPE = Object.fromEntries(TYPES.map((t, i) => [i + 1, t]));
// Tipo delle parti di un tipo multiplo.
const PART_OF = { MultiPoint: 'Point', MultiLineString: 'LineString', MultiPolygon: 'Polygon' };
// Annidamento massimo delle collezioni: oltre, il valore non è leggibile.
const MAX_NESTING = 32;

class Unreadable extends Error {}
const fail = (why) => { throw new Unreadable(why); };

// Un anello vuoto non è un anello (shapely scrive POLYGON EMPTY in GeoJSON
// come [[]] ma lo legge come []): stessa forma da WKT, WKB e GeoJSON.
const rings = (list) => list.filter(ring => ring.length > 0);

const finite2 = (x, y) => {
  if (!Number.isFinite(x) || !Number.isFinite(y)) fail('coordinata non finita');
  return [x, y];
};

// ------------------------------------------------------------------- WKT ---

/** Parola WKT (maiuscola) -> tipo e suffisso attaccato (`POINTZ`). */
function wktTypeOf(word) {
  if (WKT_TYPE[word]) return { type: WKT_TYPE[word], suffix: '' };
  const glued = /^(.*?)(ZM|Z|M)$/.exec(word);
  if (glued && WKT_TYPE[glued[1]]) return { type: WKT_TYPE[glued[1]], suffix: glued[2] };
  return fail(`tipo «${word}» non supportato`);
}

function tokenize(text) {
  const tokens = [];
  const re = /(?:([A-Za-z]+)|([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)|([(),]))/y;
  const space = /\s*/y;
  let pos = 0;
  for (;;) {
    space.lastIndex = pos;
    space.exec(text);
    pos = space.lastIndex;
    if (pos >= text.length) break;
    re.lastIndex = pos;
    const m = re.exec(text);
    if (!m) fail(`carattere inatteso alla posizione ${pos}`);
    if (m[1]) tokens.push({ kind: 'word', value: m[1].toUpperCase() });
    else if (m[2]) tokens.push({ kind: 'num', value: Number(m[2]) });
    else tokens.push({ kind: m[3] });
    pos = re.lastIndex;
  }
  return tokens;
}

function parseWkt(text) {
  const tokens = tokenize(text);
  let i = 0;
  const peek = () => tokens[i];
  const take = (kind) => {
    const t = tokens[i];
    if (!t || t.kind !== kind) fail(`atteso «${kind}»`);
    i += 1;
    return t;
  };
  const isNext = (kind, value) => peek()?.kind === kind && (value === undefined || peek().value === value);

  // Numero di valori per coordinata: dichiarato (Z/M = 3, ZM = 4) o dedotto
  // dalla prima coordinata (2, 3 o 4) e poi uguale per tutte.
  const readCoord = (dim) => {
    const values = [];
    while (isNext('num')) values.push(take('num').value);
    if (dim.n == null) {
      if (values.length < 2 || values.length > 4) fail('coordinata con un numero di valori non valido');
      dim.n = values.length;
    }
    if (values.length !== dim.n) fail('coordinate con dimensioni diverse');
    return finite2(values[0], values[1]);
  };
  const isEmpty = () => {
    if (isNext('word', 'EMPTY')) { i += 1; return true; }
    return false;
  };
  const list = (readItem) => {
    take('(');
    const items = [readItem()];
    while (isNext(',')) { i += 1; items.push(readItem()); }
    take(')');
    return items;
  };
  const coordList = (dim) => (isEmpty() ? [] : list(() => readCoord(dim)));
  const polygon = (dim) => (isEmpty() ? [] : rings(list(() => coordList(dim))));

  const geometry = (depth) => {
    if (depth > MAX_NESTING) fail('collezione troppo annidata');
    const head = wktTypeOf(take('word').value);
    const { type } = head;
    let { suffix } = head;
    if (!suffix && isNext('word') && ['Z', 'M', 'ZM'].includes(peek().value)) suffix = take('word').value;
    const dim = { n: suffix === 'ZM' ? 4 : suffix ? 3 : null };

    if (type === 'GeometryCollection') {
      return { type, geometries: isEmpty() ? [] : list(() => geometry(depth + 1)) };
    }
    if (type === 'Point') {
      if (isEmpty()) return { type, coordinates: [] };
      take('(');
      const c = readCoord(dim);
      take(')');
      return { type, coordinates: c };
    }
    if (type === 'LineString') return { type, coordinates: coordList(dim) };
    if (type === 'Polygon') return { type, coordinates: polygon(dim) };
    if (type === 'MultiPoint') {
      // MULTIPOINT ((1 2), (3 4)) e MULTIPOINT (1 2, 3 4).
      const point = () => {
        if (!isNext('(')) return readCoord(dim);
        take('(');
        const c = readCoord(dim);
        take(')');
        return c;
      };
      return { type, coordinates: isEmpty() ? [] : list(point) };
    }
    if (type === 'MultiLineString') return { type, coordinates: isEmpty() ? [] : list(() => coordList(dim)) };
    return { type, coordinates: isEmpty() ? [] : list(() => polygon(dim)) };
  };

  const geom = geometry(0);
  if (i !== tokens.length) fail('testo in coda alla geometria');
  return geom;
}

// ------------------------------------------------------------------- WKB ---

const EWKB_Z = 0x80000000;
const EWKB_M = 0x40000000;
const EWKB_SRID = 0x20000000;

/** Intero del tipo WKB/EWKB -> tipo, dimensioni e flag SRID. */
function wkbHeader(raw) {
  const code = raw & 0x1FFFFFFF;
  const iso = Math.floor(code / 1000);
  const type = WKB_TYPE[code % 1000];
  if (!type || iso > 3) fail(`tipo WKB ${code} non supportato`);
  return {
    type,
    hasZ: (raw & EWKB_Z) !== 0 || iso === 1 || iso === 3,
    hasM: (raw & EWKB_M) !== 0 || iso === 2 || iso === 3,
    hasSrid: (raw & EWKB_SRID) !== 0,
  };
}

function parseWkb(bytes) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let pos = 0;
  const need = (n) => { if (pos + n > bytes.length) fail('WKB troncato'); };

  const geometry = (depth) => {
    if (depth > MAX_NESTING) fail('collezione troppo annidata');
    need(5);
    const order = bytes[pos];
    if (order !== 0 && order !== 1) fail('ordine dei byte non valido');
    const le = order === 1;
    pos += 1;
    const u32 = () => { need(4); const v = view.getUint32(pos, le); pos += 4; return v; };
    const f64 = () => { need(8); const v = view.getFloat64(pos, le); pos += 8; return v; };

    const { type, hasZ, hasM, hasSrid } = wkbHeader(u32());
    if (hasSrid) u32();
    const dims = 2 + (hasZ ? 1 : 0) + (hasM ? 1 : 0);

    const coord = () => {
      const v = Array.from({ length: dims }, f64);
      return [v[0], v[1]];
    };
    // Conteggio con limite: ogni elemento occupa almeno `minBytes`.
    const count = (minBytes) => {
      const n = u32();
      if (n * minBytes > bytes.length - pos) fail('conteggio oltre la fine del WKB');
      return n;
    };
    const coords = () => Array.from({ length: count(dims * 8) }, () => finite2(...coord()));
    const polygon = () => rings(Array.from({ length: count(4) }, coords));
    const parts = (partType) => Array.from({ length: count(9) }, () => {
      const g = geometry(depth + 1);
      if (partType && g.type !== partType) fail(`parte ${g.type} dentro un tipo multiplo di ${partType}`);
      return partType === 'Point' && !g.coordinates.length ? fail('punto vuoto dentro un MultiPoint') : g;
    });

    switch (type) {
      case 'Point': {
        const [x, y] = coord();
        // POINT EMPTY in WKB: coordinate NaN.
        if (Number.isNaN(x) && Number.isNaN(y)) return { type, coordinates: [] };
        return { type, coordinates: finite2(x, y) };
      }
      case 'LineString': return { type, coordinates: coords() };
      case 'Polygon': return { type, coordinates: polygon() };
      case 'GeometryCollection': return { type, geometries: parts(null) };
      default: return { type, coordinates: parts(PART_OF[type]).map(g => g.coordinates) };
    }
  };

  const geom = geometry(0);
  if (pos !== bytes.length) fail('byte in coda al WKB');
  return geom;
}

const HEX = /^(?:\\x)?((?:0[01])(?:[0-9a-fA-F]{2})+)$/;

function hexBytes(hex, length = hex.length / 2) {
  const out = new Uint8Array(length);
  for (let k = 0; k < out.length; k += 1) out[k] = parseInt(hex.substr(k * 2, 2), 16);
  return out;
}

/** Tipo dichiarato nell'intestazione WKB (ordine dei byte + intero del tipo). */
function wkbTypeOf(hex) {
  if (hex.length < 10) fail('WKB troncato');
  const head = hexBytes(hex, 5);
  return wkbHeader(new DataView(head.buffer).getUint32(1, head[0] === 1)).type;
}

// ------------------------------------------------------------- Esri JSON ---

// Chiave di una geometria Esri JSON -> tipo dichiarato (prima regola che
// combacia). Un oggetto con `type` è GeoJSON, mai Esri.
const ESRI_RULES = [
  { key: 'x', type: 'Point' },
  { key: 'points', type: 'MultiPoint' },
  { key: 'paths', type: 'MultiLineString' },
  { key: 'rings', type: 'Polygon' },
];
const esriRule = (obj) => (obj.type === undefined ? ESRI_RULES.find(r => r.key in obj) : undefined);

// Area con segno (formula del laccio): <= 0 senso orario, l'anello esterno
// di un poligono Esri; > 0 antiorario, un buco.
const signedArea = (ring) => {
  let a = 0;
  for (let k = 0; k < ring.length - 1; k += 1) a += ring[k][0] * ring[k + 1][1] - ring[k + 1][0] * ring[k][1];
  return a / 2;
};
const insideRing = ([x, y], ring) => {
  let inside = false;
  for (let k = 0, j = ring.length - 1; k < ring.length; j = k, k += 1) {
    const [xi, yi] = ring[k];
    const [xj, yj] = ring[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
};

/**
 * Anelli Esri -> poligoni: ogni anello orario apre un poligono; un anello
 * antiorario è il buco del primo poligono che contiene il suo primo vertice,
 * altrimenti (dati scritti al contrario) è un poligono a sé. Le coordinate
 * restano come scritte (l'orientamento non conta per la mappa).
 */
function esriPolygons(ringList) {
  const polygons = [];
  const holes = [];
  for (const ring of ringList) {
    if (signedArea(ring) <= 0) polygons.push([ring]);
    else holes.push(ring);
  }
  for (const hole of holes) {
    const owner = polygons.find(p => insideRing(hole[0], p[0]));
    if (owner) owner.push(hole);
    else polygons.push([hole]);
  }
  return polygons;
}

/**
 * Geometria Esri JSON -> GeoJSON 2D. Point vuoto: `x` null o "NaN" (come lo
 * scrive ArcGIS); polyline con un solo path -> LineString, altrimenti
 * MultiLineString; polygon con un solo anello esterno -> Polygon, altrimenti
 * MultiPolygon. z e m (anche nelle posizioni `[x, y, z, m]`) si scartano.
 */
function fromEsriJson(obj, type) {
  const position = (p) => {
    if (!Array.isArray(p) || p.length < 2 || p.length > 4) fail('posizione Esri non valida');
    return finite2(p[0], p[1]);
  };
  const list = (value, what) => (Array.isArray(value) ? value : fail(`${what} Esri non validi`));
  if (type === 'Point') {
    if (obj.x == null || obj.x === 'NaN') return { type, coordinates: [] };
    return { type, coordinates: finite2(obj.x, obj.y) };
  }
  if (type === 'MultiPoint') return { type, coordinates: list(obj.points, 'points').map(position) };
  if (type === 'MultiLineString') {
    const paths = list(obj.paths, 'paths').map(path => list(path, 'path').map(position));
    return paths.length === 1 ? { type: 'LineString', coordinates: paths[0] } : { type, coordinates: paths };
  }
  const polygons = esriPolygons(rings(list(obj.rings, 'rings').map(ring => list(ring, 'ring').map(position))));
  return polygons.length > 1 ? { type: 'MultiPolygon', coordinates: polygons } : { type, coordinates: polygons[0] || [] };
}

// --------------------------------------------------------------- GeoJSON ---

function fromObject(obj, depth = 0) {
  if (depth > MAX_NESTING) fail('collezione troppo annidata');
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) fail('oggetto geometrico non valido');
  if (obj.type === 'Feature') return fromObject(obj.geometry, depth + 1);
  const esri = esriRule(obj);
  if (esri) return fromEsriJson(obj, esri.type);
  const { type } = obj;
  if (!TYPES.includes(type)) fail(`tipo GeoJSON «${type}» non supportato`);
  if (type === 'GeometryCollection') {
    if (!Array.isArray(obj.geometries)) fail('GeometryCollection senza geometries');
    return { type, geometries: obj.geometries.map(g => fromObject(g, depth + 1)) };
  }
  const position = (p) => {
    if (!Array.isArray(p) || p.length < 2 || p.length > 4) fail('posizione GeoJSON non valida');
    return finite2(p[0], p[1]);
  };
  const levels = { Point: 0, LineString: 1, MultiPoint: 1, Polygon: 2, MultiLineString: 2, MultiPolygon: 3 }[type];
  const walk = (value, level) => {
    if (level === 0) return position(value);
    if (!Array.isArray(value)) fail('coordinate GeoJSON non valide');
    return value.map(v => walk(v, level - 1));
  };
  const c = obj.coordinates;
  if (type === 'Point' && Array.isArray(c) && c.length === 0) return { type, coordinates: [] };
  const coordinates = walk(c, levels);
  if (type === 'Polygon') return { type, coordinates: rings(coordinates) };
  if (type === 'MultiPolygon') return { type, coordinates: coordinates.map(rings) };
  return { type, coordinates };
}

/**
 * Tipo dichiarato di un oggetto (GeoJSON, Feature, Esri) senza leggere le
 * coordinate. Unica eccezione: polyline e polygon Esri non dichiarano se sono
 * semplici o multipli, quindi si legge l'oggetto intero.
 */
function objectTypeOf(obj, depth = 0) {
  if (depth > MAX_NESTING) fail('collezione troppo annidata');
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) fail('oggetto geometrico non valido');
  if (obj.type === 'Feature') return objectTypeOf(obj.geometry, depth + 1);
  const esri = esriRule(obj);
  if (esri) return esri.type === 'Point' || esri.type === 'MultiPoint' ? esri.type : fromEsriJson(obj, esri.type).type;
  if (!TYPES.includes(obj.type)) fail(`tipo GeoJSON «${obj.type}» non supportato`);
  return obj.type;
}

// -------------------------------------------------------------- pubblico ---

/**
 * Classificazione unica di un valore: quale lettore e su quale dato. La usano
 * sia `parseGeometry` (lettura intera) sia `geometryType` (solo il tipo).
 * @returns {null | { kind: 'object'|'wkb'|'wkt', data: unknown }}
 */
function classify(value) {
  if (value == null) return null;
  if (typeof value === 'object') return { kind: 'object', data: value };
  const text = String(value).trim();
  if (!text) return null;
  if (text.startsWith('{')) return { kind: 'object', data: JSON.parse(text) };
  const hex = HEX.exec(text);
  if (hex) return { kind: 'wkb', data: hex[1] };
  const srid = /^SRID=\d+;/i.exec(text);
  return { kind: 'wkt', data: srid ? text.slice(srid[0].length) : text };
}

const READ = {
  object: (obj) => fromObject(obj),
  wkb: (hex) => parseWkb(hexBytes(hex)),
  wkt: (text) => parseWkt(text),
};

const TYPE_OF = {
  object: (obj) => objectTypeOf(obj),
  wkb: wkbTypeOf,
  wkt: (text) => {
    const word = /^\s*([A-Za-z]+)/.exec(text);
    return word ? wktTypeOf(word[1].toUpperCase()).type : fail('tipo WKT mancante');
  },
};

/**
 * Legge un valore geometrico. Ritorna GeoJSON 2D o null (non leggibile).
 * @param {unknown} value
 */
export function parseGeometry(value) {
  try {
    const c = classify(value);
    return c ? READ[c.kind](c.data) : null;
  } catch {
    return null;
  }
}

/** Etichetta del tipo per la tabella: «MULTIPOINT», «non leggibile» (lettura intera). */
export function geometryLabel(value) {
  const geom = parseGeometry(value);
  return geom ? geom.type.toUpperCase() : UNREADABLE_GEOMETRY;
}

/**
 * Etichetta del tipo DICHIARATO, senza leggere le coordinate (statistiche su
 * molte righe): stessa classificazione di `parseGeometry`, stesso testo di
 * `geometryLabel` su ogni valore leggibile.
 */
export function geometryType(value) {
  try {
    const c = classify(value);
    return c ? TYPE_OF[c.kind](c.data).toUpperCase() : UNREADABLE_GEOMETRY;
  } catch {
    return UNREADABLE_GEOMETRY;
  }
}

const hasCoordinates = (g) => (g.type === 'GeometryCollection' ? g.geometries.length > 0 : g.coordinates.length > 0);

/**
 * La parte disegnabile di una geometria: senza punti, linee o poligoni vuoti
 * (Leaflet non disegna una geometria vuota e un punto vuoto lo fa fallire).
 * Ritorna null se non resta niente da disegnare.
 */
export function drawableGeometry(geom) {
  if (!geom) return null;
  if (geom.type === 'GeometryCollection') {
    const geometries = geom.geometries.map(drawableGeometry).filter(Boolean);
    return geometries.length ? { type: geom.type, geometries } : null;
  }
  if (geom.type === 'Point') return hasCoordinates(geom) ? geom : null;
  const nonEmpty = (geom.type === 'LineString' || geom.type === 'MultiPoint')
    ? geom.coordinates
    : geom.coordinates.filter(part => part.length > 0);
  return nonEmpty.length ? { type: geom.type, coordinates: nonEmpty } : null;
}
