/**
 * PreviewTable.jsx — Tabella paginata con sort, search e raggruppamento colonne
 *
 * Estratto da PreviewModal.jsx (1366 L → split Fase 1 FE).
 * Componente presentational puro: tutto lo stato vive nel parent (PreviewModal).
 * Punto di innesto futuro per virtualizzazione react-window (Batch 1C).
 *
 * Contract:
 *  - onSearchChange(value) responsabilita' parent: reset currentPage -> 1
 *  - onSort(colName) responsabilita' parent: toggle asc/desc, non resetta page
 *  - onPageChange(page) responsabilita' parent: clamp 1..totalPages
 *  - activeTab orchestration: esterno a questo componente
 */
import React, { useMemo } from 'react';
import { getColumnTypeIcon } from '../../utils/columnUtils';
import './PreviewModal.css';

/* ─── Grouped header: inferisce gruppi sorgente dal prefisso colonna ── */

/** Palette colori per gruppo sorgente */
const GROUP_PALETTE = [
  { color: '#4B8BFF', bg: 'rgba(75,139,255,0.08)', border: 'rgba(75,139,255,0.25)' },
  { color: '#34C17A', bg: 'rgba(52,193,122,0.08)', border: 'rgba(52,193,122,0.25)' },
  { color: '#F5A623', bg: 'rgba(245,166,35,0.08)',  border: 'rgba(245,166,35,0.25)'  },
  { color: '#9B6DFF', bg: 'rgba(155,109,255,0.08)', border: 'rgba(155,109,255,0.25)' },
  { color: '#F87171', bg: 'rgba(248,113,113,0.08)', border: 'rgba(248,113,113,0.25)' },
  { color: '#2DD4BF', bg: 'rgba(45,212,191,0.08)',  border: 'rgba(45,212,191,0.25)'  },
];

/**
 * Dato un array di colonne {name, type}, rileva i gruppi per prefisso.
 * Ritorna null se non ci sono almeno 2 gruppi distinti (nessun raggruppamento utile).
 *
 * Esempio: ["PCSL_BL_ID", "PC_CD_ID", "SU_NAME"]
 * → { PCSL: { color, cols: [...] }, PC: { ... }, SU: { ... } }
 */
const inferColumnGroups = (columns) => {
  // Se almeno una colonna ha source esplicito (iniettato da EstraiEditor),
  // usa quello per il raggruppamento invece di indovinare dal prefisso del nome.
  const hasExplicitSource = columns.some(col => col.source);

  const prefixMap = {};
  columns.forEach(col => {
    let prefix;
    if (hasExplicitSource) {
      prefix = col.source ? col.source.toUpperCase() : '__none__';
    } else {
      const match = col.name.match(/^([A-Za-z][A-Za-z0-9]+)_/i);
      prefix = match ? match[1].toUpperCase() : '__none__';
    }
    if (!prefixMap[prefix]) prefixMap[prefix] = [];
    prefixMap[prefix].push(col);
  });

  const groups = Object.keys(prefixMap);
  if (groups.length < 2 || (groups.length === 2 && prefixMap['__none__'])) return null;

  let paletteIdx = 0;
  const result = {};
  groups.forEach(prefix => {
    const isNone = prefix === '__none__';
    result[prefix] = {
      label: isNone ? '—' : prefix,
      cols: prefixMap[prefix],
      ...(isNone
        ? { color: '#718096', bg: 'transparent', border: 'rgba(113,128,150,0.2)' }
        : GROUP_PALETTE[paletteIdx++ % GROUP_PALETTE.length]),
    };
  });
  return result;
};

const getColPrefix = (col) => {
  if (col.source) return col.source.toUpperCase();
  const match = col.name.match(/^([A-Za-z][A-Za-z0-9]+)_/i);
  return match ? match[1].toUpperCase() : '__none__';
};

const buildGroupSpans = (columns, groups) => {
  const spans = [];
  let i = 0;
  while (i < columns.length) {
    const col = columns[i];
    const prefix = getColPrefix(col);
    let span = 1;
    while (
      i + span < columns.length &&
      getColPrefix(columns[i + span]) === prefix
    ) span++;
    spans.push({ prefix, group: groups[prefix], span });
    i += span;
  }
  return spans;
};

/* ─── COMPONENT ───────────────────────────────────────────────────── */

