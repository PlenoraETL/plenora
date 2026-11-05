/**
 * UNICA definizione di "modifiche non salvate" (Fase 4, M7; Fase 5 T7).
 *
 * Regola: NON SALVATO = il canvas è DIVERSO dall'ultimo salvato. `isDirty`
 * (una modifica dell'utente dopo l'ultimo salvataggio o caricamento) dice
 * solo che può esserlo; il confronto della firma del canvas (quello che si
 * salva: dati, collegamenti e posizioni; mai selezione, trascinamento,
 * misure) con quella dell'ultimo salvataggio (`savedGraph`) lo decide.
 * Così Ctrl+Z fino allo stato salvato torna «salvato» (prima restava
 * «Modifiche non salvate»).
 *
 * Le correzioni automatiche (pendingSync) si salvano col prossimo
 * salvataggio ma non sono "modifiche": niente avviso. La usano indicatore,
 * avviso di uscita, copia di sicurezza, anteprime, versioni e il ciclo di
 * salvataggio dello store (che non risalva un canvas identico).
 */
import { savedCanvasSignature } from '../utils/canvasContentKey';

// Firma per coppia (nodes, edges): calcolata una volta per stato, i
// selettori la rileggono senza costo.
const cache = new WeakMap();
function signatureOf(nodes, edges) {
  if (!nodes || !edges) return savedCanvasSignature(nodes, edges);
  let byEdges = cache.get(nodes);
  if (!byEdges) { byEdges = new WeakMap(); cache.set(nodes, byEdges); }
  if (!byEdges.has(edges)) byEdges.set(edges, savedCanvasSignature(nodes, edges));
  return byEdges.get(edges);
}

/** Il canvas dello stato è uguale all'ultimo salvato? */
export function matchesSavedGraph(s) {
  if (!s.savedGraph) return false;
  if (s.nodes === s.savedGraph.nodes && s.edges === s.savedGraph.edges) return true;
  return signatureOf(s.nodes, s.edges) === signatureOf(s.savedGraph.nodes, s.savedGraph.edges);
}

export const hasUnsavedChanges = (s) => Boolean(s.isDirty) && !s.isReadOnly && !matchesSavedGraph(s);
