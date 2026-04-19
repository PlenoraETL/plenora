/**
 * FileManager.jsx — Gestione file caricati
 * Redesign Plenora: sidebar layout, hex bg, drop zone, card grid
 * Route: /files
 */
import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useToast } from '../hooks/useToast';
import ConfirmDialog from '../components/common/ConfirmDialog';
import { useConfirmModal } from '../hooks/useConfirmModal';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import {
  getFiles,
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
import ModalWrapper, { ModalButton } from '../components/common/ModalWrapper';
import { can, role } from '../utils/permissions';
import '../styles/FileManager.css';

// ── Format config ─────────────────────────────────────────────────────────
const FORMAT_CONFIG = {
  csv:     { icon: '📄', label: 'CSV',     color: '#34C17A', rgb: '52,193,122' },
  xlsx:    { icon: '📊', label: 'XLSX',    color: '#34C17A', rgb: '52,193,122' },
  xls:     { icon: '📊', label: 'XLS',     color: '#34C17A', rgb: '52,193,122' },
  json:    { icon: '📋', label: 'JSON',    color: '#4B8BFF', rgb: '75,139,255' },
  geojson: { icon: '🗺',  label: 'GeoJSON', color: '#4B8BFF', rgb: '75,139,255' },
  gpkg:    { icon: '📦', label: 'GPKG',    color: '#F5A623', rgb: '245,166,35' },
  shp:     { icon: '🔷', label: 'SHP',     color: '#9B6DFF', rgb: '155,109,255' },
  gdb:     { icon: '🗄',  label: 'GDB',     color: '#F87171', rgb: '248,113,113' },
};
const getFormatConfig = (fmt) => FORMAT_CONFIG[fmt] || { icon: '📁', label: (fmt||'').toUpperCase(), color: '#6B7280', rgb: '107,114,128' };

const formatFileSize = (bytes) => {
  if (!bytes) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024*1024) return `${(bytes/1024).toFixed(1)} KB`;
  return `${(bytes/1024/1024).toFixed(1)} MB`;
};

const FILTER_TABS = [
  { key: 'all',     label: 'Tutti' },
  { key: 'csv',     label: 'CSV' },
  { key: 'xlsx',    label: 'Excel' },
  { key: 'geojson', label: 'GeoJSON' },
  { key: 'spatial', label: 'Spaziali' },
];
const SPATIAL_FORMATS = ['gpkg','shp','gdb','geojson'];

