import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect, vi } from 'vitest';
import { EDITOR_ACTIONS } from '../../src/components/editor/editorActions';
import useCanvasStore from '../../src/store/useCanvasStore';

/**
 * Contract Blocco A, regola 9: le azioni dell'editor sono dichiarate.
 * La tabella (chi registra, chi usa) coincide con il codice: niente azioni
 * registrate senza lettore (codice morto), niente letture senza
 * registrazione (pulsanti che non fanno niente).
 */
const SRC = path.resolve(__dirname, '../../src');
const files = (dir) => fs.readdirSync(dir, { withFileTypes: true }).flatMap(d => {
  const p = path.join(dir, d.name);
  return d.isDirectory() ? files(p) : (/\.jsx?$/.test(d.name) ? [p] : []);
});
const stripComments = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:'"`])\/\/.*$/gm, '$1');
const moduleName = (p) => path.basename(p).replace(/\.jsx?$/, '');

// Chiavi di primo livello di ogni oggetto passato a setEditorActions({...}).
function registeredKeys(src) {
  const out = [];
  for (const m of src.matchAll(/(?:setEditorActions|useEditorActions)\(\{/g)) {
    let depth = 1; let j = m.index + m[0].length; const start = j;
    while (depth && j < src.length) { if (src[j] === '{') depth += 1; else if (src[j] === '}') depth -= 1; j += 1; }
    const body = src.slice(start, j - 1);
    let level = 0; let buf = '';
    const flush = () => { const k = buf.trim().match(/^([A-Za-z_]\w*)\s*(:|$)/); if (k) out.push(k[1]); buf = ''; };
    for (const ch of body) {
      if ('({['.includes(ch)) level += 1;
      if (')}]'.includes(ch)) level -= 1;
      if (level === 0 && ch === ',') flush(); else buf += ch;
    }
    flush();
  }
  return out;
}

function usedKeys(src) {
  return [
    ...[...src.matchAll(/getEditorAction\??\.?\(\s*['"](\w+)['"]/g)].map(m => m[1]),
    ...[...src.matchAll(/editorActionsRef\??\.current\??\.(\w+)/g)].map(m => m[1]),
    ...[...src.matchAll(/\baction\(\s*['"](\w+)['"]/g)].map(m => m[1]),
  ];
}

const scan = () => {
  const reg = {}; const use = {};
  for (const f of files(SRC)) {
    if (f.endsWith(`${path.sep}editorActions.js`)) continue;
    const src = stripComments(fs.readFileSync(f, 'utf-8'));
    for (const k of registeredKeys(src)) (reg[k] ||= new Set()).add(moduleName(f));
    for (const k of usedKeys(src)) (use[k] ||= new Set()).add(moduleName(f));
  }
  return { reg, use };
};

describe('editor actions are declared', () => {
  const { reg, use } = scan();

  it('every registered or used action is in the table', () => {
    const names = new Set([...Object.keys(reg), ...Object.keys(use)]);
    expect([...names].filter(n => !EDITOR_ACTIONS[n]).sort()).toEqual([]);
  });

  it('the table matches the code: who registers, who uses (no dead or dangling action)', () => {
    for (const [name, decl] of Object.entries(EDITOR_ACTIONS)) {
      expect({ name, registeredBy: [...(reg[name] || [])].sort() }).toEqual({ name, registeredBy: [...decl.registeredBy].sort() });
      expect({ name, usedBy: [...(use[name] || [])].sort() }).toEqual({ name, usedBy: [...decl.usedBy].sort() });
    }
  });

  it('the store refuses undeclared names', () => {
    const err = vi.spyOn(console, 'error').mockImplementation(() => {});
    useCanvasStore.getState().setEditorActions({ nonDichiarata: () => 1 });
    expect(useCanvasStore.getState().getEditorAction('nonDichiarata')).toBeUndefined();
    expect(err).toHaveBeenCalled();
    err.mockRestore();
  });

  it('the editors register through useEditorActions (once, stable), never directly every render', () => {
    const pages = files(path.join(SRC, 'pages')).filter(f => /Editor\.jsx$/.test(f));
    for (const f of pages) {
      const src = stripComments(fs.readFileSync(f, 'utf-8'));
      expect({ page: moduleName(f), direct: /setEditorActions\(/.test(src) }).toEqual({ page: moduleName(f), direct: false });
    }
  });

  it('no functions are stored inside node data (they end up in history and drafts)', () => {
    const offenders = files(path.join(SRC, 'pages')).filter(f => /data:\s*\{[^}]*\b(onChange|onUpdate\w*)\s*:/.test(stripComments(fs.readFileSync(f, 'utf-8'))));
    expect(offenders.map(moduleName)).toEqual([]);
  });
});
