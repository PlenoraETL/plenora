import React, { useRef } from 'react';
import { createPortal } from 'react-dom';
import useDialogFocus from '../../../hooks/useDialogFocus';

function ConflictModal({
  isOpen,
  onClose,
  report,
  error,
  policy,
  schemaName,
  tableName,
}) {
  const dialogRef = useRef(null);
  useDialogFocus(isOpen, onClose, dialogRef);

  if (!isOpen) return null;

  return createPortal(
    <div className="carica-conflict-modal__overlay" onClick={onClose}>
      <div
        ref={dialogRef}
        className="carica-conflict-modal"
        onClick={e => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="ccm-title"
        tabIndex={-1}
      >
        <div className="carica-conflict-modal__header">
          <span id="ccm-title">🔍 Rapporto dei conflitti — {schemaName}.{tableName}</span>
          <button className="carica-conflict-modal__close" onClick={onClose} aria-label="Chiudi">✕</button>
        </div>
        <div className="carica-conflict-modal__body">
          {error ? (
            <div className="carica-conflict-modal__error">❌ {error}</div>
          ) : report ? (
            <>
              <div className={`carica-conflict-modal__summary ${report.has_conflicts ? 'has-conflicts' : 'no-conflicts'}`}>
                {report.has_conflicts ? (
                  <>
                    <div>⚠️ <strong>{report.total_conflicts}</strong> conflitti trovati</div>
                    <div>• Duplicati interni: <strong>{report.internal_duplicates}</strong></div>
                    <div>• Conflitti con DB: <strong>{report.db_conflicts}</strong></div>
                  </>
                ) : (
                  <div>✅ Nessun conflitto trovato — scrittura sicura</div>
                )}
              </div>
              {report.details && report.details.length > 0 && (
                <div className="carica-conflict-modal__details">
                  <div className="carica-conflict-modal__details-title">Dettaglio conflitti (max 100):</div>
                  <table className="carica-conflict-modal__table">
                    <thead>
                      <tr><th>Riga</th><th>Valori di corrispondenza</th><th>Tipo</th></tr>
                    </thead>
                    <tbody>
                      {report.details.slice(0, 20).map((d, i) => (
                        <tr key={i}>
                          <td>{d.row_index}</td>
                          <td>{Object.entries(d.match_values || {}).map(([k, v]) => `${k}=${v}`).join(', ')}</td>
                          <td>{d.conflict_type}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {report.details.length > 20 && (
                    <div className="carica-conflict-modal__more">...e altri {report.details.length - 20} conflitti</div>
                  )}
                </div>
              )}
              <div className="carica-conflict-modal__hint">
                Regola sui conflitti attiva: <strong>{policy}</strong>
              </div>
            </>
          ) : null}
        </div>
        <div className="carica-conflict-modal__footer">
          <button className="carica-node__btn carica-node__btn--secondary" onClick={onClose}>Chiudi</button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

export default ConflictModal;