// ── Theme toggle ──────────────────────────────────────────────────────────
function ThemeToggle() {
  const [theme, setThemeState] = useState(() => {
    const saved = localStorage.getItem('plenora-theme') || 'dark';
    document.documentElement.setAttribute('data-theme', saved);
    return saved;
  });
  const toggle = (t) => {
    document.documentElement.setAttribute('data-theme', t);
    localStorage.setItem('plenora-theme', t);
    setThemeState(t);
  };
  return (
    <div className="fm__theme-toggle">
      <div className={`fm__theme-opt ${theme === 'dark'  ? 'fm__theme-opt--on' : ''}`} onClick={() => toggle('dark')}>🌙 Scuro</div>
      <div className={`fm__theme-opt ${theme === 'light' ? 'fm__theme-opt--on' : ''}`} onClick={() => toggle('light')}>☀️ Chiaro</div>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────
const FileManager = () => {
  const navigate     = useNavigate();
  const toast        = useToast();
  const confirmModal = useConfirmModal();
  const { user, logout } = useAuth();
  const canvasRef    = useRef(null);
  const fileInputRef = useRef(null);
  useHexCanvas(canvasRef);

  const [files,   setFiles]   = useState([]);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState(null);
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
  const [checkedLayers,     setCheckedLayers]     = useState({});
  const [layerColumns,      setLayerColumns]      = useState({});
  const [layerSelectedCols, setLayerSelectedCols] = useState({});
  const [activeLayerTab,    setActiveLayerTab]    = useState(null);
  const [loadingLayerCols,  setLoadingLayerCols]  = useState(false);

  // Preview modal state
  const [showPreviewModal, setShowPreviewModal] = useState(false);
  const [previewData,      setPreviewData]      = useState(null);
  const [previewFile,      setPreviewFile]      = useState(null);

  // ── Load ────────────────────────────────────────────────────────────────
  const loadFiles = useCallback(async () => {
    try {
      setLoading(true);
      const data = await getFiles();
      setFiles(data.files || []);
      setError(null);
    } catch (err) {
      setError('Errore nel caricamento dei file');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadFiles(); }, [loadFiles]);

  // ── Drag & drop ─────────────────────────────────────────────────────────
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
    setUploadFile(file);
    setUploadName(file.name.replace(/\.[^/.]+$/, ''));
    setUploadError(null);
  };

  // ── Column helpers ───────────────────────────────────────────────────────
  const _loadColumnsForLayer = async (file, layerName, currentCols, currentSelected, isSheet = false) => {
    setLoadingLayerCols(true);
    try {
      const preview = await previewFileTemp(file, isSheet ? null : layerName, isSheet ? layerName : null);
      const cols = preview.columns || [];
      setLayerColumns(prev => ({ ...prev, [layerName]: cols }));
      if (!currentSelected[layerName] || currentSelected[layerName].length === 0) {
        setLayerSelectedCols(prev => ({ ...prev, [layerName]: cols.map(c => c.name) }));
      }
    } catch (e) {
      console.error(`Errore colonne layer ${layerName}:`, e);
    } finally {
      setLoadingLayerCols(false);
    }
  };

  const handleLayerTabChange = (layerName) => {
    setActiveLayerTab(layerName);
    if (!layerColumns[layerName] || layerColumns[layerName].length === 0) {
      const ext = uploadFile_?.name.split('.').pop().toLowerCase();
      _loadColumnsForLayer(uploadFile_, layerName, layerColumns, layerSelectedCols, ['xlsx','xls'].includes(ext));
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

  // ── Multi-layer upload ───────────────────────────────────────────────────
  const handleMultiLayerUpload = async () => {
    const isSheet = ['xlsx','xls'].includes(uploadFile_?.name.split('.').pop().toLowerCase());
    const selectedLayers = Object.entries(checkedLayers)
      .filter(([, checked]) => checked)
      .map(([name]) => ({ [isSheet ? 'sheet' : 'layer']: name, selected_columns: layerSelectedCols[name]?.length > 0 ? layerSelectedCols[name] : null }));
    if (selectedLayers.length === 0) { setUploadError('Seleziona almeno un layer'); return; }
    setUploading(true); setUploadError(null);
    try {
      const srid = userSrid ? parseInt(userSrid) : null;
      await uploadMultiLayer(uploadFile_, uploadName, uploadDescription, selectedLayers, srid);
      await loadFiles();
      resetUploadModal();
    } catch (err) {
      setUploadError(err.message || "Errore durante l'upload");
    } finally { setUploading(false); }
  };

  // ── Next step ────────────────────────────────────────────────────────────
  const handleNextStep = async () => {
    if (!uploadFile_ || !uploadName.trim()) { setUploadError('Seleziona un file e inserisci un nome'); return; }
    setLoadingColumns(true); setUploadError(null);
    try {
      const ext = uploadFile_.name.split('.').pop().toLowerCase();
      const isMultiLayer  = ['gpkg','gdb'].includes(ext);
      const isMultiSheet  = ['xlsx','xls'].includes(ext);

      if (isMultiLayer || isMultiSheet) {
        const preview  = await previewFileTemp(uploadFile_);
        const itemList = isMultiSheet ? (preview.sheets || []) : (preview.layers || []);
        if (itemList.length > 1) {
          const initChecked = {}, initCols = {}, initSelected = {};
          itemList.forEach(l => { initChecked[l] = true; initCols[l] = []; initSelected[l] = []; });
          setIsMultiLayerFile(true);
          setCheckedLayers(initChecked); setLayerColumns(initCols); setLayerSelectedCols(initSelected);
          setActiveLayerTab(itemList[0]); setAvailableLayers(itemList);
          setUploadStep(2); setLoadingColumns(false);
          _loadColumnsForLayer(uploadFile_, itemList[0], initCols, initSelected, isMultiSheet);
          return;
        }
        if (itemList.length === 1) { isMultiSheet ? setUploadSheet(itemList[0]) : setUploadLayer(itemList[0]); }
      }

      const preview = await previewFileTemp(uploadFile_);
      if (preview.columns?.length > 0) {
        setIsMultiLayerFile(false);
        setAvailableColumns(preview.columns);
        setSelectedColumns(preview.columns.map(c => c.name));
        const geomCols = preview.columns.filter(c => c.type?.startsWith('geometry'));
        setHasGeometryColumn(geomCols.length > 0);
        let foundSrid = null;
        for (const c of geomCols) {
          const parts = c.type.split(':');
          if (parts.length >= 3) { const s = parseInt(parts[2]); if (!isNaN(s) && s > 0) { foundSrid = s; break; } }
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
          } catch (e) { setUploadError(e.message || "Errore upload"); } finally { setUploading(false); }
        } else {
          setUploadError('Impossibile leggere le colonne del file');
        }
      }
    } catch (err) {
      setUploadError(err.message || 'Errore nella lettura del file');
    } finally { setLoadingColumns(false); }
  };

  const handlePrevStep = () => {
    setUploadStep(1); setAvailableColumns([]); setSelectedColumns([]);
    setDetectedSrid(null); setUserSrid(''); setHasGeometryColumn(false);
  };

  const handleToggleColumn    = (col) => setSelectedColumns(prev => prev.includes(col) ? prev.filter(c => c !== col) : [...prev, col]);
  const handleSelectAllColumns = ()   => setSelectedColumns(selectedColumns.length === availableColumns.length ? [] : availableColumns.map(c => c.name));

  // ── Upload ───────────────────────────────────────────────────────────────
  const handleUpload = async () => {
    if (!uploadFile_ || !uploadName.trim()) { setUploadError('Seleziona un file e inserisci un nome'); return; }
    if (selectedColumns.length === 0) { setUploadError('Seleziona almeno una colonna'); return; }
    setUploading(true); setUploadError(null);
    try {
      const columnsToImport = selectedColumns.length === availableColumns.length ? null : selectedColumns;
      const srid = hasGeometryColumn && userSrid ? parseInt(userSrid) : null;
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
      setUploadError(err.message || "Errore durante l'upload");
    } finally { setUploading(false); }
  };

  const handleConfirmLayer = async () => { if (!pendingFile) return; await loadFiles(); resetLayerModal(); };

  // ── Preview ──────────────────────────────────────────────────────────────
  const handlePreview = async (file) => {
    try {
      const preview = await getFilePreview(file.id, 50);
      setPreviewData(preview); setPreviewFile(file); setShowPreviewModal(true);
    } catch { toast.error('Errore nel caricamento della preview'); }
  };

  // ── Delete ───────────────────────────────────────────────────────────────
  const handleDelete = async (fileId, fileName) => {
    const ok = await confirmModal.confirm({ title: 'Elimina file', message: `Eliminare "${fileName}"?`, confirmText: 'Elimina', variant: 'danger' });
    if (!ok) return;
    try { await deleteFile(fileId); await loadFiles(); }
    catch { toast.error("Errore durante l'eliminazione"); }
  };

  // ── Reset ────────────────────────────────────────────────────────────────
  const resetUploadModal = () => {
    setShowUploadModal(false); setUploadFile(null); setUploadName(''); setUploadDescription('');
    setUploadLayer(''); setUploadSheet(''); setUploadError(null); setUploadStep(1);
    setAvailableColumns([]); setSelectedColumns([]); setDetectedSrid(null); setUserSrid(''); setHasGeometryColumn(false);
    setIsMultiLayerFile(false); setCheckedLayers({}); setLayerColumns({}); setLayerSelectedCols({}); setActiveLayerTab(null); setAvailableLayers([]);
  };
  const resetLayerModal = () => { setShowLayerModal(false); setPendingFile(null); setAvailableLayers([]); setAvailableSheets([]); setSelectedLayer(''); setSelectedSheet(''); };

  // ── Filtered files ───────────────────────────────────────────────────────
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

      {/* ── SIDEBAR ── */}
      <AppSidebar counts={{ files: files.length }} />

      {/* ── MAIN ── */}
      <div className="fm__main">
        <header className="fm__topbar">
          <button type="button" className="fm__back-btn" onClick={() => navigate(-1)}>← Indietro</button>
          <div className="fm__page-title">File Manager</div>
          <div className="fm__topbar-right">
            <ThemeToggle />
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
                value={search}
                onChange={e => setSearch(e.target.value)}
              />
              {search && <button type="button" className="fm__search-clear" onClick={() => setSearch('')}>✕</button>}
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
          {error && <div className="fm__error">⚠️ {error}</div>}

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
                <div className="fm__drop-title" style={{ color: 'var(--fm-ok)' }}>{uploadFile_.name}</div>
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
                <button type="button" className="fm__btn fm__btn--primary" onClick={() => setShowUploadModal(true)} style={{ marginTop: 16 }}>
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
                  onDelete={() => handleDelete(file.id, file.name)}
                  onShare={() => setSharingFile(file)}
                  isSuperadmin={role.isSuperadmin(user)}
                />
              ))}
            </div>
          )}
        </div>
      </div>

      {/* ── UPLOAD MODAL ── */}
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
          loadingLayerCols={loadingLayerCols}
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

      {/* ── LAYER MODAL ── */}
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

      {/* ── PREVIEW MODAL ── */}
      {showPreviewModal && previewData && previewFile && (
        <PreviewModal
          file={previewFile}
          data={previewData}
          onClose={() => setShowPreviewModal(false)}
        />
      )}

      {/* ── SHARE MODAL (solo superadmin) ── */}
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

