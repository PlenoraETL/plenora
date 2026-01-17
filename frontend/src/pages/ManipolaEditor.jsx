import React, { useState, useCallback, useEffect, useRef } from 'react';
import ReactFlow, { 
  Background, 
  Controls, 
  applyEdgeChanges, 
  applyNodeChanges,
  addEdge,
  useReactFlow,
  ReactFlowProvider,
  useNodesState,
  useEdgesState,
  MarkerType
} from 'reactflow';
import 'reactflow/dist/style.css';
import { useParams, useNavigate } from 'react-router-dom';
import { 
  listProjects, 
  getProject, 
  updateCanvas,
  executeProjectQuery 
} from '../api/client';

import ProjectSourceNode from '../components/ProjectSourceNode';
import TransformNode from '../components/TransformNode';
import ResultNode from '../components/ResultNode';
import '../styles/ManipolaEditor.css';

const nodeTypes = {
  project_source: ProjectSourceNode,
  transform: TransformNode,
  result: ResultNode,
};

const defaultEdgeOptions = {
  type: 'smoothstep',
  animated: false,
  style: { 
    stroke: '#9C27B0',
    strokeWidth: 2
  },
  markerEnd: {
    type: MarkerType.ArrowClosed,
    width: 20,
    height: 20,
    color: '#9C27B0',
  },
};

