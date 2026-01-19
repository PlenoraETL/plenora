import React from 'react';
import '../styles/ExecutionModal.css';

/**
 * ExecutionModal - Modal riutilizzabile per mostrare risultati esecuzione
 * 
 * Props:
 * - isOpen: boolean - mostra/nasconde modal
 * - onClose: function - callback chiusura
 * - isExecuting: boolean - stato caricamento
 * - result: object - risultato esecuzione { success, message, rows_count, columns, output_file, output_path, query }
 * - downloadUrl: string - URL per download file
 * - title: string - titolo modal
 * - projectType: 'estrai' | 'manipola' | 'arricchisci' - per styling
 */
function ExecutionModal({ 
  isOpen, 
  onClose, 
  isExecuting, 
  result, 
  downloadUrl,
  title = '🚀 Esecuzione',
  projectType = 'estrai'
}) {
  if (!isOpen) return null;

  const handleOverlayClick = (e) => {
    if (!isExecuting && e.target === e.currentTarget) {
      onClose();
    }
  };

  const variantClass = `execution-modal--${projectType}`;

  return (
    <div className="execution-modal__overlay" onClick={handleOverlayClick}>
      <div className={`execution-modal ${variantClass}`} onClick={(e) => e.stopPropagation()}>
        <h3 className="execution-modal__title">{title}</h3>

        {/* Loading State */}
        {isExecuting && (
          <div className="execution-modal__loading">
            <div className="execution-modal__spinner">⏳</div>
            <p className="execution-modal__loading-text">
              Esecuzione in corso...
            </p>
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
                      {result.columns?.length || 0}
                    </div>
                  </div>
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
                <div className="execution-modal__error-icon">❌</div>
                <h4 className="execution-modal__error-title">
                  Errore esecuzione
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
          <button 
            className="execution-modal__btn execution-modal__btn--close"
            onClick={onClose}
            disabled={isExecuting}
          >
            Chiudi
          </button>
        </div>
      </div>
    </div>
  );
}

export default ExecutionModal;