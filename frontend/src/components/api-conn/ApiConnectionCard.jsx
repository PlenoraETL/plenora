/**
 * ApiConnectionCard.jsx — Card di un connettore API in lista
 *
 * Componente presentational: riceve l'oggetto
 * `connection` + callback (`onEdit` / `onDelete` / `onQuickTest` /
 * `onShare`) e renderizza la card. Nessuna fetch, nessuna chiamata
 * API, nessun page-level state.
 *
 * Gli helper derivati (icon by category, status by usage/quota,
 * timestamp formatter) vivono nello stesso file perche' producono
 * dati visivi presentational a partire dalla connection.
 */
import React from 'react';

const CATEGORY_ICONS = {
  geocoding:  '🌍',
  routing:    '🚗',
  catasto:    '🏠',
  datasource: '📁',
  custom:     '⚙️',
};

const STATUS_CFG = {
  active:   { cls:'acm__card--active',   dot:'var(--acm-ok)',   badge:'acm__status--ok'  },
  warning:  { cls:'acm__card--warning',  dot:'var(--acm-warn)', badge:'acm__status--warn'},
  critical: { cls:'acm__card--critical', dot:'var(--acm-err)',  badge:'acm__status--err' },
};

function getApiIcon(api) {
  if (api.category && CATEGORY_ICONS[api.category]) return CATEGORY_ICONS[api.category];
  const n = (api.name || '').toLowerCase();
  const u = (api.endpoint?.url || api.url || '').toLowerCase();
  if (n.includes('nominatim') || n.includes('geocod') || u.includes('nominatim')) return '🌍';
  if (n.includes('osrm') || n.includes('rout')) return '🚗';
  if (n.includes('catasto') || n.includes('omi')) return '🏠';
  if (n.includes('model') || n.includes('ml')) return '🧠';
  if (n.includes('sharepoint') || n.includes('ftp')) return '📁';
  return '⚙️';
}

function getApiStatus(api) {
  const quota = api.usage?.quota_used || 0;
  const ql = api.usage?.quota_limit || 0;
  const qp = ql > 0 ? Math.round((quota / ql) * 100) : 0;
  const er = api.usage?.calls_total > 0
    ? ((api.usage?.calls_error || 0) / api.usage.calls_total * 100).toFixed(1)
    : 0;
  if (qp >= 90) return { status:'critical', statusLabel:'Quota esaurita', quotaPercent:qp, errorRate:er };
  if (qp >= 80 || er > 5) return { status:'warning', statusLabel:`Attenzione ${qp || er}%`, quotaPercent:qp, errorRate:er };
  return { status:'active', statusLabel:'Attiva', quotaPercent:qp, errorRate:er };
}

function formatTimeAgo(d) {
  if (!d) return 'Mai';
  const s = Math.floor((new Date() - new Date(d)) / 1000);
  if (s < 60) return 'Ora';
  if (s < 3600) return `${Math.floor(s / 60)}m fa`;
  if (s < 86400) return `${Math.floor(s / 3600)}h fa`;
  return `${Math.floor(s / 86400)}g fa`;
}

// Re-export per uso esterno (es. globalStats del page che oggi
// chiama getApiStatus per contare warningApis). Mantengono il single
// source per la logica derivata dalla connection.
export { getApiStatus, getApiIcon, formatTimeAgo };

export default function ApiConnectionCard({
  connection,
  isSuperadmin,
  onEdit,
  onDelete,
  onQuickTest,
  onShare,
}) {
  const api = connection;
  const { status, statusLabel, quotaPercent } = getApiStatus(api);
  const sc = STATUS_CFG[status];
  const canEdit = api.can_edit !== false;

  return (
    <div className={`acm__card ${sc.cls}`}>
      <div className="acm__card-bar"/>
      <div className="acm__card-inner">
        <div className="acm__card-header">
          <div className="acm__card-icon">{getApiIcon(api)}</div>
          <div className="acm__card-header-info">
            <div className="acm__card-name">
              {api.name}
              {!canEdit && (
                <span
                  title="Solo un superadmin può modificare questa risorsa"
                  className="acm__lock-hint"
                >
                  🔒
                </span>
              )}
            </div>
            <div className="acm__card-badges">
              <span className={`acm__badge acm__badge--type${api.api_type==='generator'?' acm__badge--gen':''}`}>
                {api.api_type==='generator'?'Generatore':'Arricchitore'}
              </span>
              {api.connection_type && api.connection_type!=='manual' && (
                <span className="acm__badge acm__badge--conn">
                  {api.connection_type==='openapi'?'📋 OpenAPI':api.connection_type==='template'?'🍪 Template':api.connection_type==='datasource'?'📁 DataSource':null}
                </span>
              )}
              <span className={`acm__badge ${sc.badge}`}>
                <span className="acm__status-dot" style={{background:sc.dot}}/>
                {statusLabel}
              </span>
            </div>
          </div>
        </div>
        <div className="acm__card-desc">{api.description||'Nessuna descrizione'}</div>
        <div className="acm__card-stats">
          <div className="acm__card-stat"><span className="acm__card-stat-val">{(api.usage?.calls_total||0).toLocaleString()}</span><span className="acm__card-stat-label">chiamate</span></div>
          {api.usage?.quota_limit > 0 && <div className="acm__card-stat"><span className="acm__card-stat-val">{quotaPercent}%</span><span className="acm__card-stat-label">quota</span></div>}
          <div className="acm__card-stat"><span className="acm__card-stat-val">{formatTimeAgo(api.usage?.last_call)}</span><span className="acm__card-stat-label">ultimo</span></div>
        </div>
        {api.usage?.quota_limit > 0 && (
          <div className="acm__quota-bar"><div className={`acm__quota-fill acm__quota-fill--${status}`} style={{width:`${Math.min(quotaPercent,100)}%`}}/></div>
        )}
        <div className="acm__card-footer">
          <button type="button" className="acm__btn acm__btn--ghost acm__btn--sm" onClick={()=>onQuickTest?.(api)}>🧪 Test</button>
          <div className="acm__card-actions">
            {canEdit && (
              <button type="button" className="acm__icon-btn" title="Modifica" aria-label="Modifica connettore" onClick={()=>onEdit?.(api)}>⚙️</button>
            )}
            {isSuperadmin && (
              <button type="button" className="acm__icon-btn" title="Gestisci condivisione cross-gruppo" aria-label="Gestisci condivisione cross-gruppo" onClick={()=>onShare?.(api)}>🔗</button>
            )}
            {canEdit && (
              <button type="button" className="acm__icon-btn acm__icon-btn--danger" title="Elimina" aria-label="Elimina connettore" onClick={()=>onDelete?.(api)}>🗑</button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
