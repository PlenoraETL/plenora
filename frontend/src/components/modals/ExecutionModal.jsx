/**
 * ExecutionModal.jsx — Modal risultati esecuzione pipeline
 *
 * Mostra il task Celery in esecuzione con progress bar, log in tempo reale
 * e, a completamento, le opzioni di download dell'output nei formati disponibili.
 * Usato da tutti e tre gli editor dopo il click su "Esegui".
 */
import React from 'react';
import { downloadProjectFile } from '../../api/projects';
import ModalWrapper, { ModalButton } from '../common/ModalWrapper';
import './ExecutionModal.css';


/**
 * ExecutionModal - Modal riutilizzabile per mostrare risultati esecuzione
 * 
 * REFACTORED: Usa ModalWrapper per struttura base
 */
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
  caricaTasks = [],
  caricaPolling = false,
}) {
  const isAsync = projectType === 'arricchisci';

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

  // C3.4 Phase 4: download del report errori passa per il boundary
  // api/projects.downloadProjectFile. Pre-C3.4 il componente faceva un
  // dynamic `import('../../api/http')` dentro l'onClick — boundary
  // violation + import dinamico inutile su un componente sempre
  // attivo dietro isOpen.
  const handleDownloadErrorReport = async () => {
    if (!errorReportPath || !result?.project_id) return;
    const filename = errorReportPath.split(/[/\\]/).pop();
    const r = await downloadProjectFile(result.project_id, filename);
    const b = window.URL.createObjectURL(new Blob([r.data]));
    const a = document.createElement('a');
    a.href = b;
    a.download = 'error_report.csv';
    a.click();
    window.URL.revokeObjectURL(b);
  };

  // Footer con pulsanti
  const isStillWorking = isExecuting || caricaPolling;

  const footer = (
    <div className="execution-modal__actions">
      {isExecuting && isAsync && onCancel && (
        <ModalButton variant="danger" onClick={onCancel}>
          🛑 Annulla
        </ModalButton>
      )}
      
      <ModalButton 
        variant="secondary" 
        onClick={onClose}
        disabled={isExecuting && !isAsync}
      >
        {isStillWorking ? 'Esegui in Background' : 'Chiudi'}
      </ModalButton>
    </div>
  );

  // Variant per colore
  const variant = projectType === 'arricchisci' ? 'arricchisci' 
    : projectType === 'manipola' ? 'manipola' 
    : 'estrai';

  return (
    <ModalWrapper
      isOpen={isOpen}
      onClose={isExecuting && !isAsync ? undefined : onClose}
      title={title}
      size="md"
      variant={variant}
      footer={footer}
      closeOnOverlay={!isExecuting}
      closeOnEsc={!isExecuting || isAsync}
      showCloseButton={!isExecuting || isAsync}
    >
      {/* Loading State con Progress Bar */}
      {isExecuting && (
        <div className="execution-modal__loading">
          {isAsync ? (
            <>
              <div className="execution-modal__progress-container">
                <div className="execution-modal__progress-bar">
                  <div 
                    className="execution-modal__progress-fill"
                    style={{ 
                      width: `${progress}%`,
                      backgroundColor: getProgressColor()
                    }}
                  />
                </div>
                <div className="execution-modal__progress-text">
                  {progress}%
                </div>
              </div>
              
              <p className="execution-modal__progress-message">
                {progressMessage || 'Elaborazione in corso...'}
              </p>
              
              <div className="execution-modal__task-status">
                {taskStatus === 'pending' && '⏳ In coda...'}
                {taskStatus === 'progress' && '⚙️ In esecuzione...'}
                {taskStatus === 'started' && '🚀 Avviato...'}
              </div>
            </>
          ) : (
            <>
              <div className="execution-modal__spinner">⏳</div>
              <p className="execution-modal__loading-text">
                Esecuzione in corso...
              </p>
            </>
          )}
        </div>
      )}

      {/* Result State */}
      {!isExecuting && result && (
        <div>
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
                    {(result.rows_count || result.rows_processed || 0).toLocaleString()}
                  </div>
                </div>
                <div className="execution-modal__stat">
                  <div className="execution-modal__stat-label">Colonne output</div>
                  <div className="execution-modal__stat-value">{getColumnsCount()}</div>
                </div>
                {result.elapsed_time && (
                  <div className="execution-modal__stat">
                    <div className="execution-modal__stat-label">Tempo esecuzione</div>
                    <div className="execution-modal__stat-value">{result.elapsed_time}s</div>
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
                      Report Errori Disponibile
                    </span>
                  </div>
                  <p className="execution-modal__error-report-desc">
                    {totalErrors} righe hanno generato errori durante l'arricchimento.
                    Scarica il report CSV per vedere i dettagli di ogni errore.
                  </p>
                  <button type="button" className="execution-modal__error-report-btn" onClick={handleDownloadErrorReport}>
                    📋 Scarica Report Errori
                  </button>
                </div>
              )}

              {/* Output File Info */}
              {(result.output_file || result.output_path) && (
                <div className="execution-modal__output">
                  <div className="execution-modal__output-label">📄 File generato</div>
                  <div className="execution-modal__output-file">
                    {result.output_file || result.output_path?.split('/').pop()}
                  </div>
                </div>
              )}

              {/* Download Button */}
              {onDownload && (
                <button
                  type="button"
                  className="execution-modal__download-btn"
                  onClick={onDownload}
                >
                  ⬇️ Scarica File
                </button>
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
                          ? `${(t.rows_written || 0).toLocaleString()} righe scritte`
                          : (t.status === 'failure' || t.status === 'validation_error' || t.status === 'write_error' || t.status === 'connection_error')
                          ? (t.error_message || t.error || 'Errore sconosciuto')
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
                    Colonne output ({result.columns.length}):
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
                {taskStatus === 'revoked' ? 'Task annullato' : 'Errore esecuzione'}
              </h4>
              <p className="execution-modal__error-message">
                {result.message || result.error || 'Errore sconosciuto'}
              </p>
            </div>
          )}
        </div>
      )}
    </ModalWrapper>
  );
}

export default ExecutionModal;