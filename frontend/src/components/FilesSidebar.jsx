import React, { useState, useMemo } from 'react';
import '../styles/SourcesSidebar.css';

/**
 * FilesSidebar - Lista file esterni disponibili per drag & drop
 * 
 * Props:
 * - files: Array - lista file [{id, name, format, row_count, ...}]
 * - onDragStart: function(event, nodeType, data) - callback drag start
 * - title: string - titolo sezione
 * - subtitle: string - sottotitolo
 * - emptyMessage: string - messaggio se lista vuota
 * - uploadLink: string - link per caricare file (default: '/files')
 * - accentColor: string - colore accent
 * - showSearch: boolean - mostra barra ricerca
 * - showRowCount: boolean - mostra conteggio righe
 */
function FilesSidebar({
  files = [],
  onDragStart,
  title = "📁 File Esterni",
  subtitle = "Trascina un file nel canvas",
  emptyMessage = "Nessun file disponibile.",
  uploadLink = '/files',
  uploadLinkText = 'Carica file esterni',
  accentColor = '#3F51B5',
  showSearch = false,
  showRowCount = true,
  className = ''
}) {
  const [searchQuery, setSearchQuery] = useState('');

  // Filtra file per ricerca
  const filteredFiles = useMemo(() => {
    if (!searchQuery.trim()) return files;
    
    const query = searchQuery.toLowerCase();
    return files.filter(f => 
      f.name.toLowerCase().includes(query) ||
      f.format?.toLowerCase().includes(query)
    );
  }, [files, searchQuery]);

  // Icona per formato file
  const getFileIcon = (format) => {
    switch (format?.toLowerCase()) {
      case 'csv': return '📄';
      case 'xlsx':
      case 'xls': return '📊';
      case 'json': return '📋';
      case 'geojson':
      case 'gpkg':
      case 'shp': return '🗺️';
      case 'gdb': return '🗃️';
      default: return '📁';
    }
  };

  // Colore badge per formato
  const getBadgeStyle = (format) => {
    const styles = {
      'csv': { background: '#4CAF50', color: 'white' },
      'xlsx': { background: '#2E7D32', color: 'white' },
      'xls': { background: '#2E7D32', color: 'white' },
      'json': { background: '#FF9800', color: 'white' },
      'geojson': { background: '#2196F3', color: 'white' },
      'gpkg': { background: '#9C27B0', color: 'white' },
      'shp': { background: '#E91E63', color: 'white' },
      'gdb': { background: '#673AB7', color: 'white' }
    };
    return styles[format?.toLowerCase()] || { background: '#666', color: 'white' };
  };

  const handleDragStart = (event, file) => {
    if (onDragStart) {
      onDragStart(event, 'file_source', {
        id: file.id,
        file_source_id: file.id,
        name: file.name,
        format: file.format
      });
    }
  };

  return (
    <div className={`sources-sidebar sources-sidebar--files ${className}`}>
      {/* Header */}
      <div className="sources-sidebar__header">
        <h3 className="sources-sidebar__title">{title}</h3>
        <p className="sources-sidebar__subtitle">{subtitle}</p>
      </div>

      {/* Search (opzionale) */}
      {showSearch && (
        <div className="sources-sidebar__search">
          <input
            type="text"
            placeholder="🔍 Cerca file..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="sources-sidebar__search-input"
          />
          {searchQuery && (
            <button 
              className="sources-sidebar__search-clear"
              onClick={() => setSearchQuery('')}
            >
              ×
            </button>
          )}
        </div>
      )}

      {/* Lista file */}
      <div className="sources-sidebar__list">
        {filteredFiles.length === 0 ? (
          <div className="sources-sidebar__empty">
            {emptyMessage}
            {uploadLink && (
              <>
                <br />
                <a 
                  href={uploadLink} 
                  className="sources-sidebar__empty-link"
                  style={{ color: accentColor }}
                >
                  {uploadLinkText}
                </a>
              </>
            )}
          </div>
        ) : (
          filteredFiles.map((file) => (
            <div
              key={file.id}
              className="sources-sidebar__item sources-sidebar__item--file"
              draggable
              onDragStart={(e) => handleDragStart(e, file)}
              style={{ '--accent-color': accentColor }}
            >
              <span className="sources-sidebar__item-icon">
                {getFileIcon(file.format)}
              </span>
              <div className="sources-sidebar__item-info">
                <span className="sources-sidebar__item-name">
                  {file.name}
                </span>
                {showRowCount && file.row_count && (
                  <span className="sources-sidebar__item-meta">
                    {file.row_count.toLocaleString()} righe
                  </span>
                )}
              </div>
              <span 
                className="sources-sidebar__item-badge"
                style={getBadgeStyle(file.format)}
              >
                {file.format?.toUpperCase()}
              </span>
            </div>
          ))
        )}
      </div>

      {/* Count */}
      {filteredFiles.length > 0 && (
        <div className="sources-sidebar__count">
          {filteredFiles.length} file
        </div>
      )}
    </div>
  );
}

export default FilesSidebar;
