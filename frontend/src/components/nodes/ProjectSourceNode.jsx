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
import { useResource } from '../../hooks/useResource';
import { Position } from '@xyflow/react';
import { getProject } from '../../api';
import { getColumnTypeIcon, getColumnTypeLabel, isGeometryType, getColumnSRID, normalizeColumnType } from '../../utils/columnUtils';
import { humanizeError } from '../../utils/errorMessages';
import useCanvasStore from '../../store/useCanvasStore';
import './ProjectSourceNode.css';
import NodeHandle from '../canvas/NodeHandle';
import { pressable } from '../../utils/pressable';

/**
 * ProjectSourceNode - Nodo sorgente per progetti SQL (tipo "estrai") o Manipola
 * 
 * Restyled per uniformarsi al TableNode di ESTRAI:
 * - Sfondo bianco con header verde
 * - Struttura colonne identica (checkbox, icona, nome, badge tipo)
 * - Expand/collapse per molte colonne
 */
const ProjectSourceNode = ({ id, data }) => {
  const [isExpanded, setIsExpanded] = useState(false);
  // Fase 5 T2: progetto sorgente dall'hook comune (id di richiesta: il
  // progetto scelto prima non scrive le colonne di quello scelto dopo).
  const resource = useResource(() => getProject(data.source_project_id), {
    key: ['source-project', data.source_project_id || null],
    enabled: Boolean(data.source_project_id),
    errorMessage: (err) => humanizeError(err).message,
  });
  const selectedProject = resource.data || null;
  const loading = resource.initialLoading;
  const noContract = Boolean(selectedProject) && !(selectedProject.output_contract?.columns || []).length;
  const error = resource.error || (noContract ? 'Colonne in uscita non ancora note: esegui prima il progetto sorgente.' : null);

  useEffect(() => {
    const projectData = selectedProject;
    if (!projectData) return;
    const { updateNodeData } = useCanvasStore.getState();
    
    // Costruisci colonne dal contratto pubblico del progetto sorgente.
    let columnsWithTypes = [];

    const contractColumns = projectData.output_contract?.columns || [];
    if (contractColumns.length > 0) {
      // Pre-fix (P1 contracts-schemas): `col.type?.logical || 'string'`
      // maschera silenziosamente un drift BE: se il BE rinomina la
      // chiave (es. da `type.logical` a `data_type`) o cambia la
      // shape, TUTTE le colonne ricadono al default 'string' senza
      // alcun warning → quando il BE evolve schema, i nodi mostrano
      // tipi sbagliati ovunque ma il bug e' invisibile.
      // Post-fix: log warn aggregato (1 per render) quando >=1 colonna
      // ha mancanza del campo, cosi' il drift e' visibile in devtools.
      const _missingLogicalCount = contractColumns
        .filter(col => col.status !== 'removed' && col.status !== 'deleted')
        .filter(col => !col?.type?.logical).length;
      if (_missingLogicalCount > 0) {
        console.warn(
          `[ProjectSourceNode] Drift schema BE: ${_missingLogicalCount}/${contractColumns.length} ` +
          `colonne nel output_contract senza 'type.logical'. ` +
          `Fallback a 'string' applicato. Verifica shape backend.`
        );
      }
      columnsWithTypes = contractColumns
        .filter(col => col.status !== 'removed' && col.status !== 'deleted')
        .map(col => {
          const name = col.physical_name || col.display_name || col.source_name;
          const logicalType = col.type?.logical || 'string';
          return {
            id: col.id,
            column_id: col.id,
            source_name: col.source_name,
            display_name: col.display_name || name,
            name,
            type: normalizeColumnType({ type: logicalType }),
            visible: true
          };
        })
        .filter(col => col.id && col.name);
    } else {
      updateNodeData(id, { columns: [] }, { derived: true });
    }
    
    // Usa updateNodeData dallo store direttamente — funziona in tutti gli editor
    if (columnsWithTypes.length > 0) {
      const currentColumns = useCanvasStore.getState().nodes.find(n => n.id === id)?.data?.columns || [];
      if (currentColumns.length > 0) {
        const existingVisibility = {};
        currentColumns.forEach(col => {
          const key = col.id || col.column_id;
          if (key) {
            existingVisibility[key] = col.visible;
          }
          [col.source_name, col.name, col.display_name].forEach(name => {
            if (name) {
              existingVisibility[String(name)] = col.visible;
            }
          });
        });
        
        columnsWithTypes = columnsWithTypes.map(col => ({
          ...col,
          visible: existingVisibility[col.id] !== undefined
            ? existingVisibility[col.id]
            : existingVisibility[col.source_name] !== undefined
              ? existingVisibility[col.source_name]
              : existingVisibility[col.name] !== undefined
                ? existingVisibility[col.name]
                : col.visible
        }));
      }
      
      // Schema caricato dal progetto sorgente: dato DERIVATO (salvato via
      // pendingSync, niente avviso "modifiche non salvate" all'apertura).
      updateNodeData(id, { columns: columnsWithTypes }, { derived: true });
    }
  }, [selectedProject, id]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleToggleColumn = (columnId) => {
    if (!data.columns) return;
    const updatedColumns = data.columns.map(col =>
      (col.id || col.column_id) === columnId ? { ...col, visible: col.visible === false } : col
    );
    useCanvasStore.getState().updateNodeData(id, { columns: updatedColumns });
  };

  // Colonne e conteggi
  const columns = data.columns || [];
  const visibleCount = columns.filter(c => c.visible !== false).length;
  const totalCount = columns.length;
  const visibleColumns = isExpanded ? columns : columns.slice(0, 5);
  const hasMoreColumns = columns.length > 5;

  const projectName = selectedProject?.metadata?.name || selectedProject?.name;
  const projectType = selectedProject?.metadata?.project_type;
  const sourceFreshness = data.source_freshness;

  return (
    <div className="project-source-node">
      {/* Progetto letto e suo tipo. Titolo, chiusura ed eliminazione sono
          del pannello (NodeInspector, regola 6 del Blocco A). */}
      {(projectName || projectType) && (
        <div className="project-source-node__info">
          {projectName && <span className="project-source-node__info-name">📥 {projectName}</span>}
          {projectType && (
            <span className={`project-source-node__type-badge project-source-node__type-badge--${projectType}`}>
              {projectType}
            </span>
          )}
        </div>
      )}

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

        {/* RB.4.C: progetto sorgente non accessibile al gruppo del progetto.
            Backend (RB.4.B) marca data.access_denied=true sui canvas
            storici sporchi. UX: banner visibile, no rimozione automatica. */}
        {data.access_denied && (
          <div
            className="project-source-node__error"
            role="alert"
            title={data.access_denied_reason || 'Progetto sorgente non accessibile'}
          >
            🔒 Progetto sorgente non accessibile al tuo gruppo.
            {data.access_denied_reason ? (
              <div style={{ fontSize: '0.85em', marginTop: 4, opacity: 0.85 }}>
                {data.access_denied_reason}
              </div>
            ) : null}
          </div>
        )}

        {sourceFreshness?.stale && !data.access_denied && (
          <div
            className="project-source-node__warning"
            role="alert"
            title={sourceFreshness.message || 'Progetto sorgente da rieseguire'}
          >
            {sourceFreshness.message || 'Progetto sorgente modificato: rieseguilo prima di usare questo nodo.'}
          </div>
        )}

        {/* Colonne */}
        {columns.length > 0 && !loading && (
          <>
            <div className="project-source-node__columns-header">
              <span>Colonne ({visibleCount}/{totalCount}):</span>
              <span className="project-source-node__columns-hint">clic per escludere</span>
            </div>
            
            {visibleColumns.map((col, idx) => {
              const colType = normalizeColumnType(col);
              const isVisible = col.visible !== false;
              const columnId = col.id || col.column_id;
              return (
                <div
                  key={columnId || idx}
                  className={`project-source-node__column ${!isVisible ? 'project-source-node__column--hidden' : ''}`}
                  {...pressable(() => handleToggleColumn(columnId))}
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

      </div>

      {/* Handle output */}
      <NodeHandle 
        type="source" 
        position={Position.Right}
        className="project-source-node__handle"
      />
    </div>
  );
};

export default memo(ProjectSourceNode);
