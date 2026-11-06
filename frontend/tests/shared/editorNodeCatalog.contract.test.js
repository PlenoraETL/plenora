import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';

import {
  EDITOR_MODES, NODES, availability, buildCatalogNode, buildSourceNode, catalogKeys, catalogMenuItems,
  readSourceDrop, sourceNodeData, sourcePaletteGroups, startSourceDrag, withResolvedSourceNames,
} from '../../src/components/editor/editorNodeCatalog';
import { NODE_TYPE_TERMS } from '../../src/utils/glossary';
import { nodeTitle } from '../../src/components/canvas/nodeDescriptors';
import { serializeCanvas } from '../../src/utils/canvasSerializer';

/**
 * UI-3 (06/11/2026), P30 + U9 + U11 + U12/E3: un solo catalogo dei nodi che
 * si aggiungono negli editor. Enumerazione su OGNI voce e OGNI editor:
 * stessi strumenti, nessuna posizione fissa, voci spente col motivo,
 * sorgenti col nome.
 */
const SRC = path.resolve(import.meta.dirname, '../../src');
const EDITORS = { estrai: 'pages/EstraiEditor.jsx', manipola: 'pages/ManipolaEditor.jsx', arricchisci: 'pages/ArricchisciEditor.jsx' };
const read = (rel) => fs.readFileSync(path.join(SRC, rel), 'utf-8');
const code = (text) => text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:'"`])\/\/.*$/gm, '$1');
const OUTPUT_TYPE = { estrai: 'resultNode', manipola: 'result', arricchisci: 'result' };
const PREFIX = {
  runtime_input: 'project_input', static_values: 'static', note: 'comment', output: 'result',
  multi_join: 'multi_join', datasource: 'datasource', load_db: 'carica', load_arcgis: 'carica_arcgis',
};

