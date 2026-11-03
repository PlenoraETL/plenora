import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';

/**
 * Contract: ogni link interno punta a una route esistente.
 *
 * Audit 03/11/2026 (R7.3): ArricchisciEditor linkava "/api-connections",
 * route inesistente (la pagina e' /external-connectors) -> il catch-all
 * riportava silenziosamente alla dashboard. Il test enumera tutti i link
 * statici in src/ (navigate, to=, href=, createLink=, path:) e li confronta
 * con le <Route path> di App.jsx.
 */

const srcRoot = path.resolve(import.meta.dirname, '../../src');
const appSource = fs.readFileSync(path.join(srcRoot, 'App.jsx'), 'utf-8');

const routes = [...appSource.matchAll(/<Route\s+path="([^"]+)"/g)]
  .map((m) => m[1])
  .filter((p) => p !== '*');

const routeRegexes = routes.map((r) => new RegExp(`^${r.replace(/:[^/]+/g, '[^/]+')}$`));

// Il link puo' essere una stringa semplice o un template con ${...}: le
// interpolazioni valgono come un segmento qualsiasi.
const linkPatterns = [
  /navigate\(\s*['`](\/[^'`]*)['`]/g,
  /\bto="(\/[^"]*)"/g,
  /\bhref="(\/[^"]*)"/g,
  /\bcreateLink="(\/[^"]*)"/g,
  /\bpath:\s*['`](\/[^'`]*)['`]/g,
];

function collectSources(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) return ['api', 'mocks'].includes(entry.name) ? [] : collectSources(fullPath);
    return /\.(jsx?|tsx?)$/.test(entry.name) ? [fullPath] : [];
  });
}

function normalize(link) {
  return link
    .replace(/\$\{[^}]+\}/g, 'x')
    .split(/[?#]/)[0]
    .replace(/\/+$/, '') || '/';
}

describe('internal links contract', () => {
  it('App.jsx declares routes', () => {
    expect(routes.length).toBeGreaterThan(10);
  });

  it('every static in-app link matches a declared route', () => {
    const violations = [];
    for (const filePath of collectSources(srcRoot)) {
      const content = fs.readFileSync(filePath, 'utf-8');
      for (const pattern of linkPatterns) {
        for (const m of content.matchAll(pattern)) {
          const link = normalize(m[1]);
          if (!routeRegexes.some((re) => re.test(link))) {
            violations.push(`${path.relative(srcRoot, filePath)}: ${m[1]}`);
          }
        }
      }
    }
    expect(violations).toEqual([]);
  });
});
