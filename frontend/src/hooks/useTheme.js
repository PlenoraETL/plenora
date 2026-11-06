/**
 * useTheme — hook condiviso per il tema dark/light.
 *
 * Comportamento:
 *   - default: 'dark'
 *   - persiste su localStorage (chiave `theme` di utils/browserStorage;
 *     la vecchia 'plenora-theme' si sposta alla prima lettura)
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
import { STORAGE_KEYS, migrateLegacy, readRaw, storageKey, writeRaw } from '../utils/browserStorage';

const STORAGE_KEY = storageKey('theme');
const DEFAULT_THEME = 'dark';

const subscribers = new Set();
let initialized = false;
let currentTheme = DEFAULT_THEME;

// R5.10: con lo storage bloccato (criteri aziendali, cookie disattivati)
// getItem/setItem lanciano. Il tema funziona lo stesso, solo non si ricorda.
const readStoredTheme = () => {
  migrateLegacy('theme', STORAGE_KEY, STORAGE_KEYS.theme.legacy);
  const v = readRaw('theme', STORAGE_KEY);
  return v === 'light' || v === 'dark' ? v : null;
};

const storeTheme = (t) => { writeRaw('theme', STORAGE_KEY, t); };

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
