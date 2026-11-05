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
 *  - GeoJSON (oggetto o testo), anche dentro una Feature.
 * Z e M si scartano: la mappa disegna x e y. Una coordinata non finita, un
 * conteggio che eccede i byte, byte in coda o un tipo sconosciuto rendono il
 * valore non leggibile (mai una geometria parziale che rompa la mappa).
 *
 * Il corpus condiviso col backend (shapely) è in
 * backend/tests2/contracts/geometry_cases.json.
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
    let word = take('word').value;
    let suffix = '';
    const glued = /^(.*?)(ZM|Z|M)$/.exec(word);
    if (!WKT_TYPE[word] && glued && WKT_TYPE[glued[1]]) { word = glued[1]; suffix = glued[2]; }
    const type = WKT_TYPE[word];
    if (!type) fail(`tipo «${word}» non supportato`);
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

    const raw = u32();
    const code = raw & 0x1FFFFFFF;
    const iso = Math.floor(code / 1000);
    const type = WKB_TYPE[code % 1000];
    if (!type || iso > 3) fail(`tipo WKB ${code} non supportato`);
    const hasZ = (raw & EWKB_Z) !== 0 || iso === 1 || iso === 3;
    const hasM = (raw & EWKB_M) !== 0 || iso === 2 || iso === 3;
    if (raw & EWKB_SRID) u32();
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

function hexBytes(hex) {
  const out = new Uint8Array(hex.length / 2);
  for (let k = 0; k < out.length; k += 1) out[k] = parseInt(hex.substr(k * 2, 2), 16);
  return out;
}

// --------------------------------------------------------------- GeoJSON ---

function fromGeoJson(obj, depth = 0) {
  if (depth > MAX_NESTING) fail('collezione troppo annidata');
  if (!obj || typeof obj !== 'object') fail('GeoJSON non valido');
  if (obj.type === 'Feature') return fromGeoJson(obj.geometry, depth + 1);
  const { type } = obj;
  if (!TYPES.includes(type)) fail(`tipo GeoJSON «${type}» non supportato`);
  if (type === 'GeometryCollection') {
    if (!Array.isArray(obj.geometries)) fail('GeometryCollection senza geometries');
    return { type, geometries: obj.geometries.map(g => fromGeoJson(g, depth + 1)) };
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

// -------------------------------------------------------------- pubblico ---

/**
 * Legge un valore geometrico. Ritorna GeoJSON 2D o null (non leggibile).
 * @param {unknown} value
 */
export function parseGeometry(value) {
  if (value == null) return null;
  try {
    if (typeof value === 'object') return fromGeoJson(value);
    const text = String(value).trim();
    if (!text) return null;
    if (text.startsWith('{')) return fromGeoJson(JSON.parse(text));
    const hex = HEX.exec(text);
    if (hex) return parseWkb(hexBytes(hex[1]));
    const srid = /^SRID=\d+;/i.exec(text);
    return parseWkt(srid ? text.slice(srid[0].length) : text);
  } catch {
    return null;
  }
}

/** Etichetta del tipo per tabella e statistiche: «MULTIPOINT», «non leggibile». */
export function geometryLabel(value) {
  const geom = parseGeometry(value);
  return geom ? geom.type.toUpperCase() : UNREADABLE_GEOMETRY;
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
