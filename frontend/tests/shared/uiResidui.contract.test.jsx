/**
 * Residui del collaudo dal vivo (09/11/2026, docs/audits/ui-collaudo-live-
 * 2026-11-07.md): una prova per voce, ognuna fallisce senza la correzione.
 *
 *  L7b le chiavi delle copie seguono una regola sola (localDrafts.KEEP):
 *      via le copie della forma vecchia (<username>:<progetto>), gli
 *      annunci «salvato» (vivono solo per l'evento storage), i segnali delle
 *      schede morte, le lapidi scadute; all'avvio e all'uscita scelta;
 *  M3  all'uscita una scheda in secondo piano (segnale vecchio, timer
 *      rallentati) risponde all'appello e la sua copia resta;
 *  M5  i messaggi dei Carica passano da withStepTitles;
 *  M6  un esito riuscito senza righe dice il file (anche `output_filename`);
 *  M7  si sostituiscono solo id in forma di id dei nodi, come parola intera.
 *
 * L2b (guida a canvas vuoto dopo «Crea progetto») e' qui come sequenza
 * completa: nessuna sequenza in jsdom riproduce il difetto (dichiarato nel
 * piano).
 */
import fs from 'node:fs';
import path from 'node:path';
import React from 'react';
import { render, screen, cleanup, act, fireEvent } from '@testing-library/react';
import { createMemoryRouter, RouterProvider, Routes, Route, useParams } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  ROLL_CALL, ROLL_CALL_MS, TABS_CHANNEL, announceSaved, currentTabId, draftKey, pruneDrafts, readDraft, touchTab, writeDraft,
} from '../../src/utils/localDrafts';
import { STORAGE_KEYS } from '../../src/utils/browserStorage';
import { resetSession } from '../../src/utils/session';
import { NODE_ID_SHAPE, citedStep, withStepTitles } from '../../src/utils/stepTitles';
import { failedStepOf, runSuccessText } from '../../src/utils/runFailure';
import { nodeTitle } from '../../src/components/canvas/nodeDescriptors';
import { SRC, stripComments } from '../helpers/sourceScan.js';

const { http } = vi.hoisted(() => ({ http: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() } }));
vi.mock('../../src/api/http', () => ({ default: http, sendOnPageExit: vi.fn() }));
vi.mock('@xyflow/react', () => globalThis.__frontendTestHelpers.createReactFlowMock({ canvasStateRef: { current: { nodes: [], edges: [] } } }));
vi.mock('../../src/context/AuthContext', () => ({ useAuth: () => ({ user: { role: 'admin', user_id: '1', username: 'admin', group_id: 'g' } }) }));
vi.mock('../../src/utils/hexCanvas', () => ({ useHexCanvas: () => {} }));

const read = (rel) => fs.readFileSync(path.join(SRC, rel), 'utf-8');
const ls = () => window.localStorage;
const keys = (prefix) => Object.keys(ls()).filter(k => k.startsWith(prefix));
const DAY = 24 * 60 * 60 * 1000;

afterEach(() => { cleanup(); vi.useRealTimers(); });

// ── L7b ─────────────────────────────────────────────────────────────────

describe('L7b: una regola per le chiavi delle copie', () => {
  beforeEach(() => ls().clear());

  const seed = () => {
    const now = Date.now();
    writeDraft('7', 'p1', 'A', { savedAt: now, nodes: [], edges: [] });                         // forma attuale
    ls().setItem('plenora:draft:admin:p1', JSON.stringify({ savedAt: now, nodes: [], edges: [] })); // forma vecchia (username)
    ls().setItem('plenora:draft:7:p2:B', JSON.stringify({ savedAt: now, nodes: [] }));           // senza record v2
    ls().setItem('plenora:saved:p-cancellato', JSON.stringify({ tabId: 'X', at: now }));          // annuncio rimasto
    ls().setItem('plenora:tab:morta', String(now - DAY));                                          // scheda morta
    touchTab('viva');
    ls().setItem('plenora:deleted:vecchio', String(now - 15 * DAY));
    ls().setItem('plenora:deleted:recente', String(now));
  };

  it('all\'avvio: restano solo le copie attuali, le schede vive e le lapidi recenti', () => {
    seed();
    pruneDrafts();
    expect(keys(STORAGE_KEYS.draft.prefix)).toEqual([draftKey('7', 'p1', 'A')]);
    expect(keys(STORAGE_KEYS.draftSaved.prefix)).toEqual([]);
    expect(keys(STORAGE_KEYS.draftTab.prefix)).toEqual(['plenora:tab:viva']);
    expect(keys(STORAGE_KEYS.draftDeleted.prefix)).toEqual(['plenora:deleted:recente']);
  });

  it('all\'uscita scelta: la stessa regola, anche per le chiavi vecchie di chi esce', () => {
    vi.useFakeTimers();
    seed();
    resetSession({ reason: 'logout', userId: '9' });
    vi.advanceTimersByTime(ROLL_CALL_MS);
    expect(ls().getItem('plenora:draft:admin:p1')).toBe(null);
    expect(keys(STORAGE_KEYS.draftSaved.prefix)).toEqual([]);
    expect(readDraft('7', 'p1', 'A')).not.toBe(null); // un altro utente: resta
  });

  it('l\'annuncio «salvato» non resta nel browser', () => {
    announceSaved('p1', { tabId: 'A' });
    expect(keys(STORAGE_KEYS.draftSaved.prefix)).toEqual([]);
    expect(read('utils/localDrafts.js')).toMatch(/\[SAVED_PREFIX\]: \(\) => false/);
  });
});

