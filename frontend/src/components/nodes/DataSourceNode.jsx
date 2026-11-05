/**
 * DataSourceNode.jsx — Nodo canvas per sorgente Data Source
 *
 * Rappresenta un Data Source (SharePoint, FTP, cartella locale) come nodo
 * sorgente nel canvas. Permette di selezionare il datasource e il file
 * da usare come input della pipeline.
 *
 * Usato in: ArricchisciEditor (come alternativa ai nodi progetto/file)
 */
import { useResource } from '../../hooks/useResource';
import '../common/ListState.css';
import React, { useState, useEffect, useCallback, memo } from 'react';
import { Position } from '@xyflow/react';
import useCanvasStore from '../../store/useCanvasStore';
import { useConfirmOrDeny } from '../../context/ConfirmContext';
import { listDataSources, listFiles, getFileSchema } from '../../api/datasources';
import ModalWrapper from '../common/ModalWrapper';
import './DataSourceNode.css';
import NodeHandle from '../canvas/NodeHandle';
import { requestErrorMessage } from '../../utils/errorMessages';
import OptionSelect from '../common/OptionSelect';
import FieldInput from '../common/FieldInput';
import { integer } from '../../utils/fieldCodecs';
import { READ_ONLY_SAFE } from '../common/readOnlySafe';
import { pressable } from '../../utils/pressable';

// Fase 5 T5: numeri dal convertitore unico (utils/fieldCodecs).
const FILE_OFFSET = integer({ min: 0 });

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
  const isReadOnly     = useCanvasStore(s => s.isReadOnly);
  const confirm        = useConfirmOrDeny();
  const [browserPath, setBrowserPath]         = useState('');
  const [showBrowser, setShowBrowser]         = useState(false);
  // Foglio/layer scelti per il file mostrato (vale solo per quel file).
  const [schemaOpts, setSchemaOpts]           = useState({ file: null });

  const mode         = data.mode || 'source';
  const dsId         = data.datasource_id || '';
  const fileMode     = data.file_mode || 'fixed';
  const dynamicRule  = data.dynamic_rule || 'latest';
// Fase 5 T2 (R4.5, R4.8, N3): connettori, cartelle e schema dall'hook
  // comune. Lo schema e' della coppia (connettore, file) della richiesta:
  // scelto X e poi Y, lo schema di X arrivato dopo non diventa mai le
  // colonne di Y (id di richiesta). Gli errori si vedono, con «Riprova».
  const dsResource = useResource(async () => (await listDataSources()).datasources || [], { key: 'datasources' });
  const datasources = dsResource.data || [];
  const loadingDs = dsResource.initialLoading;
  const browser = useResource(() => listFiles(dsId, { path: browserPath }), {
    key: ['ds-files', dsId || null, browserPath],
    enabled: Boolean(showBrowser && dsId),
    errorMessage: requestErrorMessage,
  });
  const browserFiles = browser.data || [];
  const browserLoading = browser.loading;
  const browserError = browser.error;

  const filePath = data.file_path || null;
  const opts = schemaOpts.file === filePath ? schemaOpts : {};
  const schemaResource = useResource(async () => {
    const base = await getFileSchema(dsId, filePath);
    const sheets = Array.isArray(base.sheets) ? base.sheets : [];
    const layers = Array.isArray(base.layers) ? base.layers : [];
    const po = data.parse_options || {};
    const effSheet = opts.sheet ?? po.sheet ?? (sheets.length > 1 ? sheets[0] : null);
    const effLayer = opts.layer ?? po.layer ?? (layers.length > 1 ? layers[0] : null);
    let cols = base.columns || [];
    const needsDetail = (effSheet && sheets.length > 1) || (effLayer && layers.length > 1);
    if (needsDetail) {
      const detail = await getFileSchema(dsId, filePath, {
        sheet: effSheet || undefined,
        layer: effLayer || undefined,
      });
      cols = detail.columns || [];
    }
    const updates = { columns: cols, format: base.format || data.format };
    const poChanged = (sheets.length > 1 && !po.sheet && effSheet) || (layers.length > 1 && !po.layer && effLayer);
    if (poChanged) {
      const newPo = { ...po };
      if (sheets.length > 1 && !po.sheet && effSheet) newPo.sheet = effSheet;
      if (layers.length > 1 && !po.layer && effLayer) newPo.layer = effLayer;
      updates.parse_options = newPo;
    }
    return { sheets, layers, updates, filePath };
  }, {
    key: ['ds-schema', dsId || null, filePath, opts.sheet || null, opts.layer || null],
    enabled: Boolean(dsId && filePath),
  });
  const availableSheets = schemaResource.data?.sheets || [];
  const availableLayers = schemaResource.data?.layers || [];
  const loadingSchema = schemaResource.loading;
  const schemaError = schemaResource.status === 'error';
  // Schema letto dalla sorgente: dato DERIVATO (pendingSync), non una
  // modifica dell'utente; arriva al server col prossimo Salva/Esegui
  // (Fase 4, M7: niente salvataggi automatici). Le colonne a valle le
  // ricalcola la fonte unica (hooks/useColumns) dal canvas cambiato.
  const schemaData = schemaResource.data;
  useEffect(() => {
    if (!schemaData) return;
    if (useCanvasStore.getState().nodes.find(n => n.id === id)?.data?.file_path !== schemaData.filePath) return;
    updateNodeData(id, schemaData.updates, { derived: true });
  }, [schemaData, id, updateNodeData]);
  const fetchSchema = (path, nextOpts = {}) => {
    if (path !== filePath) return;
    const next = { file: path, sheet: nextOpts.sheet ?? opts.sheet, layer: nextOpts.layer ?? opts.layer };
    if (next.sheet === opts.sheet && next.layer === opts.layer) schemaResource.reload();
    else setSchemaOpts(next);
  };
