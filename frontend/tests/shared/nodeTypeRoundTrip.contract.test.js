import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { NODE_TYPES, canonicalNodeType, persistedNodeData } from '../../src/utils/nodeTypeRegistry';
import { serializeCanvas, serializeEdge } from '../../src/utils/canvasSerializer';
import { deserializeNodes, createEdgeFromData } from '../../src/utils/nodeFactories';

const node = (type, data = {}) => ({ id: 'contract', type, position: { x: 17, y: 29 }, data });
const save = n => serializeCanvas([n], []).nodes[0];
const reload = n => deserializeNodes([JSON.parse(JSON.stringify(save(n)))])[0];
function sample(key, fallback) {
  if (['feature_count', 'limit', 'source_srid'].includes(key)) return 4326;
  if (['datasource_config', 'request', 'response', 'extra_files', 'arrowStyle'].includes(key)) return { sentinel: 'value' };
  if (key === 'columns' || key === 'expected_columns') return [{ name: 'sentinel', type: 'int', alias: '', visible: false }];
  if (key === 'bindings') return [{ target_node_id: 'target', target_field: 'config.value' }];
  if (key === 'join_pairs') return [{ left: 'left_id', right: 'right_id' }];
  if (key === 'parameters') return [{ name: 'sentinel', required: false }];
  if (Array.isArray(fallback)) return ['sentinel'];
  if (fallback && typeof fallback === 'object') return { sentinel: 17, enabled: false };
  if (typeof fallback === 'boolean') return !fallback;
  if (typeof fallback === 'number') return 37;
  return 'sentinel';
}

describe('declarative node persistence contract', () => {
  for (const entry of Object.values(NODE_TYPES)) {
    describe(entry.type, () => {
      for (const [key, field] of Object.entries(entry.fields)) {
        if (!field.persisted) continue;
        it(`round-trips ${key} through the JSON wire format`, () => {
          const value = sample(key, field.default);
          // File bindings are stored on the node; runtime bindings derive from edges.
          const data = { ...(entry.type === 'project_input' ? { input_kind: 'file' } : {}), [key]: value };
          expect(reload(node(entry.type, data)).data[key]).toEqual(value);
        });
        const falsy = ['feature_count', 'limit', 'source_srid'].includes(key) ? [0]
          : key === 'default_value' ? [0, false, '']
          : typeof field.default === 'boolean' ? [false]
          : typeof field.default === 'number' ? [0]
          : typeof field.default === 'string' ? [''] : [];
        for (const value of falsy) {
          it(`preserves ${key} = ${JSON.stringify(value)}`, () => {
            expect(reload(node(entry.type, { [key]: value })).data[key]).toEqual(value);
          });
        }
      }
      for (const alias of [entry.type, ...entry.aliases]) {
        it(`accepts ${alias} in both directions with identical defaults`, () => {
          expect(canonicalNodeType(alias)).toBe(entry.type);
          expect(save(node(alias))).toEqual(save(node(entry.type)));
          expect(deserializeNodes([node(alias)])[0].type).toBe(entry.reactFlowType);
        });
      }
      it('preserves backend access annotations from either location', () => {
        const flags = { access_denied: false, access_denied_reason: 'denied', access_denied_future: 0, inaccessible_resource_type: 'connection' };
        for (const raw of [{ ...node(entry.type), ...flags }, node(entry.type, flags)]) {
          expect(reload(raw).data).toMatchObject(flags);
          expect(deserializeNodes([raw])[0].data).toMatchObject(flags);
        }
        expect(reload(node(entry.type, { access_denied: true })).data.access_denied).toBe(true);
      });
      it('excludes transient fields and callbacks', () => {
        const payload = save(node(entry.type, { availableColumns: ['secret'], inputColumns: [], _advOpen: true, _lastUpdate: 1, onChange: () => {} }));
        for (const key of ['availableColumns', 'inputColumns', '_advOpen', '_lastUpdate', 'onChange']) expect(payload).not.toHaveProperty(key);
      });

      // Lista di ESCLUSIONE, non allowlist: un campo scritto da un componente
      // e non ancora registrato non deve sparire al salvataggio.
      it('keeps non-transient fields that are not in the registry', () => {
        const payload = save(node(entry.type, { unregistered_field: 'keep-me' }));
        expect(payload.unregistered_field).toBe('keep-me');
      });
    });
  }

  it('covers all literal node types emitted by factories and serializer', () => {
    for (const file of ['nodeFactories.js', 'canvasSerializer.js']) {
      const source = readFileSync(`src/utils/${file}`, 'utf8');
      for (const [, type] of source.matchAll(/\btype:\s*['"]([^'"]+)['"]/g)) {
        expect(NODE_TYPES[canonicalNodeType(type)], `${file}: ${type}`).toBeDefined();
      }
    }
    // Independent inventory from the editor nodeTypes maps (not column/edge types).
    for (const file of ['EstraiEditor.jsx', 'ManipolaEditor.jsx', 'ArricchisciEditor.jsx']) {
      const source = readFileSync(`src/pages/${file}`, 'utf8');
      const map = source.match(/const nodeTypes = \{([\s\S]*?)\n\};/)[1].replace(/\/\/[^\n]*/g, '');
      for (const [, type] of map.matchAll(/\b(\w+):/g)) expect(NODE_TYPES[canonicalNodeType(type)], type).toBeDefined();
    }
  });

  it('never persists transient data through unknown types or nested config', () => {
    expect(save(node('future', { availableColumns: [], _advOpen: true, callback: () => {} }))).toEqual({ id: 'contract', type: 'future', position: { x: 17, y: 29 } });
    expect(save(node('transform', { config: { availableColumns: [], _advOpen: true, callback: () => {}, keep: false } })).config).toEqual({ keep: false });
  });

  it('uses backend-compatible legacy input and ArcGIS defaults on first save', () => {
    expect(save(node('project_input')).input_kind).toBeNull();
    expect(save(node('carica_arcgis', { availableColumns: ['transient'] }))).toMatchObject({ dest_type: 'arcgis', arcgis_operation: 'add', arcgis_new_layer_name: 'Layer 0', column_mappings: [] });
    expect(reload(node('api_enrich')).data.parameters).toBeNull();
  });

  it('keeps zero spatial distance in both edge directions and recognizes output aliases', () => {
    const raw = { id: 'edge', source: 'a', target: 'b', join_type: 'inner', spatial_distance: 0 };
    expect(serializeEdge(createEdgeFromData(raw, [])).spatial_distance).toBe(0);
    for (const type of ['carica_arcgis', 'caricaArcgis']) expect(createEdgeFromData(raw, [{ id: 'b', type }]).label).toBe('Carica ArcGIS');
  });

  it('does not share mutable defaults between nodes', () => {
    const a = persistedNodeData(node('source'));
    a.columns.push('changed');
    expect(persistedNodeData(node('source')).columns).toEqual([]);
  });

  it('prefers nested edits and preserves all newly recovered Carica settings over two saves', () => {
    const data = {
      create_unique_index: true, unique_key_column: 'business_id',
      update_columns: ['name'], if_table_exists: 'replace', allow_unsafe_append: true,
    };
    const original = { ...node('carica', data), unique_key_column: 'old', access_denied: true };
    const loaded = reload(original);
    expect(loaded.data).toMatchObject(data);
    expect(reload(loaded).data).toMatchObject(data);
    expect(save(loaded).access_denied).toBe(true);
  });
});
