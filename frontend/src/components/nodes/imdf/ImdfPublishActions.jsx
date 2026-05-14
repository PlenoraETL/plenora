import React from 'react';

/**
 * Blocco azioni + stato di pubblicazione del nodo IMDF Publisher.
 *
 * Raggruppa 4 sezioni che funzionalmente sono "stato post-config":
 *  - Warning "Manca: ..." con i motivi per cui il bottone Esegui e'
 *    disabled (lista verbose dei missing field).
 *  - Bottoni "Valida" + "Esegui" con tooltip dinamici.
 *  - Progress bar durante una pub in corso (percentuale + messaggio).
 *  - Banner error se la pub e' fallita.
 *
 * Il blocco `lastResult` (success con stat per layer) e' in
 * `ImdfPublishResult` — qui si gestisce solo lo state transitorio
 * o di errore della pub corrente.
 */
function ImdfPublishActions({
  canPublish,
  publishing,
  publishProgress,
  publishMessage,
  publishError,
  publishMode,
  disabledReasons,
  onValidate,
  onPublish,
}) {
  const publishTooltip = disabledReasons.length > 0
    ? `Manca: ${disabledReasons.join(', ')}`
    : `Pubblica su ArcGIS (${publishMode})`;

  return (
    <>
      {disabledReasons.length > 0 && (
        <div className="imdf-pub-node__warning">
          Manca: {disabledReasons.join(', ')}
        </div>
      )}

      <div className="imdf-pub-node__actions">
        <button
          type="button"
          className="imdf-pub-node__validate-btn"
          onClick={onValidate}
          disabled={!canPublish}
          title={!canPublish ? publishTooltip : "Controlla cosa verra' pubblicato"}
        >
          🔍 Valida
        </button>
        <button
          type="button"
          className="imdf-pub-node__run-btn"
          onClick={onPublish}
          disabled={!canPublish}
          title={publishTooltip}
        >
          {publishing
            ? `⏳ ${publishProgress}%`
            : `▶ Esegui`}
        </button>
      </div>

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

      {publishError && (
        <div className="imdf-pub-node__result imdf-pub-node__result--error">
          <div className="imdf-pub-node__result-title">✗ Pubblicazione fallita</div>
          <div className="imdf-pub-node__result-err">{publishError}</div>
        </div>
      )}
    </>
  );
}

export default ImdfPublishActions;
