import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';

/**
 * Contract: aprire un canvas non lo rende "modificato".
 *
 * Collaudo live della Fase 3 (03/11/2026): Manipola chiedeva ancora
 * "modifiche non salvate" all'apertura perche' alcuni nodi, al montaggio,
 * scrivevano lo schema caricato con una mutazione UTENTE (ProjectSourceNode,
 * FileSourceNode, DataSourceNode, auto-mapping ApiEnrich, init di
 * ColumnOrderInput, StaticValuesNode).
 *
 * Regola: dentro un useEffect, o in una funzione di caricamento
 * (nomi che iniziano con load, fetch, refresh, init, hydrate, sync), le scritture sul canvas
 * devono essere DERIVATE: `{ derived: true }` (salvate via pendingSync)
 * o applyDerivedNodes/Edges. Eccezioni solo in ALLOWED, con motivazione.
 */

const srcRoot = path.resolve(import.meta.dirname, '../../src');

// file: motivo per cui la scrittura nell'effect e' legittima
const ALLOWED = {
  'components/nodes/StaticValuesNode.jsx':
    "l'effect scrive solo se i campi locali differiscono da quelli salvati (mai al montaggio)",
};

const USER_WRITE = /\b(updateNodeData|patchNodeConfig|updateConfig|editEdges|addEdges|addNodes|removeEdges|editNodes|updateEdge)\s*\(/g;
const LOAD_FN = /(?:const|function)\s+((?:load|fetch|refresh|init|hydrate|sync)\w*)\s*(?:=\s*(?:useCallback\()?\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*=>|\()/g;

function closing(src, openIdx, open, close) {
  let depth = 0;
  for (let i = openIdx; i < src.length; i += 1) {
    if (src[i] === open) depth += 1;
    else if (src[i] === close) { depth -= 1; if (depth === 0) return i; }
  }
  return src.length;
}

function userWritesIn(body) {
  const found = [];
  for (const m of body.matchAll(USER_WRITE)) {
    const open = m.index + m[0].length - 1;
    const call = body.slice(m.index, closing(body, open, '(', ')') + 1);
    if (!/derived\s*:\s*true/.test(call)) found.push(call.slice(0, 90).replace(/\s+/g, ' '));
  }
  return found;
}

function collect(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) return ['store', 'mocks'].includes(e.name) ? [] : collect(full);
    return /\.(jsx?)$/.test(e.name) ? [full] : [];
  });
}

describe('mount-time writes contract', () => {
  it('effects and load functions only write derived data', () => {
    const violations = [];
    for (const file of collect(srcRoot)) {
      const rel = path.relative(srcRoot, file).split(path.sep).join('/');
      if (ALLOWED[rel]) continue;
      const src = fs.readFileSync(file, 'utf-8');
      for (const m of src.matchAll(/useEffect\(/g)) {
        const body = src.slice(m.index, closing(src, m.index + m[0].length - 1, '(', ')') + 1);
        for (const w of userWritesIn(body)) violations.push(`${rel} useEffect: ${w}`);
      }
      for (const m of src.matchAll(LOAD_FN)) {
        const brace = src.indexOf('{', m.index + m[0].length);
        if (brace < 0) continue;
        const body = src.slice(brace, closing(src, brace, '{', '}'));
        for (const w of userWritesIn(body)) violations.push(`${rel} ${m[1]}: ${w}`);
      }
    }
    expect(violations).toEqual([]);
  });

  it('every allowed exception still exists (no stale allowlist)', () => {
    for (const rel of Object.keys(ALLOWED)) expect(fs.existsSync(path.join(srcRoot, rel)), rel).toBe(true);
  });
});
