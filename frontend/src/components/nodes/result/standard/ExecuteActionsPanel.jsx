/**
 * ExecuteActionsPanel.jsx — Coda finale del Result node:
 * execution mode + bottoni Valida/Anteprima/Esegui + footer cronologia
 * + `RunHistoryModal`. Le etichette sono le stesse della barra dell'editor
 * (Blocco A, e.5): stesse azioni, stessi nomi.
 * Le callback `onValidate`/`onPreview`/`onExecute` arrivano dal
 * `ResultNode` (unico percorso di esecuzione).
 *
 * Stato:
 *   - "Usa cache" / "Ricalcola tutto": nello STORE (`runOptions`), cosi'
 *     vale anche per "▶ Esegui" della barra (Fase 4, M7). Solo
 *     MANIPOLA/ARRICCHISCI mostrano la scelta (`hasExecutionOptions`); su
 *     ESTRAI resta false.
 *   - `runState`: fuori da 'idle' (avvio o esecuzione) "Esegui" e' spento,
 *     come in barra.
 *   - `isRunHistoryOpen`: open/close del `RunHistoryModal`.
 *
 * `RunHistoryModal` co-localizzato qui: il trigger (footer button) e
 * il modal vivono nello stesso panel — l'unico stato `isRunHistoryOpen`
 * non escapa dal panel.
 *
 * I CSS `result-node__*` restano in `ResultNode.css` (shared col parent).
 */
import React, { useState } from 'react';
import { useBusy } from '../../../../hooks/useBusy';
import RunHistoryModal from '../../../modals/RunHistoryModal';
import useCanvasStore from '../../../../store/useCanvasStore';
import { term } from '../../../../utils/glossary';

export default function ExecuteActionsPanel({
  data,
  id,
  nodeVariant,
  hasExecutionOptions,
  isArricchisci,
  hasOnPreview,
  hasOnExecute,
  onValidate,
  onPreview,
  onExecute,
}) {
  const executionMode = useCanvasStore(s => (s.runOptions?.force_recalculate ? 'recalculate' : 'cache'));
  const setRunOptions = useCanvasStore(s => s.setRunOptions);
  const setExecutionMode = (mode) => setRunOptions({ force_recalculate: mode === 'recalculate' });
  const busy = useCanvasStore(s => s.runState === 'starting' || s.runState === 'running');
  const [isRunHistoryOpen, setIsRunHistoryOpen] = useState(false);
  // Fase 5 T2 (R9.7): Valida e Anteprima non partono due volte (doppio clic
  // = un solo "Salva e continua", una sola richiesta).
  const reads = useBusy();
  const validating = reads.isBusy('validate');
  const previewing = reads.isBusy('preview');

  return (
    <>
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

      <div className="result-node__actions">
        <button type="button"
          onClick={() => reads.run('validate', () => onValidate())}
          disabled={validating}
          aria-busy={validating}
          className="result-node__validate-btn"
          title={term('validate').description}
        >
          <span aria-hidden="true">{term('validate').icon}</span>
          <span>{term('validate').name}</span>
        </button>

        {hasOnPreview && (
          <button type="button"
            onClick={() => reads.run('preview', () => onPreview())}
            disabled={previewing}
            aria-busy={previewing}
            className={`result-node__preview-btn result-node__preview-btn--${nodeVariant}`}
          >
            <span className="result-node__preview-icon" aria-hidden="true">{term('preview').icon}</span>
            <span>{term('preview').name}</span>
          </button>
        )}

        {hasOnExecute && (
          <button type="button"
            onClick={() => onExecute()}
            disabled={busy}
            className={`result-node__execute-btn result-node__execute-btn--${nodeVariant}`}
          >
            <span className="result-node__execute-icon" aria-hidden="true">{busy ? '⏳' : term('run').icon}</span>
            <span>{busy ? term('run').busyName : term('run').name}</span>
          </button>
        )}
      </div>

      <div className="result-node__footer">
        <button type="button"
          onClick={(e) => { e.stopPropagation(); setIsRunHistoryOpen(true); }}
          onMouseDown={(e) => e.stopPropagation()}
          className="result-node__history-btn"
          title={term('run_history').description}
        >
          <span aria-hidden="true">{term('run_history').icon}</span>
          <span>{term('run_history').name}</span>
        </button>
      </div>

      <RunHistoryModal
        isOpen={isRunHistoryOpen}
        onClose={() => setIsRunHistoryOpen(false)}
        canvasId={data.project_id || data.canvas_id}
        nodeId={id}
        nodeLabel={term('output').name}
      />
    </>
  );
}
