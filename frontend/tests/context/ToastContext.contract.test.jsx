import React, { useEffect } from 'react';
import { act, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ToastProvider, useToast } from '../../src/context/ToastContext';

/**
 * Contract R5.1 (audit 03/11/2026): l'identita' di useToast() e' stabile.
 *
 * Le pagine fanno `const load = useCallback(..., [toast])` +
 * `useEffect(() => load(), [load])`. Se l'oggetto restituito cambiasse a
 * ogni toast, ogni toast.error rifarebbe il fetch -> nuovo errore -> nuovo
 * toast: loop infinito. Il test simula esattamente quel consumer.
 */

function Loader({ onLoad }) {
  const toast = useToast();
  useEffect(() => {
    onLoad();
    // Tetto di sicurezza: con il bug il loop sarebbe infinito; cosi' il
    // test fallisce sul conteggio invece di bloccarsi.
    if (onLoad.mock.calls.length <= 3) toast.error('Caricamento fallito');
  }, [toast, onLoad]);
  return null;
}

describe('ToastContext identity contract', () => {
  it('a toast does not change useToast() identity (no reload loop)', () => {
    vi.useFakeTimers();
    const onLoad = vi.fn();
    render(<ToastProvider><Loader onLoad={onLoad} /></ToastProvider>);
    expect(screen.getByText('Caricamento fallito')).toBeTruthy();
    act(() => { vi.advanceTimersByTime(10_000); });
    expect(onLoad).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });

  it('exposes only actions, never the toast list', () => {
    let api;
    function Probe() { api = useToast(); return null; }
    render(<ToastProvider><Probe /></ToastProvider>);
    expect(Object.keys(api).sort()).toEqual(['dismiss', 'error', 'info', 'success', 'warning']);
  });
});
