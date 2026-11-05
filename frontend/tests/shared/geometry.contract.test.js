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
import { parseGeometry, geometryLabel, drawableGeometry, UNREADABLE_GEOMETRY } from '../../src/utils/geometry';
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

describe('un solo lettore', () => {
  it('nessun altro file di src legge WKT o WKB', () => {
    const offenders = sourceFiles(/\.(jsx?)$/)
      .filter(f => f.file !== 'utils/geometry.js')
      .filter(f => /parseWK[TB]|readFloat64|getFloat64|MULTIPOINT\s*Z?\s*\\?\(/.test(stripComments(f.code)))
      .map(f => f.file);
    expect(offenders).toEqual([]);
  });
});
