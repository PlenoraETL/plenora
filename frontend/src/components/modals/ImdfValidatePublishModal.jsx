/**
 * ImdfValidatePublishModal.jsx — Report di pre-pubblicazione IMDF
 *
 * Mostra l'esito di POST /api/imdf/validate-publish:
 *  - Header pass/fail con contatori errori/warning
 *  - Tabella layer con action/delta/schema/issues
 *  - Lista data issues per layer (espandibile)
 *  - Pulsante "Procedi con pubblicazione" (disabilitato se ci sono errors)
 *
 * Tema scuro cyan coerente con Preview/Manifest/Validation modali.
 * Legge `var(--plen-*)` → segue data-theme.
 */
import React, { useState } from 'react';
import { createPortal } from 'react-dom';

const TOKENS = {
  bg:       'var(--plen-surface, #0C0F18)',
  surface:  'var(--plen-surface-2, #111525)',
  inset:    'var(--plen-bg, #07090F)',
  border:   'var(--plen-border, rgba(255,255,255,0.08))',
  accent:   '#7C3AED',
  accent2:  'var(--plen-manipola, #7c3aed)',
  text:     'var(--plen-text, #DDE4F2)',
  muted:    'var(--plen-text-sub, #7D8BA8)',
  danger:   'var(--plen-err, #F87171)',
  warn:     'var(--plen-warn, #FBBF24)',
  success:  'var(--plen-ok, #34D399)',
  font:     "var(--plen-font-mono, 'JetBrains Mono'), ui-monospace, Menlo, monospace",
};

const ACTION_LABEL = {
  create:       '🆕 Create',
  'create-layer': '🆕 Create layer',
  replace:      '↻ Replace',
  append:       '+ Append',
  upsert:       '⇵ Upsert',
  update:       '✎ Update',
  error:        '✗ Error',
};
const ACTION_COLOR = {
  create:       TOKENS.accent,
  'create-layer': TOKENS.accent,
  replace:      TOKENS.warn,
  append:       TOKENS.success,
  upsert:       TOKENS.accent2,
  update:       '#4B8BFF',  // blue — distinto da accent2 purple di upsert
  error:        TOKENS.danger,
};

