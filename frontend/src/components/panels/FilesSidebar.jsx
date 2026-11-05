/**
 * FilesSidebar.jsx — Sidebar lista file per editor
 * Paginazione 10 + "Mostra altri" come EstraiEditor
 */
import './SourcesSidebar.css';
import React, { useState, useMemo } from 'react';
import { formatNumber } from '../../utils/format';

const PAGE_SIZE = 10;

const FILE_ICONS = { csv:'📄', xlsx:'📊', xls:'📊', json:'📋', geojson:'🗺️', gpkg:'🗺️', shp:'🗺️', gdb:'🗃️' };
const FILE_BADGE_COLORS = {
  csv:     { bg:'rgba(52,193,122,.12)',  border:'rgba(52,193,122,.25)',  color:'#34C17A' },
  xlsx:    { bg:'rgba(52,193,122,.12)',  border:'rgba(52,193,122,.25)',  color:'#34C17A' },
  xls:     { bg:'rgba(52,193,122,.12)',  border:'rgba(52,193,122,.25)',  color:'#34C17A' },
  json:    { bg:'rgba(245,166,35,.12)',  border:'rgba(245,166,35,.25)',  color:'#F5A623' },
  geojson: { bg:'rgba(75,139,255,.12)',  border:'rgba(75,139,255,.25)',  color:'#4B8BFF' },
  gpkg:    { bg:'rgba(155,109,255,.12)', border:'rgba(155,109,255,.25)', color:'#9B6DFF' },
  shp:     { bg:'rgba(248,113,113,.12)', border:'rgba(248,113,113,.25)', color:'#F87171' },
  gdb:     { bg:'rgba(155,109,255,.12)', border:'rgba(155,109,255,.25)', color:'#9B6DFF' },
};

function FilesSidebar({
  files = [],
  onDragStart,
  title = "📁 File Esterni",
  subtitle = "Trascina un file nel canvas",
  emptyMessage = "Nessun file disponibile.",
  uploadLink = '/files',
  uploadLinkText = 'Carica file esterni',
  accentColor = '#4B8BFF',
  showSearch = false,
  showRowCount = true,
  className = ''
}) {
  const [searchQuery, setSearchQuery] = useState('');
  const [collapsed, setCollapsed] = useState(false);
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);

  const handleSearch = (val) => { setSearchQuery(val); setVisibleCount(PAGE_SIZE); };

  const filteredFiles = useMemo(() => {
    if (!searchQuery.trim()) return files;
    const q = searchQuery.toLowerCase();
    return files.filter(f => f.name.toLowerCase().includes(q) || f.format?.toLowerCase().includes(q));
  }, [files, searchQuery]);

  const getIcon = (fmt) => FILE_ICONS[fmt?.toLowerCase()] || '📁';
  const getBadgeStyle = (fmt) => {
    const s = FILE_BADGE_COLORS[fmt?.toLowerCase()];
    return s ? { background: s.bg, border: `1px solid ${s.border}`, color: s.color }
             : { background:'rgba(125,139,168,.1)', border:'1px solid rgba(125,139,168,.2)', color:'#7D8BA8' };
  };

  const handleDragStart = (event, file) => {
    if (onDragStart) onDragStart(event, 'file_source', {
      id: file.id, file_source_id: file.id,
      name: file.name, format: file.format
    });
  };

  const remaining = filteredFiles.length - visibleCount;
  const nextChunk = Math.min(PAGE_SIZE, remaining);

  return (
    <div className={`sources-sidebar sources-sidebar--files ${className}`} style={{ '--accent-color': accentColor }}>

      {/* Section header collassabile */}
      <div
        className="ss-sec-hdr"
        role="button" tabIndex={0}
        onClick={() => setCollapsed(c => !c)}
        onKeyDown={e => (e.key==='Enter'||e.key===' ') && setCollapsed(c=>!c)}
      >
        <span className={`ss-sec-hdr__arrow${collapsed ? '' : ' open'}`}>▶</span>
        <span className="ss-sec-hdr__label">{title}</span>
        <span className="ss-sec-hdr__count">{filteredFiles.length}</span>
      </div>

      {!collapsed && (
        <>
          {showSearch && (
            <div className="sources-sidebar__search">
              <input
                type="text"
                placeholder="Cerca file…"
                value={searchQuery}
                onChange={e => handleSearch(e.target.value)}
                className="sources-sidebar__search-input"
              />
              {searchQuery && (
                <button type="button" className="sources-sidebar__search-clear" onClick={() => handleSearch('')}>×</button>
              )}
            </div>
          )}

          <div className="sources-sidebar__list">
            {filteredFiles.length === 0 ? (
              <div className="sources-sidebar__empty">
                {emptyMessage}
                {uploadLink && (
                  <><br /><a href={uploadLink} className="sources-sidebar__empty-link" style={{ color: accentColor }}>{uploadLinkText}</a></>
                )}
              </div>
            ) : (
              <>
                {filteredFiles.slice(0, visibleCount).map(file => (
                  <div
                    key={file.id}
                    className="sources-sidebar__item sources-sidebar__item--file"
                    draggable
                    onDragStart={e => handleDragStart(e, file)}
                  >
                    <span className="sources-sidebar__item-icon">{getIcon(file.format)}</span>
                    <div className="sources-sidebar__item-info">
                      <span className="sources-sidebar__item-name">{file.name}</span>
                      {showRowCount && file.row_count && (
                        <span className="sources-sidebar__item-meta">{formatNumber(file.row_count)} righe</span>
                      )}
                    </div>
                    <span
                      className="sources-sidebar__item-badge"
                      style={getBadgeStyle(file.format)}
                    >
                      {file.format?.toUpperCase()}
                    </span>
                  </div>
                ))}

                {remaining > 0 && (
                  <button
                    type="button"
                    className="ss-load-more"
                    onClick={() => setVisibleCount(c => c + PAGE_SIZE)}
                  >
                    ▼ Mostra altri {nextChunk} di {remaining} rimasti
                  </button>
                )}
              </>
            )}
          </div>
        </>
      )}
    </div>
  );
}

export default FilesSidebar;
