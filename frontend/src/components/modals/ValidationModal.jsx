/**
 * ValidationModal.jsx — Modal per i risultati `CanvasValidationResult`.
 *
 * Consuma direttamente `CanvasValidationResult` (vedi
 * `utils/canvasValidationContract.js`) tramite la prop `result`. Renderizza
 * sezioni separate per `bySeverity.error` / `.warning` / `.info`.
 *
 * NOTE:
 *   - se `result` non e' passato, accetta ancora `issues` + `isValid`
 *     per compatibilita'; il modal costruisce internamente le tre sezioni
 *     con un filter inline equivalente.
 *   - i call site nuovi devono preferire `result`.
 */
import React from 'react';
import ModalWrapper, { ModalButton } from '../common/ModalWrapper';
import './ValidationModal.css';

const SEVERITY_ICON = {
  error: '❌',
  warning: '⚠️',
  info: 'ℹ️',
};

/**
 * @param {Object} props
 * @param {boolean} props.isOpen
 * @param {Function} props.onClose
 * @param {import('../../utils/canvasValidationContract').CanvasValidationResult} [props.result]
 *   Nuovo contratto. Se presente, ha precedenza su `issues`/`isValid`.
 * @param {(nodeId: string) => void} [props.onFocusNode] — Click-to-focus: se presente,
 *   le issue row con `nodeId` diventano cliccabili e invocano questa callback.
 * @param {Array} [props.issues] — KEEP_COMPAT: ignorato se `result` e' definito.
 * @param {boolean} [props.isValid] — KEEP_COMPAT: ignorato se `result` e' definito.
 */
export default function ValidationModal({ isOpen, onClose, result, onFocusNode, issues, isValid }) {
  if (!isOpen) return null;

  // Resolve shape: nuovo `result` ha priorita'; legacy fallback altrimenti.
  const ok = result ? result.ok : !!isValid;
  const errors   = result ? result.bySeverity.error   : (issues || []).filter(i => i.severity === 'error');
  const warnings = result ? result.bySeverity.warning : (issues || []).filter(i => i.severity === 'warning');
  const infos    = result ? result.bySeverity.info    : (issues || []).filter(i => i.severity === 'info');
  const totalIssues = errors.length + warnings.length + infos.length;

  return (
    <ModalWrapper
      isOpen={true}
      onClose={onClose}
      title={ok ? 'Canvas valido' : 'Problemi rilevati nel canvas'}
      icon={ok ? '✅' : '🔍'}
      size="md"
      footer={<ModalButton variant="primary" onClick={onClose}>Chiudi</ModalButton>}
    >
        <div className="validation-modal__body">
          {ok && totalIssues === 0 && (
            <div className="validation-modal__ok">
              <span className="validation-modal__ok-icon">✅</span>
              <p>Nessun problema trovato. Il progetto è pronto per l&apos;esecuzione.</p>
            </div>
          )}

          {errors.length > 0 && (
            <div className="validation-modal__section">
              <div className="validation-modal__section-title validation-modal__section-title--error">
                ❌ Errori ({errors.length}) — bloccano l'esecuzione
              </div>
              {errors.map((issue, i) => (
                <IssueRow key={issue.id ? `${issue.id}-${i}` : i} issue={issue} onFocusNode={onFocusNode} />
              ))}
            </div>
          )}

          {warnings.length > 0 && (
            <div className="validation-modal__section">
              <div className="validation-modal__section-title validation-modal__section-title--warning">
                ⚠️ Avvisi ({warnings.length})
              </div>
              {warnings.map((issue, i) => (
                <IssueRow key={issue.id ? `${issue.id}-${i}` : i} issue={issue} onFocusNode={onFocusNode} />
              ))}
            </div>
          )}

          {infos.length > 0 && (
            <div className="validation-modal__section">
              <div className="validation-modal__section-title validation-modal__section-title--info">
                ℹ️ Note ({infos.length})
              </div>
              {infos.map((issue, i) => (
                <IssueRow key={issue.id ? `${issue.id}-${i}` : i} issue={issue} onFocusNode={onFocusNode} />
              ))}
            </div>
          )}
        </div>
    </ModalWrapper>
  );
}

function IssueRow({ issue, onFocusNode }) {
  const clickable = !!(issue.nodeId && onFocusNode);
  return (
    <div
      className={
        `validation-modal__issue validation-modal__issue--${issue.severity}` +
        (clickable ? ' validation-modal__issue--clickable' : '')
      }
      onClick={clickable ? () => onFocusNode(issue.nodeId) : undefined}
      role={clickable ? 'button' : undefined}
      tabIndex={clickable ? 0 : undefined}
      onKeyDown={clickable ? (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onFocusNode(issue.nodeId); } } : undefined}
    >
      <span className="validation-modal__issue-icon">{SEVERITY_ICON[issue.severity]}</span>
      <div className="validation-modal__issue-content">
        <div className="validation-modal__issue-message">{issue.message}</div>
        {issue.nodeName && (
          <div className="validation-modal__issue-node">Nodo: {issue.nodeName}</div>
        )}
      </div>
    </div>
  );
}
