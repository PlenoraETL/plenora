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
  // Stato iniziale: tutte le modali chiuse
  const initialState = useMemo(() => {
    return modalNames.reduce((acc, name) => {
      acc[name] = false;
      return acc;
    }, {});
  }, []);

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
