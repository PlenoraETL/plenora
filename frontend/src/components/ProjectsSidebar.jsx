import React, { useState, useMemo } from 'react';
import '../styles/SourcesSidebar.css';

/**
 * ProjectsSidebar - Lista progetti disponibili per drag & drop
 * 
 * Props:
 * - projects: Array - lista progetti [{id, name, project_type, ...}]
 * - onDragStart: function(event, nodeType, data) - callback drag start
 * - title: string - titolo sezione (default: "Sorgenti Progetto")
 * - subtitle: string - sottotitolo
 * - emptyMessage: string - messaggio se lista vuota
 * - filterTypes: Array - tipi progetto da mostrare (default: ['estrai'])
 * - accentColor: string - colore accent (default: '#9C27B0')
 * - showSearch: boolean - mostra barra ricerca (default: false)
 */
function ProjectsSidebar({
  projects = [],
  onDragStart,
  title = "📊 Sorgenti Progetto",
  subtitle = "Trascina un progetto nel canvas",
  emptyMessage = "Nessun progetto disponibile.",
  emptyHint = "Crea prima un progetto di tipo Estrai.",
  filterTypes = ['estrai'],
  accentColor = '#9C27B0',
  showSearch = false,
  className = ''
}) {
  const [searchQuery, setSearchQuery] = useState('');

  // Filtra progetti per tipo
  const filteredProjects = useMemo(() => {
    let result = projects;
    
    // Filtra per tipo
    if (filterTypes && filterTypes.length > 0) {
      result = result.filter(p => 
        filterTypes.includes(p.project_type || 'estrai')
      );
    }
    
    // Filtra per ricerca
    if (searchQuery.trim()) {
      const query = searchQuery.toLowerCase();
      result = result.filter(p => 
        p.name.toLowerCase().includes(query)
      );
    }
    
    return result;
  }, [projects, filterTypes, searchQuery]);

  // Icona per tipo progetto
  const getProjectIcon = (projectType) => {
    switch (projectType) {
      case 'manipola': return '🔄';
      case 'arricchisci': return '🔗';
      default: return '📊';
    }
  };

  // Badge per tipo progetto
  const getProjectBadge = (projectType) => {
    switch (projectType) {
      case 'manipola': return 'MANIPOLA';
      case 'arricchisci': return 'ARRICCHISCI';
      default: return 'SQL';
    }
  };

  const handleDragStart = (event, project) => {
    if (onDragStart) {
      onDragStart(event, 'project_source', {
        id: project.id,
        source_project_id: project.id,
        name: project.name,
        project_type: project.project_type
      });
    }
  };

  return (
    <div className={`sources-sidebar ${className}`}>
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
            placeholder="🔍 Cerca progetto..."
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

      {/* Lista progetti */}
      <div className="sources-sidebar__list">
        {filteredProjects.length === 0 ? (
          <div className="sources-sidebar__empty">
            {emptyMessage}
            {emptyHint && (
              <>
                <br />
                <span className="sources-sidebar__empty-hint">{emptyHint}</span>
              </>
            )}
          </div>
        ) : (
          filteredProjects.map((project) => (
            <div
              key={project.id}
              className="sources-sidebar__item"
              draggable
              onDragStart={(e) => handleDragStart(e, project)}
              style={{ '--accent-color': accentColor }}
            >
              <span className="sources-sidebar__item-icon">
                {getProjectIcon(project.project_type)}
              </span>
              <span className="sources-sidebar__item-name">
                {project.name}
              </span>
              <span 
                className={`sources-sidebar__item-badge sources-sidebar__item-badge--${project.project_type || 'estrai'}`}
              >
                {getProjectBadge(project.project_type)}
              </span>
            </div>
          ))
        )}
      </div>

      {/* Count */}
      {filteredProjects.length > 0 && (
        <div className="sources-sidebar__count">
          {filteredProjects.length} progett{filteredProjects.length === 1 ? 'o' : 'i'}
        </div>
      )}
    </div>
  );
}

export default ProjectsSidebar;
