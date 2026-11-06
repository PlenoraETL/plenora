/**
 * FileManager/FileCard.jsx — card riepilogo file singolo nella griglia.
 */

import React from 'react';
import { getFormatConfig, SPATIAL_FORMATS } from './helpers';
import { formatBytes as formatFileSize, formatNumber } from '../../utils/format';

// onDelete / onShare arrivano solo se permessi (can.editFile, can.shareResources).
function FileCard({ file, onPreview, onDelete, onShare }) {
  const cfg = getFormatConfig(file.format);
  const label = file.layer ? `${cfg.label} · ${file.layer}` : file.sheet ? `${cfg.label} · ${file.sheet}` : cfg.label;
  return (
    <div className="fm__card">
      <div className="fm__card-accent" style={{ background: cfg.color }} />
      <div className="fm__card-header">
        <span className="fm__card-icon">{cfg.icon}</span>
        <div className="fm__card-header-info">
          <div className="fm__card-name">{file.name}</div>
          <span className="fm__card-badge" style={{ color: cfg.color, background: `rgba(${cfg.rgb},0.08)`, borderColor: `rgba(${cfg.rgb},0.22)` }}>
            {label}
          </span>
        </div>
      </div>
      <div className="fm__card-body">
        {file.description && <div className="fm__card-desc">{file.description}</div>}
        <div className="fm__card-meta">
          <div><div className="fm__meta-key">{SPATIAL_FORMATS.includes(file.format) ? 'Features' : 'Righe'}</div><div className="fm__meta-val">{formatNumber(file.row_count)}</div></div>
          <div><div className="fm__meta-key">Colonne</div><div className="fm__meta-val">{file.columns?.length || 0}</div></div>
          <div><div className="fm__meta-key">Dimensione</div><div className="fm__meta-val">{formatFileSize(file.file_size)}</div></div>
          {file.crs ? (
            <div><div className="fm__meta-key">CRS</div><div className="fm__meta-val">{file.crs}</div></div>
          ) : (
            <div><div className="fm__meta-key">File</div><div className="fm__meta-val fm__meta-val--filename">{file.original_filename}</div></div>
          )}
        </div>
        {file.columns?.length > 0 && (
          <div className="fm__card-columns">
            <div className="fm__columns-label">Colonne</div>
            <div className="fm__columns-list">
              {file.columns.slice(0, 5).map((col, i) => (
                <span key={i} className="fm__col-tag">{col.name}</span>
              ))}
              {file.columns.length > 5 && <span className="fm__col-more">+{file.columns.length - 5}</span>}
            </div>
          </div>
        )}
        <div className="fm__card-footer">
          <span className="fm__card-filename">{file.original_filename}</span>
          <div className="fm__card-actions">
            <button type="button" className="fm__btn fm__btn--ghost fm__btn--sm" onClick={onPreview}>👁 Anteprima</button>
            {onShare && (
              <button
                type="button"
                className="fm__btn fm__btn--ghost fm__btn--sm"
                title="Gestisci condivisione cross-gruppo"
                onClick={onShare}
              >🔗</button>
            )}
            {onDelete && (
              <button type="button" className="fm__btn fm__btn--danger fm__btn--sm" onClick={onDelete} aria-label="Elimina file">🗑</button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export default FileCard;