// ── M3 ──────────────────────────────────────────────────────────────────

describe('M3: all\'uscita le schede vive rispondono all\'appello', () => {
  beforeEach(() => ls().clear());

  it('una scheda in secondo piano (segnale vecchio) tiene la copia; una chiusa no', async () => {
    expect(typeof BroadcastChannel).toBe('function');
    const now = Date.now();
    writeDraft('7', 'p1', currentTabId(), { savedAt: now, nodes: [], edges: [] });
    writeDraft('7', 'p1', 'dietro', { savedAt: now, nodes: [], edges: [] });
    writeDraft('7', 'p1', 'chiusa', { savedAt: now, nodes: [], edges: [] });
    // In secondo piano da 6 minuti: il battito e' rallentato, il segnale e' vecchio.
    ls().setItem('plenora:tab:dietro', String(now - 6 * 60 * 1000));
    const behind = new BroadcastChannel(TABS_CHANNEL);
    behind.onmessage = (e) => { if (e.data === ROLL_CALL) touchTab('dietro'); };
    try {
      resetSession({ reason: 'logout', userId: '7' });
      await new Promise(r => setTimeout(r, ROLL_CALL_MS + 200));
    } finally { behind.close(); }
    expect(readDraft('7', 'p1', 'dietro')).not.toBe(null);
    expect(readDraft('7', 'p1', 'chiusa')).toBe(null);
    expect(readDraft('7', 'p1', currentTabId())).toBe(null);
  });

  it('la pagina risponde all\'appello rinnovando il proprio segnale', () => {
    const code = stripComments(read('utils/localDrafts.js'));
    expect(code).toMatch(/ch\.onmessage = \(e\) => \{ if \(e\?\.data === ROLL_CALL\) touchTab\(tabId\); \}/);
    expect(code).toMatch(/ch\.postMessage\(ROLL_CALL\)/);
  });
});

// ── M5, M6, M7 ──────────────────────────────────────────────────────────

const N = (id, type, data = {}) => ({ id, type, data, position: { x: 0, y: 0 } });

describe('M7: solo id in forma di id dei nodi, come parola intera', () => {
  const nodes = [N('result', 'result'), N('a', 'transform', { title: 'A' }), N('transform_1731000000000', 'transform', { title: 'Solo attivi' }),
    N('file_1731000000000_2', 'file_source', { file_name: 'clienti.csv' })];

  it('parole e id brevi non si toccano; gli id veri si', () => {
    expect(withStepTitles('result non valido in a', nodes)).toBe('result non valido in a');
    expect(withStepTitles("Nodo 'transform_1731000000000' e file_1731000000000_2.", nodes))
      .toBe(`Nodo «Solo attivi» e «${nodeTitle('file_source', nodes[3].data)}».`);
    expect(withStepTitles('transform_1731000000000_x', nodes)).toBe('transform_1731000000000_x');
  });

  it('la forma: prefisso del tipo, poi numero o uuid', () => {
    for (const id of ['transform_1731000000000', 'node_1784712505489', 'source_1731000000000_0', 'node_3f2a9c1e-1b2c-4d5e-8f90-1234567890ab']) expect(NODE_ID_SHAPE.test(id)).toBe(true);
    for (const id of ['result', 'a', 'output', 'src', '1731000000000', 'transform_']) expect(NODE_ID_SHAPE.test(id)).toBe(false);
  });

  it('«Vai al passo» dal testo usa la stessa regola', () => {
    expect(citedStep('result mancante', nodes)).toBe(null);
    expect(failedStepOf({ success: false, message: 'result mancante' }, nodes)).toBe(null);
    expect(failedStepOf({ success: false, message: 'errore in transform_1731000000000' }, nodes)?.id).toBe('transform_1731000000000');
  });
});

