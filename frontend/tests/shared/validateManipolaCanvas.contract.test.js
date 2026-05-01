/**
 * Contract test — validateManipolaCanvas (FE-NEXT-1, FE-T1).
 *
 * Pure function. Differenze chiave vs validateEstrai:
 *  - resultNode ha type='result' (non 'resultNode')
 *  - calcola "connectedToResult" via BFS reverse dagli edges
 *  - controlla orfani (transform/multi_join non nel percorso → result)
 *  - valida parametri transform via registry opzionale
 *  - valida geometry-in-path (ricerca su tutto il percorso, non solo sources)
 */

import { describe, it, expect } from 'vitest';
import { validateManipolaCanvas } from '../../src/utils/validateManipolaCanvas';

const projectSource = (id, overrides = {}) => ({
  id, type: 'project_source',
  data: { table_name: id, alias: id, columns: [{ name: 'col1', visible: true }], ...overrides.data },
  ...overrides,
});
const fileSource = (id, overrides = {}) => ({
  id, type: 'file_source',
  data: { fileName: `${id}.csv`, columns: [{ name: 'col1', visible: true }], ...overrides.data },
  ...overrides,
});
const transformNode = (id, opId, config = {}, inputColumns = []) => ({
  id, type: 'transform',
  data: { transform_op: opId, config, inputColumns },
});
const multiJoinNode = (id) => ({ id, type: 'multi_join', data: {} });
const resultM = (id = 'out', data = {}) => ({ id, type: 'result', data });
const caricaM = (id = 'car', data = {}) => ({ id, type: 'carica', data });
const caricaArcgisM = (id = 'car-ag', data = {}) => ({ id, type: 'caricaArcgis', data });
const edge = (s, t, data = {}) => ({ id: `${s}->${t}`, source: s, target: t, data });

const findIssue = (issues, regex) => issues.find(i => regex.test(i.message));
const errors = (r) => r.issues.filter(i => i.severity === 'error');
const warnings = (r) => r.issues.filter(i => i.severity === 'warning');


