/**
 * Contract test — columnUtils (FE-NEXT-1, opzionale).
 *
 * Pure functions: type normalization, type-to-icon/label, SRID extraction,
 * dedup. (calculateOutputColumnsRecursive e' uscito con la F2 del Blocco A.)
 *
 * Non testiamo gli emoji al carattere preciso (anti-flake): asseriamo che
 * il return sia una stringa non vuota e tracciamo solo le mapping critiche
 * (geometry vs base types).
 */

import { describe, it, expect } from 'vitest';
import {
  normalizeColumnType,
  mapApiTypeToStandard,
  getColumnTypeIcon,
  getColumnTypeLabel,
  getColumnSRID,
  isGeometryType,
  deduplicateColumns,
} from '../../src/utils/columnUtils';


describe('normalizeColumnType', () => {
  it('stringa diretta -> normalizza minuscolo', () => {
    expect(normalizeColumnType('STRING')).toBe('str');
    expect(normalizeColumnType('Number')).toBe('real');
    expect(normalizeColumnType('integer')).toBe('int');
  });

  it('object con type -> usa col.type', () => {
    expect(normalizeColumnType({ type: 'datetime' })).toBe('date');
  });

  it('object con dtype (legacy) -> usa col.dtype', () => {
    expect(normalizeColumnType({ dtype: 'num' })).toBe('real');
  });

  it('object vuoto -> default str', () => {
    expect(normalizeColumnType({})).toBe('str');
  });

  it('tipo non mappato -> ritorna lowercase invariato', () => {
    expect(normalizeColumnType('json')).toBe('json');
  });
});


describe('mapApiTypeToStandard', () => {
  it('mappature base', () => {
    expect(mapApiTypeToStandard('string')).toBe('str');
    expect(mapApiTypeToStandard('integer')).toBe('int');
    expect(mapApiTypeToStandard('boolean')).toBe('bool');
  });

  it('geojson + geometry generic -> geometry', () => {
    expect(mapApiTypeToStandard('geojson')).toBe('geometry');
    expect(mapApiTypeToStandard('geometry')).toBe('geometry');
  });

  it('point/polygon/linestring -> geometry:subtype', () => {
    expect(mapApiTypeToStandard('point')).toBe('geometry:point');
    expect(mapApiTypeToStandard('polygon')).toBe('geometry:polygon');
    expect(mapApiTypeToStandard('multipolygon')).toBe('geometry:multipolygon');
    expect(mapApiTypeToStandard('linestring')).toBe('geometry:line');
  });

  it('null/undefined -> default str', () => {
    expect(mapApiTypeToStandard(null)).toBe('str');
    expect(mapApiTypeToStandard(undefined)).toBe('str');
  });

  it('tipo non mappato -> str', () => {
    expect(mapApiTypeToStandard('weirdtype')).toBe('str');
  });
});


describe('isGeometryType', () => {
  it.each([
    ['geometry', true],
    ['GEOMETRY:point', true],
    ['geometry:polygon:4326', true],
    ['str', false],
    ['', false],
    [null, false],
    [undefined, false],
  ])('%j -> %j', (input, expected) => {
    expect(isGeometryType(input)).toBe(expected);
  });
});


describe('getColumnSRID', () => {
  it('estrae SRID da geometry:polygon:4326', () => {
    expect(getColumnSRID('geometry:polygon:4326')).toBe(4326);
  });

  it('senza SRID -> null', () => {
    expect(getColumnSRID('geometry:point')).toBeNull();
    expect(getColumnSRID('geometry')).toBeNull();
  });

  it('non-geometry -> null', () => {
    expect(getColumnSRID('str')).toBeNull();
    expect(getColumnSRID(null)).toBeNull();
  });

  it('SRID malformato -> null', () => {
    expect(getColumnSRID('geometry:polygon:abc')).toBeNull();
  });
});


describe('getColumnTypeIcon', () => {
  it('ritorna stringa non vuota per tipi base', () => {
    expect(getColumnTypeIcon('int')).toBeTruthy();
    expect(getColumnTypeIcon('str')).toBeTruthy();
    expect(getColumnTypeIcon('bool')).toBeTruthy();
    expect(getColumnTypeIcon('date')).toBeTruthy();
  });

  it('geometry -> icona dedicata diversa da str', () => {
    const geomIcon = getColumnTypeIcon('geometry:polygon:4326');
    const strIcon = getColumnTypeIcon('str');
    expect(geomIcon).toBeTruthy();
    expect(geomIcon).not.toBe(strIcon);
  });

  it('null/undefined -> default icon', () => {
    expect(getColumnTypeIcon(null)).toBeTruthy();
    expect(getColumnTypeIcon(undefined)).toBeTruthy();
  });
});


describe('getColumnTypeLabel', () => {
  it('mappa label IT per tipi base', () => {
    expect(getColumnTypeLabel('int')).toBe('Intero');
    expect(getColumnTypeLabel('real')).toBe('Decimale');
    expect(getColumnTypeLabel('str')).toBe('Testo');
    expect(getColumnTypeLabel('bool')).toBe('Booleano');
    expect(getColumnTypeLabel('date')).toBe('Data/Ora');
  });

  it('geometry con subtype -> "Punto" / "Poligono" / ecc.', () => {
    expect(getColumnTypeLabel('geometry:point')).toBe('Punto');
    expect(getColumnTypeLabel('geometry:polygon')).toBe('Poligono');
    expect(getColumnTypeLabel('geometry:multipolygon')).toBe('Multi-Poligono');
  });

  it('geometry con SRID -> aggiunge "(EPSG:nnn)"', () => {
    expect(getColumnTypeLabel('geometry:polygon:4326')).toBe('Poligono (EPSG:4326)');
  });

  it('null -> default Testo', () => {
    expect(getColumnTypeLabel(null)).toBe('Testo');
  });

  it('tipo sconosciuto -> ritornato invariato', () => {
    expect(getColumnTypeLabel('weirdtype')).toBe('weirdtype');
  });
});


describe('deduplicateColumns', () => {
  it('deduplica per name mantenendo ultima occorrenza', () => {
    const out = deduplicateColumns([
      { name: 'a', type: 'str' },
      { name: 'b', type: 'int' },
      { name: 'a', type: 'real' }, // sostituisce la prima
    ]);
    expect(out).toHaveLength(2);
    expect(out.find(c => c.name === 'a').type).toBe('real');
  });

  it('input vuoto -> []', () => {
    expect(deduplicateColumns([])).toEqual([]);
  });

  it('input senza duplicati -> invariato in lunghezza', () => {
    const cols = [{ name: 'a' }, { name: 'b' }, { name: 'c' }];
    expect(deduplicateColumns(cols)).toHaveLength(3);
  });
});

// calculateOutputColumnsRecursive (Estrai) e' uscito con la F2 del Blocco A
// (regola 4): le colonne dell'Output di Estrai le prevede il server con la
// stessa proiezione SQL dell'esecuzione (tests/shared/columnSource.contract).
