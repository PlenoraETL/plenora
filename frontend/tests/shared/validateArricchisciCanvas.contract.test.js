/**
 * Contract test — validateArricchisciCanvas (FE-NEXT-1, FE-T1).
 *
 * Pure function. Aspetti specifici di Arricchisci:
 *  - api_enrich nodes con apiId richiesto
 *  - upstream column tracking (BFS topologico)
 *  - "needs source" euristica via static_parameters / mode='fixed'
 *  - template syntax {col1} nei param mapping
 *  - availableConnectionIds Set per validare api_id stale
 *  - isolated check copre source + enricher + result (severity diversa)
 */

import { describe, it, expect } from 'vitest';
import { validateArricchisciCanvas } from '../../src/utils/validateArricchisciCanvas';

const projectSource = (id, columns = [{ name: 'city' }, { name: 'cap' }]) => ({
  id, type: 'project_source', data: { table_name: id, columns },
});
const fileSource = (id, columns = ['city', 'cap']) => ({
  id, type: 'file_source', data: { fileName: `${id}.csv`, columns },
});
const apiEnrichNode = (id, overrides = {}) => ({
  id, type: 'api_enrich',
  data: {
    label: id,
    api_id: 'api-1',
    parameters: [],
    output_mapping: [],
    ...overrides,
  },
});
const resultA = (id = 'out') => ({ id, type: 'result', data: {} });
const edge = (s, t) => ({ id: `${s}->${t}`, source: s, target: t });

const findIssue = (issues, regex) => issues.find(i => regex.test(i.message));
const errors = (r) => r.issues.filter(i => i.severity === 'error');
const warnings = (r) => r.issues.filter(i => i.severity === 'warning');


