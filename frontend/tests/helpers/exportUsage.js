/**
 * exportUsage — export nominati di src/ e chi li importa (Fase 5 T7, codice
 * morto). Un export è usato se un ALTRO file di src lo importa per nome dal
 * suo modulo, direttamente o da un modulo che lo riesporta (`export * from`,
 * `export { x } from`), oppure con `import * as ns` e `ns.x`.
 */
import fs from 'node:fs';
import path from 'node:path';
import { SRC, FRONTEND, walk, sourceFiles, stripComments, namedExports } from './sourceScan.js';

const EXT = ['', '.js', '.jsx', '/index.js', '/index.jsx'];

function resolveFrom(fromAbs, spec) {
  if (!spec.startsWith('.')) return null;
  const base = path.resolve(path.dirname(fromAbs), spec);
  for (const e of EXT) {
    const p = path.normalize(base + e);
    if (fs.existsSync(p) && fs.statSync(p).isFile()) return p;
  }
  return null;
}

export function exportUsage() {
  const files = sourceFiles(/\.(jsx?)$/).map(f => ({ ...f, clean: stripComments(f.code) }));
  const byAbs = new Map(files.map(f => [f.abs, f]));
  const exportsOf = new Map(files.map(f => [f.abs, namedExports(f.code)]));
  // Riesportazioni: abs -> [{ from, names: Map(esportato -> originale) | '*' }]
  const reexports = new Map();
  for (const f of files) {
    const list = [];
    for (const m of f.clean.matchAll(/^export\s*\*\s*from\s*['"]([^'"]+)['"]/gm)) {
      const to = resolveFrom(f.abs, m[1]);
      if (to) list.push({ from: to, names: '*' });
    }
    for (const m of f.clean.matchAll(/^export\s*\{([^}]*)\}\s*from\s*['"]([^'"]+)['"]/gm)) {
      const to = resolveFrom(f.abs, m[2]);
      if (!to) continue;
      const names = new Map();
      for (const part of m[1].split(',')) {
        const [orig, alias] = part.trim().split(/\s+as\s+/);
        if (orig) names.set((alias || orig).trim(), orig.trim());
      }
      list.push({ from: to, names });
    }
    reexports.set(f.abs, list);
  }
  // Moduli e nomi raggiunti da un nome importato da `mod`.
  const origins = (mod, name, seen = new Set()) => {
    const key = `${mod}::${name}`;
    if (seen.has(key)) return [];
    seen.add(key);
    const out = [];
    if (exportsOf.get(mod)?.has(name)) out.push(key);
    for (const r of reexports.get(mod) || []) {
      if (r.names === '*') out.push(...origins(r.from, name, seen));
      else if (r.names.has(name)) out.push(...origins(r.from, r.names.get(name), seen));
    }
    return out;
  };
  const used = new Set();
  for (const f of files) {
    for (const m of f.clean.matchAll(/import\s+(?:[\w$]+\s*,\s*)?\{([^}]*)\}\s*from\s*['"]([^'"]+)['"]/g)) {
      const mod = resolveFrom(f.abs, m[2]);
      if (!mod) continue;
      for (const part of m[1].split(',')) {
        const orig = part.trim().split(/\s+as\s+/)[0].trim();
        if (orig) origins(mod, orig).forEach(k => { if (!k.startsWith(`${f.abs}::`)) used.add(k); });
      }
    }
    for (const m of f.clean.matchAll(/import\s+\*\s+as\s+([\w$]+)\s+from\s*['"]([^'"]+)['"]/g)) {
      const mod = resolveFrom(f.abs, m[2]);
      if (!mod) continue;
      for (const u of f.clean.matchAll(new RegExp(`\\b${m[1]}\\.([\\w$]+)`, 'g'))) {
        origins(mod, u[1]).forEach(k => used.add(k));
      }
    }
    // Import dinamico con destrutturazione: `const { x } = await import('./y')`
    // e `import('./y').then(({ x }) => ...)` (UI-4: dagre al bisogno).
    const dynamic = [
      ...[...f.clean.matchAll(/\{([^{}]*)\}\s*=\s*await\s+import\(\s*['"]([^'"]+)['"]\s*\)/g)].map(m => [m[1], m[2]]),
      ...[...f.clean.matchAll(/import\(\s*['"]([^'"]+)['"]\s*\)\s*\.then\(\s*\(\s*\{([^{}]*)\}\s*\)/g)].map(m => [m[2], m[1]]),
    ];
    for (const [names, spec] of dynamic) {
      const mod = resolveFrom(f.abs, spec);
      if (!mod) continue;
      for (const part of names.split(',')) {
        const orig = part.trim().split(/\s*:\s*/)[0].trim();
        if (orig) origins(mod, orig).forEach(k => used.add(k));
      }
    }
  }
  // `unused`: nessun altro file di src lo importa. `dead`: in piu' non e'
  // usato nemmeno nel suo file (solo la dichiarazione): codice che gira solo
  // nei test o in nessun posto.
  const unused = [];
  const dead = [];
  for (const [abs, names] of exportsOf) {
    const own = byAbs.get(abs).clean;
    for (const name of names) {
      if (used.has(`${abs}::${name}`)) continue;
      const id = `${path.relative(SRC, abs).split(path.sep).join('/')}:${name}`;
      unused.push(id);
      // Riferimenti nel proprio file (spread `...x` compreso, `obj.x` no).
      const esc = name.replace(/\$/g, '\\$');
      const refs = (own.match(new RegExp(`(?<![\\w$])${esc}(?![\\w$])`, 'g')) || []).length
        - (own.match(new RegExp(`(?<!\\.)\\.${esc}(?![\\w$])`, 'g')) || []).length;
      if (refs <= 1) dead.push(id);
    }
  }
  // Export che nessuno importa, nemmeno i test: `export` superfluo.
  // Il test che dichiara le eccezioni non conta come importatore.
  const testText = walk(path.join(FRONTEND, 'tests')).filter(p => /\.(jsx?)$/.test(p) && !/t7Rules\.contract/.test(p))
    .map(p => fs.readFileSync(p, 'utf-8')).join('\n');
  const untested = unused.filter(id => {
    const name = id.split(':').pop();
    return !new RegExp(`(?<![\\w$])${name.replace(/\$/g, '\\$')}(?![\\w$])`).test(testText);
  });
  return { unused, dead, untested, byAbs };
}