// ── File Card ─────────────────────────────────────────────────────────────
function FileCard({ file, onPreview, onDelete, onShare, isSuperadmin }) {
  const cfg = getFormatConfig(file.format);
  const label = file.layer ? `${cfg.label} · ${file.layer}` : file.sheet ? `${cfg.label} · ${file.sheet}` : cfg.label;
  const canEdit = file.can_edit !== false;
  return (
    <div className="fm__card">
      <div className="fm__card-accent" style={{ background: cfg.color }} />
      <div className="fm__card-header">
        <span className="fm__card-icon">{cfg.icon}</span>
        <div className="fm__card-header-info">
          <div className="fm__card-name">{file.name}</div>
          <span className="fm__card-badge" style={{ color: cfg.color, background: `rgba(${cfg.rgb},0.08)`, borderColor: `rgba(${cfg.rgb},0.22)` }}>
            {label}
          </span>
        </div>
      </div>
      <div className="fm__card-body">
        {file.description && <div className="fm__card-desc">{file.description}</div>}
        <div className="fm__card-meta">
          <div><div className="fm__meta-key">{SPATIAL_FORMATS.includes(file.format) ? 'Features' : 'Righe'}</div><div className="fm__meta-val">{file.row_count?.toLocaleString() || '—'}</div></div>
          <div><div className="fm__meta-key">Colonne</div><div className="fm__meta-val">{file.columns?.length || 0}</div></div>
          <div><div className="fm__meta-key">Dimensione</div><div className="fm__meta-val">{formatFileSize(file.file_size)}</div></div>
          {file.crs ? (
            <div><div className="fm__meta-key">CRS</div><div className="fm__meta-val">{file.crs}</div></div>
          ) : (
            <div><div className="fm__meta-key">File</div><div className="fm__meta-val" style={{ fontSize: 10, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{file.original_filename}</div></div>
          )}
        </div>
        {file.columns?.length > 0 && (
          <div className="fm__card-columns">
            <div className="fm__columns-label">Colonne</div>
            <div className="fm__columns-list">
              {file.columns.slice(0, 5).map((col, i) => (
                <span key={i} className="fm__col-tag">{col.name}</span>
              ))}
              {file.columns.length > 5 && <span className="fm__col-more">+{file.columns.length - 5}</span>}
            </div>
          </div>
        )}
        <div className="fm__card-footer">
          <span className="fm__card-filename">{file.original_filename}</span>
          <div className="fm__card-actions">
            <button type="button" className="fm__btn fm__btn--ghost fm__btn--sm" onClick={onPreview}>👁 Preview</button>
            {isSuperadmin && (
              <button
                type="button"
                className="fm__btn fm__btn--ghost fm__btn--sm"
                title="Gestisci condivisione cross-gruppo"
                onClick={onShare}
              >🔗</button>
            )}
            {canEdit && (
              <button type="button" className="fm__btn fm__btn--danger fm__btn--sm" onClick={onDelete}>🗑</button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Upload Modal ──────────────────────────────────────────────────────────
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
  return (
    <div className="fm__modal-overlay" onClick={onClose}>
      <div className="fm__modal" onClick={e => e.stopPropagation()}>
        <div className="fm__modal-title">↑ Carica File</div>
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

// ── Layer Modal ───────────────────────────────────────────────────────────
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

// ── Preview Modal ─────────────────────────────────────────────────────────
function PreviewModal({ file, data, onClose }) {
  // columns può essere [{name, type}, ...] oppure ['col1', 'col2', ...]
  const colNames = (data.columns || []).map(c => (typeof c === 'object' ? c.name : c));
  return (
    <ModalWrapper
      isOpen={true}
      onClose={onClose}
      title={`Preview — ${file.name}`}
      icon="👁"
      subtitle={`Prime ${data.rows?.length || 0} righe · ${data.total_rows?.toLocaleString()} totali`}
      size="xl"
      footer={<ModalButton variant="primary" onClick={onClose}>Chiudi</ModalButton>}
    >
      {data.rows?.length > 0 && (
        <div className="fm__preview-table-wrap">
          <table className="fm__preview-table">
            <thead>
              <tr>{colNames.map(col => <th key={col}>{col}</th>)}</tr>
            </thead>
            <tbody>
              {data.rows.map((row, i) => (
                <tr key={i}>{colNames.map(col => <td key={col}>{row[col] != null ? String(row[col]) : '—'}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </ModalWrapper>
  );
}

export default FileManager;