describe('validateArricchisciCanvas', () => {

  describe('canvas vuoto / mancanza result', () => {
    it('vuoto -> isValid=false', () => {
      const r = validateArricchisciCanvas([], []);
      expect(r.isValid).toBe(false);
      expect(r.issues[0].message).toMatch(/canvas vuoto/i);
    });

    it('senza result -> errore (non early-return: continua altri check)', () => {
      const r = validateArricchisciCanvas([projectSource('s1')], []);
      expect(findIssue(errors(r), /output mancante/i)).toBeDefined();
    });
  });

  describe('nessun enricher presente', () => {
    it('canvas con source + result ma ZERO enricher -> errore ARRICCHISCI_NO_ENRICHER', () => {
      const r = validateArricchisciCanvas(
        [projectSource('s1'), resultA('r1')],
        [edge('s1','r1')],
      );
      expect(r.isValid).toBe(false);
      expect(findIssue(errors(r), /almeno un nodo api enrich/i)).toBeDefined();
    });

    it('canvas con almeno un enricher -> no ARRICCHISCI_NO_ENRICHER', () => {
      const e = apiEnrichNode('e1', {
        parameters: [{ name: 'city', required: true, mode: 'fixed' }],
      });
      const r = validateArricchisciCanvas(
        [projectSource('s1'), e, resultA('r1')],
        [edge('s1','e1'), edge('e1','r1')],
      );
      expect(findIssue(errors(r), /almeno un nodo api enrich/i)).toBeUndefined();
    });
  });

  describe('necessita di source vs enricher autonomo', () => {
    it('enricher con required+mappabile, NO source -> errore', () => {
      const enricher = apiEnrichNode('e1', {
        parameters: [{ name: 'city', required: true, mode: 'mapped' }],
      });
      const r = validateArricchisciCanvas([enricher, resultA('r1')], [edge('e1','r1')]);
      expect(findIssue(errors(r), /nessuna sorgente dati/i)).toBeDefined();
    });

    it('enricher con required ma mode=fixed -> NON serve source', () => {
      const enricher = apiEnrichNode('e1', {
        parameters: [{ name: 'city', required: true, mode: 'fixed' }],
      });
      const r = validateArricchisciCanvas([enricher, resultA('r1')], [edge('e1','r1')]);
      expect(findIssue(errors(r), /nessuna sorgente/i)).toBeUndefined();
    });

    it('enricher con required coperto da static_parameters -> NON serve source', () => {
      const enricher = apiEnrichNode('e1', {
        parameters: [{ name: 'city', required: true }],
        static_parameters: { city: 'Roma' },
      });
      const r = validateArricchisciCanvas([enricher, resultA('r1')], [edge('e1','r1')]);
      expect(findIssue(errors(r), /nessuna sorgente/i)).toBeUndefined();
    });

    it('enricher senza parametri required -> NON serve source', () => {
      const enricher = apiEnrichNode('e1', { parameters: [{ name: 'city' /* no required */ }] });
      const r = validateArricchisciCanvas([enricher, resultA('r1')], [edge('e1','r1')]);
      expect(findIssue(errors(r), /nessuna sorgente/i)).toBeUndefined();
    });
  });

  describe('apiId presence + availableConnectionIds', () => {
    it('enricher senza api_id -> errore', () => {
      const e = apiEnrichNode('e1', { api_id: null });
      const r = validateArricchisciCanvas(
        [projectSource('s1'), e, resultA('r1')],
        [edge('s1','e1'), edge('e1','r1')],
      );
      expect(findIssue(errors(r), /nessuna api connessa/i)).toBeDefined();
    });

    it('api_id non presente in availableConnectionIds -> errore "eliminata"', () => {
      const e = apiEnrichNode('e1', { api_id: 'api-stale' });
      const r = validateArricchisciCanvas(
        [projectSource('s1'), e, resultA('r1')],
        [edge('s1','e1'), edge('e1','r1')],
        new Set(['api-2', 'api-3']),
      );
      expect(findIssue(errors(r), /eliminata/i)).toBeDefined();
    });

    it('api_id presente in availableConnectionIds -> ok', () => {
      const e = apiEnrichNode('e1', { api_id: 'api-1' });
      const r = validateArricchisciCanvas(
        [projectSource('s1'), e, resultA('r1')],
        [edge('s1','e1'), edge('e1','r1')],
        new Set(['api-1']),
      );
      expect(findIssue(errors(r), /api/i)).toBeUndefined();
    });
  });

  describe('parametri required senza mapping', () => {
    it('required senza mapping + senza upstream column -> errore', () => {
      const e = apiEnrichNode('e1', {
        parameters: [{ name: 'city', required: true, mode: 'mapped' }],
        param_mapping: {}, // niente mapping
      });
      const r = validateArricchisciCanvas(
        [projectSource('s1', []), e, resultA('r1')], // source con 0 colonne
        [edge('s1','e1'), edge('e1','r1')],
      );
      expect(findIssue(errors(r), /city/i)).toBeDefined();
    });

    it('required mappato a colonna upstream esistente -> ok', () => {
      const e = apiEnrichNode('e1', {
        parameters: [{ name: 'city', required: true, mode: 'mapped' }],
        param_mapping: { city: 'cap' }, // 'cap' esiste in source
      });
      const r = validateArricchisciCanvas(
        [projectSource('s1'), e, resultA('r1')],
        [edge('s1','e1'), edge('e1','r1')],
      );
      expect(findIssue(errors(r), /city/i)).toBeUndefined();
    });

    it('required mappato a colonna upstream INESISTENTE -> errore', () => {
      const e = apiEnrichNode('e1', {
        parameters: [{ name: 'city', required: true, mode: 'mapped' }],
        param_mapping: { city: 'fantasma' },
      });
      const r = validateArricchisciCanvas(
        [projectSource('s1'), e, resultA('r1')],
        [edge('s1','e1'), edge('e1','r1')],
      );
      expect(findIssue(errors(r), /city/i)).toBeDefined();
    });

    it('mapping con suffisso "(Testo)" stripped prima del confronto', () => {
      const e = apiEnrichNode('e1', {
        parameters: [{ name: 'city', required: true, mode: 'mapped' }],
        param_mapping: { city: 'cap (Testo)' }, // suffisso del dropdown
      });
      const r = validateArricchisciCanvas(
        [projectSource('s1'), e, resultA('r1')],
        [edge('s1','e1'), edge('e1','r1')],
      );
      expect(findIssue(errors(r), /city/i)).toBeUndefined();
    });

    it('template "{X},{Y}" con X e Y entrambi presenti upstream -> ok', () => {
      const e = apiEnrichNode('e1', {
        parameters: [{ name: 'addr', required: true, mode: 'mapped' }],
        param_mapping: { addr: '{city},{cap}' },
      });
      const r = validateArricchisciCanvas(
        [projectSource('s1'), e, resultA('r1')],
        [edge('s1','e1'), edge('e1','r1')],
      );
      expect(findIssue(errors(r), /addr/i)).toBeUndefined();
    });

    it('template con un placeholder mancante upstream -> errore', () => {
      const e = apiEnrichNode('e1', {
        parameters: [{ name: 'addr', required: true, mode: 'mapped' }],
        param_mapping: { addr: '{city},{fantasma}' },
      });
      const r = validateArricchisciCanvas(
        [projectSource('s1'), e, resultA('r1')],
        [edge('s1','e1'), edge('e1','r1')],
      );
      expect(findIssue(errors(r), /addr/i)).toBeDefined();
    });
  });

  describe('upstream column propagation (B-FE-69)', () => {
    it('source -> enricher1 -> enricher2: enricher2 vede colonne aggiunte da enricher1.output_mapping', () => {
      const e1 = apiEnrichNode('e1', {
        api_id: 'api-1',
        parameters: [],
        output_mapping: [{ column: 'lat' }, { column: 'lon' }],
      });
      const e2 = apiEnrichNode('e2', {
        api_id: 'api-2',
        parameters: [{ name: 'pos', required: true, mode: 'mapped' }],
        param_mapping: { pos: '{lat},{lon}' }, // template usa colonne create da e1
      });
      const r = validateArricchisciCanvas(
        [projectSource('s1'), e1, e2, resultA('r1')],
        [edge('s1','e1'), edge('e1','e2'), edge('e2','r1')],
      );
      expect(findIssue(errors(r), /pos/i)).toBeUndefined();
    });
  });

  describe('isolated nodes', () => {
    it('source isolata (no edge) -> warning', () => {
      // label viene usata nel messaggio; senza label il fallback è node.type
      const isolata = { ...projectSource('isolata'), data: { ...projectSource('isolata').data, label: 'IsolataLabel' } };
      const r = validateArricchisciCanvas(
        [projectSource('s1'), isolata, resultA('r1')],
        [edge('s1','r1')],
      );
      expect(findIssue(warnings(r), /IsolataLabel.*non collegato/i)).toBeDefined();
    });

    it('enricher isolato -> errore (severity più alta)', () => {
      const e = apiEnrichNode('e_iso', {
        parameters: [{ name: 'x', required: true, mode: 'fixed' }], // no source needed
      });
      const r = validateArricchisciCanvas(
        [projectSource('s1'), e, resultA('r1')],
        [edge('s1','r1')],
      );
      expect(findIssue(errors(r), /e_iso.*non collegato/i)).toBeDefined();
    });

    it('result isolato -> errore', () => {
      const r = validateArricchisciCanvas(
        [projectSource('s1'), resultA('r_iso')],
        [edge('s1','s1')], // edge fittizio per non avere result orfano in early-return path
      );
      // result non in edges -> isolato
      expect(findIssue(errors(r), /result.*non collegato/i)).toBeDefined();
    });
  });

  describe('happy path', () => {
    it('source -> enricher -> result + apiId valido + mapping ok', () => {
      const e = apiEnrichNode('e1', {
        api_id: 'api-1',
        parameters: [{ name: 'city', required: true, mode: 'mapped' }],
        param_mapping: { city: 'cap' },
        output_mapping: [{ column: 'enriched_field' }],
      });
      const r = validateArricchisciCanvas(
        [projectSource('s1'), e, resultA('r1')],
        [edge('s1','e1'), edge('e1','r1')],
      );
      expect(r.isValid).toBe(true);
      expect(errors(r)).toHaveLength(0);
    });

    it('canvas con file_source + enricher autonomo + result -> ok', () => {
      const e = apiEnrichNode('e1', {
        api_id: 'api-1',
        parameters: [{ name: 'city', required: true, mode: 'fixed' }],
      });
      const r = validateArricchisciCanvas(
        [fileSource('f1'), e, resultA('r1')],
        [edge('f1','e1'), edge('e1','r1')],
      );
      expect(r.isValid).toBe(true);
    });
  });
});
