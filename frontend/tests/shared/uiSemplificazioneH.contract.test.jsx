/**
 * UI — semplificazione H1–H7 (06/11/2026), elenco docs/audits/ui-conferma-
 * finale-2026-11-06.md, «Giro successivo, su c916dc42». Una prova per voce,
 * che fallirebbe senza la correzione (H3 in virtualLists.contract; il lato
 * server di H1/H2/H4/H5/H6 in backend/tests2):
 *
 *  H2  una pulizia annotata si toglie appena il server la chiude (204, o
 *      403/404): anche dalla risposta della keepalive; 202/207/401/rete la
 *      lasciano; mai ripetizioni per una pulizia gia' chiusa;
 *  H4  uscire senza chiudere la finestra (pagina lasciata, uscita
 *      dall'account) fa quello che dice il dialog: in /run pulizia completa
 *      (il server annulla prima), fuori da /run annullamento;
 *  H7  la finestra Esecuzione si scarica all'apertura dell'editor.
 */
import fs from 'node:fs';
import path from 'node:path';
import React from 'react';
import { render, act, renderHook, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { resetSession } from '../../src/utils/session';
import { parkExitCleanup, parkedExitCleanups } from '../../src/utils/exitCleanups';
import { lazyWindow } from '../../src/components/common/LazyWindow';

const SRC = path.resolve(import.meta.dirname, '../../src');
const read = (rel) => fs.readFileSync(path.join(SRC, rel), 'utf8');
const flush = async () => { for (let i = 0; i < 8; i += 1) await Promise.resolve(); };
const asUser = (id) => window.sessionStorage.setItem('urby_user', JSON.stringify({ user_id: id }));

beforeEach(() => {
  resetSession(); window.localStorage.clear(); window.sessionStorage.clear();
  asUser('esec');
  window.sessionStorage.setItem('urby_token', 'tok');
});
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); resetSession(); });

describe('H2 — una pulizia chiusa dal server non si ripete', () => {
  const api = () => import('../../src/api/tasks');

  it('la risposta della keepalive toglie l\'annotazione con 204, la lascia con 202', async () => {
    const { releaseTaskOnPageExit } = await api();
    let status = 204;
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({ status })));
    expect(releaseTaskOnPageExit('t1')).toBe(true);
    expect(parkedExitCleanups()).toEqual(['t1']); // annotata prima di partire
    await flush();
    expect(parkedExitCleanups()).toEqual([]);
    status = 202; // in corso: la pulizia la fa chi chiude il task
    releaseTaskOnPageExit('t2');
    await flush();
    expect(parkedExitCleanups()).toEqual(['t2']);
  });

  it('all\'apertura di /run: 204 e 404 chiudono, 202, 207, 401 e la rete no', async () => {
    const { flushParkedExitCleanups } = await api();
    const { default: http } = await import('../../src/api/http');
    for (const id of ['ok', 'gone', 'running', 'partial', 'expired', 'offline']) parkExitCleanup(id);
    const answers = {
      ok: Promise.resolve({ status: 204 }),
      gone: Promise.reject(Object.assign(new Error('404'), { response: { status: 404 } })),
      running: Promise.resolve({ status: 202 }),
      partial: Promise.resolve({ status: 207 }),
      expired: Promise.reject(Object.assign(new Error('401'), { response: { status: 401 } })),
      offline: Promise.reject(new Error('Network Error')),
    };
    vi.spyOn(http, 'delete').mockImplementation((url) => answers[decodeURIComponent(url.split('/')[2])]);
    expect(await flushParkedExitCleanups()).toBe(2);
    expect(parkedExitCleanups()).toEqual(['running', 'partial', 'expired', 'offline']);
  });

  it('anche la pulizia nella pagina si annota e si toglie solo quando il server la chiude', async () => {
    const { cleanupTaskCompletely } = await api();
    const { default: http } = await import('../../src/api/http');
    const del = vi.spyOn(http, 'delete');
    del.mockResolvedValueOnce({ status: 202, data: { status: 'deferred' } });
    expect(await cleanupTaskCompletely('t1')).toMatchObject({ deferred: true, partialCleanup: false });
    expect(parkedExitCleanups()).toEqual(['t1']); // la riprende l'apertura dopo
    del.mockResolvedValueOnce({ status: 204, data: '' });
    await cleanupTaskCompletely('t1');
    expect(parkedExitCleanups()).toEqual([]);
  });
});

