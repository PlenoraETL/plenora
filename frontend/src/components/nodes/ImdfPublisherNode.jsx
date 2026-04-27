/**
 * ImdfPublisherNode.jsx — Nodo canvas per pubblicare un bundle IMDF su ArcGIS
 *
 * La pubblicazione NON e' piu' automatica nell'esecuzione del canvas:
 *  1. L'utente prima esegue l'arricchimento (REST File Processor → bundle IMDF
 *     persistito in IMDF_BUNDLES_DIR sul server)
 *  2. Il nodo result mostra il riepilogo IMDF (preview / manifest / validation)
 *  3. Quando l'utente e' soddisfatto clicca "Esegui pubblicazione" qui:
 *     POST /api/imdf/publish → task Celery asincrono → polling → esito
 *
 * Config:
 *  - Connessione ArcGIS (obbligatoria)
 *  - Folder destinazione (creata se non esiste)
 *  - Nome Feature Service (obbligatorio)
 *  - Modalita': replace | append | upsert
 *  - Apply renderers (solo replace)
 */
import React, { memo, useEffect, useRef, useState, lazy, Suspense } from 'react';
import { Handle, Position } from 'reactflow';
import useCanvasStore from '../../store/useCanvasStore';
import { listFeatureConnections, listFolders } from '../../api/features';
import { publishImdf, validateImdfPublish } from '../../api/imdf';
import { getTaskStatus } from '../../api/tasks';
import './ImdfPublisherNode.css';

const ImdfValidatePublishModal = lazy(() =>
  import('../modals/ImdfValidatePublishModal')
);

const MODE_OPTIONS = [
  { value: 'replace', label: '↻ Replace', desc: 'Cancella FS esistente e ricrea da zero' },
  { value: 'append',  label: '+ Append',  desc: 'Aggiunge features al FS esistente (no duplicate check)' },
  { value: 'upsert',  label: '⇵ Upsert',  desc: 'Cancella per business key e re-insert (nuovi OBJECTID)' },
  { value: 'update',  label: '✎ Update',  desc: 'Aggiorna features esistenti via OBJECTID (preserva history). Skip unmatched' },
];

