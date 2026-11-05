/**
 * ModalWrapper - Componente base per tutte le modali
 * 
 * Fornisce struttura comune:
 * - Header opzionale con titolo e close button
 * - Footer opzionale con pulsanti azione
 * - Esc, overlay, focus, scroll, id del titolo e `busy`: dallo stack unico
 *   delle finestre (hooks/useDialog, utils/dialogStack)
 * 
 * @example
 * <ModalWrapper
 *   isOpen={showModal}
 *   onClose={handleClose}
 *   title="Configura Join"
 *   size="lg"
 *   footer={
 *     <>
 *       <button type="button" onClick={handleClose}>Annulla</button>
 *       <button type="button" onClick={handleConfirm}>Conferma</button>
 *     </>
 *   }
 * >
 *   {contenuto}
 * </ModalWrapper>
 */

import React from 'react';
import { createPortal } from 'react-dom';
import useDialog from '../../hooks/useDialog';
import './ModalWrapper.css';

function ModalWrapper({
  isOpen,
  onClose,
  title,
  subtitle,
  icon,
  children,
  footer,
  size = 'md',           // 'sm' | 'md' | 'lg' | 'xl' | 'full'
  variant = 'default',   // 'default' | 'estrai' | 'manipola' | 'arricchisci'
  className = '',
  showCloseButton = true,
  closeOnOverlay = true,
  closeOnEsc = true,
  // Invio in corso (Fase 5, T4): niente Esc, niente overlay, × disabilitata.
  busy = false,
  // Chiede prima di chiudere (Esc, overlay, ×): false = resta aperta.
  beforeClose = null,
  loading = false,
  loadingText = 'Caricamento...'
}) {
  // Stack unico delle finestre (hooks/useDialog): Esc, focus, overlay,
  // scroll, id del titolo.
  const dialog = useDialog({ isOpen, onClose, closeOnEsc, closeOnOverlay, busy, beforeClose, labelledBy: Boolean(title) });

  if (!isOpen) return null;

  const sizeClass = `modal-wrapper--${size}`;
  const variantClass = variant !== 'default' ? `modal-wrapper--${variant}` : '';

  return createPortal(
    <div 
      className={`modal-wrapper__overlay ${variantClass}`} 
      {...dialog.overlayProps}
    >
      <div 
        ref={dialog.ref}
        className={`modal-wrapper ${sizeClass} ${className}`}
        {...dialog.dialogProps}
      >
        {/* Header */}
        {(title || showCloseButton) && (
          <div className="modal-wrapper__header">
            <div className="modal-wrapper__title-group">
              {icon && <span className="modal-wrapper__icon">{icon}</span>}
              {title && <h3 id={dialog.titleId} className="modal-wrapper__title">{title}</h3>}
              {subtitle && <span className="modal-wrapper__subtitle">{subtitle}</span>}
            </div>
            {showCloseButton && (
              <button
                type="button"
                className="modal-wrapper__close"
                {...dialog.closeButtonProps}
              >
                ×
              </button>
            )}
          </div>
        )}

        {/* Content */}
        <div className="modal-wrapper__content">
          {loading ? (
            <div className="modal-wrapper__loading">
              <div className="modal-wrapper__spinner"></div>
              <span>{loadingText}</span>
            </div>
          ) : (
            children
          )}
        </div>

        {/* Footer */}
        {footer && (
          <div className="modal-wrapper__footer">
            {footer}
          </div>
        )}
      </div>
    </div>,
    document.body
  );
}

/**
 * Bottone standard per footer modali (live API used by ~20 modal files).
 */
export function ModalButton({
  children, 
  onClick, 
  variant = 'secondary',  // 'primary' | 'secondary' | 'danger'
  disabled = false,
  loading = false,
  type = 'button'
}) {
  return (
    <button
      type={type}
      className={`modal-btn modal-btn--${variant}`}
      onClick={onClick}
      disabled={disabled || loading}
    >
      {loading ? (
        <>
          <span className="modal-btn__spinner"></span>
          Attendere...
        </>
      ) : children}
    </button>
  );
}

export default ModalWrapper;