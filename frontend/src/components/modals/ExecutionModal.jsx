/**
 * ExecutionModal.jsx — Modal risultati esecuzione pipeline
 *
 * Mostra il task in esecuzione con progress bar, log in tempo reale
 * e, a completamento, le opzioni di download dell'output nei formati disponibili.
 * Usato da tutti e tre gli editor dopo il click su "Esegui".
 */
import React, { useState } from 'react';
import { downloadProjectFile } from '../../api/projects';
import ModalWrapper, { ModalButton } from '../common/ModalWrapper';
import PermissionRevokedBanner from '../common/PermissionRevokedBanner';
import { role } from '../../utils/permissions';
import { humanizeError, ERROR_MESSAGES } from '../../utils/errorMessages';
import { useSmoothProgress } from '../../hooks/useSmoothProgress';
import './ExecutionModal.css';

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

// R-25 PR-B5: lista mode supportati lato BE (vedi
// orchestrator/router.py:133-143 + arricchisci/router.py:792-801).
// L'opzione "" (default) significa "non inviare il param" -> il BE
// usa la INTERMEDIATE_PERSISTENCE_MODE delle settings globali.
const INTERMEDIATE_MODE_OPTIONS = [
  { value: '',               label: 'Default (settings globali)' },
  { value: 'metadata_only',  label: 'metadata_only' },
  { value: 'full_artifact',  label: 'full_artifact' },
];


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
  // R-25 PR-B5 — opt-in: se awaitingLaunch + onLaunch sono passati il
  // modal mostra uno step "config" prima di partire (toggle intermediate_mode
  // visibile SOLO ai superadmin). Caller esistenti che non passano
  // questi prop hanno comportamento invariato.
  awaitingLaunch = false,
  onLaunch = null,
  user = null,
}) {
  const isAsync = projectType === 'arricchisci';
  const isSuper = role.isSuperadmin(user);
  const showConfig = awaitingLaunch && !isExecuting && !result && typeof onLaunch === 'function';
  const [intermediateMode, setIntermediateMode] = useState('');
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

  const handleLaunchClick = () => {
    if (typeof onLaunch !== 'function') return;
    // Stringa vuota = "default": passa null al hook che decide se
    // serializzare il query param a livello API.
    onLaunch(intermediateMode || null);
  };

  const footer = showConfig ? (
    <div className="execution-modal__actions">
      <ModalButton variant="secondary" onClick={onClose}>
        Annulla
      </ModalButton>
      <ModalButton variant="primary" onClick={handleLaunchClick}>
        ▶ Avvia esecuzione
      </ModalButton>
    </div>
  ) : (
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
      {/* R-25 PR-B5: config step (pre-launch). Visibile solo se il consumer
          ha passato awaitingLaunch+onLaunch. Il toggle intermediate_mode e'
          mostrato solo a superadmin (BE comunque rifiuta 403 a non-super,
          questo e' solo UX). Per gli altri ruoli mostra solo la conferma
          di lancio. */}
      {showConfig && (
        <div className="execution-modal__config">
          <p className="execution-modal__config-msg">
            Pronto a lanciare l'esecuzione. Conferma con &quot;Avvia esecuzione&quot;.
          </p>
          {isSuper && (
            <div className="execution-modal__config-row">
              <label
                htmlFor="execution-modal-intermediate-mode"
                className="execution-modal__config-label"
              >
                🔧 intermediate_mode (staff)
              </label>
              <select
                id="execution-modal-intermediate-mode"
                className="execution-modal__config-select"
                value={intermediateMode}
                onChange={(e) => setIntermediateMode(e.target.value)}
              >
                {INTERMEDIATE_MODE_OPTIONS.map((opt) => (
                  <option key={opt.value || 'default'} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
              <span className="execution-modal__config-hint">
                Override per-call della modalita&apos; di persistenza intermedi.
                &quot;Default&quot; non invia il param.
              </span>
            </div>
          )}
        </div>
      )}

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
            </div>
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
                {taskStatus === 'revoked'
                  ? (result.message || 'Task annullato')
                  : humanizeError(result).message}
              </p>
            </div>
          )}
        </div>
      )}
    </ModalWrapper>
  );
}

export default ExecutionModal;
