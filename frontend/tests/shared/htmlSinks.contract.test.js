import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';

/**
 * Contract: nessun HTML costruito a stringa nel frontend.
 *
 * Audit 03/11/2026 (P1): il tooltip Leaflet di PreviewModal concatenava
 * nomi e valori delle colonne (dati DB) in una stringa HTML -> XSS.
 * Regola unica: i contenuti dinamici si inseriscono come testo (JSX o
 * textContent). Questo test enumera tutti i sink HTML conosciuti su tutto
 * src/ e fallisce se ne ricompare uno.
 */

const srcRoot = path.resolve(import.meta.dirname, '../../src');

const sinks = [
  { label: 'dangerouslySetInnerHTML', regex: /dangerouslySetInnerHTML/ },
  { label: 'innerHTML / outerHTML assignment', regex: /\.(inner|outer)HTML\s*=/ },
  { label: 'insertAdjacentHTML', regex: /insertAdjacentHTML\s*\(/ },
  { label: 'document.write', regex: /document\.write(ln)?\s*\(/ },
  // Leaflet interpreta le stringhe come HTML: ammessi solo nodi DOM.
  { label: 'Leaflet tooltip/popup con stringa', regex: /\.(bindTooltip|bindPopup|setTooltipContent|setPopupContent)\s*\(\s*[`'"]/ },
];

function collectSources(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) return entry.name === 'mocks' ? [] : collectSources(fullPath);
    return /\.(jsx?|tsx?)$/.test(entry.name) ? [fullPath] : [];
  });
}

describe('HTML sink contract', () => {
  it('no source file builds HTML from strings', () => {
    const violations = [];
    for (const filePath of collectSources(srcRoot)) {
      const content = fs.readFileSync(filePath, 'utf-8');
      for (const { label, regex } of sinks) {
        if (regex.test(content)) violations.push(`${path.relative(srcRoot, filePath)}: ${label}`);
      }
    }
    expect(violations).toEqual([]);
  });
});
