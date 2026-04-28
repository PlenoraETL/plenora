/**
 * canvasAccessControl.contract.test.js — helper puri per pre-flight UX.
 *
 * Backend (RB.4.B / RB.7.B) marca i nodi con risorse non visibili al
 * gruppo del progetto. Il frontend deve poter rispondere alla domanda
 * "questo canvas e' eseguibile?" senza chiamare il backend.
 */

import { describe, it, expect } from 'vitest';
import {
  getAccessDeniedNodes,
  hasAccessDeniedNodes,
  describeAccessDeniedNodes,
} from '../../src/utils/canvasAccessControl';


describe('canvasAccessControl — getAccessDeniedNodes', () => {
  it('ritorna [] quando nessun nodo ha access_denied', () => {
    const nodes = [
      { id: 'a', type: 'file_source', data: { file_source_id: 'f1' } },
      { id: 'b', type: 'api_enrich', data: { api_id: 'api1' } },
    ];
    expect(getAccessDeniedNodes(nodes)).toEqual([]);
  });

  it('filtra solo i nodi con data.access_denied === true', () => {
    const nodes = [
      { id: 'ok', type: 'file_source', data: {} },
      { id: 'denied1', type: 'file_source', data: { access_denied: true, access_denied_reason: 'foo' } },
      { id: 'denied2', type: 'project_source', data: { access_denied: true } },
      { id: 'falsy', type: 'file_source', data: { access_denied: false } },
    ];
    const out = getAccessDeniedNodes(nodes);
    expect(out.map(n => n.id)).toEqual(['denied1', 'denied2']);
  });

  it('truthy non-true non e\' interpretato come denied (strict equality)', () => {
    const nodes = [
      { id: 'a', data: { access_denied: 1 } },         // numero, non true
      { id: 'b', data: { access_denied: 'yes' } },     // stringa
      { id: 'c', data: { access_denied: {} } },        // oggetto
    ];
    expect(getAccessDeniedNodes(nodes)).toEqual([]);
  });

  it('input malformati non crashano', () => {
    expect(getAccessDeniedNodes(null)).toEqual([]);
    expect(getAccessDeniedNodes(undefined)).toEqual([]);
    expect(getAccessDeniedNodes('not an array')).toEqual([]);
    expect(getAccessDeniedNodes([null, undefined, {}, { id: 'x' }])).toEqual([]);
  });
});


describe('canvasAccessControl — hasAccessDeniedNodes', () => {
  it('false per canvas pulito', () => {
    expect(hasAccessDeniedNodes([])).toBe(false);
    expect(hasAccessDeniedNodes([{ id: 'a', data: {} }])).toBe(false);
  });

  it('true se almeno un nodo e\' denied', () => {
    expect(hasAccessDeniedNodes([
      { id: 'a', data: {} },
      { id: 'b', data: { access_denied: true } },
    ])).toBe(true);
  });

  it('false su input invalidi (no throw)', () => {
    expect(hasAccessDeniedNodes(null)).toBe(false);
    expect(hasAccessDeniedNodes(undefined)).toBe(false);
  });
});


describe('canvasAccessControl — describeAccessDeniedNodes', () => {
  it('ritorna null se nessun denied (semantica truthy per `if (descrizione)`)', () => {
    expect(describeAccessDeniedNodes([])).toBeNull();
    expect(describeAccessDeniedNodes([{ id: 'a', data: {} }])).toBeNull();
    expect(describeAccessDeniedNodes(null)).toBeNull();
  });

  it('ritorna count, nodeIds, summary per nodi denied', () => {
    const nodes = [
      { id: 'ok', data: {} },
      {
        id: 'fs_bad',
        type: 'file_source',
        data: {
          access_denied: true,
          access_denied_reason: "File sorgente 'file_extra_excel_UDPN_X' non visibile al gruppo del progetto",
          inaccessible_resource_type: 'file_source',
        },
      },
      {
        id: 'ag_bad',
        type: 'carica_arcgis',
        data: {
          access_denied: true,
          access_denied_reason: "Connessione ArcGIS 'arcgis_Urby_Old' non visibile al gruppo del progetto",
          inaccessible_resource_type: 'arcgis_connection',
        },
      },
    ];
    const out = describeAccessDeniedNodes(nodes);
    expect(out).not.toBeNull();
    expect(out.count).toBe(2);
    expect(out.nodeIds).toEqual(['fs_bad', 'ag_bad']);
    expect(out.summary).toContain('file_source:');
    expect(out.summary).toContain('arcgis_connection:');
    expect(out.summary).toContain('file_extra_excel_UDPN_X');
    expect(out.summary).toContain('arcgis_Urby_Old');
  });

  it('fallback: usa node.type se inaccessible_resource_type assente', () => {
    const nodes = [
      {
        id: 'x',
        type: 'project_source',
        data: { access_denied: true, access_denied_reason: 'progetto X non visibile' },
      },
    ];
    const out = describeAccessDeniedNodes(nodes);
    expect(out.summary).toContain('project_source:');
    expect(out.summary).toContain('progetto X non visibile');
  });

  it('fallback: usa id come reason se access_denied_reason assente', () => {
    const nodes = [
      { id: 'orphan_node', type: 'file_source', data: { access_denied: true } },
    ];
    const out = describeAccessDeniedNodes(nodes);
    expect(out.summary).toContain('id=orphan_node');
  });

  it('input malformati non crashano', () => {
    const nodes = [
      { id: 'a', data: { access_denied: true } },  // no reason, no resource_type, no node.type
    ];
    const out = describeAccessDeniedNodes(nodes);
    expect(out.count).toBe(1);
    expect(out.summary).toContain('risorsa');  // fallback ultimo
    expect(out.summary).toContain('id=a');
  });
});
