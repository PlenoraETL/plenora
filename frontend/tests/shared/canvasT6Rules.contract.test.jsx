/**
 * Fase 5, T6 — residui dei canvas: una regola per classe, verificata su
 * tutti i casi della classe.
 *
 *  N6     archi: ogni campo di JOIN_EDGE_FIELDS fa il giro salvataggio -> JSON -> caricamento.
 *  R3.10  multi-join: con 3+ sorgenti le coppie non entrano nel salvato (ne' in join_keys).
 *  R2.6   nodi nuovi col tipo di rendering del registro (come il deserializer).
 *  R2.10/N11 copie della connessione API mai salvate ne' caricate.
 *  R3.20  tipi dell'API gia' del canvas restano se stessi.
 *  N7     ripiego dichiarato dei Valori statici dal `field_type`.
 *  R3.15  colonne del layer ArcGIS: una costruzione, tipo dal backend, alias non come rinomina.
 *  R9-U N7 titoli generati riconosciuti senza dipendere dal catalogo caricato.
 *  R3.16  GROUP BY/ORDER BY di Estrai: nome qualificato quando e' in piu' tabelle.
 *  R3.17  alias delle aggregazioni unici e quelli scritti dall'utente non si toccano.
 *  R3.18/R3.21 select su un elenco che cambia: valore vuoto e valore sparito visibili.
 *  R3.19  il progetto aperto non e' tra le sorgenti.
 *  R9-U N1/BA3 chiavi legacy della Mappa valori conservate col loro tipo.
 *  R9-U N2 cambio di operazione: un'azione sola che toglie i collegamenti.
 */
import React from 'react';
import fs from 'node:fs';
import path from 'node:path';
import { render, screen, cleanup } from '@testing-library/react';
import { describe, it, expect, afterEach } from 'vitest';
import { JOIN_EDGE_FIELDS, NODE_TYPES, RESOURCE_COPY_KEYS, renderNodeType } from '../../src/utils/nodeTypeRegistry';
import { serializeCanvas, serializeEdge } from '../../src/utils/canvasSerializer';
import { createEdgeFromData, deserializeNodes } from '../../src/utils/nodeFactories';
import { mapApiTypeToStandard } from '../../src/utils/columnUtils';
import { columnsOf, STATIC_FIELD_TYPES } from '../../src/utils/columnSource';
import { featureLayerColumns, featureLayerData } from '../../src/utils/featureLayers';
import { KNOWN_OPERATION_NAMES } from '../../src/utils/operationNames';
import { userTitle } from '../../src/components/canvas/nodeDescriptors';
import { estraiGroupColumns } from '../../src/components/nodes/result/ResultNodeStandardView';
import { isGeneratedAlias, uniqueAlias } from '../../src/components/nodes/result/standard/GroupOrderHavingPanel';
import OptionSelect, { missingValue } from '../../src/components/common/OptionSelect';
import { readValueMapping } from '../../src/utils/lookupMapping';
import { hasKey, toSavedRows } from '../../src/utils/lookupFill';
import useCanvasStore from '../../src/store/useCanvasStore';
import { createParameterBindingEdge } from '../../src/utils/runtimeBindingEdges';

const SRC = path.resolve(import.meta.dirname, '../../src');
const read = (rel) => fs.readFileSync(path.join(SRC, rel), 'utf-8');
const REGISTRY = JSON.parse(fs.readFileSync(
  path.resolve(import.meta.dirname, '../../../backend/app/core/manipola/transforms_registry.json'), 'utf-8'));

describe('N6: archi di join, round-trip di ogni campo', () => {
  const SAMPLES = {
    join_type: 'inner', source_column: 'a', target_column: 'b', join_conditions: [{ left: 'x', right: 'y' }],
    on_filters: [{ column: 'z', operator: '=', value: '1' }], is_spatial: true, spatial_predicate: 'ST_Within',
    source_geom_column: 'g1', target_geom_column: 'g2', spatial_distance: 0,
    suggestion: { source_column: 'id', target_column: 'fk', label: 'relazione r (1:N)' },
  };
  it('ogni campo dichiarato ha un valore di prova', () => {
    expect(Object.keys(SAMPLES).sort()).toEqual(Object.keys(JOIN_EDGE_FIELDS).sort());
  });
  it.each(Object.keys(JOIN_EDGE_FIELDS))('%s', (key) => {
    const edge = { id: 'e', source: 's', target: 't', data: { join_type: 'left', [key]: SAMPLES[key] } };
    const saved = JSON.parse(JSON.stringify(serializeEdge(edge)));
    const loaded = createEdgeFromData(saved, [{ id: 't', type: 'tableNode', data: {} }]);
    expect(loaded.data[key]).toEqual(SAMPLES[key]);
  });
  it('un arco senza suggerimento non guadagna la chiave', () => {
    expect('suggestion' in serializeEdge({ id: 'e', source: 's', target: 't', data: { join_type: 'left' } })).toBe(false);
  });
});

