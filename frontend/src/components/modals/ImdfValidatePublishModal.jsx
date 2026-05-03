/**
 * ImdfValidatePublishModal.jsx — Report di pre-pubblicazione IMDF
 *
 * Mostra l'esito di POST /api/imdf/validate-publish:
 *  - Header pass/fail con contatori errori/warning
 *  - Tabella layer con action/delta/schema/issues
 *  - Lista data issues per layer (espandibile)
 *  - Pulsante "Procedi con pubblicazione" (disabilitato se ci sono errors)
 *
 * Tema scuro cyan: usa lo shell condiviso `imdf-modal.css`
 * (FE-PHASE2-IMDF-MODAL-SHELL-001).
 * Legge `var(--plen-*)` → segue data-theme.
 */
import React, { useState } from 'react';
import { createPortal } from 'react-dom';

const VARS = {
  accent:  'var(--imdf-modal-accent)',
  accent2: 'var(--imdf-modal-accent2)',
  text:    'var(--imdf-modal-text)',
  muted:   'var(--imdf-modal-muted)',
  border:  'var(--imdf-modal-border)',
  surface: 'var(--imdf-modal-surface)',
  inset:   'var(--imdf-modal-inset)',
  font:    'var(--imdf-modal-font)',
  danger:  'var(--imdf-modal-danger)',
  warn:    'var(--imdf-modal-warn)',
  success: 'var(--imdf-modal-success)',
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
  create:       VARS.accent,
  'create-layer': VARS.accent,
  replace:      VARS.warn,
  append:       VARS.success,
  upsert:       VARS.accent2,
  update:       '#4B8BFF',  // blue — distinto da accent2 purple di upsert
  error:        VARS.danger,
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

  return createPortal((
    <div onClick={onClose} className="imdf-modal__overlay">
      <div onClick={(e) => e.stopPropagation()} className="imdf-modal__panel imdf-modal__panel--xl">

        {/* Header */}
        <div className="imdf-modal__header imdf-modal__header--wide">
          <div className="imdf-modal__header-info imdf-modal__header-info--gap-lg">
            <div>
              <h2 className="imdf-modal__title">🔍 Validazione pubblicazione IMDF</h2>
              <div className="imdf-modal__subtitle">
                Mode · {mode || '—'} {fsName ? `· ${fsName}` : ''}
              </div>
            </div>
            {!loading && report && (
              <div style={{
                fontSize: 10, fontWeight: 700,
                padding: '4px 10px', borderRadius: 4,
                textTransform: 'uppercase', letterSpacing: 0.5,
                color: ok ? VARS.success : VARS.danger,
                background: (ok ? VARS.success : VARS.danger) + '14',
                border: `1px solid ${(ok ? VARS.success : VARS.danger)}55`,
              }}>
                {ok ? '✓ OK — Pronto' : '✗ Errori da risolvere'}
              </div>
            )}
          </div>
          <button type="button" onClick={onClose} className="imdf-modal__close">✕</button>
        </div>

        {/* Body */}
        <div className="imdf-modal__body imdf-modal__body--flex">

          {loading && (
            <div className="imdf-modal__loading">
              <div className="imdf-modal__spinner" />
              <div>Analisi in corso...</div>
            </div>
          )}

          {error && !loading && (
            <div className="imdf-modal__error">
              <div className="imdf-modal__error-title">✗ Errore validazione</div>
              <div className="imdf-modal__error-detail">{error}</div>
            </div>
          )}

          {!loading && !error && report && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>

              {/* Summary row */}
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                <Badge color={VARS.danger}  count={report.summary?.errors ?? 0}   label="errori" />
                <Badge color={VARS.warn}    count={report.summary?.warnings ?? 0} label="warning" />
                <Badge color={VARS.accent}  count={report.summary?.layers_count ?? 0} label="layer" />
                <Badge color={VARS.accent2} count={report.summary?.total_features ?? 0} label="feature" />
                <div className="imdf-modal__filters-spacer" />
                <Badge
                  color={report.fs_exists ? VARS.success : VARS.muted}
                  label={report.fs_exists ? 'FS esistente' : 'Nuovo FS'}
                />
              </div>

              {/* Top issues globali */}
              {Array.isArray(report.top_issues) && report.top_issues.length > 0 && (
                <div>
                  <div className="imdf-modal__section-title">Problemi generali</div>
                  {report.top_issues.map((i, idx) => (
                    <IssueRow key={idx} issue={i} />
                  ))}
                </div>
              )}

              {/* Tabella layer */}
              {Array.isArray(report.layers) && report.layers.length > 0 && (
                <div>
                  <div className="imdf-modal__section-title">Layer coinvolti</div>
                  <div className="imdf-modal__table-wrap">
                    <table className="imdf-modal__table">
                      <thead>
                        <tr className="imdf-modal__thead-row imdf-modal__thead-row--inset">
                          <th className="imdf-modal__th">Layer</th>
                          <th className="imdf-modal__th">Azione</th>
                          <th className="imdf-modal__th">Bundle</th>
                          <th className="imdf-modal__th">FS</th>
                          <th className="imdf-modal__th">Delta</th>
                          <th className="imdf-modal__th">Schema</th>
                          <th className="imdf-modal__th">Issues</th>
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
                            ? VARS.danger
                            : (totalIssues > 0 ? VARS.warn : (ACTION_COLOR[action] || VARS.muted));
                          const isExpanded = expandedLayer === i;
                          return (
                            <React.Fragment key={l.name}>
                              <tr style={{
                                borderTop: `1px solid ${VARS.border}`,
                                cursor: totalIssues > 0 ? 'pointer' : 'default',
                              }}
                              onClick={() => totalIssues > 0 && setExpandedLayer(isExpanded ? null : i)}>
                                <td className="imdf-modal__td imdf-modal__td--pad-lg" style={{ color: VARS.accent, fontWeight: 700 }}>
                                  {totalIssues > 0 && (
                                    <span style={{ color: VARS.muted, marginRight: 4 }}>
                                      {isExpanded ? '▼' : '▶'}
                                    </span>
                                  )}
                                  {l.name}
                                </td>
                                <td className="imdf-modal__td imdf-modal__td--pad-lg">
                                  <span style={{
                                    fontSize: 10, padding: '2px 7px', borderRadius: 3,
                                    color: actColor, background: actColor + '14',
                                    border: `1px solid ${actColor}44`,
                                    textTransform: 'uppercase', letterSpacing: 0.3,
                                    fontWeight: 700,
                                  }}>{actLabel}</span>
                                </td>
                                <td className="imdf-modal__td imdf-modal__td--pad-lg" style={{ color: VARS.text }}>{l.bundle_count ?? 0}</td>
                                <td className="imdf-modal__td imdf-modal__td--pad-lg" style={{ color: VARS.muted }}>
                                  {l.fs_count != null ? l.fs_count : '—'}
                                </td>
                                <td className="imdf-modal__td imdf-modal__td--pad-lg" style={{ fontFamily: VARS.font, fontSize: 10 }}>
                                  {l.delta || '—'}
                                </td>
                                <td className="imdf-modal__td imdf-modal__td--pad-lg">
                                  <SchemaDot status={l.schema_compat} />
                                </td>
                                <td className="imdf-modal__td imdf-modal__td--pad-lg">
                                  {totalIssues > 0 ? (
                                    <span style={{
                                      fontSize: 9, fontWeight: 700,
                                      padding: '2px 6px', borderRadius: 3,
                                      color: hasIssueError(l) ? VARS.danger : VARS.warn,
                                      background: (hasIssueError(l) ? VARS.danger : VARS.warn) + '14',
                                      border: `1px solid ${(hasIssueError(l) ? VARS.danger : VARS.warn)}55`,
                                    }}>{totalIssues}</span>
                                  ) : (
                                    <span style={{ color: VARS.success, fontSize: 11 }}>✓</span>
                                  )}
                                </td>
                              </tr>
                              {isExpanded && (
                                <tr>
                                  <td colSpan={7} style={{
                                    padding: '10px 14px',
                                    background: VARS.inset,
                                    borderTop: `1px solid ${VARS.border}`,
                                  }}>
                                    {(l.schema_issues || []).length > 0 && (
                                      <div style={{ marginBottom: 8 }}>
                                        <div style={{
                                          fontSize: 9, color: VARS.muted,
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
                                          fontSize: 9, color: VARS.muted,
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
                                        fontSize: 9, color: VARS.muted, marginTop: 8,
                                      }}>
                                        match column: <code className="imdf-modal__code imdf-modal__code--xs">{l.match_column}</code>
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
                  <div className="imdf-modal__hint">
                    Click su una riga con issues per espanderla
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="imdf-modal__footer--actions">
          <button type="button" onClick={onClose} className="imdf-modal__btn imdf-modal__btn--secondary">
            Annulla
          </button>
          <button
            type="button"
            onClick={onProceed}
            disabled={loading || hasErrors || !report || !!error}
            className={`imdf-modal__btn ${
              hasErrors || loading || !report || error
                ? 'imdf-modal__btn--disabled'
                : 'imdf-modal__btn--primary'
            }`}
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
      fontFamily: VARS.font,
    }}>
      {count != null && <span style={{ fontSize: 13 }}>{count}</span>}
      <span>{label}</span>
    </span>
  );
}

function SchemaDot({ status }) {
  const color =
    status === 'ok'       ? VARS.success :
    status === 'warning'  ? VARS.warn :
    status === 'mismatch' ? VARS.danger : VARS.muted;
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
    sev === 'error'   ? VARS.danger :
    sev === 'warning' ? VARS.warn : VARS.muted;
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
      <code className="imdf-modal__code imdf-modal__code--xs">{issue.code}</code>
      <span style={{ flex: 1, color: VARS.text }}>{issue.message}</span>
    </div>
  );
}
