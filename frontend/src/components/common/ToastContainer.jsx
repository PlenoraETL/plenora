/**
 * ToastContainer — Renderizza le notifiche toast a schermo
 *
 * Posizionato in basso a destra, fuori dal flusso normale del DOM.
 * Da usare nel componente root di ogni pagina/editor.
 *
 * Uso:
 *   const toast = useToast();
 *   ...
 *   <ToastContainer toasts={toast.toasts} onDismiss={toast.dismiss} />
 */

import React from 'react';
import './ToastContainer.css';

const ICONS = {
  success: '✅',
  error:   '❌',
  warning: '⚠️',
  info:    'ℹ️',
};

export default function ToastContainer({ toasts = [], onDismiss }) {
  if (toasts.length === 0) return null;

  return (
    <div className="toast-container" role="region" aria-label="Notifiche">
      {toasts.map(toast => (
        <div
          key={toast.id}
          className={`toast toast--${toast.type}`}
          role="alert"
        >
          <span className="toast__icon">{ICONS[toast.type] || ICONS.info}</span>
          <span className="toast__message">{toast.message}</span>
          <button type="button"
            className="toast__close"
            onClick={() => onDismiss(toast.id)}
            aria-label="Chiudi notifica"
          >
            ×
          </button>
        </div>
      ))}
    </div>
  );
}
