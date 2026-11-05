import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect, vi } from 'vitest';

vi.mock('../../src/utils/transformRegistry', () => ({
  fetchTransformRegistry: vi.fn(async () => null),
  getCachedTransformRegistry: () => ({ transforms: { filter: { name: 'Filtra righe', icon: '🔍' } } }),
  isMultiInputOp: (op) => op === 'join',
}));

import { nodeDescriptor, nodeTitle, transformSummary } from '../../src/components/canvas/nodeDescriptors';
import { mergeHandles } from '../../src/components/canvas/CompactNode';

/**
 * Contract della nuova interfaccia dei canvas (Fase 4, M1).
 *  - ogni tipo registrato da un editor ha un descrittore dedicato;
 *  - i riassunti non falliscono su dati vuoti o parziali;
 *  - gli handle del nodo compatto includono quelli usati dagli edge
 *    salvati: nessun collegamento esistente resta orfano.
 */

const pages = path.resolve(import.meta.dirname, '../../src/pages');
const editorTypes = ['EstraiEditor.jsx', 'ManipolaEditor.jsx', 'ArricchisciEditor.jsx'].flatMap((file) => {
  const src = fs.readFileSync(path.join(pages, file), 'utf-8');
  const map = src.match(/const nodeTypes = \{([\s\S]*?)\n\};/)[1].replace(/\/\/[^\n]*/g, '');
  return [...map.matchAll(/\b(\w+):/g)].map(m => [file, m[1]]);
});

describe('compact canvas contract', () => {
  it('every editor node type (except notes) has a dedicated descriptor', () => {
    const missing = editorTypes.filter(([, t]) => t !== 'comment' && nodeDescriptor(t) === nodeDescriptor('__tipo_senza_descrittore__'));
    expect(missing).toEqual([]);
  });

  it('titles and summaries never throw on empty or partial data', () => {
    for (const [, type] of editorTypes) {
      if (type === 'comment') continue;
      const d = nodeDescriptor(type);
      for (const data of [{}, { config: {} }, { columns: [{ name: 'a' }], config: { conditions: [{}] } }]) {
        expect(() => nodeTitle(type, data)).not.toThrow();
        expect(() => d.summary(data)).not.toThrow();
        expect(() => d.kind(data)).not.toThrow();
        expect(Array.isArray(d.defaultHandles(data))).toBe(true);
      }
    }
  });

  it('a user title wins over the type title', () => {
    expect(nodeTitle('transform', { title: 'Escludi periodi', transform_op: 'filter' })).toBe('Escludi periodi');
    expect(nodeTitle('transform', { transform_op: 'filter' })).toBe('Filtra righe');
  });

  it('summaries read the redesign backend formats (B3/B4/B5)', () => {
    expect(transformSummary('filter', { conditions: [{ column: 'ctr_period', operator: 'not_in', value: ['UNIQUE', 'CUSTOM'] }] }))
      .toBe('ctr_period non è tra UNIQUE, CUSTOM');
    expect(transformSummary('formula', { items: [{ new_column: 'a' }, { new_column: 'b' }] })).toBe('+ a, b');
    expect(transformSummary('select_columns', { columns: [{ name: 'a' }, { name: 'b', keep: false }] })).toBe('1 colonna tenuta · 1 scartate');
  });

  it('keeps every handle used by saved edges (bindings, multi-join inputs)', () => {
    const defaults = [{ type: 'target', position: 'left' }, { type: 'source', position: 'right' }];
    const merged = mergeHandles(defaults, 'source|;target|;target|parameter:value', false);
    const keys = merged.map(h => `${h.type}|${h.id || ''}`);
    expect(keys).toEqual(expect.arrayContaining(['target|', 'source|', 'target|parameter:value']));
    expect(merged.find(h => h.id === 'parameter:value').position).toBe('top');
  });

  it('does not add an unnamed handle when the type only has named ones', () => {
    const merged = mergeHandles([{ type: 'target', id: 'input', position: 'left' }], 'target|', false);
    expect(merged).toHaveLength(1);
  });

  it('multi-join always offers a free input after the connected ones', () => {
    const defaults = nodeDescriptor('multi_join').defaultHandles({});
    const merged = mergeHandles(defaults, 'target|input-0;target|input-1;target|input-2', true);
    expect(merged.map(h => h.id)).toEqual(expect.arrayContaining(['input-0', 'input-1', 'input-2', 'input-3']));
  });
});
