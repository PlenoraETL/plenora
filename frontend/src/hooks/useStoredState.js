/**
 * useStoredState — stato di layout ricordato in questo browser (UI-4, U26):
 * barra laterale chiusa o aperta, larghezza dei pannelli. Preferenza del
 * dispositivo, non dell'account (come il tema, hooks/useTheme).
 *
 * localStorage puo' mancare o lanciare (finestra privata, dati bloccati):
 * ogni lettura e scrittura e' protetta e senza memoria lo stato vale per la
 * sola pagina. `normalize` riporta un valore letto nei limiti ammessi.
 *
 * Le chiavi sono in LAYOUT_KEYS: una sola tabella, prefisso unico.
 */
import { useCallback, useState } from 'react';

const identity = (v) => v;

export const LAYOUT_KEYS = Object.freeze({
  appSidebarCollapsed: 'plenora-layout:app-sidebar-collapsed',
  editorSidebarCollapsed: 'plenora-layout:editor-sidebar-collapsed',
  editorPanelWidth: 'plenora-layout:editor-panel-width',
});

function read(key, fallback) {
  try {
    const raw = window.localStorage.getItem(key);
    return raw == null ? fallback : JSON.parse(raw);
  } catch {
    return fallback;
  }
}

function write(key, value) {
  try { window.localStorage.setItem(key, JSON.stringify(value)); } catch { /* non ricordato */ }
}

export function useStoredState(key, fallback, normalize = identity) {
  const [value, setValue] = useState(() => {
    const v = normalize(read(key, fallback));
    return v === undefined ? fallback : v;
  });
  const update = useCallback((next) => {
    setValue((prev) => {
      const v = normalize(typeof next === 'function' ? next(prev) : next);
      write(key, v);
      return v;
    });
  }, [key, normalize]);
  return [value, update];
}

export default useStoredState;
