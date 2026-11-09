/**
 * ConfirmDialog - Dialog di conferma riutilizzabile
 * 
 * Usato per conferme di eliminazione, azioni distruttive, etc.
 * 
 * Uso semplice:
 *   const confirmDelete = useConfirmModal();
 *   const handleDelete = async () => {
 *     if (await confirmDelete.confirm({ title: 'Elimina?', variant: 'danger' })) {
 *       // procedi con eliminazione
 *     }
 *   };
 *   <ConfirmDialog {...confirmDelete} />
 */

import React from 'react';
import ModalWrapper from './ModalWrapper';
import './ConfirmDialog.css';

function ConfirmDialog({
  isOpen,
  config = {},
  handleConfirm,
  handleCancel
}) {
  const {
    title = 'Conferma',
    message = 'Sei sicuro di voler procedere?',
    confirmText = 'Conferma',
    cancelText = 'Annulla',
    variant = 'default', // 'default' | 'danger' | 'warning'
    icon = null,
    // Elenco o dettaglio sotto il messaggio (blocchi, fuori dal <p>).
    details = null
  } = config;

  const getIcon = () => {
    if (icon) return icon;
    
    switch (variant) {
      case 'danger':
        return '⚠️';
      case 'warning':
        return '⚡';
      default:
        return '❓';
    }
  };

  return (
    <ModalWrapper
      isOpen={isOpen}
      onClose={handleCancel}
      title={title}
      size="sm"
      showCloseButton={false}
      footer={
        <div className="confirm-dialog__actions">
          <button type="button" 
            className="confirm-dialog__btn confirm-dialog__btn--cancel"
            onClick={handleCancel}
          >
            {cancelText}
          </button>
          <button type="button" 
            className={`confirm-dialog__btn confirm-dialog__btn--${variant}`}
            onClick={handleConfirm}
          >
            {confirmText}
          </button>
        </div>
      }
    >
      <div className="confirm-dialog__content">
        <span className="confirm-dialog__icon">{getIcon()}</span>
        <p className="confirm-dialog__message">{message}</p>
        {details && <div className="confirm-dialog__details">{details}</div>}
      </div>
    </ModalWrapper>
  );
}

export default ConfirmDialog;
