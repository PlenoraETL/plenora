import React from 'react';
import fs from 'node:fs';
import path from 'node:path';
import { render, act } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('../../src/api/projects', async (orig) => ({
  ...(await orig()),
  updateCanvas: vi.fn(async () => ({ status: 'ok' })),
  // Server che non versiona: PUT e GET senza canvas_version (utils/canvasVersion.tokenAfterWrite).
  getProject: vi.fn(async () => ({ metadata: {}, canvas_version: null })),
}));

import { createCanvasStore, hasUnsavedChanges } from '../../src/store/useCanvasStore';
import { updateCanvas } from '../../src/api/projects';
import useLocalDraft, { DRAFT_DELAY_MS } from '../../src/hooks/useLocalDraft';
import { AuthProvider } from '../../src/context/AuthContext';
import { authBus } from '../../src/utils/authBus';
import {
  MAX_AGE_MS, ROLL_CALL_MS, currentDraftUser, draftKey, listDrafts, markProjectDeleted, pruneDrafts,
  currentTabId, keepTabCopy, readDraft, serverCanvasReplaced, touchTab, writeDraft,
} from '../../src/utils/localDrafts';
import { resetSession } from '../../src/utils/session';

/**
 * Contract Blocco A, regola 8 (raccolta h.1-h.16): la copia di sicurezza
 * e' per utente, progetto e scheda. Ogni riga della TABELLA DELLE
 * TRANSIZIONI di utils/localDrafts.js ha qui un test con DUE schede: due
 * istanze del hook (id di scheda diversi), due store, lo stesso localStorage.
 * Le scritture nel localStorage arrivano alle "altre schede" come evento
 * storage, come nel browser.
 */

// Forma reale dell'utente salvata dal login (AuthContext.login).
const LOGIN_USER = { user_id: 7, username: 'mario', group_id: 3, role: 'user' };
const U = '7';
const P = 'p1';
const SERVER_AT = '2026-11-04T09:00:00Z';

const n = (id, type = 'transform', data = {}) => ({ id, type, position: { x: 0, y: 0 }, data });
const SERVER_GRAPH = () => [n('s', 'project_source'), n('o', 'result')];
const label = (nodesOrDraft) => {
  const nodes = Array.isArray(nodesOrDraft) ? nodesOrDraft : nodesOrDraft?.nodes;
  return nodes?.find(x => x.id === 's')?.data?.label;
};
const copyOf = (tab) => readDraft(U, P, tab);
const closedCopy = (tab, lbl, extra = {}) => writeDraft(U, P, tab, {
  savedAt: Date.now() - 60000, baseUpdatedAt: SERVER_AT, nodes: [n('s', 'project_source', { label: lbl }), n('o', 'result')], edges: [], ...extra,
});

// Il localStorage avvisa le altre schede (qui: tutti gli ascoltatori della
// finestra; ogni scheda ignora i propri annunci). quotaFull simula lo spazio pieno.
let quotaFull = false;
const realSet = Storage.prototype.setItem;
const realRemove = Storage.prototype.removeItem;

function Tab({ tabId, store, serverUpdatedAt = SERVER_AT, confirm = null, apiRef }) {
  const api = useLocalDraft(P, false, { tabId, store, serverUpdatedAt, confirm });
  apiRef.current = api;
  return (
    <div>
      {(api.offer || []).map(c => (
        <p key={c.id} data-copy={c.id}>{`${c.origin}|${label(c.nodes)}|${String(c.stale)}`}</p>
      ))}
      {api.notices.map(x => <p key={x.id} data-notice={x.id}>{x.text}</p>)}
    </div>
  );
}

