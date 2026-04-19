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
 */
import React from 'react';

export function fmtAge(h) {
  if (h == null) return '';
  if (h < 1) return `${Math.round(h * 60)} min fa`;
  if (h < 24) return `${h.toFixed(1)}h fa`;
  return `${Math.round(h / 24)}g fa`;
}

const slotBtnStyle = {
  padding: '4px 10px', borderRadius: 4,
  background: 'transparent',
  border: '1px solid rgba(124,58,237,0.35)',
  color: '#7C3AED',
  fontSize: 9, fontWeight: 700,
  textTransform: 'uppercase', letterSpacing: 0.5,
  cursor: 'pointer',
  fontFamily: 'inherit',
};

const missingStyle = {
  padding: '10px 12px',
  borderRadius: 6,
  fontSize: 11,
  color: 'var(--plen-text-sub, #7D8BA8)',
  background: 'rgba(255,180,60,0.08)',
  border: '1px solid rgba(255,180,60,0.25)',
  marginBottom: 10,
};

const okStyle = {
  padding: '10px 12px',
  borderRadius: 6,
  fontSize: 11,
  color: 'var(--plen-text, #DDE4F2)',
  background: 'rgba(124,58,237,0.08)',
  border: '1px solid rgba(124,58,237,0.30)',
  marginBottom: 10,
  display: 'flex', flexDirection: 'column', gap: 8,
};

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
    return <div style={missingStyle}>⏳ Ricerca bundle...</div>;
  }
  if (!bundleTaskId) {
    return <div style={missingStyle}>⚠ Nessun bundle — esegui prima l'arricchimento</div>;
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
    <div style={okStyle}>
      {/* Riga principale */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 14 }}>{isFallback ? '📦' : '🆕'}</span>
        <strong style={{ color: '#7C3AED' }}>
          {bundle?.site_id || bundleTaskId.slice(0, 8)}
        </strong>
        <span style={{ fontSize: 10, opacity: 0.7 }}>{age}</span>
        {vLabel && (
          <span style={{
            fontSize: 9, fontWeight: 700, padding: '2px 7px', borderRadius: 3,
            color: vColor, background: vColor + '22',
            border: `1px solid ${vColor}55`,
            textTransform: 'uppercase', letterSpacing: 0.3,
          }}>{vLabel}</span>
        )}
      </div>

      {/* Riga secondaria: conteggi */}
      {bundle && (bundle.building_count != null || bundle.level_count != null || bundle.unit_count != null) && (
        <div style={{ fontSize: 10, opacity: 0.65 }}>
          {bundle.building_count ?? '—'} edifici · {bundle.level_count ?? '—'} livelli · {bundle.unit_count ?? '—'} locali
          {bundle.size_mb != null && <> · {bundle.size_mb} MB</>}
        </div>
      )}

      {/* Azioni */}
      {showSwitcher && (
        <div style={{ display: 'flex', gap: 6, marginTop: 4, position: 'relative' }}>
          <button type="button" onClick={onToggleSwitcher} disabled={disabled} style={slotBtnStyle}>
            {switcherOpen ? '▲ Chiudi' : '▾ Cambia bundle'}
          </button>
          <button type="button" onClick={onOpenManager} disabled={disabled} style={slotBtnStyle}>
            📚 Gestisci tutti
          </button>

          {switcherOpen && (
            <div style={{
              position: 'absolute', top: '100%', left: 0, right: 0, marginTop: 4,
              background: 'var(--plen-surface-2, #111525)',
              border: '1px solid rgba(124,58,237,0.35)',
              borderRadius: 5,
              boxShadow: '0 8px 24px rgba(0,0,0,0.4)',
              maxHeight: 220, overflowY: 'auto',
              zIndex: 20,
            }}>
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
                    style={{
                      display: 'flex', alignItems: 'center', gap: 8, width: '100%',
                      background: isActive ? 'rgba(124,58,237,0.15)' : 'transparent',
                      border: 'none',
                      borderBottom: '1px solid rgba(255,255,255,0.05)',
                      padding: '7px 10px',
                      color: 'var(--plen-text, #DDE4F2)',
                      fontFamily: 'inherit', fontSize: 10,
                      textAlign: 'left', cursor: 'pointer',
                    }}
                  >
                    <span style={{ color: isActive ? '#7C3AED' : '#7D8BA8' }}>
                      {isActive ? '●' : '○'}
                    </span>
                    <strong style={{ color: '#7C3AED' }}>
                      {b.site_id || b.task_id.slice(0, 8)}
                    </strong>
                    <span style={{ opacity: 0.6 }}>{fmtAge(b.age_hours)}</span>
                    <span style={{ marginLeft: 'auto', color: bvColor, fontWeight: 700 }}>
                      {bvLabel}
                    </span>
                  </button>
                );
              })}
              {(bundles || []).length > recent.length && (
                <button
                  type="button"
                  onClick={onOpenManager}
                  style={{
                    width: '100%', padding: '7px 10px',
                    background: 'transparent', border: 'none',
                    borderTop: '1px solid rgba(255,255,255,0.05)',
                    color: '#7D8BA8', fontSize: 9, cursor: 'pointer',
                    textTransform: 'uppercase', letterSpacing: 0.5,
                    fontFamily: 'inherit',
                  }}
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
