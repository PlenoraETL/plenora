/**
 * Contract del glossario dei nomi visibili (Blocco A, regola 10), per
 * enumerazione su tutto il perimetro dei tre editor canvas.
 *
 *  1. Ogni tipo di nodo di ogni mappa `nodeTypes` dei tre editor ha la sua
 *     entita' nel glossario, e il descrittore (intestazione del pannello,
 *     nodo compatto, vista Passi) ne mostra nome e icona.
 *  2. Ogni voce del menu "⋯" dei tre editor viene dal glossario
 *     (menuEntry): nessuna etichetta scritta a mano.
 *  3. Nessun nome di entita' scritto a mano fuori dal glossario: un testo
 *     visibile uguale al nome di un'entita' deve leggerlo da li'.
 *  4. Nessun termine vietato (inglese o variante di un nome) nel testo
 *     visibile dei file del perimetro: testi JSX, attributi, messaggi.
 *     Le eccezioni sono quelle decise dall'utente (ALLOWED_ENGLISH) e
 *     quelle dichiarate qui sotto, ognuna col suo motivo.
 *  5. Il catalogo delle operazioni Manipola del backend, dopo il
 *     glossario, non mostra termini vietati in nomi, descrizioni ed
 *     etichette dei parametri.
 * Un termine vietato nuovo, un tipo di nodo nuovo o una voce di menu nuova
 * senza glossario fanno fallire il test.
 */
import fs from 'node:fs';
import path from 'node:path';
import * as espree from 'espree';
import { describe, it, expect, vi } from 'vitest';
import {
  ALLOWED_ENGLISH, NODE_TYPE_TERMS, TERMS, TRANSFORM_TERMS, applyTransformTerms, flowEdgeLabel,
  withFlowEdgeLabels,
} from '../../src/utils/glossary';
import {
  nodeDescriptor, nodeIcon, nodeTitle, transformSummary, userTitle,
} from '../../src/components/canvas/nodeDescriptors';
import { normalizeRegistry } from '../../src/utils/transformRegistry';

// Il registry in cache e' quello vero del backend, normalizzato come in app.
const { cachedRegistry } = vi.hoisted(() => ({ cachedRegistry: { current: null } }));
vi.mock('../../src/utils/transformRegistry', async (importOriginal) => ({
  ...(await importOriginal()),
  getCachedTransformRegistry: () => cachedRegistry.current,
}));

const SRC = path.resolve(import.meta.dirname, '../../src');
const REGISTRY = path.resolve(import.meta.dirname, '../../../backend/app/core/manipola/transforms_registry.json');

// ── Perimetro: i tre editor e i loro componenti ──────────────────────────
const PERIMETER = [
  'pages/EstraiEditor.jsx', 'pages/ManipolaEditor.jsx', 'pages/ArricchisciEditor.jsx',
  'components/canvas', 'components/editor', 'components/nodes', 'components/transforms',
  // Pannelli e finestre usati dagli editor.
  'components/panels/ApiEnrichNode.jsx', 'components/panels/EditorSidebar.jsx',
  'components/panels/FilesSidebar.jsx', 'components/panels/ProjectsSidebar.jsx',
  'components/panels/FeatureServicePanel.jsx', 'components/panels/QueryPreview.jsx',
  'components/modals/CanvasVersionsModal.jsx', 'components/modals/ColumnBuilderModal.jsx',
  'components/modals/ExecutionModal.jsx',
  'components/modals/FilterModal.jsx', 'components/modals/PreviewModal.jsx',
  'components/modals/PreviewTable.jsx', 'components/modals/RunHistoryModal.jsx',
  'components/modals/ValidationModal.jsx', 'components/arricchisci/ConnectionSelectorModal.jsx',
  'hooks',
  // Messaggi mostrati dagli editor.
  'utils/estraiJoins.js', 'utils/taskFailureMessage.js',
];
const GLOSSARY_FILE = path.join(SRC, 'utils', 'glossary.js');

const list = (p) => (fs.statSync(p).isDirectory()
  ? fs.readdirSync(p).flatMap(n => list(path.join(p, n)))
  : (/\.jsx?$/.test(p) ? [p] : []));
