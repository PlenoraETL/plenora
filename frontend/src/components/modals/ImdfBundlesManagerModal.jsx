/**
 * ImdfBundlesManagerModal.jsx — Gestione cronologia bundle IMDF
 *
 * Tabella completa con filtro per site_id / stato validazione,
 * azioni per-bundle: usa come input publisher, elimina.
 * Non fa fetch autonomo: riceve `bundles` dal parent e invoca callback
 * `onSelect` / `onDelete` / `onClose`.
 */
import React, { useMemo, useState } from 'react';
import { createPortal } from 'react-dom';

const TOKENS = {
  bg:       'var(--plen-surface, #0C0F18)',
  surface:  'var(--plen-surface-2, #111525)',
  inset:    'var(--plen-bg, #07090F)',
  border:   'var(--plen-border, rgba(255,255,255,0.08))',
  accent:   '#7C3AED',
  text:     'var(--plen-text, #DDE4F2)',
  muted:    'var(--plen-text-sub, #7D8BA8)',
  danger:   'var(--plen-err, #F87171)',
  warn:     'var(--plen-warn, #FBBF24)',
  success:  'var(--plen-ok, #34D399)',
  font:     "var(--plen-font-mono, 'JetBrains Mono'), ui-monospace, Menlo, monospace",
};

function _ageLabel(h) {
  if (h == null) return '—';
  if (h < 1) return `${Math.round(h * 60)} min fa`;
  if (h < 24) return `${h.toFixed(1)}h fa`;
  return `${Math.round(h / 24)}g fa`;
}

function _validationLabel(vs) {
  if (!vs) return { label: '—', color: TOKENS.muted };
  if (vs.passed) return { label: '✓ valido', color: TOKENS.success };
  return { label: `✗ ${vs.total_errors || 0} err`, color: TOKENS.danger };
}