describe('H4 — uscire fa quello che dice il dialog', () => {
  async function runningHook(onCleanup) {
    vi.resetModules();
    vi.doMock('../../src/api', () => ({
      downloadEseguibileOutput: vi.fn(),
      getTaskStatus: vi.fn(() => Promise.resolve({ status: 'progress', progress: 10 })),
      cancelTask: vi.fn(),
    }));
    const mod = await import('../../src/components/eseguibili/useEseguibileRun');
    const { authBus } = await import('../../src/utils/authBus');
    const { leaveDialog } = await import('../../src/utils/leaveGuard');
    const hook = renderHook(() => mod.useEseguibileRun({ eseguibileId: 'e1', onCleanup }));
    await act(async () => { await hook.result.current.start(() => Promise.resolve({ async: true, task_id: 't1' })); });
    await waitFor(() => expect(hook.result.current.task.isActive).toBe(true), { timeout: 3000 });
    return { ...hook, mod, authBus, leaveDialog };
  }
  afterEach(() => { vi.doUnmock('../../src/api'); vi.resetModules(); });

  it('/run: lasciando la pagina con un\'esecuzione in corso parte la pulizia completa', async () => {
    const onCleanup = vi.fn();
    const { unmount, mod, leaveDialog } = await runningHook(onCleanup);
    expect(leaveDialog()).toBe(mod.RUN_LEAVE_DIALOGS.cleansOnExit);
    expect(leaveDialog().message).toMatch(/l'esecuzione in corso si ferma e il risultato viene eliminato dal server/);
    unmount();
    expect(onCleanup).toHaveBeenCalledWith('t1', { pageExit: true });
  });

  it('/run: l\'uscita dall\'account pulisce prima di togliere l\'utente (una volta sola)', async () => {
    const onCleanup = vi.fn();
    const { unmount, authBus } = await runningHook(onCleanup);
    authBus.emitBeforeLogout();
    expect(onCleanup).toHaveBeenCalledWith('t1', { pageExit: true });
    unmount();
    expect(onCleanup).toHaveBeenCalledTimes(1);
  });

  it('fuori da /run: lasciando la pagina l\'esecuzione in corso si annulla (e il dialog lo dice)', async () => {
    const fetchMock = vi.fn(() => Promise.resolve({ status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    const { unmount, mod, leaveDialog } = await runningHook(null);
    expect(leaveDialog()).toBe(mod.RUN_LEAVE_DIALOGS.keepsOnServer);
    expect(leaveDialog().message).not.toMatch(/eliminato/);
    expect(leaveDialog().message).toMatch(/si ferma/);
    unmount();
    const call = fetchMock.mock.calls.find(([url]) => /\/tasks\/t1$/.test(url));
    expect(call?.[1]).toMatchObject({ method: 'DELETE', keepalive: true });
  });
});

describe('H7 — Esecuzione precaricata all\'apertura dell\'editor', () => {
  it('con `prefetch` il file si scarica anche a finestra chiusa, una volta; senza, no', async () => {
    const loader = vi.fn(() => Promise.resolve({ default: () => <p>Esecuzione</p> }));
    const lazyOnly = vi.fn(() => Promise.resolve({ default: () => <p>Versioni</p> }));
    const Run = lazyWindow(loader, 'Esecuzione', { prefetch: true });
    const Versions = lazyWindow(lazyOnly, 'Versioni');
    const { rerender } = render(<><Run isOpen={false} onClose={() => {}} /><Versions isOpen={false} onClose={() => {}} /></>);
    await act(flush);
    rerender(<><Run isOpen={false} onClose={() => {}} /><Versions isOpen={false} onClose={() => {}} /></>);
    await act(flush);
    expect(loader).toHaveBeenCalledTimes(1);
    expect(lazyOnly).not.toHaveBeenCalled();
  });

  it('i tre editor precaricano ExecutionModal', () => {
    for (const f of ['pages/EstraiEditor.jsx', 'pages/ManipolaEditor.jsx', 'pages/ArricchisciEditor.jsx']) {
      expect({ f, ok: /const ExecutionModal = lazyWindow\([^;]*'Esecuzione', \{ prefetch: true \}\)/.test(read(f)) }).toEqual({ f, ok: true });
    }
  });
});
