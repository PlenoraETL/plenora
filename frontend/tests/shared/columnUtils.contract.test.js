/**
 * Contract test — columnUtils (FE-NEXT-1, opzionale).
 *
 * Pure functions: type normalization, type-to-icon/label, SRID extraction,
 * dedup, calculateOutputColumnsRecursive (BFS sul grafo).
 *
 * Non testiamo gli emoji al carattere preciso (anti-flake): asseriamo che
 * il return sia una stringa non vuota e tracciamo solo le mapping critiche
 * (geometry vs base types).
 */

import { describe, it, expect } from 'vitest';
import {
  normalizeColumnType,
  mapApiTypeToStandard,
  calculateOutputColumnsRecursive,
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


describe('calculateOutputColumnsRecursive', () => {
  it('1 tableNode collegato a result -> ritorna le sue colonne visibili', () => {
    const t1 = {
      id: 't1', type: 'tableNode',
      data: {
        table_name: 'Customers', alias: 'C',
        columns: [
          { name: 'id', type: 'int', visible: true },
          { name: 'name', type: 'str', visible: true },
          { name: 'hidden', type: 'str', visible: false },
        ],
      },
    };
    const r = { id: 'r1', type: 'resultNode', data: {} };
    const cols = calculateOutputColumnsRecursive([t1, r], [{ source: 't1', target: 'r1' }], 'r1');

    expect(cols).toHaveLength(2);
    expect(cols.map(c => c.name).sort()).toEqual(['id', 'name']);
    expect(cols[0].source_table).toBe('C');
  });

  it('nomi colonna normalizzati a lowercase', () => {
    const t1 = {
      id: 't1', type: 'tableNode',
      data: { table_name: 't', columns: [{ name: 'ID', type: 'int', visible: true }] },
    };
    const cols = calculateOutputColumnsRecursive([t1], [{ source: 't1', target: 'r1' }], 'r1');
    // r1 non esiste ma traverse parte da r1 -> non trova nulla; usiamo invece traversata diretta da t1
    // FIX: cambia source/target per partire dal nodo esistente
    const cols2 = calculateOutputColumnsRecursive(
      [t1, { id: 'r1', type: 'resultNode', data: {} }],
      [{ source: 't1', target: 'r1' }],
      'r1',
    );
    expect(cols2[0].name).toBe('id'); // lowercase
  });

  it('multi-table merge + dedup per full_name', () => {
    const t1 = {
      id: 't1', type: 'tableNode',
      data: { table_name: 'A', columns: [{ name: 'id', visible: true }] },
    };
    const t2 = {
      id: 't2', type: 'tableNode',
      data: { table_name: 'B', columns: [{ name: 'id', visible: true }] },
    };
    const r = { id: 'r1', type: 'resultNode', data: {} };
    const cols = calculateOutputColumnsRecursive(
      [t1, t2, r],
      [{ source: 't1', target: 'r1' }, { source: 't2', target: 'r1' }],
      'r1',
    );
    // 2 colonne: A.id e B.id (full_name diverso)
    expect(cols).toHaveLength(2);
    expect(cols.map(c => c.full_name).sort()).toEqual(['A.id', 'B.id']);
  });

  it('featureLayerNode raccoglie le sue colonne', () => {
    const f = {
      id: 'f1', type: 'featureLayerNode',
      data: { layer_name: 'Roads', columns: [{ name: 'name', visible: true, type: 'str' }] },
    };
    const r = { id: 'r1', type: 'resultNode', data: {} };
    const cols = calculateOutputColumnsRecursive([f, r], [{ source: 'f1', target: 'r1' }], 'r1');
    expect(cols).toHaveLength(1);
    expect(cols[0].source_table).toBe('Roads');
  });

  it('include calculated_columns con flag isCalculated=true', () => {
    const t = {
      id: 't1', type: 'tableNode',
      data: {
        table_name: 'T',
        columns: [{ name: 'a', visible: true }],
        calculated_columns: [{ name: 'CALC', output_type: 'real' }],
      },
    };
    const r = { id: 'r1', type: 'resultNode', data: {} };
    const cols = calculateOutputColumnsRecursive([t, r], [{ source: 't1', target: 'r1' }], 'r1');
    const calc = cols.find(c => c.isCalculated);
    expect(calc).toBeDefined();
    expect(calc.name).toBe('calc');
    expect(calc.type).toBe('real');
  });

  it('resultNode inesistente -> []', () => {
    const cols = calculateOutputColumnsRecursive([], [], 'missing');
    expect(cols).toEqual([]);
  });
});