describe('one catalog for the three editors', () => {
  it('every editor builds its "⋯" menu from the catalog, with no hand-made add handlers', () => {
    for (const [mode, rel] of Object.entries(EDITORS)) {
      const src = code(read(rel));
      expect(src, rel).toContain(`catalogMenuItems('${mode}'`);
      expect(src.match(/const handleAdd\w*Node\b/g) || [], rel).toEqual([]);
      // Nessun menuEntry di una voce del catalogo scritto a mano nell'editor.
      for (const key of catalogKeys(mode)) expect(src, `${rel}: ${key}`).not.toMatch(new RegExp(`menuEntry\\('${key}'`));
    }
  });

  it('no editor places a node at a fixed position (P30: always the visible view)', () => {
    for (const rel of Object.values(EDITORS)) {
      expect(code(read(rel)).match(/position:\s*\{\s*x:\s*-?\d/g) || [], rel).toEqual([]);
    }
  });

  it('the same tools are everywhere they make sense', () => {
    for (const mode of EDITOR_MODES) {
      for (const key of ['runtime_input', 'note', 'output', 'load_db', 'load_arcgis']) expect(catalogKeys(mode)).toContain(key);
    }
    // Estrai legge solo SQL/ArcGIS: niente Valori statici, Data Source, Multi-Join.
    expect(catalogKeys('estrai')).not.toContain('static_values');
    expect(catalogKeys('estrai')).not.toContain('datasource');
    expect(catalogKeys('arricchisci')).toContain('static_values');
  });

  it('every entry builds, in every editor where it exists, a node with its historic id and a known type', () => {
    const withOutput = (mode) => [{ id: 'out', type: OUTPUT_TYPE[mode], position: { x: 0, y: 0 }, data: {} }];
    for (const mode of EDITOR_MODES) {
      for (const key of catalogKeys(mode)) {
        const nodes = NODES[key].requires === 'output' ? withOutput(mode) : [];
        const built = buildCatalogNode(key, { mode, nodes, position: { x: 11, y: 22 }, stamp: 7 });
        expect(built.error, `${mode}:${key}`).toBeUndefined();
        expect(built.node.id).toBe(`${PREFIX[key]}_7`);
        expect(built.node.position).toEqual({ x: 11, y: 22 });
        expect(NODE_TYPE_TERMS[built.node.type], `${mode}:${key} -> ${built.node.type}`).toBeTruthy();
        // I caricamenti nascono collegati all'Output.
        if (NODES[key].edgeKind) expect(built.edges).toEqual([expect.objectContaining({ source: 'out', target: built.node.id })]);
        else expect(built.edges).toEqual([]);
      }
    }
  });

  it('a constraint has ONE reason, shown on the disabled item (E3) and returned to the toast', () => {
    for (const mode of EDITOR_MODES) {
      const empty = catalogMenuItems(mode, [], () => {});
      const full = catalogMenuItems(mode, [
        { id: 'o', type: OUTPUT_TYPE[mode], data: {} }, { id: 'c', type: 'carica', data: {} }, { id: 'a', type: 'carica_arcgis', data: {} },
      ], () => {});
      for (const item of [...empty, ...full].filter(i => !i.divider)) {
        if (item.disabled) expect(item.reason, `${mode}:${item.key}`).toMatch(/\S/);
        else expect(item.reason).toBeNull();
      }
      expect(empty.find(i => i.key === 'load_db').reason).toBe(availability('load_db', []).reason);
      expect(full.find(i => i.key === 'output').disabled).toBe(true);
      expect(buildCatalogNode('output', { mode, nodes: [{ id: 'o', type: OUTPUT_TYPE[mode] }], position: { x: 0, y: 0 } }).error)
        .toBe(availability('output', [{ id: 'o', type: OUTPUT_TYPE[mode] }]).reason);
    }
  });
});

describe('U9, U11: source nodes carry their name and are added with a click', () => {
  it('a new source node stores id AND name: its title is the name, never the id', () => {
    const p = buildSourceNode('project_source', { id: 'p1', name: 'Scheda complesso' }, { x: 0, y: 0 });
    const f = buildSourceNode('file_source', { id: 'f1', name: 'totale_immobili' }, { x: 0, y: 0 });
    expect(p.data).toEqual({ source_project_id: 'p1', project_name: 'Scheda complesso', columns: [] });
    expect(nodeTitle(p.type, p.data)).toBe('Scheda complesso');
    expect(nodeTitle(f.type, f.data)).toBe('totale_immobili');
    expect(() => sourceNodeData('unknown', {})).toThrow();
  });

  it('saved nodes with only the id get the name at load in a transient field: never saved, same array when nothing changes', () => {
    const nodes = [
      { id: 's', type: 'project_source', position: { x: 0, y: 0 }, data: { source_project_id: 'p1', columns: [] } },
      { id: 'f', type: 'file_source', position: { x: 0, y: 0 }, data: { file_source_id: 'f1', columns: [] } },
    ];
    const catalog = { projects: [{ id: 'p1', name: 'Progetto uno' }], files: [{ id: 'f1', name: 'File uno' }] };
    const resolved = withResolvedSourceNames(nodes, catalog);
    expect(resolved.map(n => nodeTitle(n.type, n.data))).toEqual(['Progetto uno', 'File uno']);
    expect(withResolvedSourceNames(resolved, catalog)).toBe(resolved);
    expect(withResolvedSourceNames(nodes, { projects: [], files: [] })).toBe(nodes);
    expect(JSON.stringify(serializeCanvas(resolved, []))).not.toContain('_sourceName');
  });

  it('drag and drop use one format for every editor', () => {
    const store = {};
    const event = { dataTransfer: { setData: (k, v) => { store[k] = v; }, getData: (k) => store[k] || '' } };
    startSourceDrag(event, 'file_source', { id: 'f1', name: 'File uno', format: 'csv' });
    expect(readSourceDrop(event)).toEqual({ kind: 'file_source', item: { id: 'f1', name: 'File uno' } });
    expect(readSourceDrop({ dataTransfer: { getData: () => '' } })).toBeNull();
    expect(readSourceDrop({ dataTransfer: { getData: () => '{rotto' } })).toBeNull();
  });

  it('sources are in the palette (U11) and in the sidebars with click/Enter', () => {
    const groups = sourcePaletteGroups({ projects: [{ id: 1, name: 'A', project_type: 'estrai' }], files: [{ id: 2, name: 'B', format: 'csv' }] });
    expect(groups.map(g => g.label)).toEqual(['Sorgenti: progetti', 'Sorgenti: file']);
    expect(groups.flatMap(g => g.items).map(i => [i.kind, i.sourceKind, i.label])).toEqual([
      ['source', 'project_source', 'A'], ['source', 'file_source', 'B'],
    ]);
    for (const rel of ['components/panels/ProjectsSidebar.jsx', 'components/panels/FilesSidebar.jsx']) {
      const src = code(read(rel));
      expect(src, rel).toContain('pressable(() => onPick');
      expect(src, rel).toContain('startSourceDrag(');
    }
    for (const rel of [EDITORS.manipola, EDITORS.arricchisci]) {
      const src = code(read(rel));
      expect(src, rel).toContain('sourcePaletteGroups(');
      expect(src, rel).toContain('readSourceDrop(event)');
      expect(src, rel).toContain('useResolvedSourceNames(');
    }
  });
});
