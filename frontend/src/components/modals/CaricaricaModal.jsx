/**
 * CaricaricaModal.jsx — Modal progresso caricamento su database
 *
 * Mostra l'avanzamento dell'operazione di scrittura su DB avviata da CaricanNode.
 * Include log real-time, count righe inserite/aggiornate e gestione errori.
 */
import React from 'react';
import ModalWrapper, { ModalButton } from '../common/ModalWrapper';
import './CaricaricaModal.css';

/**
 * CaricaricaModal - Modal per mostrare progresso e risultati del caricamento su database
 * 
 * Props:
 * - isOpen: boolean
 * - onClose: function
 * - isExecuting: boolean
 * - result: object con risultato finale
 * - progress: number (0-100)
 * - progressMessage: string
 * - taskStatus: 'pending' | 'progress' | 'success' | 'failure' | 'revoked'
 * - caricaricaInfo: { tableName, writeMode, totalRows, rowsWritten, ... }
 * - onCancel: function per annullare il task
 */
function CaricaricaModal({
  isOpen,
  onClose,
  isExecuting,
  result,
  progress = 0,
  progressMessage = '',
  taskStatus = null,
  caricaricaInfo = {},
  onCancel = null
}) {
  // Colore progress bar
  const getProgressColor = () => {
    if (taskStatus === 'failure') return '#f44336';
    if (taskStatus === 'revoked') return '#ff9800';
    if (taskStatus === 'success') return '#4caf50';
    return '#7c3aed'; // Viola per CARICA
  };

  // Label modalità scrittura
  const getWriteModeLabel = (mode) => {
    const modes = {
      create: '🆕 CREATE',
      replace: '🔄 REPLACE',
      append: '➕ APPEND',
      upsert: '🔀 UPSERT',
      update: '✏️ UPDATE',
      truncate_insert: '🗑️ TRUNCATE + INSERT'
    };
    return modes[mode] || mode;
  };

  // Determina se ci sono errori
  const hasErrors = (caricaricaInfo.rowsFailed || 0) > 0;
  const isSuccess = result?.success || result?.status === 'success';

  // Footer
  const footer = (
    <div className="caricarica-modal__actions">
      {isExecuting && onCancel && (
        <ModalButton variant="danger" onClick={onCancel}>
          🛑 Annulla
        </ModalButton>
      )}
      
      <ModalButton 
        variant="secondary" 
        onClick={onClose}
      >
        {isExecuting ? 'Esegui in Background' : 'Chiudi'}
      </ModalButton>
    </div>
  );

  return (
    <ModalWrapper
      isOpen={isOpen}
      onClose={onClose}
      title="💾 Caricamento su Database"
      size="md"
      variant="carica"
      footer={footer}
      closeOnOverlay={!isExecuting}
      closeOnEsc={true}
      showCloseButton={true}
    >
      {/* Info destinazione */}
      {caricaricaInfo.tableName && (
        <div className="caricarica-modal__destination">
          <div className="caricarica-modal__destination-row">
            <span className="caricarica-modal__destination-label">📋 Tabella:</span>
            <span className="caricarica-modal__destination-value">{caricaricaInfo.tableName}</span>
          </div>
          <div className="caricarica-modal__destination-row">
            <span className="caricarica-modal__destination-label">⚙️ Modalità:</span>
            <span className="caricarica-modal__destination-value">
              {getWriteModeLabel(caricaricaInfo.writeMode)}
            </span>
          </div>
        </div>
      )}

      {/* Loading State */}
      {isExecuting && (
        <div className="caricarica-modal__loading">
          <div className="caricarica-modal__progress-container">
            <div className="caricarica-modal__progress-bar">
              <div 
                className="caricarica-modal__progress-fill"
                style={{ 
                  width: `${progress}%`,
                  backgroundColor: getProgressColor()
                }}
              />
            </div>
            <div className="caricarica-modal__progress-text">
              {progress}%
            </div>
          </div>

          <p className="caricarica-modal__progress-message">
            {progressMessage || 'Elaborazione in corso...'}
          </p>

          <div className="caricarica-modal__task-status">
            {taskStatus === 'pending' && '⏳ In coda...'}
            {taskStatus === 'progress' && '⚙️ Scrittura in corso...'}
            {taskStatus === 'started' && '🚀 Avviato...'}
          </div>

          {/* Live stats durante esecuzione */}
          {caricaricaInfo.batchesTotal > 0 && (
            <div className="caricarica-modal__live-stats">
              <div className="caricarica-modal__live-stat">
                <span className="caricarica-modal__live-stat-value">
                  {caricaricaInfo.rowsWritten}/{caricaricaInfo.totalRows}
                </span>
                <span className="caricarica-modal__live-stat-label">righe scritte</span>
              </div>
              <div className="caricarica-modal__live-stat">
                <span className="caricarica-modal__live-stat-value">
                  {caricaricaInfo.batchesCompleted}/{caricaricaInfo.batchesTotal}
                </span>
                <span className="caricarica-modal__live-stat-label">batch completati</span>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Result State */}
      {!isExecuting && result && (
        <div className="caricarica-modal__result">
          {isSuccess ? (
            <>
              {/* Success Message */}
              <div className={`caricarica-modal__success ${hasErrors ? 'caricarica-modal__success--with-warnings' : ''}`}>
                <div className="caricarica-modal__success-icon">
                  {hasErrors ? '⚠️' : '✅'}
                </div>
                <h4 className="caricarica-modal__success-title">
                  {hasErrors ? 'Completato con errori' : 'Caricamento completato!'}
                </h4>
                <p className="caricarica-modal__success-message">
                  {hasErrors 
                    ? `${caricaricaInfo.rowsFailed} righe su ${caricaricaInfo.totalRows} non sono state caricate`
                    : `${caricaricaInfo.rowsWritten} righe caricate con successo`
                  }
                </p>
              </div>

              {/* Stats Grid */}
              <div className="caricarica-modal__stats">
                <div className="caricarica-modal__stat caricarica-modal__stat--success">
                  <div className="caricarica-modal__stat-value">{caricaricaInfo.rowsWritten || 0}</div>
                  <div className="caricarica-modal__stat-label">✅ Scritte</div>
                </div>
                <div className={`caricarica-modal__stat ${hasErrors ? 'caricarica-modal__stat--error' : ''}`}>
                  <div className="caricarica-modal__stat-value">{caricaricaInfo.rowsFailed || 0}</div>
                  <div className="caricarica-modal__stat-label">❌ Fallite</div>
                </div>
                <div className="caricarica-modal__stat">
                  <div className="caricarica-modal__stat-value">{caricaricaInfo.rowsSkipped || 0}</div>
                  <div className="caricarica-modal__stat-label">⏭️ Saltate</div>
                </div>
                <div className="caricarica-modal__stat">
                  <div className="caricarica-modal__stat-value">{caricaricaInfo.totalRows || 0}</div>
                  <div className="caricarica-modal__stat-label">📊 Totale</div>
                </div>
              </div>

              {/* Additional Info */}
              <div className="caricarica-modal__info">
                {caricaricaInfo.successRate !== undefined && (
                  <div className="caricarica-modal__info-row">
                    <span>Tasso di successo:</span>
                    <span className={caricaricaInfo.successRate === 100 ? 'caricarica-modal__info-value--success' : ''}>
                      {caricaricaInfo.successRate.toFixed(1)}%
                    </span>
                  </div>
                )}
                {caricaricaInfo.duration && (
                  <div className="caricarica-modal__info-row">
                    <span>Durata:</span>
                    <span>{caricaricaInfo.duration.toFixed(2)}s</span>
                  </div>
                )}
                {caricaricaInfo.batchesTotal > 0 && (
                  <div className="caricarica-modal__info-row">
                    <span>Batch:</span>
                    <span>{caricaricaInfo.batchesCompleted}/{caricaricaInfo.batchesTotal}</span>
                  </div>
                )}
              </div>
            </>
          ) : (
            /* Error State */
            <div className="caricarica-modal__error">
              <div className="caricarica-modal__error-icon">
                {taskStatus === 'revoked' ? '🛑' : '❌'}
              </div>
              <h4 className="caricarica-modal__error-title">
                {taskStatus === 'revoked' ? 'Caricamento annullato' : 'Errore caricamento'}
              </h4>
              <p className="caricarica-modal__error-message">
                {result.message || result.error_message || 'Errore sconosciuto'}
              </p>
            </div>
          )}
        </div>
      )}
    </ModalWrapper>
  );
}

export default CaricaricaModal;
