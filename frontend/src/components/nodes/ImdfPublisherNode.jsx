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
import React, { memo, useState, lazy, Suspense } from 'react';
import { Handle, Position } from 'reactflow';
import useCanvasStore from '../../store/useCanvasStore';
import { publishImdf, validateImdfPublish } from '../../api/imdf';
import { useTaskPolling } from '../../hooks/useTaskPolling';
import ImdfPublishResult from './imdf/ImdfPublishResult';
import ImdfPublishActions from './imdf/ImdfPublishActions';
import ImdfConfigFields from './imdf/ImdfConfigFields';
import { useImdfConnectionsAndFolders } from './imdf/useImdfConnectionsAndFolders';
import './ImdfPublisherNode.css';

const ImdfValidatePublishModal = lazy(() =>
  import('../modals/ImdfValidatePublishModal')
);

const ImdfPublisherNode = memo(({ data, id }) => {
  const [isExpanded, setIsExpanded]         = useState(true);

  // Stato pubblicazione in corso
  const [publishing, setPublishing]         = useState(false);
  const [publishProgress, setPublishProgress] = useState(0);
  const [publishMessage, setPublishMessage]   = useState('');
  const [publishError, setPublishError]       = useState('');

  // Polling task Celery via hook condiviso: stopPolling auto-chiamato
  // su unmount + prima di ogni nuova startPolling (no leak su retry).
  const { startPolling } = useTaskPolling({ interval: 1000 });

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

  const { connections, folders, loadingConns, loadingFolders } =
    useImdfConnectionsAndFolders({ connectionId, isAccessDenied });

  // Cleanup polling on unmount: gestito da useTaskPolling.

  const isValid = !isAccessDenied && connectionId && (fsNameTemplate || '').trim().length > 0;
  const canPublish = isValid && !!bundleTaskId && !publishing;
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

      // Polling stato task: useTaskPolling garantisce single-flight
      // (stopPolling auto-chiamato prima di ogni nuovo setInterval).
      startPolling(
        taskId,
        (status) => {
          setPublishProgress(status.progress || 0);
          setPublishMessage(status.message || '');

          if (status.status === 'success' || status.status === 'SUCCESS') {
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
            return true;
          }
          if (status.status === 'failure' || status.status === 'FAILURE') {
            setPublishing(false);
            setPublishError(status.error || status.message || 'Pubblicazione fallita');
            return true;
          }
          return false;
        },
        {
          onError: (e) => {
            setPublishing(false);
            setPublishError(`Errore polling: ${e.message || e}`);
            return true;
          },
        },
      );
    } catch (e) {
      setPublishing(false);
      setPublishError(e?.message || 'Errore pubblicazione');
    }
  };

  // Motivo per cui i pulsanti sono disabilitati (mostrato inline nel nodo
  // + come tooltip). Rende trasparente cosa manca.
  const disabledReasons = [];
  if (isAccessDenied)                       disabledReasons.push('connessione ArcGIS non accessibile al gruppo');
  if (!connectionId)                        disabledReasons.push('connessione ArcGIS');
  if (!(fsNameTemplate || '').trim())       disabledReasons.push('nome Feature Service');
  if (!bundleTaskId)                        disabledReasons.push('bundle IMDF (esegui arricchimento)');

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
      setValidateError(e?.message || 'Errore validazione');
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

          <ImdfConfigFields
            connectionId={connectionId}
            folderId={folderId}
            fsNameTemplate={fsNameTemplate}
            publishMode={publishMode}
            applyRenderers={applyRenderers}
            isAccessDenied={isAccessDenied}
            accessDeniedReason={accessDeniedReason}
            bundleTaskId={bundleTaskId}
            bundleSiteId={bundleSiteId}
            connections={connections}
            folders={folders}
            loadingConns={loadingConns}
            loadingFolders={loadingFolders}
            publishing={publishing}
            onUpdate={handleUpdate}
          />

          <ImdfPublishActions
            canPublish={canPublish}
            publishing={publishing}
            publishProgress={publishProgress}
            publishMessage={publishMessage}
            publishError={publishError}
            publishMode={publishMode}
            disabledReasons={disabledReasons}
            onValidate={handleValidate}
            onPublish={handlePublish}
          />

          {!publishing && !publishError && (
            <ImdfPublishResult lastResult={lastResult} />
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

