/**
 * useTheme — hook condiviso per il tema dark/light.
 *
 * Comportamento:
 *   - default: 'dark'
 *   - persiste su localStorage['plenora-theme']
 *   - applica `data-theme` su document.documentElement
 *   - mantiene aggiornato lo stato React di TUTTI i consumer:
 *     un cambio di tema fatto da una qualsiasi istanza viene
 *     propagato a ogni altro componente che usa `useTheme()`,
 *     senza bisogno di MutationObserver locali.
 *
 * API:
 *   const { theme, setTheme, toggleTheme } = useTheme();
 */
import { useState, useEffect, useCallback } from 'react';

const STORAGE_KEY = 'plenora-theme';
const DEFAULT_THEME = 'dark';

const subscribers = new Set();
let initialized = false;
let currentTheme = DEFAULT_THEME;

// R5.10: con lo storage bloccato (criteri aziendali, cookie disattivati)
// getItem/setItem lanciano. Il tema funziona lo stesso, solo non si ricorda.
const readStoredTheme = () => {
  try {
    const v = window.localStorage.getItem(STORAGE_KEY);
    return v === 'light' || v === 'dark' ? v : null;
  } catch {
    return null;
  }
};

const storeTheme = (t) => {
  try { window.localStorage.setItem(STORAGE_KEY, t); } catch { /* non persistito */ }
};

const applyTheme = (t) => {
  document.documentElement.setAttribute('data-theme', t);
};

const ensureInit = () => {
  if (!initialized) {
    initialized = true;
    currentTheme = readStoredTheme() || DEFAULT_THEME;
    applyTheme(currentTheme);
  }
  return currentTheme;
};

const broadcast = (t) => {
  currentTheme = t;
  subscribers.forEach((fn) => fn(t));
};

const writeTheme = (t) => {
  applyTheme(t);
  storeTheme(t);
  broadcast(t);
};

export function useTheme() {
  const [theme, setLocal] = useState(ensureInit);

  useEffect(() => {
    subscribers.add(setLocal);
    if (theme !== currentTheme) setLocal(currentTheme);
    return () => { subscribers.delete(setLocal); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const setTheme = useCallback((t) => { writeTheme(t); }, []);
  const toggleTheme = useCallback(() => {
    writeTheme(currentTheme === 'dark' ? 'light' : 'dark');
  }, []);

  return { theme, setTheme, toggleTheme };
}

export default useTheme;
