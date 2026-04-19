/**
 * ValidationModal.jsx — Modal per mostrare risultati validazione canvas MANIPOLA
 */
import React from 'react';
import ModalWrapper, { ModalButton } from '../common/ModalWrapper';
import './ValidationModal.css';

const SEVERITY_ICON = {
  error: '❌',
  warning: '⚠️',
  info: 'ℹ️',
};

const SEVERITY_LABEL = {
  error: 'Errore',
  warning: 'Avviso',
  info: 'Info',
};

/**
 * @param {Object} props
 * @param {boolean} props.isOpen
 * @param {Function} props.onClose
 * @param {Array} props.issues - Array di { severity, message, nodeId?, nodeName? }
 * @param {boolean} props.isValid
 */
export default function ValidationModal({ isOpen, onClose, issues = [], isValid }) {
  if (!isOpen) return null;

  const errors   = issues.filter(i => i.severity === 'error');
  const warnings = issues.filter(i => i.severity === 'warning');
  const infos    = issues.filter(i => i.severity === 'info');

  return (
    <ModalWrapper
      isOpen={true}
      onClose={onClose}
      title={isValid ? 'Canvas valido' : 'Problemi rilevati nel canvas'}
      icon={isValid ? '✅' : '🔍'}
      size="md"
      footer={<ModalButton variant="primary" onClick={onClose}>Chiudi</ModalButton>}
    >
        <div className="validation-modal__body">
          {isValid && issues.length === 0 && (
            <div className="validation-modal__ok">
              <span className="validation-modal__ok-icon">✅</span>
              <p>Nessun problema trovato. La pipeline è pronta per l'esecuzione.</p>
            </div>
          )}

          {errors.length > 0 && (
            <div className="validation-modal__section">
              <div className="validation-modal__section-title validation-modal__section-title--error">
                ❌ Errori ({errors.length}) — bloccano l'esecuzione
              </div>
              {errors.map((issue, i) => (
                <IssueRow key={i} issue={issue} />
              ))}
            </div>
          )}

          {warnings.length > 0 && (
            <div className="validation-modal__section">
              <div className="validation-modal__section-title validation-modal__section-title--warning">
                ⚠️ Avvisi ({warnings.length})
              </div>
              {warnings.map((issue, i) => (
                <IssueRow key={i} issue={issue} />
              ))}
            </div>
          )}

          {infos.length > 0 && (
            <div className="validation-modal__section">
              <div className="validation-modal__section-title validation-modal__section-title--info">
                ℹ️ Note ({infos.length})
              </div>
              {infos.map((issue, i) => (
                <IssueRow key={i} issue={issue} />
              ))}
            </div>
          )}
        </div>
    </ModalWrapper>
  );
}

function IssueRow({ issue }) {
  return (
    <div className={`validation-modal__issue validation-modal__issue--${issue.severity}`}>
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
