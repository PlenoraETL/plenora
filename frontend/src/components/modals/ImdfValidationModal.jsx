/**
 * ImdfValidationModal.jsx — Visualizza validation.json IMDF
 *
 * Riceve `validation` (oggetto JSON parsato dal backend, salvato in
 * lastTaskResult.imdf.validation dal task celery). Mostra summary + tabella
 * errori/warning per livello.
 *
 * Tema scuro cyan: usa lo shell condiviso `imdf-modal.css`
 * (FE-PHASE2-IMDF-MODAL-SHELL-001).
 * Nota Rules of Hooks: useState/useMemo devono stare PRIMA del return early
 * (React error #310 se spostati sotto).
 */
import React, { useState, useMemo } from 'react';
import { createPortal } from 'react-dom';

const VARS = {
  accent:  'var(--imdf-modal-accent)',
  accent2: 'var(--imdf-modal-accent2)',
  text:    'var(--imdf-modal-text)',
  muted:   'var(--imdf-modal-muted)',
  border:  'var(--imdf-modal-border)',
  surface: 'var(--imdf-modal-surface)',
  font:    'var(--imdf-modal-font)',
  danger:  'var(--imdf-modal-danger)',
  warn:    'var(--imdf-modal-warn)',
  success: 'var(--imdf-modal-success)',
};

export default function ImdfValidationModal({ isOpen, onClose, validation }) {
  const [filter, setFilter] = useState('all'); // 'all' | 'errors' | 'warnings'

  const v             = validation || {};
  const byLevel       = v.by_level || {};
  const totalErrors   = v.total_errors || 0;
  const totalWarnings = v.total_warnings || 0;
  const passed        = v.passed;

  const issues = useMemo(() => {
    const list = [];
    Object.entries(byLevel).forEach(([levelId, info]) => {
      (info.errors   || []).forEach(e => list.push({ levelId, severity: 'error',   ...e }));
      (info.warnings || []).forEach(w => list.push({ levelId, severity: 'warning', ...w }));
    });
    if (filter === 'errors')   return list.filter(i => i.severity === 'error');
    if (filter === 'warnings') return list.filter(i => i.severity === 'warning');
    return list;
  }, [byLevel, filter]);

  if (!isOpen) return null;

  const passBadge = passed === true
    ? { label: '✓ Passed', color: VARS.success }
    : passed === false
      ? { label: '✗ Failed', color: VARS.danger }
      : { label: '— unknown', color: VARS.muted };

  return createPortal((
    <div onClick={onClose} className="imdf-modal__overlay">
      <div
        onClick={(e) => e.stopPropagation()}
        className="imdf-modal__panel imdf-modal__panel--lg"
      >
        {/* Header */}
        <div className="imdf-modal__header">
          <div className="imdf-modal__header-info">
            <div>
              <h2 className="imdf-modal__title">⚠ Validazione IMDF</h2>
              <div className="imdf-modal__subtitle">
                Errori e warning per livello
              </div>
            </div>
            <div style={{
              fontSize: 10, fontWeight: 700,
              padding: '4px 10px',
              color: passBadge.color,
              background: passBadge.color + '14',
              border: `1px solid ${passBadge.color}55`,
              borderRadius: 4,
              textTransform: 'uppercase', letterSpacing: 0.5,
            }}>{passBadge.label}</div>
          </div>
          <button type="button" onClick={onClose} className="imdf-modal__close">✕</button>
        </div>

        {!validation ? (
          <div className="imdf-modal__empty">
            Nessuna validazione disponibile. Esegui il canvas per generare i dati IMDF.
          </div>
        ) : (
          <>
            {/* Summary + filtri */}
            <div className="imdf-modal__filters">
              <Badge color={VARS.danger}  count={totalErrors} label="errori"  />
              <Badge color={VARS.warn}    count={totalWarnings} label="warning" />
              <Badge color={VARS.accent2} count={Object.keys(byLevel).length} label="livelli" />
              <div className="imdf-modal__filters-spacer" />
              <FilterBtn active={filter === 'all'}      onClick={() => setFilter('all')}>Tutti</FilterBtn>
              <FilterBtn active={filter === 'errors'}   onClick={() => setFilter('errors')}>Solo errori</FilterBtn>
              <FilterBtn active={filter === 'warnings'} onClick={() => setFilter('warnings')}>Solo warning</FilterBtn>
            </div>

            {/* Tabella issues */}
            <div className="imdf-modal__body--flex">
              {issues.length === 0 ? (
                <div className="imdf-modal__empty imdf-modal__empty--lg">
                  Nessuna issue per i filtri selezionati.
                </div>
              ) : (
                <table className="imdf-modal__table">
                  <thead>
                    <tr className="imdf-modal__thead-row">
                      <th className="imdf-modal__th">Sev</th>
                      <th className="imdf-modal__th">Livello</th>
                      <th className="imdf-modal__th">Codice</th>
                      <th className="imdf-modal__th">Unit ID</th>
                      <th className="imdf-modal__th">Messaggio</th>
                    </tr>
                  </thead>
                  <tbody>
                    {issues.map((i, idx) => {
                      const isError = i.severity === 'error';
                      const severityColor = isError ? VARS.danger : VARS.warn;
                      return (
                        <tr key={idx} style={{ borderTop: `1px solid ${VARS.border}` }}>
                          <td className="imdf-modal__td imdf-modal__td--top">
                            <span style={{
                              display: 'inline-block',
                              width: 18, textAlign: 'center',
                              color: severityColor,
                              fontSize: 13,
                            }}>{isError ? '●' : '▲'}</span>
                          </td>
                          <td className="imdf-modal__td imdf-modal__td--top" style={{ color: VARS.accent2 }}>
                            <code className="imdf-modal__code">{i.levelId}</code>
                          </td>
                          <td className="imdf-modal__td imdf-modal__td--top">
                            <code className="imdf-modal__code">{i.code || '-'}</code>
                          </td>
                          <td className="imdf-modal__td imdf-modal__td--top">
                            <code className="imdf-modal__code" style={{ color: VARS.muted }}>
                              {i.unit_id || '-'}
                            </code>
                          </td>
                          <td className="imdf-modal__td imdf-modal__td--top" style={{ color: VARS.text }}>{i.message}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  ), document.body);
}

// ── Sub-components ──────────────────────────────────────────────────────
function Badge({ color, count, label }) {
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 6,
      padding: '5px 10px',
      background: color + '14',
      border: `1px solid ${color}55`,
      borderRadius: 4,
      fontSize: 10,
      fontWeight: 700,
      textTransform: 'uppercase',
      letterSpacing: 0.5,
      color: color,
    }}>
      <span style={{ fontSize: 13 }}>{count}</span>
      <span>{label}</span>
    </span>
  );
}

function FilterBtn({ active, onClick, children }) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        background: active ? 'rgba(124,58,237,0.12)' : VARS.surface,
        border: `1px solid ${active ? VARS.accent : VARS.border}`,
        color: active ? VARS.accent : VARS.muted,
        padding: '5px 11px',
        borderRadius: 4,
        cursor: 'pointer',
        fontSize: 9,
        fontWeight: 600,
        textTransform: 'uppercase',
        letterSpacing: 0.5,
        fontFamily: VARS.font,
      }}
    >{children}</button>
  );
}
