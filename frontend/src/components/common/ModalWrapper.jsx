/**
 * ModalWrapper - Componente base per tutte le modali
 * 
 * Fornisce struttura comune:
 * - Overlay con click per chiudere
 * - Container con stopPropagation
 * - Header opzionale con titolo e close button
 * - Footer opzionale con pulsanti azione
 * - Gestione ESC per chiudere
 * - Focus trap (accessibilità)
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

import React, { useEffect, useCallback, useRef } from 'react';
import { createPortal } from 'react-dom';
import useDialogFocus from '../../hooks/useDialogFocus';
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
  loading = false,
  loadingText = 'Caricamento...'
}) {
  const modalRef = useRef(null);

  // Focus management (initial focus, Tab trap, Escape close, restore)
  // gestito dall'hook condiviso. Sostituisce la doppia gestione ESC +
  // initial focus che era inline qui.
  useDialogFocus(isOpen, onClose, modalRef, { closeOnEsc });

  // Blocca scroll body quando modale aperta
  useEffect(() => {
    if (isOpen) {
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = '';
    }
    return () => {
      document.body.style.overflow = '';
    };
  }, [isOpen]);

  const handleOverlayClick = useCallback((e) => {
    if (closeOnOverlay && e.target === e.currentTarget) {
      onClose();
    }
  }, [closeOnOverlay, onClose]);

  if (!isOpen) return null;

  const sizeClass = `modal-wrapper--${size}`;
  const variantClass = variant !== 'default' ? `modal-wrapper--${variant}` : '';

  return createPortal(
    <div 
      className={`modal-wrapper__overlay ${variantClass}`} 
      onClick={handleOverlayClick}
    >
      <div 
        ref={modalRef}
        className={`modal-wrapper ${sizeClass} ${className}`}
        onClick={(e) => e.stopPropagation()}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby={title ? 'modal-title' : undefined}
      >
        {/* Header */}
        {(title || showCloseButton) && (
          <div className="modal-wrapper__header">
            <div className="modal-wrapper__title-group">
              {icon && <span className="modal-wrapper__icon">{icon}</span>}
              {title && <h3 id="modal-title" className="modal-wrapper__title">{title}</h3>}
              {subtitle && <span className="modal-wrapper__subtitle">{subtitle}</span>}
            </div>
            {showCloseButton && (
              <button type="button" 
                className="modal-wrapper__close" 
                onClick={onClose}
                aria-label="Chiudi"
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