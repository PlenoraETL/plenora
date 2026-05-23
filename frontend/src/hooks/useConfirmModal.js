/**
 * useConfirmModal — Hook per gestire ConfirmDialog in modo promise-based
 *
 * Uso:
 *   const confirmModal = useConfirmModal();
 *
 *   const ok = await confirmModal.confirm({
 *     title: 'Elimina file',
 *     message: 'Sei sicuro?',
 *     confirmText: 'Elimina',
 *     variant: 'danger',
 *   });
 *   if (ok) { ... }
 *
 *   // Nel JSX:
 *   <ConfirmDialog {...confirmModal} />
 */

import { useState, useCallback, useRef, useEffect } from 'react';

export function useConfirmModal() {
  const [isOpen,  setIsOpen]  = useState(false);
  const [config,  setConfig]  = useState({});
  const resolveRef = useRef(null);

  const confirm = useCallback((cfg = {}) => {
    setConfig(cfg);
    setIsOpen(true);
    return new Promise(resolve => { resolveRef.current = resolve; });
  }, []);

  const handleConfirm = useCallback(() => {
    setIsOpen(false);
    resolveRef.current?.(true);
    resolveRef.current = null;
  }, []);

  const handleCancel = useCallback(() => {
    setIsOpen(false);
    resolveRef.current?.(false);
    resolveRef.current = null;
  }, []);

  // Pre-fix (P2 frontend-hooks): se il componente che ospita
  // ConfirmDialog veniva smontato mentre il modal era aperto (es.
  // navigazione client-side via React Router), `resolveRef.current`
  // non veniva mai chiamato → la Promise restava pending. Caller in
  // useCallback che facevano `await confirmModal.confirm(...)` poi
  // setState mantenevano la closure viva. Post-fix: cleanup
  // su unmount risolve con `false` (semantica "annullato") e azzera
  // il ref per consentire al GC di liberare la closure caller.
  useEffect(() => () => {
    resolveRef.current?.(false);
    resolveRef.current = null;
  }, []);

  return { isOpen, config, confirm, handleConfirm, handleCancel };
}