const FILES = PERIMETER.flatMap(r => list(path.join(SRC, r)));
const rel = (f) => path.relative(SRC, f).split(path.sep).join('/');

// ── Estrazione del testo visibile ─────────────────────────────────────────
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
function visibleTexts(file) {
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

const TEXTS = FILES.filter(f => f !== GLOSSARY_FILE).flatMap(visibleTexts);

// ── Termini vietati ───────────────────────────────────────────────────────
// Parole inglesi da tradurre (minuscole: si cercano senza distinguere).
const FORBIDDEN_WORDS = [
  'preview', 'previews', 'pipeline', 'pipelines', 'run', 'runs', 'runtime', 'mapping', 'mappings', 'auto-mapping',
  'edge', 'edges', 'body', 'scheduler', 'schedule', 'datasource', 'delete', 'save', 'saved', 'cancel', 'cancelled',
  'loading', 'error', 'errors', 'warning', 'close', 'upload', 'download', 'settings', 'default', 'override',
  'header', 'headers', 'batch', 'timeout', 'template', 'templates', 'feature', 'features', 'step', 'steps',
  'node', 'nodes', 'source', 'sources', 'target', 'field', 'fields', 'value', 'values', 'column', 'columns',
  'table', 'tables', 'filter', 'sort', 'select', 'insert', 'update', 'upsert', 'append', 'replace', 'overwrite',
  'truncate', 'add', 'remove', 'edit', 'drag', 'drop', 'click', 'logout', 'login', 'bundle', 'diff', 'fallback',
  'shape', 'sidebar', 'snapshot', 'backend', 'frontend', 'placeholder', 'match', 'matchati', 'lookup', 'task',
  'tasks', 'status', 'success', 'failed', 'pending', 'running', 'done', 'job', 'jobs', 'retry', 'merge', 'split',
  'spatial', 'buffer', 'clip', 'nearest', 'boundary', 'overlay', 'unpivot', 'melt', 'union', 'padding', 'dedup',
  'regex', 'wizard', 'toolbar', 'debug', 'workflow', 'dataset', 'df', 'param', 'params', 'mode', 'per-call',
  'group by', 'case when', 'bounding box', 'cross join', 'spatial join', 'change detection', 'multi join',
  'multijoin', 'file source', 'static values', 'report', 'background', 'rollback', 'policy', 'pattern',
  'registry', 'legacy', 'secret', 'sheet', 'username', 'extent', 'dark', 'offset', 'check', 'connection',
  'project', 'service', 'stop', 'fit', 'extra', 'drift', 'contract',
];
// Varianti dei nomi delle entita' (nomi delle versioni precedenti).
const FORMER_NAMES = Object.values(TERMS).flatMap(t => t.formerNames || []);
// "Output" e "Join" sono nomi di entita': si scrivono sempre maiuscoli.
const EXACT_CASE = [['output', 'Output'], ['join', 'Join'], ['data source', 'Data Source'], ['multi-join', 'Multi-Join']];

// Eccezioni dichiarate: testo ammesso nonostante una parola vietata.
const DECLARED_EXCEPTIONS = [
  // Parole tutte maiuscole: parole chiave SQL mostrate come codice o come
  // modalita' di scrittura del database (SELECT, JOIN, UPSERT, TRUNCATE...).
  /(?<![\p{L}\d_])\p{Lu}{2,}(?:[ +]+\p{Lu}{2,})*(?![\p{L}\d_])/u,
  // "Input runtime": nome dell'entita' nel glossario, indicato dall'utente.
  /\binput runtime\b/iu,
  // Segnaposto delle variabili dinamiche ({project}, {date}): codice.
  /\{[\w.]+\}/u,
];

const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const wordRe = (w, flags = 'iu') => new RegExp(`(?<![\\p{L}\\d_-])${escapeRe(w)}(?![\\p{L}\\d_-])`, flags);
const FORBIDDEN_RES = FORBIDDEN_WORDS.map(w => [w, wordRe(w)]);
const FORMER_RES = FORMER_NAMES.map(w => [w, wordRe(w, 'u')]);
const ALLOWED_RES = [
  ...ALLOWED_ENGLISH.names.map(w => wordRe(w, 'u')),
  ...ALLOWED_ENGLISH.technical.map(w => wordRe(w, 'iu')),
];

const strip = (text, res) => res.reduce((t, re) => t.replace(new RegExp(re.source, `${re.flags.replace('g', '')}g`), ' '), text);
// Nomi precedenti che sono anche parole italiane comuni ("Unione
// geometrica", "unione per righe"): vietati solo come testo intero.
const FORMER_AS_WHOLE_TEXT = new Set(['Unione', 'Trasformazione']);
const bare = (text) => text.replace(/^[^\p{L}]+|[^\p{L}]+$/gu, '');

/** Parole vietate presenti in un testo, tolte le eccezioni ammesse. */
function forbiddenIn(text) {
  // Varianti e frasi si cercano prima di togliere le parole ammesse
  // ("Multi Join" contiene "Join"); le parole singole dopo.
  const raw = strip(text, DECLARED_EXCEPTIONS);
  const t = strip(raw, ALLOWED_RES);
  const found = FORBIDDEN_RES.filter(([w, re]) => re.test(/[ -]/.test(w) ? raw : t)).map(([w]) => w);
  for (const [w, re] of FORMER_RES) {
    if (FORMER_AS_WHOLE_TEXT.has(w) ? bare(text) === w : re.test(text)) found.push(w);
  }
  for (const [w, exact] of EXACT_CASE) {
    for (const m of raw.matchAll(new RegExp(`(?<![\\p{L}\\d_-])${escapeRe(w)}(?![\\p{L}\\d_-])`, 'giu'))) {
      if (m[0] !== exact) found.push(m[0]);
    }
  }
  return found;
}

// ── Editor: mappe nodeTypes e menu "⋯" ──────────────────────────────────
const EDITORS = ['EstraiEditor.jsx', 'ManipolaEditor.jsx', 'ArricchisciEditor.jsx'];
const editorSource = (file) => fs.readFileSync(path.join(SRC, 'pages', file), 'utf-8');
const nodeTypesOf = (src) => {
  const body = src.match(/const nodeTypes = \{([\s\S]*?)\n\};/)[1].replace(/\/\/[^\n]*/g, '');
  return [...body.matchAll(/\b(\w+):\s*\w+/g)].map(m => m[1]);
};
// La proprieta' `menuItems` della barra: fino alla chiusura del suo elenco
// (`    ],`), comprese le forme `isReadOnly ? [] : [...]`.
const menuBlockOf = (src) => (src.match(/\n\s*menuItems:[\s\S]*?\n {4}\],/) || [''])[0];

