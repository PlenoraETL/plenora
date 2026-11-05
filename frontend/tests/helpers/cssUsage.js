/**
 * cssUsage — quali classi CSS sono usate dal codice (Fase 5 T7, codice CSS
 * morto). Una classe è usata se:
 *  - il suo nome compare intero nel testo di un file JS/JSX (className,
 *    stringhe, template, classList, querySelector);
 *  - oppure comincia con un prefisso dinamico scritto nel codice
 *    (`pl__status--${x}`, `'lv__row--' + x`): il prefisso finisce con
 *    `--`, `__` o `-` subito prima di `${` o `' +`;
 *  - oppure è generata da ThemeToggle (`${classPrefix}__theme-*`) per un
 *    `classPrefix="..."` scritto nel codice.
 */
import { cssClasses, sourceFiles } from './sourceScan.js';

export function cssUsage() {
  const files = sourceFiles(/\.(jsx?|css|html)$/);
  const defined = cssClasses(files);
  const js = files.filter(f => /\.(jsx?|html)$/.test(f.file));
  const text = js.map(f => f.code).join('\n');
  const tokens = new Set(text.match(/[A-Za-z_][\w-]*/g) || []);
  const prefixes = new Set();
  for (const m of text.matchAll(/([A-Za-z_][\w-]*(?:--|__|-))\$\{/g)) prefixes.add(m[1]);
  for (const m of text.matchAll(/['"]([A-Za-z_][\w-]*(?:--|__|-))['"]\s*\+/g)) prefixes.add(m[1]);
  for (const m of text.matchAll(/classPrefix=["']([\w-]+)["']/g)) {
    for (const s of ['__theme-toggle', '__theme-opt', '__theme-opt--on']) tokens.add(m[1] + s);
  }
  const isUsed = (name) => tokens.has(name) || [...prefixes].some(p => p.length >= 4 && name.startsWith(p));
  const unused = [...defined.entries()].filter(([name]) => !isUsed(name));
  return { defined, unused, isUsed };
}
