import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import ReactFlow, { 
  Background, 
  Controls, 
  MiniMap,
  useNodesState,
  useEdgesState
} from 'reactflow';
import 'reactflow/dist/style.css';
import { 
  getProject, 
  getCatalog, 
  updateCanvas, 
  listConnections, 
  getConnectionTables
} from '../api/client';
import TableNode from '../components/TableNode';
import JoinModal from '../components/JoinModal';
import QueryPreview from '../components/QueryPreview';
import ResultNode from '../components/ResultNode';
import PreviewModal from '../components/PreviewModal';
import ExecutionModal from '../components/ExecutionModal';
import { calculateOutputColumnsRecursive } from '../utils/columnUtils';
import { 
  usePreview, 
  useExecution, 
  useCanvasKeyboard,
  useCanvasValidation,
  useEdgeHandlers
} from '../hooks';
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

  // ===== STATE =====
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

  // ===== SALVATAGGIO SILENZIOSO =====
  const handleSaveQuiet = useCallback(async () => {
    const currentNodes = nodesRef.current;
    const currentEdges = edgesRef.current;
    
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
          geomColumn: node.data.geomColumn || null,
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
    
    await updateCanvas(projectId, { nodes: nodesToSave, edges: edgesToSave });
  }, [projectId]);

  // ===== CUSTOM HOOKS =====
  
  // Preview
  const preview = usePreview(projectId, handleSaveQuiet);

  // Execution
  const execution = useExecution(projectId, {
    onBeforeExecute: handleSaveQuiet
  });

  // Validazione canvas
  const validation = useCanvasValidation(nodes, edges, {
    validateOnChange: true,
    debounceMs: 300
  });

  // Gestione edges e modal join
  const edgeHandlers = useEdgeHandlers(nodes, setEdges, {
    resultNodeType: 'resultNode'
  });

  // Keyboard shortcuts
  useCanvasKeyboard({
    nodes,
    edges,
    onDeleteNode: handleDeleteNode,
    setEdges
  });

  // ===== EFFECTS =====

  useEffect(() => {
    loadData();
  }, []);

  // Applica stile nodi disconnessi
  useEffect(() => {
    if (validation.disconnectedNodeIds.length > 0) {
      setNodes((nds) =>
        nds.map((node) => ({
          ...node,
          style: validation.getNodeStyle(node.id)
        }))
      );
    }
  }, [validation.disconnectedNodeIds, setNodes, validation]);

  // Aggiorna colonne del nodo risultato in real-time
  useEffect(() => {
    if (nodes.length === 0) return;
    
    const resultNode = nodes.find(n => n.type === 'resultNode');
    if (!resultNode) return;

    const outputColumns = calculateOutputColumnsRecursive(nodes, edges, resultNode.id);
    const currentColumns = JSON.stringify(resultNode.data.columns || []);
    const newColumns = JSON.stringify(outputColumns);
    
    if (currentColumns !== newColumns) {
      setNodes((nds) =>
        nds.map((node) => {
          if (node.type === 'resultNode') {
            return { ...node, data: { ...node.data, columns: outputColumns } };
          }
          return node;
        })
      );
    }
  }, [nodes, edges, setNodes]);

  // ===== DATA LOADING =====

  const loadData = async () => {
    try {
      setLoading(true);
      
      const projectData = await getProject(projectId);
      setProject(projectData);
      
      // Carica connessione
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
      
      // Carica catalogo
      if (connection) {
        try {
          const catalogData = await getConnectionTables(connection.id);
          setCatalog(catalogData.tables || {});
        } catch (error) {
          console.error('Errore caricamento catalogo:', error);
          setCatalog({});
        }
      } else {
        const catalogData = await getCatalog();
        setCatalog(catalogData.tables || {});
      }
      
      // Carica nodi dal canvas
      const loadedNodes = (projectData.canvas.nodes || []).map(node => {
        if (node.type === 'result') {
          return createResultNodeFromData(node);
        } else {
          return createTableNodeFromData(node, connection);
        }
      });
      
      // Carica edges
      const loadedEdges = (projectData.canvas.edges || []).map(edge => 
        createEdgeFromData(edge, loadedNodes)
      );

      setNodes(loadedNodes);
      setEdges(loadedEdges);
      
    } catch (error) {
      console.error('Errore caricamento:', error);
      alert('Errore caricamento progetto');
    } finally {
      setLoading(false);
    }
  };

  // ===== NODE FACTORY FUNCTIONS =====

  const createResultNodeFromData = (node) => ({
    id: node.id,
    type: 'resultNode',
    position: node.position,
    data: {
      fileName: node.fileName || 'output',
      format: node.format || 'csv',
      distinct: node.distinct || false,
      limit: node.limit || null,
      geomColumn: node.geomColumn || null,
      columns: node.columns || [],
      group_by_enabled: node.group_by_enabled || false,
      group_by_columns: node.group_by_columns || [],
      aggregations: node.aggregations || [],
      onUpdateFileName: handleUpdateResultFileName,
      onUpdateFormat: handleUpdateResultFormat,
      onToggleDistinct: handleToggleDistinct,
      onUpdateLimit: handleUpdateLimit,
      onUpdateGeomColumn: handleUpdateGeomColumn,
      onUpdateGroupBy: handleUpdateGroupBy,
      onPreview: () => preview.handlePreview(),
      onExecute: () => execution.handleExecute(),
      onDelete: handleDeleteNode
    }
  });

  const createTableNodeFromData = (node, connection) => ({
    id: node.id,
    type: 'tableNode',
    position: node.position,
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
  });

  const createEdgeFromData = (edge, loadedNodes) => {
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
        is_spatial: edge.is_spatial || false,
        spatial_predicate: edge.spatial_predicate || null,
        source_geom_column: edge.source_geom_column || null,
        target_geom_column: edge.target_geom_column || null,
        spatial_distance: edge.spatial_distance || null
      }
    };
  };

  // ===== NODE HANDLERS =====

  const handleUpdateNodeAlias = useCallback((nodeId, newAlias) => {
    setNodes((nds) =>
      nds.map((node) => {
        if (node.id === nodeId) {
          return {
            ...node,
            data: { ...node.data, alias: newAlias, label: newAlias || node.data.table_name }
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
          return { ...node, data: { ...node.data, columns: updatedColumns } };
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
          updatedColumns[columnIndex] = { ...updatedColumns[columnIndex], alias: newAlias };
          return { ...node, data: { ...node.data, columns: updatedColumns } };
        }
        return node;
      })
    );
  }, [setNodes]);

  const handleUpdateFilters = useCallback((nodeId, newFilters) => {
    setNodes((nds) =>
      nds.map((node) => {
        if (node.id === nodeId) {
          return { ...node, data: { ...node.data, filters: newFilters } };
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
            data: { ...node.data, calculated_columns: [...currentCalculated, columnDef] }
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

  function handleDeleteNode(nodeId) {
    setNodes((nds) => nds.filter((node) => node.id !== nodeId));
    setEdges((eds) => eds.filter((edge) => edge.source !== nodeId && edge.target !== nodeId));
  }

  // ===== RESULT NODE HANDLERS =====

  const handleUpdateResultFileName = useCallback((nodeId, newFileName) => {
    setNodes((nds) =>
      nds.map((node) => node.id === nodeId 
        ? { ...node, data: { ...node.data, fileName: newFileName } } 
        : node
      )
    );
  }, [setNodes]);

  const handleUpdateResultFormat = useCallback((nodeId, newFormat) => {
    setNodes((nds) =>
      nds.map((node) => node.id === nodeId 
        ? { ...node, data: { ...node.data, format: newFormat } } 
        : node
      )
    );
  }, [setNodes]);

  const handleToggleDistinct = useCallback((nodeId, newDistinct) => {
    setNodes((nds) =>
      nds.map((node) => node.id === nodeId 
        ? { ...node, data: { ...node.data, distinct: newDistinct } } 
        : node
      )
    );
  }, [setNodes]);

  const handleUpdateLimit = useCallback((nodeId, newLimit) => {
    setNodes((nds) =>
      nds.map((node) => node.id === nodeId 
        ? { ...node, data: { ...node.data, limit: newLimit } } 
        : node
      )
    );
  }, [setNodes]);

  const handleUpdateGeomColumn = useCallback((nodeId, newGeomColumn) => {
    setNodes((nds) =>
      nds.map((node) => node.id === nodeId 
        ? { ...node, data: { ...node.data, geomColumn: newGeomColumn } } 
        : node
      )
    );
  }, [setNodes]);

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
  }, [nodes, setNodes, preview, execution]);

  // ===== SAVE =====

  const handleSave = async () => {
    if (!validation.isValid) {
      alert(
        'Impossibile salvare. Errori rilevati:\n\n' + 
        validation.errors.map((e, i) => `${i + 1}. ${e}`).join('\n')
      );
      return;
    }
    
    try {
      await handleSaveQuiet();
      alert('Progetto salvato con successo!');
    } catch (error) {
      console.error('Errore salvataggio:', error);
      alert('Errore salvataggio progetto');
    }
  };

  // ===== DRAG & DROP =====

  const onDrop = useCallback(
    (event) => {
      event.preventDefault();
      const tableName = event.dataTransfer.getData('tableName');
      
      if (!tableName || !reactFlowInstance) return;

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
          columns: tableColumns.map(col => ({
            name: typeof col === 'object' ? col.name : col,
            type: typeof col === 'object' ? col.type || 'str' : 'str',
            alias: null,
            visible: true
          })),
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
    [reactFlowInstance, catalog, setNodes, projectConnection]
  );

  const onDragOver = useCallback((event) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  }, []);

  // ===== RENDER HELPERS =====

  const handleBack = () => navigate('/');

  const filteredTables = Object.keys(catalog).filter(tableName => 
    tableName.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const tablesInCanvas = new Set(nodes.map(node => node.data.table_name));
  const availableTables = filteredTables.filter(tableName => !tablesInCanvas.has(tableName));

  // ===== LOADING / ERROR =====

  if (loading) {
    return <div className="estrai-loading">Caricamento progetto...</div>;
  }

  if (!project) {
    return (
      <div className="estrai-error">
        <p className="estrai-error__message">Progetto non trovato</p>
        <button className="estrai-error__btn" onClick={handleBack}>Torna alla lista</button>
      </div>
    );
  }

  const tableCount = Object.keys(catalog).length;
  const hasResultNode = nodes.some(n => n.type === 'resultNode');

  // ===== RENDER =====

  return (
    <div className="estrai-editor">
      {/* TOOLBAR */}
      <div className="estrai-toolbar">
        <div className="estrai-toolbar__left">
          <button className="estrai-toolbar__btn estrai-toolbar__btn--back" onClick={handleBack}>
            ← Indietro
          </button>
          <button 
            className="estrai-toolbar__btn estrai-toolbar__btn--connections"
            onClick={() => navigate('/connections')}
          >
            🔌 Connessioni
          </button>
        </div>
        
        <div className="estrai-toolbar__center">
          <h2 className="estrai-toolbar__title">{project.metadata.name}</h2>
          {projectConnection && (
            <div className="estrai-toolbar__connection-badge">
              <span>🔌</span>
              <span className="estrai-toolbar__connection-name">{projectConnection.name}</span>
              <span className="estrai-toolbar__connection-type">{projectConnection.type.toUpperCase()}</span>
            </div>
          )}
        </div>
        
        <div className="estrai-toolbar__right">
          <span className="estrai-toolbar__stats">Nodi: {nodes.length} | Edges: {edges.length}</span>
          <button 
            className="estrai-toolbar__btn estrai-toolbar__btn--output"
            onClick={handleAddResultNode}
            disabled={hasResultNode}
          >
            📁 Output
          </button>
          <button className="estrai-toolbar__btn estrai-toolbar__btn--save" onClick={handleSave}>
            💾 Salva
          </button>
        </div>
      </div>

      {/* MAIN */}
      <div className="estrai-main">
        {/* Sidebar */}
        <div className="estrai-sidebar">
          <div className="estrai-sidebar__content">
            <h3 className="estrai-sidebar__title">Tabelle Database</h3>
            <p className="estrai-sidebar__subtitle">Trascina una tabella nel canvas →</p>
            
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
                <span className="estrai-sidebar__count-detail"> ({tablesInCanvas.size} nel canvas)</span>
              )}
            </div>
            
            <div className="estrai-sidebar__table-list">
              {availableTables.length === 0 ? (
                <div className="estrai-sidebar__empty">
                  {filteredTables.length === 0 ? 'Nessuna tabella trovata' : 'Tutte le tabelle sono nel canvas'}
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
                    <span className="estrai-sidebar__table-name">📊 {tableName}</span>
                    <span className="estrai-sidebar__table-cols">{catalog[tableName]?.length || 0} col</span>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>

        {/* Canvas */}
        <div className="estrai-canvas" ref={reactFlowWrapper}>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={edgeHandlers.onConnect}
            onEdgeClick={edgeHandlers.onEdgeClick}
            onInit={setReactFlowInstance}
            onDrop={onDrop}
            onDragOver={onDragOver}
            nodeTypes={nodeTypes}
            fitView={false}
          >
            <Background color="#ddd" gap={16} />
            <Controls />
            <MiniMap style={{ height: 120, background: '#f5f5f5' }} zoomable pannable />
          </ReactFlow>

          <QueryPreview 
            nodes={nodes} 
            edges={edges}
            validationErrors={validation.errors}
          />
        </div>
      </div>

      {/* JOIN MODAL */}
      {edgeHandlers.showJoinModal && (
        <JoinModal
          edge={edgeHandlers.editingEdge || edgeHandlers.pendingEdge}
          sourceNode={edgeHandlers.sourceNode}
          targetNode={edgeHandlers.targetNode}
          onSave={edgeHandlers.handleSaveJoin}
          onCancel={edgeHandlers.handleCancelJoin}
          onDelete={edgeHandlers.handleDeleteEdge}
          isEditing={edgeHandlers.isEditing}
        />
      )}

      {/* EXECUTION MODAL */}
      <ExecutionModal
        isOpen={execution.showModal}
        onClose={execution.closeModal}
        isExecuting={execution.isExecuting}
        result={execution.result}
        downloadUrl={execution.downloadUrl}
        title="🚀 Esecuzione Query"
        projectType="estrai"
      />

      {/* PREVIEW MODAL */}
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