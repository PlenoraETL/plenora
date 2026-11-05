/**
 * useLocalDraft — copia di sicurezza nel browser delle modifiche NON
 * salvate (Fase 4, M7; Blocco A, regola 8). Archivio, regola e TABELLA
 * DELLE TRANSIZIONI: utils/localDrafts.js. Questo hook applica la tabella
 * per una scheda; i test la percorrono riga per riga con due schede
 * (tests/components/DraftTabs.contract.test.jsx).
 *
 *  - la copia e' per utente, progetto e SCHEDA: questa scheda scrive e
 *    cancella solo la propria (e quella orfana che ha adottato);
 *  - all'apertura offre l'istantanea di TUTTE le copie di quell'utente sul
 *    progetto, con data, provenienza e "salvato dopo"; "Ripristina" carica
 *    esattamente il contenuto annunciato;
 *  - con l'offerta aperta il lavoro nuovo ha comunque la sua copia;
 *  - ascolta le altre schede (evento storage): salvataggio e eliminazione;
 *  - ogni scrittura ha un esito visibile (spazio pieno: avviso);
 *  - all'uscita (pagehide, smontaggio, 401/uscita dall'account) la copia in
 *    sospeso si scrive subito.
 * Il server non vede mai la copia finche' l'utente non salva.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import useCanvasStore from '../store/useCanvasStore';
import { hasUnsavedChanges, matchesSavedGraph } from '../store/saveState';
import { authBus } from '../utils/authBus';
import { redactRestHeaders } from '../utils/restSecrets';
import {
  announceSaved, currentDraftUser, currentTabId, draftOrigin, isProjectDeleted, listDrafts,
  parseDraftEvent, removeDraft, removeDraftIf, retireOwnDraft, serverMovedOn, touchTab, writeDraft,
} from '../utils/localDrafts';
import { formatTime } from '../utils/format';

export const DRAFT_DELAY_MS = 1500;

// Solo il contenuto: selezione e misure non contano. La copia locale non
// porta i VALORI segreti REST: la riga resta con valore vuoto e
// `redacted: true`, cosi' il nodo ripristinato resta "da migrare" invece di
// perdere la riga in silenzio (utils/restSecrets, R10.8).
const withoutSecrets = (n) => (Array.isArray(n.data?.rest_headers)
  ? { ...n, data: { ...n.data, rest_headers: redactRestHeaders(n.data.rest_headers) } }
  : n);
const clean = (nodes, edges) => ({
  // eslint-disable-next-line no-unused-vars
  nodes: (nodes || []).map(({ selected, dragging, measured, ...n }) => withoutSecrets(n)),
  // eslint-disable-next-line no-unused-vars
  edges: (edges || []).map(({ selected, ...e }) => e),
});

export const DRAFT_ORIGIN_LABEL = {
  this: 'Questa scheda, prima di ricaricare',
  open: "Un'altra scheda aperta",
  closed: 'Una scheda chiusa',
};

const hhmm = (at) => formatTime(at);

export default function useLocalDraft(projectId, notReady, {
  serverUpdatedAt = null,
  tabId: tabIdOption = null,
  store = useCanvasStore,
  confirm = null,
} = {}) {
  const tabId = useMemo(() => tabIdOption || currentTabId(), [tabIdOption]);
  // Istantanea delle copie offerte: [{ id, savedAt, origin, stale, nodes, edges }].
  const [offer, setOffer] = useState(null);
  const offerRef = useRef(null);
  offerRef.current = offer;
  const [savedElsewhereAt, setSavedElsewhereAt] = useState(null);
  const [deleted, setDeleted] = useState(false);
  const deletedRef = useRef(false);
  const [writeProblem, setWriteProblem] = useState(null);
  // Versione del server da cui parte il lavoro di questa scheda.
  const baseRef = useRef(serverUpdatedAt);
  useEffect(() => { baseRef.current = serverUpdatedAt; }, [serverUpdatedAt]);
  // Copia orfana ripristinata (adottata): { id, savedAt }.
  const adoptedRef = useRef(null);
  const flushRef = useRef(() => {});
  const confirmRef = useRef(confirm);
  confirmRef.current = confirm;
  const checkedRef = useRef(null);

  // Questa scheda e' viva (l'avvio dell'app la tiene viva; qui per sicurezza).
  useEffect(() => { touchTab(tabId); }, [tabId]);

  // Apertura: la copia propria diventa orfana, poi istantanea di tutte.
  useEffect(() => {
    if (notReady || !projectId || checkedRef.current === projectId) return;
    checkedRef.current = projectId;
    if (isProjectDeleted(projectId)) { deletedRef.current = true; setDeleted(true); return; }
    const user = currentDraftUser();
    if (!user) return;
    retireOwnDraft(user, projectId, tabId);
    const copies = listDrafts(user, projectId).map(d => ({
      id: d.tabId,
      savedAt: d.savedAt,
      origin: draftOrigin(d.tabId, tabId),
      stale: serverMovedOn(d.baseUpdatedAt, serverUpdatedAt),
      nodes: d.nodes,
      edges: d.edges || [],
    }));
    if (copies.length) setOffer(copies);
  }, [projectId, notReady, serverUpdatedAt, tabId]);

  // Copia delle modifiche, salvataggio, uscita.
  useEffect(() => {
    if (!projectId || typeof store.subscribe !== 'function') return undefined;
    let timer = null;
    let alive = true;
    const write = () => {
      timer = null;
      const st = store.getState();
      if (deletedRef.current || !hasUnsavedChanges(st) || (st.projectId && st.projectId !== projectId)) return;
      const res = writeDraft(currentDraftUser(), projectId, tabId, {
        savedAt: Date.now(), baseUpdatedAt: baseRef.current || null, ...clean(st.nodes, st.edges),
      });
      if (alive) setWriteProblem(res.ok || res.reason === 'nouser' || res.reason === 'deleted' ? null : res.reason);
    };
    const flush = () => { if (timer) { clearTimeout(timer); write(); } };
    flushRef.current = flush;
    const unsub = store.subscribe((s, prev) => {
      if (s.projectId && s.projectId !== projectId) return;
      if (s.lastSavedAt && s.lastSavedAt !== prev.lastSavedAt) {
        clearTimeout(timer);
        timer = null;
        const user = currentDraftUser();
        removeDraft(user, projectId, tabId);
        if (adoptedRef.current) {
          removeDraftIf(user, projectId, adoptedRef.current.id, adoptedRef.current.savedAt);
          adoptedRef.current = null;
        }
        baseRef.current = s.lastSavedServerAt || null;
        announceSaved(projectId, { tabId, updatedAt: s.lastSavedServerAt || null });
        setWriteProblem(null);
        // Offerta ancora aperta: le copie restano (decide l'utente), ma ora
        // sono piu' vecchie di quanto appena salvato.
        setOffer(o => (o ? o.map(c => ({ ...c, stale: true })) : o));
        return;
      }
      // Fase 5 T7: tornati allo stato salvato (es. Ctrl+Z) la copia di QUESTA
      // scheda non ha piu' nulla da conservare; quella adottata resta (h.8).
      const graphChanged = s.nodes !== prev.nodes || s.edges !== prev.edges;
      if (graphChanged && hasUnsavedChanges(prev) && matchesSavedGraph(s)) {
        clearTimeout(timer);
        timer = null;
        removeDraft(currentDraftUser(), projectId, tabId);
        return;
      }
      if (!hasUnsavedChanges(s) || (s.nodes === prev.nodes && s.edges === prev.edges && hasUnsavedChanges(prev))) return;
      clearTimeout(timer);
      timer = setTimeout(write, DRAFT_DELAY_MS);
    });
    // Uscita: la copia in sospeso si scrive subito.
    const onLeave = () => flush();
    window.addEventListener('pagehide', onLeave);
    window.addEventListener('beforeunload', onLeave);
    const offLogout = authBus.addBeforeLogoutListener(flush);
    return () => {
      flush();
      alive = false;
      unsub();
      offLogout();
      window.removeEventListener('pagehide', onLeave);
      window.removeEventListener('beforeunload', onLeave);
      flushRef.current = () => {};
    };
  }, [projectId, tabId, store]);

  // Le altre schede: salvataggio e eliminazione dello stesso progetto.
  useEffect(() => {
    if (!projectId) return undefined;
    const onStorage = (e) => {
      const ev = parseDraftEvent(e);
      if (!ev || ev.projectId !== String(projectId)) return;
      if (ev.type === 'saved' && ev.tabId !== tabId) {
        setSavedElsewhereAt(ev.at || Date.now());
        setOffer(o => (o ? o.map(c => ({ ...c, stale: true })) : o));
      } else if (ev.type === 'deleted') {
        deletedRef.current = true;
        setDeleted(true);
        setOffer(null);
      }
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, [projectId, tabId]);

  const dropFromOffer = (id) => setOffer(o => {
    const rest = (o || []).filter(c => c.id !== id);
    return rest.length ? rest : null;
  });

  const restore = useCallback(async (id) => {
    const copy = (offerRef.current || []).find(c => c.id === id) || (id == null ? offerRef.current?.[0] : null);
    if (!copy) return false;
    const st = store.getState();
    // Modifiche fatte da quando la scheda e' aperta: si chiede; restano
    // comunque nella cronologia (loadUnsavedGraph).
    if (hasUnsavedChanges(st) && confirmRef.current) {
      const ok = await confirmRef.current({
        title: 'Ripristinare la copia?',
        message: 'Le modifiche fatte da quando hai aperto il progetto vengono sostituite dalla copia. Puoi tornarci con Annulla.',
        confirmText: 'Ripristina',
        cancelText: 'Non ripristinare',
      });
      if (!ok) return false;
    }
    setOffer(null);
    // Copia di una scheda chiusa (o di questa, prima di ricaricare): adottata,
    // resta finche' non si salva o si scarta. Di una scheda aperta: mai toccata.
    adoptedRef.current = copy.origin === 'open' ? null : { id: copy.id, savedAt: copy.savedAt };
    // Le colonne si ricalcolano da sole dal canvas ripristinato (hooks/useColumns).
    st.loadUnsavedGraph(copy.nodes, copy.edges || []);
    return true;
  }, [store]);

  const discard = useCallback((id) => {
    const copy = (offerRef.current || []).find(c => c.id === id);
    if (!copy) return;
    if (copy.origin !== 'open') removeDraftIf(currentDraftUser(), projectId, copy.id, copy.savedAt);
    dropFromOffer(id);
  }, [projectId]);

  const notices = [];
  if (deleted) notices.push({ id: 'deleted', text: "Il progetto è stato eliminato in un'altra scheda: le modifiche non vengono più copiate in questo browser." });
  else {
    if (savedElsewhereAt) notices.push({ id: 'savedElsewhere', text: `Il progetto è stato salvato in un'altra scheda alle ${hhmm(savedElsewhereAt)}. Le modifiche di questa scheda restano (anche nella copia di sicurezza): salvando sostituiresti quella versione.` });
    if (writeProblem === 'full') notices.push({ id: 'full', text: 'Spazio del browser pieno: la copia di sicurezza delle modifiche non è aggiornata. Salva appena puoi.' });
    else if (writeProblem) notices.push({ id: 'unavailable', text: 'Il browser non permette la copia di sicurezza delle modifiche. Salva appena puoi.' });
  }
  const dismissNotice = useCallback((id) => {
    if (id === 'savedElsewhere') setSavedElsewhereAt(null);
    if (id === 'full' || id === 'unavailable') setWriteProblem(null);
  }, []);

  return { offer, restore, discard, notices, dismissNotice, flush: () => flushRef.current() };
}
