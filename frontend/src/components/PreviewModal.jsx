import React, { useState, useEffect, memo, useMemo } from 'react';
import { getColumnTypeIcon } from '../utils/columnUtils';
import '../styles/PreviewModal.css';

/**
 * PreviewModal - Modal per anteprima dati
 * Utilizzabile sia per progetti ESTRAI che MANIPOLA
 * 
 * Props:
 * - isOpen: boolean - controlla visibilità
 * - onClose: function - callback chiusura
 * - onRefresh: function(limit) - callback per ricaricare con nuovo limit
 * - data: object - dati preview { columns, rows, total_count, preview_count, truncated }
 * - loading: boolean - stato caricamento
 * - error: string - eventuale errore
 * - title: string - titolo modal (opzionale)
 * - projectType: 'estrai' | 'manipola' - tipo progetto per styling
 * - initialLimit: number - limite iniziale righe (default 100)
 */
const PreviewModal = memo(({ 
  isOpen, 
  onClose, 
  onRefresh,
  data, 
  loading = false, 
  error = null,
  title = 'Anteprima Dati',
  projectType = 'estrai',
  initialLimit = 100
}) => {
  const [currentPage, setCurrentPage] = useState(1);
  const [sortColumn, setSortColumn] = useState(null);
  const [sortDirection, setSortDirection] = useState('asc');
  const [searchTerm, setSearchTerm] = useState('');
  const [columnFilters, setColumnFilters] = useState({});
  const [selectedLimit, setSelectedLimit] = useState(initialLimit);
  
  const rowsPerPage = 20;
  const limitOptions = [10, 50, 100, 250, 500, 1000];

  // Estrai dati (prima degli hooks che dipendono da essi)
  const columns = data?.columns || [];
  const rows = data?.rows || [];
  const totalCount = data?.total_count || 0;
  const previewCount = data?.preview_count || rows.length;
  const truncated = data?.truncated || false;

  // Reset state quando cambia data
  useEffect(() => {
    setCurrentPage(1);
    setSortColumn(null);
    setSortDirection('asc');
    setSearchTerm('');
    setColumnFilters({});
  }, [data]);

  // Filtra righe per ricerca globale
  const filteredRows = useMemo(() => {
    if (!searchTerm.trim()) return rows;
    
    const term = searchTerm.toLowerCase();
    return rows.filter(row => 
      Object.values(row).some(val => 
        String(val).toLowerCase().includes(term)
      )
    );
  }, [rows, searchTerm]);

  // Ordina righe
  const sortedRows = useMemo(() => {
    if (!sortColumn) return filteredRows;
    
    return [...filteredRows].sort((a, b) => {
      const aVal = a[sortColumn];
      const bVal = b[sortColumn];
      
      // Gestione null/undefined
      if (aVal == null && bVal == null) return 0;
      if (aVal == null) return sortDirection === 'asc' ? 1 : -1;
      if (bVal == null) return sortDirection === 'asc' ? -1 : 1;
      
      // Confronto
      if (typeof aVal === 'number' && typeof bVal === 'number') {
        return sortDirection === 'asc' ? aVal - bVal : bVal - aVal;
      }
      
      const comparison = String(aVal).localeCompare(String(bVal));
      return sortDirection === 'asc' ? comparison : -comparison;
    });
  }, [filteredRows, sortColumn, sortDirection]);

  // ✅ Early return DOPO tutti gli hooks
  if (!isOpen) return null;

  // Paginazione
  const totalPages = Math.ceil(sortedRows.length / rowsPerPage);
  const paginatedRows = sortedRows.slice(
    (currentPage - 1) * rowsPerPage,
    currentPage * rowsPerPage
  );

  // Handlers
  const handleSort = (columnName) => {
    if (sortColumn === columnName) {
      setSortDirection(d => d === 'asc' ? 'desc' : 'asc');
    } else {
      setSortColumn(columnName);
      setSortDirection('asc');
    }
  };

  const handlePageChange = (page) => {
    setCurrentPage(Math.max(1, Math.min(page, totalPages)));
  };

  const handleOverlayClick = (e) => {
    if (e.target === e.currentTarget) {
      onClose();
    }
  };

  // Formatta valore cella
  const formatCellValue = (value, type) => {
    if (value === null || value === undefined) {
      return <span className="preview-modal__null">NULL</span>;
    }
    
    if (type?.startsWith('geometry')) {
      // Tronca geometrie lunghe
      const str = String(value);
      if (str.length > 50) {
        return <span className="preview-modal__geom" title={str}>{str.substring(0, 47)}...</span>;
      }
      return <span className="preview-modal__geom">{str}</span>;
    }
    
    if (type === 'bool') {
      return value ? '✓' : '✗';
    }
    
    if (type === 'date' || type === 'datetime') {
      return <span className="preview-modal__date">{String(value)}</span>;
    }
    
    // Numeri
    if (type === 'int' || type === 'real') {
      return <span className="preview-modal__number">{value}</span>;
    }
    
    // Stringhe lunghe
    const str = String(value);
    if (str.length > 100) {
      return <span title={str}>{str.substring(0, 97)}...</span>;
    }
    
    return str;
  };

  // Stile variante
  const variantClass = projectType === 'manipola' ? 'preview-modal--manipola' : 'preview-modal--estrai';

  return (
    <div className="preview-modal__overlay" onClick={handleOverlayClick}>
      <div className={`preview-modal ${variantClass}`}>
        {/* Header */}
        <div className="preview-modal__header">
          <div className="preview-modal__title-area">
            <h2 className="preview-modal__title">
              👁️ {title}
            </h2>
            {!loading && !error && (
              <span className="preview-modal__stats">
                {truncated ? (
                  <>Mostrando {previewCount.toLocaleString()} di {totalCount.toLocaleString()} righe</>
                ) : (
                  <>{rows.length.toLocaleString()} righe totali</>
                )}
                {' • '}{columns.length} colonne
              </span>
            )}
          </div>
          <button className="preview-modal__close" onClick={onClose}>×</button>
        </div>

        {/* Content */}
        <div className="preview-modal__content">
          {/* Loading */}
          {loading && (
            <div className="preview-modal__loading">
              <div className="preview-modal__spinner">⏳</div>
              <p>Caricamento anteprima...</p>
            </div>
          )}

          {/* Error */}
          {error && !loading && (
            <div className="preview-modal__error">
              <div className="preview-modal__error-icon">❌</div>
              <p className="preview-modal__error-title">Errore caricamento anteprima</p>
              <p className="preview-modal__error-message">{error}</p>
            </div>
          )}

          {/* Data */}
          {!loading && !error && rows.length > 0 && (
            <>
              {/* Toolbar */}
              <div className="preview-modal__toolbar">
                <div className="preview-modal__search">
                  <input
                    type="text"
                    placeholder="🔍 Cerca nei dati..."
                    value={searchTerm}
                    onChange={(e) => {
                      setSearchTerm(e.target.value);
                      setCurrentPage(1);
                    }}
                    className="preview-modal__search-input"
                  />
                  {searchTerm && (
                    <button 
                      className="preview-modal__search-clear"
                      onClick={() => setSearchTerm('')}
                    >
                      ×
                    </button>
                  )}
                </div>
                
                <div className="preview-modal__toolbar-right">
                  {/* Limit selector */}
                  <div className="preview-modal__limit-selector">
                    <label>Righe:</label>
                    <select
                      value={selectedLimit}
                      onChange={(e) => setSelectedLimit(Number(e.target.value))}
                      className="preview-modal__limit-select"
                    >
                      {limitOptions.map(opt => (
                        <option key={opt} value={opt}>{opt}</option>
                      ))}
                    </select>
                    {onRefresh && selectedLimit !== (data?.preview_count || initialLimit) && (
                      <button
                        className="preview-modal__refresh-btn"
                        onClick={() => onRefresh(selectedLimit)}
                        title="Ricarica con nuovo limite"
                      >
                        🔄
                      </button>
                    )}
                  </div>
                  
                  {searchTerm && (
                    <span className="preview-modal__filter-info">
                      Filtrate: {sortedRows.length} righe
                    </span>
                  )}
                </div>
              </div>

              {/* Table */}
              <div className="preview-modal__table-container">
                <table className="preview-modal__table">
                  <thead>
                    <tr>
                      <th className="preview-modal__row-num">#</th>
                      {columns.map((col, idx) => (
                        <th 
                          key={idx}
                          className={`preview-modal__th ${sortColumn === col.name ? 'preview-modal__th--sorted' : ''}`}
                          onClick={() => handleSort(col.name)}
                        >
                          <div className="preview-modal__th-content">
                            <span className="preview-modal__th-icon">
                              {getColumnTypeIcon(col.type)}
                            </span>
                            <span className="preview-modal__th-name">{col.name}</span>
                            {sortColumn === col.name && (
                              <span className="preview-modal__sort-indicator">
                                {sortDirection === 'asc' ? '↑' : '↓'}
                              </span>
                            )}
                          </div>
                          <div className="preview-modal__th-type">{col.type || 'str'}</div>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {paginatedRows.map((row, rowIdx) => (
                      <tr key={rowIdx} className="preview-modal__row">
                        <td className="preview-modal__row-num">
                          {(currentPage - 1) * rowsPerPage + rowIdx + 1}
                        </td>
                        {columns.map((col, colIdx) => (
                          <td key={colIdx} className="preview-modal__cell">
                            {formatCellValue(row[col.name], col.type)}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Pagination */}
              {totalPages > 1 && (
                <div className="preview-modal__pagination">
                  <button
                    className="preview-modal__page-btn"
                    onClick={() => handlePageChange(1)}
                    disabled={currentPage === 1}
                  >
                    ««
                  </button>
                  <button
                    className="preview-modal__page-btn"
                    onClick={() => handlePageChange(currentPage - 1)}
                    disabled={currentPage === 1}
                  >
                    «
                  </button>
                  
                  <span className="preview-modal__page-info">
                    Pagina {currentPage} di {totalPages}
                  </span>
                  
                  <button
                    className="preview-modal__page-btn"
                    onClick={() => handlePageChange(currentPage + 1)}
                    disabled={currentPage === totalPages}
                  >
                    »
                  </button>
                  <button
                    className="preview-modal__page-btn"
                    onClick={() => handlePageChange(totalPages)}
                    disabled={currentPage === totalPages}
                  >
                    »»
                  </button>
                </div>
              )}
            </>
          )}

          {/* Empty state */}
          {!loading && !error && rows.length === 0 && (
            <div className="preview-modal__empty">
              <div className="preview-modal__empty-icon">📭</div>
              <p>Nessun dato da visualizzare</p>
              <p className="preview-modal__empty-hint">
                La query non ha restituito risultati
              </p>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="preview-modal__footer">
          {truncated && (
            <span className="preview-modal__truncated-warning">
              ⚠️ Anteprima limitata a {previewCount.toLocaleString()} righe. 
              Esegui per ottenere tutti i {totalCount.toLocaleString()} risultati.
            </span>
          )}
          <button className="preview-modal__btn-close" onClick={onClose}>
            Chiudi
          </button>
        </div>
      </div>
    </div>
  );
});

PreviewModal.displayName = 'PreviewModal';

export default PreviewModal;