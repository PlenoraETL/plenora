/**
 * Contract test — useTheme (FE-CLN-001).
 *
 * Hook condiviso per il tema dark/light. Garanzie da preservare:
 *   1. default 'dark' quando localStorage e' vuoto;
 *   2. all'init il hook applica `data-theme` su document.documentElement;
 *   3. setTheme('light') aggiorna localStorage['plenora:theme'] (chiave della
 *      tabella unica utils/browserStorage; la vecchia si sposta) e
 *      l'attributo `data-theme`;
 *   4. due consumer dello stesso hook si sincronizzano: una chiamata a
 *      setTheme da un consumer aggiorna lo state dell'altro.
 *
 * Lo stato di useTheme e' a livello di modulo (subscribers, currentTheme,
 * initialized). Per isolare i casi facciamo `vi.resetModules()` e
 * import dinamico in ogni test, e ripuliamo localStorage / data-theme.
 */

import { renderHook, act } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';

const STORAGE_KEY = 'plenora:theme';

const importFreshHook = async () => {
  vi.resetModules();
  return (await import('../../src/hooks/useTheme')).useTheme;
};

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute('data-theme');
});

describe('useTheme', () => {
  it('default "dark" quando localStorage e\' vuoto', async () => {
    const useTheme = await importFreshHook();
    const { result } = renderHook(() => useTheme());

    expect(result.current.theme).toBe('dark');
  });

  it('applica `data-theme` su document.documentElement all\'init', async () => {
    const useTheme = await importFreshHook();
    renderHook(() => useTheme());

    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
  });

  it('legge il tema persistito da localStorage', async () => {
    localStorage.setItem(STORAGE_KEY, 'light');
    const useTheme = await importFreshHook();
    const { result } = renderHook(() => useTheme());

    expect(result.current.theme).toBe('light');
    expect(document.documentElement.getAttribute('data-theme')).toBe('light');
  });

  it('setTheme("light") aggiorna localStorage e data-theme', async () => {
    const useTheme = await importFreshHook();
    const { result } = renderHook(() => useTheme());

    act(() => {
      result.current.setTheme('light');
    });

    expect(result.current.theme).toBe('light');
    expect(localStorage.getItem(STORAGE_KEY)).toBe('light');
    expect(document.documentElement.getAttribute('data-theme')).toBe('light');
  });

  it('toggleTheme alterna dark <-> light', async () => {
    const useTheme = await importFreshHook();
    const { result } = renderHook(() => useTheme());

    act(() => { result.current.toggleTheme(); });
    expect(result.current.theme).toBe('light');
    expect(localStorage.getItem(STORAGE_KEY)).toBe('light');

    act(() => { result.current.toggleTheme(); });
    expect(result.current.theme).toBe('dark');
    expect(localStorage.getItem(STORAGE_KEY)).toBe('dark');
  });

  it('due consumer si sincronizzano quando uno cambia tema', async () => {
    const useTheme = await importFreshHook();
    const a = renderHook(() => useTheme());
    const b = renderHook(() => useTheme());

    expect(a.result.current.theme).toBe('dark');
    expect(b.result.current.theme).toBe('dark');

    act(() => {
      a.result.current.setTheme('light');
    });

    expect(a.result.current.theme).toBe('light');
    expect(b.result.current.theme).toBe('light');
    expect(document.documentElement.getAttribute('data-theme')).toBe('light');
  });
});

// T3 / R5.10: storage bloccato (criteri aziendali, cookie disattivati).
describe('useTheme con localStorage bloccato', () => {
  it('non lancia: tema di default, e il cambio di tema funziona senza ricordarlo', async () => {
    const useTheme = await importFreshHook();
    const blocked = () => { throw new DOMException('blocked', 'SecurityError'); };
    const getSpy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(blocked);
    const setSpy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(blocked);
    try {
      const { result } = renderHook(() => useTheme());
      expect(result.current.theme).toBe('dark');
      act(() => { result.current.setTheme('light'); });
      expect(result.current.theme).toBe('light');
      expect(document.documentElement.getAttribute('data-theme')).toBe('light');
    } finally {
      getSpy.mockRestore();
      setSpy.mockRestore();
    }
  });
});
