import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import ReactFlow, { 
  Background, 
  Controls, 
  MiniMap,
  useNodesState,
  useEdgesState,
  addEdge
} from 'reactflow';
import 'reactflow/dist/style.css';
import { 
  getProject, 
  getCatalog, 
  updateCanvas, 
  listConnections, 
  getConnectionTables, 
  getDownloadUrl 
} from '../api/client';
import TableNode from '../components/TableNode';
import JoinModal from '../components/JoinModal';
import QueryPreview from '../components/QueryPreview';
import ResultNode from '../components/ResultNode';
import PreviewModal from '../components/PreviewModal';
import ExecutionModal from '../components/ExecutionModal';
import { validateCanvas } from '../utils/canvasValidation';
import { calculateOutputColumnsRecursive } from '../utils/columnUtils';
import { usePreview, useExecution } from '../hooks';
import '../styles/EstraiEditor.css';

const nodeTypes = {
  tableNode: TableNode,
  resultNode: ResultNode
};

function EstraiEditor() {
  const { projectId } = useParams();
  const navigate = useNavigate();
  
  const reactFlowWrapper = useRef(null);
  const [reactFlowInstance, setReactFlowInstance] = useState(null);

  const [loading, setLoading] = useState(true);
  const [project, setProject] = useState(null);
  const [catalog, setCatalog] = useState({});
  const [searchQuery, setSearchQuery] = useState('');
  const [projectConnection, setProjectConnection] = useState(null);
  
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);

  // Ref per evitare stale closure
  const nodesRef = useRef(nodes);
  const edgesRef = useRef(edges);
  
  useEffect(() => {
    nodesRef.current = nodes;
    edgesRef.current = edges;
  }, [nodes, edges]);

  // State per modal join
  const [showJoinModal, setShowJoinModal] = useState(false);
  const [pendingEdge, setPendingEdge] = useState(null);
  const [editingEdge, setEditingEdge] = useState(null);
  
  // State per validazione
  const [validationErrors, setValidationErrors] = useState([]);
  const [disconnectedNodeIds, setDisconnectedNodeIds] = useState([]);

  // ===== HOOKS CUSTOM =====
  
  // Salvataggio silenzioso per preview
  const handleSaveQuiet = useCallback(async () => {
    const currentNodes = nodesRef.current;
    const currentEdges = edgesRef.current;
    
    const validation = validateCanvas(currentNodes, currentEdges);
    
    if (!validation.valid) {
      throw new Error(
        'Canvas non valido:\n' + 
        validation.errors.map((e, i) => `${i + 1}. ${e}`).join('\n')
      );
    }
    
    const nodesToSave = currentNodes.map(node => {
      if (node.type === 'resultNode') {
        const outputColumns = calculateOutputColumnsRecursive(currentNodes, currentEdges, node.id);
        
        return {
          id: node.id,
          type: 'result',
          position: node.position,
          fileName: node.data.fileName,
          format: node.data.format,
          distinct: node.data.distinct || false,
          limit: node.data.limit || null,
          columns: outputColumns,
          group_by_enabled: node.data.group_by_enabled || false,
          group_by_columns: node.data.group_by_columns || [],
          aggregations: node.data.aggregations || []
        };
      } else {
        return {
          id: node.id,
          type: node.data.type || 'source',
          table_name: node.data.table_name,
          alias: node.alias,
          position: node.position,
          columns: node.data.columns || [],
          filters: node.data.filters || [],
          calculated_columns: node.data.calculated_columns || []
        };
      }
    });
    
    const edgesToSave = currentEdges.map(edge => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      join_type: edge.data?.join_type || 'left',
      source_column: edge.data?.source_column || '',
      target_column: edge.data?.target_column || '',
      is_spatial: edge.data?.is_spatial || false,
      spatial_predicate: edge.data?.spatial_predicate || null,
      source_geom_column: edge.data?.source_geom_column || null,
      target_geom_column: edge.data?.target_geom_column || null,
      spatial_distance: edge.data?.spatial_distance || null
    }));
    
    await updateCanvas(projectId, { 
      nodes: nodesToSave, 
      edges: edgesToSave 
    });
  }, [projectId]);

  // Hook Preview
  const preview = usePreview(projectId, handleSaveQuiet);

  // Hook Execution
  const execution = useExecution(projectId, {
    onBeforeExecute: handleSaveQuiet,
    validateCanvas: () => validateCanvas(nodesRef.current, edgesRef.current)
  });

  // ===== EFFECTS =====

  useEffect(() => {
    loadData();
  }, []);

  // Valida canvas quando cambiano nodi/edges
  useEffect(() => {
    if (nodes.length > 0) {
      const validation = validateCanvas(nodes, edges);
      setValidationErrors(validation.errors);
      setDisconnectedNodeIds(validation.disconnectedNodeIds);
      
      setNodes((nds) =>
        nds.map((node) => ({
          ...node,
          style: validation.disconnectedNodeIds.includes(node.id) ? {
            border: '2px solid #f44336',
            boxShadow: '0 0 10px rgba(244, 67, 54, 0.3)'
          } : undefined
        }))
      );
    }
  }, [nodes.length, edges.length]);

  // Aggiorna colonne del nodo risultato in real-time
  useEffect(() => {
    if (nodes.length > 0) {
      const resultNode = nodes.find(n => n.type === 'resultNode');
      if (!resultNode) return;

      const outputColumns = calculateOutputColumnsRecursive(nodes, edges, resultNode.id);
      
      const currentColumns = JSON.stringify(resultNode.data.columns || []);
      const newColumns = JSON.stringify(outputColumns);
      
      if (currentColumns !== newColumns) {
        setNodes((nds) =>
          nds.map((node) => {
            if (node.type === 'resultNode') {
              return {
                ...node,
                data: {
                  ...node.data,
                  columns: outputColumns
                }
              };
            }
            return node;
          })
        );
      }
    }
  }, [nodes, edges]);

  // ===== DATA LOADING =====

  const loadData = async () => {
    try {
      setLoading(true);
      
      const projectData = await getProject(projectId);
      setProject(projectData);
      
      let connection = null;
      if (projectData.metadata.default_connection_id) {
        try {
          const connectionsData = await listConnections();
          connection = connectionsData.connections.find(
            c => c.id === projectData.metadata.default_connection_id
          );
          setProjectConnection(connection || null);
        } catch (error) {
          console.error('Errore caricamento connessione:', error);
        }
      }
      
      if (connection) {
        try {
          const catalogData = await getConnectionTables(connection.id);
          setCatalog(catalogData.tables || {});
        } catch (error) {
          console.error('Errore caricamento catalogo da connessione:', error);
          alert('Errore caricamento tabelle dal database. Verifica la connessione.');
          setCatalog({});
        }
      } else {
        const catalogData = await getCatalog();
        setCatalog(catalogData.tables || {});
      }
      
      // Carica nodi dal canvas salvato
      const loadedNodes = (projectData.canvas.nodes || []).map(node => {
        if (node.type === 'result') {
          return {
            id: node.id,
            type: 'resultNode',
            position: node.position,
            data: {
              fileName: node.fileName || 'output',
              format: node.format || 'csv',
              distinct: node.distinct || false,
              limit: node.limit || null,
              columns: node.columns || [],
              group_by_enabled: node.group_by_enabled || false,
              group_by_columns: node.group_by_columns || [],
              aggregations: node.aggregations || [],
              onUpdateFileName: handleUpdateResultFileName,
              onUpdateFormat: handleUpdateResultFormat,
              onToggleDistinct: handleToggleDistinct,
              onUpdateLimit: handleUpdateLimit,
              onUpdateGroupBy: handleUpdateGroupBy,
              onPreview: () => preview.handlePreview(),
              onExecute: () => execution.handleExecute(),
              onDelete: handleDeleteNode
            }
          };
        } else {
          return {
            id: node.id,
            type: 'tableNode',
            position: node.position,
            style: disconnectedNodeIds.includes(node.id) ? {
              border: '2px solid #f44336',
              boxShadow: '0 0 10px rgba(244, 67, 54, 0.3)'
            } : undefined,
            data: { 
              label: node.alias || node.table_name,
              table_name: node.table_name,
              alias: node.alias,
              columns: node.columns || [],
              filters: node.filters || [],
              calculated_columns: node.calculated_columns || [],
              db_type: connection?.db_type || 'postgres',
              onUpdateAlias: handleUpdateNodeAlias,
              onToggleColumn: handleToggleColumn,
              onUpdateColumnAlias: handleUpdateColumnAlias,
              onUpdateFilters: handleUpdateFilters,
              onAddCalculatedColumn: handleAddCalculatedColumn,
              onDeleteCalculatedColumn: handleDeleteCalculatedColumn,
              onDelete: handleDeleteNode
            }
          };
        }
      });
      
      // Carica edges
      const loadedEdges = (projectData.canvas.edges || []).map(edge => {
        const targetNode = loadedNodes.find(n => n.id === edge.target);
        const isSpatial = edge.is_spatial || false;
        
        let label, strokeColor;
        if (targetNode?.type === 'resultNode') {
          label = 'output';
          strokeColor = '#4CAF50';
        } else if (isSpatial) {
          label = `🌐 ${edge.spatial_predicate?.replace('ST_', '') || 'spatial'}`;
          strokeColor = '#E91E63';
        } else {
          label = `${edge.join_type || 'left'} join`;
          strokeColor = '#2196F3';
        }
        
        return {
          id: edge.id,
          source: edge.source,
          target: edge.target,
          label,
          animated: true,
          style: { stroke: strokeColor, strokeWidth: 2 },
          data: { 
            join_type: edge.join_type || 'left',
            source_column: edge.source_column || '',
            target_column: edge.target_column || '',
            source_table: edge.source_table,
            target_table: edge.target_table,
            is_spatial: edge.is_spatial || false,
            spatial_predicate: edge.spatial_predicate || null,
            source_geom_column: edge.source_geom_column || null,
            target_geom_column: edge.target_geom_column || null,
            spatial_distance: edge.spatial_distance || null
          }
        };
      });

      setNodes(loadedNodes);
      setEdges(loadedEdges);
      
    } catch (error) {
      console.error('Errore caricamento:', error);
      alert('Errore caricamento progetto');
    } finally {
      setLoading(false);
    }
  };

  // ===== NODE HANDLERS =====

  const handleUpdateNodeAlias = useCallback((nodeId, newAlias) => {
    setNodes((nds) =>
      nds.map((node) => {
        if (node.id === nodeId) {
          return {
            ...node,
            data: {
              ...node.data,
              alias: newAlias,
              label: newAlias || node.data.table_name
            }
          };
        }
        return node;
      })
    );
  }, [setNodes]);

  const handleToggleColumn = useCallback((nodeId, columnIndex) => {
    setNodes((nds) =>
      nds.map((node) => {
        if (node.id === nodeId) {
          const updatedColumns = [...node.data.columns];
          updatedColumns[columnIndex] = {
            ...updatedColumns[columnIndex],
            visible: !updatedColumns[columnIndex].visible
          };
          return {
            ...node,
            data: {
              ...node.data,
              columns: updatedColumns
            }
          };
        }
        return node;
      })
    );
  }, [setNodes]);

  const handleUpdateColumnAlias = useCallback((nodeId, columnIndex, newAlias) => {
    setNodes((nds) =>
      nds.map((node) => {
        if (node.id === nodeId) {
          const updatedColumns = [...node.data.columns];
          updatedColumns[columnIndex] = {
            ...updatedColumns[columnIndex],
            alias: newAlias
          };
          return {
            ...node,
            data: {
              ...node.data,
              columns: updatedColumns
            }
          };
        }
        return node;
      })
    );
  }, [setNodes]);

  const handleUpdateFilters = useCallback((nodeId, newFilters) => {
    setNodes((nds) =>
      nds.map((node) => {
        if (node.id === nodeId) {
          return {
            ...node,
            data: {
              ...node.data,
              filters: newFilters
            }
          };
        }
        return node;
      })
    );
  }, [setNodes]);

  const handleAddCalculatedColumn = useCallback((nodeId, columnDef) => {
    setNodes((nds) =>
      nds.map((node) => {
        if (node.id === nodeId) {
          const currentCalculated = node.data.calculated_columns || [];
          return {
            ...node,
            data: {
              ...node.data,
              calculated_columns: [...currentCalculated, columnDef]
            }
          };
        }
        return node;
      })
    );
  }, [setNodes]);

  const handleDeleteCalculatedColumn = useCallback((nodeId, columnIndex) => {
    setNodes((nds) =>
      nds.map((node) => {
        if (node.id === nodeId) {
          const currentCalculated = node.data.calculated_columns || [];
          return {
            ...node,
            data: {
              ...node.data,
              calculated_columns: currentCalculated.filter((_, idx) => idx !== columnIndex)
            }
          };
        }
        return node;
      })
    );
  }, [setNodes]);

  const handleDeleteNode = useCallback((nodeId) => {
    setNodes((nds) => nds.filter((node) => node.id !== nodeId));
    setEdges((eds) => eds.filter((edge) => 
      edge.source !== nodeId && edge.target !== nodeId
    ));
  }, [setNodes, setEdges]);

  // ===== RESULT NODE HANDLERS =====

  const handleUpdateGroupBy = useCallback((nodeId, groupByConfig) => {
    setNodes((nds) =>
      nds.map((node) => {
        if (node.id === nodeId) {
          return {
            ...node,
            data: {
              ...node.data,
              group_by_enabled: groupByConfig.group_by_enabled,
              group_by_columns: groupByConfig.group_by_columns,
              aggregations: groupByConfig.aggregations
            }
          };
        }
        return node;
      })
    );
  }, [setNodes]);

  const handleUpdateResultFileName = useCallback((nodeId, newFileName) => {
    setNodes((nds) =>
      nds.map((node) => {
        if (node.id === nodeId) {
          return { ...node, data: { ...node.data, fileName: newFileName } };
        }
        return node;
      })
    );
  }, [setNodes]);

  const handleUpdateResultFormat = useCallback((nodeId, newFormat) => {
    setNodes((nds) =>
      nds.map((node) => {
        if (node.id === nodeId) {
          return { ...node, data: { ...node.data, format: newFormat } };
        }
        return node;
      })
    );
  }, [setNodes]);

  const handleToggleDistinct = useCallback((nodeId, newDistinct) => {
    setNodes((nds) =>
      nds.map((node) => {
        if (node.id === nodeId) {
          return { ...node, data: { ...node.data, distinct: newDistinct } };
        }
        return node;
      })
    );
  }, [setNodes]);

  const handleUpdateLimit = useCallback((nodeId, newLimit) => {
    setNodes((nds) =>
      nds.map((node) => {
        if (node.id === nodeId) {
          return { ...node, data: { ...node.data, limit: newLimit } };
        }
        return node;
      })
    );
  }, [setNodes]);

  const handleAddResultNode = useCallback(() => {
    if (nodes.some(n => n.type === 'resultNode')) {
      alert('Puoi avere solo un nodo risultato per progetto');
      return;
    }

    const newNode = {
      id: `result_${Date.now()}`,
      type: 'resultNode',
      position: { x: 400, y: 300 },
      data: {
        fileName: 'output',
        format: 'csv',
        distinct: false,
        limit: null,
        group_by_enabled: false,
        group_by_columns: [],
        aggregations: [],
        onUpdateFileName: handleUpdateResultFileName,
        onUpdateFormat: handleUpdateResultFormat,
        onToggleDistinct: handleToggleDistinct,
        onUpdateLimit: handleUpdateLimit,
        onUpdateGroupBy: handleUpdateGroupBy,
        onPreview: () => preview.handlePreview(),
        onExecute: () => execution.handleExecute(),
        onDelete: handleDeleteNode
      }
    };

    setNodes((nds) => nds.concat(newNode));
  }, [nodes, setNodes, handleDeleteNode, handleUpdateGroupBy, preview, execution]);

  // ===== SAVE =====

  const handleSave = async () => {
    const validation = validateCanvas(nodes, edges);
    
    if (!validation.valid) {
      setValidationErrors(validation.errors);
      setDisconnectedNodeIds(validation.disconnectedNodeIds);
      
      alert(
        'Impossibile salvare. Errori rilevati:\n\n' + 
        validation.errors.map((e, i) => `${i + 1}. ${e}`).join('\n')
      );
      return;
    }
    
    setValidationErrors([]);
    setDisconnectedNodeIds([]);
    
    try {
      await handleSaveQuiet();
      alert('Progetto salvato con successo!');
    } catch (error) {
      console.error('Errore salvataggio:', error);
      alert('Errore salvataggio progetto');
    }
  };

  // ===== EDGE HANDLERS =====

  const onConnect = useCallback((params) => {
    const targetNode = nodes.find(n => n.id === params.target);
    
    if (targetNode && targetNode.type === 'resultNode') {
      const newEdge = {
        id: `edge_${Date.now()}`,
        source: params.source,
        target: params.target,
        label: 'output',
        animated: true,
        style: { stroke: '#4CAF50', strokeWidth: 2 },
        data: { join_type: 'output' }
      };
      
      setEdges((eds) => addEdge(newEdge, eds));
      return;
    }
    
    setPendingEdge({
      source: params.source,
      target: params.target
    });
    setShowJoinModal(true);
  }, [nodes, setEdges]);

  const onEdgeClick = useCallback((event, edge) => {
    if (edge.data?.join_type === 'output') return;
    
    setEditingEdge(edge);
    setShowJoinModal(true);
  }, []);

  const handleSaveJoin = useCallback((joinConfig) => {
    const sourceNode = nodes.find(n => n.id === (editingEdge?.source || pendingEdge?.source));
    const targetNode = nodes.find(n => n.id === (editingEdge?.target || pendingEdge?.target));
    
    const isSpatial = joinConfig.is_spatial;
    const label = isSpatial 
      ? `🌐 ${joinConfig.spatial_predicate?.replace('ST_', '') || 'spatial'}`
      : `${joinConfig.join_type} join`;
    const strokeColor = isSpatial ? '#E91E63' : '#2196F3';
    
    if (editingEdge) {
      setEdges((eds) =>
        eds.map((e) => {
          if (e.id === editingEdge.id) {
            return {
              ...e,
              label,
              style: { stroke: strokeColor, strokeWidth: 2 },
              data: {
                ...e.data,
                ...joinConfig,
                source_table: sourceNode?.data?.table_name,
                target_table: targetNode?.data?.table_name
              }
            };
          }
          return e;
        })
      );
    } else if (pendingEdge) {
      const newEdge = {
        id: `edge_${Date.now()}`,
        source: pendingEdge.source,
        target: pendingEdge.target,
        label,
        animated: true,
        style: { stroke: strokeColor, strokeWidth: 2 },
        data: {
          ...joinConfig,
          source_table: sourceNode?.data?.table_name,
          target_table: targetNode?.data?.table_name
        }
      };
      
      setEdges((eds) => addEdge(newEdge, eds));
    }
    
    setShowJoinModal(false);
    setPendingEdge(null);
    setEditingEdge(null);
  }, [nodes, pendingEdge, editingEdge, setEdges]);

  const handleCancelJoin = useCallback(() => {
    setShowJoinModal(false);
    setPendingEdge(null);
    setEditingEdge(null);
  }, []);

  const handleDeleteEdge = useCallback(() => {
    if (editingEdge) {
      setEdges((eds) => eds.filter((e) => e.id !== editingEdge.id));
      setShowJoinModal(false);
      setEditingEdge(null);
    }
  }, [editingEdge, setEdges]);

  // ===== DRAG & DROP =====

  const onDrop = useCallback(
    (event) => {
      event.preventDefault();

      const tableName = event.dataTransfer.getData('tableName');
      
      if (!tableName || !reactFlowInstance) {
        return;
      }

      const reactFlowBounds = reactFlowWrapper.current.getBoundingClientRect();
      const position = reactFlowInstance.project({
        x: event.clientX - reactFlowBounds.left,
        y: event.clientY - reactFlowBounds.top,
      });

      const tableColumns = catalog[tableName] || [];
      
      const newNode = {
        id: `node_${Date.now()}`,
        type: 'tableNode',
        position,
        data: {
          label: tableName,
          table_name: tableName,
          alias: null,
          type: 'source',
          columns: tableColumns.map(col => {
            if (typeof col === 'object' && col.name) {
              return {
                name: col.name,
                type: col.type || 'str',
                alias: null,
                visible: true
              };
            }
            return {
              name: col,
              type: 'str',
              alias: null,
              visible: true
            };
          }),
          filters: [],
          calculated_columns: [],
          db_type: projectConnection?.db_type || 'postgres',
          onUpdateAlias: handleUpdateNodeAlias,
          onToggleColumn: handleToggleColumn,
          onUpdateColumnAlias: handleUpdateColumnAlias,
          onUpdateFilters: handleUpdateFilters,
          onAddCalculatedColumn: handleAddCalculatedColumn,
          onDeleteCalculatedColumn: handleDeleteCalculatedColumn,
          onDelete: handleDeleteNode
        }
      };

      setNodes((nds) => nds.concat(newNode));
    },
    [reactFlowInstance, catalog, setNodes, handleUpdateNodeAlias, handleToggleColumn, handleUpdateColumnAlias, handleUpdateFilters, handleDeleteNode, projectConnection]
  );

  const onDragOver = useCallback((event) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  }, []);

  // ===== RENDER HELPERS =====

  const handleBack = () => {
    navigate('/');
  };

  const filteredTables = Object.keys(catalog).filter(tableName => 
    tableName.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const tablesInCanvas = new Set(nodes.map(node => node.data.table_name));
  const availableTables = filteredTables.filter(tableName => 
    !tablesInCanvas.has(tableName)
  );

  // ===== LOADING / ERROR STATES =====

  if (loading) {
    return <div className="estrai-loading">Caricamento progetto...</div>;
  }

  if (!project) {
    return (
      <div className="estrai-error">
        <p className="estrai-error__message">Progetto non trovato</p>
        <button className="estrai-error__btn" onClick={handleBack}>
          Torna alla lista
        </button>
      </div>
    );
  }

  const tableCount = Object.keys(catalog).length;
  const hasResultNode = nodes.some(n => n.type === 'resultNode');

  const sourceNodeId = editingEdge?.source || pendingEdge?.source;
  const targetNodeId = editingEdge?.target || pendingEdge?.target;
  const sourceNode = nodes.find(n => n.id === sourceNodeId);
  const targetNode = nodes.find(n => n.id === targetNodeId);

  // ===== RENDER =====

  return (
    <div className="estrai-editor">
      {/* ===== TOOLBAR ===== */}
      <div className="estrai-toolbar">
        <div className="estrai-toolbar__left">
          <button 
            className="estrai-toolbar__btn estrai-toolbar__btn--back"
            onClick={handleBack}
          >
            ← Indietro
          </button>
          
          <button 
            className="estrai-toolbar__btn estrai-toolbar__btn--connections"
            onClick={() => navigate('/connections')}
            title="Gestisci connessioni database"
          >
            🔌 Connessioni
          </button>
        </div>
        
        <div className="estrai-toolbar__center">
          <h2 className="estrai-toolbar__title">{project.metadata.name}</h2>
          
          {projectConnection && (
            <div className="estrai-toolbar__connection-badge">
              <span>🔌</span>
              <span className="estrai-toolbar__connection-name">
                {projectConnection.name}
              </span>
              <span className="estrai-toolbar__connection-type">
                {projectConnection.type.toUpperCase()}
              </span>
            </div>
          )}
        </div>
        
        <div className="estrai-toolbar__right">
          <span className="estrai-toolbar__stats">
            Nodi: {nodes.length} | Edges: {edges.length}
          </span>
          
          <button 
            className="estrai-toolbar__btn estrai-toolbar__btn--output"
            onClick={handleAddResultNode}
            disabled={hasResultNode}
            title={hasResultNode ? 'Nodo risultato già presente' : 'Aggiungi nodo risultato'}
          >
            📁 Output
          </button>
          
          <button 
            className="estrai-toolbar__btn estrai-toolbar__btn--save"
            onClick={handleSave}
          >
            💾 Salva
          </button>
        </div>
      </div>

      {/* ===== MAIN CONTENT ===== */}
      <div className="estrai-main">
        {/* Sidebar */}
        <div className="estrai-sidebar">
          <div className="estrai-sidebar__content">
            <h3 className="estrai-sidebar__title">Tabelle Database</h3>
            <p className="estrai-sidebar__subtitle">
              Trascina una tabella nel canvas →
            </p>
            
            <input
              type="text"
              className="estrai-sidebar__search"
              placeholder="🔍 Cerca tabella..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
            
            <div className="estrai-sidebar__count">
              {availableTables.length} di {tableCount} tabelle
              {tablesInCanvas.size > 0 && (
                <span className="estrai-sidebar__count-detail">
                  ({tablesInCanvas.size} già nel canvas)
                </span>
              )}
            </div>
            
            <div className="estrai-sidebar__table-list">
              {availableTables.length === 0 ? (
                <div className="estrai-sidebar__empty">
                  {filteredTables.length === 0 
                    ? 'Nessuna tabella trovata'
                    : 'Tutte le tabelle sono già nel canvas'
                  }
                </div>
              ) : (
                availableTables.map(tableName => (
                  <div 
                    key={tableName}
                    className="estrai-sidebar__table-item"
                    draggable
                    onDragStart={(e) => {
                      e.dataTransfer.setData('tableName', tableName);
                      e.dataTransfer.effectAllowed = 'move';
                    }}
                  >
                    <span className="estrai-sidebar__table-name">
                      📊 {tableName}
                    </span>
                    <span className="estrai-sidebar__table-cols">
                      {catalog[tableName]?.length || 0} col
                    </span>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>

        {/* Canvas Area */}
        <div className="estrai-canvas" ref={reactFlowWrapper}>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onEdgeClick={onEdgeClick}
            onInit={(instance) => {
              setReactFlowInstance(instance);
              instance.setViewport({ x: 0, y: 0, zoom: 1 });
            }}
            onDrop={onDrop}
            onDragOver={onDragOver}
            nodeTypes={nodeTypes}
            fitView={false}
            className="estrai-canvas__flow"
          >
            <Background color="#ddd" gap={16} />
            <Controls />
            <MiniMap 
              style={{ height: 120, background: '#f5f5f5' }}
              zoomable
              pannable
            />
          </ReactFlow>

          <QueryPreview 
            nodes={nodes} 
            edges={edges}
            validationErrors={validationErrors}
          />
        </div>
      </div>

      {/* ===== JOIN MODAL ===== */}
      {showJoinModal && (
        <JoinModal
          edge={editingEdge || pendingEdge}
          sourceNode={sourceNode}
          targetNode={targetNode}
          onSave={handleSaveJoin}
          onCancel={handleCancelJoin}
          onDelete={handleDeleteEdge}
          isEditing={!!editingEdge}
        />
      )}

      {/* ===== EXECUTION MODAL ===== */}
      <ExecutionModal
        isOpen={execution.showModal}
        onClose={() => execution.closeModal()}
        isExecuting={execution.isExecuting}
        result={execution.result}
        downloadUrl={execution.downloadUrl}
        title="🚀 Esecuzione Query"
        projectType="estrai"
      />

      {/* ===== PREVIEW MODAL ===== */}
      <PreviewModal
        isOpen={preview.showModal}
        onClose={preview.closeModal}
        onRefresh={(limit) => preview.handlePreview(limit)}
        data={preview.data}
        loading={preview.loading}
        error={preview.error}
        title="Anteprima Query SQL"
        projectType="estrai"
      />
    </div>
  );
}

export default EstraiEditor;