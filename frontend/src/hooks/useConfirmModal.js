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

import { useState, useCallback, useRef } from 'react';

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
  }, []);

  const handleCancel = useCallback(() => {
    setIsOpen(false);
    resolveRef.current?.(false);
  }, []);

  return { isOpen, config, confirm, handleConfirm, handleCancel };
}
