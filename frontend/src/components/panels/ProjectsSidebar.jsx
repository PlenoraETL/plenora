/**
 * ProjectsSidebar.jsx — Sidebar lista progetti per editor
 * Paginazione 10 + "Mostra altri" come EstraiEditor
 */
import './SourcesSidebar.css';
import React, { useState, useMemo } from 'react';
import SearchField, { SearchEmpty } from '../common/SearchField';
import { useSearch, useSearchPage } from '../../hooks/useSearch';

const PAGE_SIZE = 10;

function ProjectsSidebar({
  projects = [],
  onDragStart,
  title = "📊 Sorgenti Progetto",
  subtitle = "Trascina un progetto nel canvas",
  emptyMessage = "Nessun progetto disponibile.",
  emptyHint = "Crea prima un progetto di tipo Estrai.",
  filterTypes = ['estrai'],
  accentColor = '#9B6DFF',
  showSearch = false,
  className = ''
}) {
  const search = useSearch();
  const [collapsed, setCollapsed] = useState(false);
  // Paginazione legata alla ricerca: una ricerca nuova riparte da PAGE_SIZE.
  const [visibleCount, setVisibleCount] = useSearchPage(search, PAGE_SIZE);
  const showMore = () => setVisibleCount(c => c + PAGE_SIZE);

  const typedProjects = useMemo(() => (filterTypes?.length > 0
    ? projects.filter(p => filterTypes.includes(p.project_type || 'estrai'))
    : projects), [projects, filterTypes]);
  const filteredProjects = useMemo(() => search.filter(typedProjects, p => [p.name]), [typedProjects, search.filter]);

  const getProjectIcon = (t) => ({ manipola:'🔄', arricchisci:'🔗' }[t] || '📊');
  const getProjectBadge = (t) => ({ manipola:'MANIPOLA', arricchisci:'ARRICCHISCI' }[t] || 'SQL');

  const handleDragStart = (event, project) => {
    if (onDragStart) onDragStart(event, 'project_source', {
      id: project.id, source_project_id: project.id,
      name: project.name, project_type: project.project_type
    });
  };

  const remaining = filteredProjects.length - visibleCount;
  const nextChunk = Math.min(PAGE_SIZE, remaining);

  return (
    <div className={`sources-sidebar ${className}`} style={{ '--accent-color': accentColor }}>

      {/* Section header collassabile */}
      <div
        className="ss-sec-hdr"
        role="button" tabIndex={0}
        onClick={() => setCollapsed(c => !c)}
        onKeyDown={e => (e.key==='Enter'||e.key===' ') && setCollapsed(c=>!c)}
      >
        <span className={`ss-sec-hdr__arrow${collapsed ? '' : ' open'}`}>▶</span>
        <span className="ss-sec-hdr__label">{title}</span>
        <span className="ss-sec-hdr__count">{filteredProjects.length}</span>
      </div>

      {!collapsed && (
        <>
          {/* Search */}
          {showSearch && (
            <SearchField
              search={search}
              label="Cerca progetto"
              className="sources-sidebar__search"
              compact
              shown={filteredProjects.length}
              total={typedProjects.length}
            />
          )}

          {/* Lista */}
          <div className="sources-sidebar__list">
            {filteredProjects.length === 0 && search.active ? (
              <SearchEmpty search={search} shown={0} />
            ) : filteredProjects.length === 0 ? (
              <div className="sources-sidebar__empty">
                {emptyMessage}
                {emptyHint && <><br /><span className="sources-sidebar__empty-hint">{emptyHint}</span></>}
              </div>
            ) : (
              <>
                {filteredProjects.slice(0, visibleCount).map(project => (
                  <div
                    key={project.id}
                    className="sources-sidebar__item"
                    draggable
                    onDragStart={e => handleDragStart(e, project)}
                  >
                    <span className="sources-sidebar__item-icon">{getProjectIcon(project.project_type)}</span>
                    <span className="sources-sidebar__item-name">{project.name}</span>
                    <span className={`sources-sidebar__item-badge sources-sidebar__item-badge--${project.project_type || 'estrai'}`}>
                      {getProjectBadge(project.project_type)}
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

export default ProjectsSidebar;
