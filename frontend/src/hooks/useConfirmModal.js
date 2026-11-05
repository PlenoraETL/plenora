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

/**
 * R6.9: le richieste sono in CODA. Una seconda `confirm()` mentre la prima
 * e' aperta non la sovrascrive: si mostra dopo, e ogni promise ha la sua
 * risposta (prima la promise rimaneva pending per sempre).
 */
export function useConfirmModal() {
  const queueRef = useRef([]); // [{ cfg, resolve }]
  const [head, setHead] = useState(null);

  const showHead = useCallback(() => {
    setHead(queueRef.current[0] || null);
  }, []);

  const confirm = useCallback((cfg = {}) => new Promise((resolve) => {
    queueRef.current.push({ cfg, resolve });
    if (queueRef.current.length === 1) showHead();
  }), [showHead]);

  const settle = useCallback((value) => {
    const current = queueRef.current.shift();
    current?.resolve(value);
    showHead();
  }, [showHead]);

  const handleConfirm = useCallback(() => settle(true), [settle]);
  const handleCancel = useCallback(() => settle(false), [settle]);

  // Smontato con richieste aperte (navigazione): tutte annullate, nessuna
  // promise resta pending (P2 frontend-hooks).
  useEffect(() => () => {
    const pending = queueRef.current;
    queueRef.current = [];
    pending.forEach(p => p.resolve(false));
  }, []);

  return {
    isOpen: Boolean(head),
    config: head?.cfg || {},
    confirm,
    handleConfirm,
    handleCancel,
  };
}
