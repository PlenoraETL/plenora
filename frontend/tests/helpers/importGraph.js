/**
 * importGraph — grafo degli import STATICI di src/ (UI-4, V1/V2/V3b/V3c).
 *
 * Il chunk iniziale della build e' la chiusura degli import statici del
 * punto d'ingresso (src/index.jsx): gli `import()` dinamici (React.lazy,
 * caricamenti al bisogno) aprono un chunk separato e qui non si seguono.
 * Solo lettura dei file: nessun import dei moduli dell'app.
 */
import fs from 'node:fs';
import path from 'node:path';
import { SRC, stripComments } from './sourceScan.js';

const EXTS = ['', '.js', '.jsx', '/index.js', '/index.jsx'];

// `import x from 'y'`, `import 'y'`, `export { x } from 'y'`, `export * from 'y'`.
// Non `import('y')` (dinamico) ne' `import.meta`.
const STATIC_RE = /(?:^|[;\n])\s*(?:import\s+(?:[\w$*{}\s,]+?\s+from\s+)?|export\s+(?:\*|\{[^}]*\})\s*(?:as\s+[\w$]+\s+)?from\s+)['"]([^'"]+)['"]/g;

/** Specificatori importati staticamente da un file. */
export function staticImports(code) {
  return [...stripComments(code).matchAll(STATIC_RE)].map(m => m[1]);
}

function resolve(from, spec) {
  if (!spec.startsWith('.')) return null;
  const base = path.resolve(path.dirname(from), spec);
  for (const ext of EXTS) {
    const p = base + ext;
    if (fs.existsSync(p) && fs.statSync(p).isFile()) return p;
  }
  return null;
}

/** Nome del pacchetto di uno specificatore ('@xyflow/react/dist/x' -> '@xyflow/react'). */
export const packageOf = (spec) => (spec.startsWith('@') ? spec.split('/').slice(0, 2).join('/') : spec.split('/')[0]);

const rel = (p) => path.relative(SRC, p).split(path.sep).join('/');

/**
 * Chiusura statica da un file di src: { files: Map(file -> catena), packages: Map(pacchetto -> catena) }.
 * La catena e' il percorso di import che porta li' (per il messaggio d'errore).
 */
export function staticClosure(entry = 'index.jsx') {
  const files = new Map();
  const packages = new Map();
  const start = path.join(SRC, entry);
  const queue = [[start, [rel(start)]]];
  while (queue.length) {
    const [file, chain] = queue.shift();
    if (files.has(rel(file))) continue;
    files.set(rel(file), chain);
    if (!/\.jsx?$/.test(file)) continue;
    for (const spec of staticImports(fs.readFileSync(file, 'utf-8'))) {
      if (spec.startsWith('.')) {
        const next = resolve(file, spec);
        if (next) queue.push([next, [...chain, rel(next)]]);
      } else if (!packages.has(packageOf(spec))) {
        packages.set(packageOf(spec), [...chain, spec]);
      }
    }
  }
  return { files, packages };
}