const mounted = [];
function openTab(tabId, { serverUpdatedAt, confirm, wrap } = {}) {
  const store = createCanvasStore();
  store.getState().setReadOnly(false);
  store.getState().init(P, 'manipola');
  store.getState().loadGraph(SERVER_GRAPH(), []);
  const apiRef = { current: null };
  const el = <Tab tabId={tabId} store={store} serverUpdatedAt={serverUpdatedAt} confirm={confirm} apiRef={apiRef} />;
  const r = render(wrap ? wrap(el) : el);
  const tab = {
    tabId, store, apiRef, unmount: r.unmount,
    offer: () => [...r.container.querySelectorAll('[data-copy]')].map(e => `${e.dataset.copy}=${e.textContent}`),
    notice: (id) => r.container.querySelector(`[data-notice="${id}"]`),
    edit: (lbl) => act(() => store.getState().updateNodeData('s', { label: lbl })),
    pause: () => act(() => { vi.advanceTimersByTime(DRAFT_DELAY_MS + 100); }),
    save: () => act(async () => { await store.getState().handleSaveQuiet(); }),
  };
  mounted.push(tab);
  return tab;
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-11-04T10:00:00Z'));
  localStorage.clear();
  sessionStorage.clear();
  sessionStorage.setItem('urby_user', JSON.stringify(LOGIN_USER));
  quotaFull = false;
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(function setItem(k, v) {
    if (this === window.localStorage && quotaFull && k.startsWith('plenora:draft:')) throw new Error('QuotaExceededError');
    realSet.call(this, k, v);
    if (this === window.localStorage) window.dispatchEvent(new StorageEvent('storage', { key: k, newValue: String(v) }));
  });
  vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(function removeItem(k) {
    realRemove.call(this, k);
    if (this === window.localStorage) window.dispatchEvent(new StorageEvent('storage', { key: k, newValue: null }));
  });
  updateCanvas.mockReset();
  updateCanvas.mockImplementation(async () => ({ status: 'ok' }));
});
afterEach(() => {
  mounted.splice(0).forEach(t => { try { t.unmount(); } catch { /* gia' smontata */ } });
  vi.restoreAllMocks();
  vi.useRealTimers();
});

