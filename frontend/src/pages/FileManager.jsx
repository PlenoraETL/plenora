/**
 * FileManager.jsx — Gestione file caricati
 * Redesign Plenora: sidebar layout, hex bg, drop zone, card grid
 * Route: /files
 */
import React, { useState, useRef } from 'react';
import { srid as sridCodec } from '../utils/fieldCodecs';
import { getColumnSRID } from '../utils/columnUtils';
import { useSharedResource } from '../hooks/useSharedResource';
import { filesOf } from '../utils/sharedSelectors';
import { useToast } from '../hooks/useToast';
import ConfirmDialog from '../components/common/ConfirmDialog';
import { useConfirmModal } from '../hooks/useConfirmModal';
import { useAuth } from '../context/AuthContext';
import {

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
import PageLayout from '../components/common/PageLayout';
import EmptyState from '../components/common/EmptyState';
import ModalWrapper from '../components/common/ModalWrapper';
import { can } from '../utils/permissions';
import { humanizeErrorMessage } from '../utils/errorMessages';
import '../styles/FileManager.css';
import { formatBytes as formatFileSize } from '../utils/format';
import {
  FORMAT_CONFIG,
  getFormatConfig,
  SPATIAL_FORMATS,
  buildMultiSourceSelection,
  resolveMultiSourcePreview,
} from './FileManager/helpers';
import FileCard from './FileManager/FileCard';
import UploadModal from './FileManager/UploadModal';
import LayerModal from './FileManager/LayerModal';
import PreviewModal from './FileManager/PreviewModal';
import { pressable } from '../utils/pressable';
import SearchField, { SearchEmpty } from '../components/common/SearchField';
import { useSearch } from '../hooks/useSearch';
import VirtualList from '../components/common/VirtualList';
import { useTransferProgress } from '../hooks/useTransferProgress';

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
  const toast        = useToast();
  const confirmModal = useConfirmModal();
  const { user } = useAuth();
  const canvasRef    = useRef(null);
  const fileInputRef = useRef(null);
  useHexCanvas(canvasRef);

  // Fase 5 T2 (R4.3, R4.7): elenco dall'hook comune (spinner a pagina
  // intera solo al primo caricamento, errore con «Riprova»).
  // UI-1 (C27): lista condivisa (utils/sharedResources), invalidata dalle scritture.
  const resource = useSharedResource('files', { select: filesOf, errorMessage: humanizeErrorMessage });
  const files = resource.data || [];
  const loading = resource.initialLoading;
  const error = resource.error;
  const loadFiles = () => resource.reload();
  // Id della scelta del file: anteprime e colonne di un file scelto prima
  // non scrivono sul file scelto dopo.
  const selectionRef = useRef(0);
  const search = useSearch();
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
  // U17 (UI-3): avanzamento del caricamento nella finestra.
  const upload = useTransferProgress();
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
      // P16: si carica un file alla volta; con piu' file lo si dice.
      if (e.dataTransfer.files.length > 1) toast.warning(`Si carica un file alla volta: scelto "${e.dataTransfer.files[0].name}".`);
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
      await uploadMultiLayer(uploadFile_, uploadName, uploadDescription, selectedLayers, srid, { onProgress: upload.onProgress });
      await loadFiles();
      resetUploadModal();
    } catch (err) {
      setUploadError(humanizeErrorMessage(err, "Caricamento non riuscito"));
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
            await uploadFile(uploadFile_, uploadName, uploadDescription, null, null, null, null, { onProgress: upload.onProgress });
            await loadFiles(); resetUploadModal();
          } catch (e) { setUploadError(humanizeErrorMessage(e, "Caricamento non riuscito")); } finally { setUploading(false); }
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
      const result = await uploadFile(uploadFile_, uploadName, uploadDescription, uploadLayer, uploadSheet, columnsToImport, srid, { onProgress: upload.onProgress });
      if (result.format === 'gdb' || result.format === 'gpkg') {
        const info = await getFileLayers(result.id);
        // P4: resetUploadModal azzera i layer: va chiamato PRIMA di impostarli.
        if (info.layers?.length > 1) { resetUploadModal(); setPendingFile(result); setAvailableLayers(info.layers); setShowLayerModal(true); return; }
      } else if (result.format === 'xlsx') {
        const info = await getFileLayers(result.id);
        if (info.sheets?.length > 1) { resetUploadModal(); setPendingFile(result); setAvailableSheets(info.sheets); setShowLayerModal(true); return; }
      }
      await loadFiles(); resetUploadModal();
    } catch (err) {
      setUploadError(humanizeErrorMessage(err, "Caricamento non riuscito"));
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
  const resetLayerModal = () => {
    // P4: il file e' gia' caricato anche se si annulla la scelta del layer:
    // la lista lo deve mostrare.
    const uploaded = Boolean(pendingFile);
    setShowLayerModal(false); setPendingFile(null); setAvailableLayers([]); setAvailableSheets([]); setSelectedLayer(''); setSelectedSheet('');
    if (uploaded) loadFiles();
  };
  const filteredFiles = search.filter(files, f => [f.name, f.original_filename]).filter(f => (
    activeTab === 'all'     ? true :
    activeTab === 'spatial' ? SPATIAL_FORMATS.includes(f.format) :
    f.format === activeTab
  ));

  const totalSize = files.reduce((s, f) => s + (f.file_size || 0), 0);

  if (loading) return (
    <PageLayout className="fm" title="File Manager">
      <div className="fm__loading-inner"><div className="fm__loading-icon">⬡</div>Caricamento...</div>
    </PageLayout>
  );

  return (
    <PageLayout
      className="fm"
      bare
      title="File Manager"
      actions={can.uploadFile(user) && (
        <button type="button" className="fm__btn fm__btn--primary" onClick={() => setShowUploadModal(true)}>↑ Carica File</button>
      )}
    >
    <ConfirmDialog {...confirmModal} />

        <div className="fm__content">
          <canvas ref={canvasRef} className="fm__hex-bg" />

          {/* Toolbar */}
          <div className="fm__toolbar">
            <SearchField search={search} label="Cerca file" className="fm__search" shown={filteredFiles.length} total={files.length} />
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
            {...pressable(() => setShowUploadModal(true))}
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
                <div className="fm__drop-sub">Un file alla volta</div>
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
          {!error && <SearchEmpty search={search} shown={filteredFiles.length} />}
          {filteredFiles.length === 0 && !error && !search.active && (
            <EmptyState
              icon="📂"
              title={activeTab !== 'all' && files.length > 0 ? 'Nessun file di questo formato' : 'Nessun file caricato'}
              text={activeTab !== 'all' && files.length > 0
                ? 'Cambia scheda per vedere gli altri file.'
                : 'Carica file CSV, Excel, GeoJSON o altri formati per usarli nei tuoi progetti'}
              action={activeTab === 'all' && can.uploadFile(user) && (
                <button type="button" className="fm__btn fm__btn--primary" onClick={() => setShowUploadModal(true)}>
                  ↑ Carica il primo file
                </button>
              )}
            />
          )}

          {/* Files grid */}
          {filteredFiles.length > 0 && (
            // Oltre la soglia solo le righe visibili (UI-2, V9: common/VirtualList).
            <VirtualList
              className="fm__grid" minColumnWidth={300} gap={12} estimatedItemHeight={170}
              label="File caricati" items={filteredFiles} itemKey={file => file.id}
              renderItem={file => (
                <FileCard
                  file={file}
                  onPreview={() => handlePreview(file)}
                  onDelete={can.editFile(user, file) ? () => handleDelete(file.id, file.name) : null}
                  onShare={can.shareResources(user) ? () => setSharingFile(file) : null}
                />
              )}
            />
          )}
        </div>
{showUploadModal && (
        <UploadModal
          step={uploadStep}
          uploadFile={uploadFile_}
          uploadName={uploadName}
          uploadDescription={uploadDescription}
          uploading={uploading}
          uploadProgress={upload.progress}
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
    </PageLayout>
  );
};


export default FileManager;
