/**
 * displayIcon — un'icona sempre mostrabile come glifo.
 *
 * Alcune icone del registry delle trasformazioni sono nomi ('columns'),
 * non glifi: mostrate come testo sbordano dal riquadro. Il registry le
 * normalizza una volta, quando entra in cache (utils/transformRegistry):
 * ogni consumatore (palette, nodo compatto, pannello, TransformNode) riceve
 * gia' un glifo.
 */
const ICON_ALIASES = { columns: '▥' };

export function displayIcon(icon, fallback = '⚙️') {
  const s = typeof icon === 'string' ? icon.trim() : '';
  if (!s) return fallback;
  if ([...s].some(ch => ch.codePointAt(0) > 127)) return s;
  return ICON_ALIASES[s] || fallback;
}
