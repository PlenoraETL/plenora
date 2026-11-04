/**
 * expressionRefs — riferimenti a colonne dentro un'espressione (Formula,
 * parametri `expression` del registro) e loro riscrittura quando una
 * colonna cambia nome a monte.
 *
 * Regola unica: un nome nell'espressione si riscrive SOLO se si risolve in
 * modo univoco a una colonna, e la riscrittura segue l'id.
 *  - Con i riferimenti salvati (`<param>_column_refs` = [{id, name}]): per
 *    id. Il nome vecchio si sostituisce col nome attuale della stessa
 *    colonna, ma non se il nome vecchio e' oggi il nome di UN'ALTRA colonna
 *    (sarebbe ambiguo: si lascia com'e').
 *  - Senza riferimenti (progetti vecchi): un nome che e' il nome ATTUALE di
 *    una colonna non si tocca mai; un nome che non esiste piu' si riscrive
 *    solo se esattamente una colonna lo dichiara come nome di origine
 *    (source_name / display_name). Esempio: a valle di un Join con nomi
 *    doppi `name` resta `name` (la colonna di sinistra), non diventa
 *    `name_R`.
 */
const columnKey = (col) => col?.id || col?.column_id || null;
const isIdentifier = (token) => /^[A-Za-z_][A-Za-z0-9_]*$/.test(token);
const escapeRegExp = (value) => String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

export function expressionHasToken(expression, token) {
  if (!expression || !token || typeof expression !== 'string') return false;
  if (expression.includes(`\`${token}\``)) return true;
  if (expression.includes(`['${token}']`)) return true;
  if (expression.includes(`["${token}"]`)) return true;
  if (isIdentifier(token)) return new RegExp(`\\b${escapeRegExp(token)}\\b`).test(expression);
  return expression.includes(token);
}

export function rewriteExpressionToken(expression, oldName, newName) {
  if (!expression || !oldName || !newName || oldName === newName) return expression;
  let rewritten = expression
    .replaceAll(`\`${oldName}\``, `\`${newName}\``)
    .replaceAll(`['${oldName}']`, `['${newName}']`)
    .replaceAll(`["${oldName}"]`, `["${newName}"]`);
  if (isIdentifier(oldName)) {
    rewritten = rewritten.replace(new RegExp(`\\b${escapeRegExp(oldName)}\\b`, 'g'), newName);
  }
  return rewritten;
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
  const seen = new Set();
  const refs = [];
  for (const token of candidateTokens(columns)) {
    if (!expressionHasToken(expression, token)) continue;
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
  let next = expression;
  const saved = (Array.isArray(refs) ? refs : []).filter(r => r?.id && byId.get(r.id)?.name);
  if (saved.length > 0) {
    for (const ref of saved) {
      const current = byId.get(ref.id);
      if (!ref.name || ref.name === current.name) continue;
      // Il nome vecchio e' oggi di un'altra colonna: non univoco, non si tocca.
      if (list.some(c => c?.name === ref.name && columnKey(c) !== ref.id)) continue;
      next = rewriteExpressionToken(next, ref.name, current.name);
    }
    return next;
  }
  for (const token of candidateTokens(list)) {
    if (!expressionHasToken(expression, token)) continue;
    const col = resolveColumnToken(token, list);
    if (col && col.name !== token) next = rewriteExpressionToken(next, token, String(col.name));
  }
  return next;
}
