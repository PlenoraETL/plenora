/**
 * ToastContext.jsx — Toast globale per tutta l'applicazione
 *
 * PRIMA: ogni pagina aveva il proprio useToast() + <ToastContainer> in fondo al JSX.
 *   12 istanze separate, ognuna con stato isolato → i toast di un modulo non
 *   erano visibili se il componente veniva smontato durante la navigazione.
 *
 * ORA: un solo provider in App.jsx gestisce tutti i toast dell'applicazione.
 *   Un solo <ToastContainer> montato sempre, indipendentemente dalla route.
 *
 * API invariata: i consumer continuano a fare
 *   const toast = useToast();
 *   toast.success('Salvato!');
 *
 * La differenza è che useToast() ora legge dallo stesso stato globale invece
 * di creare stato locale isolato.
 *
 * Migration checklist per ogni pagina:
 *   ✅ Rimuovere: import ToastContainer from '...'
 *   ✅ Rimuovere: <ToastContainer toasts={toast.toasts} onDismiss={toast.dismiss} />
 *   ✅ L'import di useToast rimane invariato (re-exported da qui)
 */

import React, { createContext, useContext, useState, useCallback } from 'react';
import { createPortal } from 'react-dom';
import ToastContainer from '../components/common/ToastContainer';
let _nextId = 0;
const ToastContext = createContext(null);
/**
 * ToastProvider — da inserire una volta sola in App.jsx, dentro BrowserRouter.
 * Renderizza <ToastContainer> internamente: le pagine non devono più farlo.
 */
export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);

  const addToast = useCallback((message, type = 'info', duration = 4000) => {
    const id = ++_nextId;
    setToasts(prev => [...prev, { id, message, type }]);

    if (duration > 0) {
      setTimeout(() => {
        setToasts(prev => prev.filter(t => t.id !== id));
      }, duration);
    }

    return id;
  }, []);

  const dismiss = useCallback((id) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  }, []);

  const success = useCallback((msg, duration)        => addToast(msg, 'success', duration),        [addToast]);
  const error   = useCallback((msg, duration)        => addToast(msg, 'error',   duration ?? 6000), [addToast]);
  const warning = useCallback((msg, duration)        => addToast(msg, 'warning', duration),        [addToast]);
  const info    = useCallback((msg, duration)        => addToast(msg, 'info',    duration),        [addToast]);

  const value = { toasts, success, error, warning, info, dismiss };

  return (
    <ToastContext.Provider value={value}>
      {children}
      {/* Un solo ToastContainer per tutta l'app, montato via portal su
          document.body per uscire da qualsiasi stacking context parent
          (transform/filter/will-change/contain che bloccherebbe il z-index). */}
      {typeof document !== 'undefined' && createPortal(
        <ToastContainer toasts={toasts} onDismiss={dismiss} />,
        document.body,
      )}
    </ToastContext.Provider>
  );
}
/**
 * useToast() — API invariata rispetto alla versione locale.
 * Lancia se usato fuori da <ToastProvider>.
 */
export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    throw new Error('useToast deve essere usato dentro <ToastProvider>');
  }
  return ctx;
}