describe('M6: un esito riuscito senza righe dice il file', () => {
  it('output_filename e i file restituiti cosi\' come sono', () => {
    expect(runSuccessText({ success: true, output_filename: 'arricchito.json' })).toBe('Risultato salvato in «arricchito.json».');
    expect(runSuccessText({ success: true, output_type: 'file', rows_count: 0, output_filename: 'risposta.zip' })).toBe('Risultato salvato in «risposta.zip».');
    expect(runSuccessText({ success: true, rows_processed: 12, output_file: 'a.csv' })).toBe('12 righe salvate in «a.csv».');
  });
});

describe('M5: i messaggi dei Carica coi titoli dei passi', () => {
  it('stato ed errore di ogni caricamento passano da withStepTitles', () => {
    const modal = stripComments(read('components/modals/ExecutionModal.jsx'));
    expect(modal).toMatch(/withStepTitles\(caricaTaskErrorText\(t\), editorStepsFor\(projectId, useCanvasStore\.getState\(\)\)\)/);
    expect(modal).toMatch(/withStepTitles\(t\.message, editorStepsFor\(projectId, useCanvasStore\.getState\(\)\)\)/);
    expect(modal).not.toMatch(/: \(t\.message \|\| 'In coda\.\.\.'\)/);
  });
});

// ── L2b: la sequenza di «Crea progetto» ─────────────────────────────────

describe('L2b: guida a canvas vuoto dopo «Crea progetto» (sequenza completa)', () => {
  it('dashboard → Crea progetto (Invio) → editor lazy con navigazione in transizione', { timeout: 30000 }, async () => {
    http.post.mockImplementation(async () => ({ data: { metadata: { id: 'p1', project_type: 'manipola' } } }));
    http.get.mockImplementation(async (url) => {
      if (url === '/projects/p1') return { data: { metadata: { id: 'p1', name: 'Nuovo', project_type: 'manipola' }, canvas: { nodes: [], edges: [] }, canvas_version: 'v1' } };
      if (url === '/projects') return { data: { projects: [] } };
      if (url.startsWith('/eseguibili/by-source')) return { data: [] };
      return { data: {} };
    });
    const { default: NewProjectModal } = await import('../../src/components/projects/NewProjectModal');
    const { ToastProvider } = await import('../../src/context/ToastContext');
    const { ConfirmProvider } = await import('../../src/context/ConfirmContext');
    const ManipolaEditor = React.lazy(() => import('../../src/pages/ManipolaEditor'));
    function PerProject({ children }) { const { projectId } = useParams(); return <React.Fragment key={projectId}>{children}</React.Fragment>; }
    function Dash() { const [open, setOpen] = React.useState(true); return <NewProjectModal isOpen={open} initialType="manipola" onClose={() => setOpen(false)} />; }
    const Root = () => (
      <ToastProvider><ConfirmProvider><React.Suspense fallback={<div>…</div>}>
        <Routes>
          <Route path="/" element={<Dash />} />
          <Route path="/editor/manipola/:projectId" element={<PerProject><ManipolaEditor /></PerProject>} />
        </Routes>
      </React.Suspense></ConfirmProvider></ToastProvider>
    );
    const router = createMemoryRouter([{ path: '*', element: <Root /> }], { initialEntries: ['/'], future: { v7_relativeSplatPath: true } });
    render(<RouterProvider router={router} future={{ v7_startTransition: true }} />);
    const input = document.querySelector('.pl__modal-input');
    fireEvent.change(input, { target: { value: 'Nuovo' } });
    await act(async () => { fireEvent.submit(input.closest('form')); });
    for (let i = 0; i < 100 && !screen.queryByText('Il canvas è vuoto'); i += 1) {
      await act(async () => { await new Promise(r => setTimeout(r, 50)); });
    }
    expect(router.state.location.pathname).toBe('/editor/manipola/p1');
    expect(screen.getByText('Il canvas è vuoto')).toBeTruthy();
  });
});