const upd = useCallback((fields) => updateNodeData(id, fields), [id, updateNodeData]);

  // Cambio di modalita' (sorgente <-> scrittura): toglie le colonne lette,
  // quindi con colonne presenti chiede conferma (Blocco A, e.8).
  const switchMode = useCallback(async (next) => {
    if (next === mode) return;
    const count = (data.columns || []).length;
    if (count > 0) {
      const ok = await confirm({
        title: 'Cambia modalità',
        message: `Passando a «${next === 'carica' ? 'Scrittura' : 'Sorgente'}» le ${count} colonne lette dal file vengono tolte. Continuare?`,
        confirmText: 'Cambia modalità',
        variant: 'warning',
      });
      if (!ok) return;
    }
    upd({ mode: next, columns: [] });
  }, [mode, data.columns, confirm, upd]);
const openBrowser = useCallback((path = '') => {
    if (!dsId) return;
    setShowBrowser(true);
    setBrowserPath(path);
  }, [dsId]);

  const handleSelectFile = useCallback((file) => {
    if (file.type === 'folder') {
      openBrowser(file.path);
      return;
    }
    upd({ file_path: file.path, format: file.type });
    setShowBrowser(false);
  }, [upd, openBrowser]);
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
        <>
        {/* Errore sempre visibile: senza dati al posto della scelta, con
            dati come avviso sopra (hooks/useResource). */}
        {dsResource.status === 'error' && (
          <div className="dsn__loading-inline" role="alert">
            ⚠️ {dsResource.stale ? 'Connettori non aggiornati' : 'Connettori non caricati'}: {dsResource.error}{' '}
            <button type="button" {...READ_ONLY_SAFE} className="nodrag list-state__retry" onClick={() => dsResource.reload()}>Riprova</button>
          </div>
        )}
        {dsResource.hasData && (
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
        </>
      )}
    </div>
  );