describe('R3.10: multi-join con 3 o piu\' sorgenti', () => {
  const mj = (sources) => {
    const node = { id: 'm', type: 'multi_join', position: { x: 0, y: 0 },
      data: { config: { join_keys: ['cod'], join_pairs: [{ left: 'id', right: 'id' }, { left: 'a', right: 'b' }], join_type: 'inner' } } };
    const edges = Array.from({ length: sources }, (_, i) => ({ id: `e${i}`, source: `s${i}`, target: 'm', targetHandle: `input-${i}` }));
    return serializeCanvas([node], edges).nodes[0];
  };
  it('2 sorgenti: coppie salvate come prima', () => {
    const saved = mj(2);
    expect(saved.join_pairs).toEqual([{ left: 'id', right: 'id' }, { left: 'a', right: 'b' }]);
    expect(saved.join_keys).toEqual(['cod', 'id']);
  });
  it.each([3, 4])('%i sorgenti: niente coppie fantasma, join_keys = chiavi comuni scelte', (n) => {
    const saved = mj(n);
    expect(saved.join_pairs).toEqual([]);
    expect(saved.left_keys).toEqual([]);
    expect(saved.right_keys).toEqual([]);
    expect(saved.join_keys).toEqual(['cod']);
  });
});

describe('R2.6: tipo di rendering dei nodi nuovi', () => {
  it('renderNodeType = reactFlowType del registro per ogni tipo e alias', () => {
    for (const entry of Object.values(NODE_TYPES)) {
      for (const t of [entry.type, ...entry.aliases]) expect(renderNodeType(t)).toBe(entry.reactFlowType);
    }
  });
  it('gli editor non creano nodi con un tipo diverso da quello del deserializer', () => {
    const differing = Object.values(NODE_TYPES).filter(e => e.type !== e.reactFlowType && e.type !== 'result').map(e => e.type);
    for (const editor of ['pages/EstraiEditor.jsx', 'pages/ManipolaEditor.jsx', 'pages/ArricchisciEditor.jsx']) {
      const text = read(editor);
      // Tipo del NODO (`id: ..., type: '...'`), non `data.type`.
      for (const t of differing) expect(text).not.toMatch(new RegExp(`id:\\s*[^,]+,\\s*type:\\s*'${t}'`));
    }
    expect(read('pages/ManipolaEditor.jsx')).not.toMatch(/datasource_node:\s*DataSourceNode/);
  });
});

describe('R2.10/N11: copie della connessione API', () => {
  const raw = { id: 'a', type: 'api_enrich', position: { x: 0, y: 0 }, data: {
    api_id: 'api1', endpoint: { url: 'https://x' }, auth: { type: 'bearer', token: '***' }, openapi_spec: { paths: {} },
    parameters: [], param_mapping: { q: 'col' } } };
  it.each([...RESOURCE_COPY_KEYS.api_enrich])('%s non si salva e non si carica', (key) => {
    expect(serializeCanvas([raw], []).nodes[0][key]).toBeUndefined();
    expect(deserializeNodes([raw])[0].data[key]).toBeUndefined();
  });
  it('il riferimento e la configurazione dell\'utente restano', () => {
    const saved = serializeCanvas([raw], []).nodes[0];
    expect(saved.api_id).toBe('api1');
    expect(saved.param_mapping).toEqual({ q: 'col' });
  });
  it('il pannello legge i dettagli dalla risorsa (sempre, non solo se mancano nel nodo)', () => {
    const text = read('components/panels/ApiEnrichNode.jsx');
    expect(text).toContain("enabled: Boolean(data.api_id)");
    expect(text).not.toMatch(/data\.parameters \|\| fetchedApi/);
    expect(read('pages/ArricchisciEditor.jsx')).not.toMatch(/openapi_spec: connection\.openapi_spec|auth: connection\.auth|endpoint: connection\.endpoint/);
  });
});

describe('R3.20 e N7: tipi', () => {
  it.each([['int', 'int'], ['real', 'real'], ['str', 'str'], ['integer', 'int'], ['geometry:point', 'geometry:point'], ['boh', 'str']])('%s -> %s', (t, out) => {
    expect(mapApiTypeToStandard(t)).toBe(out);
  });
  it.each(Object.entries(STATIC_FIELD_TYPES))('Valori statici %s -> %s prima della risposta del server', (fieldType, type) => {
    const snapshot = { schemas: {}, states: {}, status: 'loading', error: null, mode: 'manipola',
      nodes: [{ id: 's', type: 'staticValues', data: { fields: [{ id: 'f', name: 'x', value: '1', field_type: fieldType }] } }], edges: [] };
    expect(columnsOf(snapshot, 's').columns[0].type).toBe(type);
  });
});

