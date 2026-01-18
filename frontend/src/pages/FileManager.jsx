import React, { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { 
  getFiles, 
  uploadFile, 
  deleteFile, 
  getFileLayers, 
  getFilePreview,
  previewFileTemp
} from '../api/files';
import '../styles/FileManager.css';

/**
 * FileManager - Gestione file esterni
 * 
 * Permette di:
 * - Caricare nuovi file (CSV, Excel, GeoJSON, GDB, etc.)
 * - Visualizzare file caricati
 * - Selezionare layer/sheet per file multi-layer
 * - Eliminare file
 */
const FileManager = () => {
  const navigate = useNavigate();
  
  // State
  const [files, setFiles] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  
  // Modal state
  const [showUploadModal, setShowUploadModal] = useState(false);
  const [showLayerModal, setShowLayerModal] = useState(false);
  const [showPreviewModal, setShowPreviewModal] = useState(false);
  
  // Upload state
  const [uploadFile_, setUploadFile] = useState(null);
  const [uploadName, setUploadName] = useState('');
  const [uploadDescription, setUploadDescription] = useState('');
  const [uploadLayer, setUploadLayer] = useState('');
  const [uploadSheet, setUploadSheet] = useState('');
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState(null);
  const [dragActive, setDragActive] = useState(false);
  
  // Step selezione colonne
  const [uploadStep, setUploadStep] = useState(1); // 1 = file, 2 = colonne
  const [availableColumns, setAvailableColumns] = useState([]);
  const [selectedColumns, setSelectedColumns] = useState([]);
  const [loadingColumns, setLoadingColumns] = useState(false);
  const [detectedSrid, setDetectedSrid] = useState(null); // SRID rilevato dal file
  const [userSrid, setUserSrid] = useState(''); // SRID impostato dall'utente
  const [hasGeometryColumn, setHasGeometryColumn] = useState(false); // Se c'è una colonna geometry
  
  // Layer selection state
  const [pendingFile, setPendingFile] = useState(null);
  const [availableLayers, setAvailableLayers] = useState([]);
  const [availableSheets, setAvailableSheets] = useState([]);
  const [selectedLayer, setSelectedLayer] = useState('');
  const [selectedSheet, setSelectedSheet] = useState('');
  
  // Preview state
  const [previewData, setPreviewData] = useState(null);
  const [previewFile, setPreviewFile] = useState(null);

  // Carica lista file
  const loadFiles = useCallback(async () => {
    try {
      setLoading(true);
      const data = await getFiles();
      setFiles(data.files || []);
      setError(null);
    } catch (err) {
      console.error('Errore caricamento file:', err);
      setError('Errore nel caricamento dei file');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadFiles();
  }, [loadFiles]);

  // Drag & Drop handlers
  const handleDrag = (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === 'dragenter' || e.type === 'dragover') {
      setDragActive(true);
    } else if (e.type === 'dragleave') {
      setDragActive(false);
    }
  };

  const handleDrop = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      handleFileSelect(e.dataTransfer.files[0]);
    }
  };

  const handleFileSelect = (file) => {
    setUploadFile(file);
    // Auto-genera nome dal filename
    const baseName = file.name.replace(/\.[^/.]+$/, '');
    setUploadName(baseName);
    setUploadError(null);
  };

  const handleFileInputChange = (e) => {
    if (e.target.files && e.target.files[0]) {
      handleFileSelect(e.target.files[0]);
    }
  };

  // Passa allo step 2: carica preview colonne
  const handleNextStep = async () => {
    if (!uploadFile_ || !uploadName.trim()) {
      setUploadError('Seleziona un file e inserisci un nome');
      return;
    }

    setLoadingColumns(true);
    setUploadError(null);

    try {
      // Carica preview temporanea per ottenere le colonne
      const preview = await previewFileTemp(uploadFile_);
      
      if (preview.columns && preview.columns.length > 0) {
        setAvailableColumns(preview.columns);
        // Seleziona tutte le colonne di default
        setSelectedColumns(preview.columns.map(col => col.name));
        
        // Controlla se ci sono colonne geometry e se hanno SRID
        const geomColumns = preview.columns.filter(col => 
          col.type && col.type.startsWith('geometry')
        );
        
        setHasGeometryColumn(geomColumns.length > 0);
        
        // Cerca SRID nelle colonne geometry (formato: geometry:polygon:4326)
        let foundSrid = null;
        for (const col of geomColumns) {
          const parts = col.type.split(':');
          if (parts.length >= 3) {
            const srid = parseInt(parts[2]);
            if (!isNaN(srid) && srid > 0) {
              foundSrid = srid;
              break;
            }
          }
        }
        
        setDetectedSrid(foundSrid);
        setUserSrid(foundSrid ? String(foundSrid) : '4326'); // Default 4326 se non rilevato
        
        setUploadStep(2);
      } else {
        setUploadError('Impossibile leggere le colonne del file');
      }
    } catch (err) {
      console.error('Errore lettura colonne:', err);
      setUploadError(err.message || 'Errore nella lettura del file');
    } finally {
      setLoadingColumns(false);
    }
  };

  // Torna allo step 1
  const handlePrevStep = () => {
    setUploadStep(1);
    setAvailableColumns([]);
    setSelectedColumns([]);
    setDetectedSrid(null);
    setUserSrid('');
    setHasGeometryColumn(false);
  };

  // Toggle selezione colonna
  const handleToggleColumn = (colName) => {
    setSelectedColumns(prev => {
      if (prev.includes(colName)) {
        return prev.filter(c => c !== colName);
      } else {
        return [...prev, colName];
      }
    });
  };

  // Seleziona/Deseleziona tutte
  const handleSelectAllColumns = () => {
    if (selectedColumns.length === availableColumns.length) {
      setSelectedColumns([]);
    } else {
      setSelectedColumns(availableColumns.map(col => col.name));
    }
  };

  // Upload file (step finale)
  const handleUpload = async () => {
    if (!uploadFile_ || !uploadName.trim()) {
      setUploadError('Seleziona un file e inserisci un nome');
      return;
    }

    if (selectedColumns.length === 0) {
      setUploadError('Seleziona almeno una colonna');
      return;
    }

    setUploading(true);
    setUploadError(null);

    try {
      // Passa le colonne selezionate (se non sono tutte)
      const columnsToImport = selectedColumns.length === availableColumns.length 
        ? null // tutte le colonne
        : selectedColumns;
      
      // Passa SRID solo se c'è una colonna geometry e l'utente ha specificato un valore
      const sridToUse = hasGeometryColumn && userSrid ? parseInt(userSrid) : null;
      
      const result = await uploadFile(
        uploadFile_, 
        uploadName, 
        uploadDescription, 
        uploadLayer, 
        uploadSheet,
        columnsToImport,
        sridToUse
      );
      
      // Controlla se serve selezione layer/sheet
      if (result.format === 'gdb' || result.format === 'gpkg') {
        // Carica layers disponibili
        const layersInfo = await getFileLayers(result.id);
        if (layersInfo.layers && layersInfo.layers.length > 1) {
          setPendingFile(result);
          setAvailableLayers(layersInfo.layers);
          resetUploadModal();
          setShowLayerModal(true);
          return;
        }
      } else if (result.format === 'xlsx') {
        const layersInfo = await getFileLayers(result.id);
        if (layersInfo.sheets && layersInfo.sheets.length > 1) {
          setPendingFile(result);
          setAvailableSheets(layersInfo.sheets);
          resetUploadModal();
          setShowLayerModal(true);
          return;
        }
      }
      
      // Upload completato
      await loadFiles();
      resetUploadModal();
      
    } catch (err) {
      console.error('Errore upload:', err);
      setUploadError(err.message || 'Errore durante l\'upload');
    } finally {
      setUploading(false);
    }
  };

  // Conferma selezione layer/sheet
  const handleConfirmLayer = async () => {
    if (!pendingFile) return;
    
    try {
      // Aggiorna il file con il layer/sheet selezionato
      const updateData = {};
      if (selectedLayer) updateData.layer = selectedLayer;
      if (selectedSheet) updateData.sheet = selectedSheet;
      
      // Qui potresti chiamare un endpoint di update
      // Per ora ricarica semplicemente
      await loadFiles();
      resetLayerModal();
      
    } catch (err) {
      console.error('Errore selezione layer:', err);
    }
  };

  // Preview file
  const handlePreview = async (file) => {
    try {
      const preview = await getFilePreview(file.id, 50);
      setPreviewData(preview);
      setPreviewFile(file);
      setShowPreviewModal(true);
    } catch (err) {
      console.error('Errore preview:', err);
      alert('Errore nel caricamento della preview');
    }
  };

  // Elimina file
  const handleDelete = async (fileId, fileName) => {
    if (!window.confirm(`Eliminare il file "${fileName}"?\n\nQuesta azione non può essere annullata.`)) {
      return;
    }
    
    try {
      await deleteFile(fileId);
      await loadFiles();
    } catch (err) {
      console.error('Errore eliminazione:', err);
      alert('Errore durante l\'eliminazione');
    }
  };

  // Reset modals
  const resetUploadModal = () => {
    setShowUploadModal(false);
    setUploadFile(null);
    setUploadName('');
    setUploadDescription('');
    setUploadLayer('');
    setUploadSheet('');
    setUploadError(null);
    setUploadStep(1);
    setAvailableColumns([]);
    setSelectedColumns([]);
    setDetectedSrid(null);
    setUserSrid('');
    setHasGeometryColumn(false);
  };

  const resetLayerModal = () => {
    setShowLayerModal(false);
    setPendingFile(null);
    setAvailableLayers([]);
    setAvailableSheets([]);
    setSelectedLayer('');
    setSelectedSheet('');
  };

  // Formatta dimensione file
  const formatFileSize = (bytes) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  };

  // Formatta data
  const formatDate = (dateStr) => {
    const date = new Date(dateStr);
    return date.toLocaleDateString('it-IT', {
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    });
  };

  // Icona formato
  const getFormatIcon = (format) => {
    const icons = {
      'csv': '📄',
      'xlsx': '📊',
      'json': '📋',
      'geojson': '🗺️',
      'gpkg': '🗺️',
      'shp': '🗺️',
      'gdb': '🗃️'
    };
    return icons[format] || '📁';
  };

  // Colore formato
  const getFormatColor = (format) => {
    const colors = {
      'csv': '#4CAF50',
      'xlsx': '#2E7D32',
      'json': '#FF9800',
      'geojson': '#2196F3',
      'gpkg': '#9C27B0',
      'shp': '#E91E63',
      'gdb': '#673AB7'
    };
    return colors[format] || '#666';
  };

  // Loading state
  if (loading) {
    return (
      <div className="file-manager__loading">
        <div>⏳ Caricamento file...</div>
      </div>
    );
  }

  return (
    <div className="file-manager">
      {/* Header */}
      <header className="file-manager__header">
        <div className="file-manager__header-content">
          <div className="file-manager__header-left">
            <button 
              className="file-manager__back-btn"
              onClick={() => navigate('/')}
            >
              ← Indietro
            </button>
            <div>
              <h1 className="file-manager__title">📁 File Esterni</h1>
              <p className="file-manager__subtitle">
                Gestisci file CSV, Excel, GeoJSON, GeoPackage, Shapefile, FileGDB
              </p>
            </div>
          </div>
          <button 
            className="file-manager__new-btn"
            onClick={() => setShowUploadModal(true)}
          >
            + Carica Nuovo File
          </button>
        </div>
      </header>

      {/* Main Content */}
      <main className="file-manager__main">
        {error && (
          <div className="file-manager__error">
            ⚠️ {error}
          </div>
        )}

        {/* Empty State */}
        {files.length === 0 && !error && (
          <div className="file-manager__empty">
            <div className="file-manager__empty-icon">📂</div>
            <h3 className="file-manager__empty-title">Nessun file caricato</h3>
            <p className="file-manager__empty-text">
              Carica file CSV, Excel, GeoJSON o altri formati per usarli nei tuoi progetti MANIPOLA
            </p>
            <button 
              className="file-manager__empty-btn"
              onClick={() => setShowUploadModal(true)}
            >
              + Carica il primo file
            </button>
          </div>
        )}

        {/* Files Grid */}
        {files.length > 0 && (
          <div className="file-manager__grid">
            {files.map(file => (
              <div key={file.id} className="file-card">
                {/* Card Header */}
                <div 
                  className="file-card__header"
                  style={{ borderLeftColor: getFormatColor(file.format) }}
                >
                  <span className="file-card__icon">{getFormatIcon(file.format)}</span>
                  <div className="file-card__header-info">
                    <h3 className="file-card__name">{file.name}</h3>
                    <span 
                      className="file-card__format-badge"
                      style={{ background: getFormatColor(file.format) }}
                    >
                      {file.format.toUpperCase()}
                    </span>
                  </div>
                </div>

                {/* Card Body */}
                <div className="file-card__body">
                  {file.description && (
                    <p className="file-card__description">{file.description}</p>
                  )}
                  
                  <div className="file-card__details">
                    <div className="file-card__detail-row">
                      <span className="file-card__detail-label">File originale:</span>
                      <span className="file-card__detail-value">{file.original_filename}</span>
                    </div>
                    <div className="file-card__detail-row">
                      <span className="file-card__detail-label">Righe:</span>
                      <span className="file-card__detail-value">{file.row_count.toLocaleString()}</span>
                    </div>
                    <div className="file-card__detail-row">
                      <span className="file-card__detail-label">Colonne:</span>
                      <span className="file-card__detail-value">{file.columns?.length || 0}</span>
                    </div>
                    <div className="file-card__detail-row">
                      <span className="file-card__detail-label">Dimensione:</span>
                      <span className="file-card__detail-value">{formatFileSize(file.file_size)}</span>
                    </div>
                    {file.layer && (
                      <div className="file-card__detail-row">
                        <span className="file-card__detail-label">Layer:</span>
                        <span className="file-card__detail-value">{file.layer}</span>
                      </div>
                    )}
                    {file.sheet && (
                      <div className="file-card__detail-row">
                        <span className="file-card__detail-label">Sheet:</span>
                        <span className="file-card__detail-value">{file.sheet}</span>
                      </div>
                    )}
                    {file.crs && (
                      <div className="file-card__detail-row">
                        <span className="file-card__detail-label">CRS:</span>
                        <span className="file-card__detail-value">{file.crs}</span>
                      </div>
                    )}
                  </div>

                  {/* Colonne preview */}
                  {file.columns && file.columns.length > 0 && (
                    <div className="file-card__columns">
                      <div className="file-card__columns-label">Colonne:</div>
                      <div className="file-card__columns-list">
                        {file.columns.slice(0, 5).map((col, idx) => (
                          <span key={idx} className="file-card__column-tag">
                            {col.name}
                          </span>
                        ))}
                        {file.columns.length > 5 && (
                          <span className="file-card__column-more">
                            +{file.columns.length - 5}
                          </span>
                        )}
                      </div>
                    </div>
                  )}
                </div>

                {/* Card Footer */}
                <div className="file-card__footer">
                  <span className="file-card__date">
                    {formatDate(file.created_at)}
                  </span>
                  <div className="file-card__actions">
                    <button 
                      className="file-card__btn file-card__btn--preview"
                      onClick={() => handlePreview(file)}
                      title="Anteprima dati"
                    >
                      👁️
                    </button>
                    <button 
                      className="file-card__btn file-card__btn--delete"
                      onClick={() => handleDelete(file.id, file.name)}
                      title="Elimina"
                    >
                      🗑️
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </main>

      {/* Upload Modal */}
      {showUploadModal && (
        <div className="file-manager__modal-overlay" onClick={resetUploadModal}>
          <div className={`file-manager__modal ${uploadStep === 2 ? 'file-manager__modal--large' : ''}`} onClick={e => e.stopPropagation()}>
            
            {/* Step Indicator */}
            <div className="file-manager__steps">
              <div className={`file-manager__step ${uploadStep >= 1 ? 'file-manager__step--active' : ''}`}>
                <span className="file-manager__step-number">1</span>
                <span className="file-manager__step-label">File</span>
              </div>
              <div className="file-manager__step-line"></div>
              <div className={`file-manager__step ${uploadStep >= 2 ? 'file-manager__step--active' : ''}`}>
                <span className="file-manager__step-number">2</span>
                <span className="file-manager__step-label">Colonne</span>
              </div>
            </div>

            {/* STEP 1: Selezione File */}
            {uploadStep === 1 && (
              <>
                <h2 className="file-manager__modal-title">📤 Carica Nuovo File</h2>
                
                {/* Drop Zone */}
                <div 
                  className={`file-manager__dropzone ${dragActive ? 'file-manager__dropzone--active' : ''} ${uploadFile_ ? 'file-manager__dropzone--has-file' : ''}`}
                  onDragEnter={handleDrag}
                  onDragLeave={handleDrag}
                  onDragOver={handleDrag}
                  onDrop={handleDrop}
                  onClick={() => document.getElementById('file-input').click()}
                >
                  <input
                    id="file-input"
                    type="file"
                    accept=".csv,.xlsx,.xls,.json,.geojson,.gpkg,.shp,.zip"
                    onChange={handleFileInputChange}
                    style={{ display: 'none' }}
                  />
                  
                  {uploadFile_ ? (
                    <div className="file-manager__dropzone-file">
                      <span className="file-manager__dropzone-icon">✅</span>
                      <span className="file-manager__dropzone-filename">{uploadFile_.name}</span>
                      <span className="file-manager__dropzone-size">({formatFileSize(uploadFile_.size)})</span>
                    </div>
                  ) : (
                    <>
                      <span className="file-manager__dropzone-icon">📁</span>
                      <span className="file-manager__dropzone-text">
                        Trascina qui il file o clicca per selezionare
                      </span>
                      <span className="file-manager__dropzone-formats">
                        CSV, Excel, JSON, GeoJSON, GeoPackage, Shapefile (.zip), FileGDB (.zip)
                      </span>
                    </>
                  )}
                </div>

                {/* Form */}
                <div className="file-manager__form-group">
                  <label className="file-manager__form-label">Nome *</label>
                  <input
                    type="text"
                    className="file-manager__form-input"
                    value={uploadName}
                    onChange={e => setUploadName(e.target.value)}
                    placeholder="Es. Catasto Comuni 2024"
                  />
                </div>

                <div className="file-manager__form-group">
                  <label className="file-manager__form-label">Descrizione</label>
                  <textarea
                    className="file-manager__form-textarea"
                    value={uploadDescription}
                    onChange={e => setUploadDescription(e.target.value)}
                    placeholder="Descrizione opzionale..."
                    rows={2}
                  />
                </div>

                {uploadError && (
                  <div className="file-manager__form-error">
                    ⚠️ {uploadError}
                  </div>
                )}

                {/* Actions Step 1 */}
                <div className="file-manager__form-actions">
                  <button 
                    className="file-manager__form-btn file-manager__form-btn--cancel"
                    onClick={resetUploadModal}
                  >
                    Annulla
                  </button>
                  <button 
                    className="file-manager__form-btn file-manager__form-btn--submit"
                    onClick={handleNextStep}
                    disabled={loadingColumns || !uploadFile_ || !uploadName.trim()}
                  >
                    {loadingColumns ? '⏳ Lettura colonne...' : 'Avanti →'}
                  </button>
                </div>
              </>
            )}

            {/* STEP 2: Selezione Colonne */}
            {uploadStep === 2 && (
              <>
                <h2 className="file-manager__modal-title">📋 Seleziona Colonne</h2>
                
                <p className="file-manager__modal-text">
                  Seleziona le colonne da importare. Deseleziona quelle che non ti servono.
                </p>

                {/* Select All / Deselect All */}
                <div className="file-manager__columns-header">
                  <span className="file-manager__columns-count">
                    {selectedColumns.length} di {availableColumns.length} colonne selezionate
                  </span>
                  <button 
                    className="file-manager__columns-toggle"
                    onClick={handleSelectAllColumns}
                  >
                    {selectedColumns.length === availableColumns.length ? '☐ Deseleziona tutte' : '☑ Seleziona tutte'}
                  </button>
                </div>

                {/* Columns List */}
                <div className="file-manager__columns-list">
                  {availableColumns.map((col, idx) => (
                    <label 
                      key={idx} 
                      className={`file-manager__column-item ${selectedColumns.includes(col.name) ? 'file-manager__column-item--selected' : ''}`}
                    >
                      <input
                        type="checkbox"
                        checked={selectedColumns.includes(col.name)}
                        onChange={() => handleToggleColumn(col.name)}
                      />
                      <span className="file-manager__column-name">{col.name}</span>
                      <span className="file-manager__column-type">{col.type}</span>
                    </label>
                  ))}
                </div>

                {/* SRID Input - Solo se c'è una colonna geometry */}
                {hasGeometryColumn && (
                  <div className="file-manager__srid-section">
                    <label className="file-manager__srid-label">
                      🌍 Sistema di Riferimento (SRID)
                      {detectedSrid && (
                        <span className="file-manager__srid-detected">
                          Rilevato: {detectedSrid}
                        </span>
                      )}
                    </label>
                    <div className="file-manager__srid-input-wrapper">
                      <input
                        type="number"
                        className="file-manager__srid-input"
                        value={userSrid}
                        onChange={(e) => setUserSrid(e.target.value)}
                        placeholder="Es. 4326 (WGS84), 32632 (UTM 32N)"
                      />
                      <div className="file-manager__srid-presets">
                        <button 
                          type="button"
                          className={`file-manager__srid-preset ${userSrid === '4326' ? 'file-manager__srid-preset--active' : ''}`}
                          onClick={() => setUserSrid('4326')}
                        >
                          WGS84
                        </button>
                        <button 
                          type="button"
                          className={`file-manager__srid-preset ${userSrid === '32632' ? 'file-manager__srid-preset--active' : ''}`}
                          onClick={() => setUserSrid('32632')}
                        >
                          UTM 32N
                        </button>
                        <button 
                          type="button"
                          className={`file-manager__srid-preset ${userSrid === '3857' ? 'file-manager__srid-preset--active' : ''}`}
                          onClick={() => setUserSrid('3857')}
                        >
                          Web Mercator
                        </button>
                      </div>
                    </div>
                    {!detectedSrid && (
                      <p className="file-manager__srid-hint">
                        ⚠️ SRID non rilevato automaticamente. Specifica il sistema di riferimento dei dati geometrici.
                      </p>
                    )}
                  </div>
                )}

                {uploadError && (
                  <div className="file-manager__form-error">
                    ⚠️ {uploadError}
                  </div>
                )}

                {/* Actions Step 2 */}
                <div className="file-manager__form-actions">
                  <button 
                    className="file-manager__form-btn file-manager__form-btn--cancel"
                    onClick={handlePrevStep}
                  >
                    ← Indietro
                  </button>
                  <button 
                    className="file-manager__form-btn file-manager__form-btn--submit"
                    onClick={handleUpload}
                    disabled={uploading || selectedColumns.length === 0}
                  >
                    {uploading ? '⏳ Caricamento...' : `📤 Carica (${selectedColumns.length} colonne)`}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {/* Layer/Sheet Selection Modal */}
      {showLayerModal && (
        <div className="file-manager__modal-overlay" onClick={resetLayerModal}>
          <div className="file-manager__modal" onClick={e => e.stopPropagation()}>
            <h2 className="file-manager__modal-title">
              {availableLayers.length > 0 ? '🗺️ Seleziona Layer' : '📊 Seleziona Sheet'}
            </h2>
            
            <p className="file-manager__modal-text">
              Il file contiene più {availableLayers.length > 0 ? 'layer' : 'fogli'}. 
              Seleziona quale vuoi utilizzare:
            </p>

            <div className="file-manager__layer-list">
              {availableLayers.map((layer, idx) => (
                <label key={idx} className="file-manager__layer-option">
                  <input
                    type="radio"
                    name="layer"
                    value={layer}
                    checked={selectedLayer === layer}
                    onChange={e => setSelectedLayer(e.target.value)}
                  />
                  <span>{layer}</span>
                </label>
              ))}
              {availableSheets.map((sheet, idx) => (
                <label key={idx} className="file-manager__layer-option">
                  <input
                    type="radio"
                    name="sheet"
                    value={sheet}
                    checked={selectedSheet === sheet}
                    onChange={e => setSelectedSheet(e.target.value)}
                  />
                  <span>{sheet}</span>
                </label>
              ))}
            </div>

            <div className="file-manager__form-actions">
              <button 
                className="file-manager__form-btn file-manager__form-btn--cancel"
                onClick={resetLayerModal}
              >
                Annulla
              </button>
              <button 
                className="file-manager__form-btn file-manager__form-btn--submit"
                onClick={handleConfirmLayer}
                disabled={!selectedLayer && !selectedSheet}
              >
                Conferma
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Preview Modal */}
      {showPreviewModal && previewData && (
        <div className="file-manager__modal-overlay" onClick={() => setShowPreviewModal(false)}>
          <div className="file-manager__modal file-manager__modal--large" onClick={e => e.stopPropagation()}>
            <h2 className="file-manager__modal-title">
              👁️ Anteprima: {previewFile?.name}
            </h2>
            
            <div className="file-manager__preview-info">
              Righe totali: {previewData.total_count?.toLocaleString()} | 
              Mostrate: {previewData.preview_count}
            </div>

            <div className="file-manager__preview-table-container">
              <table className="file-manager__preview-table">
                <thead>
                  <tr>
                    {previewData.columns?.map((col, idx) => (
                      <th key={idx}>
                        {col.name}
                        <span className="file-manager__preview-col-type">{col.type}</span>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {previewData.rows?.map((row, rowIdx) => (
                    <tr key={rowIdx}>
                      {previewData.columns?.map((col, colIdx) => (
                        <td key={colIdx}>
                          {row[col.name] !== null && row[col.name] !== undefined 
                            ? String(row[col.name]).substring(0, 100) 
                            : <span className="file-manager__preview-null">NULL</span>
                          }
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="file-manager__form-actions">
              <button 
                className="file-manager__form-btn file-manager__form-btn--cancel"
                onClick={() => setShowPreviewModal(false)}
              >
                Chiudi
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default FileManager;