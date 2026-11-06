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
import { canvasSignatures, samePositions } from '../utils/canvasContentKey';

/**
 * Il canvas dello stato è uguale all'ultimo salvato? Stessa firma di ciò che
 * si salva (utils/canvasContentKey: memorizzata, mai ricalcolata per un
 * cambio di sola posizione) e stesse posizioni, confrontate nodo per nodo:
 * a ogni fotogramma di un trascinamento costa un confronto per nodo, non una
 * serializzazione (UI-2, V3).
 */
export function matchesSavedGraph(s) {
  if (!s.savedGraph) return false;
  if (s.nodes === s.savedGraph.nodes && s.edges === s.savedGraph.edges) return true;
  if (!samePositions(s.nodes, s.savedGraph.nodes)) return false;
  return canvasSignatures(s.nodes, s.edges).saved === canvasSignatures(s.savedGraph.nodes, s.savedGraph.edges).saved;
}

export const hasUnsavedChanges = (s) => Boolean(s.isDirty) && !s.isReadOnly && !matchesSavedGraph(s);
