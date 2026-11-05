import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';
import { escapeHtml } from '../../src/utils/escapeHtml';

/**
 * Contract: nessun HTML costruito da stringhe nel frontend.
 *
 * Audit 03/11/2026 (P1): il tooltip Leaflet di PreviewModal concatenava
 * nomi e valori delle colonne (dati DB) in una stringa HTML -> XSS.
 * Fase 5 T1 (R9-N5): la guardia riconosceva solo i letterali; ora OGNI sink
 * HTML accetta solo
 *   - un nodo DOM costruito nel file (`const x = document.createElement(...)`
 *     riempito con textContent), oppure
 *   - un valore passato dall'unica funzione di escape (utils/escapeHtml).
 * Variabili, template, concatenazioni e chiamate ad altro sono rifiutati.
 * I casi di prova in fondo verificano che il controllo li riconosca.
 */

const srcRoot = path.resolve(import.meta.dirname, '../../src');

function collectSources(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) return entry.name === 'mocks' ? [] : collectSources(fullPath);
    return /\.(jsx?|tsx?)$/.test(entry.name) ? [fullPath] : [];
  });
}

// Argomenti (testo) della chiamata che si apre a `openIndex` ('(').
function callArgs(content, openIndex) {
  const args = [];
  let depth = 0;
  let current = '';
  let quote = null;
  for (let i = openIndex; i < content.length; i += 1) {
    const ch = content[i];
    if (quote) {
      current += ch;
      if (ch === '\\') { current += content[i + 1] || ''; i += 1; continue; }
      if (ch === quote) quote = null;
      continue;
    }
    if (ch === '"' || ch === "'" || ch === '`') { quote = ch; current += ch; continue; }
    if ('([{'.includes(ch)) { depth += 1; if (depth === 1) continue; }
    if (')]}'.includes(ch)) { depth -= 1; if (depth === 0) { args.push(current.trim()); return args; } }
    if (ch === ',' && depth === 1) { args.push(current.trim()); current = ''; continue; }
    current += ch;
  }
  return args;
}

