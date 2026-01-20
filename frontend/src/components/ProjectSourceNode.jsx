import React, { useState, useEffect } from 'react';
import { Handle, Position } from 'reactflow';
import { getProject } from '../api/client';
import { getColumnTypeIcon, getColumnTypeLabel } from '../utils/columnUtils';

/**
 * ProjectSourceNode - Nodo sorgente per progetti SQL (tipo "estrai") o Manipola
 * 
 * Il progetto viene selezionato dal drag&drop dalla sidebar.
 * Il nodo mostra il nome del progetto (non modificabile) e le colonne con i tipi.
 */
const ProjectSourceNode = ({ id, data }) => {
  const [selectedProject, setSelectedProject] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // Carica dettagli progetto quando cambia source_project_id
  useEffect(() => {
    if (data.source_project_id) {
      loadProjectDetails(data.source_project_id);
    }
  }, [data.source_project_id]);

  /**
   * Normalizza il tipo colonna per consistenza
   * Supporta sia il vecchio formato (dtype: num/str/date/bool) 
   * che il nuovo (type: int/real/str/bool/date/geometry:*)
   */
  const normalizeColumnType = (col) => {
    // Prova prima 'type' (nuovo formato), poi 'dtype' (vecchio formato)
    const rawType = col.type || col.dtype || 'str';
    
    // Mappa vecchi valori ai nuovi
    const typeMap = {
      'num': 'real',      // vecchio 'num' -> nuovo 'real'
      'number': 'real',
      'integer': 'int',
      'float': 'real',
      'double': 'real',
      'string': 'str',
      'text': 'str',
      'boolean': 'bool',
      'datetime': 'date',
      'timestamp': 'date',
    };
    
    const normalized = typeMap[rawType.toLowerCase()] || rawType.toLowerCase();
    return normalized;
  };

  const loadProjectDetails = async (projectId) => {
    if (!projectId) return;
    
    setLoading(true);
    setError(null);
    
    try {
      const projectData = await getProject(projectId);
      
      console.log(`[ProjectSourceNode] Caricato progetto: ${projectId}`, projectData);
      
      // Estrai nome file output dal nodo result
      let outputFileName = null;
      if (projectData.canvas?.nodes) {
        const resultNode = projectData.canvas.nodes.find(n => n.type === 'result');
        if (resultNode?.fileName && resultNode?.format) {
          outputFileName = `${resultNode.fileName}.${resultNode.format}`;
        }
      }
      
      // Salva il nome file nel progetto per visualizzarlo
      if (outputFileName) {
        projectData.output_file = outputFileName;
      }
      
      setSelectedProject(projectData);
      
      // Costruisci colonne con tipi normalizzati
      let columnsWithTypes = [];
      
      // 1. Prima prova: columns_metadata (progetti Estrai eseguiti)
      if (projectData.columns_metadata && projectData.columns_metadata.length > 0) {
        columnsWithTypes = projectData.columns_metadata.map(col => ({
          name: col.name,
          type: normalizeColumnType(col),
          visible: true
        }));
        
        console.log(`[ProjectSourceNode] Colonne da columns_metadata: ${columnsWithTypes.length}`);
      } 
      // 2. Seconda prova: cerca nel canvas
      else if (projectData.canvas?.nodes) {
        columnsWithTypes = extractColumnsFromCanvas(projectData.canvas);
        console.log(`[ProjectSourceNode] Colonne da canvas: ${columnsWithTypes.length}`);
      }
      
      // Notifica parent con le colonne
      if (data.onUpdateColumns && columnsWithTypes.length > 0) {
        data.onUpdateColumns(id, columnsWithTypes);
      }
      
    } catch (err) {
      console.error(`[ProjectSourceNode] Errore caricamento progetto ${projectId}:`, err);
      setError(`Errore caricamento: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  /**
   * Estrae le colonne dal canvas di un progetto.
   * Cerca in ordine:
   * 1. Nodo result con data.columns
   * 2. Ultimo nodo transform con data.columns
   * 3. Nodo project_source con data.columns
   * 4. Nodo source con columns
   */
  const extractColumnsFromCanvas = (canvas) => {
    const nodes = canvas.nodes || [];
    const edges = canvas.edges || [];
    
    // Trova il nodo result
    const resultNode = nodes.find(n => n.type === 'result');
    
    // Helper per estrarre colonne da un array
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
    
    // 1. Prova result.data.columns
    if (resultNode?.data?.columns && resultNode.data.columns.length > 0) {
      return extractFromArray(resultNode.data.columns);
    }
    
    // 2. Prova result.columns (vecchio formato)
    if (resultNode?.columns && resultNode.columns.length > 0) {
      return extractFromArray(resultNode.columns);
    }
    
    // 3. Trova il nodo collegato al result (l'ultimo della pipeline)
    if (resultNode) {
      const incomingEdge = edges.find(e => e.target === resultNode.id);
      if (incomingEdge) {
        const sourceNode = nodes.find(n => n.id === incomingEdge.source);
        if (sourceNode?.data?.columns && sourceNode.data.columns.length > 0) {
          return extractFromArray(sourceNode.data.columns);
        }
      }
    }
    
    // 4. Cerca qualsiasi nodo con colonne (transform, project_source)
    for (const node of nodes) {
      if (node.data?.columns && node.data.columns.length > 0) {
        return extractFromArray(node.data.columns);
      }
      // Vecchio formato: columns direttamente sul nodo
      if (node.columns && node.columns.length > 0) {
        return extractFromArray(node.columns);
      }
    }
    
    return [];
  };

  const handleToggleColumn = (colName) => {
    if (!data.columns) return;
    
    const updatedColumns = data.columns.map(col => 
      col.name === colName ? { ...col, visible: !col.visible } : col
    );
    
    if (data.onUpdateColumns) {
      data.onUpdateColumns(id, updatedColumns);
    }
  };

  const handleDelete = () => {
    if (data.onDelete) {
      data.onDelete(id);
    }
  };

  // Conta colonne visibili
  const visibleCount = data.columns?.filter(c => c.visible !== false).length || 0;
  const totalCount = data.columns?.length || 0;

  return (
    <div style={{
      background: 'linear-gradient(135deg, #E8F5E9 0%, #C8E6C9 100%)',
      border: '2px solid #4CAF50',
      borderRadius: '8px',
      padding: '12px',
      minWidth: '280px',
      maxWidth: '320px',
      boxShadow: '0 2px 8px rgba(76,175,80,0.3)'
    }}>
      {/* Header */}
      <div style={{ 
        display: 'flex', 
        justifyContent: 'space-between', 
        alignItems: 'center',
        marginBottom: '10px'
      }}>
        <div style={{ fontWeight: 'bold', color: '#2E7D32', fontSize: '13px' }}>
          📥 Progetto Sorgente
        </div>
        <button
          onClick={handleDelete}
          style={{
            background: 'transparent',
            border: 'none',
            color: '#c62828',
            cursor: 'pointer',
            fontSize: '16px',
            padding: '2px 6px'
          }}
          title="Elimina nodo"
        >
          ×
        </button>
      </div>
      
      {/* Nome progetto FISSO (non modificabile) */}
      <div style={{ 
        width: '100%', 
        padding: '8px 10px',
        borderRadius: '4px',
        background: '#fff',
        border: '1px solid #81C784',
        marginBottom: '8px',
        fontSize: '13px',
        fontWeight: '600',
        color: '#2E7D32'
      }}>
        {selectedProject?.metadata?.name || selectedProject?.name || data.source_project_id?.split('_')[0] || 'Caricamento...'}
      </div>

      {/* Loading */}
      {loading && (
        <div style={{ 
          textAlign: 'center', 
          padding: '10px', 
          color: '#666',
          fontSize: '12px'
        }}>
          ⏳ Caricamento...
        </div>
      )}

      {/* Errore */}
      {error && (
        <div style={{ 
          color: '#c62828', 
          fontSize: '11px',
          padding: '5px',
          background: '#ffebee',
          borderRadius: '4px',
          marginBottom: '8px'
        }}>
          ⚠️ {error}
        </div>
      )}

      {/* Colonne con tipi e icone */}
      {data.columns && data.columns.length > 0 && !loading && (
        <div>
          <div style={{ 
            fontSize: '11px', 
            color: '#555', 
            marginBottom: '6px',
            display: 'flex',
            justifyContent: 'space-between'
          }}>
            <span>Colonne ({visibleCount}/{totalCount}):</span>
            <span style={{ color: '#888', fontSize: '10px' }}>
              click per escludere
            </span>
          </div>
          
          <div style={{ 
            maxHeight: '200px', 
            overflowY: 'auto',
            background: '#fff',
            borderRadius: '4px',
            padding: '6px'
          }}>
            {data.columns.map((col, idx) => {
              const colType = normalizeColumnType(col);
              return (
                <div
                  key={idx}
                  onClick={() => handleToggleColumn(col.name)}
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    padding: '3px 6px',
                    marginBottom: '2px',
                    background: col.visible !== false ? '#E8F5E9' : '#f5f5f5',
                    borderRadius: '3px',
                    cursor: 'pointer',
                    opacity: col.visible !== false ? 1 : 0.5,
                    fontSize: '11px',
                    fontFamily: 'monospace'
                  }}
                  title={getColumnTypeLabel(colType)}
                >
                  <span style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '4px',
                    textDecoration: col.visible !== false ? 'none' : 'line-through'
                  }}>
                    {/* Icona tipo */}
                    <span style={{ fontSize: '12px' }}>
                      {getColumnTypeIcon(colType)}
                    </span>
                    {col.name}
                  </span>
                  
                  {/* Badge tipo testuale */}
                  <span style={{
                    fontSize: '9px',
                    padding: '1px 4px',
                    borderRadius: '3px',
                    marginLeft: '4px',
                    background: colType === 'int' || colType === 'real' ? '#E3F2FD' : 
                                colType === 'date' ? '#FFF3E0' :
                                colType === 'bool' ? '#F3E5F5' : 
                                colType.startsWith('geometry') ? '#FCE4EC' : '#ECEFF1',
                    color: colType === 'int' || colType === 'real' ? '#1565C0' : 
                           colType === 'date' ? '#E65100' :
                           colType === 'bool' ? '#7B1FA2' : 
                           colType.startsWith('geometry') ? '#C2185B' : '#546E7A'
                  }}>
                    {colType}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Messaggio se nessuna colonna */}
      {(!data.columns || data.columns.length === 0) && !loading && !error && selectedProject && (
        <div style={{
          padding: '10px',
          background: '#FFF3E0',
          borderRadius: '4px',
          fontSize: '11px',
          color: '#E65100',
          textAlign: 'center'
        }}>
          ⚠️ Nessuna colonna trovata.<br/>
          Esegui prima il progetto sorgente.
        </div>
      )}

      {/* Info progetto selezionato */}
      {selectedProject && !loading && (
        <div style={{
          marginTop: '8px',
          fontSize: '10px',
          color: '#666',
          borderTop: '1px solid #C8E6C9',
          paddingTop: '6px'
        }}>
          {/* Nome file output */}
          {selectedProject.output_file && (
            <div style={{
              display: 'flex',
              alignItems: 'center',
              gap: '4px',
              marginBottom: '4px',
              fontFamily: 'monospace',
              fontSize: '11px',
              color: '#2E7D32'
            }}>
              📄 {selectedProject.output_file}
            </div>
          )}
          
          {/* Tipo progetto */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
            📋 {selectedProject.metadata?.name || selectedProject.name}
            {selectedProject.metadata?.project_type && (
              <span style={{
                padding: '1px 4px',
                background: selectedProject.metadata.project_type === 'manipola' ? '#F3E5F5' : '#E8F5E9',
                borderRadius: '3px',
                color: selectedProject.metadata.project_type === 'manipola' ? '#7B1FA2' : '#2E7D32'
              }}>
                {selectedProject.metadata.project_type}
              </span>
            )}
          </div>
        </div>
      )}

      {/* Handle output */}
      <Handle 
        type="source" 
        position={Position.Right}
        style={{
          background: '#4CAF50',
          width: '10px',
          height: '10px'
        }}
      />
    </div>
  );
};

export default ProjectSourceNode;