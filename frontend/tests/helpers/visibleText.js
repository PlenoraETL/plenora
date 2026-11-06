/**
 * visibleText — testo visibile all'utente nei sorgenti di src (Blocco A,
 * regola 10; UI-4, U22): testi JSX, attributi mostrati (title, placeholder,
 * aria-label...), stringhe e template dei messaggi. Esclusi import, classi,
 * chiavi, confronti, argomenti di chiamate tecniche e una parola minuscola
 * sola fuori dal JSX (e' una chiave, non testo).
 *
 * La usano il test del glossario degli editor e quello dei termini di
 * tutta l'app (tests/shared/glossary.contract.test.js, terms.contract.test.js).
 */
import fs from 'node:fs';
import path from 'node:path';
import * as espree from 'espree';
import { SRC } from './sourceScan.js';

const rel = (f) => path.relative(SRC, f).split(path.sep).join('/');

// Attributi JSX tecnici (mai mostrati): classi, chiavi, id, stili, valori.
const TECH_ATTR = /^(className|key|id|type|name|style|htmlFor|role|to|href|src|target|value|defaultValue|accept|autoComplete|rel|method|viewBox|d|fill|stroke\w*|width|height|x|y|cx|cy|r|tabIndex|inputMode|pattern|step|min|max|data-[\w-]+|aria-(?!label)[\w-]+|form|draggable|dir|lang|testId|variant|size|kind|mode|prefix|category|projectType|icon|createLink|uploadLink|nodeType|resultNodeType|position|handleId|sourceHandle|targetHandle)$/;
// Chiamate il cui argomento non e' testo per l'utente.
const TECH_CALL = /^(console\.\w+|require|import|fetch|navigate|http\.\w+|api\.\w+|\w+\.(?:get|set|has|delete|includes|startsWith|endsWith|indexOf|split|join|replace|replaceAll|match|test|querySelector|querySelectorAll|closest|getItem|setItem|removeItem|addEventListener|removeEventListener|setData|getData|getAttribute|setAttribute|dispatchEvent|toLocaleString|toLocaleDateString|toLocaleTimeString|padStart|padEnd)|useState|useCanvasStore|URLSearchParams|RegExp|Intl\.\w+|new\w*|encodeURIComponent|structuredClone)$/;
const COMPARE = new Set(['===', '!==', '==', '!=', 'in', 'instanceof']);
// Liste di classi CSS (blocco__elemento--modificatore) e chiavi tecniche.
// Anche percorsi ('/login'), selettori CSS ('select:not([disabled])') e
// chiavi composte ('source|output').
const isTechToken = (s) => /^[a-z0-9]+(?:[_.:/|\-[\]()="]+[a-z0-9]*)+$/i.test(s) || /^[a-z]+[A-Z]\w*$/.test(s)
  || /^\/[\w/-]*$/.test(s);
const isTechString = (s) => {
  const tokens = s.trim().split(/\s+/).filter(Boolean);
  return tokens.length > 0 && tokens.every(isTechToken);
};

const calleeName = (c) => {
  if (!c) return '';
  if (c.type === 'Identifier') return c.name;
  if (c.type === 'MemberExpression') return `${calleeName(c.object)}.${c.property?.name || ''}`;
  return '';
};

/** Testi visibili di un file: { file, line, text, exact }. */
export function visibleTexts(file) {
  const src = fs.readFileSync(file, 'utf-8');
  const ast = espree.parse(src, { ecmaVersion: 'latest', sourceType: 'module', ecmaFeatures: { jsx: true }, loc: true });
  const out = [];
  const push = (node, text) => {
    const t = String(text ?? '').replace(/\s+/g, ' ').trim();
    if (t && /\p{L}/u.test(t)) out.push({ file: rel(file), line: node.loc.start.line, text: t });
  };
  const walk = (node, parent, key, skip) => {
    if (!node || typeof node.type !== 'string') return;
    let skipChildren = skip;
    switch (node.type) {
      case 'ImportDeclaration': case 'ExportAllDeclaration': case 'ImportExpression':
        return;
      case 'ExportNamedDeclaration':
        // `export { x } from './modulo'`: il percorso non e' testo.
        if (node.declaration) walk(node.declaration, node, 'declaration', skip);
        return;
      case 'JSXAttribute':
        if (TECH_ATTR.test(node.name?.name || '')) skipChildren = true;
        break;
      case 'CallExpression': case 'NewExpression':
        if (TECH_CALL.test(calleeName(node.callee)) || (node.type === 'NewExpression' && calleeName(node.callee) !== 'Error')) skipChildren = true;
        break;
      case 'BinaryExpression':
        if (COMPARE.has(node.operator)) skipChildren = true;
        break;
      case 'SwitchCase':
        // Il valore di `case` e' tecnico, il corpo no.
        node.consequent.forEach(c => walk(c, node, 'consequent', skip));
        return;
      case 'MemberExpression':
        if (node.computed && node.property?.type === 'Literal') { walk(node.object, node, 'object', skip); return; }
        break;
      case 'Property':
        // Chiavi e valori di proprieta' tecniche (className, type, key...).
        if (!node.computed && TECH_ATTR.test(node.key?.name || node.key?.value || '')) return;
        walk(node.value, node, 'value', skip);
        return;
      case 'JSXText':
        if (!skip) push(node, node.value);
        return;
      case 'Literal':
        // Una sola parola minuscola fuori dal JSX e' una chiave (stato, tipo,
        // modalita'), non testo: il testo visibile ha spazi o maiuscole.
        if (!skip && typeof node.value === 'string' && !isTechString(node.value)
          && !/^[a-z][a-z0-9_-]*$/.test(node.value)) push(node, node.value);
        return;
      case 'TemplateLiteral':
        if (!skip) {
          const text = node.quasis.map(q => q.value.cooked).join(' X ');
          if (!isTechString(node.quasis.map(q => q.value.cooked).join(''))) push(node, text);
        }
        node.expressions.forEach(e => walk(e, node, 'expressions', skip));
        return;
      default:
    }
    for (const k of Object.keys(node)) {
      if (k === 'loc' || k === 'range') continue;
      const v = node[k];
      if (Array.isArray(v)) v.forEach(c => walk(c, node, k, skipChildren));
      else if (v && typeof v === 'object') walk(v, node, k, skipChildren);
    }
  };
  walk(ast, null, null, false);
  return out;
}