const isEscaped = (expr) => /^escapeHtml\s*\(/.test(expr);
function isSafeValue(expr, content) {
  if (!expr) return false;
  if (isEscaped(expr)) return true;
  if (/^[A-Za-z_$][\w$]*$/.test(expr)) {
    // Identificatore: deve essere un nodo DOM creato nel file.
    return new RegExp(`(?:const|let)\\s+${expr}\\s*=\\s*document\\.createElement\\s*\\(`).test(content);
  }
  return false;
}

/** Violazioni dei sink HTML in un sorgente. */
function htmlSinkViolations(content) {
  const out = [];
  const each = (re, fn) => { for (const m of content.matchAll(re)) fn(m); };

  each(/dangerouslySetInnerHTML\s*=\s*\{\{\s*__html\s*:\s*/g, (m) => {
    const rest = content.slice(m.index + m[0].length);
    if (!isEscaped(rest)) out.push('dangerouslySetInnerHTML senza escapeHtml');
  });
  if (/dangerouslySetInnerHTML(?!\s*=\s*\{\{\s*__html\s*:)/.test(content)) out.push('dangerouslySetInnerHTML non letterale');

  each(/\.(inner|outer)HTML\s*\+?=\s*/g, (m) => {
    const rest = content.slice(m.index + m[0].length);
    if (!(isEscaped(rest) || /^(''|""|``)\s*[;\n]/.test(rest))) out.push(`${m[1]}HTML assegnato senza escapeHtml`);
  });

  each(/insertAdjacentHTML\s*\(/g, (m) => {
    const args = callArgs(content, m.index + m[0].length - 1);
    if (!isEscaped(args[1] || '')) out.push('insertAdjacentHTML senza escapeHtml');
  });

  if (/document\.write(ln)?\s*\(/.test(content)) out.push('document.write');
  if (/createContextualFragment\s*\(/.test(content)) out.push('createContextualFragment');

  // Leaflet interpreta le stringhe come HTML.
  each(/\.(bindTooltip|bindPopup|setTooltipContent|setPopupContent|setContent)\s*\(/g, (m) => {
    const args = callArgs(content, m.index + m[0].length - 1);
    if (!isSafeValue(args[0], content)) out.push(`${m[1]}(${args[0] || ''}) non e' un nodo DOM ne' escapeHtml`);
  });

  // L.divIcon({ html }) / new L.DivIcon({ html: ... })
  each(/\b(?:divIcon|DivIcon)\s*\(/g, (m) => {
    const [options = ''] = callArgs(content, m.index + m[0].length - 1);
    const html = options.match(/(?:^|[,{\s])html\s*(?::\s*([^,}]+)|(?=[,}\s]|$))/);
    if (!html) return;
    const expr = (html[1] || 'html').trim();
    if (!isSafeValue(expr, content)) out.push(`divIcon html (${expr}) non e' un nodo DOM ne' escapeHtml`);
  });

  // maplibre Popup#setHTML
  each(/\.setHTML\s*\(/g, (m) => {
    const args = callArgs(content, m.index + m[0].length - 1);
    if (!isEscaped(args[0] || '')) out.push('setHTML senza escapeHtml');
  });
  return out;
}

describe('HTML sink contract', () => {
  it('no source file feeds an HTML sink with anything but DOM nodes or escapeHtml', () => {
    const violations = [];
    for (const filePath of collectSources(srcRoot)) {
      if (filePath.endsWith(`utils${path.sep}escapeHtml.js`)) continue;
      const content = fs.readFileSync(filePath, 'utf-8');
      for (const v of htmlSinkViolations(content)) violations.push(`${path.relative(srcRoot, filePath)}: ${v}`);
    }
    expect(violations).toEqual([]);
  });

  it.each([
    ['bindTooltip con variabile stringa', "const html = '<b>' + v + '</b>'; layer.bindTooltip(html);"],
    ['bindTooltip con template', 'layer.bindTooltip(`<b>${v}</b>`, { sticky: true });'],
    ['bindPopup con chiamata', 'layer.bindPopup(buildHtml(row));'],
    ['divIcon con html abbreviato', "const html = '<i>' + v + '</i>'; L.divIcon({ html, className: 'x' });"],
    ['divIcon con html stringa', "L.divIcon({ className: 'x', html: '<i>' + v + '</i>' });"],
    ['innerHTML', 'el.innerHTML = row.name;'],
    ['innerHTML +=', 'el.innerHTML += row.name;'],
    ['outerHTML', 'el.outerHTML = `<p>${x}</p>`;'],
    ['insertAdjacentHTML', "el.insertAdjacentHTML('beforeend', row.name);"],
    ['dangerouslySetInnerHTML', '<div dangerouslySetInnerHTML={{ __html: row.name }} />'],
    ['setHTML', 'new maplibregl.Popup().setHTML(row.name);'],
  ])('riconosce il sink non sicuro: %s', (_label, code) => {
    expect(htmlSinkViolations(code).length).toBeGreaterThan(0);
  });

  it.each([
    ['tooltip con nodo DOM', "const table = document.createElement('table'); layer.bindTooltip(table, { sticky: true });"],
    ['tooltip con escape', 'layer.bindTooltip(escapeHtml(row.name));'],
    ['divIcon con escape', "L.divIcon({ className: 'x', html: escapeHtml(label) });"],
    ['divIcon senza html', "L.divIcon({ className: 'pin' });"],
    ['innerHTML svuotato', "el.innerHTML = '';\n"],
    ['dangerouslySetInnerHTML con escape', '<div dangerouslySetInnerHTML={{ __html: escapeHtml(text) }} />'],
  ])('accetta il sink sicuro: %s', (_label, code) => {
    expect(htmlSinkViolations(code)).toEqual([]);
  });

  it('escapeHtml neutralizza i caratteri HTML', () => {
    expect(escapeHtml('<img src=x onerror="a()">&\'`')).toBe('&lt;img src=x onerror=&quot;a()&quot;&gt;&amp;&#39;&#96;');
    expect(escapeHtml(null)).toBe('');
  });
});
