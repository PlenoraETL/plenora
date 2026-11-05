/**
 * cssUsage — quali classi CSS il codice può produrre (Fase 5 T7, codice CSS
 * morto; correzione dopo la conferma). Una classe è usata se:
 *  - il suo nome compare intero nel testo di un file JS/JSX (className,
 *    stringhe, oggetti `{ 'x--on': cond }`, classList, querySelector);
 *  - oppure è prodotta da una COMPOSIZIONE dinamica scritta nel codice.
 *    Composizioni riconosciute (tutte quelle che il codice scrive):
 *      template:      `pl__status--${x}`, `${prefix}-canvas__hex`,
 *                     `${c.field}--${f.width}`, `a-${b}-c`;
 *      concatenazione: `'lv__row--' + x`, `prefix + '-canvas'`.
 *    Ogni `${espressione}` / operando vale uno dei suoi VALORI NOTI:
 *      - nome (o percorso `c.field`): le stringhe letterali assegnate a quel
 *        nome nel codice (`prefix="estrai"`, `field: 'dsm__field'`,
 *        `width: 'wide'`, `classPrefix = 'x'`);
 *      - espressione composta (`cond ? 'a' : 'b'`): le sue stringhe e '';
 *      - nome senza valori noti: qualunque testo (jolly), ammesso solo dopo
 *        un prefisso letterale di almeno 4 caratteri (`pl__status--${x}`).
 */
import { cssClasses, sourceFiles, stripComments, lineOf } from './sourceScan.js';

