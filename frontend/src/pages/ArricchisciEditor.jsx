import React, { useState, useCallback, useEffect, useRef } from 'react';
import ReactFlow, { 
  Background, 
  Controls, 
  MiniMap,
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
  executeProjectQuery,
  getDownloadUrl
} from '../api/client';

import ProjectSourceNode from '../components/ProjectSourceNode';
import ApiEnrichNode from '../components/ApiEnrichNode';
import ResultNode from '../components/ResultNode';
import '../styles/ArricchisciEditor.css';

const nodeTypes = {
  project_source: ProjectSourceNode,
  api_enrich: ApiEnrichNode,
  result: ResultNode,
};

const defaultEdgeOptions = {
  type: 'smoothstep',
  animated: true,
  style: { 
    stroke: '#FF6B35',
    strokeWidth: 2
  },
  markerEnd: {
    type: MarkerType.ArrowClosed,
    width: 20,
    height: 20,
    color: '#FF6B35',
  },
};

const ArricchisciEditorContent = () => {
  const { projectId } = useParams();
  const navigate = useNavigate();
  const { screenToFlowPosition } = useReactFlow();
  const reactFlowInstance = useReactFlow();
  
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [availableSources, setAvailableSources] = useState([]);
  const [project, setProject] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  
  // Stati per modal esecuzione
  const [showExecutionModal, setShowExecutionModal] = useState(false);
  const [executionResult, setExecutionResult] = useState(null);
  const [isExecuting, setIsExecuting] = useState(false);
  const [exportFormat, setExportFormat] = useState('csv');
  
  const nodesRef = useRef(nodes);
  const edgesRef = useRef(edges);
  
  useEffect(() => {
    nodesRef.current = nodes;
    edgesRef.current = edges;
  }, [nodes, edges]);

  // ===== PROPAGA COLONNE AL RESULT NODE =====
  // Raccoglie colonne da project_source e api_enrich per mostrare formati geo
  useEffect(() => {
    const resultNode = nodes.find(n => n.type === 'result');
    if (!resultNode) return;
    
    // Raccogli colonne da tutti i nodi
    const availableColumns = [];
    
    for (const node of nodes) {
      if (node.type === 'project_source' && node.data?.columns) {
        for (const col of node.data.columns) {
          // ProjectSourceNode: colonne sono oggetti {name, type, visible, ...}
          if (typeof col === 'object' && col !== null) {
            availableColumns.push({
              name: col.name || col.alias || 'unknown',
              type: col.type || col.dtype || 'str',
              source: node.data?.source_project_id || 'source'
            });
          } else if (typeof col === 'string') {
            availableColumns.push({
              name: col,
              type: 'str',
              source: node.data?.source_project_id || 'source'
            });
          }
        }
      } else if (node.type === 'api_enrich' && node.data?.columns) {
        for (const col of node.data.columns) {
          // ApiEnrichNode: colonne sono oggetti {name, type, visible, fromApi}
          if (typeof col === 'object' && col !== null) {
            availableColumns.push({
              name: col.name || 'unknown',
              type: col.type || 'str',
              source: 'api_enrich',
              fromApi: col.fromApi || false
            });
          }
        }
      }
    }
    
    // Deduplica per nome (mantieni l'ultima occorrenza che potrebbe avere tipo più preciso)
    const uniqueColumns = [...new Map(availableColumns.map(col => [col.name, col])).values()];
    
    // Aggiorna solo se le colonne sono cambiate
    const currentColumns = resultNode.data?.availableColumns || [];
    const columnsChanged = JSON.stringify(uniqueColumns) !== JSON.stringify(currentColumns);
    
    if (columnsChanged && uniqueColumns.length > 0) {
      setNodes((nds) =>
        nds.map((node) => {
          if (node.type === 'result') {
            return {
              ...node,
              data: {
                ...node.data,
                availableColumns: uniqueColumns
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
                ...node.data,
                isArricchisci: true,
                fileName: node.data?.fileName || 'output',
                format: node.data?.format || 'csv',
                availableColumns: node.data?.availableColumns || [],
                geomColumn: node.data?.geomColumn || null,
                onUpdateFileName: handleUpdateResultFileName,
                onUpdateFormat: handleUpdateResultFormat,
                onUpdateGeomColumn: handleUpdateResultGeomColumn,
                onExecute: handleExecute,
                onDelete: handleDeleteNode
              }
            };
          } else if (node.type === 'project_source') {
            return {
              ...node,
              data: {
                ...node.data,
                source_project_id: node.data?.source_project_id || '',
                columns: node.data?.columns || [],
                onChange: (val) => handleUpdateProjectSource(node.id, val),
                onUpdateColumns: handleUpdateProjectSourceColumns,
                onDelete: handleDeleteNode
              }
            };
          } else if (node.type === 'api_enrich') {
            return {
              ...node,
              data: {
                ...node.data,
                api_id: node.data?.api_id || '',
                param_mapping: node.data?.param_mapping || {},
                columns: node.data?.columns || [],
                onDelete: handleDeleteNode
              }
            };
          }
          return node;
        });
        
        setNodes(loadedNodes);
        setEdges(projectData.canvas.edges || []);
      }
      
      // Carica progetti disponibili (Estrai e Manipola)
      const projectsData = await listProjects();
      const sourceProjects = (projectsData.projects || []).filter(
        p => p.project_type === 'estrai' || p.project_type === 'manipola'
      );
      setAvailableSources(sourceProjects);
      
    } catch (err) {
      console.error("[ArricchisciEditor] Errore caricamento:", err);
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

  const handleUpdateResultGeomColumn = useCallback((nodeId, geomColumn) => {
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

  const handleDeleteNode = useCallback((nodeId) => {
    setNodes((nds) => nds.filter((node) => node.id !== nodeId));
    setEdges((eds) => eds.filter((edge) => 
      edge.source !== nodeId && edge.target !== nodeId
    ));
  }, [setNodes, setEdges]);

  const handleAddResultNode = useCallback(() => {
    const currentNodes = nodesRef.current;
    if (currentNodes.some(n => n.type === 'result')) {
      alert('Puoi avere solo un nodo risultato per progetto');
      return;
    }

    const newNode = {
      id: `result_${Date.now()}`,
      type: 'result',
      position: { x: 600, y: 200 },
      data: {
        isArricchisci: true,
        fileName: 'output',
        format: 'csv',
        availableColumns: [],
        onUpdateFileName: handleUpdateResultFileName,
        onUpdateFormat: handleUpdateResultFormat,
        onUpdateGeomColumn: handleUpdateResultGeomColumn,
        onExecute: handleExecute,
        onDelete: handleDeleteNode
      }
    };

    setNodes((nds) => nds.concat(newNode));
  }, [setNodes, handleUpdateResultFileName, handleUpdateResultFormat, handleUpdateResultGeomColumn, handleDeleteNode]);

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
            api_id: node.data?.api_id,
            param_mapping: node.data?.param_mapping,
            fileName: node.data?.fileName,
            format: node.data?.format
          }
        })),
        edges: currentEdges
      };
      
      await updateCanvas(projectId, canvasToSave);
      
    } catch (error) {
      console.error('[ArricchisciEditor] Errore salvataggio canvas:', error);
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
      
      const sourceNodes = currentNodes.filter(n => n.type === 'project_source');
      if (sourceNodes.length === 0) {
        throw new Error('Aggiungi almeno un progetto sorgente.');
      }
      
      const apiNodes = currentNodes.filter(n => n.type === 'api_enrich');
      if (apiNodes.length === 0) {
        throw new Error('Aggiungi almeno un nodo API Enrich.');
      }
      
      // Salva prima di eseguire
      await handleSaveCanvas();
      
      // Esegui con formato selezionato e force_recalculate
      const response = await executeProjectQuery(projectId, { 
        format: exportFormat,
        force_recalculate: options.force_recalculate || false
      });
      
      setExecutionResult(response);
      
    } catch (error) {
      console.error('[ArricchisciEditor] Errore esecuzione:', error);
      setExecutionResult({
        success: false,
        message: error.response?.data?.detail || error.message || 'Errore esecuzione arricchimento'
      });
    } finally {
      setIsExecuting(false);
    }
  }, [projectId, handleSaveCanvas, exportFormat]);

  const onConnect = useCallback((params) => {
    setEdges((eds) => addEdge({ ...params, ...defaultEdgeOptions }, eds));
  }, [setEdges]);

  const onDragStart = (event, nodeType, nodeData = {}) => {
    event.dataTransfer.setData('application/reactflow', nodeType);
    event.dataTransfer.setData('nodeData', JSON.stringify(nodeData));
    event.dataTransfer.effectAllowed = 'move';
  };

  const onDragOver = useCallback((event) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  }, []);

  const onDrop = useCallback(
    (event) => {
      event.preventDefault();

      const nodeType = event.dataTransfer.getData('application/reactflow');
      const nodeDataStr = event.dataTransfer.getData('nodeData');

      if (!nodeType) return;

      const nodeData = nodeDataStr ? JSON.parse(nodeDataStr) : {};
      const position = screenToFlowPosition({ 
        x: event.clientX, 
        y: event.clientY 
      });

      const newNodeId = `${nodeType}_${Date.now()}`;
      let newNode;

      if (nodeType === 'project_source') {
        newNode = {
          id: newNodeId,
          type: 'project_source',
          position,
          data: { 
            source_project_id: nodeData.source_project_id,
            columns: [],
            onChange: (val) => handleUpdateProjectSource(newNodeId, val),
            onUpdateColumns: handleUpdateProjectSourceColumns,
            onDelete: handleDeleteNode
          }
        };
      } else if (nodeType === 'api_enrich') {
        newNode = {
          id: newNodeId,
          type: 'api_enrich',
          position,
          data: {
            api_id: '',
            param_mapping: {},
            columns: [],
            onDelete: handleDeleteNode
          }
        };
      } else if (nodeType === 'result') {
        // Controlla se esiste già un nodo risultato
        if (nodesRef.current.some(n => n.type === 'result')) {
          alert('Puoi avere solo un nodo risultato per progetto');
          return;
        }
        
        newNode = {
          id: newNodeId,
          type: 'result',
          position,
          data: {
            isArricchisci: true,
            fileName: 'output',
            format: 'csv',
            availableColumns: [],
            onUpdateFileName: handleUpdateResultFileName,
            onUpdateFormat: handleUpdateResultFormat,
            onUpdateGeomColumn: handleUpdateResultGeomColumn,
            onExecute: handleExecute,
            onDelete: handleDeleteNode
          }
        };
      }

      if (newNode) {
        setNodes((nds) => nds.concat(newNode));
      }
    },
    [screenToFlowPosition, setNodes, handleUpdateProjectSource, handleUpdateProjectSourceColumns, handleDeleteNode, handleUpdateResultFileName, handleUpdateResultFormat, handleUpdateResultGeomColumn, handleExecute]
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
    return <div className="arricchisci-loading">⏳ Caricamento progetto...</div>;
  }

  // Error state
  if (!project) {
    return (
      <div className="arricchisci-error">
        <p>Progetto non trovato</p>
        <button onClick={handleBack}>Torna alla lista</button>
      </div>
    );
  }

  return (
    <div className="arricchisci-editor">
      {/* ===== TOOLBAR ===== */}
      <div className="arricchisci-toolbar">
        <div className="arricchisci-toolbar__left">
          <button 
            className="arricchisci-toolbar__btn arricchisci-toolbar__btn--back"
            onClick={handleBack}
          >
            ← Indietro
          </button>
        </div>
        
        <div className="arricchisci-toolbar__center">
          <h2 className="arricchisci-toolbar__title">{project.metadata?.name}</h2>
          <span className="arricchisci-toolbar__badge">ARRICCHISCI</span>
        </div>
        
        <div className="arricchisci-toolbar__right">
          <button 
            className="arricchisci-toolbar__btn arricchisci-toolbar__btn--save"
            onClick={handleSaveCanvas}
            disabled={saving}
          >
            {saving ? '⏳ Salvataggio...' : '💾 Salva'}
          </button>
          <button 
            className="arricchisci-toolbar__btn arricchisci-toolbar__btn--execute"
            onClick={() => setShowExecutionModal(true)}
          >
            ▶ Esegui
          </button>
        </div>
      </div>

      {/* ===== MAIN CONTENT ===== */}
      <div className="arricchisci-main">
        {/* Sidebar */}
        <div className="arricchisci-sidebar">
          {/* Sorgenti Dati */}
          <div className="arricchisci-sidebar__section">
            <div className="arricchisci-sidebar__section-title">
              📊 Sorgente Dati
            </div>
            
            {availableSources.length === 0 ? (
              <div className="arricchisci-sidebar__empty">
                Nessun progetto disponibile.<br/>
                Crea prima un progetto Estrai o Manipola.
              </div>
            ) : (
              availableSources.map((proj) => (
                <div
                  key={proj.id}
                  className={`arricchisci-sidebar__source-item arricchisci-sidebar__source-item--${proj.project_type}`}
                  draggable
                  onDragStart={(e) => onDragStart(e, 'project_source', { 
                    source_project_id: proj.id 
                  })}
                >
                  <div className="arricchisci-sidebar__source-header">
                    <span>{proj.project_type === 'manipola' ? '🔄' : '📥'}</span>
                    <span>{proj.name}</span>
                  </div>
                  <div className="arricchisci-sidebar__source-type">
                    {proj.project_type === 'manipola' ? 'Manipola' : 'Estrai'}
                  </div>
                </div>
              ))
            )}
          </div>

          {/* API Enrichment */}
          <div className="arricchisci-sidebar__section">
            <div className="arricchisci-sidebar__section-title">
              🔗 Arricchimento
            </div>
            
            <div
              className="arricchisci-sidebar__api-item"
              draggable
              onDragStart={(e) => onDragStart(e, 'api_enrich', {})}
            >
              <div className="arricchisci-sidebar__api-header">
                <span>🔗</span>
                <span>API Enrich</span>
              </div>
              <div className="arricchisci-sidebar__api-desc">
                Arricchisci con API esterna
              </div>
            </div>
          </div>

          {/* Output */}
          <div className="arricchisci-sidebar__section">
            <div className="arricchisci-sidebar__section-title">
              📁 Output
            </div>
            
            <div
              className="arricchisci-sidebar__output-item"
              draggable
              onDragStart={(e) => onDragStart(e, 'result', {})}
            >
              <div className="arricchisci-sidebar__api-header">
                <span>📁</span>
                <span>Output</span>
              </div>
              <div className="arricchisci-sidebar__api-desc">
                Risultato finale
              </div>
            </div>
          </div>

          {/* Help */}
          <div className="arricchisci-sidebar__section">
            <div className="arricchisci-sidebar__help">
              <div className="arricchisci-sidebar__help-title">
                💡 Come funziona
              </div>
              <ol className="arricchisci-sidebar__help-list">
                <li>Trascina una sorgente dati</li>
                <li>Aggiungi nodi API Enrich</li>
                <li>Collega i nodi</li>
                <li>Configura il mapping</li>
                <li>Esegui!</li>
              </ol>
            </div>
          </div>
        </div>

        {/* Canvas Area */}
        <div 
          className="arricchisci-canvas"
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
            <Background color="#ddd" gap={20} />
            <Controls />
            <MiniMap 
              style={{ height: 100, background: '#f5f5f5' }}
              nodeColor={(node) => {
                if (node.type === 'project_source') return '#4CAF50';
                if (node.type === 'api_enrich') return '#FF6B35';
                if (node.type === 'result') return '#2196F3';
                return '#999';
              }}
              zoomable
              pannable
            />
          </ReactFlow>
        </div>
      </div>

      {/* ===== EXECUTION MODAL ===== */}
      {showExecutionModal && (
        <div className="modal-overlay" onClick={() => !isExecuting && setShowExecutionModal(false)}>
          <div 
            className="modal arricchisci-execution-modal" 
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="arricchisci-execution-modal__title">
              🔗 Esegui Arricchimento
            </h3>

            {isExecuting ? (
              <div className="arricchisci-execution-modal__loading">
                <div className="arricchisci-execution-modal__spinner">⏳</div>
                <p className="arricchisci-execution-modal__loading-text">
                  Esecuzione arricchimento in corso...
                </p>
              </div>
            ) : executionResult ? (
              <div>
                {executionResult.success ? (
                  <div>
                    <div className="arricchisci-execution-modal__success">
                      <div className="arricchisci-execution-modal__success-icon">✅</div>
                      <h4 className="arricchisci-execution-modal__success-title">
                        Arricchimento completato!
                      </h4>
                      <p className="arricchisci-execution-modal__success-message">
                        {executionResult.message || 'Arricchimento eseguito con successo'}
                      </p>
                    </div>

                    <div className="arricchisci-execution-modal__stats">
                      <div className="arricchisci-execution-modal__stat">
                        <div className="arricchisci-execution-modal__stat-label">
                          Righe processate
                        </div>
                        <div className="arricchisci-execution-modal__stat-value">
                          {(executionResult.rows_count || executionResult.rows_processed || 0).toLocaleString()}
                        </div>
                      </div>
                      <div className="arricchisci-execution-modal__stat">
                        <div className="arricchisci-execution-modal__stat-label">
                          Colonne output
                        </div>
                        <div className="arricchisci-execution-modal__stat-value">
                          {executionResult.columns?.length || 0}
                        </div>
                      </div>
                    </div>

                    {executionResult.output_file && (
                      <div className="arricchisci-execution-modal__output">
                        <div className="arricchisci-execution-modal__output-label">
                          📄 File generato
                        </div>
                        <div className="arricchisci-execution-modal__output-path">
                          {executionResult.output_file}
                        </div>
                      </div>
                    )}

                    {/* ========== DOWNLOAD BUTTON ========== */}
                    <a 
                      href={getDownloadUrl(projectId)}
                      className="arricchisci-execution-modal__download-btn"
                      download
                    >
                      ⬇️ Scarica File
                    </a>

                    {executionResult.columns && executionResult.columns.length > 0 && (
                      <div className="arricchisci-execution-modal__columns">
                        <div className="arricchisci-execution-modal__columns-label">
                          Colonne output ({executionResult.columns.length}):
                        </div>
                        <div className="arricchisci-execution-modal__columns-list">
                          {executionResult.columns.map((col, idx) => (
                            <span 
                              key={idx} 
                              className="arricchisci-execution-modal__column-tag"
                            >
                              {col}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="arricchisci-execution-modal__error">
                    <div className="arricchisci-execution-modal__error-icon">❌</div>
                    <h4 className="arricchisci-execution-modal__error-title">
                      Errore esecuzione
                    </h4>
                    <p className="arricchisci-execution-modal__error-message">
                      {executionResult.message || executionResult.error || 'Errore sconosciuto'}
                    </p>
                  </div>
                )}

                <div className="arricchisci-execution-modal__actions">
                  <button 
                    className="arricchisci-execution-modal__btn arricchisci-execution-modal__btn--close"
                    onClick={() => {
                      setShowExecutionModal(false);
                      setExecutionResult(null);
                    }}
                  >
                    Chiudi
                  </button>
                </div>
              </div>
            ) : (
              <>
                <div>
                  <label style={{ display: 'block', marginBottom: '10px', fontWeight: '500' }}>
                    Formato output:
                  </label>
                  <div className="arricchisci-execution-modal__format-grid">
                    {['csv', 'xlsx', 'geojson', 'parquet'].map(fmt => (
                      <div
                        key={fmt}
                        className={`arricchisci-execution-modal__format-option ${exportFormat === fmt ? 'arricchisci-execution-modal__format-option--selected' : ''}`}
                        onClick={() => setExportFormat(fmt)}
                      >
                        <div className="arricchisci-execution-modal__format-label">{fmt}</div>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="arricchisci-execution-modal__actions">
                  <button 
                    className="arricchisci-execution-modal__btn arricchisci-execution-modal__btn--cancel"
                    onClick={() => setShowExecutionModal(false)}
                  >
                    Annulla
                  </button>
                  <button 
                    className="arricchisci-execution-modal__btn arricchisci-execution-modal__btn--execute"
                    onClick={() => handleExecute(null)}
                  >
                    ▶ Esegui
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

const ArricchisciEditor = () => (
  <ReactFlowProvider>
    <ArricchisciEditorContent />
  </ReactFlowProvider>
);

export default ArricchisciEditor;