const PreviewTable = ({
  columns,
  sortedRows,
  sortColumn,
  sortDirection,
  searchTerm,
  selectedLimit,
  currentPage,
  rowsPerPage,
  totalPages,
  collapsedGroups,
  formatCellValue,
  disableGrouping,
  data,
  initialLimit,
  limitOptions,
  onSort,
  onSearchChange,
  onLimitChange,
  onPageChange,
  onToggleGroup,
  onRefresh,
}) => {
  const columnGroups = useMemo(
    () => disableGrouping ? null : inferColumnGroups(columns),
    [columns, disableGrouping]
  );

  const visibleColumns = useMemo(() => {
    if (!columnGroups) return columns;
    return columns.filter(col => {
      const prefix = getColPrefix(col);
      return !collapsedGroups[prefix];
    });
  }, [columns, columnGroups, collapsedGroups]);

  const groupSpans = useMemo(() => {
    if (!columnGroups) return null;
    return buildGroupSpans(visibleColumns, columnGroups);
  }, [visibleColumns, columnGroups]);

  const paginatedRows = useMemo(() => sortedRows.slice(
    (currentPage - 1) * rowsPerPage,
    currentPage * rowsPerPage
  ), [sortedRows, currentPage, rowsPerPage]);

  return (
    <>
      {/* toolbar */}
      <div className="preview-modal__toolbar">
        <div className="preview-modal__search">
          <input
            type="text"
            placeholder="⌕  Cerca nei dati…"
            value={searchTerm}
            onChange={e => onSearchChange(e.target.value)}
            className="preview-modal__search-input"
          />
          {searchTerm && (
            <button type="button" className="preview-modal__search-clear" onClick={() => onSearchChange('')}>
              ×
            </button>
          )}
        </div>

        <div className="preview-modal__toolbar-right">
          <div className="preview-modal__limit-selector">
            <label>Righe:</label>
            <select
              value={selectedLimit}
              onChange={e => onLimitChange(Number(e.target.value))}
              className="preview-modal__limit-select"
            >
              {limitOptions.map(opt => <option key={opt} value={opt}>{opt}</option>)}
            </select>
            {onRefresh && selectedLimit !== (data?.preview_count || initialLimit) && (
              <button type="button" className="preview-modal__refresh-btn"
                onClick={() => onRefresh(selectedLimit)} title="Ricarica con nuovo limite">
                🔄
              </button>
            )}
          </div>
          {searchTerm && (
            <span className="preview-modal__filter-info">
              {sortedRows.length} risultati
            </span>
          )}
        </div>
      </div>

      {/* table */}
      <div className="preview-modal__table-container">
        <table className="preview-modal__table">
          <thead>
            {/* ── RIGA GRUPPI SORGENTE (solo se rilevati) ── */}
            {columnGroups && groupSpans && (
              <tr>
                <th className="preview-modal__row-num pm-group-th pm-group-th--none" />
                {groupSpans.map(({ prefix, group, span }, idx) => (
                  <th
                    key={`g${prefix}_${idx}`}
                    colSpan={span}
                    onClick={() => prefix !== '__none__' && onToggleGroup(prefix)}
                    className={`pm-group-th${prefix === '__none__' ? ' pm-group-th--none' : ''}`}
                    style={{
                      background: group?.bg || 'transparent',
                      borderBottom: `2px solid ${group?.color || 'var(--plen-border, rgba(255,255,255,0.06))'}`,
                      borderLeft: `1px solid ${group?.border || 'transparent'}`,
                      borderRight: `1px solid ${group?.border || 'transparent'}`,
                    }}
                  >
                    {group && prefix !== '__none__' && (
                      <div className="pm-group-th-inner">
                        <div className="pm-group-dot" style={{ background: group.color }} />
                        <span className="pm-group-label" style={{ color: group.color }}>{group.label}</span>
                        <span className="pm-group-meta">
                          {collapsedGroups[prefix]
                            ? `▶ ${columnGroups[prefix].cols.length} col.`
                            : `▼ ${columnGroups[prefix].cols.length}`}
                        </span>
                      </div>
                    )}
                  </th>
                ))}
              </tr>
            )}
            <tr>
              <th className="preview-modal__row-num">#</th>
              {visibleColumns.map((col) => {
                const prefix = getColPrefix(col);
                const group = columnGroups?.[prefix];
                const isGeo = typeof col.type === 'string' && (
                  col.type.startsWith('geometry') || col.type.startsWith('geography') ||
                  col.type === 'geom' || col.type === 'geojson' || col.type === 'geo'
                );
                return (
                  <th
                    key={col.name}
                    className={`preview-modal__th${sortColumn === col.name ? ' preview-modal__th--sorted' : ''}${isGeo ? ' preview-modal__th--geo' : ''}`}
                    onClick={() => onSort(col.name)}
                    style={group && prefix !== '__none__' ? {
                      borderLeft: `1px solid ${group.border}`,
                      borderRight: `1px solid ${group.border}`,
                    } : undefined}
                  >
                    <div className="preview-modal__th-content">
                      <span className="preview-modal__th-icon">{getColumnTypeIcon(col.type)}</span>
                      <span className="preview-modal__th-name">{col.name}</span>
                      {sortColumn === col.name && (
                        <span className="preview-modal__sort-indicator">
                          {sortDirection === 'asc' ? '↑' : '↓'}
                        </span>
                      )}
                    </div>
                    <div className="preview-modal__th-type">{col.type || 'str'}</div>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {paginatedRows.map((row, rowIdx) => (
              <tr key={`r${(currentPage - 1) * rowsPerPage + rowIdx}`} className="preview-modal__row">
                <td className="preview-modal__row-num">
                  {(currentPage - 1) * rowsPerPage + rowIdx + 1}
                </td>
                {visibleColumns.map((col) => (
                  <td key={col.name} className="preview-modal__cell">
                    {formatCellValue(row[col.name], col.type)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* pagination */}
      {totalPages > 1 && (
        <div className="preview-modal__pagination">
          <button type="button" className="preview-modal__page-btn"
            onClick={() => onPageChange(1)} disabled={currentPage === 1}>««</button>
          <button type="button" className="preview-modal__page-btn"
            onClick={() => onPageChange(currentPage - 1)} disabled={currentPage === 1}>«</button>
          <span className="preview-modal__page-info">
            {currentPage} / {totalPages}
          </span>
          <button type="button" className="preview-modal__page-btn"
            onClick={() => onPageChange(currentPage + 1)} disabled={currentPage === totalPages}>»</button>
          <button type="button" className="preview-modal__page-btn"
            onClick={() => onPageChange(totalPages)} disabled={currentPage === totalPages}>»»</button>
        </div>
      )}
    </>
  );
};

PreviewTable.displayName = 'PreviewTable';
export default PreviewTable;
