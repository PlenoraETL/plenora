/**
 * expressionRefs — riferimenti a colonne dentro un'espressione (Formula,
 * parametri `expression` del registro) e loro riscrittura quando una
 * colonna cambia nome a monte.
 *
 * DOVE si riscrive (Fase 5, T6, R3.12): solo sui token che il motore delle
 * formule legge come riferimenti a colonna. Copia del tokenizzatore del
 * backend (backend/app/shared/execution/expression_refs.py, lo stesso
 * riconoscimento di `ast` sulle espressioni valide): un nome che non e' una
 * parola chiave, un attributo (`x.str`), il nome di una funzione chiamata
 * (`round(x)`) o di un argomento (`ndigits=2`), oppure un nome tra backtick.
 * Mai dentro le stringhe tra apici o virgolette, mai nei numeri (`1e5`), mai
 * nei commenti. Il corpus backend/tests2/contracts/expression_refs_cases.json
 * fa girare le due copie sugli stessi casi.
 *
 * QUANDO si riscrive: un nome nell'espressione si riscrive SOLO se si
 * risolve in modo univoco a una colonna, e la riscrittura segue l'id.
 *  - Con i riferimenti salvati (`<param>_column_refs` = [{id, name}]): per
 *    id. Il nome vecchio si sostituisce col nome attuale della stessa
 *    colonna, ma non se il nome vecchio e' oggi il nome di UN'ALTRA colonna
 *    (sarebbe ambiguo: si lascia com'e'), a meno che quell'altra colonna non
 *    sia rinominata anche lei secondo i riferimenti (scambio a<->b).
 *  - Tutte le rinomine di un'espressione si applicano INSIEME
 *    (`rewriteExpressionNames`, come `rewrite_expression_refs` del backend).
 *  - Senza riferimenti (progetti vecchi): un nome che e' il nome ATTUALE di
 *    una colonna non si tocca mai; un nome che non esiste piu' si riscrive
 *    solo se esattamente una colonna lo dichiara come nome di origine
 *    (source_name / display_name). Esempio: a valle di un Join con nomi
 *    doppi `name` resta `name` (la colonna di sinistra), non diventa
 *    `name_R`.
 */
const columnKey = (col) => col?.id || col?.column_id || null;

const PY_KEYWORDS = new Set(['False', 'None', 'True', 'and', 'as', 'assert', 'async', 'await', 'break',
  'class', 'continue', 'def', 'del', 'elif', 'else', 'except', 'finally', 'for', 'from', 'global', 'if',
  'import', 'in', 'is', 'lambda', 'nonlocal', 'not', 'or', 'pass', 'raise', 'return', 'try', 'while',
  'with', 'yield']);
// Come Python `[^\W\d]\w*` (lettere, numeri non decimali, underscore).
const NAME_RE = /[\p{L}\p{Nl}\p{No}_][\p{L}\p{N}_]*/uy;
const NUMBER_RE = /0[xXoObB][0-9a-fA-F_]+|(?:[0-9][0-9_]*(?:\.[0-9_]*)?|\.[0-9][0-9_]*)(?:[eE][+-]?[0-9][0-9_]*)?[jJ]?/y;
const STRING_PREFIXES = new Set(['r', 'u', 'b', 'f', 'br', 'rb', 'fr', 'rf']);
const SPACES = ' \t\r\n\f\v';
const isDigit = (ch) => ch >= '0' && ch <= '9';

function stringEnd(expr, index) {
  const quote = expr[index];
  const triple = quote.repeat(3);
  if (expr.startsWith(triple, index)) {
    const close = expr.indexOf(triple, index + 3);
    return close < 0 ? expr.length : close + 3;
  }
  let i = index + 1;
  while (i < expr.length) {
    const ch = expr[i];
    if (ch === '\\') { i += 2; continue; }
    if (ch === quote || ch === '\n') return i + 1;
    i += 1;
  }
  return expr.length;
}

function skipSpaces(expr, index, step) {
  let i = index;
  while (i >= 0 && i < expr.length && SPACES.includes(expr[i])) i += step;
  return i;
}

function nameKind(expr, start, end, text) {
  if (PY_KEYWORDS.has(text)) return 'keyword';
  const before = skipSpaces(expr, start - 1, -1);
  if (before >= 0 && expr[before] === '.') return 'attribute';
  const after = skipSpaces(expr, end, 1);
  if (after < expr.length) {
    if (expr[after] === '(') return 'function';
    if (expr[after] === '=' && !expr.startsWith('==', after)) return 'kwarg';
  }
  return 'name';
}

/** Token dell'espressione: { kind, start, end, text }. */
function expressionTokens(expression) {
  const tokens = [];
  if (typeof expression !== 'string') return tokens;
  const expr = expression;
  const n = expr.length;
  let i = 0;
  const push = (kind, start, end) => tokens.push({ kind, start, end, text: expr.slice(start, end) });
  while (i < n) {
    const ch = expr[i];
    if (SPACES.includes(ch)) { i += 1; continue; }
    if (ch === '#') {
      const nl = expr.indexOf('\n', i);
      const end = nl < 0 ? n : nl;
      push('comment', i, end); i = end; continue;
    }
    if (ch === "'" || ch === '"') {
      const end = stringEnd(expr, i);
      push('string', i, end); i = end; continue;
    }
    if (ch === '`') {
      const close = expr.indexOf('`', i + 1);
      if (close < 0) { push('other', i, n); break; }
      push('backtick', i, close + 1); i = close + 1; continue;
    }
    if (isDigit(ch) || (ch === '.' && i + 1 < n && isDigit(expr[i + 1]))) {
      NUMBER_RE.lastIndex = i;
      const m = NUMBER_RE.exec(expr);
      const end = m && m[0].length > 0 ? i + m[0].length : i + 1;
      push('number', i, end); i = end; continue;
    }
    NAME_RE.lastIndex = i;
    const m = NAME_RE.exec(expr);
    if (m) {
      const text = m[0];
      const end = i + text.length;
      if (end < n && (expr[end] === "'" || expr[end] === '"') && STRING_PREFIXES.has(text.toLowerCase())) {
        const close = stringEnd(expr, end);
        push('string', i, close); i = close; continue;
      }
      tokens.push({ kind: nameKind(expr, i, end, text), start: i, end, text });
      i = end; continue;
    }
    // Un carattere (anche fuori dal BMP) alla volta.
    const cp = expr.codePointAt(i);
    const width = cp > 0xffff ? 2 : 1;
    push('other', i, i + width); i += width;
  }
  return tokens;
}

