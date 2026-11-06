/**
 * Regole dei selettori sul grafo del canvas (UI-2, 06/11/2026; V4, V5, V6).
 * Un selettore di `useCanvasStore`/`useStore` gira a OGNI aggiornamento dello
 * store, cioe' a ogni fotogramma di un trascinamento. Quindi:
 *  - niente iscrizione a `s.nodes` / `s.edges` interi (si ridisegna a ogni
 *    fotogramma): solo FlowCanvas, che disegna React Flow;
 *  - niente selettori che scorrono creando liste o serializzano il grafo
 *    (map, filter, flatMap, sort, join, JSON.stringify, firme): quei calcoli
 *    passano da hooks/useCanvasDerived, che li rifa' solo quando sale il
 *    contatore giusto (store/graphRevisions).
 * Le ricerche puntuali (find, some, un ciclo senza allocazioni) restano
 * ammesse.
 */
export const FORBIDDEN = [
  [/JSON\.stringify\(/, 'serializza il grafo'],
  [/\.join\(/, 'costruisce una stringa dal grafo'],
  [/\.(?:map|filter|flatMap|sort|reduce)\(/, 'crea una lista dal grafo'],
  [/\b(?:structureKey|canvasContentKey|canvasSignatures|columnSignature|serializeCanvas|upstreamSourceNodes)\(/, 'calcola una firma o una visita del grafo'],
];

const WHOLE = /^(?:useShallow\()?\(?\s*([A-Za-z_$][\w$]*)\s*\)?\s*=>\s*\(?\s*\1\.(nodes|edges)\s*(?:\|\|\s*\w+)?\s*\)?\)?$/;

// Eccezioni dichiarate, con il motivo.
export const ALLOWED = [
  { file: 'components/editor/EditorShell.jsx', whole: 'nodes', reason: 'FlowCanvas: l\'unico iscritto al grafo, disegna React Flow' },
  { file: 'components/editor/EditorShell.jsx', whole: 'edges', reason: 'FlowCanvas: l\'unico iscritto al grafo, disegna React Flow' },
];

export function violationsOf(selectors) {
  const out = [];
  for (const sel of selectors) {
    if (!/\bnodes\b|\bedges\b/.test(sel.text)) continue;
    const whole = sel.arg.replace(/\s+/g, ' ').match(WHOLE);
    if (whole) {
      if (!ALLOWED.some(a => a.file === sel.file && a.whole === whole[2])) {
        out.push(`${sel.file}:${sel.line} iscritto a s.${whole[2]} intero: ${sel.arg}`);
      }
      continue;
    }
    for (const [re, why] of FORBIDDEN) {
      if (re.test(sel.text)) { out.push(`${sel.file}:${sel.line} ${why}: ${sel.arg.slice(0, 120)}`); break; }
    }
  }
  return out;
}
