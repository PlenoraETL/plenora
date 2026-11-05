/**
 * sourceScan — lettura statica di src/ per i test di enumerazione della
 * Fase 5 T7 (formati, CSS, codice morto, accessibilità, layout).
 *
 * Solo lettura di file: nessun import dei moduli dell'app.
 */
import fs from 'node:fs';
import path from 'node:path';

export const FRONTEND = path.resolve(import.meta.dirname, '../..');
export const SRC = path.join(FRONTEND, 'src');

export function walk(dir, out = []) {
  for (const name of fs.readdirSync(dir)) {
    const p = path.join(dir, name);
    if (fs.statSync(p).isDirectory()) walk(p, out);
    else out.push(p);
  }
  return out;
}

const rel = (p) => path.relative(SRC, p).split(path.sep).join('/');

/** File di src con il loro testo: [{ file: 'pages/X.jsx', abs, code }]. */
export function sourceFiles(exts = /\.(jsx?|css)$/) {
  return walk(SRC).filter(p => exts.test(p)).map(abs => ({ file: rel(abs), abs, code: fs.readFileSync(abs, 'utf-8') }));
}

export const stripComments = (code) => code
  .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
  .replace(/(^|[^:'"`\\])\/\/[^\n]*/g, (m, p) => p + ' '.repeat(m.length - p.length));

/** Righe (1-based) di un indice nel testo. */
export const lineOf = (code, index) => code.slice(0, index).split('\n').length;

// ------------------------------------------------------------------ CSS ---

/** Classi definite nei CSS: Map(classe -> [file:riga]). */
export function cssClasses(files) {
  const out = new Map();
  for (const { file, code } of files.filter(f => f.file.endsWith('.css'))) {
    const clean = code.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '));
    // Solo i selettori (prima di `{`), non i valori delle dichiarazioni.
    for (const rule of clean.matchAll(/([^{}]+)\{/g)) {
      const sel = rule[1];
      if (/^\s*@/.test(sel) || /^\s*(from|to|\d+%)\s*$/.test(sel)) continue;
      for (const m of sel.matchAll(/\.(-?[_a-zA-Z][\w-]*)/g)) {
        const name = m[1];
        if (!out.has(name)) out.set(name, []);
        out.get(name).push(`${file}:${lineOf(clean, rule.index + m.index)}`);
      }
    }
  }
  return out;
}

/** Variabili CSS definite (CSS, style={{'--x': }} e setProperty) e usate. */
export function cssVariables(files) {
  const defined = new Set();
  const used = new Map();
  for (const { file, code } of files) {
    for (const m of code.matchAll(/(--[\w-]+)\s*:/g)) defined.add(m[1]);
    for (const m of code.matchAll(/['"](--[\w-]+)['"]\s*[:,]/g)) defined.add(m[1]);
    for (const m of code.matchAll(/var\(\s*(--[\w-]+)/g)) {
      if (!used.has(m[1])) used.set(m[1], new Set());
      used.get(m[1]).add(file);
    }
  }
  return { defined, used };
}

// ------------------------------------------------------------- moduli ---

/** Export nominati di un modulo JS. */
export function namedExports(code) {
  const names = new Set();
  const clean = stripComments(code);
  for (const m of clean.matchAll(/^export\s+(?:async\s+)?(?:function\*?|const|let|var|class)\s+([A-Za-z_$][\w$]*)/gm)) names.add(m[1]);
  for (const m of clean.matchAll(/^export\s*\{([^}]*)\}(?!\s*from)/gm)) {
    for (const part of m[1].split(',')) {
      const name = part.trim().split(/\s+as\s+/).pop().trim();
      if (name && name !== 'default') names.add(name);
    }
  }
  return names;
}
