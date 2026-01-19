import React from 'react';
import ProjectsSidebar from './ProjectsSidebar';
import FilesSidebar from './FilesSidebar';
import QuickActions from './QuickActions';
import '../styles/SourcesSidebar.css'

/**
 * EditorSidebar - Sidebar completa per editor (combina progetti, file e azioni)
 * 
 * Props:
 * - projects: Array - lista progetti
 * - files: Array - lista file
 * - onDragStart: function - callback drag start
 * - quickActions: Array - lista azioni rapide
 * - projectsConfig: Object - config per ProjectsSidebar
 * - filesConfig: Object - config per FilesSidebar
 * - variant: 'manipola' | 'arricchisci' - variante colore
 * - showProjects: boolean - mostra sezione progetti (default: true)
 * - showFiles: boolean - mostra sezione file (default: true)
 * - showQuickActions: boolean - mostra azioni rapide (default: true)
 */
function EditorSidebar({
  projects = [],
  files = [],
  onDragStart,
  quickActions = [],
  projectsConfig = {},
  filesConfig = {},
  variant = 'manipola',
  showProjects = true,
  showFiles = true,
  showQuickActions = true,
  className = ''
}) {
  // Colori per variante
  const colors = {
    manipola: {
      accent: '#9C27B0',
      fileAccent: '#3F51B5'
    },
    arricchisci: {
      accent: '#FF6B35',
      fileAccent: '#3F51B5'
    },
    estrai: {
      accent: '#2196F3',
      fileAccent: '#4CAF50'
    }
  };

  const variantColors = colors[variant] || colors.manipola;

  return (
    <div className={`editor-sidebar editor-sidebar--${variant} ${className}`}>
      {/* Progetti */}
      {showProjects && (
        <ProjectsSidebar
          projects={projects}
          onDragStart={onDragStart}
          accentColor={variantColors.accent}
          {...projectsConfig}
        />
      )}

      {/* File */}
      {showFiles && (
        <FilesSidebar
          files={files}
          onDragStart={onDragStart}
          accentColor={variantColors.fileAccent}
          uploadLink="/files"
          {...filesConfig}
        />
      )}

      {/* Azioni Rapide */}
      {showQuickActions && quickActions.length > 0 && (
        <QuickActions
          actions={quickActions}
          accentColor={variantColors.accent}
        />
      )}
    </div>
  );
}

export default EditorSidebar;
