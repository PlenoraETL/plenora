/**
 * serverActions — UNICO punto da cui il canvas dell'editor arriva al server
 * (Fase 4, M7, salvataggio esplicito).
 *
 * Quello che gira (Scheduler, Eseguibili) e' l'ultima versione SALVATA,
 * quindi il canvas si salva solo quando l'utente lo chiede:
 *   - saveNow()        "💾 Salva" e Ctrl+S;
 *   - saveForRun()     "▶ Esegui" (il pulsante dice "Salva, controlla ed esegui");
 *   - saveBeforeServerRead(azione)  Valida, Anteprima, "Dati dopo…",
 *                      "Aggiorna": leggono la versione salvata; con modifiche
 *                      non salvate CHIEDONO "Salva e continua" (o lo dice gia'
 *                      il pulsante: { explicit: true }). Annulla = niente.
 * Nessun altro modulo chiama handleSaveQuiet (test di enumerazione).
 */
import useCanvasStore from '../store/useCanvasStore';
import { hasUnsavedChanges } from '../store/saveState';

const save = () => useCanvasStore.getState().handleSaveQuiet();

export const saveNow = () => save();

export const saveForRun = () => save();

/**
 * @param {string} action  es. "La validazione", "L'anteprima"
 * @param {{explicit?: boolean}} [opts] explicit: il pulsante premuto diceva gia' "Salva e …"
 * @returns {Promise<boolean>} false = l'utente ha annullato
 */
export async function saveBeforeServerRead(action, { explicit = false } = {}) {
  const st = useCanvasStore.getState();
  if (!hasUnsavedChanges(st)) return true;
  if (!explicit) {
    const ask = st.getEditorAction?.('confirmSaveBeforeRead');
    // Senza chi chiede (fuori dall'editor) non si salva di nascosto: si
    // legge la versione salvata.
    if (!ask) return true;
    if (!(await ask(action))) return false;
  }
  await save();
  return true;
}

/** Prima di un'anteprima (usePreview): stessa regola, funzione stabile. */
export const beforePreview = () => saveBeforeServerRead("L'anteprima");
