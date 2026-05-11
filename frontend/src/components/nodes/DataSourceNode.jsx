/**
 * DataSourceNode.jsx — Nodo canvas per sorgente Data Source
 *
 * Rappresenta un Data Source (SharePoint, FTP, cartella locale) come nodo
 * sorgente nel canvas. Permette di selezionare il datasource e il file
 * da usare come input della pipeline.
 *
 * Usato in: ArricchisciEditor (come alternativa ai nodi progetto/file)
 */
import React, { useState, useEffect, useCallback, useRef, memo } from 'react';
import { Handle, Position } from 'reactflow';
import useCanvasStore from '../../store/useCanvasStore';
import { listDataSources, listFiles, getFileSchema } from '../../api/datasources';
import useDialogFocus from '../../hooks/useDialogFocus';
import ModalWrapper from '../common/ModalWrapper';
import './DataSourceNode.css';

/**
 * DataSourceNode — nodo canvas ESTRAI
 *
 * Modalità SORGENTE (handle output a destra):
 *   Legge un file da Data Source e lo porta nella pipeline come tabella.
 *   - File Fisso: path specifico scelto dall'utente via browser
 *   - Dinamico: cartella + regola automatica (più recente, pattern, non processato)
 *
 * Modalità CARICA (handle input a sinistra):
 *   Scrive il risultato della query su Data Source.
 *   - Cartella destinazione, nome file, formato
 *
 * data props:
 *   mode            'source' | 'carica'
 *   datasource_id   string
 *   datasource_name string
 *   -- SORGENTE --
 *   file_mode       'fixed' | 'dynamic'
 *   file_path       string (fixed)
 *   folder_path     string (dynamic)
 *   dynamic_rule    'latest' | 'latest_pattern' | 'latest_unprocessed'
 *   pattern         string
 *   format          string rilevato
 *   columns         array colonne
 *   parse_options   { encoding, sep, sheet, layer }
 *   -- CARICA --
 *   dest_folder     string
 *   dest_filename   string
 *   dest_format     'csv' | 'geojson' | 'xlsx' | 'gpkg'
 */
