/**
 * EditorSidebar.jsx — Sidebar completa per editor (progetti + file)
 * Layout: sezione progetti + sezione file + footer utente in fondo
 */
import './SourcesSidebar.css';
import React from 'react';
import ProjectsSidebar from './ProjectsSidebar';
import { ResourceError } from '../common/ListState';
import FilesSidebar from './FilesSidebar';
import { useAuth } from '../../context/AuthContext';
import { confirmLeave } from '../../hooks/useUnsavedChangesGuard';
import { useConfirmOrDeny } from '../../context/ConfirmContext';
import { useNavigate } from 'react-router-dom';

function EditorSidebar({
  projects = [],
  files = [],
  onDragStart,
  projectsConfig = {},
  filesConfig = {},
  variant = 'manipola',
  showProjects = true,
  showFiles = true,
  className = '',
  // Fase 5 T2 (R4.8): sorgenti in caricamento o non caricate. Senza questo
  // la sidebar diceva "Nessun progetto disponibile" anche per un errore.
  sourcesLoading = false,
  sourcesError = null,
  onRetrySources = null,
}) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const confirm = useConfirmOrDeny();

  const accentColors = {
    manipola:   { project: '#9B6DFF', file: '#4B8BFF' },
    arricchisci:{ project: '#F5A623', file: '#4B8BFF' },
    estrai:     { project: '#34C17A', file: '#4B8BFF' },
  };
  const colors = accentColors[variant] || accentColors.manipola;

  return (
    <div className={`editor-sidebar editor-sidebar--${variant} ${className}`}>

      {/* Body scrollabile */}
      <div className="editor-sidebar__body">
        {sourcesLoading && <div className="list-state__loading" role="status">Caricamento sorgenti…</div>}
        {sourcesError && (
          <ResourceError compact title="Sorgenti non caricate" message={sourcesError} onRetry={onRetrySources} />
        )}
        {showProjects && (
          <ProjectsSidebar
            projects={projects}
            onDragStart={onDragStart}
            accentColor={colors.project}
            {...projectsConfig}
          />
        )}
        {showFiles && (
          <FilesSidebar
            files={files}
            onDragStart={onDragStart}
            accentColor={colors.file}
            uploadLink="/files"
            {...filesConfig}
          />
        )}
      </div>

      {/* User footer — fuori dal body, sempre in fondo */}
      <div className="editor-sidebar__user">
        <div className="editor-sidebar__user-info">
          <div className="editor-sidebar__user-name">{user?.username}</div>
          <div className="editor-sidebar__user-email">{user?.email}</div>
        </div>
        <button
          type="button"
          className="editor-sidebar__logout"
          onClick={async () => { if (await confirmLeave(confirm)) { logout(); navigate('/login'); } }}
          title="Esci"
        >⏻</button>
      </div>

    </div>
  );
}

export default EditorSidebar;
