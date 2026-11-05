/**
 * FileManager.jsx — Gestione file caricati
 * Redesign Plenora: sidebar layout, hex bg, drop zone, card grid
 * Route: /files
 */
import React, { useState, useRef } from 'react';
import { srid as sridCodec } from '../utils/fieldCodecs';
import { getColumnSRID } from '../utils/columnUtils';
import { useResource } from '../hooks/useResource';
import { useToast } from '../hooks/useToast';
import ConfirmDialog from '../components/common/ConfirmDialog';
import { useConfirmModal } from '../hooks/useConfirmModal';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import {
  getFiles,
  updateFile,
  uploadFile,
  uploadMultiLayer,
  deleteFile,
  getFileLayers,
  getFilePreview,
  previewFileTemp,
} from '../api/files';
import { useHexCanvas } from '../utils/hexCanvas';
import AllowedGroupsSelector from '../components/shared/AllowedGroupsSelector';
import AppSidebar from '../components/AppSidebar';
import ThemeToggle from '../components/common/ThemeToggle';
import ModalWrapper from '../components/common/ModalWrapper';
import { can } from '../utils/permissions';
import { humanizeErrorMessage } from '../utils/errorMessages';
import '../styles/FileManager.css';
import {
  FORMAT_CONFIG,
  getFormatConfig,
  formatFileSize,
  SPATIAL_FORMATS,
  buildMultiSourceSelection,
  resolveMultiSourcePreview,
} from './FileManager/helpers';
import FileCard from './FileManager/FileCard';
import UploadModal from './FileManager/UploadModal';
import LayerModal from './FileManager/LayerModal';
import PreviewModal from './FileManager/PreviewModal';
import { goBack } from '../utils/navigation';

// SRID dal convertitore unico (R8.23): "4326", "EPSG:4326" -> 4326.
const SRID = sridCodec({ optional: true });

