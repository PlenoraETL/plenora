import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';
import { escapeHtml } from '../../src/utils/escapeHtml';

/**
 * Contract: nessun HTML costruito da stringhe nel frontend.
 *
 * Audit 03/11/2026 (P1): il tooltip Leaflet di PreviewModal concatenava
 * nomi e valori delle colonne (dati DB) in una stringa HTML -> XSS.
 * Fase 5 T1 (R9-N5) e correzione dopo la conferma (punto 10).
 *
 * REGOLA: l'argomento di un sink HTML e' INTERAMENTE
 *   - una chiamata `escapeHtml(...)` (l'unica funzione di escape,
 *     utils/escapeHtml) che finisce dove finisce l'argomento
 *     (`escapeHtml(a) + row.name` NON lo e'), oppure
 *   - l'identificatore di un nodo DOM creato nel file
 *     (`document.createElement(...)`) e mai riassegnato, oppure
 *   - la stringa vuota (svuotare un contenitore).
 * Tutto il resto (variabili, template, concatenazioni, altre chiamate) e'
 * rifiutato. I sink sono elencati in SINKS; i casi di prova in fondo
 * verificano che il controllo riconosca ciascuno, nei due versi.
 */

const srcRoot = path.resolve(import.meta.dirname, '../../src');

function collectSources(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) return entry.name === 'mocks' ? [] : collectSources(fullPath);
    return /\.(jsx?|tsx?)$/.test(entry.name) ? [fullPath] : [];
  });
}

const OPEN = '([{';
const CLOSE = ')]}';

/**
 * Espressione che parte da `start` e finisce, a profondita' 0, su `;`, `,`,
 * fine riga o su una parentesi che chiude oltre l'inizio.
 */
function expressionAt(content, start) {
  let depth = 0;
  let quote = null;
  let i = start;
  for (; i < content.length; i += 1) {
    const ch = content[i];
    if (quote) {
      if (ch === '\\') { i += 1; continue; }
      if (ch === quote) quote = null;
      continue;
    }
    if (ch === '"' || ch === "'" || ch === '`') { quote = ch; continue; }
    if (OPEN.includes(ch)) { depth += 1; continue; }
    if (CLOSE.includes(ch)) { if (depth === 0) break; depth -= 1; continue; }
    if (depth === 0 && (ch === ';' || ch === ',' || ch === '\n')) break;
  }
  return content.slice(start, i).trim();
}

// Argomenti (testo) della chiamata che si apre a `openIndex` ('(').
function callArgs(content, openIndex) {
  const args = [];
  let i = openIndex + 1;
  while (i < content.length) {
    while (/\s/.test(content[i] || '')) i += 1;
    if (content[i] === ')') break;
    const expr = expressionAt(content, i);
    args.push(expr);
    i += content.slice(i).indexOf(expr) + expr.length;
    while (/\s/.test(content[i] || '')) i += 1;
    if (content[i] === ',') { i += 1; continue; }
    break;
  }
  return args;
}

// Indice della parentesi che chiude quella aperta a `openIndex`.
function matchingParen(text, openIndex) {
  let depth = 0;
  let quote = null;
  for (let i = openIndex; i < text.length; i += 1) {
    const ch = text[i];
    if (quote) { if (ch === '\\') { i += 1; continue; } if (ch === quote) quote = null; continue; }
    if (ch === '"' || ch === "'" || ch === '`') { quote = ch; continue; }
    if (OPEN.includes(ch)) depth += 1;
    if (CLOSE.includes(ch)) { depth -= 1; if (depth === 0) return i; }
  }
  return -1;
}

