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
 * finche' `readOnly` e' vero. Allo sblocco ogni controllo spento da qui torna
 * allo stato che il form vuole.
 *
 * Nessun riferimento trattenuto (Fase 5, T5+T6, seconda correzione): chi e'
 * stato spento lo dice un marcatore sull'elemento (`data-readonly-locked`),
 * non un Set di elementi. Un controllo smontato mentre il pannello e' in
 * sola lettura esce dal DOM e da qui: nessun elemento staccato resta vivo.
 *
 * REGOLA (correzione finale prima del deploy): il marcatore porta lo stato
 * `disabled` che il controllo deve avere allo sblocco (`'0'` acceso, `'1'`
 * spento). Parte da quello che aveva prima del blocco; se durante il blocco
 * qualcun altro (React, il form) cambia `disabled` su un controllo bloccato,
 * l'osservatore lo vede e il marcatore prende quel valore. Le modifiche fatte
 * da qui si scartano (`takeRecords`) e non contano come volonta' del form.
 * Allo sblocco: `disabled = marcatore === '1'`.
 */
const LOCKED_ATTR = 'data-readonly-locked';
const WANT_ENABLED = '0';
const WANT_DISABLED = '1';

export function useReadOnlyLock(ref, readOnly) {
  useLayoutEffect(() => {
    const root = ref.current;
    if (!root || !readOnly) return undefined;
    const lock = () => {
      for (const el of root.querySelectorAll(FORM_CONTROLS)) {
        if (isReadOnlySafe(el) || el.disabled) continue;
        // Acceso adesso: allo sblocco torna acceso, salvo che il form lo cambi.
        el.setAttribute(LOCKED_ATTR, WANT_ENABLED);
        el.disabled = true;
      }
    };
    lock();
    let observer = null;
    const onMutations = (records) => {
      // Solo cambi fatti da altri: i nostri si scartano a fine giro.
      for (const record of records) {
        const el = record.target;
        if (record.attributeName !== 'disabled' || !el.hasAttribute?.(LOCKED_ATTR)) continue;
        // Valore voluto da chi l'ha cambiato (l'ultimo record vince: e' lo stato attuale).
        el.setAttribute(LOCKED_ATTR, el.disabled ? WANT_DISABLED : WANT_ENABLED);
        if (!el.disabled) el.disabled = true;
      }
      lock();
      observer?.takeRecords();
    };
    observer = typeof MutationObserver === 'function' ? new MutationObserver(onMutations) : null;
    observer?.observe(root, { subtree: true, childList: true, attributes: true, attributeFilter: ['disabled'] });
    return () => {
      if (observer) onMutations(observer.takeRecords());
      observer?.disconnect();
      for (const el of root.querySelectorAll(`[${LOCKED_ATTR}]`)) {
        el.disabled = el.getAttribute(LOCKED_ATTR) === WANT_DISABLED;
        el.removeAttribute(LOCKED_ATTR);
      }
    };
  }, [ref, readOnly]);
}
