/**
 * BundleSlotCard.jsx — card compatta del bundle IMDF attivo.
 *
 * Visualizza: icona live/fallback, site_id, età, badge validation, conteggi
 * edifici/livelli/locali/MB. Include dropdown "Cambia bundle" con le ultime
 * 5 entry + bottone "Gestisci tutti" che apre il modal gestione.
 *
 * Componente puramente presentazionale: riceve bundles + callback, non fa
 * fetch né scrive nello store. Il caller (ResultNode oggi, Publisher prima)
 * gestisce lo stato.
 *
 * Stili statici in `BundleSlotCard.css` co-locato. Le palette runtime
 * (validation summary passed/failed, isActive selection) restano inline
 * via CSS vars locali.
 */
import React from 'react';
import './BundleSlotCard.css';

export function fmtAge(h) {
  if (h == null) return '';
  if (h < 1) return `${Math.round(h * 60)} min fa`;
  if (h < 24) return `${h.toFixed(1)}h fa`;
  return `${Math.round(h / 24)}g fa`;
}

export default function BundleSlotCard({
  loading,
  bundle,
  bundleTaskId,
  isFallback,
  bundles,
  switcherOpen,
  onToggleSwitcher,
  onSelectBundle,
  onOpenManager,
  disabled,
}) {
  if (loading && !bundleTaskId) {
    return <div className="bsc--missing">⏳ Ricerca bundle...</div>;
  }
  if (!bundleTaskId) {
    return <div className="bsc--missing">⚠ Nessun bundle — esegui prima l'arricchimento</div>;
  }
  const age = fmtAge(bundle?.age_hours);
  const v = bundle?.validation_summary;
  const vLabel = v
    ? (v.passed ? '✓ valido' : `✗ ${v.total_errors || 0} err`)
    : null;
  const vColor = v
    ? (v.passed ? '#34D399' : '#F87171')
    : null;
  const recent = (bundles || []).slice(0, 5);
  const showSwitcher = (bundles || []).length > 1;

  return (
    <div className="bsc">
      {/* Riga principale */}
      <div className="bsc__row">
        <span className="bsc__icon">{isFallback ? '📦' : '🆕'}</span>
        <strong className="bsc__site">
          {bundle?.site_id || bundleTaskId.slice(0, 8)}
        </strong>
        <span className="bsc__age">{age}</span>
        {vLabel && (
          <span
            className="bsc__vbadge"
            style={{
              '--bsc-vcolor':  vColor,
              '--bsc-vbg':     vColor + '22',
              '--bsc-vborder': vColor + '55',
            }}
          >{vLabel}</span>
        )}
      </div>

      {/* Riga secondaria: conteggi */}
      {bundle && (bundle.building_count != null || bundle.level_count != null || bundle.unit_count != null) && (
        <div className="bsc__counts">
          {bundle.building_count ?? '—'} edifici · {bundle.level_count ?? '—'} livelli · {bundle.unit_count ?? '—'} locali
          {bundle.size_mb != null && <> · {bundle.size_mb} MB</>}
        </div>
      )}

      {/* Azioni */}
      {showSwitcher && (
        <div className="bsc__actions">
          <button type="button" onClick={onToggleSwitcher} disabled={disabled} className="bsc__btn">
            {switcherOpen ? '▲ Chiudi' : '▾ Cambia bundle'}
          </button>
          <button type="button" onClick={onOpenManager} disabled={disabled} className="bsc__btn">
            📚 Gestisci tutti
          </button>

          {switcherOpen && (
            <div className="bsc__dropdown">
              {recent.map((b) => {
                const isActive = b.task_id === bundleTaskId;
                const bv = b.validation_summary;
                const bvLabel = bv ? (bv.passed ? '✓' : `✗${bv.total_errors || 0}`) : '';
                const bvColor = bv ? (bv.passed ? '#34D399' : '#F87171') : '#7D8BA8';
                return (
                  <button
                    key={b.task_id}
                    type="button"
                    onClick={() => onSelectBundle(b.task_id)}
                    className="bsc__dropdown-item"
                    style={{
                      background: isActive ? 'rgba(124,58,237,0.15)' : 'transparent',
                    }}
                  >
                    <span style={{ color: isActive ? '#7C3AED' : '#7D8BA8' }}>
                      {isActive ? '●' : '○'}
                    </span>
                    <strong className="bsc__dropdown-item-site">
                      {b.site_id || b.task_id.slice(0, 8)}
                    </strong>
                    <span className="bsc__dropdown-item-age">{fmtAge(b.age_hours)}</span>
                    <span className="bsc__dropdown-item-summary" style={{ color: bvColor }}>
                      {bvLabel}
                    </span>
                  </button>
                );
              })}
              {(bundles || []).length > recent.length && (
                <button
                  type="button"
                  onClick={onOpenManager}
                  className="bsc__dropdown-more"
                >
                  📚 Vedi tutti i {bundles.length} bundle...
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