export default function ImdfBundlesManagerModal({
  isOpen,
  onClose,
  bundles,           // array da listImdfBundles
  selectedTaskId,    // task_id corrente del publisher
  onSelect,          // (task_id) → void
  onDelete,          // async (task_id) → void
}) {
  const [filterSite, setFilterSite] = useState('');
  const [filterStatus, setFilterStatus] = useState('all'); // 'all' | 'valid' | 'invalid'
  const [busyId, setBusyId] = useState(null);
  const [confirmId, setConfirmId] = useState(null);

  const filtered = useMemo(() => {
    let out = bundles || [];
    if (filterSite) {
      const q = filterSite.toLowerCase();
      out = out.filter(b => (b.site_id || '').toLowerCase().includes(q));
    }
    if (filterStatus === 'valid') {
      out = out.filter(b => b.validation_summary?.passed === true);
    } else if (filterStatus === 'invalid') {
      out = out.filter(b => b.validation_summary?.passed === false);
    }
    return out;
  }, [bundles, filterSite, filterStatus]);

  if (!isOpen) return null;

  const handleDelete = async (taskId) => {
    if (confirmId !== taskId) {
      setConfirmId(taskId);
      return;
    }
    setBusyId(taskId);
    try {
      await onDelete(taskId);
    } finally {
      setBusyId(null);
      setConfirmId(null);
    }
  };

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
          width: 'min(1100px, 96vw)',
          height: 'min(760px, 90vh)',
          boxShadow: '0 24px 80px rgba(0,0,0,0.7)',
          display: 'flex', flexDirection: 'column', overflow: 'hidden',
        }}
      >
        {/* Header */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '16px 20px', background: TOKENS.surface,
          borderBottom: `1px solid ${TOKENS.border}`,
        }}>
          <div>
            <h2 style={{
              margin: 0, fontSize: 15, fontWeight: 800,
              color: TOKENS.accent, letterSpacing: 0.3,
            }}>
              📚 Cronologia bundle IMDF
            </h2>
            <div style={{
              fontSize: 9, color: TOKENS.muted, marginTop: 2,
              textTransform: 'uppercase', letterSpacing: 0.5,
            }}>
              {bundles.length} bundle · {filtered.length} visibili
            </div>
          </div>
          <button type="button" onClick={onClose} style={{
            background: 'none', border: 'none', color: TOKENS.muted,
            fontSize: 18, cursor: 'pointer', padding: 4,
          }}>✕</button>
        </div>

        {/* Filtri */}
        <div style={{
          display: 'flex', gap: 10, alignItems: 'center',
          padding: '12px 20px', borderBottom: `1px solid ${TOKENS.border}`,
        }}>
          <input
            type="text"
            placeholder="Filtra per site_id..."
            value={filterSite}
            onChange={e => setFilterSite(e.target.value)}
            style={{
              flex: 1, background: TOKENS.surface,
              border: `1px solid ${TOKENS.border}`,
              color: TOKENS.text, padding: '6px 10px',
              borderRadius: 4, fontSize: 11, fontFamily: TOKENS.font,
            }}
          />
          <select
            value={filterStatus}
            onChange={e => setFilterStatus(e.target.value)}
            style={{
              background: TOKENS.surface,
              border: `1px solid ${TOKENS.border}`,
              color: TOKENS.text, padding: '6px 10px',
              borderRadius: 4, fontSize: 11, fontFamily: TOKENS.font,
            }}
          >
            <option value="all">Tutti</option>
            <option value="valid">Solo validi</option>
            <option value="invalid">Solo con errori</option>
          </select>
        </div>

        {/* Tabella */}
        <div style={{ flex: 1, overflow: 'auto' }}>
          {filtered.length === 0 ? (
            <div style={{
              padding: '40px 20px', color: TOKENS.muted, fontSize: 12,
              textAlign: 'center',
            }}>
              Nessun bundle trovato coi filtri selezionati.
            </div>
          ) : (
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
              <thead>
                <tr style={{
                  background: TOKENS.surface, position: 'sticky', top: 0, zIndex: 1,
                }}>
                  <th style={thStyle}></th>
                  <th style={thStyle}>Site</th>
                  <th style={thStyle}>Età</th>
                  <th style={thStyle}>Dimensioni</th>
                  <th style={thStyle}>Edifici</th>
                  <th style={thStyle}>Livelli</th>
                  <th style={thStyle}>Locali</th>
                  <th style={thStyle}>Validazione</th>
                  <th style={thStyle}>Azioni</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((b) => {
                  const isActive = b.task_id === selectedTaskId;
                  const v = _validationLabel(b.validation_summary);
                  const isConfirming = confirmId === b.task_id;
                  const isBusy = busyId === b.task_id;
                  return (
                    <tr key={b.task_id} style={{
                      borderTop: `1px solid ${TOKENS.border}`,
                      background: isActive ? 'rgba(124,58,237,0.08)' : 'transparent',
                    }}>
                      <td style={{ ...tdStyle, width: 24 }}>
                        {isActive ? <span style={{ color: TOKENS.accent }}>●</span>
                                  : <span style={{ color: TOKENS.muted }}>○</span>}
                      </td>
                      <td style={{ ...tdStyle, fontWeight: 700, color: TOKENS.accent }}>
                        {b.site_id || b.task_id.slice(0, 8)}
                      </td>
                      <td style={{ ...tdStyle, color: TOKENS.muted }}>
                        {_ageLabel(b.age_hours)}
                      </td>
                      <td style={{ ...tdStyle, color: TOKENS.muted }}>
                        {b.size_mb != null ? `${b.size_mb} MB` : '—'}
                      </td>
                      <td style={tdStyle}>{b.building_count ?? '—'}</td>
                      <td style={tdStyle}>{b.level_count ?? '—'}</td>
                      <td style={tdStyle}>{b.unit_count ?? '—'}</td>
                      <td style={tdStyle}>
                        <span style={{
                          padding: '2px 7px', borderRadius: 3,
                          color: v.color, background: v.color + '14',
                          border: `1px solid ${v.color}55`,
                          fontSize: 9, fontWeight: 700,
                          textTransform: 'uppercase', letterSpacing: 0.3,
                        }}>{v.label}</span>
                      </td>
                      <td style={{ ...tdStyle, display: 'flex', gap: 6 }}>
                        {!isActive && (
                          <button
                            type="button"
                            onClick={() => onSelect(b.task_id)}
                            style={actionBtn(TOKENS.accent)}
                            title="Usa questo bundle nel publisher"
                          >Usa</button>
                        )}
                        <button
                          type="button"
                          onClick={() => handleDelete(b.task_id)}
                          disabled={isBusy}
                          style={actionBtn(isConfirming ? TOKENS.danger : TOKENS.muted, isConfirming)}
                          title={isConfirming ? 'Click di nuovo per confermare' : 'Elimina bundle'}
                        >
                          {isBusy ? '⏳' : isConfirming ? '✗ Conferma' : '🗑'}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>

        {/* Footer */}
        <div style={{
          padding: '12px 20px', borderTop: `1px solid ${TOKENS.border}`,
          background: TOKENS.surface, fontSize: 9, color: TOKENS.muted,
          textAlign: 'center',
        }}>
          La cancellazione è definitiva: il bundle viene rimosso dal disco del server.
        </div>
      </div>
    </div>
  ), document.body);
}

const thStyle = {
  padding: '10px 12px', fontSize: 9, color: TOKENS.muted,
  fontWeight: 700, textTransform: 'uppercase', letterSpacing: 1,
  textAlign: 'left', borderBottom: `1px solid ${TOKENS.border}`,
};
const tdStyle = { padding: '8px 12px', verticalAlign: 'middle' };

function actionBtn(color, filled = false) {
  return {
    padding: '4px 9px', borderRadius: 4,
    fontSize: 9, fontWeight: 700,
    textTransform: 'uppercase', letterSpacing: 0.5,
    border: `1px solid ${color}55`,
    background: filled ? color + '22' : 'transparent',
    color: color,
    cursor: 'pointer',
    fontFamily: TOKENS.font,
  };
}