const DataSourceNode = memo(({ id, data }) => {
  const updateNodeData = useCanvasStore(s => s.updateNodeData);
  const deleteNode     = useCanvasStore(s => s.deleteNode);
  const handleSaveQuiet = useCanvasStore(s => s.handleSaveQuiet);
const [datasources, setDatasources]         = useState([]);
  const [loadingDs, setLoadingDs]             = useState(false);
  const [browserFiles, setBrowserFiles]       = useState([]);
  const [browserPath, setBrowserPath]         = useState('');
  const [showBrowser, setShowBrowser]         = useState(false);
  const browserDialogRef = useRef(null);
  useDialogFocus(showBrowser, () => setShowBrowser(false), browserDialogRef);
  const [browserLoading, setBrowserLoading]   = useState(false);
  const [browserError, setBrowserError]       = useState(null);
  const [loadingSchema, setLoadingSchema]     = useState(false);
  const [schemaError, setSchemaError]         = useState(null);
  // Sheet/layer disponibili per file multi-tabella.
  const [availableSheets, setAvailableSheets] = useState([]);
  const [availableLayers, setAvailableLayers] = useState([]);
  const [isExpanded, setIsExpanded]           = useState(true);

  const mode         = data.mode || 'source';
  const dsId         = data.datasource_id || '';
  const fileMode     = data.file_mode || 'fixed';
  const dynamicRule  = data.dynamic_rule || 'latest';
useEffect(() => {
    const load = async () => {
      setLoadingDs(true);
      try {
        const res = await listDataSources();
        setDatasources(res.datasources || []);
      } catch { /* silenzioso */ }
      finally { setLoadingDs(false); }
    };
    load();
  }, []);
const upd = useCallback((fields) => updateNodeData(id, fields), [id, updateNodeData]);
const openBrowser = useCallback(async (path = '') => {
    if (!dsId) return;
    setShowBrowser(true);
    setBrowserPath(path);
    setBrowserLoading(true);
    setBrowserError(null);
    try {
      const files = await listFiles(dsId, { path });
      setBrowserFiles(files);
    } catch (e) {
      setBrowserError(e.message);
    } finally {
      setBrowserLoading(false);
    }
  }, [dsId]);

  const handleSelectFile = useCallback((file) => {
    if (file.type === 'folder') {
      openBrowser(file.path);
      return;
    }
    upd({ file_path: file.path, format: file.type });
    setShowBrowser(false);
    fetchSchema(file.path);
  }, [upd, openBrowser]);
const fetchSchema = useCallback(async (filePath, opts = {}) => {
    if (!dsId || !filePath) return;
    setLoadingSchema(true);
    setSchemaError(null);
    try {
      const base = await getFileSchema(dsId, filePath);
      const sheets = Array.isArray(base.sheets) ? base.sheets : [];
      const layers = Array.isArray(base.layers) ? base.layers : [];
      setAvailableSheets(sheets);
      setAvailableLayers(layers);

      const po = data.parse_options || {};
      const effSheet = opts.sheet ?? po.sheet ?? (sheets.length > 1 ? sheets[0] : null);
      const effLayer = opts.layer ?? po.layer ?? (layers.length > 1 ? layers[0] : null);

      let cols = base.columns || [];
      const needsDetail =
        (effSheet && sheets.length > 1) ||
        (effLayer && layers.length > 1);
      if (needsDetail) {
        const detail = await getFileSchema(dsId, filePath, {
          sheet: effSheet || undefined,
          layer: effLayer || undefined,
        });
        cols = detail.columns || [];
      }

      const updates = {
        columns: cols,
        format:  base.format || data.format,
      };
      const poChanged =
        (sheets.length > 1 && !po.sheet && effSheet) ||
        (layers.length > 1 && !po.layer && effLayer);
      if (poChanged) {
        const newPo = { ...po };
        if (sheets.length > 1 && !po.sheet && effSheet) newPo.sheet = effSheet;
        if (layers.length > 1 && !po.layer && effLayer) newPo.layer = effLayer;
        updates.parse_options = newPo;
      }
      upd(updates);
      setTimeout(() => { handleSaveQuiet().catch(() => {}); }, 100);
    } catch {
      setSchemaError(true);
    } finally {
      setLoadingSchema(false);
    }
  }, [dsId, upd, data.format, data.parse_options, handleSaveQuiet]);

  useEffect(() => {
    if (!dsId || !data.file_path) return;
    fetchSchema(data.file_path);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dsId, data.file_path]);
const BreadCrumb = () => {
    const parts = browserPath.split('/').filter(Boolean);
    return (
      <div className="dsn__breadcrumb">
        <button type="button" onClick={() => openBrowser('')}>root</button>
        {parts.map((seg, i) => {
          const path = parts.slice(0, i + 1).join('/');
          return (
            <React.Fragment key={path}>
              <span>/</span>
              <button type="button" onClick={() => openBrowser(path)}>{seg}</button>
            </React.Fragment>
          );
        })}
      </div>
    );
  };
const selectedDs = datasources.find(d => d.id === dsId);

  const DsSelector = () => (
    <div className="dsn__field">
      <label className="dsn__label">Connettore</label>
      {loadingDs ? (
        <div className="dsn__loading-inline">⏳ Caricamento...</div>
      ) : (
        <select
          className="dsn__select"
          value={dsId}
          onChange={e => {
            const ds = datasources.find(d => d.id === e.target.value);
            upd({ datasource_id: e.target.value, datasource_name: ds?.name || '', file_path: '', columns: [] });
          }}
        >
          <option value="">— Seleziona Data Source —</option>
          {datasources.map(ds => (
            <option key={ds.id} value={ds.id}>{ds.name} ({ds.type})</option>
          ))}
        </select>
      )}
    </div>
  );
return (
    <div className={`dsn dsn--${mode}`}>

      {/* Handle input (solo CARICA) */}
      {mode === 'carica' && (
        <Handle key="handle-carica" type="target" position={Position.Left} className="dsn__handle dsn__handle--in" id="carica-in" />
      )}
<div className="dsn__header">
        <div className="dsn__header-left">
          <span className="dsn__icon">{mode === 'source' ? '🗄️' : '💾'}</span>
          <span className="dsn__title">{mode === 'source' ? 'Data Source' : 'Carica → DS'}</span>
        </div>
        <div className="dsn__header-right">
          {/* Toggle modalità */}
          <button type="button"
            className="dsn__mode-toggle"
            title={`Passa a ${mode === 'source' ? 'modalità Carica' : 'modalità Sorgente'}`}
            onClick={() => upd({ mode: mode === 'source' ? 'carica' : 'source', columns: [] })}
          >
            {mode === 'source' ? '→ Carica' : '← Sorgente'}
          </button>
          <button type="button" className="dsn__expand-btn" onClick={() => setIsExpanded(v => !v)}>
            {isExpanded ? '▲' : '▼'}
          </button>
          <button type="button" className="dsn__delete-btn" onClick={() => deleteNode(id)}>×</button>
        </div>
      </div>

      {/* Badge DS selezionato */}
      {selectedDs && (
        <div className="dsn__ds-badge">
          {selectedDs.type === 'sharepoint' ? '☁️' :
           selectedDs.type === 'ftp' ? '📁' :
           selectedDs.type === 'sftp' ? '🔐' : '📂'} {selectedDs.name}
        </div>
      )}

      {isExpanded && (
        <div className="dsn__body">
          <DsSelector />

          {mode === 'source' && dsId && (
            <>
              {/* Selezione modalità file */}
              <div className="dsn__field">
                <label className="dsn__label">Modalità file</label>
                <div className="dsn__mode-tabs">
                  <button type="button"
                    className={`dsn__mode-tab ${fileMode === 'fixed' ? 'dsn__mode-tab--active' : ''}`}
                    onClick={() => upd({ file_mode: 'fixed' })}
                  >
                    📌 Fisso
                  </button>
                  <button type="button"
                    className={`dsn__mode-tab ${fileMode === 'dynamic' ? 'dsn__mode-tab--active' : ''}`}
                    onClick={() => upd({ file_mode: 'dynamic' })}
                  >
                    ⚡ Dinamico
                  </button>
                </div>
              </div>
{fileMode === 'fixed' && (
                <div className="dsn__field">
                  <label className="dsn__label">File selezionato</label>
                  <div className="dsn__file-picker">
                    <div className="dsn__file-path" title={data.file_path}>
                      {data.file_path || <span className="dsn__placeholder">Nessun file selezionato</span>}
                    </div>
                    <button type="button" className="dsn__browse-btn" onClick={() => openBrowser('')}>
                      📂 Sfoglia
                    </button>
                  </div>
                </div>
              )}
{fileMode === 'dynamic' && (
                <>
                  <div className="dsn__field">
                    <label className="dsn__label">Cartella</label>
                    <div className="dsn__file-picker">
                      <input
                        className="dsn__input"
                        value={data.folder_path || ''}
                        onChange={e => upd({ folder_path: e.target.value })}
                        placeholder="/estrazioni/"
                      />
                      <button type="button" className="dsn__browse-btn" onClick={() => openBrowser(data.folder_path || '')}>
                        📂
                      </button>
                    </div>
                  </div>
                  <div className="dsn__field">
                    <label className="dsn__label">Regola selezione</label>
                    <select
                      className="dsn__select"
                      value={dynamicRule}
                      onChange={e => upd({ dynamic_rule: e.target.value })}
                    >
                      <option value="latest">📅 Più recente (per data modifica)</option>
                      <option value="latest_pattern">🔍 Più recente per pattern</option>
                    </select>
                  </div>
                  {(dynamicRule === 'latest_pattern') && (
                    <div className="dsn__field">
                      <label className="dsn__label">Pattern</label>
                      <input
                        className="dsn__input"
                        value={data.pattern || ''}
                        onChange={e => upd({ pattern: e.target.value })}
                        placeholder="es. export_*.csv"
                      />
                    </div>
                  )}
                  <div className="dsn__field">
                    <label className="dsn__label">Offset (file precedenti)</label>
                    <input
                      className="dsn__input"
                      type="number"
                      min="0"
                      value={data.file_offset ?? 0}
                      onChange={e => upd({ file_offset: parseInt(e.target.value) || 0 })}
                      placeholder="0"
                    />
                    <span className="dsn__hint">0 = più recente &nbsp;·&nbsp; 1 = ieri &nbsp;·&nbsp; 2 = 2 giorni fa</span>
                  </div>
                </>
              )}

              {fileMode === 'fixed' && availableSheets.length > 1 && (
                <div className="dsn__field">
                  <label className="dsn__label">Foglio (sheet)</label>
                  <select
                    className="dsn__select"
                    value={data.parse_options?.sheet || availableSheets[0]}
                    onChange={e => {
                      const sheet = e.target.value;
                      const po = { ...(data.parse_options || {}), sheet };
                      upd({ parse_options: po });
                      fetchSchema(data.file_path, { sheet });
                    }}
                  >
                    {availableSheets.map(s => (
                      <option key={s} value={s}>{s}</option>
                    ))}
                  </select>
                </div>
              )}
              {fileMode === 'fixed' && availableLayers.length > 1 && (
                <div className="dsn__field">
                  <label className="dsn__label">Layer</label>
                  <select
                    className="dsn__select"
                    value={data.parse_options?.layer || availableLayers[0]}
                    onChange={e => {
                      const layer = e.target.value;
                      const po = { ...(data.parse_options || {}), layer };
                      upd({ parse_options: po });
                      fetchSchema(data.file_path, { layer });
                    }}
                  >
                    {availableLayers.map(l => (
                      <option key={l} value={l}>{l}</option>
                    ))}
                  </select>
                </div>
              )}

              {(mode === 'source') && (
                <div className="dsn__columns">
                  <div className="dsn__columns-header">
                    <span>Colonne{data.columns?.length ? ` (${data.columns.length})` : ''}</span>
                    {data.format && <span className="dsn__format-badge">{data.format.toUpperCase()}</span>}
                    {data.file_path && !loadingSchema && (
                      <button
                        type="button"
                        className="dsn__columns-refresh"
                        onClick={() => fetchSchema(data.file_path, data.parse_options || {})}
                        title="Ricarica schema dal file"
                        aria-label="Ricarica schema"
                      >🔄</button>
                    )}
                  </div>
                  <div className="dsn__columns-list nowheel">
                    {loadingSchema ? (
                      <div className="dsn__columns-empty">⏳ Rilevamento schema...</div>
                    ) : schemaError ? (
                      <div className="dsn__columns-empty dsn__columns-empty--err">
                        ⚠️ Schema non disponibile
                      </div>
                    ) : data.columns?.length > 0 ? (
                      data.columns.map((col, i) => (
                        <div key={i} className="dsn__column">
                          <span className="dsn__column-name">{col.name}</span>
                          <span className={`dsn__column-type dsn__column-type--${col.type?.split(':')[0] || 'str'}`}>
                            {col.type || 'str'}
                          </span>
                        </div>
                      ))
                    ) : !data.file_path ? (
                      <div className="dsn__columns-empty">
                        Seleziona un file per rilevare le colonne
                      </div>
                    ) : (
                      <div className="dsn__columns-empty">
                        Schema non rilevato
                      </div>
                    )}
                  </div>
                </div>
              )}
            </>
          )}

          {mode === 'carica' && dsId && (
            <>
              <div className="dsn__field">
                <label className="dsn__label">Cartella destinazione</label>
                <div className="dsn__file-picker">
                  <input
                    className="dsn__input"
                    value={data.dest_folder || ''}
                    onChange={e => upd({ dest_folder: e.target.value })}
                    placeholder="/output/"
                  />
                  <button type="button" className="dsn__browse-btn" onClick={() => openBrowser(data.dest_folder || '')}>
                    📂
                  </button>
                </div>
              </div>

              <div className="dsn__field">
                <label className="dsn__label">Nome file</label>
                <input
                  className="dsn__input"
                  value={data.dest_filename || ''}
                  onChange={e => upd({ dest_filename: e.target.value })}
                  placeholder="es. output_2026-02.csv"
                />
                <span className="dsn__hint">
                  Variabili disponibili: {'{date}'} {'{datetime}'} {'{project}'}<br/>
                  💡 Usa <strong>{'{date}'}</strong> per storicizzare (es. <em>output_{'{date}'}.csv</em>)
                </span>
              </div>

              <div className="dsn__field">
                <label className="dsn__label">Formato</label>
                <select
                  className="dsn__select"
                  value={data.dest_format || 'csv'}
                  onChange={e => upd({ dest_format: e.target.value })}
                >
                  <option value="csv">📄 CSV</option>
                  <option value="xlsx">📗 Excel (XLSX)</option>
                  <option value="geojson">🗺️ GeoJSON</option>
                  <option value="gpkg">🗺️ GeoPackage</option>
                </select>
              </div>
            </>
          )}

          {/* Nessun DS selezionato */}
          {!dsId && (
            <div className="dsn__hint-box dsn__hint-box--warn">
              ⚠️ Seleziona un connettore Data Source per continuare
            </div>
          )}
        </div>
      )}
{showBrowser && (
        <ModalWrapper
          isOpen={showBrowser}
          onClose={() => setShowBrowser(false)}
          title="Sfoglia file"
          icon="📂"
          size="lg"
          subtitle={browserPath ? `/${browserPath}` : '/'}
        >
          {browserPath && <BreadCrumb />}
          <div className="dsn__browser-body dsn__browser-body--modal">
            {browserLoading && <div className="dsn__browser-status">⏳ Caricamento...</div>}
            {browserError  && <div className="dsn__browser-status dsn__browser-status--err">❌ {browserError}</div>}
            {!browserLoading && !browserError && browserFiles.length === 0 && (
              <div className="dsn__browser-status">Cartella vuota</div>
            )}
            {!browserLoading && !browserError && browserFiles.map(f => (
              <div
                key={f.path}
                className={`dsn__browser-item ${f.type === 'folder' ? 'dsn__browser-item--folder' : ''}`}
                onClick={() => handleSelectFile(f)}
              >
                <span className="dsn__browser-icon">
                  {f.type === 'folder' ? '📁' :
                   f.type === 'csv'    ? '📄' :
                   f.type === 'xlsx'   ? '📗' :
                   f.type === 'geojson' || f.type === 'gpkg' ? '🗺️' : '📄'}
                </span>
                <span className="dsn__browser-name">{f.name}</span>
                {f.type !== 'folder' && (
                  <span className="dsn__browser-type">{f.type.toUpperCase()}</span>
                )}
              </div>
            ))}
          </div>
        </ModalWrapper>
      )}

      {/* Handle output (solo SORGENTE) */}
      {mode === 'source' && (
        <Handle key="handle-source" type="source" position={Position.Right} className="dsn__handle dsn__handle--out" id="source-out" />
      )}
    </div>
  );
});

DataSourceNode.displayName = 'DataSourceNode';
export default DataSourceNode;
