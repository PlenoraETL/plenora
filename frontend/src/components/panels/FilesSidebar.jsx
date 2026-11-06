/**
 * FilesSidebar.jsx — Sidebar lista file per editor
 * Paginazione 10 + "Mostra altri" come EstraiEditor
 */
import './SourcesSidebar.css';
import React, { useState, useMemo } from 'react';
import { formatNumber } from '../../utils/format';
import SearchField, { SearchEmpty } from '../common/SearchField';
import { useSearch, useSearchPage } from '../../hooks/useSearch';
import { pressable } from '../../utils/pressable';
import { termName } from '../../utils/glossary';
import { startSourceDrag } from '../editor/editorNodeCatalog';

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
  onPick,
  // UI-3 (U30): carica un file senza uscire dall'editor (finestra
  // dell'editor); senza, resta il collegamento alla gestione file.
  onUpload = null,
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
  const search = useSearch();
  const [collapsed, setCollapsed] = useState(false);
  // Paginazione legata alla ricerca: una ricerca nuova riparte da PAGE_SIZE.
  const [visibleCount, setVisibleCount] = useSearchPage(search, PAGE_SIZE);
  const showMore = () => setVisibleCount(c => c + PAGE_SIZE);

  const filteredFiles = useMemo(() => search.filter(files, f => [f.name, f.format]), [files, search.filter]);

  const getIcon = (fmt) => FILE_ICONS[fmt?.toLowerCase()] || '📁';
  const getBadgeStyle = (fmt) => {
    const s = FILE_BADGE_COLORS[fmt?.toLowerCase()];
    return s ? { background: s.bg, border: `1px solid ${s.border}`, color: s.color }
             : { background:'rgba(125,139,168,.1)', border:'1px solid rgba(125,139,168,.2)', color:'#7D8BA8' };
  };

  // UI-3 (U11): clic o Invio aggiunge il file al centro della vista;
  // trascinamento nel formato unico (editorNodeCatalog.startSourceDrag).
  const handleDragStart = (event, file) => startSourceDrag(event, 'file_source', file);

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
          {onUpload && (
            <button type="button" className="ss-load-more" onClick={() => onUpload()}>
              ＋ {termName('upload_file')}
            </button>
          )}
          {showSearch && (
            <SearchField
              search={search}
              label="Cerca file"
              className="sources-sidebar__search"
              compact
              shown={filteredFiles.length}
              total={files.length}
            />
          )}

          <div className="sources-sidebar__list">
            {filteredFiles.length === 0 && search.active ? (
              <SearchEmpty search={search} shown={0} />
            ) : filteredFiles.length === 0 ? (
              <div className="sources-sidebar__empty">
                {emptyMessage}
                {!onUpload && uploadLink && (
                  <><br /><a href={uploadLink} className="sources-sidebar__empty-link" style={{ color: accentColor }}>{uploadLinkText}</a></>
                )}
              </div>
            ) : (
              <>
                {filteredFiles.slice(0, visibleCount).map(file => (
                  <div
                    key={file.id}
                    className="sources-sidebar__item sources-sidebar__item--file"
                    title="Clic o Invio per aggiungere al centro · trascina per posizionare"
                    draggable
                    {...pressable(() => onPick?.('file_source', file), { disabled: !onPick })}
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
                    onClick={showMore}
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
