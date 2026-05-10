/**
 * Contract test — validateEstraiCanvas (FE-NEXT-1, FE-T1).
 *
 * Pure function. Decide se "Esegui" e' abilitato lato UI: bug silente
 * = utente blocca canvas valido OPPURE esegue canvas invalido. Cover
 * delle 18 regole (S1-S5, R1-R2, J1-J3, P1-P2, C1-C7) raggruppate per
 * categoria. Snapshot evitati: si asserisce solo presenza/severity dei
 * messaggi rilevanti per non legarsi al wording esatto.
 */

import { describe, it, expect } from 'vitest';
import { validateEstraiCanvas } from '../../src/utils/validateEstraiCanvas';

const tableNode = (id, overrides = {}) => ({
  id, type: 'tableNode',
  data: { table_name: id, alias: id, columns: [{ name: 'col1', visible: true }], ...overrides.data },
  ...overrides,
});
const resultNode = (id = 'out', data = {}) => ({ id, type: 'resultNode', data });
const caricaNode = (id = 'car', data = {}) => ({ id, type: 'carica', data });
const caricaArcgisNode = (id = 'car-ag', data = {}) => ({ id, type: 'caricaArcgis', data });
const edge = (s, t, data = {}) => ({ id: `${s}->${t}`, source: s, target: t, data });

const findIssue = (issues, regex) => issues.find(i => regex.test(i.message));
const errors = (r) => r.issues.filter(i => i.severity === 'error');
const warnings = (r) => r.issues.filter(i => i.severity === 'warning');