const FILTER_TABS = [
  { key: 'all',     label: 'Tutti' },
  { key: 'csv',     label: 'CSV' },
  { key: 'xlsx',    label: 'Excel' },
  { key: 'geojson', label: 'GeoJSON' },
  { key: 'spatial', label: 'Spaziali' },
];
const FileManager = () => {
  const navigate     = useNavigate();
  const toast        = useToast();
  const confirmModal = useConfirmModal();
  const { user } = useAuth();
  const canvasRef    = useRef(null);
  const fileInputRef = useRef(null);
  useHexCanvas(canvasRef);

  // Fase 5 T2 (R4.3, R4.7): elenco dall'hook comune (spinner a pagina
  // intera solo al primo caricamento, errore con «Riprova»).
  const resource = useResource(async () => (await getFiles()).files || [], { key: 'files', errorMessage: humanizeErrorMessage });
  const files = resource.data || [];
  const loading = resource.initialLoading;
  const error = resource.error;
  const loadFiles = () => resource.reload();
  // Id della scelta del file: anteprime e colonne di un file scelto prima
  // non scrivono sul file scelto dopo.
  const selectionRef = useRef(0);
  const [search,  setSearch]  = useState('');
  const [activeTab, setActiveTab] = useState('all');
  const [sharingFile, setSharingFile] = useState(null);  // file in sharing modal (solo super)

  // Upload modal state
  const [showUploadModal, setShowUploadModal] = useState(false);
  const [uploadFile_,     setUploadFile]      = useState(null);
  const [uploadName,      setUploadName]      = useState('');
  const [uploadDescription, setUploadDescription] = useState('');
  const [uploadLayer,     setUploadLayer]     = useState('');
  const [uploadSheet,     setUploadSheet]     = useState('');
  const [uploading,       setUploading]       = useState(false);
  const [uploadError,     setUploadError]     = useState(null);
  const [dragActive,      setDragActive]      = useState(false);
  const [uploadStep,      setUploadStep]      = useState(1);

  // Columns step
  const [availableColumns, setAvailableColumns] = useState([]);
  const [selectedColumns,  setSelectedColumns]  = useState([]);
  const [loadingColumns,   setLoadingColumns]   = useState(false);
  const [detectedSrid,     setDetectedSrid]     = useState(null);
  const [userSrid,         setUserSrid]         = useState('');
  const [hasGeometryColumn, setHasGeometryColumn] = useState(false);

  // Layer modal state
  const [showLayerModal,  setShowLayerModal]  = useState(false);
  const [pendingFile,     setPendingFile]     = useState(null);
  const [availableLayers, setAvailableLayers] = useState([]);
  const [availableSheets, setAvailableSheets] = useState([]);
  const [selectedLayer,   setSelectedLayer]   = useState('');
  const [selectedSheet,   setSelectedSheet]   = useState('');

  // Multi-layer wizard
  const [isMultiLayerFile,  setIsMultiLayerFile]  = useState(false);
  const [multiSourceKind,   setMultiSourceKind]   = useState(null);
  const [checkedLayers,     setCheckedLayers]     = useState({});
  const [layerColumns,      setLayerColumns]      = useState({});
  const [layerSelectedCols, setLayerSelectedCols] = useState({});
  const [activeLayerTab,    setActiveLayerTab]    = useState(null);
  // Lettura delle colonne PER layer (le schede non si contendono un flag).
  const [loadingLayers,     setLoadingLayers]     = useState({});

  // Preview modal state
  const [showPreviewModal, setShowPreviewModal] = useState(false);
  const [previewData,      setPreviewData]      = useState(null);
  const [previewFile,      setPreviewFile]      = useState(null);
const handleDrag = (e) => {
    e.preventDefault(); e.stopPropagation();
    setDragActive(e.type === 'dragenter' || e.type === 'dragover');
  };
  const handleDrop = (e) => {
    e.preventDefault(); e.stopPropagation();
    setDragActive(false);
    if (e.dataTransfer.files?.[0]) {
      handleFileSelect(e.dataTransfer.files[0]);
      setShowUploadModal(true);
    }
  };
  const handleFileSelect = (file) => {
    // Un file nuovo: niente di quello di prima (layer, foglio, colonne).
    selectionRef.current += 1;
    setUploadFile(file);
    setUploadName(file.name.replace(/\.[^/.]+$/, ''));
    setUploadError(null);
    setUploadLayer(''); setUploadSheet(''); setUploadStep(1);
    setAvailableColumns([]); setSelectedColumns([]); setDetectedSrid(null); setUserSrid(''); setHasGeometryColumn(false);
    setIsMultiLayerFile(false); setMultiSourceKind(null); setCheckedLayers({}); setLayerColumns({}); setLayerSelectedCols({});
    setActiveLayerTab(null); setAvailableLayers([]); setLoadingLayers({}); setLoadingColumns(false);
  };
const _loadColumnsForLayer = async (file, layerName, currentCols, currentSelected, isSheet = false) => {
    const token = selectionRef.current;
    setLoadingLayers(prev => ({ ...prev, [layerName]: true }));
    try {
      const preview = await previewFileTemp(file, isSheet ? null : layerName, isSheet ? layerName : null);
      if (token !== selectionRef.current) return;
      const cols = preview.columns || [];
      setLayerColumns(prev => ({ ...prev, [layerName]: cols }));
      if (!currentSelected[layerName] || currentSelected[layerName].length === 0) {
        setLayerSelectedCols(prev => ({ ...prev, [layerName]: cols.map(c => c.name) }));
      }
    } catch (e) {
      if (token !== selectionRef.current) return;
      setUploadError(`Colonne di "${layerName}" non lette: ${humanizeErrorMessage(e)}`);
    } finally {
      if (token === selectionRef.current) setLoadingLayers(prev => ({ ...prev, [layerName]: false }));
    }
  };

  const handleLayerTabChange = (layerName) => {
    setActiveLayerTab(layerName);
    if (!layerColumns[layerName] || layerColumns[layerName].length === 0) {
      _loadColumnsForLayer(
        uploadFile_,
        layerName,
        layerColumns,
        layerSelectedCols,
        multiSourceKind === 'sheet',
      );
    }
  };

  const handleToggleLayerCol = (layerName, colName) => {
    setLayerSelectedCols(prev => {
      const current = prev[layerName] || [];
      const updated = current.includes(colName) ? current.filter(c => c !== colName) : [...current, colName];
      return { ...prev, [layerName]: updated };
    });
  };

  const handleToggleAllLayerCols = (layerName) => {
    const all = (layerColumns[layerName] || []).map(c => c.name);
    setLayerSelectedCols(prev => {
      const current = prev[layerName] || [];
      return { ...prev, [layerName]: current.length === all.length ? [] : all };
    });
  };
const handleMultiLayerUpload = async () => {
    const isSheet = multiSourceKind === 'sheet';
    const { config: selectedLayers, emptySelection } = buildMultiSourceSelection({
      checkedLayers,
      layerColumns,
      layerSelectedCols,
      isSheet,
    });
    if (emptySelection) {
      setUploadError(`Seleziona almeno una colonna per "${emptySelection}"`);
      return;
    }
    if (selectedLayers.length === 0) { setUploadError('Seleziona almeno un layer'); return; }
    if (SRID.parse(userSrid).error) { setUploadError(`SRID: ${SRID.parse(userSrid).error}`); return; }
    setUploading(true); setUploadError(null);
    try {
      const sridResult = SRID.parse(userSrid);
      if (sridResult.error) { setUploadError(`SRID: ${sridResult.error}`); return; }
      const srid = sridResult.value;
      await uploadMultiLayer(uploadFile_, uploadName, uploadDescription, selectedLayers, srid);
      await loadFiles();
      resetUploadModal();
    } catch (err) {
      setUploadError(humanizeErrorMessage(err, "Errore durante l'upload"));
    } finally { setUploading(false); }
  };
const handleNextStep = async () => {
    if (!uploadFile_ || !uploadName.trim()) { setUploadError('Seleziona un file e inserisci un nome'); return; }
    setLoadingColumns(true); setUploadError(null);
    const token = selectionRef.current;
    try {
      const ext = uploadFile_.name.split('.').pop().toLowerCase();
      const preview = await previewFileTemp(uploadFile_);
      // Nel frattempo e' stato scelto un altro file: questa anteprima non vale.
      if (token !== selectionRef.current) return;
      const { sourceKind, itemList } = resolveMultiSourcePreview(preview);
      if (itemList.length > 1) {
        const initChecked = {}, initCols = {}, initSelected = {};
        itemList.forEach(l => { initChecked[l] = true; initCols[l] = []; initSelected[l] = []; });
        setIsMultiLayerFile(true);
        setMultiSourceKind(sourceKind);
        setCheckedLayers(initChecked); setLayerColumns(initCols); setLayerSelectedCols(initSelected);
        setActiveLayerTab(itemList[0]); setAvailableLayers(itemList);
        setUploadStep(2); setLoadingColumns(false);
        _loadColumnsForLayer(uploadFile_, itemList[0], initCols, initSelected, sourceKind === 'sheet');
        return;
      }
      if (itemList.length === 1) {
        sourceKind === 'sheet' ? setUploadSheet(itemList[0]) : setUploadLayer(itemList[0]);
      }

      if (preview.columns?.length > 0) {
        setIsMultiLayerFile(false);
        setAvailableColumns(preview.columns);
        setSelectedColumns(preview.columns.map(c => c.name));
        const geomCols = preview.columns.filter(c => c.type?.startsWith('geometry'));
        setHasGeometryColumn(geomCols.length > 0);
        let foundSrid = null;
        for (const c of geomCols) {
          const s = getColumnSRID(c.type);
          if (s) { foundSrid = s; break; }
        }
        setDetectedSrid(foundSrid);
        setUserSrid(foundSrid ? String(foundSrid) : '4326');
        setUploadStep(2);
      } else {
        const binaryExts = ['zip','gz','tar','7z','rar','dxf'];
        if (binaryExts.includes(ext)) {
          setUploading(true);
          try {
            await uploadFile(uploadFile_, uploadName, uploadDescription, null, null, null, null);
            await loadFiles(); resetUploadModal();
          } catch (e) { setUploadError(humanizeErrorMessage(e, "Errore upload")); } finally { setUploading(false); }
        } else {
          setUploadError('Impossibile leggere le colonne del file');
        }
      }
    } catch (err) {
      if (token !== selectionRef.current) return;
      setUploadError(humanizeErrorMessage(err, 'Errore nella lettura del file'));
    } finally { if (token === selectionRef.current) setLoadingColumns(false); }
  };

  const handlePrevStep = () => {
    setUploadStep(1); setAvailableColumns([]); setSelectedColumns([]);
    setDetectedSrid(null); setUserSrid(''); setHasGeometryColumn(false);
  };

  const handleToggleColumn    = (col) => setSelectedColumns(prev => prev.includes(col) ? prev.filter(c => c !== col) : [...prev, col]);
  const handleSelectAllColumns = ()   => setSelectedColumns(selectedColumns.length === availableColumns.length ? [] : availableColumns.map(c => c.name));
const handleUpload = async () => {
    if (!uploadFile_ || !uploadName.trim()) { setUploadError('Seleziona un file e inserisci un nome'); return; }
    if (selectedColumns.length === 0) { setUploadError('Seleziona almeno una colonna'); return; }
    // R8.23: SRID dal convertitore unico ("EPSG:4326" -> 4326); un testo non
    // valido e' un errore, mai NaN inviato al backend.
    const sridResult = hasGeometryColumn ? SRID.parse(userSrid) : { value: null, error: null };
    if (sridResult.error) { setUploadError(`SRID: ${sridResult.error}`); return; }
    setUploading(true); setUploadError(null);
    try {
      const columnsToImport = selectedColumns.length === availableColumns.length ? null : selectedColumns;
      const srid = sridResult.value;
      const result = await uploadFile(uploadFile_, uploadName, uploadDescription, uploadLayer, uploadSheet, columnsToImport, srid);
      if (result.format === 'gdb' || result.format === 'gpkg') {
        const info = await getFileLayers(result.id);
        if (info.layers?.length > 1) { setPendingFile(result); setAvailableLayers(info.layers); resetUploadModal(); setShowLayerModal(true); return; }
      } else if (result.format === 'xlsx') {
        const info = await getFileLayers(result.id);
        if (info.sheets?.length > 1) { setPendingFile(result); setAvailableSheets(info.sheets); resetUploadModal(); setShowLayerModal(true); return; }
      }
      await loadFiles(); resetUploadModal();
    } catch (err) {
      setUploadError(humanizeErrorMessage(err, "Errore durante l'upload"));
    } finally { setUploading(false); }
  };

  const handleConfirmLayer = async () => {
    if (!pendingFile) return;
    try {
      await updateFile(pendingFile.id, {
        layer: selectedLayer || null,
        sheet: selectedSheet || null,
      });
      await loadFiles();
      resetLayerModal();
    } catch (err) {
      toast.error(humanizeErrorMessage(err));
    }
  };
const handlePreview = async (file) => {
    try {
      const preview = await getFilePreview(file.id, 50);
      setPreviewData(preview); setPreviewFile(file); setShowPreviewModal(true);
    } catch (err) {
      // RB.5 (2026-04-27): se il file e' stato eliminato tra il caricamento
      // della lista e il click "Preview" (race condition: altra tab,
      // scheduler cleanup, share revocato, ecc.) il backend risponde 404
      // legittimamente. UX dedicata: messaggio specifico + auto-refresh
      // della lista per rimuovere lo stato sporco.
      if (err?.response?.status === 404) {
        toast.error('File non trovato: potrebbe essere stato eliminato. Aggiorno la lista...');
        loadFiles();
        return;
      }
      // Pre-fix (P2 contracts-flows): 403 PermissionError (accesso al
      // file condiviso revocato dal superadmin) cadeva nel fallback
      // generico "Errore nel caricamento della preview". L'utente non
      // capiva che la condivisione era stata revocata. Post-fix:
      // messaggio dedicato + auto-refresh per rimuovere il file dalla
      // lista (allineato al pattern 404).
      if (err?.response?.status === 403) {
        toast.error('Accesso al file non piu\' disponibile (condivisione revocata). Aggiorno la lista...');
        loadFiles();
        return;
      }
      // P1 (2026-05-26): propaga dettaglio server invece di toast
      // generico. Allineato al pattern 404/403 sopra.
      toast.error(humanizeErrorMessage(err));
    }
  };
const handleDelete = async (fileId, fileName) => {
    const ok = await confirmModal.confirm({ title: 'Elimina file', message: `Eliminare "${fileName}"?`, confirmText: 'Elimina', variant: 'danger' });
    if (!ok) return;
    try {
      await deleteFile(fileId);
      await loadFiles();
    } catch (err) {
      // Pre-fix: `catch { toast.error(...) }` senza param scartava
      // err.message e impediva il troubleshooting di errori specifici
      // (es. file in uso da un canvas, lock DB). Post-fix mostriamo il
      // messaggio originale quando disponibile, fallback al generico.
      toast.error(humanizeErrorMessage(err));
    }
  };
const resetUploadModal = () => {
    setShowUploadModal(false); setUploadFile(null); setUploadName(''); setUploadDescription('');
    setUploadLayer(''); setUploadSheet(''); setUploadError(null); setUploadStep(1);
    setAvailableColumns([]); setSelectedColumns([]); setDetectedSrid(null); setUserSrid(''); setHasGeometryColumn(false);
    setIsMultiLayerFile(false); setMultiSourceKind(null); setCheckedLayers({}); setLayerColumns({}); setLayerSelectedCols({}); setActiveLayerTab(null); setAvailableLayers([]);
    setLoadingLayers({}); selectionRef.current += 1;
  };
  const resetLayerModal = () => { setShowLayerModal(false); setPendingFile(null); setAvailableLayers([]); setAvailableSheets([]); setSelectedLayer(''); setSelectedSheet(''); };
  const filteredFiles = files.filter(f => {
    const matchSearch = !search || f.name.toLowerCase().includes(search.toLowerCase()) || f.original_filename?.toLowerCase().includes(search.toLowerCase());
    const matchTab =
      activeTab === 'all'     ? true :
      activeTab === 'spatial' ? SPATIAL_FORMATS.includes(f.format) :
      f.format === activeTab;
    return matchSearch && matchTab;
  });

  const totalSize = files.reduce((s, f) => s + (f.file_size || 0), 0);

  if (loading) return (
    <div className="fm fm--loading">
      <div className="fm__loading-inner"><div className="fm__loading-icon">⬡</div>Caricamento...</div>
    </div>
  );

  return (
    <>
    <ConfirmDialog {...confirmModal} />
    <div className="fm">
<AppSidebar counts={{ files: files.length }} />
<div className="fm__main">
        <header className="fm__topbar">
          <button type="button" className="fm__back-btn" onClick={() => goBack(navigate)}>← Indietro</button>
          <div className="fm__page-title">File Manager</div>
          <div className="fm__topbar-right">
            <ThemeToggle classPrefix="fm" />
            {can.uploadFile(user) && (
              <button type="button" className="fm__btn fm__btn--primary" onClick={() => setShowUploadModal(true)}>↑ Carica File</button>
            )}
          </div>
        </header>

        <div className="fm__content">
          <canvas ref={canvasRef} className="fm__hex-bg" />

          {/* Toolbar */}
          <div className="fm__toolbar">
            <div className="fm__search">
              <span className="fm__search-icon">⌕</span>
              <input
                className="fm__search-input"
                placeholder="Cerca file..."
                aria-label="Cerca file"
                value={search}
                onChange={e => setSearch(e.target.value)}
              />
              {search && <button type="button" className="fm__search-clear" onClick={() => setSearch('')} aria-label="Pulisci ricerca">✕</button>}
            </div>
            <div className="fm__filter-tabs">
              {FILTER_TABS.map(t => {
                const count = t.key === 'all' ? files.length : t.key === 'spatial' ? files.filter(f => SPATIAL_FORMATS.includes(f.format)).length : files.filter(f => f.format === t.key).length;
                return (
                  <button key={t.key} type="button" className={`fm__ftab${activeTab === t.key ? ' fm__ftab--active' : ''}`} onClick={() => setActiveTab(t.key)}>
                    {t.label} <span className="fm__ftab-count">{count}</span>
                  </button>
                );
              })}
            </div>
            <div className="fm__toolbar-right">
              <span className="fm__file-count">{files.length} file · {formatFileSize(totalSize)} totali</span>
            </div>
          </div>

          {/* Error */}
          {error && (
            <div className="fm__error" role="alert">⚠️ {error} <button type="button" className="fm__btn fm__btn--ghost" onClick={() => resource.reload()}>Riprova</button></div>
          )}

          {/* Drop zone */}
          {can.uploadFile(user) && (
          <div
            className={`fm__drop-zone${dragActive ? ' fm__drop-zone--active' : ''}`}
            onDragEnter={handleDrag} onDragOver={handleDrag} onDragLeave={handleDrag} onDrop={handleDrop}
            onClick={() => setShowUploadModal(true)}
          >
            {uploadFile_ && !showUploadModal ? (
              <>
                <div className="fm__drop-icon">✓</div>
                <div className="fm__drop-title fm__drop-title--ok">{uploadFile_.name}</div>
                <div className="fm__drop-sub">{formatFileSize(uploadFile_.size)} · Clicca per configurare</div>
              </>
            ) : (
              <>
                <div className="fm__drop-icon">↑</div>
                <div className="fm__drop-title">Trascina i file qui oppure clicca per caricare</div>
                <div className="fm__drop-sub">Supporta upload multiplo</div>
                <div className="fm__drop-formats">
                  {Object.entries(FORMAT_CONFIG).map(([key, cfg]) => (
                    <span key={key} className="fm__format-chip" style={{ color: cfg.color, background: `rgba(${cfg.rgb},0.08)`, borderColor: `rgba(${cfg.rgb},0.22)` }}>
                      {cfg.label}
                    </span>
                  ))}
                </div>
              </>
            )}
          </div>
          )}

          {/* Empty state */}
          {filteredFiles.length === 0 && !error && (
            <div className="fm__empty">
              <div className="fm__empty-icon">📂</div>
              <div className="fm__empty-title">{search ? 'Nessun file trovato' : 'Nessun file caricato'}</div>
              <div className="fm__empty-text">{search ? `Nessun risultato per "${search}"` : 'Carica file CSV, Excel, GeoJSON o altri formati per usarli nei tuoi progetti'}</div>
              {!search && can.uploadFile(user) && (
                <button type="button" className="fm__btn fm__btn--primary fm__btn--mt-md" onClick={() => setShowUploadModal(true)}>
                  ↑ Carica il primo file
                </button>
              )}
            </div>
          )}

          {/* Files grid */}
          {filteredFiles.length > 0 && (
            <div className="fm__grid">
              {filteredFiles.map(file => (
                <FileCard
                  key={file.id}
                  file={file}
                  onPreview={() => handlePreview(file)}
                  onDelete={can.editFile(user, file) ? () => handleDelete(file.id, file.name) : null}
                  onShare={can.shareResources(user) ? () => setSharingFile(file) : null}
                />
              ))}
            </div>
          )}
        </div>
      </div>
{showUploadModal && (
        <UploadModal
          step={uploadStep}
          uploadFile={uploadFile_}
          uploadName={uploadName}
          uploadDescription={uploadDescription}
          uploading={uploading}
          uploadError={uploadError}
          dragActive={dragActive}
          availableColumns={availableColumns}
          selectedColumns={selectedColumns}
          loadingColumns={loadingColumns}
          detectedSrid={detectedSrid}
          userSrid={userSrid}
          hasGeometryColumn={hasGeometryColumn}
          isMultiLayerFile={isMultiLayerFile}
          availableLayers={availableLayers}
          checkedLayers={checkedLayers}
          layerColumns={layerColumns}
          layerSelectedCols={layerSelectedCols}
          activeLayerTab={activeLayerTab}
          loadingLayerCols={Boolean(activeLayerTab && loadingLayers[activeLayerTab])}
          fileInputRef={fileInputRef}
          onClose={resetUploadModal}
          onDrag={handleDrag}
          onDrop={handleDrop}
          onFileInput={e => e.target.files?.[0] && handleFileSelect(e.target.files[0])}
          onNameChange={setUploadName}
          onDescChange={setUploadDescription}
          onNext={handleNextStep}
          onPrev={handlePrevStep}
          onUpload={handleUpload}
          onMultiUpload={handleMultiLayerUpload}
          onToggleColumn={handleToggleColumn}
          onSelectAll={handleSelectAllColumns}
          onUserSridChange={setUserSrid}
          onLayerCheck={(l, v) => setCheckedLayers(prev => ({ ...prev, [l]: v }))}
          onLayerTabChange={handleLayerTabChange}
          onToggleLayerCol={handleToggleLayerCol}
          onToggleAllLayerCols={handleToggleAllLayerCols}
        />
      )}
{showLayerModal && (
        <LayerModal
          pendingFile={pendingFile}
          availableLayers={availableLayers}
          availableSheets={availableSheets}
          selectedLayer={selectedLayer}
          selectedSheet={selectedSheet}
          onLayerChange={setSelectedLayer}
          onSheetChange={setSelectedSheet}
          onConfirm={handleConfirmLayer}
          onClose={resetLayerModal}
        />
      )}
{showPreviewModal && previewData && previewFile && (
        <PreviewModal
          file={previewFile}
          data={previewData}
          onClose={() => setShowPreviewModal(false)}
        />
      )}
{sharingFile && (
        <ModalWrapper
          isOpen={true}
          onClose={() => setSharingFile(null)}
          title="Condividi file"
          icon="🔗"
          subtitle={sharingFile.name}
          size="md"
        >
          <AllowedGroupsSelector
            embedded
            resourceType="file_sources"
            resourceId={sharingFile.id}
            onClose={() => setSharingFile(null)}
          />
        </ModalWrapper>
      )}
    </div>
    </>
  );
};


export default FileManager;
