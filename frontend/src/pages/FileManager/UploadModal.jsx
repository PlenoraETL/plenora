/**
 * FileManager/UploadModal.jsx — modale wizard upload (3 step: File →
 * Colonne → Conferma) con supporto multi-layer (gpkg/gdb/zip) e
 * multi-sheet (xlsx).
 *
 * Estratto da FileManager.jsx (FE-PHASE1-CLOSURE-3) senza cambio
 * comportamentale. Il caller possiede tutto lo stato e i callback;
 * il modale e' completamente controlled.
 */

import React, { useRef } from 'react';
import useDialogFocus from '../../hooks/useDialogFocus';
import { formatFileSize } from './helpers';

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
  const dialogRef = useRef(null);
  // Componente montato solo quando aperto (controlled dal caller).
  useDialogFocus(true, onClose, dialogRef);
  return (
    <div className="fm__modal-overlay" onClick={onClose}>
      <div
        ref={dialogRef}
        className="fm__modal"
        onClick={e => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="fm-upload-title"
        tabIndex={-1}
      >
        <div className="fm__modal-title" id="fm-upload-title">↑ Carica File</div>
        <div className="fm__modal-sub">CSV, Excel, GeoJSON, GeoPackage, Shapefile, FileGDB</div>

        {/* Steps */}
        <div className="fm__steps">
          {['File', 'Colonne', 'Conferma'].map((label, i) => {
            const n = i + 1;
            const isDone   = step > n;
            const isActive = step === n;
            return (
              <React.Fragment key={label}>
                {i > 0 && <div className="fm__step-sep" />}
                <div className={`fm__step${isActive ? ' fm__step--active' : ''}${isDone ? ' fm__step--done' : ''}`}>
                  <div className="fm__step-num">{isDone ? '✓' : n}</div>
                  <div className="fm__step-label">{label}</div>
                </div>
              </React.Fragment>
            );
          })}
        </div>

        {/* Step 1: File */}
        {step === 1 && (
          <>
            <div
              className={`fm__modal-drop${dragActive ? ' fm__modal-drop--active' : ''}`}
              onDragEnter={onDrag} onDragOver={onDrag} onDragLeave={onDrag} onDrop={onDrop}
              onClick={() => fileInputRef.current?.click()}
            >
              <input ref={fileInputRef} type="file" style={{ display: 'none' }} onChange={onFileInput}
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
                  <div className="fm__modal-drop-sub">Max 500 MB per file</div>
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
        )}

        {/* Step 2: Columns (single file) */}
        {step === 2 && !isMultiLayerFile && (
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
                      <div key={col.name} className="fm__col-item" onClick={() => onToggleColumn(col.name)}>
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
        )}

        {/* Step 2: Multi-layer */}
        {step === 2 && isMultiLayerFile && (
          <>
            <div className="fm__modal-field">
              <label className="fm__modal-label">Layer disponibili</label>
              <div className="fm__layer-list">
                {availableLayers.map(layer => (
                  <div key={layer} className={`fm__layer-item${checkedLayers[layer] ? ' fm__layer-item--on' : ''}`} onClick={() => onLayerCheck(layer, !checkedLayers[layer])}>
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
                  <div className="fm__col-list" style={{ marginTop: 8 }}>
                    {(layerColumns[activeLayerTab] || []).map(col => (
                      <div key={col.name} className="fm__col-item" onClick={() => onToggleLayerCol(activeLayerTab, col.name)}>
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
        )}

        {uploadError && <div className="fm__modal-error">⚠️ {uploadError}</div>}

        <div className="fm__modal-actions">
          <button type="button" className="fm__modal-btn fm__modal-btn--cancel" onClick={onClose}>Annulla</button>
          {step === 1 && (
            <button type="button" className="fm__modal-btn fm__modal-btn--submit" onClick={onNext} disabled={loadingColumns}>
              {loadingColumns ? 'Lettura...' : 'Avanti →'}
            </button>
          )}
          {step === 2 && !isMultiLayerFile && (
            <>
              <button type="button" className="fm__modal-btn fm__modal-btn--ghost" onClick={onPrev}>← Indietro</button>
              <button type="button" className="fm__modal-btn fm__modal-btn--submit" onClick={onUpload} disabled={uploading}>
                {uploading ? '⏳ Caricamento...' : '↑ Carica'}
              </button>
            </>
          )}
          {step === 2 && isMultiLayerFile && (
            <>
              <button type="button" className="fm__modal-btn fm__modal-btn--ghost" onClick={onPrev}>← Indietro</button>
              <button type="button" className="fm__modal-btn fm__modal-btn--submit" onClick={onMultiUpload} disabled={uploading}>
                {uploading ? '⏳ Caricamento...' : '↑ Carica layer selezionati'}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default UploadModal;
