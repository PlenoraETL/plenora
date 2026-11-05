/**
 * Lettore unico delle geometrie (Fase 5, P7).
 *
 * Il corpus è generato con shapely dal backend
 * (backend/tests2/contracts/generate_geometry_cases.py): ogni forma di ogni
 * geometria (WKT, EWKT, WKB ISO, EWKB, bytea, GeoJSON) deve dare lo stesso
 * tipo e le stesse coordinate 2D di shapely; i valori che shapely rifiuta
 * sono «non leggibile» anche qui. Poi: nessun altro lettore nel frontend e la
 * mappa disegna solo geometrie non vuote.
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';
import { parseGeometry, geometryLabel, geometryType, drawableGeometry, UNREADABLE_GEOMETRY } from '../../src/utils/geometry';
import { sourceFiles, stripComments } from '../helpers/sourceScan.js';

const CASES = JSON.parse(fs.readFileSync(
  path.resolve(import.meta.dirname, '../../../backend/tests2/contracts/geometry_cases.json'), 'utf-8'));

describe('corpus condiviso con shapely', () => {
  it.each(CASES.readable.map(c => [`${c.kind}: ${c.source}`, c]))('%s', (_label, c) => {
    expect(parseGeometry(c.value)).toEqual(c.expected.geometry);
    expect(geometryLabel(c.value)).toBe(c.expected.type.toUpperCase());
  });

  it.each(CASES.unreadable.map(c => [c.value, c]))('non leggibile: %s', (_label, c) => {
    expect(parseGeometry(c.value)).toBeNull();
    expect(geometryLabel(c.value)).toBe(UNREADABLE_GEOMETRY);
  });

  it('GeoJSON come oggetto e dentro una Feature', () => {
    const geojson = CASES.readable.filter(c => c.kind === 'geojson');
    for (const c of geojson) {
      const obj = JSON.parse(c.value);
      expect(parseGeometry(obj)).toEqual(c.expected.geometry);
      expect(parseGeometry({ type: 'Feature', geometry: obj, properties: {} })).toEqual(c.expected.geometry);
    }
  });
});

describe('valori non leggibili fuori dal corpus', () => {
  it.each([
    [null], [undefined], [''], ['   '], [42], ['{non json'], [{ type: 'Point', coordinates: ['a', 1] }],
    [{ type: 'Circle', coordinates: [0, 0] }], ['POINT (1e400 0)'], ['0101000000'],
    // WKB con un byte in coda.
    ['0101000000000000000000F03F000000000000004000'],
    // Conteggio enorme: rifiutato senza allocare.
    ['0102000000FFFFFFFF'],
  ])('%s', (value) => {
    expect(parseGeometry(value)).toBeNull();
  });
});

describe('disegnabile', () => {
  it('toglie le parti vuote e rifiuta ciò che resta vuoto', () => {
    expect(drawableGeometry(parseGeometry('POINT EMPTY'))).toBeNull();
    expect(drawableGeometry(parseGeometry('GEOMETRYCOLLECTION EMPTY'))).toBeNull();
    expect(drawableGeometry(parseGeometry('GEOMETRYCOLLECTION (POINT EMPTY, POINT (1 2))')))
      .toEqual({ type: 'GeometryCollection', geometries: [{ type: 'Point', coordinates: [1, 2] }] });
    expect(drawableGeometry(parseGeometry('MULTILINESTRING ((0 0, 1 1), EMPTY)')))
      .toEqual({ type: 'MultiLineString', coordinates: [[[0, 0], [1, 1]]] });
    expect(drawableGeometry(null)).toBeNull();
  });
  it('ogni caso leggibile del corpus è disegnabile o vuoto, mai con coordinate non numeriche', () => {
    const flat = (g) => (g.type === 'GeometryCollection' ? g.geometries.flatMap(flat) : [g.coordinates].flat(4));
    for (const c of CASES.readable) {
      const g = drawableGeometry(parseGeometry(c.value));
      if (g) expect(flat(g).every(Number.isFinite)).toBe(true);
    }
  });
});

// Esri JSON (ArcGIS REST): casi scritti a mano, perché shapely non legge
// questo formato. Anelli esterni in senso orario, buchi antiorari.
const OUTER = [[0, 0], [0, 10], [10, 10], [10, 0], [0, 0]];
const HOLE = [[2, 2], [4, 2], [4, 4], [2, 4], [2, 2]];
const OUTER2 = [[20, 0], [20, 5], [25, 5], [25, 0], [20, 0]];
const ESRI_CASES = [
  [{ x: 11.34, y: 44.49, spatialReference: { wkid: 4326 } }, { type: 'Point', coordinates: [11.34, 44.49] }],
  [{ x: 1, y: 2, z: 3, m: 4 }, { type: 'Point', coordinates: [1, 2] }],
  [{ x: null, spatialReference: { wkid: 4326 } }, { type: 'Point', coordinates: [] }],
  [{ x: 'NaN', y: 'NaN' }, { type: 'Point', coordinates: [] }],
  [{ points: [[1, 2], [3, 4, 5]], hasZ: true }, { type: 'MultiPoint', coordinates: [[1, 2], [3, 4]] }],
  [{ points: [] }, { type: 'MultiPoint', coordinates: [] }],
  [{ paths: [[[0, 0], [1, 1]]] }, { type: 'LineString', coordinates: [[0, 0], [1, 1]] }],
  [{ paths: [[[0, 0], [1, 1]], [[2, 2, 9], [3, 3, 9]]] },
    { type: 'MultiLineString', coordinates: [[[0, 0], [1, 1]], [[2, 2], [3, 3]]] }],
  [{ paths: [] }, { type: 'MultiLineString', coordinates: [] }],
  [{ rings: [OUTER] }, { type: 'Polygon', coordinates: [OUTER] }],
  [{ rings: [OUTER, HOLE] }, { type: 'Polygon', coordinates: [OUTER, HOLE] }],
  [{ rings: [OUTER, OUTER2, HOLE] }, { type: 'MultiPolygon', coordinates: [[OUTER, HOLE], [OUTER2]] }],
  // Un buco fuori da ogni esterno (anello scritto al contrario) è un poligono a sé.
  [{ rings: [HOLE] }, { type: 'Polygon', coordinates: [HOLE] }],
  [{ rings: [] }, { type: 'Polygon', coordinates: [] }],
];
const ESRI_UNREADABLE = [
  { x: 1 }, { x: 'a', y: 2 }, { points: [[1]] }, { paths: [[[0, 'x']]] }, { rings: 'no' },
  { curveRings: [] }, { curvePaths: [] },
];

describe('Esri JSON (geometrie ArcGIS)', () => {
  it.each(ESRI_CASES.map(([v, g]) => [JSON.stringify(v), v, g]))('%s', (_label, value, expected) => {
    expect(parseGeometry(value)).toEqual(expected);
    expect(parseGeometry(JSON.stringify(value))).toEqual(expected);
    expect(geometryLabel(value)).toBe(expected.type.toUpperCase());
    expect(geometryType(value)).toBe(expected.type.toUpperCase());
  });
  it.each(ESRI_UNREADABLE.map(v => [JSON.stringify(v), v]))('non leggibile: %s', (_label, value) => {
    expect(parseGeometry(value)).toBeNull();
  });
  it('un oggetto con `type` è GeoJSON, mai Esri', () => {
    expect(parseGeometry({ type: 'Point', coordinates: [1, 2], x: 9, y: 9 })).toEqual({ type: 'Point', coordinates: [1, 2] });
  });
});

describe('tipo dichiarato (statistiche)', () => {
  it('coincide con geometryLabel su ogni caso leggibile del corpus', () => {
    const diff = CASES.readable.filter(c => geometryType(c.value) !== geometryLabel(c.value)).map(c => `${c.kind}: ${c.source}`);
    expect(diff).toEqual([]);
    for (const c of CASES.readable.filter(x => x.kind === 'geojson')) {
      expect(geometryType(JSON.parse(c.value))).toBe(c.expected.type.toUpperCase());
    }
  });
  it('sui non leggibili: intestazione rotta -> «non leggibile», corpo rotto -> tipo dichiarato', () => {
    const got = Object.fromEntries(CASES.unreadable.map(c => [c.value, geometryType(c.value)]));
    expect(got).toEqual({
      'POINT (1)': 'POINT',
      'POINT (1 2 3 4 5)': 'POINT',
      'POINT Z (1 2)': 'POINT',
      'LINESTRING (0 0, 1 1': 'LINESTRING',
      'POINT (1 2) POINT (3 4)': 'POINT',
      'POINT (a b)': 'POINT',
      'CIRCULARSTRING (0 0, 1 1, 2 0)': UNREADABLE_GEOMETRY,
      ciao: UNREADABLE_GEOMETRY,
      '0101': UNREADABLE_GEOMETRY,
      '01010000000000000000': 'POINT',
      '0108000000000000000000F03F0000000000000040': UNREADABLE_GEOMETRY,
      '01040000000500000001010000000000000000000000': 'MULTIPOINT',
    });
    for (const v of [null, undefined, '', '   ', 42, '{non json']) expect(geometryType(v)).toBe(UNREADABLE_GEOMETRY);
  });
  it('10.000 MULTIPOLYGON da 200 vertici (WKT ed EWKB): meno di 150 ms', () => {
    const ring = Array.from({ length: 201 }, (_, k) => {
      const a = (2 * Math.PI * (k % 200)) / 200;
      return [11 + Math.cos(a) * 0.01, 44 + Math.sin(a) * 0.01];
    });
    const wkt = `MULTIPOLYGON (((${ring.map(p => p.join(' ')).join(', ')})))`;
    const buf = new DataView(new ArrayBuffer(1 + 4 + 4 + 4 + 1 + 4 + 4 + 4 + ring.length * 16));
    let o = 0;
    const u8 = (v) => { buf.setUint8(o, v); o += 1; };
    const u32 = (v) => { buf.setUint32(o, v, true); o += 4; };
    u8(1); u32(6 | 0x20000000); u32(4326); u32(1); u8(1); u32(3); u32(1); u32(ring.length);
    for (const [x, y] of ring) { buf.setFloat64(o, x, true); buf.setFloat64(o + 8, y, true); o += 16; }
    const ewkb = [...new Uint8Array(buf.buffer)].map(b => b.toString(16).padStart(2, '0')).join('').toUpperCase();
    expect(geometryLabel(ewkb)).toBe('MULTIPOLYGON');
    for (const sample of [wkt, ewkb]) {
      // Valori distinti come in una colonna reale (niente cache sulla stringa).
      const values = Array.from({ length: 10000 }, (_, i) => `${sample} `.slice(0, sample.length + (i % 2)));
      const t = performance.now();
      const types = new Set(values.map(geometryType));
      const ms = performance.now() - t;
      expect([...types]).toEqual(['MULTIPOLYGON']);
      expect(ms).toBeLessThan(150);
    }
  });
});

describe('un solo lettore', () => {
  it('nessun altro file di src legge WKT o WKB', () => {
    const offenders = sourceFiles(/\.(jsx?)$/)
      .filter(f => f.file !== 'utils/geometry.js')
      .filter(f => /parseWK[TB]|readFloat64|getFloat64|MULTIPOINT\s*Z?\s*\\?\(/.test(stripComments(f.code)))
      .map(f => f.file);
    expect(offenders).toEqual([]);
  });
});
