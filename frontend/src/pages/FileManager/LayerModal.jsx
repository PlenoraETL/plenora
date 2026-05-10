/**
 * FileManager/LayerModal.jsx — modale di selezione layer/sheet per file
 * multi-layer (gpkg/gdb) o multi-sheet (xlsx) — flusso "carica un solo
 * layer/sheet" come scorciatoia rispetto al wizard multi-layer di
 * UploadModal.
 */

import React from 'react';
import ModalWrapper, { ModalButton } from '../../components/common/ModalWrapper';

function LayerModal({ pendingFile, availableLayers, availableSheets, selectedLayer, selectedSheet, onLayerChange, onSheetChange, onConfirm, onClose }) {
  const isSheet = availableSheets.length > 0;
  const items   = isSheet ? availableSheets : availableLayers;
  const value   = isSheet ? selectedSheet : selectedLayer;
  const onChange = isSheet ? onSheetChange : onLayerChange;
  return (
    <ModalWrapper
      isOpen={true}
      onClose={onClose}
      title={`Seleziona ${isSheet ? 'Sheet' : 'Layer'}`}
      subtitle={pendingFile?.name}
      size="md"
      footer={
        <>
          <ModalButton variant="secondary" onClick={onClose}>Annulla</ModalButton>
          <ModalButton variant="primary" onClick={onConfirm} disabled={!value}>Conferma</ModalButton>
        </>
      }
    >
      <div className="fm__modal-field">
        <label className="fm__modal-label">{isSheet ? 'Sheet disponibili' : 'Layer disponibili'}</label>
        <div className="fm__col-list">
          {items.map(item => (
            <div key={item} className="fm__col-item" onClick={() => onChange(item)}>
              <div className={`fm__col-check${value === item ? ' fm__col-check--on' : ''}`}>{value === item ? '✓' : ''}</div>
              <span className="fm__col-name">{item}</span>
            </div>
          ))}
        </div>
      </div>
    </ModalWrapper>
  );
}

export default LayerModal;
