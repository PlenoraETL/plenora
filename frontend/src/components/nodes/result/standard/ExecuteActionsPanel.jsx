/**
 * ExecuteActionsPanel.jsx — Coda finale del Result node:
 * execution mode + bottoni Valida/Preview/Esegui + footer cronologia
 * + `RunHistoryModal`.
 *
 * Estratto da `ResultNodeStandardView.jsx` in `FE-SPLIT-005C`.
 * Componente presentational locale: niente fetch, niente API,
 * niente global state. Le callback `onValidate`/`onPreview`/`onExecute`
 * sono ricevute dal parent (che le inoltra a sua volta dal grandparent
 * `ResultNode`).
 *
 * State locale:
 *   - `executionMode` ('cache' | 'recalculate'): UI radio, scope-locale
 *     a questo panel. Solo MANIPOLA/ARRICCHISCI lo mostrano (gated
 *     `hasExecutionOptions`); su ESTRAI il radio non viene renderizzato
 *     ma il default 'cache' rimane usato per la firma `force_recalculate`
 *     (false su ESTRAI).
 *   - `isRunHistoryOpen`: open/close del `RunHistoryModal`.
 *
 * `RunHistoryModal` co-localizzato qui: il trigger (footer button) e
 * il modal vivono nello stesso panel — l'unico stato `isRunHistoryOpen`
 * non escapa dal panel.
 *
 * I CSS `result-node__*` restano in `ResultNode.css` (shared col parent).
 */
import React, { useState } from 'react';
import RunHistoryModal from '../../../modals/RunHistoryModal';

export default function ExecuteActionsPanel({
  data,
  id,
  nodeVariant,
  executeLabel,
  hasExecutionOptions,
  isArricchisci,
  hasOnPreview,
  hasOnExecute,
  onValidate,
  onPreview,
  onExecute,
}) {
  const [executionMode, setExecutionMode] = useState('cache');
  const [isRunHistoryOpen, setIsRunHistoryOpen] = useState(false);

  return (
    <>
      {/* Modalita' Esecuzione (Manipola/Arricchisci) */}
      {hasExecutionOptions && (
        <div className="result-node__execution-options">
          <div className="result-node__label">Esecuzione:</div>

          <label className={`result-node__execution-option ${
            executionMode === 'cache' ? 'result-node__execution-option--selected' : ''
          }`}>
            <input
              type="radio"
              name={`executionMode-${id}`}
              value="cache"
              checked={executionMode === 'cache'}
              onChange={(e) => setExecutionMode(e.target.value)}
              className="result-node__execution-radio"
            />
            <div className="result-node__execution-content">
              <div className="result-node__execution-title">
                ⚡ Usa cache esistente
              </div>
              <div className="result-node__execution-desc">
                Veloce - carica dati già elaborati (se disponibili)
              </div>
            </div>
          </label>

          <label className={`result-node__execution-option ${
            executionMode === 'recalculate' ? 'result-node__execution-option--selected' : ''
          }`}>
            <input
              type="radio"
              name={`executionMode-${id}`}
              value="recalculate"
              checked={executionMode === 'recalculate'}
              onChange={(e) => setExecutionMode(e.target.value)}
              className="result-node__execution-radio"
            />
            <div className="result-node__execution-content">
              <div className="result-node__execution-title">
                🔄 Ricalcola tutto
              </div>
              <div className="result-node__execution-desc">
                {isArricchisci
                  ? 'Aggiornato - riesegue tutte le chiamate API'
                  : 'Aggiornato - riesegue tutte le query SQL'}
              </div>
            </div>
          </label>
        </div>
      )}

      {/* Bottoni azione */}
      <div className="result-node__actions">
        <button type="button"
          onClick={onValidate}
          className="result-node__validate-btn"
          title="Valida il canvas prima di eseguire"
        >
          <span>🔍</span>
          <span>Valida</span>
        </button>

        {hasOnPreview && (
          <button type="button"
            onClick={onPreview}
            className={`result-node__preview-btn result-node__preview-btn--${nodeVariant}`}
          >
            <span className="result-node__preview-icon">👁️</span>
            <span>Preview</span>
          </button>
        )}

        {hasOnExecute && (
          <button type="button"
            onClick={() => onExecute({ force_recalculate: executionMode === 'recalculate' })}
            className={`result-node__execute-btn result-node__execute-btn--${nodeVariant}`}
          >
            <span className="result-node__execute-icon">▶️</span>
            <span>{executeLabel}</span>
          </button>
        )}
      </div>

      {/* Footer cronologia run */}
      <div className="result-node__footer">
        <button type="button"
          onClick={(e) => { e.stopPropagation(); setIsRunHistoryOpen(true); }}
          onMouseDown={(e) => e.stopPropagation()}
          className="result-node__history-btn"
          title="Cronologia esecuzioni"
        >
          <span>🕐</span>
          <span>Cronologia run</span>
        </button>
      </div>

      {/* Modal cronologia run */}
      <RunHistoryModal
        isOpen={isRunHistoryOpen}
        onClose={() => setIsRunHistoryOpen(false)}
        canvasId={data.project_id || data.canvas_id}
        nodeId={id}
        nodeLabel="Output"
      />
    </>
  );
}