export default function ImdfValidatePublishModal({
  isOpen,
  onClose,
  report,         // il dict ritornato da validateImdfPublish
  loading,        // true mentre il POST è in corso
  error,          // string se il POST ha fallito
  onProceed,      // callback chiamato quando l'utente preme "Procedi"
  mode,           // mode selezionato (per l'header)
  fsName,         // nome FS target (per l'header)
}) {
  const [expandedLayer, setExpandedLayer] = useState(null);

  if (!isOpen) return null;

  const ok = report?.ok ?? false;
  const hasErrors = (report?.summary?.errors ?? 0) > 0;

  const overlay = { position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.72)',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    zIndex: 'var(--z-modal-priority, 10000)', fontFamily: TOKENS.font };
  const box = { background: TOKENS.bg, color: TOKENS.text,
    border: `1px solid ${TOKENS.border}`, borderRadius: 10,
    width: 'min(980px, 96vw)', height: 'min(780px, 92vh)',
    boxShadow: '0 24px 80px rgba(0,0,0,0.7)',
    display: 'flex', flexDirection: 'column', overflow: 'hidden' };

  return createPortal((
    <div onClick={onClose} style={overlay}>
      <div onClick={(e) => e.stopPropagation()} style={box}>

        {/* Header */}
        <div style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          padding: '16px 22px', background: TOKENS.surface,
          borderBottom: `1px solid ${TOKENS.border}`,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
            <div>
              <h2 style={{ margin: 0, fontSize: 15, fontWeight: 800,
                           color: TOKENS.accent, letterSpacing: 0.3 }}>
                🔍 Validazione pubblicazione IMDF
              </h2>
              <div style={{ fontSize: 9, color: TOKENS.muted, textTransform: 'uppercase',
                            letterSpacing: 0.5, marginTop: 2 }}>
                Mode · {mode || '—'} {fsName ? `· ${fsName}` : ''}
              </div>
            </div>
            {!loading && report && (
              <div style={{
                fontSize: 10, fontWeight: 700,
                padding: '4px 10px', borderRadius: 4,
                textTransform: 'uppercase', letterSpacing: 0.5,
                color: ok ? TOKENS.success : TOKENS.danger,
                background: (ok ? TOKENS.success : TOKENS.danger) + '14',
                border: `1px solid ${(ok ? TOKENS.success : TOKENS.danger)}55`,
              }}>
                {ok ? '✓ OK — Pronto' : '✗ Errori da risolvere'}
              </div>
            )}
          </div>
          <button type="button" onClick={onClose} style={{
            background: 'none', border: 'none', color: TOKENS.muted,
            fontSize: 18, cursor: 'pointer', padding: 4,
          }}>✕</button>
        </div>

        {/* Body */}
        <div style={{ flex: 1, overflow: 'auto', padding: 20 }}>

          {loading && (
            <div style={{
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              height: '100%', color: TOKENS.muted, fontSize: 12,
              flexDirection: 'column', gap: 10,
            }}>
              <div style={{
                width: 28, height: 28, border: `2px solid ${TOKENS.border}`,
                borderTopColor: TOKENS.accent, borderRadius: '50%',
                animation: 'spin 0.8s linear infinite',
              }} />
              <div>Analisi in corso...</div>
            </div>
          )}

          {error && !loading && (
            <div style={{
              color: TOKENS.danger, fontSize: 12,
              background: 'rgba(248,113,113,0.08)',
              border: `1px solid ${TOKENS.danger}40`,
              borderRadius: 6, padding: '12px 14px',
            }}>
              <div style={{ fontWeight: 700, marginBottom: 4 }}>✗ Errore validazione</div>
              <div style={{ fontFamily: TOKENS.font }}>{error}</div>
            </div>
          )}

          {!loading && !error && report && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>

              {/* Summary row */}
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                <Badge color={TOKENS.danger}  count={report.summary?.errors ?? 0}   label="errori" />
                <Badge color={TOKENS.warn}    count={report.summary?.warnings ?? 0} label="warning" />
                <Badge color={TOKENS.accent}  count={report.summary?.layers_count ?? 0} label="layer" />
                <Badge color={TOKENS.accent2} count={report.summary?.total_features ?? 0} label="feature" />
                <div style={{ flex: 1 }} />
                <Badge
                  color={report.fs_exists ? TOKENS.success : TOKENS.muted}
                  label={report.fs_exists ? 'FS esistente' : 'Nuovo FS'}
                />
              </div>

              {/* Top issues globali */}
              {Array.isArray(report.top_issues) && report.top_issues.length > 0 && (
                <div>
                  <SectionTitle>Problemi generali</SectionTitle>
                  {report.top_issues.map((i, idx) => (
                    <IssueRow key={idx} issue={i} />
                  ))}
                </div>
              )}

              {/* Tabella layer */}
              {Array.isArray(report.layers) && report.layers.length > 0 && (
                <div>
                  <SectionTitle>Layer coinvolti</SectionTitle>
                  <div style={{
                    background: TOKENS.surface,
                    border: `1px solid ${TOKENS.border}`,
                    borderRadius: 6, overflow: 'hidden',
                  }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
                      <thead>
                        <tr style={{ background: TOKENS.inset }}>
                          <th style={thStyle}>Layer</th>
                          <th style={thStyle}>Azione</th>
                          <th style={thStyle}>Bundle</th>
                          <th style={thStyle}>FS</th>
                          <th style={thStyle}>Delta</th>
                          <th style={thStyle}>Schema</th>
                          <th style={thStyle}>Issues</th>
                        </tr>
                      </thead>
                      <tbody>
                        {report.layers.map((l, i) => {
                          const action = l.action || '—';
                          const actLabel = ACTION_LABEL[action] || action;
                          const totalIssues = (l.schema_issues?.length || 0) + (l.data_issues?.length || 0);
                          // Il colore del badge riflette lo stato reale della
                          // riga: error > warning > colore di default della mode.
                          // Cosi' una 'append' con duplicati non appare verde.
                          const layerHasErr = hasIssueError(l);
                          const actColor = layerHasErr
                            ? TOKENS.danger
                            : (totalIssues > 0 ? TOKENS.warn : (ACTION_COLOR[action] || TOKENS.muted));
                          const isExpanded = expandedLayer === i;
                          return (
                            <React.Fragment key={l.name}>
                              <tr style={{
                                borderTop: `1px solid ${TOKENS.border}`,
                                cursor: totalIssues > 0 ? 'pointer' : 'default',
                              }}
                              onClick={() => totalIssues > 0 && setExpandedLayer(isExpanded ? null : i)}>
                                <td style={{ ...tdStyle, color: TOKENS.accent, fontWeight: 700 }}>
                                  {totalIssues > 0 && (
                                    <span style={{ color: TOKENS.muted, marginRight: 4 }}>
                                      {isExpanded ? '▼' : '▶'}
                                    </span>
                                  )}
                                  {l.name}
                                </td>
                                <td style={tdStyle}>
                                  <span style={{
                                    fontSize: 10, padding: '2px 7px', borderRadius: 3,
                                    color: actColor, background: actColor + '14',
                                    border: `1px solid ${actColor}44`,
                                    textTransform: 'uppercase', letterSpacing: 0.3,
                                    fontWeight: 700,
                                  }}>{actLabel}</span>
                                </td>
                                <td style={{ ...tdStyle, color: TOKENS.text }}>{l.bundle_count ?? 0}</td>
                                <td style={{ ...tdStyle, color: TOKENS.muted }}>
                                  {l.fs_count != null ? l.fs_count : '—'}
                                </td>
                                <td style={{ ...tdStyle, fontFamily: TOKENS.font, fontSize: 10 }}>
                                  {l.delta || '—'}
                                </td>
                                <td style={tdStyle}>
                                  <SchemaDot status={l.schema_compat} />
                                </td>
                                <td style={tdStyle}>
                                  {totalIssues > 0 ? (
                                    <span style={{
                                      fontSize: 9, fontWeight: 700,
                                      padding: '2px 6px', borderRadius: 3,
                                      color: hasIssueError(l) ? TOKENS.danger : TOKENS.warn,
                                      background: (hasIssueError(l) ? TOKENS.danger : TOKENS.warn) + '14',
                                      border: `1px solid ${(hasIssueError(l) ? TOKENS.danger : TOKENS.warn)}55`,
                                    }}>{totalIssues}</span>
                                  ) : (
                                    <span style={{ color: TOKENS.success, fontSize: 11 }}>✓</span>
                                  )}
                                </td>
                              </tr>
                              {isExpanded && (
                                <tr>
                                  <td colSpan={7} style={{
                                    padding: '10px 14px',
                                    background: TOKENS.inset,
                                    borderTop: `1px solid ${TOKENS.border}`,
                                  }}>
                                    {(l.schema_issues || []).length > 0 && (
                                      <div style={{ marginBottom: 8 }}>
                                        <div style={{
                                          fontSize: 9, color: TOKENS.muted,
                                          textTransform: 'uppercase', letterSpacing: 1,
                                          marginBottom: 5,
                                        }}>Schema issues</div>
                                        {l.schema_issues.map((it, k) => (
                                          <IssueRow key={k} issue={it} compact />
                                        ))}
                                      </div>
                                    )}
                                    {(l.data_issues || []).length > 0 && (
                                      <div>
                                        <div style={{
                                          fontSize: 9, color: TOKENS.muted,
                                          textTransform: 'uppercase', letterSpacing: 1,
                                          marginBottom: 5,
                                        }}>Data issues</div>
                                        {l.data_issues.map((it, k) => (
                                          <IssueRow key={k} issue={it} compact />
                                        ))}
                                      </div>
                                    )}
                                    {l.match_column && (
                                      <div style={{
                                        fontSize: 9, color: TOKENS.muted, marginTop: 8,
                                      }}>
                                        match column: <code style={codeStyle}>{l.match_column}</code>
                                        {l.will_delete != null && ` · will_delete: ${l.will_delete}`}
                                        {l.will_add != null && ` · will_add: ${l.will_add}`}
                                      </div>
                                    )}
                                  </td>
                                </tr>
                              )}
                            </React.Fragment>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                  <div style={{ fontSize: 9, color: TOKENS.muted, marginTop: 6 }}>
                    Click su una riga con issues per espanderla
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div style={{
          display: 'flex', gap: 10, padding: '14px 20px',
          borderTop: `1px solid ${TOKENS.border}`, background: TOKENS.surface,
          justifyContent: 'flex-end',
        }}>
          <button type="button" onClick={onClose} style={btnSecondary}>
            Annulla
          </button>
          <button
            type="button"
            onClick={onProceed}
            disabled={loading || hasErrors || !report || !!error}
            style={hasErrors || loading || !report || error ? btnDisabled : btnPrimary}
            title={hasErrors ? 'Risolvi gli errori per procedere' : ''}
          >
            {hasErrors ? '✗ Errori presenti' : '▶ Procedi con pubblicazione'}
          </button>
        </div>
      </div>
    </div>
  ), document.body);
}

// ── Sub-components ─────────────────────────────────────────────────────

function hasIssueError(layer) {
  const all = [...(layer.schema_issues || []), ...(layer.data_issues || [])];
  return all.some(i => i.severity === 'error');
}

function SectionTitle({ children }) {
  return (
    <div style={{
      fontSize: 9, color: TOKENS.muted,
      textTransform: 'uppercase', letterSpacing: 1, marginBottom: 8,
      paddingBottom: 4, borderBottom: `1px solid ${TOKENS.border}`,
    }}>{children}</div>
  );
}

function Badge({ color, count, label }) {
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 6,
      padding: '5px 10px',
      background: color + '14',
      border: `1px solid ${color}55`,
      borderRadius: 4,
      fontSize: 10, fontWeight: 700,
      textTransform: 'uppercase', letterSpacing: 0.5,
      color,
      fontFamily: TOKENS.font,
    }}>
      {count != null && <span style={{ fontSize: 13 }}>{count}</span>}
      <span>{label}</span>
    </span>
  );
}

function SchemaDot({ status }) {
  const color =
    status === 'ok'       ? TOKENS.success :
    status === 'warning'  ? TOKENS.warn :
    status === 'mismatch' ? TOKENS.danger : TOKENS.muted;
  const label =
    status === 'ok'       ? '✓' :
    status === 'warning'  ? '▲' :
    status === 'mismatch' ? '✗' : '—';
  return (
    <span style={{ color, fontSize: 12, fontWeight: 700 }}>{label}</span>
  );
}

function IssueRow({ issue, compact }) {
  const sev = issue.severity || 'info';
  const color =
    sev === 'error'   ? TOKENS.danger :
    sev === 'warning' ? TOKENS.warn : TOKENS.muted;
  return (
    <div style={{
      display: 'flex', gap: 8, alignItems: 'flex-start',
      padding: compact ? '3px 0' : '6px 10px',
      marginBottom: compact ? 0 : 6,
      fontSize: compact ? 10 : 11,
      background: compact ? 'transparent' : color + '10',
      border: compact ? 'none' : `1px solid ${color}35`,
      borderRadius: compact ? 0 : 5,
    }}>
      <span style={{ color, fontWeight: 700, minWidth: 18 }}>
        {sev === 'error' ? '●' : sev === 'warning' ? '▲' : '·'}
      </span>
      <code style={codeStyle}>{issue.code}</code>
      <span style={{ flex: 1, color: TOKENS.text }}>{issue.message}</span>
    </div>
  );
}

// ── Styles ────────────────────────────────────────────────────────────
const thStyle = {
  padding: '10px 12px', fontSize: 9, color: TOKENS.muted,
  fontWeight: 700, textTransform: 'uppercase', letterSpacing: 1,
  textAlign: 'left',
  borderBottom: `1px solid ${TOKENS.border}`,
};
const tdStyle = {
  padding: '9px 12px', verticalAlign: 'middle',
};
const codeStyle = {
  fontFamily: TOKENS.font, fontSize: 9,
  background: TOKENS.surface,
  padding: '1px 5px', borderRadius: 3,
  border: `1px solid ${TOKENS.border}`,
  color: TOKENS.muted,
};
const btnBase = {
  padding: '9px 18px', borderRadius: 5,
  fontFamily: TOKENS.font, fontSize: 11, fontWeight: 700,
  cursor: 'pointer', border: 'none',
  textTransform: 'uppercase', letterSpacing: 0.5,
};
const btnPrimary = {
  ...btnBase,
  background: TOKENS.accent, color: '#000',
};
const btnSecondary = {
  ...btnBase,
  background: 'transparent',
  color: TOKENS.text,
  border: `1px solid ${TOKENS.border}`,
};
const btnDisabled = {
  ...btnBase,
  background: 'rgba(100,116,139,0.15)',
  color: TOKENS.muted,
  cursor: 'not-allowed',
};