return (
    <div className={`dsn dsn--${mode}`}>

      {/* Handle input (solo CARICA) */}
      {mode === 'carica' && (
        <NodeHandle key="handle-carica" type="target" position={Position.Left} className="dsn__handle dsn__handle--in" id="carica-in" />
      )}
{/* Titolo, chiusura ed eliminazione sono del pannello (NodeInspector,
          regola 6 del Blocco A); qui resta il modulo, sempre aperto. */}
      {/* Badge DS selezionato */}
      {selectedDs && (
        <div className="dsn__ds-badge">
          {selectedDs.type === 'sharepoint' ? '☁️' :
           selectedDs.type === 'ftp' ? '📁' :
           selectedDs.type === 'sftp' ? '🔐' : '📂'} {selectedDs.name}
        </div>
      )}

      <div className="dsn__body">
          <div className="dsn__field">
            <label className="dsn__label">Uso</label>
            <div className="dsn__mode-tabs" role="group" aria-label="Uso del Data Source">
              <button type="button"
                className={`dsn__mode-tab ${mode === 'source' ? 'dsn__mode-tab--active' : ''}`}
                aria-pressed={mode === 'source'}
                disabled={isReadOnly}
                onClick={() => switchMode('source')}
              >
                🗄️ Sorgente
              </button>
              <button type="button"
                className={`dsn__mode-tab ${mode === 'carica' ? 'dsn__mode-tab--active' : ''}`}
                aria-pressed={mode === 'carica'}
                disabled={isReadOnly}
                onClick={() => switchMode('carica')}
              >
                💾 Scrittura
              </button>
            </div>
          </div>

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
                      <option value="latest_pattern">🔍 Più recente per modello del nome</option>
                    </select>
                  </div>
                  {(dynamicRule === 'latest_pattern') && (
                    <div className="dsn__field">
                      <label className="dsn__label">Modello del nome</label>
                      <input
                        className="dsn__input"
                        value={data.pattern || ''}
                        onChange={e => upd({ pattern: e.target.value })}
                        placeholder="es. export_*.csv"
                      />
                    </div>
                  )}
                  <div className="dsn__field">
                    <label className="dsn__label">File precedenti da saltare</label>
                    {/* N12: campo unico, svuotato non torna "0" in silenzio. */}
                    <FieldInput
                      className="dsn__input"
                      codec={FILE_OFFSET}
                      value={data.file_offset ?? 0}
                      onCommit={v => upd({ file_offset: v })}
                      aria-label="File precedenti da saltare"
                      placeholder="0"
                    />
                    <span className="dsn__hint">0 = più recente &nbsp;·&nbsp; 1 = ieri &nbsp;·&nbsp; 2 = 2 giorni fa</span>
                  </div>
                </>
              )}

              {fileMode === 'fixed' && availableSheets.length > 1 && (
                <div className="dsn__field">
                  <label className="dsn__label">Foglio</label>
                  <OptionSelect
                    className="dsn__select"
                    value={data.parse_options?.sheet || ''}
                    options={availableSheets}
                    placeholder="Primo foglio (predefinito)"
                    onChange={e => {
                      const sheet = e.target.value;
                      upd(d => ({ parse_options: { ...(d.parse_options || {}), sheet } }));
                      fetchSchema(data.file_path, { sheet });
                    }}
                  />
                </div>
              )}
              {fileMode === 'fixed' && availableLayers.length > 1 && (
                <div className="dsn__field">
                  <label className="dsn__label">Layer</label>
                  <OptionSelect
                    className="dsn__select"
                    value={data.parse_options?.layer || ''}
                    options={availableLayers}
                    placeholder="Primo layer (predefinito)"
                    onChange={e => {
                      const layer = e.target.value;
                      upd(d => ({ parse_options: { ...(d.parse_options || {}), layer } }));
                      fetchSchema(data.file_path, { layer });
                    }}
                  />
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
                      <div className="dsn__columns-empty dsn__columns-empty--err" role="alert">
                        ⚠️ Schema non disponibile: {schemaResource.error}{' '}
                        <button type="button" {...READ_ONLY_SAFE} className="nodrag list-state__retry" onClick={() => schemaResource.reload()}>Riprova</button>
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
                    placeholder="/esportazioni/"
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
                {...pressable(() => handleSelectFile(f))}
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
        <NodeHandle key="handle-source" type="source" position={Position.Right} className="dsn__handle dsn__handle--out" id="source-out" />
      )}
    </div>
  );
});

DataSourceNode.displayName = 'DataSourceNode';
export default DataSourceNode;
