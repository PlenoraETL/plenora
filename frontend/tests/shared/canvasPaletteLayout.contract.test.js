import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';
import { buildManipolaPaletteGroups, PURPOSES } from '../../src/components/canvas/manipolaPalette';
import { autoLayout } from '../../src/components/canvas/autoLayout';

/**
 * Contract Fase 4 M2: palette per scopo e layout automatico.
 * La palette e' verificata sul registry VERO del backend: ogni operazione
 * deve comparire (anche quelle future, in "Altre operazioni").
 */
const registry = JSON.parse(fs.readFileSync(
  path.resolve(import.meta.dirname, '../../../backend/app/core/manipola/transforms_registry.json'), 'utf-8'));

describe('manipola palette', () => {
  it('every backend operation appears in a purpose group', () => {
    const groups = buildManipolaPaletteGroups(registry);
    const listed = new Set(groups.filter(g => !g.onlyWhenEmpty).flatMap(g => g.items)
      .filter(i => i.kind === 'transform').map(i => i.id));
    const missing = Object.keys(registry.transforms).filter(op => !listed.has(op));
    expect(missing).toEqual([]);
  });

  it('known operations land in a real purpose, not in the fallback group', () => {
    const groups = buildManipolaPaletteGroups(registry);
    const other = groups.find(g => g.label === 'Altre operazioni');
    expect(other ? other.items.map(i => i.id) : []).toEqual([]);
    expect(groups.map(g => g.label)).toEqual(expect.arrayContaining(PURPOSES));
  });

  it('an unknown future operation is not lost', () => {
    const groups = buildManipolaPaletteGroups({ transforms: { brand_new_op: { name: 'Nuova', category: 'base' } } });
    expect(groups.flatMap(g => g.items).some(i => i.id === 'brand_new_op')).toBe(true);
  });

  it('works before the registry is loaded (only joins and outputs)', () => {
    expect(buildManipolaPaletteGroups(null).map(g => g.label)).toEqual(['Unisci e uscite']);
  });
});

describe('auto layout', () => {
  const n = (id, type = 'transform', position = { x: 0, y: 0 }) => ({ id, type, position, data: {} });

  it('lays the flow out left to right and keeps notes in place', () => {
    const nodes = [n('a'), n('b'), n('c'), n('note', 'comment', { x: 999, y: 999 })];
    const edges = [{ id: 'e1', source: 'a', target: 'b' }, { id: 'e2', source: 'b', target: 'c' }];
    const out = Object.fromEntries(autoLayout(nodes, edges).map(x => [x.id, x.position]));
    expect(out.a.x).toBeLessThan(out.b.x);
    expect(out.b.x).toBeLessThan(out.c.x);
    expect(out.note).toEqual({ x: 999, y: 999 });
  });

  it('ignores runtime-input binding edges when ranking', () => {
    const nodes = [n('src'), n('step'), n('input', 'project_input')];
    const edges = [
      { id: 'e1', source: 'src', target: 'step' },
      { id: 'b1', source: 'input', target: 'step', targetHandle: 'parameter:value', data: { binding_kind: 'parameter' } },
    ];
    expect(() => autoLayout(nodes, edges)).not.toThrow();
  });
});