/** L'espressione e' INTERAMENTE escapeHtml(...). */
function isEscapeCall(expr) {
  const m = /^escapeHtml\s*\(/.exec(expr);
  if (!m) return false;
  return matchingParen(expr, m[0].length - 1) === expr.length - 1;
}

/** Nodo DOM creato nel file e mai riassegnato. */
function isDomNode(name, content) {
  if (!/^[A-Za-z_$][\w$]*$/.test(name)) return false;
  const declared = new RegExp(`(?:const|let|var)\\s+${name}\\s*=\\s*document\\.createElement\\s*\\(`).test(content);
  if (!declared) return false;
  const assignments = content.match(new RegExp(`(?<![\\w$.])${name}\\s*(?:\\+)?=(?!=)`, 'g')) || [];
  return assignments.length === 1;
}

function isSafeValue(expr, content) {
  const value = (expr || '').trim();
  if (!value) return false;
  if (/^(''|""|``)$/.test(value)) return true;
  return isEscapeCall(value) || isDomNode(value, content);
}

// Valore di una proprieta' di un oggetto letterale (`{ html }` = `html`).
function optionValue(options, key) {
  const m = new RegExp(`(?:^|[,{\\s])${key}\\s*(:)?`).exec(options);
  if (!m) return null;
  if (!m[1]) return key; // abbreviata
  return expressionAt(options, m.index + m[0].length);
}

/**
 * SINKS: ogni modo di far interpretare HTML a una stringa.
 * kind: 'call' (argomento `arg` della chiamata `.name(`), 'option' (proprieta'
 * `key` dell'oggetto passato a `name(`), 'assign' (assegnazione), 'jsx'.
 */
const SINKS = [
  { kind: 'assign', re: /\.(innerHTML|outerHTML|srcdoc)\s*\+?=(?!=)\s*/g, label: (m) => m[1] },
  { kind: 'assign', re: /\[\s*(['"`])(innerHTML|outerHTML|srcdoc)\1\s*\]\s*\+?=(?!=)\s*/g, label: (m) => `[${m[2]}]` },
  { kind: 'jsx', re: /dangerouslySetInnerHTML\s*=\s*\{\{\s*__html\s*:\s*/g, label: () => 'dangerouslySetInnerHTML' },
  { kind: 'jsx', re: /\bsrcDoc\s*=\s*\{\s*/g, label: () => 'srcDoc' },
  { kind: 'jsx', re: /\bsrcDoc\s*=\s*(['"])/g, label: () => 'srcDoc stringa', always: true },
  { kind: 'call', re: /\.insertAdjacentHTML\s*\(/g, arg: 1 },
  { kind: 'call', re: /\.(setHTML|setHTMLUnsafe)\s*\(/g, arg: 0 },
  { kind: 'call', re: /\.setAttribute\s*\(\s*(['"])srcdoc\1\s*,/g, arg: 1, attr: true },
  // Leaflet interpreta le stringhe come HTML.
  { kind: 'call', re: /\.(bindTooltip|bindPopup|setTooltipContent|setPopupContent|setContent)\s*\(/g, arg: 0 },
  // openPopup()/openTooltip() senza argomenti aprono quello gia' legato.
  { kind: 'call', re: /\.(openPopup|openTooltip)\s*\(/g, arg: 0, optional: true },
  { kind: 'option', re: /\b(?:divIcon|DivIcon)\s*\(/g, key: 'html' },
  { kind: 'option', re: /\b(?:L\.popup|L\.tooltip|new\s+L\.Popup|new\s+L\.Tooltip)\s*\(/g, key: 'content' },
];

/** Violazioni dei sink HTML in un sorgente. */
function htmlSinkViolations(content) {
  const out = [];
  for (const sink of SINKS) {
    for (const m of content.matchAll(sink.re)) {
      const after = m.index + m[0].length;
      if (sink.always) { out.push(`${sink.label(m)}: HTML letterale`); continue; }
      if (sink.kind === 'assign' || sink.kind === 'jsx') {
        const expr = expressionAt(content, after);
        if (!isSafeValue(expr, content)) out.push(`${sink.label(m)} (${expr}) non e' un nodo DOM ne' escapeHtml`);
        continue;
      }
      const open = sink.attr ? content.lastIndexOf('(', after) : after - 1;
      const args = callArgs(content, open);
      if (sink.kind === 'call') {
        const expr = args[sink.arg];
        if (expr === undefined && sink.optional) continue;
        if (!isSafeValue(expr, content)) out.push(`${m[0].replace(/\s*\($/, '')}(${expr || ''}) non e' un nodo DOM ne' escapeHtml`);
        continue;
      }
      // option: la proprieta' dell'oggetto passato (o un oggetto non letterale).
      const options = args[0] || '';
      if (!options.startsWith('{')) {
        if (options && !/^[A-Za-z_$][\w$]*$/.test(options)) continue;
        if (options) out.push(`${m[0].replace(/\s*\($/, '')}(${options}): opzioni non letterali`);
        continue;
      }
      const value = optionValue(options, sink.key);
      if (value !== null && !isSafeValue(value, content)) out.push(`${sink.key} (${value}) non e' un nodo DOM ne' escapeHtml`);
    }
  }
  if (/dangerouslySetInnerHTML(?!\s*=\s*\{\{\s*__html\s*:)/.test(content)) out.push('dangerouslySetInnerHTML non letterale');
  if (/document\.write(ln)?\s*\(/.test(content)) out.push('document.write');
  if (/createContextualFragment\s*\(/.test(content)) out.push('createContextualFragment');
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
    ['bindTooltip con escape seguito da concatenazione', 'layer.bindTooltip(escapeHtml(a) + row.name);'],
    ['bindTooltip con escape concatenato senza spazi', 'layer.bindTooltip(escapeHtml(a)+row.name);'],
    ['bindPopup con concatenazione prima dell\'escape', "layer.bindPopup('<b>' + escapeHtml(a));"],
    ['openPopup con contenuto', 'map.openPopup(row.name, latlng);'],
    ['openTooltip con contenuto', 'layer.openTooltip(`<i>${x}</i>`);'],
    ['L.popup con content', 'L.popup({ content: row.name }).openOn(map);'],
    ['L.tooltip con content abbreviato', "const content = '<b>' + v; L.tooltip({ content });"],
    ['new L.Popup con content', 'new L.Popup({ maxWidth: 200, content: html });'],
    ['divIcon con html abbreviato', "const html = '<i>' + v + '</i>'; L.divIcon({ html, className: 'x' });"],
    ['divIcon con html stringa', "L.divIcon({ className: 'x', html: '<i>' + v + '</i>' });"],
    ['divIcon con escape concatenato', "L.divIcon({ html: escapeHtml(a) + b });"],
    ['innerHTML', 'el.innerHTML = row.name;'],
    ['innerHTML +=', 'el.innerHTML += row.name;'],
    ['innerHTML con escape concatenato', 'el.innerHTML = escapeHtml(a) + row.name;'],
    ["el['innerHTML']", "el['innerHTML'] = row.name;"],
    ['el["outerHTML"]', 'el["outerHTML"] = x;'],
    ['outerHTML', 'el.outerHTML = `<p>${x}</p>`;'],
    ['srcdoc', 'frame.srcdoc = html;'],
    ['setAttribute srcdoc', "frame.setAttribute('srcdoc', html);"],
    ['srcDoc JSX', '<iframe srcDoc={html} />'],
    ['srcDoc JSX stringa', '<iframe srcDoc="<b>x</b>" />'],
    ['insertAdjacentHTML', "el.insertAdjacentHTML('beforeend', row.name);"],
    ['dangerouslySetInnerHTML', '<div dangerouslySetInnerHTML={{ __html: row.name }} />'],
    ['dangerouslySetInnerHTML con escape concatenato', '<div dangerouslySetInnerHTML={{ __html: escapeHtml(a) + b }} />'],
    ['setHTML', 'new maplibregl.Popup().setHTML(row.name);'],
    ['nodo DOM riassegnato', "let node = document.createElement('div'); node = row.name; layer.bindTooltip(node);"],
    ['nodo DOM non creato nel file', 'layer.bindTooltip(node);'],
  ])('riconosce il sink non sicuro: %s', (_label, code) => {
    expect(htmlSinkViolations(code).length).toBeGreaterThan(0);
  });

  it.each([
    ['tooltip con nodo DOM', "const table = document.createElement('table'); layer.bindTooltip(table, { sticky: true });"],
    ['tooltip con escape', 'layer.bindTooltip(escapeHtml(row.name));'],
    ['tooltip con escape e opzioni', 'layer.bindTooltip(escapeHtml(`${a} ${b}`), { sticky: true });'],
    ['openPopup senza argomenti', 'layer.openPopup();'],
    ['L.popup con content escape', 'L.popup({ content: escapeHtml(x) });'],
    ['L.popup senza content', 'L.popup({ maxWidth: 200 }).setContent(escapeHtml(x));'],
    ['divIcon con escape', "L.divIcon({ className: 'x', html: escapeHtml(label) });"],
    ['divIcon senza html', "L.divIcon({ className: 'pin' });"],
    ['innerHTML svuotato', "el.innerHTML = '';\n"],
    ['innerHTML con escape', 'el.innerHTML = escapeHtml(row.name);'],
    ['srcDoc con escape', '<iframe srcDoc={escapeHtml(text)} />'],
    ['dangerouslySetInnerHTML con escape', '<div dangerouslySetInnerHTML={{ __html: escapeHtml(text) }} />'],
    ['confronto, non assegnazione', "if (el.innerHTML === '') return;"],
  ])('accetta il sink sicuro: %s', (_label, code) => {
    expect(htmlSinkViolations(code)).toEqual([]);
  });

  it('escapeHtml neutralizza i caratteri HTML', () => {
    expect(escapeHtml('<img src=x onerror="a()">&\'`')).toBe('&lt;img src=x onerror=&quot;a()&quot;&gt;&amp;&#39;&#96;');
    expect(escapeHtml(null)).toBe('');
  });
});