describe('R3.15: layer ArcGIS, una sola costruzione', () => {
  const schema = { name: 'Comuni', geometry_type_urby: 'geometry:polygon', feature_count: 3, relationships: [{ id: 1 }],
    fields: [{ name: 'POP', alias: 'Popolazione', field_type: 'esriFieldTypeInteger', urby_type: 'int' }] };
  it('tipo dal backend, alias ArcGIS non usato come rinomina, relazioni presenti', () => {
    expect(featureLayerColumns(schema.fields)).toEqual([{ name: 'POP', type: 'int', alias: null, visible: true }]);
    const data = featureLayerData(schema, { layerUrl: 'u', connectionId: 'c' });
    expect(data.relationships).toEqual([{ id: 1 }]);
    expect(data.geometry_type).toBe('geometry:polygon');
  });
  it('il nome della colonna prevista e\' quello del campo anche per i layer salvati con l\'alias', () => {
    const snapshot = { schemas: {}, states: {}, status: 'loading', error: null, mode: 'estrai', edges: [],
      nodes: [{ id: 'l', type: 'featureLayerNode', data: { columns: [{ name: 'POP', alias: 'Popolazione', type: 'int' }] } }] };
    expect(columnsOf(snapshot, 'l').columns[0].name).toBe('POP');
  });
  it('trascinamento e layer correlato passano dalla stessa funzione', () => {
    const text = read('pages/EstraiEditor.jsx');
    expect((text.match(/featureLayerData\(schema,/g) || []).length).toBe(2);
    expect(text).not.toMatch(/columns: \[\] \}/);
  });
});

describe('R9-U N7: titoli generati senza il catalogo', () => {
  it('la tabella statica e\' il registry del backend', () => {
    const expected = Object.fromEntries(Object.entries(REGISTRY.transforms)
      .map(([op, d]) => [op, [d.name, d.catalog_name].filter(Boolean)]));
    expect(KNOWN_OPERATION_NAMES).toEqual(expected);
  });
  it('un titolo legacy uguale al nome di un\'operazione e\' generato (registro non caricato)', () => {
    expect(userTitle('transform', { transform_op: 'lookup', title: REGISTRY.transforms.conditional.name })).toBe('');
    expect(userTitle('transform', { transform_op: 'lookup', title: 'Il mio passo' })).toBe('Il mio passo');
  });
});

describe('R3.16: colonne di GROUP BY/ORDER BY in Estrai', () => {
  it('nome in piu\' tabelle -> una voce qualificata per tabella; nome unico semplice', () => {
    const cols = [
      { name: 'id', source_name: 'id', source_table: 'comuni' },
      { name: 'id', source_name: 'id', source_table: 'province' },
      { name: 'pop', source_name: 'pop', source_table: 'comuni' },
    ];
    expect(estraiGroupColumns(cols).map(c => c.name)).toEqual(['comuni.id', 'province.id', 'pop']);
  });
});

describe('R3.17: alias delle aggregazioni', () => {
  it('nessuna collisione dopo una rimozione', () => {
    const aggs = [{ function: 'COUNT', column: '*', alias: 'count_all' }];
    expect(uniqueAlias('count_all', aggs)).toBe('count_all_2');
    expect(uniqueAlias('count_all', [...aggs, { alias: 'count_all_2' }])).toBe('count_all_3');
  });
  it.each([
    [{ function: 'SUM', column: 'area', alias: 'sum_area' }, true],
    [{ function: 'SUM', column: 'area', alias: 'sum_area_2' }, true],
    [{ function: 'COUNT', column: '*', alias: 'count_3' }, true],
    [{ function: 'SUM', column: 'area', alias: 'totale' }, false],
    [{ function: 'SUM', column: 'area', alias: 'sum_area', alias_source: 'user' }, false],
  ])('%j generato=%s', (agg, generated) => {
    expect(isGeneratedAlias(agg)).toBe(generated);
  });
  it('spegnere il GROUP BY toglie l\'HAVING', () => {
    expect(read('components/nodes/result/standard/GroupOrderHavingPanel.jsx')).toMatch(/having_enabled: false, having_conditions: \[\]/);
  });
});

