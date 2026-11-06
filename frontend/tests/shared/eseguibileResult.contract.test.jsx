/**
 * Contract del risultato di un Eseguibile e della pulizia dell'esecutore
 * (UI conferma B-K4, B-K5), sopra l'hook unico useEseguibileRun.
 *
 *  B-K4 — privacy anche chiudendo la scheda: `pagehide` fa partire la
 *    pulizia (una volta) con `pageExit`, e `active` se l'esecuzione e'
 *    ancora in corso; la variante dell'API usa fetch keepalive con il token
 *    di sessione verso le route esistenti (annullamento, poi pulizia).
 *    Chiudere la scheda con qualcosa da perdere chiede (beforeunload).
 *  B-K5 — un solo stato del risultato: scaduta la copia scaricata (misurata
 *    sull'orologio, anche dopo una scheda nascosta) il risultato e' «non piu'
 *    disponibile»: blob tolto, server pulito, niente «Scarica» (che
 *    tornerebbe al server: consegna unica), niente domanda di chiusura
 *    falsa; durante lo scaricamento la finestra non si chiude e non pulisce.
 */
import React from 'react';
import { renderHook, act, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const api = vi.hoisted(() => ({
  downloadEseguibileOutput: vi.fn(),
  getTaskStatus: vi.fn(),
  cancelTask: vi.fn(),
}));
vi.mock('../../src/api', () => api);
const saved = vi.hoisted(() => ({ calls: [] }));
vi.mock('../../src/utils/downloadBlob', async (orig) => ({
  ...(await orig()),
  saveBlob: (blob, name) => { saved.calls.push([blob, name]); },
}));

import { useEseguibileRun, RESULT_KEEP_MINUTES } from '../../src/components/eseguibili/useEseguibileRun';
import { ConfirmProvider } from '../../src/context/ConfirmContext';
import { releaseTaskOnPageExit } from '../../src/api/tasks';

const SUCCESS = { status: 'success', progress: 100, result: { rows_processed: 3 } };

async function finishedRun({ onCleanup = vi.fn(), status = SUCCESS, wrapper } = {}) {
  api.getTaskStatus.mockResolvedValue(status);
  const onClose = vi.fn();
  const hook = renderHook(() => useEseguibileRun({ eseguibileId: 'e1', onCleanup, onClose }), { wrapper });
  await act(async () => { await hook.result.current.start(() => Promise.resolve({ async: true, task_id: 't1' })); });
  if (status) await waitFor(() => expect(hook.result.current.task.isDone || hook.result.current.task.isActive).toBe(true), { timeout: 3000 });
  return { ...hook, onCleanup, onClose };
}

const pageHide = () => act(() => { window.dispatchEvent(new Event('pagehide')); });
const beforeUnload = () => {
  const ev = new Event('beforeunload', { cancelable: true });
  act(() => { window.dispatchEvent(ev); });
  return ev.defaultPrevented;
};

beforeEach(() => {
  vi.clearAllMocks();
  saved.calls = [];
  api.downloadEseguibileOutput.mockResolvedValue({ blob: new Blob(['x']), filename: 'out.csv' });
});
afterEach(() => { vi.useRealTimers(); });

describe('B-K4: pulizia anche chiudendo la scheda', () => {
  it('esecuzione finita: pagehide pulisce UNA volta con pageExit; il risultato non c\'e\' piu\'', async () => {
    const { result, onCleanup, unmount } = await finishedRun();
    await waitFor(() => expect(result.current.resultState).toBe('on_server'), { timeout: 3000 });
    pageHide();
    expect(onCleanup).toHaveBeenCalledTimes(1);
    expect(onCleanup).toHaveBeenCalledWith('t1', { pageExit: true, active: false });
    // Pagina rimessa dalla cache del browser: niente «Scarica» verso un server pulito.
    expect(result.current.resultState).toBe('gone');
    expect(result.current.canDownload).toBe(false);
    pageHide();
    unmount();
    expect(onCleanup).toHaveBeenCalledTimes(1);
  });

  it('esecuzione in corso: pagehide pulisce con active (da annullare)', async () => {
    api.getTaskStatus.mockResolvedValue({ status: 'progress', progress: 40 });
    const onCleanup = vi.fn();
    const { result } = renderHook(() => useEseguibileRun({ eseguibileId: 'e1', onCleanup }));
    await act(async () => { await result.current.start(() => Promise.resolve({ async: true, task_id: 't2' })); });
    expect(result.current.task.isActive).toBe(true);
    expect(beforeUnload()).toBe(true);
    pageHide();
    expect(onCleanup).toHaveBeenCalledWith('t2', { pageExit: true, active: true });
  });

  it('senza pulizia dichiarata (ruoli non esecutore) pagehide non fa nulla', async () => {
    const { result } = renderHook(() => useEseguibileRun({ eseguibileId: 'e1' }));
    api.getTaskStatus.mockResolvedValue(SUCCESS);
    await act(async () => { await result.current.start(() => Promise.resolve({ async: true, task_id: 't3' })); });
    await waitFor(() => expect(result.current.resultState).toBe('on_server'), { timeout: 3000 });
    pageHide();
    expect(result.current.resultState).toBe('on_server');
  });

  it('chiudere la scheda con un risultato non scaricato chiede; scaricato no', async () => {
    const { result } = await finishedRun();
    await waitFor(() => expect(result.current.resultState).toBe('on_server'), { timeout: 3000 });
    expect(beforeUnload()).toBe(true);
    await act(async () => { await result.current.download('out.csv'); });
    expect(result.current.resultState).toBe('local');
    expect(beforeUnload()).toBe(false);
  });

  it('l\'API parte con fetch keepalive e il token: annullamento (se in corso), poi pulizia', () => {
    const fetchSpy = vi.fn(() => Promise.resolve({ ok: true }));
    vi.stubGlobal('fetch', fetchSpy);
    sessionStorage.setItem('urby_token', 'tok-1');
    try {
      expect(releaseTaskOnPageExit('t/9', { active: true })).toBe(true);
      expect(fetchSpy.mock.calls.map(([url, o]) => [url.replace(/^.*\/api/, ''), o.method, o.keepalive, o.headers.Authorization]))
        .toEqual([
          ['/tasks/t%2F9', 'DELETE', true, 'Bearer tok-1'],
          ['/tasks/t%2F9/full-cleanup', 'DELETE', true, 'Bearer tok-1'],
        ]);
      fetchSpy.mockClear();
      releaseTaskOnPageExit('t1');
      expect(fetchSpy).toHaveBeenCalledTimes(1);
      // Senza sessione non parte niente.
      sessionStorage.removeItem('urby_token');
      fetchSpy.mockClear();
      expect(releaseTaskOnPageExit('t1')).toBe(false);
      expect(fetchSpy).not.toHaveBeenCalled();
    } finally {
      vi.unstubAllGlobals();
      sessionStorage.removeItem('urby_token');
    }
  });
});

describe('B-K5: un solo stato del risultato', () => {
  it('scaduta la copia scaricata (orologio, anche a scheda nascosta): non piu\' disponibile, server pulito, nessuna richiesta', async () => {
    const wrapper = ({ children }) => <ConfirmProvider>{children}</ConfirmProvider>;
    const { result, onCleanup, onClose } = await finishedRun({ wrapper });
    await waitFor(() => expect(result.current.resultState).toBe('on_server'), { timeout: 3000 });
    await act(async () => { await result.current.download('out.csv'); });
    expect(result.current.resultState).toBe('local');
    expect(result.current.resultNote).toMatch(/fino alle \d{2}:\d{2}/);
    await act(async () => { await result.current.download('out.csv'); });
    expect(saved.calls).toHaveLength(1);
    expect(api.downloadEseguibileOutput).toHaveBeenCalledTimes(1);

    // Il timer di 30 minuti non e' arrivato (scheda nascosta, sospensione):
    // al ritorno sulla scheda conta l'orologio.
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(Date.now() + RESULT_KEEP_MINUTES * 60_000 + 1000);
    act(() => { document.dispatchEvent(new Event('visibilitychange')); });
    expect(result.current.resultState).toBe('gone');
    expect(result.current.downloaded).toBe(false);
    expect(result.current.canDownload).toBe(false);
    expect(result.current.resultNote).toMatch(/non è più disponibile/);
    expect(onCleanup).toHaveBeenCalledWith('t1', { pageExit: false, active: false });

    // «Scarica» non torna al server (consegna unica: sarebbe un errore).
    await act(async () => { await result.current.download('out.csv'); });
    expect(api.downloadEseguibileOutput).toHaveBeenCalledTimes(1);
    expect(saved.calls).toHaveLength(1);
    // Chiudere non chiede «non scaricato» (non c'e' niente da perdere) e non ripulisce.
    let closed;
    await act(async () => { closed = await result.current.close(); });
    expect(closed).toBe(true);
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(document.body.textContent).not.toMatch(/Risultato non scaricato/);
    expect(onCleanup).toHaveBeenCalledTimes(1);
  });

  it('scadenza controllata anche al clic su «Scarica di nuovo»', async () => {
    const { result } = await finishedRun();
    await waitFor(() => expect(result.current.resultState).toBe('on_server'), { timeout: 3000 });
    await act(async () => { await result.current.download('out.csv'); });
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(Date.now() + RESULT_KEEP_MINUTES * 60_000 + 1000);
    await act(async () => { await result.current.download('out.csv'); });
    expect(saved.calls).toHaveLength(0);
    expect(result.current.resultState).toBe('gone');
  });

  it('durante lo scaricamento la finestra non si chiude e non pulisce', async () => {
    let release;
    api.downloadEseguibileOutput.mockReturnValue(new Promise((r) => { release = r; }));
    const { result, onCleanup, onClose } = await finishedRun();
    await waitFor(() => expect(result.current.resultState).toBe('on_server'), { timeout: 3000 });
    let pending;
    act(() => { pending = result.current.download('out.csv'); });
    await waitFor(() => expect(result.current.downloading).toBe(true));
    let closed; let discarded;
    await act(async () => { closed = await result.current.close(); discarded = await result.current.discard(); });
    expect([closed, discarded]).toEqual([false, false]);
    expect(onCleanup).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    await act(async () => { release({ blob: new Blob(['x']), filename: 'out.csv' }); await pending; });
    await act(async () => { closed = await result.current.close(); });
    expect(closed).toBe(true);
    expect(onCleanup).toHaveBeenCalledTimes(1);
  });
});
