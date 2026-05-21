/**
 * useMultiModal - Hook per gestire multiple modali
 * 
 * Semplifica la gestione di più modali in un componente,
 * evitando di dichiarare useState per ogni modale.
 * 
 * @example
 * const modals = useMultiModal(['catalog', 'template', 'settings']);
 * 
 * modals.open('catalog');      // Apre modale catalog
 * modals.close('catalog');     // Chiude modale catalog
 * modals.isOpen('catalog');    // true/false
 * modals.toggle('catalog');    // Toggle
 * modals.closeAll();           // Chiude tutte
 */

import { useState, useCallback, useMemo } from 'react';

/**
 * Hook per gestire multiple modali
 * @param {string[]} modalNames - Array dei nomi delle modali
 * @returns {Object} Oggetto con metodi per gestire le modali
 */
export function useMultiModal(modalNames = []) {
  // Stato iniziale: tutte le modali chiuse.
  // Pre-fix il `useMemo` aveva deps `[]`: catturava solo il primo
  // render. Se il caller passava un array diverso in render successivi
  // (es. feature flag condizionale che aggiunge un modale), il nuovo
  // nome non veniva inizializzato → `isOpen('newModal')` ritornava
  // `undefined → false` ma `open('newModal')` produceva uno state
  // shape incompleto.
  //
  // Soluzione: dep stabilizzata come join sui nomi (string primitiva,
  // confronto referenziale e' OK su Reacto identity). Cambio reale del
  // set di modali → recompute; identity-only change → no-op.
  const namesKey = modalNames.join(',');
  const initialState = useMemo(() => {
    return modalNames.reduce((acc, name) => {
      acc[name] = false;
      return acc;
    }, {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
    // namesKey copre la semantica di `modalNames` come dep — usarlo
    // direttamente eviterebbe il recompute ad ogni nuova array
    // identity (modalNames literal inline = nuovo array per render).
  }, [namesKey]);

  const [modals, setModals] = useState(initialState);

  /**
   * Apre una modale specifica
   */
  const open = useCallback((name) => {
    setModals(prev => ({ ...prev, [name]: true }));
  }, []);

  /**
   * Chiude una modale specifica
   */
  const close = useCallback((name) => {
    setModals(prev => ({ ...prev, [name]: false }));
  }, []);

  /**
   * Toggle di una modale
   */
  const toggle = useCallback((name) => {
    setModals(prev => ({ ...prev, [name]: !prev[name] }));
  }, []);

  /**
   * Verifica se una modale è aperta
   */
  const isOpen = useCallback((name) => {
    return modals[name] || false;
  }, [modals]);

  /**
   * Chiude tutte le modali
   */
  const closeAll = useCallback(() => {
    setModals(initialState);
  }, [initialState]);

  /**
   * Apre una modale e chiude tutte le altre
   */
  const openExclusive = useCallback((name) => {
    const newState = { ...initialState, [name]: true };
    setModals(newState);
  }, [initialState]);

  return {
    open,
    close,
    toggle,
    isOpen,
    closeAll,
    openExclusive,
    // Accesso diretto allo stato (per debug)
    state: modals
  };
}

export default useMultiModal;