describe('R3.18/R3.21: select su elenchi che cambiano', () => {
  afterEach(cleanup);
  it('nessun `value={x || lista[0]}` nei componenti', () => {
    const walk = (dir) => fs.readdirSync(dir, { withFileTypes: true }).flatMap(e => {
      const p = path.join(dir, e.name);
      return e.isDirectory() ? walk(p) : (/\.jsx$/.test(e.name) ? [p] : []);
    });
    const strip = (t) => t.replace(/\/\*[\s\S]*?\*\//g, '');
    const offenders = walk(path.join(SRC, 'components')).filter(p => /value=\{[^}]*\|\|[^}]*\[0\]/.test(strip(fs.readFileSync(p, 'utf-8'))));
    expect(offenders).toEqual([]);
  });
  it('valore vuoto: segnaposto; valore sparito: visibile e selezionato', () => {
    expect(missingValue('', ['a'])).toBeNull();
    expect(missingValue('b', ['a'])).toBe('b');
    const { container, rerender } = render(<OptionSelect value="" options={['geom']} placeholder="Scegli…" onChange={() => {}} />);
    expect(container.querySelector('select').selectedOptions[0].textContent).toBe('Scegli…');
    rerender(<OptionSelect value="vecchia" options={['geom']} onChange={() => {}} />);
    expect(container.querySelector('select').selectedOptions[0].textContent).toContain('vecchia');
    expect(screen.getByText(/non più disponibile/)).toBeTruthy();
  });
});

describe('R3.19: il progetto aperto non e\' una sorgente', () => {
  it('useEditorData lo esclude', () => {
    expect(read('hooks/useEditorData.js')).toMatch(/String\(p\.id\) !== String\(projectId\)/);
  });
});

describe('R9-U N1 / BA3: chiavi legacy della Mappa valori', () => {
  const LEGACY = [{ from: true, to: 'Si' }, { from: 5, to: 'cinque' }, { from: null, to: 'vuoto' }, { from: 'x', to: 7 }];
  it('salvare senza toccare niente restituisce la mappa con i tipi originali', () => {
    expect(toSavedRows(readValueMapping(LEGACY))).toEqual(LEGACY);
  });
  it('modificare una riga non riscrive le altre come testo', () => {
    const rows = readValueMapping(LEGACY);
    rows[3] = { ...rows[3], from: 'y' };
    expect(toSavedRows(rows)).toEqual([LEGACY[0], LEGACY[1], LEGACY[2], { from: 'y', to: 7 }]);
  });
  it('il testo mostrato e\' quello del confronto del backend (True, 5); la chiave null non blocca', () => {
    const rows = readValueMapping(LEGACY);
    expect(rows.map(r => r.from)).toEqual(['True', '5', '', 'x']);
    expect(rows.every(hasKey)).toBe(true);
  });
  it('se l\'utente riscrive il testo della chiave vale il testo scritto', () => {
    const rows = readValueMapping(LEGACY);
    rows[0] = { ...rows[0], from: 'true' };
    expect(toSavedRows(rows)[0]).toEqual({ from: 'true', to: 'Si' });
  });
});

describe('R9-U N2: cambio di operazione', () => {
  afterEach(() => useCanvasStore.getState().reset());
  it('operazione, config e collegamenti agli input in UN Ctrl+Z', () => {
    useCanvasStore.getState().setReadOnly(false);
    const binding = createParameterBindingEdge({ source: 'in', target: 't', targetField: 'config.value', targetParameter: 'value' });
    useCanvasStore.getState().loadGraph([
      { id: 'in', type: 'project_input', position: { x: 0, y: 0 }, data: { input_kind: 'text', input_key: 'k' } },
      { id: 't', type: 'transform', position: { x: 0, y: 0 }, data: { transform_op: 'filter', config: { value: 1 } } },
    ], [binding]);
    useCanvasStore.getState().editGraph(({ nodes, edges }) => ({
      nodes: nodes.map(n => (n.id === 't' ? { ...n, data: { ...n.data, transform_op: 'sort', config: {} } } : n)),
      edges: edges.filter(e => e.target !== 't'),
    }));
    expect(useCanvasStore.getState().edges).toEqual([]);
    useCanvasStore.getState().undo();
    expect(useCanvasStore.getState().edges.map(e => e.id)).toEqual([binding.id]);
    expect(useCanvasStore.getState().nodes[1].data.transform_op).toBe('filter');
  });
  it('TransformNode chiede conferma e usa editGraph', () => {
    const text = read('components/transforms/TransformNode.jsx');
    expect(text).toContain('await confirm(');
    expect(text).toContain('editGraph(');
    expect(text).not.toMatch(/updateNodeData\(id, \{ transform_op: evt\.target\.value, config: \{\} \}\)/);
  });
});
