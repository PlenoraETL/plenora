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

import React, { createContext, useContext, useState, useCallback, useEffect, useMemo, useRef } from 'react';
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
  // timersRef tiene i setTimeout id per cancellarli su unmount: pre-fix
  // il setTimeout dentro addToast non era cancellabile; se il provider
  // si smontava (mai in prod ma si' in HMR/test), il timer continuava
  // a chiamare setToasts su un componente smontato → warning React +
  // memory leak fra hot reloads.
  const timersRef = useRef(new Set());

  const addToast = useCallback((message, type = 'info', duration = 4000) => {
    const id = ++_nextId;
    setToasts(prev => [...prev, { id, message, type }]);

    if (duration > 0) {
      const timerId = setTimeout(() => {
        timersRef.current.delete(timerId);
        setToasts(prev => prev.filter(t => t.id !== id));
      }, duration);
      timersRef.current.add(timerId);
    }

    return id;
  }, []);

  const dismiss = useCallback((id) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  }, []);

  // Cleanup al unmount del provider: cancella tutti i timer pending.
  useEffect(() => {
    const timers = timersRef.current;
    return () => {
      for (const timerId of timers) clearTimeout(timerId);
      timers.clear();
    };
  }, []);

  const success = useCallback((msg, duration)        => addToast(msg, 'success', duration),        [addToast]);
  const error   = useCallback((msg, duration)        => addToast(msg, 'error',   duration ?? 6000), [addToast]);
  const warning = useCallback((msg, duration)        => addToast(msg, 'warning', duration),        [addToast]);
  const info    = useCallback((msg, duration)        => addToast(msg, 'info',    duration),        [addToast]);

  // Il context espone SOLO azioni stabili: l'identita' di `useToast()` non
  // cambia mai dopo il mount. I consumer fanno `useCallback(..., [toast])`
  // e `useEffect(load, [load])`: se `value` dipendesse da `toasts`, ogni
  // toast mostrato/scaduto rigenererebbe i loader -> nuovo fetch -> nuovo
  // toast (loop infinito su errore; audit 03/11/2026 R5.1). La lista dei
  // toast resta interna al provider e arriva solo al ToastContainer.
  const value = useMemo(
    () => ({ success, error, warning, info, dismiss }),
    [success, error, warning, info, dismiss],
  );

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
