/**
 * ImdfValidationModal.jsx — Visualizza validation.json IMDF
 *
 * Riceve `validation` (oggetto JSON parsato dal backend, salvato in
 * lastTaskResult.imdf.validation dal task celery). Mostra summary + tabella
 * errori/warning per livello.
 *
 * Tema scuro cyan coerente con ImdfPreviewModal / ImdfManifestModal.
 * Nota Rules of Hooks: useState/useMemo devono stare PRIMA del return early
 * (React error #310 se spostati sotto).
 */
import React, { useState, useMemo } from 'react';
import { createPortal } from 'react-dom';

// Tokens → var(--plen-*) del design system Plenora. Seguono data-theme
// automaticamente. Il cyan resta costante come brand IMDF.
const TOKENS = {
  bg:       'var(--plen-surface, #0C0F18)',    // modale esterno
  surface:  'var(--plen-surface-2, #111525)',  // header + thead
  inset:    'var(--plen-bg, #07090F)',
  border:   'var(--plen-border, rgba(255,255,255,0.08))',
  accent:   '#7C3AED',                          // IMDF brand (violet Plenora)
  accent2:  'var(--plen-manipola, #7c3aed)',
  text:     'var(--plen-text, #DDE4F2)',
  muted:    'var(--plen-text-sub, #7D8BA8)',
  danger:   'var(--plen-err, #F87171)',
  warn:     'var(--plen-warn, #FBBF24)',
  success:  'var(--plen-ok, #34D399)',
  font:     "var(--plen-font-mono, 'JetBrains Mono'), ui-monospace, Menlo, monospace",
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
    ? { label: '✓ Passed', color: TOKENS.success }
    : passed === false
      ? { label: '✗ Failed', color: TOKENS.danger }
      : { label: '— unknown', color: TOKENS.muted };

  return createPortal((
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0,
        background: 'rgba(0,0,0,0.72)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        zIndex: 'var(--z-modal-priority, 10000)',
        fontFamily: TOKENS.font,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: TOKENS.bg,
          color: TOKENS.text,
          border: `1px solid ${TOKENS.border}`,
          borderRadius: 10,
          width: 'min(900px, 96vw)',
          height: 'min(720px, 90vh)',
          boxShadow: '0 24px 80px rgba(0,0,0,0.7)',
          display: 'flex', flexDirection: 'column',
          overflow: 'hidden',
        }}
      >
        {/* Header */}
        <div style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          padding: '16px 20px',
          background: TOKENS.surface,
          borderBottom: `1px solid ${TOKENS.border}`,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <div>
              <h2 style={{
                margin: 0, fontSize: 15, fontWeight: 800,
                color: TOKENS.accent, letterSpacing: 0.3,
              }}>
                ⚠ Validazione IMDF
              </h2>
              <div style={{
                fontSize: 9, color: TOKENS.muted, textTransform: 'uppercase',
                letterSpacing: 0.5, marginTop: 2,
              }}>
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
          <button type="button" onClick={onClose} style={{
            background: 'none', border: 'none', color: TOKENS.muted,
            fontSize: 18, cursor: 'pointer', padding: 4,
          }}>✕</button>
        </div>

        {!validation ? (
          <div style={{ padding: '30px 20px', color: TOKENS.muted, fontSize: 12, textAlign: 'center' }}>
            Nessuna validazione disponibile. Esegui il canvas per generare i dati IMDF.
          </div>
        ) : (
          <>
            {/* Summary + filtri */}
            <div style={{
              display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap',
              padding: '12px 20px',
              borderBottom: `1px solid ${TOKENS.border}`,
            }}>
              <Badge color={TOKENS.danger} count={totalErrors} label="errori"  />
              <Badge color={TOKENS.warn}   count={totalWarnings} label="warning" />
              <Badge color={TOKENS.accent2} count={Object.keys(byLevel).length} label="livelli" />
              <div style={{ flex: 1 }} />
              <FilterBtn active={filter === 'all'}      onClick={() => setFilter('all')}>Tutti</FilterBtn>
              <FilterBtn active={filter === 'errors'}   onClick={() => setFilter('errors')}>Solo errori</FilterBtn>
              <FilterBtn active={filter === 'warnings'} onClick={() => setFilter('warnings')}>Solo warning</FilterBtn>
            </div>

            {/* Tabella issues */}
            <div style={{ flex: 1, overflow: 'auto' }}>
              {issues.length === 0 ? (
                <div style={{
                  padding: '40px 20px', color: TOKENS.muted, fontSize: 12, textAlign: 'center',
                }}>
                  Nessuna issue per i filtri selezionati.
                </div>
              ) : (
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
                  <thead>
                    <tr style={{
                      background: TOKENS.surface,
                      position: 'sticky', top: 0, zIndex: 1,
                    }}>
                      <th style={thStyle}>Sev</th>
                      <th style={thStyle}>Livello</th>
                      <th style={thStyle}>Codice</th>
                      <th style={thStyle}>Unit ID</th>
                      <th style={thStyle}>Messaggio</th>
                    </tr>
                  </thead>
                  <tbody>
                    {issues.map((i, idx) => {
                      const isError = i.severity === 'error';
                      const severityColor = isError ? TOKENS.danger : TOKENS.warn;
                      return (
                        <tr key={idx} style={{ borderTop: `1px solid ${TOKENS.border}` }}>
                          <td style={tdStyle}>
                            <span style={{
                              display: 'inline-block',
                              width: 18, textAlign: 'center',
                              color: severityColor,
                              fontSize: 13,
                            }}>{isError ? '●' : '▲'}</span>
                          </td>
                          <td style={{ ...tdStyle, color: TOKENS.accent2 }}>
                            <code style={codeStyle}>{i.levelId}</code>
                          </td>
                          <td style={tdStyle}>
                            <code style={codeStyle}>{i.code || '-'}</code>
                          </td>
                          <td style={tdStyle}>
                            <code style={{ ...codeStyle, color: TOKENS.muted }}>
                              {i.unit_id || '-'}
                            </code>
                          </td>
                          <td style={{ ...tdStyle, color: TOKENS.text }}>{i.message}</td>
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

// ── Styling helpers ─────────────────────────────────────────────────────
const thStyle = {
  padding: '10px 12px',
  fontWeight: 700, fontSize: 9, color: TOKENS.muted,
  textTransform: 'uppercase', letterSpacing: 1,
  textAlign: 'left',
  borderBottom: `1px solid ${TOKENS.border}`,
};

const tdStyle = {
  padding: '8px 12px',
  verticalAlign: 'top',
};

const codeStyle = {
  fontFamily: TOKENS.font,
  fontSize: 10,
  background: TOKENS.surface,
  padding: '2px 6px',
  borderRadius: 3,
  border: `1px solid ${TOKENS.border}`,
};

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
        background: active ? 'rgba(124,58,237,0.12)' : TOKENS.surface,
        border: `1px solid ${active ? TOKENS.accent : TOKENS.border}`,
        color: active ? TOKENS.accent : TOKENS.muted,
        padding: '5px 11px',
        borderRadius: 4,
        cursor: 'pointer',
        fontSize: 9,
        fontWeight: 600,
        textTransform: 'uppercase',
        letterSpacing: 0.5,
        fontFamily: TOKENS.font,
      }}
    >{children}</button>
  );
}
