/**
 * ExecutionModal.jsx — Modal risultati esecuzione pipeline
 *
 * Mostra il task in esecuzione con progress bar, log in tempo reale
 * e, a completamento, le opzioni di download dell'output nei formati disponibili.
 * Usato da tutti e tre gli editor dopo il click su "Esegui".
 */
import React, { useMemo, useState } from 'react';
import { downloadProjectFile } from '../../api/projects';
import { getTicketsMeta } from '../../api/tickets';
import { useAuth } from '../../context/AuthContext';
import useCanvasStore from '../../store/useCanvasStore';
import TicketCreateModal from '../TicketCreateModal';
import TransferProgress from '../common/TransferProgress';
import { failedStepOf, failureTicketDraft, runLogPath } from '../../utils/runFailure';
import { useDownload } from '../../hooks/useDownload';
import { useResource } from '../../hooks/useResource';
import { saveBlob } from '../../utils/downloadBlob';
import ModalWrapper, { ModalButton } from '../common/ModalWrapper';
import PermissionRevokedBanner from '../common/PermissionRevokedBanner';
import { humanizeError, ERROR_MESSAGES } from '../../utils/errorMessages';
import { useSmoothProgress } from '../../hooks/useSmoothProgress';
import './ExecutionModal.css';
import { formatNumber, formatSeconds } from '../../utils/format';

// Etichette umane per i bare status token dei task carica (quando il backend
// non fornisce né un codice né un error_message più specifico).
const CARICA_STATUS_LABELS = {
  write_error: 'Scrittura fallita.',
  connection_error: 'Connessione fallita.',
  validation_error: 'Dati non validi.',
  failure: 'Esecuzione fallita.',
};

// Testo errore umano per un task carica (Epic A): codice strutturato mappato →
// messaggio curato; altrimenti error_message del backend (tradotto se è un bare
// status token); infine fallback per status.
function caricaTaskErrorText(t) {
  if (t.error && ERROR_MESSAGES[t.error]) return ERROR_MESSAGES[t.error].message;
  const em = t.error_message && String(t.error_message).trim();
  if (em) return CARICA_STATUS_LABELS[em] || em;
  if (t.error) return humanizeError({ code: t.error }).message;
  return CARICA_STATUS_LABELS[t.status] || 'Errore sconosciuto.';
}


/**
 * ExecutionModal - Modal riutilizzabile per mostrare risultati esecuzione.
 * Input, opzioni e conferme prima dell'avvio stanno nella finestra unica
 * «Prima di avviare» (editor/BeforeRunModal, Blocco A regola 5): qui solo
 * avanzamento ed esito.
 *
 * Fase 5 T2 (R4.11, R9.4, R9.8, R9-N3, R9-N4): stesse props nei tre editor
 * (hooks/useExecution.executionModalProps). Annulla per ogni tipo di
 * progetto quando il task e' partito; la finestra si chiude sempre
 * ("Continua in secondo piano", ×, Esc) e la barra la riapre; download del
 * risultato e del rapporto errori con l'errore mostrato qui.
 */
/**
 * «Segnala il problema» (U16): il ticket precompilato, montato solo quando
 * si apre (categorie e gravita' lette allora; utente dalla sessione).
 */
function FailureReport({ draft, onClose }) {
  const { user } = useAuth();
  const meta = useResource(getTicketsMeta, { key: 'tickets-meta' }).data || null;
  return <TicketCreateModal meta={meta} user={user} initial={draft} onClose={onClose} onCreated={onClose} />;
}

