/**
 * useLocalDraft — copia di sicurezza nel browser delle modifiche NON
 * salvate (Fase 4, M7, salvataggio esplicito). Archivio: utils/localDrafts.
 *
 *  - con modifiche non salvate (hasUnsavedChanges) il canvas si copia dopo
 *    un attimo di pausa; la copia e' dell'utente della sessione;
 *  - un salvataggio riuscito (lastSavedAt, scritto insieme allo stato
 *    "pulito") o "Scarta" la cancella;
 *  - all'apertura, se c'e' una copia, l'editor offre "Ripristina" o
 *    "Scarta". Finche' l'utente non sceglie, la copia resta COM'E' (le
 *    modifiche fatte nel frattempo non la sovrascrivono). Se il progetto e'
 *    stato salvato dopo la copia (da un collega o da un'altra scheda), lo
 *    dice (serverNewer).
 *  - "Ripristina" carica la copia com'era nello store (stessi dati, colonne
 *    comprese), resta "da salvare", "Annulla" torna alla versione del
 *    server; l'editor puo' ricalcolare (azione onCanvasRestored).
 * Il server non vede mai la copia finche' l'utente non salva: quello che
 * gira (Scheduler, Eseguibili) resta l'ultima versione salvata.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import useCanvasStore from '../store/useCanvasStore';
import { hasUnsavedChanges } from '../store/saveState';
import { clearDraft, currentDraftUser, readDraft, writeDraft } from '../utils/localDrafts';

const DELAY_MS = 1500;
// Solo il contenuto: selezione e misure non contano.
const clean = (nodes, edges) => ({
  // eslint-disable-next-line no-unused-vars
  nodes: (nodes || []).map(({ selected, dragging, measured, ...n }) => n),
  // eslint-disable-next-line no-unused-vars
  edges: (edges || []).map(({ selected, ...e }) => e),
});

export default function useLocalDraft(projectId, notReady, { serverUpdatedAt = null } = {}) {
  const [offer, setOffer] = useState(null);
  const offerRef = useRef(null);
  offerRef.current = offer;
  const checkedRef = useRef(null);

  // All'apertura: una copia esiste solo se si e' usciti con modifiche non
  // salvate (ogni salvataggio la cancella), quindi se c'e' si offre.
  useEffect(() => {
    if (notReady || !projectId || checkedRef.current === projectId) return;
    checkedRef.current = projectId;
    const draft = readDraft(currentDraftUser(), projectId);
    if (!draft) return;
    const serverAt = serverUpdatedAt ? Date.parse(serverUpdatedAt) : NaN;
    setOffer({ savedAt: draft.savedAt, serverNewer: Number.isFinite(serverAt) && serverAt > draft.savedAt });
  }, [projectId, notReady, serverUpdatedAt]);

  // Copia delle modifiche non salvate, dopo una pausa.
  useEffect(() => {
    if (!projectId || typeof useCanvasStore.subscribe !== 'function') return undefined;
    let timer = null;
    const unsub = useCanvasStore.subscribe((s, prev) => {
      if (s.projectId && s.projectId !== projectId) return;
      if (s.lastSavedAt && s.lastSavedAt !== prev.lastSavedAt) {
        clearTimeout(timer);
        // Offerta ancora aperta: la copia resta (decide l'utente), ma ora e'
        // piu' vecchia di quanto appena salvato e lo dice.
        if (offerRef.current) setOffer(o => (o ? { ...o, serverNewer: true } : o));
        else clearDraft(projectId);
        return;
      }
      if (!hasUnsavedChanges(s) || (s.nodes === prev.nodes && s.edges === prev.edges && hasUnsavedChanges(prev))) return;
      clearTimeout(timer);
      timer = setTimeout(() => {
        const st = useCanvasStore.getState();
        // Offerta in attesa: la copia vecchia non si tocca.
        if (offerRef.current || !hasUnsavedChanges(st) || (st.projectId && st.projectId !== projectId)) return;
        writeDraft(currentDraftUser(), projectId, { savedAt: Date.now(), ...clean(st.nodes, st.edges) });
      }, DELAY_MS);
    });
    return () => { clearTimeout(timer); unsub(); };
  }, [projectId]);

  const restore = useCallback(() => {
    const draft = readDraft(currentDraftUser(), projectId);
    setOffer(null);
    if (!draft) return;
    const st = useCanvasStore.getState();
    st.loadUnsavedGraph(draft.nodes, draft.edges || []);
    st.getEditorAction?.('onCanvasRestored')?.();
  }, [projectId]);

  const discard = useCallback(() => { clearDraft(projectId); setOffer(null); }, [projectId]);

  return { offer, restore, discard };
}