const ManipolaEditorContent = () => {
  const { id: projectId } = useParams();
  const navigate = useNavigate();
  const { screenToFlowPosition } = useReactFlow();
  const reactFlowInstance = useReactFlow();
  
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [availableSources, setAvailableSources] = useState([]);
  const [project, setProject] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [executing, setExecuting] = useState(false);
  
  // Stati per modal esecuzione
  const [showExecutionModal, setShowExecutionModal] = useState(false);
  const [executionResult, setExecutionResult] = useState(null);
  const [isExecuting, setIsExecuting] = useState(false);
  
  const nodesRef = useRef(nodes);
  const edgesRef = useRef(edges);
  
  useEffect(() => {
    nodesRef.current = nodes;
    edgesRef.current = edges;
  }, [nodes, edges]);

  // Aggiorna availableColumns nel ResultNode quando cambiano i nodi sorgente
  useEffect(() => {
    const resultNode = nodes.find(n => n.type === 'result');
    if (!resultNode) return;
    
    // Raccogli colonne da tutti i nodi sorgente
    const availableColumns = [];
    for (const node of nodes) {
      if (node.type === 'project_source' && node.data?.columns) {
        for (const col of node.data.columns) {
          availableColumns.push({
            name: col.name || col,
            type: col.type || 'str',
            source: node.data?.source_project_id || 'source'
          });
        }
      } else if (node.type === 'transform' && node.data?.columns) {
        for (const col of node.data.columns) {
          availableColumns.push({
            name: col.name || col,
            type: col.type || 'str',
            source: 'transform'
          });
        }
      }
    }
    
    // Aggiorna solo se le colonne sono cambiate
    const currentColumns = resultNode.data?.availableColumns || [];
    const columnsChanged = JSON.stringify(availableColumns) !== JSON.stringify(currentColumns);
    
    if (columnsChanged && availableColumns.length > 0) {
      setNodes((nds) =>
        nds.map((node) => {
          if (node.type === 'result') {
            return {
              ...node,
              data: {
                ...node.data,
                availableColumns: availableColumns
              }
            };
          }
          return node;
        })
      );
    }
  }, [nodes, setNodes]);

  // Gestione Delete/Backspace
  useEffect(() => {
    const handleKeyDown = (event) => {
      const target = event.target;
      if (target.tagName === 'INPUT' || 
          target.tagName === 'TEXTAREA' || 
          target.isContentEditable) {
        return;
      }
      
      if (event.key === 'Delete' || event.key === 'Backspace') {
        const selectedNodes = nodes.filter(n => n.selected);
        const selectedEdges = edges.filter(e => e.selected);
        
        if (selectedNodes.length > 0) {
          selectedNodes.forEach(node => {
            handleDeleteNode(node.id);
          });
        }
        
        if (selectedEdges.length > 0) {
          setEdges((eds) => 
            eds.filter(edge => !selectedEdges.find(e => e.id === edge.id))
          );
        }
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [nodes, edges, setEdges]);

  useEffect(() => {
    loadProjectAndSources();
  }, [projectId]);

  const loadProjectAndSources = async () => {
    try {
      setLoading(true);
      
      const projectData = await getProject(projectId);
      setProject(projectData);
      
      if (projectData.canvas && projectData.canvas.nodes) {
        const loadedNodes = projectData.canvas.nodes.map(node => {
          if (node.type === 'result') {
            return {
              ...node,
              data: {
                isManipulate: true,
                fileName: node.data?.fileName || 'output',
                format: node.data?.format || 'csv',
                geomColumn: node.data?.geomColumn || null,
                availableColumns: [], // Sarà aggiornato dall'useEffect
                onUpdateFileName: handleUpdateResultFileName,
                onUpdateFormat: handleUpdateResultFormat,
                onUpdateGeomColumn: handleUpdateGeomColumn,
                onExecute: handleExecute,
                onDelete: handleDeleteNode
              }
            };
          } else if (node.type === 'project_source') {
            return {
              ...node,
              data: {
                source_project_id: node.data?.source_project_id || '',
                columns: node.data?.columns || [],
                onChange: (val) => handleUpdateProjectSource(node.id, val),
                onUpdateColumns: handleUpdateProjectSourceColumns,
                onDelete: handleDeleteNode
              }
            };
          } else if (node.type === 'transform') {
            return {
              ...node,
              data: {
                transform_op: node.data?.transform_op || 'join',
                config: node.data?.config || {},
                columns: node.data?.columns || [],
                onChange: (newData) => handleUpdateTransform(node.id, newData),
                onUpdateColumns: handleUpdateProjectSourceColumns,
                onDelete: handleDeleteNode
              }
            };
          }
          return node;
        });
        
        setNodes(loadedNodes);
        setEdges(projectData.canvas.edges || []);
      }
      
      const projectsData = await listProjects();
      const sqlProjects = (projectsData.projects || []).filter(
        p => (p.project_type || 'estrai') === 'estrai'
      );
      setAvailableSources(sqlProjects);
      
    } catch (err) {
      console.error("[ManipolaEditor] Errore caricamento:", err);
      alert('Errore caricamento progetto');
    } finally {
      setLoading(false);
    }
  };

  const handleUpdateProjectSource = useCallback((nodeId, projectSourceId) => {
    setNodes((nds) =>
      nds.map((node) => {
        if (node.id === nodeId) {
          return {
            ...node,
            data: {
              ...node.data,
              source_project_id: projectSourceId
            }
          };
        }
        return node;
      })
    );
  }, [setNodes]);

  const handleUpdateProjectSourceColumns = useCallback((nodeId, columns) => {
    setNodes((nds) =>
      nds.map((node) => {
        if (node.id === nodeId) {
          return {
            ...node,
            data: {
              ...node.data,
              columns: columns
            }
          };
        }
        return node;
      })
    );
  }, [setNodes]);

  const handleUpdateTransform = useCallback((nodeId, newData) => {
    setNodes((nds) =>
      nds.map((node) => {
        if (node.id === nodeId) {
          return {
            ...node,
            data: {
              ...node.data,
              ...newData
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
          return {
            ...node,
            data: {
              ...node.data,
              fileName: newFileName
            }
          };
        }
        return node;
      })
    );
  }, [setNodes]);

  const handleUpdateResultFormat = useCallback((nodeId, newFormat) => {
    setNodes((nds) =>
      nds.map((node) => {
        if (node.id === nodeId) {
          return {
            ...node,
            data: {
              ...node.data,
              format: newFormat
            }
          };
        }
        return node;
      })
    );
  }, [setNodes]);

  const handleUpdateGeomColumn = useCallback((nodeId, geomColumn) => {
    setNodes((nds) =>
      nds.map((node) => {
        if (node.id === nodeId) {
          return {
            ...node,
            data: {
              ...node.data,
              geomColumn: geomColumn
            }
          };
        }
        return node;
      })
    );
  }, [setNodes]);

  // Calcola colonne disponibili dai nodi sorgente (ProjectSource e Transform)
  const getAvailableColumns = useCallback(() => {
    const columns = [];
    
    for (const node of nodesRef.current) {
      if (node.type === 'project_source' && node.data?.columns) {
        // Colonne da ProjectSource
        for (const col of node.data.columns) {
          columns.push({
            name: col.name || col,
            type: col.type || 'str',
            source: node.data?.source_project_id || 'source'
          });
        }
      } else if (node.type === 'transform' && node.data?.columns) {
        // Colonne da TransformNode (se disponibili)
        for (const col of node.data.columns) {
          columns.push({
            name: col.name || col,
            type: col.type || 'str',
            source: 'transform'
          });
        }
      }
    }
    
    return columns;
  }, []);

  const handleDeleteNode = useCallback((nodeId) => {
    setNodes((nds) => nds.filter((node) => node.id !== nodeId));
    setEdges((eds) => eds.filter((edge) => 
      edge.source !== nodeId && edge.target !== nodeId
    ));
  }, [setNodes, setEdges]);

  const handleAddTransformNode = useCallback(() => {
    const newNodeId = `transform_${Date.now()}`;
    const newNode = {
      id: newNodeId,
      type: 'transform',
      position: { 
        x: Math.random() * 300 + 200, 
        y: Math.random() * 200 + 100 
      },
      data: {
        transform_op: 'join',
        config: {},
        columns: [],
        onChange: (newData) => handleUpdateTransform(newNodeId, newData),
        onUpdateColumns: handleUpdateProjectSourceColumns,
        onDelete: handleDeleteNode
      }
    };

    setNodes((nds) => nds.concat(newNode));
  }, [setNodes, handleUpdateTransform, handleUpdateProjectSourceColumns, handleDeleteNode]);

  const handleAddResultNode = useCallback(() => {
    const currentNodes = nodesRef.current;
    if (currentNodes.some(n => n.type === 'result')) {
      alert('Puoi avere solo un nodo risultato per progetto');
      return;
    }

    const newNode = {
      id: `result_${Date.now()}`,
      type: 'result',
      position: { x: 500, y: 200 },
      data: {
        isManipulate: true,
        fileName: 'output',
        format: 'csv',
        availableColumns: getAvailableColumns(),
        onUpdateFileName: handleUpdateResultFileName,
        onUpdateFormat: handleUpdateResultFormat,
        onUpdateGeomColumn: handleUpdateGeomColumn,
        onExecute: handleExecute,
        onDelete: handleDeleteNode
      }
    };

    setNodes((nds) => nds.concat(newNode));
  }, [setNodes, handleUpdateResultFileName, handleUpdateResultFormat, handleUpdateGeomColumn, handleDeleteNode, getAvailableColumns]);

  const handleSaveCanvas = useCallback(async () => {
    try {
      setSaving(true);
      
      const currentNodes = nodesRef.current;
      const currentEdges = edgesRef.current;
      
      const canvasToSave = {
        nodes: currentNodes.map(node => ({
          id: node.id,
          type: node.type,
          position: node.position,
          data: {
            source_project_id: node.data?.source_project_id,
            columns: node.data?.columns,
            transform_op: node.data?.transform_op,
            config: node.data?.config,
            fileName: node.data?.fileName,
            format: node.data?.format
          }
        })),
        edges: currentEdges
      };
      
      await updateCanvas(projectId, canvasToSave);
      
    } catch (error) {
      console.error('[ManipolaEditor] Errore salvataggio canvas:', error);
      alert('Errore durante il salvataggio');
    } finally {
      setSaving(false);
    }
  }, [projectId]);

  const handleExecute = useCallback(async (nodeId, options = {}) => {
    setShowExecutionModal(true);
    setIsExecuting(true);
    setExecutionResult(null);
    
    try {
      const currentNodes = nodesRef.current;
      
      if (currentNodes.length === 0) {
        throw new Error('Canvas vuoto. Aggiungi nodi prima di eseguire.');
      }
      
      const resultNode = currentNodes.find(n => n.type === 'result');
      if (!resultNode) {
        throw new Error('Aggiungi un nodo Output prima di eseguire.');
      }
      
      const sourceNodes = currentNodes.filter(n => n.type === 'project_source');
      if (sourceNodes.length === 0) {
        throw new Error('Aggiungi almeno un progetto sorgente.');
      }
      
      await handleSaveCanvas();
      
      const response = await executeProjectQuery(projectId, options);
      
      setExecutionResult(response);
      
    } catch (error) {
      console.error('[ManipolaEditor] Errore esecuzione pipeline:', error);
      setExecutionResult({
        success: false,
        message: error.response?.data?.detail || error.message || 'Errore esecuzione pipeline'
      });
    } finally {
      setIsExecuting(false);
    }
  }, [projectId, handleSaveCanvas]);

  const onConnect = useCallback((params) => {
    setEdges((eds) => addEdge({ ...params, ...defaultEdgeOptions }, eds));
  }, [setEdges]);

  const onDragStart = (event, project) => {
    event.dataTransfer.setData('application/reactflow', 'project_source');
    event.dataTransfer.setData('projectData', JSON.stringify(project));
    event.dataTransfer.effectAllowed = 'move';
  };

  const onDragOver = useCallback((event) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  }, []);

  const onDrop = useCallback(
    (event) => {
      event.preventDefault();

      const type = event.dataTransfer.getData('application/reactflow');
      const projectDataStr = event.dataTransfer.getData('projectData');

      if (!type || !projectDataStr) return;

      const projectData = JSON.parse(projectDataStr);
      const position = screenToFlowPosition({ 
        x: event.clientX, 
        y: event.clientY 
      });

      const newNodeId = `source_${Date.now()}`;
      const newNode = {
        id: newNodeId,
        type: 'project_source',
        position,
        data: { 
          source_project_id: projectData.id,
          columns: [],
          onChange: (val) => handleUpdateProjectSource(newNodeId, val),
          onUpdateColumns: handleUpdateProjectSourceColumns,
          onDelete: handleDeleteNode
        }
      };

      setNodes((nds) => nds.concat(newNode));
    },
    [screenToFlowPosition, setNodes, handleUpdateProjectSource, handleUpdateProjectSourceColumns, handleDeleteNode]
  );

  const onEdgeContextMenu = useCallback((event, edge) => {
    event.preventDefault();
    if (window.confirm('Eliminare questo collegamento?')) {
      setEdges((eds) => eds.filter((e) => e.id !== edge.id));
    }
  }, [setEdges]);

  const handleBack = () => {
    navigate('/');
  };

  // Loading state
  if (loading) {
    return <div className="manipola-loading">Caricamento progetto...</div>;
  }

  // Error state
  if (!project) {
    return (
      <div className="manipola-error">
        <p>Progetto non trovato</p>
        <button onClick={handleBack}>Torna alla lista</button>
      </div>
    );
  }

  return (
    <div className="manipola-editor">
      {/* ===== TOOLBAR ===== */}
      <div className="manipola-toolbar">
        <div className="manipola-toolbar__left">
          <button 
            className="manipola-toolbar__btn manipola-toolbar__btn--back"
            onClick={handleBack}
          >
            ← Indietro
          </button>
        </div>
        
        <div className="manipola-toolbar__center">
          <h2 className="manipola-toolbar__title">{project.metadata?.name}</h2>
          <span className="manipola-toolbar__badge">MANIPOLA</span>
        </div>
        
        <div className="manipola-toolbar__right">
          <button 
            className="manipola-toolbar__btn manipola-toolbar__btn--transform"
            onClick={handleAddTransformNode}
          >
            + Transform
          </button>
          <button 
            className="manipola-toolbar__btn manipola-toolbar__btn--output"
            onClick={handleAddResultNode}
          >
            📁 Output
          </button>
          <button 
            className="manipola-toolbar__btn manipola-toolbar__btn--save"
            onClick={handleSaveCanvas}
            disabled={saving}
          >
            {saving ? 'Salvataggio...' : '💾 Salva'}
          </button>
        </div>
      </div>

      {/* ===== MAIN CONTENT ===== */}
      <div className="manipola-main">
        {/* Sidebar */}
        <div className="manipola-sidebar">
          <div className="manipola-sidebar__header">
            <h3 className="manipola-sidebar__title">Sorgenti Progetto</h3>
            <p className="manipola-sidebar__subtitle">
              Trascina un progetto SQL nel canvas
            </p>
          </div>
          
          <div className="manipola-sidebar__sources">
            {availableSources.length === 0 ? (
              <div className="manipola-sidebar__empty">
                Nessun progetto SQL disponibile.
                <br />
                Crea prima un progetto di tipo Estrai.
              </div>
            ) : (
              availableSources.map((proj) => (
                <div
                  key={proj.id}
                  className="manipola-sidebar__source-item"
                  draggable
                  onDragStart={(e) => onDragStart(e, proj)}
                >
                  <span className="manipola-sidebar__source-icon">📊</span>
                  <span className="manipola-sidebar__source-name">{proj.name}</span>
                  <span className="manipola-sidebar__source-badge">SQL</span>
                </div>
              ))
            )}
          </div>

          <div className="manipola-sidebar__actions">
            <h4 className="manipola-sidebar__actions-title">Azioni Rapide</h4>
            
            <button
              className="manipola-sidebar__action-btn manipola-sidebar__action-btn--transform"
              onClick={handleAddTransformNode}
            >
              + Aggiungi Transform
            </button>
            
            <button
              className="manipola-sidebar__action-btn manipola-sidebar__action-btn--output"
              onClick={handleAddResultNode}
            >
              📁 Aggiungi Output
            </button>
          </div>
        </div>

        {/* Canvas Area */}
        <div 
          className="manipola-canvas"
          onDrop={onDrop} 
          onDragOver={onDragOver}
        >
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onEdgeContextMenu={onEdgeContextMenu}
            nodeTypes={nodeTypes}
            defaultEdgeOptions={defaultEdgeOptions}
            fitView
            fitViewOptions={{ padding: 0.2 }}
            minZoom={0.5}
            maxZoom={2}
          >
            <Background color="#aaa" gap={20} />
            <Controls />
          </ReactFlow>
        </div>
      </div>

      {/* ===== EXECUTION MODAL ===== */}
      {showExecutionModal && (
        <div className="modal-overlay" onClick={() => setShowExecutionModal(false)}>
          <div 
            className="modal manipola-execution-modal" 
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="manipola-execution-modal__title">
              🔄 Esecuzione Pipeline Manipola
            </h3>

            {isExecuting ? (
              <div className="manipola-execution-modal__loading">
                <div className="manipola-execution-modal__spinner">⏳</div>
                <p className="manipola-execution-modal__loading-text">
                  Esecuzione pipeline in corso...
                </p>
              </div>
            ) : executionResult ? (
              <div>
                {executionResult.success ? (
                  <div>
                    <div className="manipola-execution-modal__success">
                      <div className="manipola-execution-modal__success-title">
                        ✓ Pipeline eseguita con successo!
                      </div>
                      <div className="manipola-execution-modal__success-message">
                        {executionResult.message || 'Pipeline completata'}
                      </div>
                    </div>

                    <div className="manipola-execution-modal__stats">
                      <div className="manipola-execution-modal__stat">
                        <div className="manipola-execution-modal__stat-label">
                          Righe processate
                        </div>
                        <div className="manipola-execution-modal__stat-value">
                          {executionResult.rows_count?.toLocaleString() || 'N/A'}
                        </div>
                      </div>
                      <div className="manipola-execution-modal__stat">
                        <div className="manipola-execution-modal__stat-label">
                          Colonne output
                        </div>
                        <div className="manipola-execution-modal__stat-value">
                          {executionResult.columns?.length || 0}
                        </div>
                      </div>
                    </div>

                    {executionResult.output_path && (
                      <div className="manipola-execution-modal__output">
                        <div className="manipola-execution-modal__output-label">
                          📄 File generato
                        </div>
                        <div className="manipola-execution-modal__output-path">
                          {executionResult.output_path}
                        </div>
                      </div>
                    )}

                    {executionResult.columns && executionResult.columns.length > 0 && (
                      <div className="manipola-execution-modal__columns">
                        <div className="manipola-execution-modal__columns-label">
                          Colonne output ({executionResult.columns.length}):
                        </div>
                        <div className="manipola-execution-modal__columns-list">
                          {executionResult.columns.map((col, idx) => (
                            <span 
                              key={idx} 
                              className="manipola-execution-modal__column-tag"
                            >
                              {col}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="manipola-execution-modal__error">
                    <div className="manipola-execution-modal__error-title">
                      ✗ Errore esecuzione pipeline
                    </div>
                    <div className="manipola-execution-modal__error-message">
                      {executionResult.message}
                    </div>
                  </div>
                )}
              </div>
            ) : null}

            <div className="manipola-execution-modal__actions">
              <button 
                className="manipola-execution-modal__btn" 
                onClick={() => setShowExecutionModal(false)}
              >
                Chiudi
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

const ManipolaEditor = () => (
  <ReactFlowProvider>
    <ManipolaEditorContent />
  </ReactFlowProvider>
);

export default ManipolaEditor;