describe('validateManipolaCanvas', () => {

  describe('canvas vuoto / mancanza result', () => {
    it('nodes vuoto -> isValid=false', () => {
      const r = validateManipolaCanvas([], []);
      expect(r.isValid).toBe(false);
      expect(r.issues).toHaveLength(1);
      expect(r.issues[0].message).toMatch(/canvas vuoto/i);
    });

    it('nessun result -> errore + early return', () => {
      const r = validateManipolaCanvas([projectSource('s1')], []);
      expect(findIssue(r.issues, /output mancante/i)).toBeDefined();
      expect(r.isValid).toBe(false);
    });
  });

  describe('S1-S2 max-1 result/carica', () => {
    it('2 result -> errore', () => {
      const r = validateManipolaCanvas(
        [projectSource('s1'), resultM('r1'), resultM('r2')],
        [edge('s1','r1')],
      );
      expect(findIssue(r.issues, /troppi nodi output/i)).toBeDefined();
    });

    it('2 carica -> errore', () => {
      const r = validateManipolaCanvas(
        [projectSource('s1'), resultM('r1'),
         caricaM('c1', { connection_id: 'X', schema_name: 's', table_name: 't' }),
         caricaM('c2', { connection_id: 'X', schema_name: 's', table_name: 't' })],
        [edge('s1','r1'), edge('r1','c1'), edge('r1','c2')],
      );
      expect(findIssue(r.issues, /troppi nodi carica/i)).toBeDefined();
    });
  });

  describe('cycle detection', () => {
    it('ciclo s1 -> t1 -> s1 -> errore', () => {
      const r = validateManipolaCanvas(
        [projectSource('s1'), transformNode('t1','op1'), resultM('r1')],
        [edge('s1','t1'), edge('t1','s1'), edge('t1','r1')],
      );
      expect(findIssue(r.issues, /ciclo/i)).toBeDefined();
    });
  });

  describe('R1 - result non connesso', () => {
    it('resultNode senza incoming -> errore', () => {
      const r = validateManipolaCanvas([projectSource('s1'), resultM('r1')], []);
      expect(findIssue(errors(r), /output non connesso/i)).toBeDefined();
    });
  });

  describe('R - sorgenti nel percorso', () => {
    it('result connesso solo via transform-orfano (= no source upstream) -> errore', () => {
      // Caso: result ha incoming da un transform che non ha source upstream.
      // connectedToResult include il transform ma nessuna source -> errore
      // "Nessuna sorgente dati collegata al percorso di output".
      const r = validateManipolaCanvas(
        [transformNode('t1','op1'), resultM('r1')],
        [edge('t1','r1')],
      );
      expect(findIssue(errors(r), /nessuna sorgente.*percorso/i)).toBeDefined();
    });
  });

  describe('R2 geo format senza geometry nel percorso', () => {
    it('format=gpkg senza geometry -> errore', () => {
      const r = validateManipolaCanvas(
        [projectSource('s1'), resultM('r1', { format: 'gpkg' })],
        [edge('s1','r1')],
      );
      expect(findIssue(errors(r), /GPKG.*geometry/i)).toBeDefined();
    });

    it('format=geojson con colonna geometry nel percorso -> ok', () => {
      const s1 = projectSource('s1', { data: { columns: [{ name: 'geom', type: 'geometry', visible: true }] } });
      const r = validateManipolaCanvas([s1, resultM('r1', { format: 'geojson' })], [edge('s1','r1')]);
      expect(findIssue(errors(r), /geometry/i)).toBeUndefined();
    });
  });

  describe('orfani: transform fuori dal percorso', () => {
    it('transform non collegato a result -> warning', () => {
      const r = validateManipolaCanvas(
        [projectSource('s1'), transformNode('t1','op1'), transformNode('t2','op1'), resultM('r1')],
        [edge('s1','r1'), edge('s1','t1')], // t2 isolato
      );
      const w = findIssue(warnings(r), /trasformazione non collegato.*output/i);
      expect(w).toBeDefined();
    });

    it('source isolata (no edge) -> warning', () => {
      const r = validateManipolaCanvas(
        [projectSource('s1'), projectSource('isolata'), resultM('r1')],
        [edge('s1','r1')],
      );
      expect(findIssue(warnings(r), /sorgente non collegata/i)).toBeDefined();
    });
  });

  describe('P1 - tutte colonne deselezionate', () => {
    it('source con tutte colonne visible:false -> errore', () => {
      const s = projectSource('s1', { data: { columns: [{ name: 'a', visible: false }] } });
      const r = validateManipolaCanvas([s, resultM('r1')], [edge('s1','r1')]);
      expect(findIssue(errors(r), /tutte le colonne.*deselezionate/i)).toBeDefined();
    });

    it('colonne come stringhe (no visible flag) -> nessun errore', () => {
      const s = projectSource('s1', { data: { columns: ['col_a', 'col_b'] } });
      const r = validateManipolaCanvas([s, resultM('r1')], [edge('s1','r1')]);
      expect(findIssue(errors(r), /tutte le colonne/i)).toBeUndefined();
    });
  });

  describe('carica DB validation', () => {
    it('carica senza connection/schema/table -> 3 errori', () => {
      const r = validateManipolaCanvas(
        [projectSource('s1'), resultM('r1'), caricaM('c1', {})],
        [edge('s1','r1'), edge('r1','c1')],
      );
      expect(findIssue(errors(r), /connessione database/i)).toBeDefined();
      expect(findIssue(errors(r), /schema di destinazione/i)).toBeDefined();
      expect(findIssue(errors(r), /nome della tabella/i)).toBeDefined();
    });

    it('carica DB write_mode=update senza match_columns -> errore', () => {
      const r = validateManipolaCanvas(
        [projectSource('s1'), resultM('r1'),
         caricaM('c1', { connection_id: 'X', schema_name: 's', table_name: 't', write_mode: 'update' })],
        [edge('s1','r1'), edge('r1','c1')],
      );
      expect(findIssue(errors(r), /update.*colonna chiave/i)).toBeDefined();
    });
  });

  describe('carica ArcGIS validation', () => {
    it('arcgis op=add senza layer_url -> errore C5', () => {
      const r = validateManipolaCanvas(
        [projectSource('s1'), resultM('r1'),
         caricaArcgisM('c1', { arcgis_connection_id: 'X', arcgis_operation: 'add' })],
        [edge('s1','r1'), edge('r1','c1')],
      );
      expect(findIssue(errors(r), /layer di destinazione/i)).toBeDefined();
    });
  });

  describe('transform validation con registry', () => {
    const REGISTRY = {
      filter: {
        name: 'Filter',
        parameters: [
          { id: 'col', label: 'Colonna', type: 'column', required: true },
          { id: 'value', label: 'Valore', type: 'string', required: true },
        ],
      },
      compute: {
        name: 'Compute',
        parameters: [
          { id: 'cols', label: 'Colonne', type: 'columns', required: false },
          { id: 'geom', label: 'Geometria', type: 'geometry_column', required: false },
        ],
      },
    };

    it('T1 - parametro required mancante -> errore', () => {
      const t = transformNode('t1', 'filter', { col: 'col1' /* manca value */ }, [{ name: 'col1' }]);
      const r = validateManipolaCanvas(
        [projectSource('s1'), t, resultM('r1')],
        [edge('s1','t1'), edge('t1','r1')],
        REGISTRY,
      );
      expect(findIssue(errors(r), /Filter.*Valore.*non configurato/i)).toBeDefined();
    });

    it('T2 - column ref inesistente -> warning', () => {
      const t = transformNode('t1', 'filter', { col: 'fantasma', value: 'x' }, [{ name: 'col1' }]);
      const r = validateManipolaCanvas(
        [projectSource('s1'), t, resultM('r1')],
        [edge('s1','t1'), edge('t1','r1')],
        REGISTRY,
      );
      expect(findIssue(warnings(r), /Filter.*fantasma.*non.*disponibile/i)).toBeDefined();
    });

    it('T3 - lista colonne con elemento mancante -> warning', () => {
      const t = transformNode('t1', 'compute', { cols: ['col1', 'fantasma'] }, [{ name: 'col1' }]);
      const r = validateManipolaCanvas(
        [projectSource('s1'), t, resultM('r1')],
        [edge('s1','t1'), edge('t1','r1')],
        REGISTRY,
      );
      expect(findIssue(warnings(r), /fantasma.*non.*disponibile/i)).toBeDefined();
    });

    it('T4 - geometry_column su colonna non-geometry -> errore', () => {
      const t = transformNode('t1', 'compute', { geom: 'col1' }, [{ name: 'col1', type: 'string' }]);
      const r = validateManipolaCanvas(
        [projectSource('s1'), t, resultM('r1')],
        [edge('s1','t1'), edge('t1','r1')],
        REGISTRY,
      );
      expect(findIssue(errors(r), /col1.*non.*geometry/i)).toBeDefined();
    });

    it('transform senza transform_op selezionato -> errore', () => {
      const t = { id: 't1', type: 'transform', data: {} };
      const r = validateManipolaCanvas(
        [projectSource('s1'), t, resultM('r1')],
        [edge('s1','t1'), edge('t1','r1')],
        REGISTRY,
      );
      expect(findIssue(errors(r), /senza operazione/i)).toBeDefined();
    });

    it('senza registry: transform skippato (no errori T*)', () => {
      const t = transformNode('t1', 'filter', {}, [{ name: 'col1' }]);
      const r = validateManipolaCanvas(
        [projectSource('s1'), t, resultM('r1')],
        [edge('s1','t1'), edge('t1','r1')],
        // niente registry
      );
      expect(findIssue(errors(r), /Filter/)).toBeUndefined();
    });
  });

  describe('happy path canvas valido', () => {
    it('source -> result diretto, csv, colonne ok -> isValid=true', () => {
      const r = validateManipolaCanvas(
        [projectSource('s1'), resultM('r1', { format: 'csv' })],
        [edge('s1','r1')],
      );
      expect(r.isValid).toBe(true);
      expect(errors(r)).toHaveLength(0);
    });

    it('source -> transform -> result + carica DB completo -> isValid=true', () => {
      const t = transformNode('t1', 'filter', { col: 'col1', value: 'x' }, [{ name: 'col1' }]);
      const REGISTRY = { filter: { name: 'Filter', parameters: [
        { id: 'col', type: 'column', required: true },
        { id: 'value', type: 'string', required: true },
      ]}};
      const r = validateManipolaCanvas(
        [projectSource('s1'), t, resultM('r1', { format: 'csv' }),
         caricaM('c1', { connection_id: 'X', schema_name: 's', table_name: 't', write_mode: 'create' })],
        [edge('s1','t1'), edge('t1','r1'), edge('r1','c1')],
        REGISTRY,
      );
      expect(errors(r)).toHaveLength(0);
      expect(r.isValid).toBe(true);
    });
  });
});
