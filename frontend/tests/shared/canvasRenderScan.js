/**
 * Scansione statica dei selettori sul grafo del canvas (UI-2, 06/11/2026):
 * ogni chiamata `useCanvasStore(...)` dei file di src (store esclusi) e
 * ogni `useStore(...)` di React Flow, con il testo del selettore (anche
 * quando e' una costante del file). Usata da canvasRender.contract.
 */
import fs from 'node:fs';
import path from 'node:path';

export const SRC = path.resolve(import.meta.dirname, '../../src');

const walk = (dir) => fs.readdirSync(dir, { withFileTypes: true }).flatMap(e => (e.isDirectory()
  ? walk(path.join(dir, e.name))
  : [path.join(dir, e.name)]));

/** Il sorgente senza commenti (spazi al loro posto: le righe restano quelle). */
export function stripComments(text) {
  let out = '';
  let i = 0;
  let quote = null;
  while (i < text.length) {
    const ch = text[i];
    const next = text[i + 1];
    if (quote) {
      out += ch;
      if (ch === '\\') { out += next ?? ''; i += 2; continue; }
      if (ch === quote) quote = null;
      i += 1;
      continue;
    }
    if (ch === '/' && next === '/') {
      while (i < text.length && text[i] !== '\n') { out += ' '; i += 1; }
      continue;
    }
    if (ch === '/' && next === '*') {
      while (i < text.length && !(text[i] === '*' && text[i + 1] === '/')) { out += text[i] === '\n' ? '\n' : ' '; i += 1; }
      out += '  ';
      i += 2;
      continue;
    }
    if (ch === '\'' || ch === '"' || ch === '`') quote = ch;
    out += ch;
    i += 1;
  }
  return out;
}

/** Testo fra la parentesi aperta in `start` e la sua chiusa. */
function balanced(text, start) {
  let depth = 0;
  for (let i = start; i < text.length; i += 1) {
    const ch = text[i];
    if (ch === '(') depth += 1;
    else if (ch === ')') {
      depth -= 1;
      if (depth === 0) return text.slice(start + 1, i);
    }
  }
  return text.slice(start + 1);
}

/** Definizione di una costante del file (`const name = ...;`), se c'e'. */
function definitionOf(text, name) {
  const m = new RegExp(`(?:const|function)\\s+${name}\\b`).exec(text);
  if (!m) return null;
  const rest = text.slice(m.index);
  const end = rest.search(/\n(?:\}|\);|};)?\s*\n/);
  return end > 0 ? rest.slice(0, end + 3) : rest.slice(0, 400);
}

export function scanSelectors() {
  const out = [];
  for (const file of walk(SRC)) {
    if (!/\.(jsx?|tsx?)$/.test(file)) continue;
    const rel = path.relative(SRC, file).split(path.sep).join('/');
    if (rel.startsWith('store/')) continue;
    const text = stripComments(fs.readFileSync(file, 'utf-8'));
    const usesFlowStore = /import\s*\{[^}]*\buseStore\b[^}]*\}\s*from\s*'@xyflow\/react'/.test(text);
    const re = usesFlowStore ? /\b(useCanvasStore|useStore)\(/g : /\b(useCanvasStore)\(/g;
    let m;
    while ((m = re.exec(text))) {
      const arg = balanced(text, m.index + m[0].length - 1).trim();
      if (!arg) continue;
      const line = text.slice(0, m.index).split('\n').length;
      // Selettore per nome (costante o fabbrica del file): si legge la definizione.
      const named = arg.match(/^(?:useCallback\()?([A-Za-z_$][\w$]*)(?:\(|$)/);
      const definition = named && !/=>/.test(arg.split('(')[0]) ? definitionOf(text, named[1]) : null;
      out.push({ file: rel, line, store: m[1], arg, text: definition ? `${arg} :: ${definition}` : arg });
    }
  }
  return out;
}