describe('glossario: ogni entita\' ha un nome, un\'icona e una descrizione', () => {
  it('ogni voce del glossario e\' completa', () => {
    for (const [key, t] of Object.entries(TERMS)) {
      expect({ key, ok: Boolean(t.name && t.icon && t.description) }).toEqual({ key, ok: true });
    }
  });

  it.each(EDITORS)('%s: ogni tipo di nodo ha la sua entita\' e il descrittore la mostra', (file) => {
    for (const type of nodeTypesOf(editorSource(file))) {
      expect({ type, inGlossary: Boolean(NODE_TYPE_TERMS[type] && TERMS[NODE_TYPE_TERMS[type]]) }).toEqual({ type, inGlossary: true });
      if (type === 'comment' || type === 'transform') continue;
      const t = TERMS[NODE_TYPE_TERMS[type]];
      expect({ type, kind: nodeDescriptor(type).kind({}), icon: nodeIcon(type, {}) }).toEqual({ type, kind: t.name, icon: t.icon });
    }
  });

  it.each(EDITORS)('%s: ogni voce del menu "⋯" viene dal glossario', (file) => {
    const block = menuBlockOf(editorSource(file));
    expect(block.length).toBeGreaterThan(20);
    // Niente etichette o icone scritte a mano: solo menuEntry('chiave', ...) e divisori.
    expect(block.match(/\b(label|icon):/g) || []).toEqual([]);
    const keys = [...block.matchAll(/menuEntry\('(\w+)'/g)].map(m => m[1]);
    expect(keys.length).toBeGreaterThan(3);
    expect(keys.filter(k => !TERMS[k])).toEqual([]);
  });
});

describe('glossario: i nomi delle versioni precedenti', () => {
  it('un titolo congelato con un nome precedente torna al nome del glossario', () => {
    const cases = Object.entries(NODE_TYPE_TERMS)
      .filter(([type]) => type !== 'transform' && type !== 'comment')
      .flatMap(([type, key]) => (TERMS[key].formerNames || []).map(former => [type, key, former]));
    expect(cases.length).toBeGreaterThan(3);
    for (const [type, , former] of cases) {
      expect({ type, former, user: userTitle(type, { title: former }) }).toEqual({ type, former, user: '' });
    }
    expect(nodeTitle('multi_join', { title: 'Unione' })).toBe('Multi-Join');
  });

  it('le etichette degli archi verso Output e caricamenti vengono dal glossario', () => {
    const saved = [
      { id: 'a', data: { join_type: 'carica' }, label: 'Carica DB' },
      { id: 'b', data: { join_type: 'output' }, label: 'output' },
      { id: 'c', data: { join_type: 'left' }, label: 'left join' },
    ];
    const shown = withFlowEdgeLabels(saved);
    expect(shown.map(e => e.label)).toEqual([TERMS.load_db.name, TERMS.output.name, 'left join']);
    expect(shown[2]).toBe(saved[2]);
    expect(withFlowEdgeLabels(shown)).toBe(shown);
    expect(flowEdgeLabel('carica_arcgis')).toBe(TERMS.load_arcgis.name);
  });
});

describe('glossario: un solo nome per entita\', scritto in un solo posto', () => {
  // Nomi brevi e comuni anche come parole qualsiasi: non si richiede il glossario.
  const GENERIC = new Set(['File', 'Tabella', 'Vista', 'Nota', 'Passo', 'Operazione', 'Join', 'Output', 'Connessioni', 'Versioni']);
  const NAMES = new Set(Object.values(TERMS).map(t => t.name).filter(n => !GENERIC.has(n)));

  it('nessun testo visibile uguale al nome di un\'entita\' fuori dal glossario', () => {
    const offenders = TEXTS
      .filter(({ text }) => NAMES.has(text.replace(/^[^\p{L}]+|[^\p{L}…]+$/gu, '')))
      .map(({ file, line, text }) => `${file}:${line}: ${text}`);
    expect(offenders).toEqual([]);
  });
});

describe('glossario: nessun termine vietato nel testo visibile degli editor', () => {
  it('il perimetro e\' quello atteso (file letti)', () => {
    expect(FILES.length).toBeGreaterThan(90);
    expect(TEXTS.length).toBeGreaterThan(500);
  });

  it('nessuna parola inglese vietata ne\' variante di un nome', () => {
    const offenders = TEXTS
      .map(({ file, line, text }) => ({ where: `${file}:${line}`, text, words: forbiddenIn(text) }))
      .filter(o => o.words.length)
      .map(o => `${o.where}: [${o.words.join(', ')}] ${o.text.slice(0, 120)}`);
    expect(offenders).toEqual([]);
  });

  it('il controllo riconosce i termini vietati e rispetta le eccezioni', () => {
    expect(forbiddenIn('Anteprima Pipeline Manipola')).toEqual(['pipeline']);
    expect(forbiddenIn('Cronologia run')).toEqual(['run']);
    expect(forbiddenIn('Unione')).toEqual(['Unione']);
    expect(forbiddenIn('Multi Join')).toContain('multi join');
    expect(forbiddenIn('colonne in output')).toEqual(['output']);
    expect(forbiddenIn('Aggiungi un Output e un Join su Data Source o Multi-Join')).toEqual([]);
    expect(forbiddenIn('Query SQL verso ArcGIS, API REST, file CSV e GeoJSON')).toEqual([]);
    expect(forbiddenIn('SELECT * FROM a LEFT JOIN b')).toEqual([]);
  });
});

describe('glossario: il catalogo delle operazioni Manipola in italiano', () => {
  const registry = JSON.parse(fs.readFileSync(REGISTRY, 'utf-8'));
  cachedRegistry.current = normalizeRegistry(registry);
  // Termini tecnici del catalogo (SQL e analisi spaziale), ammessi.
  const CATALOG_TECHNICAL = [
    /^(Inner|Left|Right|Outer|Full|Cross) \(/, // tipi di join SQL
    /^(Intersects|Contains|Within|Crosses|Overlaps|Touches) \(/, // predicati spaziali (ST_*)
    /^(Rank|Dense Rank|Lag|Lead) \(/, // funzioni finestra SQL
    /\(per lag\/lead\)/, /^Null$/,
  ];
  const texts = (op, def) => [
    [`${op}.name`, def.name], [`${op}.description`, def.description],
    ...(def.parameters || []).flatMap(p => [
      [`${op}.${p.id}.label`, p.label],
      [`${op}.${p.id}.description`, p.description],
      ...(Array.isArray(p.options) ? p.options : []).filter(o => o && typeof o === 'object').map(o => [`${op}.${p.id}=${o.value}`, o.label]),
    ]),
  ].filter(([, t]) => typeof t === 'string' && t);

  it('nomi, descrizioni, etichette e descrizioni dei parametri senza termini vietati', () => {
    const offenders = Object.entries(registry.transforms)
      .flatMap(([op, def]) => texts(op, applyTransformTerms(op, def)))
      .filter(([, t]) => !CATALOG_TECHNICAL.some(re => re.test(t)))
      .map(([where, t]) => [where, t, forbiddenIn(t)])
      .filter(([, , words]) => words.length)
      .map(([where, t, words]) => `${where}: [${words.join(', ')}] ${t}`);
    expect(offenders).toEqual([]);
  });

  it('ogni sovrascrittura riguarda un\'operazione del catalogo e cambia qualcosa', () => {
    for (const [op, over] of Object.entries(TRANSFORM_TERMS)) {
      const def = registry.transforms[op];
      expect({ op, exists: Boolean(def) }).toEqual({ op, exists: true });
      if (over.name) expect({ op, changed: over.name !== def.name }).toEqual({ op, changed: true });
      for (const pid of Object.keys(over.params || {})) {
        expect({ op, pid, exists: (def.parameters || []).some(p => p.id === pid) }).toEqual({ op, pid, exists: true });
      }
    }
  });

  it('il nome del backend resta riconoscibile (titoli congelati, ricerca)', () => {
    const def = applyTransformTerms('aggregate', registry.transforms.aggregate);
    expect(def.name).toBe('Raggruppa');
    expect(def.catalog_name).toBe(registry.transforms.aggregate.name);
    // Un titolo congelato col nome del catalogo non e' testo dell'utente.
    const data = { transform_op: 'aggregate', config: {}, title: registry.transforms.aggregate.name };
    expect(userTitle('transform', data)).toBe('');
    expect(nodeTitle('transform', data)).toBe('Raggruppa');
  });

  it('il riepilogo di riserva usa le etichette, mai le chiavi tecniche (ogni operazione)', () => {
    const DEDICATED = new Set(['filter', 'formula', 'rename', 'drop_columns', 'select_columns', 'aggregate', 'join', 'lookup', 'type_cast', 'sort', 'distinct']);
    for (const [op, def] of Object.entries(registry.transforms)) {
      if (DEDICATED.has(op)) continue;
      // Ogni parametro con un valore semplice: la prima opzione o un testo.
      const config = Object.fromEntries((def.parameters || []).map(p => [p.id,
        Array.isArray(p.options) && p.options[0] && typeof p.options[0] === 'object' ? p.options[0].value : 'x']));
      const summary = transformSummary(op, config);
      const leaked = (def.parameters || []).filter(p => p.id.length > 1 && summary.includes(`${p.id}:`)).map(p => p.id);
      expect({ op, leaked }).toEqual({ op, leaked: [] });
    }
    expect(transformSummary('sjoin', { how: 'inner', predicate: 'intersects' })).not.toMatch(/\bhow:|\bpredicate:/);
  });
});