const ImdfPublisherNode = memo(({ data, id }) => {
  const [isExpanded, setIsExpanded]         = useState(true);
  const [connections, setConnections]       = useState([]);
  const [loadingConns, setLoadingConns]     = useState(false);
  const [folders, setFolders]               = useState([]);
  const [loadingFolders, setLoadingFolders] = useState(false);

  // Stato pubblicazione in corso
  const [publishing, setPublishing]         = useState(false);
  const [publishProgress, setPublishProgress] = useState(0);
  const [publishMessage, setPublishMessage]   = useState('');
  const [publishError, setPublishError]       = useState('');
  const pollingRef = useRef(null);

  // Stato modal validazione
  const [validateOpen, setValidateOpen]       = useState(false);
  const [validateLoading, setValidateLoading] = useState(false);
  const [validateReport, setValidateReport]   = useState(null);
  const [validateError, setValidateError]     = useState('');

  const _updateNodeData = useCanvasStore(s => s.updateNodeData);
  const setLastTaskResult = useCanvasStore(s => s.setLastTaskResult);
  const handleUpdate = (field, value) => _updateNodeData(id, { [field]: value });

  // Campi di config
  const connectionId   = data.imdf_arcgis_connection_id || '';
  const folderId       = data.imdf_arcgis_folder        || '';
  const fsNameTemplate = data.imdf_fs_name_template     || '';
  const applyRenderers = !!data.imdf_apply_renderers;
  const publishMode    = data.imdf_publish_mode         || 'replace';

  // RB.7.C: backend (RB.7.B) marca data.access_denied=true quando la
  // connessione ArcGIS riferita dal canvas non e' visibile al gruppo del
  // progetto. Disabilitiamo le call API (eviterebbero 404 a vuoto su
  // listFolders/publish) e marchiamo isValid=false. Nessuna rimozione
  // automatica del nodo: l'utente deve poter sostituire la connessione
  // o eliminare il nodo.
  const isAccessDenied = !!data.access_denied;
  const accessDeniedReason = data.access_denied_reason || '';

  // Bundle attivo: letto SOLO dallo store (il Result/Output node è
  // responsabile della selezione/cronologia; il Publisher pubblica
  // quel che il browser di bundle sta mostrando).
  const imdfStore = useCanvasStore(s => s.lastTaskResult?.imdf || null);
  const bundleTaskId = imdfStore?.bundle_task_id || null;
  const bundleSiteId = imdfStore?.site_id || null;

  // Esito ultimo publish (letto reattivamente dallo store).
  const lastResult = imdfStore?.publish || null;

  // ── Carica connessioni ArcGIS ───────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoadingConns(true);
      try {
        const res = await listFeatureConnections();
        if (!cancelled) setConnections(Array.isArray(res) ? res : (res.connections || []));
      } catch (e) {
        console.error('Errore caricamento connessioni ArcGIS:', e);
      } finally {
        if (!cancelled) setLoadingConns(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // ── Carica folder quando cambia connessione ────────────────────
  useEffect(() => {
    // RB.7.C: skip API call quando la connection e' marcata access_denied
    // dal backend (eviterebbe 404 a vuoto su listFolders).
    if (isAccessDenied) { setFolders([]); return; }
    if (!connectionId) { setFolders([]); return; }
    let cancelled = false;
    (async () => {
      setLoadingFolders(true);
      try {
        const res = await listFolders(connectionId);
        if (!cancelled) setFolders(res?.folders || res || []);
      } catch (e) {
        if (!cancelled) setFolders([]);
      } finally {
        if (!cancelled) setLoadingFolders(false);
      }
    })();
    return () => { cancelled = true; };
  }, [connectionId, isAccessDenied]);

  // ── Cleanup polling on unmount ─────────────────────────────────
  useEffect(() => () => {
    if (pollingRef.current) clearInterval(pollingRef.current);
  }, []);

  const isValid = !isAccessDenied && connectionId && (fsNameTemplate || '').trim().length > 0;
  const canPublish = isValid && !!bundleTaskId && !publishing;

  // ── Avvia pubblicazione ──────────────────────────────────────────
  const handlePublish = async () => {
    if (!canPublish) return;
    setPublishing(true);
    setPublishProgress(0);
    setPublishMessage('Avvio pubblicazione...');
    setPublishError('');

    try {
      // Folder name: trova il title dal folderId selezionato
      const folderEntry = folders.find(f => (f.title || f.id) === folderId);
      const folderName = folderEntry?.title || (folderId || null);

      const resp = await publishImdf({
        bundle_task_id:  bundleTaskId,
        connection_id:   connectionId,
        fs_name:         fsNameTemplate.trim(),
        folder:          folderName,
        mode:            publishMode,
        apply_renderers: applyRenderers,
      });

      const taskId = resp.task_id;
      setPublishMessage(resp.message || 'In coda...');

      // Polling stato task ogni secondo
      pollingRef.current = setInterval(async () => {
        try {
          const status = await getTaskStatus(taskId);
          setPublishProgress(status.progress || 0);
          setPublishMessage(status.message || '');

          if (status.status === 'success' || status.status === 'SUCCESS') {
            clearInterval(pollingRef.current);
            pollingRef.current = null;
            setPublishing(false);
            setPublishProgress(100);

            // Merge pubblicazione nel lastTaskResult così i modali del result
            // node (ManifestModal) la vedono reattivamente.
            const pubResult = status.result?.imdf?.publish;
            if (pubResult) {
              const prev = useCanvasStore.getState().lastTaskResult || {};
              setLastTaskResult({
                ...prev,
                imdf: { ...(prev.imdf || {}), publish: pubResult },
              });
            }
          } else if (status.status === 'failure' || status.status === 'FAILURE') {
            clearInterval(pollingRef.current);
            pollingRef.current = null;
            setPublishing(false);
            setPublishError(status.error || status.message || 'Pubblicazione fallita');
          }
        } catch (e) {
          clearInterval(pollingRef.current);
          pollingRef.current = null;
          setPublishing(false);
          setPublishError(`Errore polling: ${e.message || e}`);
        }
      }, 1000);
    } catch (e) {
      setPublishing(false);
      setPublishError(e?.response?.data?.detail || e.message || 'Errore pubblicazione');
    }
  };

  // Motivo per cui i pulsanti sono disabilitati (mostrato inline nel nodo
  // + come tooltip). Rende trasparente cosa manca.
  const disabledReasons = [];
  if (isAccessDenied)                       disabledReasons.push('connessione ArcGIS non accessibile al gruppo');
  if (!connectionId)                        disabledReasons.push('connessione ArcGIS');
  if (!(fsNameTemplate || '').trim())       disabledReasons.push('nome Feature Service');
  if (!bundleTaskId)                        disabledReasons.push('bundle IMDF (esegui arricchimento)');
  const publishTooltip = disabledReasons.length > 0
    ? `Manca: ${disabledReasons.join(', ')}`
    : `Pubblica su ArcGIS (${publishMode})`;

  // ── Apri modal validazione e lancia la call ─────────────────────
  const handleValidate = async () => {
    if (!canPublish) return;
    setValidateOpen(true);
    setValidateLoading(true);
    setValidateReport(null);
    setValidateError('');
    try {
      const folderEntry = folders.find(f => (f.title || f.id) === folderId);
      const folderName = folderEntry?.title || (folderId || null);
      const report = await validateImdfPublish({
        bundle_task_id: bundleTaskId,
        connection_id:  connectionId,
        fs_name:        fsNameTemplate.trim(),
        mode:           publishMode,
        folder:         folderName,
      });
      setValidateReport(report);
    } catch (e) {
      setValidateError(e?.response?.data?.detail || e.message || 'Errore validazione');
    } finally {
      setValidateLoading(false);
    }
  };

  // Dal modal, dopo validazione OK, l'utente clicca Procedi → esegue publish
  const handleValidateProceed = () => {
    setValidateOpen(false);
    handlePublish();
  };

  return (
    <div className={`imdf-pub-node ${!isValid ? 'imdf-pub-node--invalid' : ''}`}>
      <Handle
        type="target"
        position={Position.Left}
        className="imdf-pub-node__handle imdf-pub-node__handle--input"
      />

      {/* Header */}
      <div
        className="imdf-pub-node__header"
        role="button"
        tabIndex={0}
        onClick={() => setIsExpanded(v => !v)}
        onKeyDown={e => (e.key === 'Enter' || e.key === ' ') && setIsExpanded(v => !v)}
      >
        <div className="imdf-pub-node__header-left">
          <span className="imdf-pub-node__icon">🗺️</span>
          <span className="imdf-pub-node__title">Pubblica IMDF su ArcGIS</span>
        </div>
        <div className="imdf-pub-node__header-right">
          <span className="imdf-pub-node__expand-icon">{isExpanded ? '▼' : '▶'}</span>
          <button
            type="button"
            className="imdf-pub-node__delete-btn"
            title="Elimina nodo"
            onClick={(e) => { e.stopPropagation(); useCanvasStore.getState().deleteNode(id); }}
          >×</button>
        </div>
      </div>

      {isExpanded && (
        <div className="imdf-pub-node__body">

          {/* RB.7.C: connessione ArcGIS non accessibile al gruppo del progetto.
              Banner visibile, no rimozione automatica del nodo. */}
          {isAccessDenied && (
            <div
              role="alert"
              title={accessDeniedReason || 'Connessione ArcGIS non accessibile'}
              style={{
                background: '#fff4e5',
                border: '1px solid #d97706',
                borderRadius: 4,
                color: '#92400e',
                padding: '8px 10px',
                marginBottom: 8,
              }}
            >
              🔒 Connessione ArcGIS non accessibile al tuo gruppo.
              {accessDeniedReason ? (
                <div style={{ fontSize: '0.85em', marginTop: 4, opacity: 0.85 }}>
                  {accessDeniedReason}
                </div>
              ) : null}
            </div>
          )}

          {/* Bundle read-only: letto dall'Output node (store condiviso) */}
          <div className={`imdf-pub-node__bundle ${bundleTaskId ? 'ok' : 'missing'}`}>
            {bundleTaskId ? (
              <>
                📦 Bundle: <strong>{bundleSiteId || bundleTaskId.slice(0, 8)}</strong>
                <span style={{ fontSize: 9, opacity: 0.6, marginLeft: 8 }}>
                  (gestito dall'Output node)
                </span>
              </>
            ) : (
              <>⚠ Nessun bundle — esegui il canvas o selezionalo dall'Output</>
            )}
          </div>

          {/* Connessione */}
          <div className="imdf-pub-node__section">
            <label className="imdf-pub-node__label">Connessione ArcGIS</label>
            <select
              value={connectionId}
              onChange={e => handleUpdate('imdf_arcgis_connection_id', e.target.value)}
              className="imdf-pub-node__select"
              disabled={loadingConns || publishing}
            >
              <option value="">
                {loadingConns ? 'Caricamento...' : 'Seleziona connessione...'}
              </option>
              {connections.map(c => (
                <option key={c.id} value={c.id}>
                  {c.name}{c.server_type ? ` (${c.server_type})` : ''}
                </option>
              ))}
            </select>
          </div>

          {/* Folder */}
          <div className="imdf-pub-node__section">
            <label className="imdf-pub-node__label">Cartella destinazione</label>
            <select
              value={folderId}
              onChange={e => handleUpdate('imdf_arcgis_folder', e.target.value)}
              className="imdf-pub-node__select"
              disabled={!connectionId || loadingFolders || publishing}
            >
              <option value="">{loadingFolders ? 'Caricamento...' : '(root)'}</option>
              {folders.filter(f => f.id).map((f, i) => (
                <option key={i} value={f.title || f.id}>{f.title || f.id}</option>
              ))}
            </select>
          </div>

          {/* Nome FS */}
          <div className="imdf-pub-node__section">
            <label className="imdf-pub-node__label">Nome Feature Service</label>
            <input
              type="text"
              value={fsNameTemplate}
              onChange={e => handleUpdate('imdf_fs_name_template', e.target.value)}
              placeholder="es. imdf_LO2244"
              className="imdf-pub-node__input"
              disabled={publishing}
            />
          </div>

          {/* Modalita' */}
          <div className="imdf-pub-node__section">
            <label className="imdf-pub-node__label">Modalita'</label>
            <select
              value={publishMode}
              onChange={e => handleUpdate('imdf_publish_mode', e.target.value)}
              className="imdf-pub-node__select"
              disabled={publishing}
            >
              {MODE_OPTIONS.map(m => (
                <option key={m.value} value={m.value}>{m.label}</option>
              ))}
            </select>
            <div className="imdf-pub-node__hint">
              {MODE_OPTIONS.find(m => m.value === publishMode)?.desc}
            </div>
          </div>

          {/* Apply renderers (solo replace) */}
          {publishMode === 'replace' && (
            <div className="imdf-pub-node__section">
              <label className="imdf-pub-node__checkbox">
                <input
                  type="checkbox"
                  checked={applyRenderers}
                  onChange={e => handleUpdate('imdf_apply_renderers', e.target.checked)}
                  disabled={publishing}
                />
                <span>Applica stili da renderers.json</span>
              </label>
            </div>
          )}

          {/* Stato validazione config: mostra i motivi specifici */}
          {disabledReasons.length > 0 && (
            <div className="imdf-pub-node__warning">
              Manca: {disabledReasons.join(', ')}
            </div>
          )}

          {/* Pulsanti Valida + Esegui */}
          <div className="imdf-pub-node__actions">
            <button
              type="button"
              className="imdf-pub-node__validate-btn"
              onClick={handleValidate}
              disabled={!canPublish}
              title={!canPublish ? publishTooltip : "Controlla cosa verra' pubblicato"}
            >
              🔍 Valida
            </button>
            <button
              type="button"
              className="imdf-pub-node__run-btn"
              onClick={handlePublish}
              disabled={!canPublish}
              title={publishTooltip}
            >
              {publishing
                ? `⏳ ${publishProgress}%`
                : `▶ Esegui`}
            </button>
          </div>

          {/* Progress bar durante il publish */}
          {publishing && (
            <div className="imdf-pub-node__progress">
              <div className="imdf-pub-node__progress-bar">
                <div
                  className="imdf-pub-node__progress-fill"
                  style={{ width: `${publishProgress}%` }}
                />
              </div>
              <div className="imdf-pub-node__progress-msg">
                {publishMessage || 'Elaborazione...'}
              </div>
            </div>
          )}

          {/* Errore publish */}
          {publishError && (
            <div className="imdf-pub-node__result imdf-pub-node__result--error">
              <div style={{ fontWeight: 600 }}>✗ Pubblicazione fallita</div>
              <div className="imdf-pub-node__result-err">{publishError}</div>
            </div>
          )}

          {/* Risultato ultima pubblicazione */}
          {!publishing && !publishError && lastResult && (
            <div className={`imdf-pub-node__result ${lastResult.success ? 'imdf-pub-node__result--success' : 'imdf-pub-node__result--error'}`}>
              <div style={{ fontWeight: 600 }}>
                {lastResult.success ? '✓ Pubblicato su ArcGIS' : '✗ Errore pubblicazione'}
                {lastResult.mode ? ` (${lastResult.mode})` : ''}
              </div>

              {lastResult.success && Array.isArray(lastResult.layers) && (
                <div className="imdf-pub-node__result-stats">
                  {lastResult.layers.map((l) => {
                    // Mostra le stat rilevanti per la mode
                    const bits = [];
                    if (l.updated_count) bits.push(`~${l.updated_count}`);
                    if (l.added_count)   bits.push(`+${l.added_count}`);
                    if (l.deleted_count) bits.push(`−${l.deleted_count}`);
                    if (l.skipped_count) bits.push(`×${l.skipped_count}`);
                    if (bits.length === 0) bits.push(`${l.feature_count ?? 0} feat.`);
                    return (
                      <div key={l.id ?? l.name} className="imdf-pub-node__result-row">
                        <span>{l.name}</span>
                        <span>{bits.join(' ')}</span>
                      </div>
                    );
                  })}
                </div>
              )}

              {lastResult.success && lastResult.renderers_applied != null && (
                <div className="imdf-pub-node__result-row">
                  <span>Renderer applicati</span>
                  <span>{lastResult.renderers_applied}/{lastResult.layers?.length ?? 0}</span>
                </div>
              )}

              {lastResult.service_url && (
                <a
                  href={lastResult.service_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="imdf-pub-node__result-url"
                  title={lastResult.service_url}
                  onClick={(e) => e.stopPropagation()}
                >
                  ↗ Apri Feature Service
                </a>
              )}

              {!lastResult.success && Array.isArray(lastResult.errors) && lastResult.errors.length > 0 && (
                <div className="imdf-pub-node__result-err">
                  {lastResult.errors.slice(0, 3).join(' · ')}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Modal pre-validazione */}
      {validateOpen && (
        <Suspense fallback={null}>
          <ImdfValidatePublishModal
            isOpen={validateOpen}
            onClose={() => setValidateOpen(false)}
            report={validateReport}
            loading={validateLoading}
            error={validateError}
            onProceed={handleValidateProceed}
            mode={publishMode}
            fsName={fsNameTemplate}
          />
        </Suspense>
      )}

    </div>
  );
});

ImdfPublisherNode.displayName = 'ImdfPublisherNode';

export default ImdfPublisherNode;