function ExecutionModal({
  isOpen,
  onClose,
  isExecuting,
  result,
  onDownload,
  title = '🚀 Esecuzione',
  projectType = 'estrai',
  progress = 0,
  progressMessage = '',
  taskStatus = null,
  onCancel = null,
  cancelling = false,
  cancelError = null,
  downloading = false,
  downloadError = null,
  caricaTasks = [],
  caricaPolling = false,
  // false = la finestra non va in secondo piano (Eseguibili: niente barra
  // da cui riaprirla): durante l'esecuzione resta aperta, con Annulla.
  backgroundable = true,
  // UI-3 (U16): progetto dell'esecuzione, per il registro e «Segnala».
  projectId = null,
  projectName = '',
  // UI-3 (U13, U17): avanzamento dello scaricamento; file gia' scaricato
  // (riscaricabile dalla finestra); pulizia esplicita del risultato.
  downloadProgress = null,
  downloaded = false,
  downloadNote = null,
  onDiscard = null,
}) {
  const reportDownload = useDownload();
  const [reporting, setReporting] = useState(false);
  // Il passo fallito tra i nodi dell'editor aperto (fuori dall'editor: nessuno).
  const failedStep = useMemo(
    () => (result && !result.success ? failedStepOf(result, useCanvasStore.getState().nodes) : null),
    [result],
  );
  const goToStep = (nodeId) => {
    const st = useCanvasStore.getState();
    onClose();
    const changes = st.nodes.filter(n => Boolean(n.selected) !== (n.id === nodeId)).map(n => ({ id: n.id, type: 'select', selected: n.id === nodeId }));
    if (changes.length) st.onNodesChange?.(changes);
    st.setCanvasView?.(st.canvasView, nodeId);
  };
  const logHref = runLogPath({ projectId: projectId || result?.project_id || null });
  // Progress percepita fluida (Epic C): se il backend resta a 0% la barra
  // striscia comunque verso un tetto, così non sembra bloccata.
  const smoothProgress = useSmoothProgress(progress, isExecuting);

  // Calcola il numero di colonne
  const getColumnsCount = () => {
    if (result?.columns_count !== undefined) {
      return result.columns_count;
    }
    if (result?.columns?.length !== undefined) {
      return result.columns.length;
    }
    return 0;
  };

  // Colore progress bar in base allo stato
  const getProgressColor = () => {
    if (taskStatus === 'failure') return '#f44336';
    if (taskStatus === 'revoked') return '#ff9800';
    if (taskStatus === 'success') return '#4caf50';
    return projectType === 'arricchisci' ? '#FF6B35' : '#2196f3';
  };

  // Estrai statistiche enrichment
  const enrichmentStats = result?.enrichment_stats;
  const totalErrors = enrichmentStats 
    ? (enrichmentStats.errors || 0)
    : 0;
  const hasEnrichmentErrors = totalErrors > 0;
  const errorReportPath = result?.error_report;

  const handleDownloadErrorReport = () => reportDownload.run('report', async () => {
    if (!errorReportPath || !result?.project_id) return;
    const filename = errorReportPath.split(/[/\\]/).pop();
    const r = await downloadProjectFile(result.project_id, filename);
    saveBlob(new Blob([r.data]), 'error_report.csv');
  });

  // Footer con pulsanti
  const isStillWorking = isExecuting || caricaPolling;
  const lockedOpen = isStillWorking && !backgroundable;

  const footer = (
    <div className="execution-modal__actions">
      {isExecuting && onCancel && (
        <ModalButton variant="danger" onClick={onCancel} disabled={cancelling}>
          {cancelling ? 'Annullamento…' : '🛑 Annulla'}
        </ModalButton>
      )}

      <ModalButton variant="secondary" onClick={() => onClose()} disabled={lockedOpen}>
        {isStillWorking && backgroundable ? 'Continua in secondo piano' : 'Chiudi'}
      </ModalButton>
    </div>
  );

  // Variant per colore
  const variant = projectType === 'arricchisci' ? 'arricchisci' 
    : projectType === 'manipola' ? 'manipola' 
    : 'estrai';

  // Stack unico (T4): senza secondo piano la finestra e' occupata (`busy`:
  // niente Esc, overlay ne' ×); con il secondo piano Esc e × la mandano
  // dietro, un clic sull'overlay no (si riapre dalla barra, non per sbaglio).
  return (
    <ModalWrapper
      isOpen={isOpen}
      onClose={() => onClose()}
      title={title}
      size="md"
      variant={variant}
      footer={footer}
      busy={lockedOpen}
      closeOnOverlay={!isStillWorking}
    >
      {/* Loading State con Progress Bar */}
      {isExecuting && (
        <div className="execution-modal__loading">
          <>
            <div className="execution-modal__progress-container">
              <div className="execution-modal__progress-bar">
                <div
                  className="execution-modal__progress-fill"
                  style={{
                    width: `${smoothProgress}%`,
                    backgroundColor: getProgressColor()
                  }}
                />
              </div>
              <div className="execution-modal__progress-text">
                {smoothProgress}%
              </div>
            </div>

            <p className="execution-modal__progress-message">
              {progressMessage || 'Elaborazione in corso...'}
            </p>

            <div className="execution-modal__task-status">
              {taskStatus === 'pending' && '⏳ In coda...'}
              {taskStatus === 'progress' && '⚙️ In esecuzione...'}
              {taskStatus === 'started' && '🚀 Avviato...'}
              {taskStatus === 'retry' && '🔁 Nuovo tentativo...'}
            </div>
            {cancelError && (
              <p className="execution-modal__error-message" role="alert">Annullamento non riuscito: {cancelError}</p>
            )}
            {backgroundable && (
              <p className="execution-modal__hint">Puoi chiudere questa finestra: l'esecuzione continua e la riapri dalla barra.</p>
            )}
          </>
        </div>
      )}

      {/* Result State */}
      {!isExecuting && result && (
        <div>
          {/* PR-A UX Tier 1: banner inline quando il task ha rilevato
              permessi revocati mid-esecuzione (status='permission_revoked'
              propagato dai task carica/enrichment/pipeline). */}
          {(result.permissionRevoked
            || result.status === 'permission_revoked'
            || result.result?.status === 'permission_revoked') && (
            <PermissionRevokedBanner message={result.message || result.result?.message} />
          )}
          {result.test_run && (
            <div className="execution-modal__test-run" role="status">
              <strong>Esecuzione di prova (utente: {result.test_run.username})</strong>
              {' '}— il risultato non è l&apos;Output del progetto, non aggiorna chi lo usa come sorgente, non
              ha ricalcolato i progetti a monte e non ha avviato Carica. Il file si scarica una volta.
            </div>
          )}
          {result.success ? (
            <div>
              {/* Success Message */}
              <div className={`execution-modal__success ${hasEnrichmentErrors ? 'execution-modal__success--with-warnings' : ''}`}>
                <div className="execution-modal__success-icon">
                  {hasEnrichmentErrors ? '⚠️' : '✅'}
                </div>
                <h4 className="execution-modal__success-title">
                  {hasEnrichmentErrors ? 'Completato con errori' : 'Esecuzione completata!'}
                </h4>
                <p className="execution-modal__success-message">
                  {hasEnrichmentErrors 
                    ? `${totalErrors} righe su ${enrichmentStats.total_rows} hanno generato errori`
                    : (result.message || 'Operazione eseguita con successo')
                  }
                </p>
              </div>

              {/* Stats Base */}
              <div className="execution-modal__stats">
                <div className="execution-modal__stat">
                  <div className="execution-modal__stat-label">Righe processate</div>
                  <div className="execution-modal__stat-value">
                    {formatNumber(result.rows_count || result.rows_processed || 0)}
                  </div>
                </div>
                <div className="execution-modal__stat">
                  <div className="execution-modal__stat-label">Colonne in uscita</div>
                  <div className="execution-modal__stat-value">{getColumnsCount()}</div>
                </div>
                {result.elapsed_time && (
                  <div className="execution-modal__stat">
                    <div className="execution-modal__stat-label">Tempo esecuzione</div>
                    {/* U17 (UI-3): durate da utils/format, mai secondi grezzi. */}
                    <div className="execution-modal__stat-value">{formatSeconds(result.elapsed_time)}</div>
                  </div>
                )}
              </div>

              {/* Enrichment Stats */}
              {enrichmentStats && projectType === 'arricchisci' && (
                <div className="execution-modal__enrichment-stats">
                  <div className="execution-modal__enrichment-stats-title">
                    📊 Statistiche Arricchimento
                  </div>
                  <div className="execution-modal__enrichment-stats-grid">
                    <div className="execution-modal__enrichment-stat execution-modal__enrichment-stat--success">
                      <span className="execution-modal__enrichment-stat-value">
                        {enrichmentStats.success || 0}
                      </span>
                      <span className="execution-modal__enrichment-stat-label">✅ Successi</span>
                    </div>
                    <div className="execution-modal__enrichment-stat execution-modal__enrichment-stat--cached">
                      <span className="execution-modal__enrichment-stat-value">
                        {enrichmentStats.cached || 0}
                      </span>
                      <span className="execution-modal__enrichment-stat-label">💾 Da cache</span>
                    </div>
                    <div className={`execution-modal__enrichment-stat ${totalErrors > 0 ? 'execution-modal__enrichment-stat--error' : ''}`}>
                      <span className="execution-modal__enrichment-stat-value">{totalErrors}</span>
                      <span className="execution-modal__enrichment-stat-label">❌ Errori</span>
                    </div>
                    <div className="execution-modal__enrichment-stat execution-modal__enrichment-stat--saved">
                      <span className="execution-modal__enrichment-stat-value">
                        {enrichmentStats.api_calls_saved || 0}
                      </span>
                      <span className="execution-modal__enrichment-stat-label">🚀 Call risparmiate</span>
                    </div>
                  </div>
                  
                  {enrichmentStats.avg_response_time_ms > 0 && (
                    <div className="execution-modal__enrichment-avg-time">
                      ⏱️ Tempo medio risposta: {Math.round(enrichmentStats.avg_response_time_ms)}ms
                    </div>
                  )}
                </div>
              )}

              {/* Error Report Download */}
              {hasEnrichmentErrors && errorReportPath && (
                <div className="execution-modal__error-report">
                  <div className="execution-modal__error-report-header">
                    <span className="execution-modal__error-report-icon">⚠️</span>
                    <span className="execution-modal__error-report-title">
                      Rapporto degli errori disponibile
                    </span>
                  </div>
                  <p className="execution-modal__error-report-desc">
                    {totalErrors} righe hanno generato errori durante l'arricchimento.
                    Scarica il rapporto CSV per vedere i dettagli di ogni errore.
                  </p>
                  <button
                    type="button"
                    className="execution-modal__error-report-btn"
                    onClick={handleDownloadErrorReport}
                    disabled={reportDownload.busy}
                  >
                    {reportDownload.busy ? 'Scaricamento…' : '📋 Scarica il rapporto degli errori'}
                  </button>
                  {reportDownload.error && (
                    <p className="execution-modal__error-message" role="alert">{reportDownload.error}</p>
                  )}
                </div>
              )}

              {/* Output File Info */}
              {/* P1 (2026-05-26): rimosso fallback `output_path`
                  (sempre undefined dopo pipeline RAM refactor). */}
              {result.output_file && (
                <div className="execution-modal__output">
                  <div className="execution-modal__output-label">📄 File generato</div>
                  <div className="execution-modal__output-file">
                    {result.output_file}
                  </div>
                </div>
              )}

              {/* Download Button */}
              {onDownload && (
                <button
                  type="button"
                  className="execution-modal__download-btn"
                  onClick={() => onDownload()}
                  disabled={downloading}
                >
                  {downloading ? 'Scaricamento…' : (downloaded ? '⬇️ Scarica di nuovo' : '⬇️ Scarica il file')}
                </button>
              )}
              <TransferProgress progress={downloadProgress} verb="Scaricamento" />
              {downloadNote && <p className="execution-modal__hint">{downloadNote}</p>}
              {onDiscard && (
                <ModalButton variant="secondary" onClick={() => onDiscard()} disabled={downloading}>
                  🗑 Elimina il risultato
                </ModalButton>
              )}
              {downloadError && (
                <p className="execution-modal__error-message" role="alert">{downloadError}</p>
              )}

              {/* CARICA Tasks */}
              {caricaTasks.length > 0 && (
                <div className="execution-modal__carica-section">
                  <div className="execution-modal__carica-title">
                    {caricaPolling ? '⏳ Caricamento in corso...' : '📤 Caricamento'}
                  </div>
                  {caricaTasks.map((t) => (
                    <div key={t.task_id} className={`execution-modal__carica-task execution-modal__carica-task--${t.status}`}>
                      <span className="execution-modal__carica-icon">
                        {t.status === 'success' ? '✅'
                          : (t.status === 'failure' || t.status === 'validation_error' || t.status === 'write_error' || t.status === 'connection_error') ? '❌'
                          : t.status === 'pending' ? '⏳'
                          : '⚙️'}
                      </span>
                      <span className="execution-modal__carica-info">
                        {t.status === 'success'
                          ? `${formatNumber(t.rows_written || 0)} righe scritte`
                          : (t.status === 'failure' || t.status === 'validation_error' || t.status === 'write_error' || t.status === 'connection_error')
                          ? caricaTaskErrorText(t)
                          : (t.message || 'In coda...')}
                      </span>
                    </div>
                  ))}
                </div>
              )}

              {/* Query SQL (solo per Estrai) */}
              {result.query && projectType === 'estrai' && (
                <div className="execution-modal__query">
                  <div className="execution-modal__query-label">Query SQL generata:</div>
                  <pre className="execution-modal__query-code">{result.query}</pre>
                </div>
              )}

              {/* Columns List */}
              {result.columns && result.columns.length > 0 && (
                <div className="execution-modal__columns">
                  <div className="execution-modal__columns-label">
                    Colonne in uscita ({result.columns.length}):
                  </div>
                  <div className="execution-modal__columns-list">
                    {result.columns.map((col, idx) => (
                      <span key={idx} className="execution-modal__column-tag">
                        {typeof col === 'string' ? col : col.name || col}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ) : (
            /* Error State */
            <div className="execution-modal__error">
              <div className="execution-modal__error-icon">
                {taskStatus === 'revoked' ? '🛑' : '❌'}
              </div>
              <h4 className="execution-modal__error-title">
                {taskStatus === 'revoked' ? 'Esecuzione annullata' : "Errore nell'esecuzione"}
              </h4>
              {/* U16 (UI-3): il passo che ha fallito, quando si sa. */}
              {failedStep && taskStatus !== 'revoked' && (
                <p className="execution-modal__failed-step">Passo fallito: <strong>{failedStep.title}</strong></p>
              )}
              <p className="execution-modal__error-message">
                {taskStatus === 'revoked'
                  ? (result.message || 'Esecuzione annullata')
                  : humanizeError(result).message}
              </p>
              {taskStatus !== 'revoked' && (
                <div className="execution-modal__failure-actions">
                  {failedStep && (
                    <ModalButton variant="secondary" onClick={() => goToStep(failedStep.id)}>Vai al passo</ModalButton>
                  )}
                  {/* In un'altra scheda: l'editor (o la finestra dell'Eseguibile) resta com'e'. */}
                  <a className="execution-modal__log-link" href={logHref} target="_blank" rel="noreferrer">Apri il registro</a>
                  <ModalButton variant="secondary" onClick={() => setReporting(true)}>Segnala il problema</ModalButton>
                </div>
              )}
            </div>
          )}
        </div>
      )}
      {reporting && result && (
        <FailureReport
          draft={failureTicketDraft({ projectName, projectId: projectId || result?.project_id || null, projectType, result, step: failedStep })}
          onClose={() => setReporting(false)}
        />
      )}
    </ModalWrapper>
  );
}

export default ExecutionModal;