const tokenRef = (t) => {
  if (t.kind === 'name') return t.text;
  if (t.kind === 'backtick') return t.text.slice(1, -1);
  return null;
};

/** Nomi delle colonne citate, nell'ordine, senza doppioni. */
export function expressionColumnRefs(expression) {
  return [...new Set(expressionTokens(expression).map(tokenRef).filter(r => r !== null))];
}


/**
 * Rinomina SIMULTANEA (Fase 5, T5+T6, seconda correzione; come
 * `rewrite_expression_refs` del backend): `renames` {vecchio: nuovo} (oggetto
 * o Map). Ogni riferimento si legge UNA volta sull'espressione di partenza:
 * gli scambi (a->b, b->a) e le catene (a->b, b->c) non si ripassano.
 */
export function rewriteExpressionNames(expression, renames) {
  if (!expression || typeof expression !== 'string' || !renames) return expression;
  const map = new Map([...(renames instanceof Map ? renames : Object.entries(renames))]
    .filter(([oldName, newName]) => oldName && newName && oldName !== newName));
  if (map.size === 0) return expression;
  let out = '';
  let cursor = 0;
  let changed = false;
  for (const t of expressionTokens(expression)) {
    const ref = tokenRef(t);
    if (ref === null || !map.has(ref)) continue;
    const newName = map.get(ref);
    out += expression.slice(cursor, t.start) + (t.kind === 'backtick' ? `\`${newName}\`` : newName);
    cursor = t.end;
    changed = true;
  }
  return changed ? out + expression.slice(cursor) : expression;
}

/**
 * La colonna a cui `token` si riferisce, o null se non si risolve in modo
 * univoco: prima il nome attuale (una sola colonna), poi il nome di origine
 * (una sola colonna, e nessuna colonna che oggi si chiama cosi').
 */
export function resolveColumnToken(token, columns) {
  if (!token) return null;
  const byName = (columns || []).filter(c => c?.name === token);
  if (byName.length === 1) return byName[0];
  if (byName.length > 1) return null;
  const byOrigin = (columns || []).filter(c => c?.name && [c.source_name, c.display_name].some(n => n && n !== c.name && String(n) === token));
  return byOrigin.length === 1 ? byOrigin[0] : null;
}

const candidateTokens = (columns) => [...new Set((columns || [])
  .flatMap(c => [c?.name, c?.source_name, c?.display_name]).filter(Boolean).map(String))];

/** Riferimenti {id, name} dell'espressione: solo i nomi risolti in modo univoco. */
export function collectExpressionRefs(expression, columns) {
  if (!expression || typeof expression !== 'string') return [];
  const present = new Set(expressionColumnRefs(expression));
  const seen = new Set();
  const refs = [];
  for (const token of candidateTokens(columns)) {
    if (!present.has(token)) continue;
    const col = resolveColumnToken(token, columns);
    const id = columnKey(col);
    if (!id || seen.has(id)) continue;
    seen.add(id);
    refs.push({ id, name: col.name });
  }
  return refs;
}

/**
 * Espressione con i nomi attuali, secondo la regola unica. `columns`: le
 * colonne che il parametro vede; `refs`: i riferimenti salvati (anche vuoti).
 */
export function rewriteExpressionRefs(expression, refs, columns) {
  if (!expression || typeof expression !== 'string') return expression;
  const list = columns || [];
  const byId = new Map(list.map(c => [columnKey(c), c]).filter(([k]) => Boolean(k)));
  // Rinomina simultanea: si raccolgono i nomi, si riscrive una volta sola.
  const renames = new Map();
  const saved = (Array.isArray(refs) ? refs : []).filter(r => r?.id && byId.get(r.id)?.name);
  if (saved.length > 0) {
    // Colonne che i riferimenti dicono rinominate (nome salvato diverso dal
    // nome attuale): il loro nome attuale non era il loro nome nell'espressione.
    const renamedAway = new Set(saved.filter(r => r.name && r.name !== byId.get(r.id).name).map(r => r.id));
    for (const ref of saved) {
      const current = byId.get(ref.id);
      if (!ref.name || ref.name === current.name || renames.has(ref.name)) continue;
      // Il nome vecchio e' oggi di un'altra colonna (che non e' stata a sua
      // volta rinominata, come negli scambi): non univoco, non si tocca.
      if (list.some(c => c?.name === ref.name && columnKey(c) !== ref.id && !renamedAway.has(columnKey(c)))) continue;
      renames.set(ref.name, String(current.name));
    }
    return rewriteExpressionNames(expression, renames);
  }
  const present = new Set(expressionColumnRefs(expression));
  for (const token of candidateTokens(list)) {
    if (!present.has(token)) continue;
    const col = resolveColumnToken(token, list);
    if (col && col.name !== token) renames.set(token, String(col.name));
  }
  return rewriteExpressionNames(expression, renames);
}
