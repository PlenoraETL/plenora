/**
 * ProjectSourceNode.jsx — Nodo canvas per sorgente progetto ESTRAI/MANIPOLA
 *
 * Permette di usare l'output di un progetto esistente come input
 * di una pipeline Manipola o Arricchisci. Carica dinamicamente
 * lo schema colonne dal progetto collegato.
 *
 * Usato in: ManipolaEditor, ArricchisciEditor
 */
import React, { useState, useEffect, memo } from 'react';
import { Handle, Position } from 'reactflow';
import { getProject } from '../../api';
import { getColumnTypeIcon, getColumnTypeLabel, isGeometryType, getColumnSRID, normalizeColumnType } from '../../utils/columnUtils';
import useCanvasStore from '../../store/useCanvasStore';
import './ProjectSourceNode.css';

/**
 * ProjectSourceNode - Nodo sorgente per progetti SQL (tipo "estrai") o Manipola
 * 
 * Restyled per uniformarsi al TableNode di ESTRAI:
 * - Sfondo bianco con header verde
 * - Struttura colonne identica (checkbox, icona, nome, badge tipo)
 * - Expand/collapse per molte colonne
 */
const ProjectSourceNode = ({ id, data }) => {
  const [selectedProject, setSelectedProject] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [isExpanded, setIsExpanded] = useState(false);

  // Carica dettagli progetto quando cambia source_project_id
  useEffect(() => {
    if (data.source_project_id) {
      loadProjectDetails(data.source_project_id);
    }
  }, [data.source_project_id]);


  const loadProjectDetails = async (projectId) => {
    if (!projectId) return;
    
    setLoading(true);
    setError(null);
    
    try {
      const projectData = await getProject(projectId);
      
      // Estrai nome file output dal nodo result
      let outputFileName = null;
      if (projectData.canvas?.nodes) {
        const resultNode = projectData.canvas.nodes.find(n => n.type === 'result');
        if (resultNode?.fileName && resultNode?.format) {
          outputFileName = `${resultNode.fileName}.${resultNode.format}`;
        }
      }
      if (outputFileName) {
        projectData.output_file = outputFileName;
      }
      
      setSelectedProject(projectData);
      
      // Costruisci colonne con tipi normalizzati
      let columnsWithTypes = [];
      
      if (projectData.columns_metadata && projectData.columns_metadata.length > 0) {
        columnsWithTypes = projectData.columns_metadata.map(col => ({
          name: col.name,
          type: normalizeColumnType(col),
          visible: true
        }));
      } else if (projectData.canvas?.nodes) {
        columnsWithTypes = extractColumnsFromCanvas(projectData.canvas);
      }
      
      // Usa updateNodeData dallo store direttamente — funziona in tutti gli editor
      const { updateNodeData } = useCanvasStore.getState();
      if (columnsWithTypes.length > 0) {
        if (data.columns && data.columns.length > 0) {
          const existingVisibility = {};
          data.columns.forEach(col => {
            existingVisibility[col.name] = col.visible;
          });
          
          columnsWithTypes = columnsWithTypes.map(col => ({
            ...col,
            visible: existingVisibility[col.name] !== undefined 
              ? existingVisibility[col.name] 
              : col.visible
          }));
        }
        
        updateNodeData(id, { columns: columnsWithTypes });
      }
      
    } catch (err) {
      console.error(`[ProjectSourceNode] Errore caricamento progetto ${projectId}:`, err);
      setError(`Errore caricamento: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  const extractColumnsFromCanvas = (canvas) => {
    const nodes = canvas.nodes || [];
    const edges = canvas.edges || [];
    
    const resultNode = nodes.find(n => n.type === 'result');
    
    const extractFromArray = (columns) => {
      return columns.map(col => {
        if (typeof col === 'string') {
          return { name: col, type: 'str', visible: true };
        }
        return {
          name: col.name,
          type: normalizeColumnType(col),
          visible: col.visible !== false
        };
      });
    };
    
    if (resultNode?.data?.columns && resultNode.data.columns.length > 0) {
      return extractFromArray(resultNode.data.columns);
    }
    if (resultNode?.columns && resultNode.columns.length > 0) {
      return extractFromArray(resultNode.columns);
    }
    if (resultNode) {
      const incomingEdge = edges.find(e => e.target === resultNode.id);
      if (incomingEdge) {
        const sourceNode = nodes.find(n => n.id === incomingEdge.source);
        if (sourceNode?.data?.columns && sourceNode.data.columns.length > 0) {
          return extractFromArray(sourceNode.data.columns);
        }
      }
    }
    for (const node of nodes) {
      if (node.data?.columns && node.data.columns.length > 0) {
        return extractFromArray(node.data.columns);
      }
      if (node.columns && node.columns.length > 0) {
        return extractFromArray(node.columns);
      }
    }

    // B-FE-51: fallback per canvas ARRICCHISCI — result node non ha colonne proprie.
    // Ricava colonne da: sorgenti (project_source/file_source) + output_mapping degli api_enrich.
    const seen = new Set();
    const arricchisciCols = [];
    for (const node of nodes) {
      const d = node.data || {};
      if (['project_source', 'file_source', 'staticValues'].includes(node.type)) {
        const srcCols = d.columns || node.columns || [];
        extractFromArray(srcCols).forEach(c => {
          if (!seen.has(c.name)) { arricchisciCols.push(c); seen.add(c.name); }
        });
      }
      if (node.type === 'api_enrich') {
        (d.output_mapping || []).forEach(m => {
          const name = m.column || m.target_column || m.name;
          if (name && !seen.has(name)) {
            arricchisciCols.push({ name, type: m.type || 'str', visible: true });
            seen.add(name);
          }
        });
      }
    }
    if (arricchisciCols.length > 0) return arricchisciCols;

    return [];
  };

  const handleToggleColumn = (colName) => {
    if (!data.columns) return;
    const updatedColumns = data.columns.map(col => 
      col.name === colName ? { ...col, visible: !col.visible } : col
    );
    useCanvasStore.getState().updateNodeData(id, { columns: updatedColumns });
  };

  const handleDelete = () => {
    useCanvasStore.getState().deleteNode(id);
  };

  // Colonne e conteggi
  const columns = data.columns || [];
  const visibleCount = columns.filter(c => c.visible !== false).length;
  const totalCount = columns.length;
  const visibleColumns = isExpanded ? columns : columns.slice(0, 5);
  const hasMoreColumns = columns.length > 5;

  const projectName = selectedProject?.metadata?.name || selectedProject?.name || data.source_project_id?.split('_')[0] || 'Caricamento...';
  const projectType = selectedProject?.metadata?.project_type;

  return (
    <div className="project-source-node">
      {/* Header */}
      <div className="project-source-node__header">
        <div className="project-source-node__header-content">
          <div className="project-source-node__title">
            <span className="project-source-node__title-icon">📥</span>
            {projectName}
          </div>
          {projectType && (
            <span className={`project-source-node__type-badge project-source-node__type-badge--${projectType}`}>
              {projectType}
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={handleDelete}
          className="project-source-node__delete-btn"
          title="Elimina nodo"
        >
          ×
        </button>
      </div>

      {/* Body */}
      <div className="project-source-node__body">
        {/* Loading */}
        {loading && (
          <div className="project-source-node__loading">⏳ Caricamento...</div>
        )}

        {/* Errore */}
        {error && (
          <div className="project-source-node__error">⚠️ {error}</div>
        )}

        {/* Colonne */}
        {columns.length > 0 && !loading && (
          <>
            <div className="project-source-node__columns-header">
              <span>Colonne ({visibleCount}/{totalCount}):</span>
              <span className="project-source-node__columns-hint">click per escludere</span>
            </div>
            
            {visibleColumns.map((col, idx) => {
              const colType = normalizeColumnType(col);
              const isVisible = col.visible !== false;
              return (
                <div
                  key={idx}
                  className={`project-source-node__column ${!isVisible ? 'project-source-node__column--hidden' : ''}`}
                  onClick={() => handleToggleColumn(col.name)}
                >
                  {/* Icona tipo */}
                  <span 
                    className={`project-source-node__column-type-icon ${isGeometryType(colType) ? 'project-source-node__column-type-icon--geo' : ''}`}
                    title={getColumnTypeLabel(colType)}
                  >
                    {getColumnTypeIcon(colType)}
                  </span>
                  
                  {/* Nome */}
                  <span className={`project-source-node__column-name ${!isVisible ? 'project-source-node__column-name--hidden' : ''}`}>
                    {col.name}
                  </span>

                  {/* Badge SRID per geometrie */}
                  {isGeometryType(colType) && getColumnSRID(colType) && (
                    <span className="project-source-node__column-srid" title={`EPSG:${getColumnSRID(colType)}`}>
                      {getColumnSRID(colType)}
                    </span>
                  )}
                  
                  {/* Badge tipo */}
                  <span className={`project-source-node__column-badge project-source-node__column-badge--${
                    colType === 'int' || colType === 'real' ? 'num' :
                    colType === 'date' ? 'date' :
                    colType === 'bool' ? 'bool' :
                    colType.startsWith('geometry') ? 'geo' : 'str'
                  }`}>
                    {colType}
                  </span>
                </div>
              );
            })}

            {/* Expand/collapse */}
            {hasMoreColumns && (
              <button
                type="button"
                onClick={() => setIsExpanded(!isExpanded)}
                className="project-source-node__expand-btn"
              >
                {isExpanded ? '▲ Nascondi' : `▼ Mostra tutte (${columns.length})`}
              </button>
            )}
          </>
        )}

        {/* Nessuna colonna */}
        {columns.length === 0 && !loading && !error && selectedProject && (
          <div className="project-source-node__no-columns">
            ⚠️ Nessuna colonna trovata.<br/>
            Esegui prima il progetto sorgente.
          </div>
        )}

        {/* Info progetto */}
        {selectedProject && !loading && selectedProject.output_file && (
          <div className="project-source-node__info">
            <span className="project-source-node__info-file">📄 {selectedProject.output_file}</span>
          </div>
        )}
      </div>

      {/* Handle output */}
      <Handle 
        type="source" 
        position={Position.Right}
        className="project-source-node__handle"
      />
    </div>
  );
};

export default memo(ProjectSourceNode);