describe('validateEstraiCanvas', () => {

  describe('S1 - canvas vuoto', () => {
    it('nodes vuoto -> isValid=false + 1 errore', () => {
      const r = validateEstraiCanvas([], []);
      expect(r.isValid).toBe(false);
      expect(r.issues).toHaveLength(1);
      expect(r.issues[0].severity).toBe('error');
      expect(r.issues[0].message).toMatch(/canvas vuoto/i);
    });

    it('nodes null -> trattato come vuoto', () => {
      const r = validateEstraiCanvas(null, []);
      expect(r.isValid).toBe(false);
      expect(r.issues).toHaveLength(1);
    });
  });

  describe('S2-S3 - max-1 di resultNode/carica', () => {
    it('2 resultNode -> errore', () => {
      const r = validateEstraiCanvas(
        [tableNode('t1'), resultNode('r1'), resultNode('r2'), edge('t1','r1')],
        [edge('t1','r1')],
      );
      expect(findIssue(r.issues, /troppi nodi output/i)).toBeDefined();
      expect(r.isValid).toBe(false);
    });

    it('2 carica -> errore', () => {
      const r = validateEstraiCanvas(
        [tableNode('t1'), resultNode('r1'), caricaNode('c1', { connection_id: 'X', schema_name: 's', table_name: 't' }), caricaNode('c2', { connection_id: 'X', schema_name: 's', table_name: 't' })],
        [edge('t1','r1'), edge('r1','c1'), edge('r1','c2')],
      );
      expect(findIssue(r.issues, /troppi nodi carica/i)).toBeDefined();
    });
  });

  describe('S4 - cycle detection', () => {
    it('ciclo t1 -> t2 -> t1 -> errore', () => {
      const nodes = [tableNode('t1'), tableNode('t2'), resultNode('r1')];
      const edges = [edge('t1','t2',{source_column:'col1', target_column:'col1'}), edge('t2','t1',{source_column:'col1', target_column:'col1'}), edge('t1','r1')];
      const r = validateEstraiCanvas(nodes, edges);
      expect(findIssue(r.issues, /ciclo/i)).toBeDefined();
      expect(r.isValid).toBe(false);
    });
  });

  describe('S5 - sorgenti isolate', () => {
    it('table senza alcun edge -> warning, non blocca', () => {
      const r = validateEstraiCanvas(
        [tableNode('t1'), tableNode('isolata'), resultNode('r1')],
        [edge('t1','r1')],
      );
      const w = findIssue(warnings(r), /sorgente non collegata/i);
      expect(w).toBeDefined();
      expect(w.nodeId).toBe('isolata');
    });
  });

  describe('mancanza resultNode -> early return', () => {
    it('canvas senza resultNode -> errore + return', () => {
      const r = validateEstraiCanvas([tableNode('t1')], []);
      expect(findIssue(r.issues, /output mancante/i)).toBeDefined();
      expect(r.isValid).toBe(false);
    });
  });

  describe('R1 - resultNode non connesso', () => {
    it('resultNode senza incoming edges -> errore', () => {
      const r = validateEstraiCanvas([tableNode('t1'), resultNode('r1')], []);
      const e = findIssue(errors(r), /output non connesso/i);
      expect(e).toBeDefined();
      expect(e.nodeId).toBe('r1');
    });
  });

  describe('R2 - formato geo senza geometry', () => {
    it('format=geojson + nessuna colonna geometry -> errore', () => {
      const r = validateEstraiCanvas(
        [tableNode('t1'), resultNode('r1', { format: 'geojson' })],
        [edge('t1','r1')],
      );
      expect(findIssue(errors(r), /GEOJSON.*geometry/i)).toBeDefined();
    });

    it('format=geojson + colonna geometry presente -> nessun errore R2', () => {
      const r = validateEstraiCanvas(
        [{ ...tableNode('t1'), data: { columns: [{ name: 'geom', type: 'geometry', visible: true }] } }, resultNode('r1', { format: 'geojson' })],
        [edge('t1','r1')],
      );
      expect(findIssue(errors(r), /geometry/i)).toBeUndefined();
    });

    it('format=csv -> nessun check geometry', () => {
      const r = validateEstraiCanvas(
        [tableNode('t1'), resultNode('r1', { format: 'csv' })],
        [edge('t1','r1')],
      );
      expect(findIssue(errors(r), /geometry/i)).toBeUndefined();
    });
  });

  describe('J1 - join standard non configurato', () => {
    it('edge tra 2 tabelle senza source/target column -> errore', () => {
      const r = validateEstraiCanvas(
        [tableNode('t1'), tableNode('t2'), resultNode('r1')],
        [edge('t1','t2'), edge('t2','r1')],
      );
      expect(findIssue(errors(r), /join non configurato/i)).toBeDefined();
    });
  });

  describe('J2 - spatial join senza geom column', () => {
    it('is_spatial=true senza source_geom_column -> errore', () => {
      const r = validateEstraiCanvas(
        [tableNode('t1'), tableNode('t2'), resultNode('r1')],
        [edge('t1','t2',{is_spatial:true}), edge('t2','r1')],
      );
      expect(findIssue(errors(r), /join spaziale/i)).toBeDefined();
    });
  });

  describe('J3 - colonna join inesistente -> warning', () => {
    it('source_column non esiste in t1 -> warning', () => {
      const r = validateEstraiCanvas(
        [tableNode('t1'), tableNode('t2'), resultNode('r1')],
        [edge('t1','t2',{source_column:'fantasma', target_column:'col1'}), edge('t2','r1')],
      );
      expect(findIssue(warnings(r), /fantasma.*non trovata/i)).toBeDefined();
    });
  });

  describe('P1 - tutte le colonne deselezionate', () => {
    it('table con colonne tutte visible:false -> errore', () => {
      const r = validateEstraiCanvas(
        [{ ...tableNode('t1'), data: { columns: [{ name: 'a', visible: false }, { name: 'b', visible: false }] } }, resultNode('r1')],
        [edge('t1','r1')],
      );
      expect(findIssue(errors(r), /tutte le colonne.*deselezionate/i)).toBeDefined();
    });

    it('table senza columns array -> nessun errore P1 (caso schema-less)', () => {
      const r = validateEstraiCanvas(
        [{ ...tableNode('t1'), data: { columns: [] } }, resultNode('r1')],
        [edge('t1','r1')],
      );
      expect(findIssue(errors(r), /tutte le colonne/i)).toBeUndefined();
    });
  });

  describe('P2 - conflitti nome colonne', () => {
    it('stesso nome in 2 tabelle senza alias -> warning', () => {
      const t1 = { ...tableNode('t1'), data: { columns: [{ name: 'id', visible: true }] } };
      const t2 = { ...tableNode('t2'), data: { columns: [{ name: 'id', visible: true }] } };
      const r = validateEstraiCanvas([t1, t2, resultNode('r1')], [edge('t1','r1'), edge('t2','r1')]);
      expect(findIssue(warnings(r), /conflitto nome colonna.*id/i)).toBeDefined();
    });
  });

  describe('C1-C3 - validazione carica DB', () => {
    it('carica senza connection_id/schema/table -> 3 errori', () => {
      const r = validateEstraiCanvas(
        [tableNode('t1'), resultNode('r1'), caricaNode('c1', {})],
        [edge('t1','r1'), edge('r1','c1')],
      );
      expect(findIssue(errors(r), /connessione database/i)).toBeDefined();
      expect(findIssue(errors(r), /schema di destinazione/i)).toBeDefined();
      expect(findIssue(errors(r), /nome della tabella/i)).toBeDefined();
    });

    it('carica DB upsert senza match_columns -> errore', () => {
      const r = validateEstraiCanvas(
        [tableNode('t1'), resultNode('r1'), caricaNode('c1', { connection_id: 'X', schema_name: 's', table_name: 't', write_mode: 'upsert' })],
        [edge('t1','r1'), edge('r1','c1')],
      );
      expect(findIssue(errors(r), /upsert.*colonna chiave/i)).toBeDefined();
    });

    it('carica non collegato al resultNode -> errore C1', () => {
      const r = validateEstraiCanvas(
        [tableNode('t1'), resultNode('r1'), caricaNode('c1', { connection_id: 'X', schema_name: 's', table_name: 't' })],
        [edge('t1','r1'), edge('t1','c1')], // c1 collegato a t1 ma non a r1
      );
      expect(findIssue(errors(r), /carica non collegato.*output/i)).toBeDefined();
    });

    it('carica REST non richiede connection/schema/table DB', () => {
      const r = validateEstraiCanvas(
        [tableNode('t1'), resultNode('r1'), caricaNode('c1', {
          dest_type: 'rest',
          rest_url: 'https://api.example.com/items',
          rest_method: 'POST',
        })],
        [edge('t1','r1'), edge('r1','c1')],
      );
      expect(findIssue(errors(r), /connessione database/i)).toBeUndefined();
      expect(findIssue(errors(r), /schema di destinazione/i)).toBeUndefined();
      expect(findIssue(errors(r), /nome della tabella/i)).toBeUndefined();
    });
  });

  describe('C4-C7 - validazione carica ArcGIS', () => {
    it('arcgis senza connection -> errore C4', () => {
      const r = validateEstraiCanvas(
        [tableNode('t1'), resultNode('r1'), caricaArcgisNode('c1', { arcgis_operation: 'add' })],
        [edge('t1','r1'), edge('r1','c1')],
      );
      expect(findIssue(errors(r), /arcgis.*connessione/i)).toBeDefined();
    });

    it('arcgis op=add senza arcgis_layer_url -> errore C5', () => {
      const r = validateEstraiCanvas(
        [tableNode('t1'), resultNode('r1'), caricaArcgisNode('c1', { arcgis_connection_id: 'X', arcgis_operation: 'add' })],
        [edge('t1','r1'), edge('r1','c1')],
      );
      expect(findIssue(errors(r), /layer di destinazione/i)).toBeDefined();
    });

    it('arcgis create_service senza nome -> errore C6', () => {
      const r = validateEstraiCanvas(
        [tableNode('t1'), resultNode('r1'), caricaArcgisNode('c1', { arcgis_connection_id: 'X', arcgis_operation: 'create_service' })],
        [edge('t1','r1'), edge('r1','c1')],
      );
      expect(findIssue(errors(r), /nuovo feature service/i)).toBeDefined();
    });

    it('arcgis update senza match_column -> errore C7', () => {
      const r = validateEstraiCanvas(
        [tableNode('t1'), resultNode('r1'), caricaArcgisNode('c1', { arcgis_connection_id: 'X', arcgis_operation: 'update', arcgis_layer_url: 'u' })],
        [edge('t1','r1'), edge('r1','c1')],
      );
      expect(findIssue(errors(r), /colonna chiave.*update/i)).toBeDefined();
    });
  });

  describe('canvas valido happy path', () => {
    it('1 table + resultNode connesso, csv, colonne visibili -> isValid=true', () => {
      const r = validateEstraiCanvas(
        [tableNode('t1'), resultNode('r1', { format: 'csv' })],
        [edge('t1','r1')],
      );
      expect(r.isValid).toBe(true);
      expect(errors(r)).toHaveLength(0);
    });

    it('canvas valido con join configurato + carica DB completo', () => {
      const t1 = { ...tableNode('t1'), data: { columns: [{ name: 'id', visible: true }] } };
      const t2 = { ...tableNode('t2'), data: { columns: [{ name: 'id', visible: true, alias: 't2_id' }] } };
      const r = validateEstraiCanvas(
        [t1, t2, resultNode('r1', { format: 'csv' }), caricaNode('c1', { connection_id: 'X', schema_name: 's', table_name: 't', write_mode: 'create' })],
        [
          edge('t1','t2',{source_column:'id', target_column:'id'}),
          edge('t2','r1'),
          edge('r1','c1'),
        ],
      );
      expect(errors(r)).toHaveLength(0);
      expect(r.isValid).toBe(true);
    });
  });
});
