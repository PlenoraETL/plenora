/**
 * ImdfBundlesManagerModal.jsx — Gestione cronologia bundle IMDF
 *
 * Tabella completa con filtro per site_id / stato validazione,
 * azioni per-bundle: usa come input publisher, elimina.
 * Non fa fetch autonomo: riceve `bundles` dal parent e invoca callback
 * `onSelect` / `onDelete` / `onClose`.
 *
 * Tema scuro cyan: usa lo shell condiviso `imdf-modal.css`
 * (FE-PHASE2-IMDF-MODAL-SHELL-001).
 */
import React, { useMemo, useState } from 'react';
import { createPortal } from 'react-dom';

const VARS = {
  accent:  'var(--imdf-modal-accent)',
  text:    'var(--imdf-modal-text)',
  muted:   'var(--imdf-modal-muted)',
  border:  'var(--imdf-modal-border)',
  surface: 'var(--imdf-modal-surface)',
  font:    'var(--imdf-modal-font)',
  danger:  'var(--imdf-modal-danger)',
  success: 'var(--imdf-modal-success)',
};

function _ageLabel(h) {
  if (h == null) return '—';
  if (h < 1) return `${Math.round(h * 60)} min fa`;
  if (h < 24) return `${h.toFixed(1)}h fa`;
  return `${Math.round(h / 24)}g fa`;
}

function _validationLabel(vs) {
  if (!vs) return { label: '—', color: VARS.muted };
  if (vs.passed) return { label: '✓ valido', color: VARS.success };
  return { label: `✗ ${vs.total_errors || 0} err`, color: VARS.danger };
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
    <div onClick={onClose} className="imdf-modal__overlay">
      <div
        onClick={(e) => e.stopPropagation()}
        className="imdf-modal__panel imdf-modal__panel--xxl"
      >
        {/* Header */}
        <div className="imdf-modal__header">
          <div>
            <h2 className="imdf-modal__title">📚 Cronologia bundle IMDF</h2>
            <div className="imdf-modal__subtitle">
              {bundles.length} bundle · {filtered.length} visibili
            </div>
          </div>
          <button type="button" onClick={onClose} className="imdf-modal__close">✕</button>
        </div>

        {/* Filtri */}
        <div className="imdf-modal__filters imdf-modal__filters--gap-md">
          <input
            type="text"
            placeholder="Filtra per site_id..."
            value={filterSite}
            onChange={e => setFilterSite(e.target.value)}
            className="imdf-modal__field"
          />
          <select
            value={filterStatus}
            onChange={e => setFilterStatus(e.target.value)}
            className="imdf-modal__select"
          >
            <option value="all">Tutti</option>
            <option value="valid">Solo validi</option>
            <option value="invalid">Solo con errori</option>
          </select>
        </div>

        {/* Tabella */}
        <div className="imdf-modal__body--flex">
          {filtered.length === 0 ? (
            <div className="imdf-modal__empty imdf-modal__empty--lg">
              Nessun bundle trovato coi filtri selezionati.
            </div>
          ) : (
            <table className="imdf-modal__table">
              <thead>
                <tr className="imdf-modal__thead-row">
                  <th className="imdf-modal__th"></th>
                  <th className="imdf-modal__th">Site</th>
                  <th className="imdf-modal__th">Età</th>
                  <th className="imdf-modal__th">Dimensioni</th>
                  <th className="imdf-modal__th">Edifici</th>
                  <th className="imdf-modal__th">Livelli</th>
                  <th className="imdf-modal__th">Locali</th>
                  <th className="imdf-modal__th">Validazione</th>
                  <th className="imdf-modal__th">Azioni</th>
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
                      borderTop: `1px solid ${VARS.border}`,
                      background: isActive ? 'rgba(124,58,237,0.08)' : 'transparent',
                    }}>
                      <td className="imdf-modal__td" style={{ width: 24 }}>
                        {isActive ? <span style={{ color: VARS.accent }}>●</span>
                                  : <span style={{ color: VARS.muted }}>○</span>}
                      </td>
                      <td className="imdf-modal__td" style={{ fontWeight: 700, color: VARS.accent }}>
                        {b.site_id || b.task_id.slice(0, 8)}
                      </td>
                      <td className="imdf-modal__td" style={{ color: VARS.muted }}>
                        {_ageLabel(b.age_hours)}
                      </td>
                      <td className="imdf-modal__td" style={{ color: VARS.muted }}>
                        {b.size_mb != null ? `${b.size_mb} MB` : '—'}
                      </td>
                      <td className="imdf-modal__td">{b.building_count ?? '—'}</td>
                      <td className="imdf-modal__td">{b.level_count ?? '—'}</td>
                      <td className="imdf-modal__td">{b.unit_count ?? '—'}</td>
                      <td className="imdf-modal__td">
                        <span style={{
                          padding: '2px 7px', borderRadius: 3,
                          color: v.color, background: v.color + '14',
                          border: `1px solid ${v.color}55`,
                          fontSize: 9, fontWeight: 700,
                          textTransform: 'uppercase', letterSpacing: 0.3,
                        }}>{v.label}</span>
                      </td>
                      <td className="imdf-modal__td" style={{ display: 'flex', gap: 6 }}>
                        {!isActive && (
                          <button
                            type="button"
                            onClick={() => onSelect(b.task_id)}
                            style={actionBtn(VARS.accent)}
                            title="Usa questo bundle nel publisher"
                          >Usa</button>
                        )}
                        <button
                          type="button"
                          onClick={() => handleDelete(b.task_id)}
                          disabled={isBusy}
                          style={actionBtn(isConfirming ? VARS.danger : VARS.muted, isConfirming)}
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
        <div className="imdf-modal__footer">
          La cancellazione è definitiva: il bundle viene rimosso dal disco del server.
        </div>
      </div>
    </div>
  ), document.body);
}

function actionBtn(color, filled = false) {
  return {
    padding: '4px 9px', borderRadius: 4,
    fontSize: 9, fontWeight: 700,
    textTransform: 'uppercase', letterSpacing: 0.5,
    border: `1px solid ${color}55`,
    background: filled ? color + '22' : 'transparent',
    color: color,
    cursor: 'pointer',
    fontFamily: VARS.font,
  };
}
