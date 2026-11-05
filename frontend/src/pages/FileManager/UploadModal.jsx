/**
 * FileManager/UploadModal.jsx — modale wizard upload (3 step: File →
 * Colonne → Conferma) con supporto multi-layer (gpkg/gdb/zip) e
 * multi-sheet (xlsx).
 *
 * Il caller possiede tutto lo stato e i callback; il modale e'
 * completamente controlled. Il CHROME (overlay/dialog/step-bar/a11y) e'
 * delegato a `common/Wizard`; la navigazione e' side-effectful (Avanti
 * carica le colonne) e l'azione finale dipende da multi-layer, quindi il
 * footer e' fornito come override (allowJump=false).
 */

import React from 'react';
import { MAX_UPLOAD_SIZE_BYTES } from '../../api/uploadGuards';
import Wizard from '../../components/common/Wizard';
import { formatBytes as formatFileSize } from '../../utils/format';
import { pressable } from '../../utils/pressable';

function UploadModal({
  step, uploadFile, uploadName, uploadDescription, uploading, uploadError, dragActive,
  availableColumns, selectedColumns, loadingColumns, detectedSrid, userSrid, hasGeometryColumn,
  isMultiLayerFile, availableLayers, checkedLayers, layerColumns, layerSelectedCols,
  activeLayerTab, loadingLayerCols, fileInputRef,
  onClose, onDrag, onDrop, onFileInput, onNameChange, onDescChange,
  onNext, onPrev, onUpload, onMultiUpload,
  onToggleColumn, onSelectAll, onUserSridChange,
  onLayerCheck, onLayerTabChange, onToggleLayerCol, onToggleAllLayerCols,
}) {
  const step1Content = (
    <>
      <div
        className={`fm__modal-drop${dragActive ? ' fm__modal-drop--active' : ''}`}
        onDragEnter={onDrag} onDragOver={onDrag} onDragLeave={onDrag} onDrop={onDrop}
        {...pressable(() => fileInputRef.current?.click())}
      >
        <input ref={fileInputRef} type="file" className="fm__file-input--hidden" onChange={onFileInput}
          accept=".csv,.xlsx,.xls,.json,.geojson,.gpkg,.zip,.gdb,.tar.gz" />
        {uploadFile ? (
          <>
            <div className="fm__modal-drop-icon">✓</div>
            <div className="fm__modal-drop-file">{uploadFile.name}</div>
            <div className="fm__modal-drop-sub">{formatFileSize(uploadFile.size)} · Clicca per cambiare</div>
          </>
        ) : (
          <>
            <div className="fm__modal-drop-icon">↑</div>
            <div className="fm__modal-drop-text">Trascina qui il file oppure clicca</div>
            <div className="fm__modal-drop-sub">
              Max {formatFileSize(MAX_UPLOAD_SIZE_BYTES)} per file
            </div>
          </>
        )}
      </div>
      <div className="fm__modal-field">
        <label className="fm__modal-label">Nome *</label>
        <input type="text" className="fm__modal-input" value={uploadName} onChange={e => onNameChange(e.target.value)} placeholder="Nome descrittivo del file" autoFocus />
      </div>
      <div className="fm__modal-field">
        <label className="fm__modal-label">Descrizione</label>
        <input type="text" className="fm__modal-input" value={uploadDescription} onChange={e => onDescChange(e.target.value)} placeholder="Descrizione opzionale..." />
      </div>
    </>
  );

  const step2Single = (
    <>
      {loadingColumns ? (
        <div className="fm__modal-loading">⏳ Lettura colonne...</div>
      ) : (
        <>
          {hasGeometryColumn && (
            <div className="fm__modal-field">
              <label className="fm__modal-label">SRID / CRS {detectedSrid && <span className="fm__modal-hint-inline">(rilevato: EPSG:{detectedSrid})</span>}</label>
              <input type="text" className="fm__modal-input" value={userSrid} onChange={e => onUserSridChange(e.target.value)} placeholder="Es. 4326, 32633..." />
            </div>
          )}
          <div className="fm__modal-field">
            <div className="fm__col-header">
              <label className="fm__modal-label">Seleziona colonne ({selectedColumns.length}/{availableColumns.length})</label>
              <button type="button" className="fm__col-toggle-all" onClick={onSelectAll}>
                {selectedColumns.length === availableColumns.length ? 'Deseleziona tutte' : 'Seleziona tutte'}
              </button>
            </div>
            <div className="fm__col-list">
              {availableColumns.map(col => (
                <div key={col.name} className="fm__col-item" {...pressable(() => onToggleColumn(col.name))}>
                  <div className={`fm__col-check${selectedColumns.includes(col.name) ? ' fm__col-check--on' : ''}`}>{selectedColumns.includes(col.name) ? '✓' : ''}</div>
                  <span className="fm__col-name">{col.name}</span>
                  <span className="fm__col-type">{col.type}</span>
                </div>
              ))}
            </div>
          </div>
        </>
      )}
    </>
  );

  const step2Multi = (
    <>
      <div className="fm__modal-field">
        <label className="fm__modal-label">Layer disponibili</label>
        <div className="fm__layer-list">
          {availableLayers.map(layer => (
            <div key={layer} className={`fm__layer-item${checkedLayers[layer] ? ' fm__layer-item--on' : ''}`} {...pressable(() => onLayerCheck(layer, !checkedLayers[layer]))}>
              <div className={`fm__col-check${checkedLayers[layer] ? ' fm__col-check--on' : ''}`}>{checkedLayers[layer] ? '✓' : ''}</div>
              <span className="fm__col-name">{layer}</span>
              {layerSelectedCols[layer]?.length > 0 && <span className="fm__col-type">{layerSelectedCols[layer].length} col.</span>}
            </div>
          ))}
        </div>
      </div>
      {activeLayerTab && (
        <div className="fm__modal-field">
          <div className="fm__layer-tabs">
            {availableLayers.filter(l => checkedLayers[l]).map(l => (
              <button key={l} type="button" className={`fm__layer-tab${activeLayerTab === l ? ' fm__layer-tab--active' : ''}`} onClick={() => onLayerTabChange(l)}>{l}</button>
            ))}
          </div>
          {loadingLayerCols ? (
            <div className="fm__modal-loading">⏳ Lettura colonne...</div>
          ) : (
            <div className="fm__col-list fm__col-list--mt-sm">
              {(layerColumns[activeLayerTab] || []).map(col => (
                <div key={col.name} className="fm__col-item" {...pressable(() => onToggleLayerCol(activeLayerTab, col.name))}>
                  <div className={`fm__col-check${layerSelectedCols[activeLayerTab]?.includes(col.name) ? ' fm__col-check--on' : ''}`}>
                    {layerSelectedCols[activeLayerTab]?.includes(col.name) ? '✓' : ''}
                  </div>
                  <span className="fm__col-name">{col.name}</span>
                  <span className="fm__col-type">{col.type}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </>
  );

  const errorBox = uploadError ? <div className="fm__modal-error">⚠️ {uploadError}</div> : null;

  const steps = [
    { label: 'File', content: <>{step1Content}{errorBox}</> },
    { label: 'Colonne', content: <>{isMultiLayerFile ? step2Multi : step2Single}{errorBox}</> },
  ];

  // Annulla come × ed Esc: con un file scelto chiede (regola del Wizard).
  const footer = ({ requestClose }) => (
    <>
      <button type="button" className="fm__modal-btn fm__modal-btn--cancel" onClick={requestClose} disabled={uploading}>Annulla</button>
      {step === 1 && (
        <button type="button" className="fm__modal-btn fm__modal-btn--submit" onClick={onNext} disabled={loadingColumns}>
          {loadingColumns ? 'Lettura...' : 'Avanti →'}
        </button>
      )}
      {step === 2 && !isMultiLayerFile && (
        <>
          <button type="button" className="fm__modal-btn fm__modal-btn--ghost" onClick={onPrev} disabled={uploading}>← Indietro</button>
          <button type="button" className="fm__modal-btn fm__modal-btn--submit" onClick={onUpload} disabled={uploading}>
            {uploading ? '⏳ Caricamento...' : '↑ Carica'}
          </button>
        </>
      )}
      {step === 2 && isMultiLayerFile && (
        <>
          <button type="button" className="fm__modal-btn fm__modal-btn--ghost" onClick={onPrev} disabled={uploading}>← Indietro</button>
          <button type="button" className="fm__modal-btn fm__modal-btn--submit" onClick={onMultiUpload} disabled={uploading}>
            {uploading ? '⏳ Caricamento...' : '↑ Carica layer selezionati'}
          </button>
        </>
      )}
    </>
  );

  return (
    <Wizard
      isOpen
      onClose={onClose}
      title="↑ Carica File"
      subtitle="CSV, Excel, GeoJSON, GeoPackage, Shapefile, FileGDB"
      step={Math.max(0, step - 1)}
      onStepChange={() => {}}
      allowJump={false}
      busy={uploading}
      steps={steps}
      footer={footer}
    />
  );
}

export default UploadModal;
