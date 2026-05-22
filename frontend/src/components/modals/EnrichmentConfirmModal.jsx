/**
 * EnrichmentConfirmModal.jsx — Conferma pre-esecuzione arricchimento
 *
 * Mostra validazione (righe valide/invalide, colonne mancanti),
 * stima del tempo e numero chiamate API prima di avviare l'esecuzione.
 * Warn se l'operazione richiede più di 30 secondi.
 */
import React from 'react';
import ModalWrapper, { ModalButton } from '../common/ModalWrapper';
import './EnrichmentConfirmModal.css';

/**
 * Modal di conferma pre-esecuzione enrichment
 * 
 * Mostra:
 * - Risultato validazione
 * - Stima tempo
 * - Statistiche deduplicazione
 * - Warning se ci sono problemi
 */
const EnrichmentConfirmModal = ({
  isOpen,
  onClose,
  onConfirm,
  validation,
  estimation,
  isValidating,
  isEstimating,
  formatTime
}) => {
  if (!isOpen) return null;

  const isLoading = isValidating || isEstimating;
  const isValid = validation?.valid ?? true;
  const hasWarnings = (validation?.warnings?.length || 0) > 0;
  const totalRows = estimation?.total_rows || validation?.total_rows || 0;
  const estimatedSeconds = estimation?.estimated_time_seconds || 0;
  const estimatedTime = estimation?.estimated_time_formatted || formatTime?.(estimatedSeconds) || '-';
  const deduplicationSavings = estimation?.deduplication_savings_percent || 0;
  const uniqueCombinations = estimation?.unique_combinations || totalRows;
  const apiCalls = estimation?.estimated_api_calls || uniqueCombinations;

  // Determina se il tempo è "lungo" (> 2 minuti)
  const isLongRunning = estimatedSeconds > 120;
  
  // Determina se ci sono problemi significativi
  const hasIssues = !isValid || (validation?.missing_columns?.length > 0);

  const title = isLoading ? '⏳ Analisi in corso...' :
                hasIssues ? '⚠️ Problemi rilevati' :
                '✅ Pronto per l\'esecuzione';

  return (
    <ModalWrapper
      isOpen={true}
      onClose={onClose}
      title={title}
      size="md"
      closeOnOverlay={!isLoading}
      closeOnEsc={!isLoading}
      footer={
        <>
          <ModalButton variant="secondary" onClick={onClose}>Annulla</ModalButton>
          <ModalButton variant="primary" onClick={onConfirm} loading={isLoading} disabled={!isValid}>
            🚀 Esegui
          </ModalButton>
        </>
      }
    >
        <div className="enrichment-confirm-content">

          {isLoading ? (
            <div className="enrichment-confirm-loading">
              <div className="enrichment-confirm-spinner"></div>
              <p>{isValidating ? 'Validazione configurazione...' : 'Calcolo stima tempo...'}</p>
            </div>
          ) : (
            <>
              {/* Validazione */}
              {!isValid && (
                <div className="enrichment-confirm-section enrichment-confirm-section--error">
                  <h3>❌ Errori di validazione</h3>
                  {validation?.missing_columns?.length > 0 && (
                    <div className="enrichment-confirm-error">
                      <strong>Colonne mancanti:</strong>
                      <ul>
                        {validation.missing_columns.map((col, i) => (
                          <li key={i}>{col}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}

              {/* Warnings */}
              {hasWarnings && (
                <div className="enrichment-confirm-section enrichment-confirm-section--warning">
                  <h3>⚠️ Avvisi</h3>
                  <ul>
                    {validation.warnings.map((warning, i) => (
                      <li key={i}>{warning}</li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Statistiche */}
              {isValid && estimation && (
                <div className="enrichment-confirm-section">
                  <h3>📊 Statistiche</h3>
                  <div className="enrichment-confirm-stats">
                    <div className="enrichment-confirm-stat">
                      <span className="enrichment-confirm-stat-value">{totalRows.toLocaleString()}</span>
                      <span className="enrichment-confirm-stat-label">Righe totali</span>
                    </div>
                    <div className="enrichment-confirm-stat">
                      <span className="enrichment-confirm-stat-value">{apiCalls.toLocaleString()}</span>
                      <span className="enrichment-confirm-stat-label">Chiamate API</span>
                    </div>
                    {deduplicationSavings > 0 && (
                      <div className="enrichment-confirm-stat enrichment-confirm-stat--success">
                        <span className="enrichment-confirm-stat-value">-{deduplicationSavings}%</span>
                        <span className="enrichment-confirm-stat-label">Deduplicazione</span>
                      </div>
                    )}
                    {/* null_param_rows_count: righe che il BE saltera'
                        per valori null nei parametri (drift FE↔BE
                        contracts-schemas P1: pre-fix il campo non era
                        mai esposto, l'utente non sapeva quante righe
                        sarebbero state skippate silenziosamente). */}
                    {validation?.null_param_rows_count > 0 && (
                      <div className="enrichment-confirm-stat enrichment-confirm-stat--warning">
                        <span className="enrichment-confirm-stat-value">
                          {validation.null_param_rows_count.toLocaleString()}
                        </span>
                        <span className="enrichment-confirm-stat-label">
                          Righe saltate (parametri null)
                        </span>
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Tempo stimato */}
              {isValid && estimation && (
                <div className={`enrichment-confirm-section enrichment-confirm-section--time ${isLongRunning ? 'enrichment-confirm-section--warning' : ''}`}>
                  <h3>⏱️ Tempo stimato</h3>
                  <div className="enrichment-confirm-time">
                    <span className="enrichment-confirm-time-value">{estimatedTime}</span>
                    {isLongRunning && (
                      <p className="enrichment-confirm-time-warning">
                        ⚠️ L'operazione potrebbe richiedere alcuni minuti. 
                        Puoi monitorare il progresso nella barra di stato.
                      </p>
                    )}
                  </div>
                </div>
              )}

              {/* Info progetto vuoto: solo se NON e' un'estrazione REST
                  generator. Per i generator, total_rows=0 e' atteso —
                  le righe vengono prodotte dalla chiamata REST stessa,
                  non da un dataframe upstream. Il backend segnala il
                  caso via warning "Estrazione REST". */}
              {isValid && totalRows === 0 && !(validation?.warnings || []).some(w =>
                String(w).toLowerCase().includes('estrazione rest')
              ) && (
                <div className="enrichment-confirm-section enrichment-confirm-section--info">
                  <h3>ℹ️ Nessun dato</h3>
                  <p>Il progetto non contiene ancora dati di output.
                     Assicurati di aver eseguito il progetto sorgente.</p>
                </div>
              )}
            </>
          )}
        </div>
    </ModalWrapper>
  );
};

export default EnrichmentConfirmModal;
