/**
 * readOnlySafe — le AZIONI DI LETTURA del pannello del passo (Fase 5, T5+T6,
 * correzione dopo la conferma della sola lettura).
 *
 * REGOLA: in sola lettura si spengono SOLO i controlli che modificano il
 * canvas; le azioni di lettura restano attive. Un'azione di lettura non
 * cambia il canvas salvato: apre la cronologia, valida, mostra o ricarica
 * un'anteprima, esegue (con i permessi suoi), riprova a caricare un elenco,
 * cerca in una lista, sceglie un'opzione dell'esecuzione (non del canvas).
 *
 * Seconda correzione (Fase 5, T5+T6): un controllo che cambia SOLO lo stato
 * di VISTA locale del pannello (ricerca, filtro della lista, espandi/
 * comprimi, «Mostra tutte»/«Nascondi», ordinamento della vista, schede) e'
 * un'azione di lettura: si dichiara anche lui con READ_ONLY_SAFE. I modali
 * aperti dal pannello (ModalWrapper, createPortal) stanno fuori dal form e
 * fuori da questo blocco; si aprono solo da azioni di lettura o da controlli
 * di modifica (spenti).
 *
 * Si dichiara in UN punto, sul controllo: `{...READ_ONLY_SAFE}`. Il pannello
 * (NodeInspector, ReadOnlyForm) spegne ogni altro input/select/textarea/
 * button del form con `useReadOnlyLock`, anche quelli che i form montano
 * dopo (MutationObserver): chi aggiunge un controllo nuovo lo trova spento
 * in sola lettura finche' non lo dichiara azione di lettura.
 * Enumerazione: tests/components/InspectorShell.contract.test.jsx (per ogni
 * tipo di nodo dei tre editor: controlli di modifica spenti, azioni di
 * lettura attive, confronto con quelle del viewer a 8461197c).
 */
import { useLayoutEffect } from 'react';

const READ_ONLY_SAFE_ATTR = 'data-readonly-safe';
/** Da spargere sul controllo: `<button {...READ_ONLY_SAFE}>`. */
export const READ_ONLY_SAFE = Object.freeze({ [READ_ONLY_SAFE_ATTR]: '' });
const SAFE_SELECTOR = `[${READ_ONLY_SAFE_ATTR}]`;
const FORM_CONTROLS = 'input, select, textarea, button';

/** true se il controllo e' un'azione di lettura (lui o un contenitore dichiarato). */
export const isReadOnlySafe = (el) => Boolean(el?.closest?.(SAFE_SELECTOR));

/**
 * Spegne in `ref.current` ogni controllo che non sia un'azione di lettura,
 * finche' `readOnly` e' vero. Riaccende solo quelli che ha spento lui.
 *
 * Nessun riferimento trattenuto (Fase 5, T5+T6, seconda correzione): chi e'
 * stato spento lo dice un marcatore sull'elemento (`data-readonly-locked`),
 * non un Set di elementi. Un controllo smontato mentre il pannello e' in
 * sola lettura esce dal DOM e da qui: nessun elemento staccato resta vivo.
 */
const LOCKED_ATTR = 'data-readonly-locked';

export function useReadOnlyLock(ref, readOnly) {
  useLayoutEffect(() => {
    const root = ref.current;
    if (!root || !readOnly) return undefined;
    const lock = () => {
      for (const el of root.querySelectorAll(FORM_CONTROLS)) {
        if (isReadOnlySafe(el) || el.disabled) continue;
        el.disabled = true;
        el.setAttribute(LOCKED_ATTR, '');
      }
    };
    lock();
    const observer = typeof MutationObserver === 'function' ? new MutationObserver(lock) : null;
    observer?.observe(root, { subtree: true, childList: true, attributes: true, attributeFilter: ['disabled'] });
    return () => {
      observer?.disconnect();
      for (const el of root.querySelectorAll(`[${LOCKED_ATTR}]`)) {
        el.disabled = false;
        el.removeAttribute(LOCKED_ATTR);
      }
    };
  }, [ref, readOnly]);
}