const LIT = '[A-Za-z_-][\\w-]*';
const EXPR = '\\$\\{[^{}`]*\\}';
// Una sequenza attaccata di pezzi letterali ed espressioni.
const TEMPLATE_RE = new RegExp(`(?:${LIT})?(?:${EXPR}(?:${LIT})?)+`, 'g');
// Concatenazioni: 'pref-' + x  |  x + '-suf'.
const CONCAT_PREFIX_RE = /['"]([A-Za-z_][\w-]*(?:--|__|-))['"]\s*\+\s*([\w$.?]+)/g;
const CONCAT_SUFFIX_RE = /([\w$.?\])]+)\s*\+\s*['"]((?:--|__|-)[A-Za-z][\w-]*)['"]/g;

const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/** Valori letterali assegnati a ogni nome: Map(nome -> Set(valori)). */
function assignedLiterals(text) {
  const out = new Map();
  const add = (name, v) => { if (!out.has(name)) out.set(name, new Set()); out.get(name).add(v); };
  const res = [
    /\b([A-Za-z_$][\w$]*)\s*[:=]\s*['"]([\w-]*)['"]/g, // name: 'v' | name = 'v' | name="v" (JSX)
    /\b([A-Za-z_$][\w$]*)=\{\s*['"`]([\w-]*)['"`]\s*\}/g, // name={'v'}
  ];
  for (const re of res) for (const m of text.matchAll(re)) add(m[1], m[2]);
  return out;
}

/**
 * Valori noti di un'espressione: { values: Set | null, closed }.
 *  - nome o percorso (`prefix`, `c.field`): i letterali assegnati a quel nome;
 *  - scelta tra letterali (`a ? 'x' : ''`, `a && 'x'`): esattamente quelli
 *    (closed: nessun altro valore possibile); di un letterale conta il pezzo
 *    attaccato (`' x--on'` vale '': è un'altra classe, già nel testo);
 *  - altro (`(x || '').toLowerCase()`): i letterali che contiene, aperto.
 */
const STR = `(?:'[^']*'|"[^"]*"|\`[^\`$]*\`)`;
const CLOSED_RE = new RegExp(`^[^?]*\\?\\s*${STR}\\s*:\\s*${STR}$|^[^&|?]*&&\\s*${STR}$`);
function exprValues(expr, assigned) {
  const e = expr.trim();
  if (/^[\w$]+(?:\??\.[\w$]+)*$/.test(e)) {
    const name = e.split('.').pop().replace('?', '');
    return { values: assigned.get(name) || null, closed: false };
  }
  const lits = [...e.matchAll(new RegExp(STR, 'g'))].map(m => m[0].slice(1).match(/^[\w-]*/)[0]);
  const closed = CLOSED_RE.test(e);
  return { values: lits.length ? new Set([...lits, '']) : null, closed };
}

/** Nome a cui va la composizione (`className=`, `const canvasClassName =`, `htmlFor=`). */
function attrOf(clean, index) {
  const before = clean.slice(clean.lastIndexOf('\n', index - 1) + 1, index);
  const names = [...before.matchAll(/([A-Za-z_$][\w$]*)\s*=(?![=>])/g)];
  return names.length ? names[names.length - 1][1] : '';
}

/**
 * Composizioni dinamiche del codice: [{ file, line, attr, parts, re }].
 * parts: [{ lit } | { expr, values, closed }]; re: RegExp delle classi prodotte
 * (null se un'espressione in testa non ha valori noti); attr: il nome a cui
 * è assegnata (className, …ClassName, htmlFor, id, key...).
 */
export function classCompositions(files, assigned) {
  const out = [];
  const push = (file, clean, index, parts) => {
    if (!parts.some(p => p.lit != null)) return;
    const base = { file, line: lineOf(clean, index), attr: attrOf(clean, index), parts };
    // Dopo un prefisso letterale di almeno 4 caratteri (`pl__status--${x}`)
    // l'espressione vale qualunque testo (jolly): il valore viene spesso dal
    // server. Un'espressione in testa vale solo i suoi valori noti; se non
    // ne ha, la composizione non produce classi note (di solito è un id:
    // `${fid}-name`); se è una classe, la segnala il test t7Rules.
    const leadLit = parts[0].lit != null && parts[0].lit.length >= 4;
    const wild = (p, i) => leadLit && i > 0 && p.expr != null && !p.closed;
    if (parts.some((p, i) => p.expr != null && !p.values && !wild(p, i))) {
      out.push({ ...base, re: null });
      return;
    }
    const src = parts.map((p, i) => (p.lit != null ? esc(p.lit)
      : wild(p, i) ? '[\\w-]*' : `(?:${[...p.values].map(esc).join('|')})`)).join('');
    out.push({ ...base, re: new RegExp(`^${src}$`) });
  };
  for (const { file, code } of files) {
    const clean = stripComments(code);
    for (const m of clean.matchAll(TEMPLATE_RE)) {
      const parts = [];
      for (const piece of m[0].matchAll(/\$\{([^{}`]*)\}|([^$]+)/g)) {
        if (piece[1] != null) parts.push({ expr: piece[1], ...exprValues(piece[1], assigned) });
        else parts.push({ lit: piece[2] });
      }
      if (parts.some(p => p.expr != null)) push(file, clean, m.index, parts);
    }
    for (const m of clean.matchAll(CONCAT_PREFIX_RE)) {
      push(file, clean, m.index, [{ lit: m[1] }, { expr: m[2], ...exprValues(m[2], assigned) }]);
    }
    for (const m of clean.matchAll(CONCAT_SUFFIX_RE)) {
      push(file, clean, m.index, [{ expr: m[1], ...exprValues(m[1], assigned) }, { lit: m[2] }]);
    }
  }
  return out;
}

export function cssUsage(files = sourceFiles(/\.(jsx?|css|html)$/)) {
  const defined = cssClasses(files);
  const js = files.filter(f => /\.(jsx?|html)$/.test(f.file));
  const text = js.map(f => f.code).join('\n');
  const tokens = new Set(text.match(/[A-Za-z_][\w-]*/g) || []);
  const assigned = assignedLiterals(stripComments(text));
  const compositions = classCompositions(js, assigned);
  const isUsed = (name) => tokens.has(name) || compositions.some(c => c.re && c.re.test(name));
  const unused = [...defined.entries()].filter(([name]) => !isUsed(name));
  return { defined, unused, isUsed, compositions };
}
