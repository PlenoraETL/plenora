import React from 'react';
import '../styles/ExecutionModal.css';

/**
 * ExecutionModal - Modal riutilizzabile per mostrare risultati esecuzione
 * 
 * Props:
 * - isOpen: boolean - mostra/nasconde modal
 * - onClose: function - callback chiusura
 * - isExecuting: boolean - stato caricamento
 * - result: object - risultato esecuzione
 * - downloadUrl: string - URL per download file
 * - title: string - titolo modal
 * - projectType: 'estrai' | 'manipola' | 'arricchisci' - per styling
 * 
 * Props per task asincroni (Arricchisci):
 * - progress: number - percentuale 0-100
 * - progressMessage: string - messaggio avanzamento
 * - taskStatus: string - stato task (pending/progress/success/failure/revoked)
 * - onCancel: function - callback per annullare task
 */
function ExecutionModal({ 
  isOpen, 
  onClose, 
  isExecuting, 
  result, 
  downloadUrl,
  title = '🚀 Esecuzione',
  projectType = 'estrai',
  // Nuove props per task asincroni
  progress = 0,
  progressMessage = '',
  taskStatus = null,
  onCancel = null
}) {
  if (!isOpen) return null;

  const handleOverlayClick = (e) => {
    if (!isExecuting && e.target === e.currentTarget) {
      onClose();
    }
  };

  const variantClass = `execution-modal--${projectType}`;
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

  return (
    <div className="execution-modal__overlay" onClick={handleOverlayClick}>
      <div className={`execution-modal ${variantClass}`} onClick={(e) => e.stopPropagation()}>
        <h3 className="execution-modal__title">{title}</h3>

        {/* Loading State con Progress Bar */}
        {isExecuting && (
          <div className="execution-modal__loading">
            {isAsync ? (
              <>
                {/* Progress Bar per task asincroni */}
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
                
                {/* Messaggio avanzamento */}
                <p className="execution-modal__progress-message">
                  {progressMessage || 'Elaborazione in corso...'}
                </p>
                
                {/* Stato task */}
                <div className="execution-modal__task-status">
                  {taskStatus === 'pending' && '⏳ In coda...'}
                  {taskStatus === 'progress' && '⚙️ In esecuzione...'}
                  {taskStatus === 'started' && '🚀 Avviato...'}
                </div>
              </>
            ) : (
              <>
                {/* Spinner classico per task sincroni */}
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
                <div className="execution-modal__success">
                  <div className="execution-modal__success-icon">✅</div>
                  <h4 className="execution-modal__success-title">
                    Esecuzione completata!
                  </h4>
                  <p className="execution-modal__success-message">
                    {result.message || 'Operazione eseguita con successo'}
                  </p>
                </div>

                {/* Stats */}
                <div className="execution-modal__stats">
                  <div className="execution-modal__stat">
                    <div className="execution-modal__stat-label">
                      Righe processate
                    </div>
                    <div className="execution-modal__stat-value">
                      {(result.rows_count || result.rows_processed || 0).toLocaleString()}
                    </div>
                  </div>
                  <div className="execution-modal__stat">
                    <div className="execution-modal__stat-label">
                      Colonne output
                    </div>
                    <div className="execution-modal__stat-value">
                      {getColumnsCount()}
                    </div>
                  </div>
                  {result.elapsed_time && (
                    <div className="execution-modal__stat">
                      <div className="execution-modal__stat-label">
                        Tempo esecuzione
                      </div>
                      <div className="execution-modal__stat-value">
                        {result.elapsed_time}s
                      </div>
                    </div>
                  )}
                </div>

                {/* Output File Info */}
                {(result.output_file || result.output_path) && (
                  <div className="execution-modal__output">
                    <div className="execution-modal__output-label">📄 File generato</div>
                    <div className="execution-modal__output-file">
                      {result.output_file || result.output_path?.split('/').pop()}
                    </div>
                    {result.output_path && (
                      <div className="execution-modal__output-path">
                        {result.output_path}
                      </div>
                    )}
                  </div>
                )}

                {/* Download Button */}
                {downloadUrl && (
                  <a 
                    href={downloadUrl}
                    className="execution-modal__download-btn"
                    download
                  >
                    ⬇️ Scarica File
                  </a>
                )}

                {/* Query SQL (solo per Estrai) */}
                {result.query && projectType === 'estrai' && (
                  <div className="execution-modal__query">
                    <div className="execution-modal__query-label">
                      Query SQL generata:
                    </div>
                    <pre className="execution-modal__query-code">
                      {result.query}
                    </pre>
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

        {/* Actions */}
        <div className="execution-modal__actions">
          {/* Pulsante Annulla (solo durante esecuzione asincrona) */}
          {isExecuting && isAsync && onCancel && (
            <button 
              className="execution-modal__btn execution-modal__btn--cancel"
              onClick={onCancel}
            >
              🛑 Annulla
            </button>
          )}
          
          <button 
            className="execution-modal__btn execution-modal__btn--close"
            onClick={onClose}
            disabled={isExecuting && !isAsync}
          >
            {isExecuting ? 'Esegui in Background' : 'Chiudi'}
          </button>
        </div>
      </div>
    </div>
  );
}

export default ExecutionModal;