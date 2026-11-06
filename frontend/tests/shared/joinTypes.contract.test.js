import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';

import { JOIN_TYPES, MISSING_JOIN_TYPE, joinTypeName, joinTypeOptions, newJoinType, readJoinType } from '../../src/utils/joinTypes';
import { JOIN_TYPES as ESTRAI_JOIN_TYPES, newJoinEdge } from '../../src/utils/estraiJoins';
import { JOIN_TYPES as MANIPOLA_JOIN_TYPES } from '../../src/components/transforms/panels/panelConfig';
import { NODES } from '../../src/components/editor/editorNodeCatalog';

/**
 * UI-3 (06/11/2026), U21: un catalogo unico dei tipi di Join. Stesse
 * etichette in tutti gli editor, valori uguali a quelli del backend, ogni
 * Join nuovo Left (prima Inner dalla palette e Left da «Collega»), lettura
 * dei Join salvati senza tipo invariata.
 */
const REPO = path.resolve(import.meta.dirname, '../../..');
const SRC = path.join(REPO, 'frontend', 'src');
const registry = JSON.parse(fs.readFileSync(path.join(REPO, 'backend/app/core/manipola/transforms_registry.json'), 'utf-8'));
const canvasTypes = fs.readFileSync(path.join(REPO, 'backend/app/shared/execution/canvas_types.py'), 'utf-8');
const executor = fs.readFileSync(path.join(REPO, 'backend/app/core/manipola/executor.py'), 'utf-8');
const estraiJoinsPy = fs.readFileSync(path.join(REPO, 'backend/app/core/estrai/query_joins.py'), 'utf-8');
const walk = (dir) => fs.readdirSync(dir, { withFileTypes: true }).flatMap(e => {
  const p = path.join(dir, e.name);
  return e.isDirectory() ? walk(p) : (/\.jsx?$/.test(e.name) ? [p] : []);
});
const code = (t) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:'"`])\/\/.*$/gm, '$1');

describe('one catalog of join types', () => {
  it('Manipola values are the backend Join options; Multi-Join values are the backend literal', () => {
    const how = registry.transforms.join.parameters.find(p => p.id === 'how');
    expect(joinTypeOptions('manipola').map(([v]) => v).sort()).toEqual(how.options.map(o => o.value).sort());
    const multi = canvasTypes.match(/MultiJoinTypeLiteral = Literal\[([^\]]+)\]/)[1].match(/"(\w+)"/g).map(s => s.replace(/"/g, ''));
    expect(joinTypeOptions('multi_join').map(([v]) => v).sort()).toEqual(multi.sort());
  });

  it('a saved join without a type reads as the backend reads it (unchanged)', () => {
    expect(MISSING_JOIN_TYPE.manipola).toBe(how().default);
    expect(executor).toMatch(new RegExp(`data_config\\.get\\('join_type', '${MISSING_JOIN_TYPE.multi_join}'\\)`));
    expect(estraiJoinsPy).toMatch(new RegExp(`edge\\.join_type or '${MISSING_JOIN_TYPE.estrai.toUpperCase()}'`));
    for (const engine of Object.keys(MISSING_JOIN_TYPE)) expect(readJoinType(engine, undefined)).toBe(MISSING_JOIN_TYPE[engine]);
  });

  it('every engine uses the same labels', () => {
    const label = (t) => `${t.label} (${t.short})`;
    expect(MANIPOLA_JOIN_TYPES.map(([, l]) => l)).toEqual(JOIN_TYPES.filter(t => t.values.manipola).map(label));
    expect(ESTRAI_JOIN_TYPES.map(t => t.label)).toEqual(JOIN_TYPES.filter(t => t.values.estrai).map(label));
    expect(joinTypeName('manipola', 'outer')).toBe('Full Join');
    expect(joinTypeName('estrai', 'full')).toBe('Full Join');
  });

  it('every new join is Left: Estrai edge, Manipola palette and «Collega», Multi-Join', () => {
    expect(newJoinEdge({ source: 'a', target: 'b' }).data.join_type).toBe('left');
    expect(NODES.multi_join.build({ mode: 'manipola', nodes: [] }).data.config.join_type).toBe(newJoinType('multi_join'));
    expect(newJoinType('multi_join')).toBe('left');
    const manipola = code(fs.readFileSync(path.join(SRC, 'pages/ManipolaEditor.jsx'), 'utf-8'));
    expect(manipola.match(/how: newJoinType\('manipola'\)/g) || []).toHaveLength(2);
  });

  it('no hand-written join type list or literal default in the editors', () => {
    const offenders = [];
    for (const f of walk(SRC)) {
      const rel = path.relative(SRC, f).split(path.sep).join('/');
      if (rel === 'utils/joinTypes.js') continue;
      const src = code(fs.readFileSync(f, 'utf-8'));
      if (/<option value="(inner|left|outer|full)"/.test(src)) offenders.push(`${rel}: <option> scritta a mano`);
      if (/\[\s*'(left|inner)'\s*,\s*'[^']*(Left|Inner)/.test(src)) offenders.push(`${rel}: elenco scritto a mano`);
      if (/join_type:\s*'(inner|left)'|how:\s*'(inner|left)'|\.how \|\| '|join_type \|\| '/.test(src)) offenders.push(`${rel}: tipo predefinito scritto a mano`);
    }
    expect(offenders).toEqual([]);
  });
});

function how() {
  return registry.transforms.join.parameters.find(p => p.id === 'how');
}