// Una voce per ogni riga della tabella in utils/localDrafts.js.
const DRAFT_TRANSITIONS = {
  'apertura': async () => {
    // A lavora (scheda aperta), C e' una scheda chiusa, D ha una copia senza base.
    const NOW_AT = '2026-11-04T09:30:00Z';
    const A = openTab('A', { serverUpdatedAt: NOW_AT });
    A.edit('di-A'); A.pause();
    closedCopy('C', 'di-C');
    writeDraft(U, P, 'D', { savedAt: Date.now() - 1000, nodes: [n('s', 'project_source', { label: 'di-D' })], edges: [] });
    // h.15: la base di C e' superata dal server (due orari del server).
    const B = openTab('B', { serverUpdatedAt: NOW_AT });
    expect(B.offer().sort()).toEqual(['A=open|di-A|false', 'C=closed|di-C|true', 'D=closed|di-D|null'].sort());
    // Ricaricamento della stessa scheda: la sua copia e' offerta come "questa
    // scheda" ed e' spostata, il posto della scheda resta libero.
    A.unmount();
    const A2 = openTab('A');
    expect(A2.offer().find(x => x.startsWith('A~'))).toMatch(/=this\|di-A\|/);
    expect(copyOf('A')).toBe(null);
  },

  'modifica': async () => {
    closedCopy('C', 'di-C');
    const B = openTab('B');
    expect(B.offer()).toEqual(['C=closed|di-C|false']);
    // h.6: con l'offerta aperta il lavoro nuovo ha una copia separata.
    B.edit('nuovo-B');
    expect(copyOf('B')).toBe(null); // in sospeso
    B.pause();
    expect(label(copyOf('B'))).toBe('nuovo-B');
    expect(label(copyOf('C'))).toBe('di-C');
    expect(B.offer()).toEqual(['C=closed|di-C|false']);
  },

  'pausa': async () => {
    const A = openTab('A');
    const B = openTab('B');
    A.edit('a1'); A.pause();
    B.edit('b1'); B.pause();
    // h.1 e h.9: una chiave per utente (user_id del login), progetto e scheda.
    expect(localStorage.getItem(draftKey(U, P, 'A'))).toBeTruthy();
    expect(localStorage.getItem(draftKey('mario', P, 'A'))).toBe(null);
    expect(label(copyOf('A'))).toBe('a1');
    expect(label(copyOf('B'))).toBe('b1');
    expect(copyOf('A').baseUpdatedAt).toBe(SERVER_AT);
    expect(A.notice('full')).toBe(null);
    // Solo JSON: una funzione non entra nella copia.
    writeDraft(U, P, 'Z', { savedAt: Date.now(), nodes: [n('s', 'project_source', { onPick: () => 1 })], edges: [] });
    expect(copyOf('Z').nodes[0].data).toEqual({});
  },

  'spazio pieno': async () => {
    const A = openTab('A');
    const B = openTab('B');
    A.edit('a1'); A.pause();
    quotaFull = true;
    A.edit('a2'); A.pause();
    // h.11: esito visibile, la copia precedente resta con la sua data.
    expect(A.notice('full')).toBeTruthy();
    expect(label(copyOf('A'))).toBe('a1');
    expect(B.notice('full')).toBe(null);
    quotaFull = false;
    A.edit('a3'); A.pause();
    expect(A.notice('full')).toBe(null);
  },

  'salvataggio riuscito': async () => {
    closedCopy('C', 'di-C');
    const A = openTab('A');
    const B = openTab('B');
    // A adotta la copia della scheda chiusa e poi salva.
    await act(async () => { await A.apiRef.current.restore('C'); });
    A.pause();
    B.edit('b1'); B.pause();
    updateCanvas.mockImplementationOnce(async () => ({ status: 'ok', updated_at: '2026-11-04T10:05:00Z' }));
    await A.save();
    // h.3: A cancella la sua e quella adottata; la copia di B resta.
    expect(copyOf('A')).toBe(null);
    expect(copyOf('C')).toBe(null);
    expect(label(copyOf('B'))).toBe('b1');
    // h.5: B sa che il progetto e' stato salvato in un'altra scheda; A no.
    expect(B.notice('savedElsewhere')).toBeTruthy();
    expect(A.notice('savedElsewhere')).toBe(null);
    // Aggancio h.4: la base riparte dalla versione restituita dal server.
    A.edit('a2'); A.pause();
    expect(copyOf('A').baseUpdatedAt).toBe('2026-11-04T10:05:00Z');
  },

  'salvataggio fallito': async () => {
    const A = openTab('A');
    const B = openTab('B');
    A.edit('a1'); A.pause();
    updateCanvas.mockImplementationOnce(async () => { throw new Error('rete'); });
    await act(async () => { await A.store.getState().handleSaveQuiet().catch(() => {}); });
    expect(label(copyOf('A'))).toBe('a1');
    expect(hasUnsavedChanges(A.store.getState())).toBe(true);
    expect(B.notice('savedElsewhere')).toBe(null);
  },

  'ripristino': async () => {
    const A = openTab('A');
    A.edit('annunciato'); A.pause();
    const ask = vi.fn(async () => true);
    const B = openTab('B', { confirm: ask });
    expect(B.offer()).toEqual(['A=open|annunciato|false']);
    // A continua a lavorare: B ripristina comunque cio' che ha annunciato (h.2).
    A.edit('dopo'); A.pause();
    // h.7: B aveva modifiche sue -> si chiede, e restano nella cronologia.
    B.edit('b-prima');
    await act(async () => { await B.apiRef.current.restore('A'); });
    expect(ask).toHaveBeenCalledTimes(1);
    expect(label(B.store.getState().nodes)).toBe('annunciato');
    expect(hasUnsavedChanges(B.store.getState())).toBe(true);
    act(() => B.store.getState().undo());
    expect(label(B.store.getState().nodes)).toBe('b-prima');
    act(() => B.store.getState().undo());
    expect(label(B.store.getState().nodes)).toBe(undefined); // versione del server
    // La copia della scheda aperta non si tocca.
    expect(label(copyOf('A'))).toBe('dopo');
    // "Non ripristinare": niente cambia.
    closedCopy('C', 'di-C');
    const ask2 = vi.fn(async () => false);
    const E = openTab('E', { confirm: ask2 });
    E.edit('e1');
    await act(async () => { await E.apiRef.current.restore('C'); });
    expect(label(E.store.getState().nodes)).toBe('e1');
    expect(E.offer().length).toBe(2);
  },

  'undo dopo ripristino': async () => {
    closedCopy('C', 'di-C');
    const B = openTab('B');
    await act(async () => { await B.apiRef.current.restore('C'); });
    B.pause();
    act(() => B.store.getState().undo());
    B.pause();
    // h.8: la copia ripristinata resta finche' non si salva o si scarta.
    expect(label(copyOf('C'))).toBe('di-C');
    expect(label(copyOf('B'))).toBe(undefined);
    await B.save();
    expect(copyOf('C')).toBe(null);
  },

  'scarta': async () => {
    const A = openTab('A');
    A.edit('a1'); A.pause();
    closedCopy('C', 'di-C');
    closedCopy('F', 'di-F');
    const B = openTab('B');
    // Copia di una scheda aperta: solo nascosta.
    act(() => B.apiRef.current.discard('A'));
    expect(label(copyOf('A'))).toBe('a1');
    // Copia di una scheda chiusa: cancellata.
    act(() => B.apiRef.current.discard('C'));
    expect(copyOf('C')).toBe(null);
    // Confronta e cancella: F nel frattempo ha una copia nuova, che resta.
    closedCopy('F', 'di-F-nuova', { savedAt: Date.now() });
    act(() => B.apiRef.current.discard('F'));
    expect(label(copyOf('F'))).toBe('di-F-nuova');
    expect(B.offer()).toEqual([]);
  },

  'uscita (pagehide/smontag.)': async () => {
    const A = openTab('A');
    const B = openTab('B');
    // h.13: entro la pausa, l'uscita scrive subito.
    A.edit('a-uscita');
    act(() => { window.dispatchEvent(new Event('pagehide')); });
    expect(label(copyOf('A'))).toBe('a-uscita');
    B.edit('b-smontata');
    B.unmount();
    expect(label(copyOf('B'))).toBe('b-smontata');
  },

  '401': async () => {
    const wrap = (el) => <MemoryRouter><AuthProvider>{el}</AuthProvider></MemoryRouter>;
    const A = openTab('A', { wrap });
    const B = openTab('B');
    A.edit('a-401');
    // h.14: il 401 passa da logout(); la copia si scrive prima di togliere l'utente.
    act(() => { authBus.onUnauthorized(); });
    expect(sessionStorage.getItem('urby_user')).toBe(null);
    // h.16: la copia resta (e' di quell'utente) e quella di B non cambia.
    expect(label(copyOf('A'))).toBe('a-401');
    expect(copyOf('B')).toBe(null);
  },

  "uscita scelta dall'account": async () => {
    // Collaudo live L7: dopo la domanda sulle modifiche non salvate, uscendo
    // si tolgono le copie di chi esce; quella della sua altra scheda ancora
    // aperta resta (mai cancellata da fuori), come quelle degli altri utenti.
    // A e' la scheda di questa pagina (quella da cui si esce).
    const me = currentTabId();
    const A = openTab(me);
    const B = openTab('B');
    A.edit('a-uscita'); A.pause();
    B.edit('b-aperta'); B.pause();
    closedCopy('chiusa', 'c-chiusa');
    writeDraft('altro', P, 'X', { savedAt: Date.now(), nodes: [], edges: [] });
    act(() => { touchTab('B'); });
    act(() => { resetSession({ reason: 'logout', userId: U }); vi.advanceTimersByTime(ROLL_CALL_MS); });
    expect(copyOf(me)).toBe(null);
    expect(copyOf('chiusa')).toBe(null);
    expect(label(copyOf('B'))).toBe('b-aperta');
    expect(readDraft('altro', P, 'X')).not.toBe(null);
  },

  'eliminazione progetto': async () => {
    const A = openTab('A');
    const B = openTab('B');
    A.edit('a1'); A.pause();
    B.edit('b1');
    act(() => markProjectDeleted(P));
    expect(listDrafts(U, P)).toEqual([]);
    // h.12: nessuna scheda fa rinascere la copia.
    B.pause();
    A.edit('a2'); A.pause();
    expect(listDrafts(U, P)).toEqual([]);
    expect(writeDraft(U, P, 'A', { savedAt: Date.now(), nodes: [], edges: [] })).toEqual({ ok: false, reason: 'deleted' });
    expect(A.notice('deleted')).toBeTruthy();
    expect(B.notice('deleted')).toBeTruthy();
  },

  'ripristino versione': async () => {
    const A = openTab('A');
    const B = openTab('B');
    A.edit('a1'); A.pause();
    B.edit('b1'); B.pause();
    // CanvasVersionsModal in A: markClean + serverCanvasReplaced.
    act(() => { A.store.getState().markClean(); serverCanvasReplaced(P, U, 'A'); });
    expect(copyOf('A')).toBe(null);
    expect(label(copyOf('B'))).toBe('b1');
    expect(B.notice('savedElsewhere')).toBeTruthy();
  },

  'conflitto: ricarica': async () => {
    // B ha salvato dopo che A ha aperto: il salvataggio di A e' rifiutato
    // (409) e A sceglie "Ricarica la versione del server" (serverActions).
    const A = openTab('A');
    const B = openTab('B');
    B.edit('b1'); B.pause();
    A.edit('a1'); // in sospeso: non ancora nella copia
    const st = A.store.getState();
    expect(keepTabCopy(P, { nodes: st.nodes, edges: st.edges, baseUpdatedAt: SERVER_AT }, U, 'A')).toEqual({ ok: true });
    expect(label(copyOf('A'))).toBe('a1');
    expect(label(copyOf('B'))).toBe('b1');
    // Ricaricamento: la copia e' offerta come "questa scheda", superata dal server.
    act(() => A.store.getState().markClean());
    A.unmount();
    const A2 = openTab('A', { serverUpdatedAt: '2026-11-04T09:45:00Z' });
    expect(A2.offer().find(x => x.startsWith('A~'))).toMatch(/=this\|a1\|true$/);
  },

  'storage: salvato altrove': async () => {
    closedCopy('C', 'di-C');
    const B = openTab('B');
    B.edit('b1'); B.pause();
    const A = openTab('A');
    A.edit('a1');
    await A.save();
    expect(B.offer()).toEqual(['C=closed|di-C|true']);
    expect(B.notice('savedElsewhere')).toBeTruthy();
    expect(label(copyOf('B'))).toBe('b1');
  },

  'storage: progetto eliminato': async () => {
    closedCopy('C', 'di-C');
    const B = openTab('B');
    expect(B.offer().length).toBe(1);
    act(() => markProjectDeleted(P));
    expect(B.offer()).toEqual([]);
    expect(B.notice('deleted')).toBeTruthy();
  },
};

