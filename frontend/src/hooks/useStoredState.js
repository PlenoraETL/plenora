/**
 * useStoredState — stato di layout ricordato in questo browser (UI-4, U26):
 * barra laterale chiusa o aperta, larghezza dei pannelli. Preferenza del
 * dispositivo, non dell'account (come il tema, hooks/useTheme).
 *
 * localStorage puo' mancare o lanciare (finestra privata, dati bloccati):
 * ogni lettura e scrittura e' protetta e senza memoria lo stato vale per la
 * sola pagina. `normalize` riporta un valore letto nei limiti ammessi.
 *
 * Le chiavi sono in LAYOUT_KEYS, sotto la riga `layout` della tabella unica
 * utils/browserStorage (conferma finale G8: prefisso `plenora:layout:`; le
 * chiavi `plenora-layout:` di prima si spostano alla prima lettura).
 */
import { useCallback, useState } from 'react';
import { STORAGE_KEYS, migrateLegacy, readJson, storageKey, writeJson } from '../utils/browserStorage';

const identity = (v) => v;

export const LAYOUT_KEYS = Object.freeze({
  appSidebarCollapsed: storageKey('layout', 'app-sidebar-collapsed'),
  editorSidebarCollapsed: storageKey('layout', 'editor-sidebar-collapsed'),
  editorPanelWidth: storageKey('layout', 'editor-panel-width'),
});

const { prefix, legacyPrefix } = STORAGE_KEYS.layout;

function read(key, fallback) {
  if (key.startsWith(prefix)) migrateLegacy('layout', key, [`${legacyPrefix}${key.slice(prefix.length)}`]);
  return readJson('layout', key, fallback);
}

function write(key, value) {
  writeJson('layout', key, value);
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
