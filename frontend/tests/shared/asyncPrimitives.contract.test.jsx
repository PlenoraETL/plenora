/**
 * asyncPrimitives.contract.test.jsx — Fase 5 T2: i meccanismi comuni.
 *
 *  - hooks/useResource: id di richiesta, chiave, stati distinti, Riprova,
 *    primo caricamento vs reload, aggiornamento periodico fermo a scheda
 *    nascosta, initialData, keepPreviousData.
 *  - components/common/ListState: errore come errore (mai vuoto), vuoto,
 *    errore sopra i dati gia' caricati.
 *  - utils/taskOutcome: una fase per ogni stato del task (tabella).
 *  - utils/taskPoller: 404 ripetuti, errori ripetuti, 401/403, stop.
 *  - hooks/useBusy, hooks/useDownload, utils/downloadBlob.
 */
import React from 'react';
import { render, screen, renderHook, act, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { useResource } from '../../src/hooks/useResource';
import ListState from '../../src/components/common/ListState';
import { taskOutcome, TASK_PHASES } from '../../src/utils/taskOutcome';
import { pollTask } from '../../src/utils/taskPoller';
import { useBusy } from '../../src/hooks/useBusy';
import { useDownload } from '../../src/hooks/useDownload';
import { saveBlob, REVOKE_DELAY_MS } from '../../src/utils/downloadBlob';

const deferred = () => {
  let resolve; let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
};

afterEach(() => { vi.useRealTimers(); });

describe('useResource', () => {
  it('la risposta superata (chiave cambiata) non scrive mai', async () => {
    const a = deferred();
    const loader = vi.fn((k) => (k === 'A' ? a.promise : Promise.resolve('dati di B')));
    const { result, rerender } = renderHook(({ k }) => useResource(() => loader(k), { key: k }), { initialProps: { k: 'A' } });
    rerender({ k: 'B' });
    await waitFor(() => expect(result.current.data).toBe('dati di B'));
    await act(async () => { a.resolve('dati di A'); });
    expect(result.current.data).toBe('dati di B');
    expect(result.current.status).toBe('ready');
  });

  it('chiave cambiata: i dati della chiave di prima non si mostrano durante la nuova richiesta', async () => {
    const b = deferred();
    const { result, rerender } = renderHook(({ k }) => useResource(() => (k === 'A' ? Promise.resolve(['a']) : b.promise), { key: k }), { initialProps: { k: 'A' } });
    await waitFor(() => expect(result.current.data).toEqual(['a']));
    rerender({ k: 'B' });
    expect(result.current.data).toBeUndefined();
    expect(result.current.status).toBe('loading');
    expect(result.current.initialLoading).toBe(true);
  });

  it('keepPreviousData: la stessa risorsa con parametri nuovi tiene i dati di prima', async () => {
    const b = deferred();
    const { result, rerender } = renderHook(({ k }) => useResource(() => (k === 1 ? Promise.resolve(['p1']) : b.promise), { key: k, keepPreviousData: true }), { initialProps: { k: 1 } });
    await waitFor(() => expect(result.current.data).toEqual(['p1']));
    rerender({ k: 2 });
    expect(result.current.data).toEqual(['p1']);
    expect(result.current.refreshing).toBe(true);
    await act(async () => { b.resolve(['p2']); });
    expect(result.current.data).toEqual(['p2']);
  });

  it('errore = stato error con il testo unico (mai vuoto); Riprova ricarica', async () => {
    const loader = vi.fn()
      .mockRejectedValueOnce({ response: { status: 500, data: {} } })
      .mockResolvedValueOnce([]);
    const { result } = renderHook(() => useResource(loader, { key: 'x' }));
    await waitFor(() => expect(result.current.status).toBe('error'));
    expect(result.current.error).toMatch(/errore interno/i);
    await act(async () => { await result.current.reload(); });
    expect(result.current.status).toBe('empty');
  });

  it('reload dopo un\'azione: niente primo caricamento, i dati restano (refreshing)', async () => {
    const second = deferred();
    const loader = vi.fn().mockResolvedValueOnce(['x']).mockReturnValueOnce(second.promise);
    const { result } = renderHook(() => useResource(loader, { key: 'y' }));
    await waitFor(() => expect(result.current.data).toEqual(['x']));
    act(() => { result.current.reload(); });
    expect(result.current.initialLoading).toBe(false);
    expect(result.current.refreshing).toBe(true);
    expect(result.current.data).toEqual(['x']);
    await act(async () => { second.resolve(['y']); });
  });

  it('errore di un reload con dati caricati: stale (dati sotto l\'errore)', async () => {
    const loader = vi.fn().mockResolvedValueOnce(['x']).mockRejectedValueOnce(new Error('rete giu'));
    const { result } = renderHook(() => useResource(loader, { key: 'z' }));
    await waitFor(() => expect(result.current.data).toEqual(['x']));
    await act(async () => { await result.current.reload(); });
    expect(result.current.status).toBe('error');
    expect(result.current.stale).toBe(true);
    expect(result.current.data).toEqual(['x']);
  });

  it('enabled false = nessuna richiesta (idle); initialData = nessuna richiesta al montaggio', () => {
    const loader = vi.fn(() => Promise.resolve(1));
    const off = renderHook(() => useResource(loader, { key: 'off', enabled: false }));
    expect(off.result.current.status).toBe('idle');
    const cached = renderHook(() => useResource(loader, { key: 'c', initialData: () => ['cache'] }));
    expect(cached.result.current.data).toEqual(['cache']);
    expect(loader).not.toHaveBeenCalled();
  });

  it('aggiornamento periodico: un poll alla volta, fermo con la scheda nascosta, riparte quando torna visibile', async () => {
    vi.useFakeTimers();
    let visibility = 'visible';
    const spy = vi.spyOn(document, 'visibilityState', 'get').mockImplementation(() => visibility);
    const loader = vi.fn(() => Promise.resolve('ok'));
    renderHook(() => useResource(loader, { key: 'poll', refreshInterval: 1000 }));
    await act(async () => { await Promise.resolve(); });
    expect(loader).toHaveBeenCalledTimes(1);
    await act(async () => { vi.advanceTimersByTime(1000); await Promise.resolve(); });
    expect(loader).toHaveBeenCalledTimes(2);
    visibility = 'hidden';
    await act(async () => { vi.advanceTimersByTime(1000); await Promise.resolve(); });
    // il timer pianificato prima di nascondere scatta ancora una volta
    const afterHide = loader.mock.calls.length;
    await act(async () => { vi.advanceTimersByTime(5000); await Promise.resolve(); });
    expect(loader.mock.calls.length).toBe(afterHide);
    visibility = 'visible';
    await act(async () => { document.dispatchEvent(new Event('visibilitychange')); await Promise.resolve(); });
    expect(loader.mock.calls.length).toBe(afterHide + 1);
    spy.mockRestore();
  });
});

describe('ListState', () => {
  const base = { reload: vi.fn(), stale: false, data: undefined, error: null };
  it('errore senza dati: errore con Riprova, MAI il messaggio vuoto', () => {
    render(<ListState resource={{ ...base, status: 'error', error: 'Server giù' }} empty="Nessun elemento" errorTitle="Lista non caricata">{() => 'righe'}</ListState>);
    expect(screen.getByRole('alert')).toHaveTextContent('Lista non caricata');
    expect(screen.getByText('Server giù')).toBeInTheDocument();
    expect(screen.queryByText('Nessun elemento')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Riprova' }));
    expect(base.reload).toHaveBeenCalled();
  });
  it('vuoto, caricamento, pronto, errore sopra i dati', () => {
    const { rerender } = render(<ListState resource={{ ...base, status: 'empty', data: [] }} empty="Nessun elemento">x</ListState>);
    expect(screen.getByText('Nessun elemento')).toBeInTheDocument();
    rerender(<ListState resource={{ ...base, status: 'loading' }} loading="Attendi">x</ListState>);
    expect(screen.getByRole('status')).toHaveTextContent('Attendi');
    rerender(<ListState resource={{ ...base, status: 'ready', data: [1] }}>{(d) => `righe ${d.length}`}</ListState>);
    expect(screen.getByText('righe 1')).toBeInTheDocument();
    rerender(<ListState resource={{ ...base, status: 'error', error: 'rete', stale: true, data: [1] }}>{(d) => `righe ${d.length}`}</ListState>);
    expect(screen.getByText('righe 1')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('rete');
  });
});

describe('taskOutcome — una fase per ogni stato', () => {
  const cases = [
    [{ status: 'pending' }, 'running'],
    [{ status: 'started' }, 'running'],
    [{ status: 'progress', progress: 30 }, 'running'],
    [{ status: 'retry' }, 'running'],
    [{ status: 'success', result: { rows: 1 } }, 'success'],
    [{ status: 'success', result: { success: false, message: 'no' } }, 'failure'],
    [{ status: 'success', result: { status: 'permission_revoked', message: 'ruolo' } }, 'permission_revoked'],
    [{ status: 'failure', result: { status: 'permission_revoked' } }, 'permission_revoked'],
    [{ status: 'failure', error: 'boom' }, 'failure'],
    [{ status: 'revoked' }, 'revoked'],
    [{ status: 'qualcosa' }, 'failure'],
  ];
  it.each(cases)('%j -> %s', (data, phase) => {
    const o = taskOutcome(data);
    expect(o.phase).toBe(phase);
    expect(o.done).toBe(phase !== 'running');
    expect(TASK_PHASES).toContain(phase);
  });
  it('mai "completata" falsa: solo success e senza segnali contrari', () => {
    expect(taskOutcome({ status: 'success', result: { status: 'write_error' } }, { businessStatus: true }).phase).toBe('failure');
    expect(taskOutcome({ status: 'success', result: { status: 'partial_success' } }, { businessStatus: true }).phase).toBe('success');
  });
});

describe('pollTask — nessun "continua per sempre"', () => {
  const run = (getStatus, opts = {}) => new Promise((resolve) => {
    pollTask('t1', { interval: 0, getStatus, onDone: resolve, ...opts });
  });
  it('404 ripetuti -> lost (task non piu\' disponibile)', async () => {
    const getStatus = vi.fn().mockRejectedValue({ response: { status: 404 } });
    const o = await run(getStatus, { notFoundLimit: 3 });
    expect(o.phase).toBe('lost');
    expect(getStatus).toHaveBeenCalledTimes(3);
  });
  it('5xx ripetuti -> lost con il testo unico; un errore isolato si riprova', async () => {
    const getStatus = vi.fn()
      .mockRejectedValueOnce({ response: { status: 503, data: {} } })
      .mockResolvedValueOnce({ status: 'success', result: {} });
    expect((await run(getStatus, { errorLimit: 2 })).phase).toBe('success');
    const always = vi.fn().mockRejectedValue({ response: { status: 500, data: {} } });
    const o = await run(always, { errorLimit: 2 });
    expect(o.phase).toBe('lost');
    expect(o.error).toMatch(/errore interno/i);
  });
  it('401 e 403 fermano subito', async () => {
    expect((await run(vi.fn().mockRejectedValue({ response: { status: 403 } }))).phase).toBe('lost');
    expect((await run(vi.fn().mockRejectedValue({ response: { status: 401 } }))).phase).toBe('lost');
  });
  it('stop(): nessuna risposta dopo scrive', async () => {
    const d = deferred();
    const onUpdate = vi.fn();
    const onDone = vi.fn();
    const stop = pollTask('t1', { getStatus: () => d.promise, onUpdate, onDone });
    stop();
    d.resolve({ status: 'success', result: {} });
    await new Promise((r) => setTimeout(r, 0));
    expect(onUpdate).not.toHaveBeenCalled();
    expect(onDone).not.toHaveBeenCalled();
  });
});

describe('useBusy e useDownload', () => {
  it('busy per elemento: la fine di A non riabilita B; doppio clic sullo stesso elemento non parte', async () => {
    const a = deferred();
    const b = deferred();
    const { result } = renderHook(() => useBusy());
    let ra; let rb;
    act(() => { ra = result.current.run('A', () => a.promise); rb = result.current.run('B', () => b.promise); });
    const again = await act(async () => result.current.run('A', () => Promise.resolve('x')));
    expect(again).toEqual({ skipped: true });
    await act(async () => { a.resolve('fatto A'); await ra; });
    expect(result.current.isBusy('A')).toBe(false);
    expect(result.current.isBusy('B')).toBe(true);
    await act(async () => { b.resolve(); await rb; });
  });

  it('useDownload: un download rifiutato diventa un messaggio, non un errore perso', async () => {
    const { result } = renderHook(() => useDownload());
    await act(async () => { await result.current.run('r', () => Promise.reject({ response: { status: 410, data: {} } })); });
    expect(result.current.error).toMatch(/non è più disponibile/i);
    expect(result.current.busy).toBe(false);
  });

  it('saveBlob revoca l\'URL dopo, non subito dopo il click', () => {
    vi.useFakeTimers();
    const create = vi.fn(() => 'blob:1');
    const revoke = vi.fn();
    const origCreate = window.URL.createObjectURL;
    const origRevoke = window.URL.revokeObjectURL;
    window.URL.createObjectURL = create;
    window.URL.revokeObjectURL = revoke;
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    try {
      saveBlob(new Blob(['x']), 'report.csv');
      expect(click).toHaveBeenCalled();
      expect(revoke).not.toHaveBeenCalled();
      vi.advanceTimersByTime(REVOKE_DELAY_MS);
      expect(revoke).toHaveBeenCalledWith('blob:1');
    } finally {
      click.mockRestore();
      window.URL.createObjectURL = origCreate;
      window.URL.revokeObjectURL = origRevoke;
    }
  });
});