describe('copia di sicurezza: tabella delle transizioni, due schede', () => {
  it('ogni riga della tabella in utils/localDrafts.js ha il suo test (e viceversa)', () => {
    const src = fs.readFileSync(path.resolve(__dirname, '../../src/utils/localDrafts.js'), 'utf-8');
    const rows = src.split(/\r?\n/)
      .filter(l => /^ \* \| /.test(l))
      .map(l => l.split('|')[1].trim())
      .filter(c => c && c !== 'evento' && !/^-+$/.test(c));
    expect(rows.sort()).toEqual(Object.keys(DRAFT_TRANSITIONS).sort());
  });

  Object.entries(DRAFT_TRANSITIONS).forEach(([event, run]) => {
    it(`transizione: ${event}`, run);
  });
});

describe('copia di sicurezza: regole fuori dalla tabella', () => {
  it("h.9: l'utente e' il user_id del login, mai lo username", () => {
    expect(currentDraftUser()).toBe('7');
    sessionStorage.setItem('urby_user', JSON.stringify({ username: 'mario' }));
    expect(currentDraftUser()).toBe(null);
  });

  it("h.10: pulizia globale all'avvio, di ogni utente e progetto", () => {
    writeDraft('9', 'altro', 'X', { savedAt: Date.now() - MAX_AGE_MS - 1, nodes: [], edges: [] });
    writeDraft(U, P, 'Y', { savedAt: Date.now(), nodes: [], edges: [] });
    localStorage.setItem('plenora:deleted:vecchio', String(Date.now() - MAX_AGE_MS - 1));
    pruneDrafts();
    expect(localStorage.getItem(draftKey('9', 'altro', 'X'))).toBe(null);
    expect(localStorage.getItem('plenora:deleted:vecchio')).toBe(null);
    expect(readDraft(U, P, 'Y')).toBeTruthy();
  });

  it('id di scheda: stabile al ricaricamento, nuovo per una scheda duplicata', async () => {
    sessionStorage.setItem('plenora:tab-id', 'scheda-1');
    vi.resetModules();
    expect((await import('../../src/utils/localDrafts')).currentTabId()).toBe('scheda-1');
    // Duplicata: il sessionStorage e' copiato ma l'originale e' viva.
    localStorage.setItem('plenora:tab:scheda-1', String(Date.now()));
    vi.resetModules();
    const id = (await import('../../src/utils/localDrafts')).currentTabId();
    expect(id).not.toBe('scheda-1');
    expect(sessionStorage.getItem('plenora:tab-id')).toBe(id);
  });
});
