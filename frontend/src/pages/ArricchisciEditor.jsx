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
  getDownloadUrl
} from '../api/client';
import { getFiles } from '../api/files';

import ProjectSourceNode from '../components/ProjectSourceNode';
import FileSourceNode from '../components/FileSourceNode';
import ApiEnrichNode from '../components/ApiEnrichNode';
import ResultNode from '../components/ResultNode';
import ExecutionModal from '../components/ExecutionModal';
import { useExecution, useCanvasKeyboard } from '../hooks';
import '../styles/ArricchisciEditor.css';

const nodeTypes = {
  project_source: ProjectSourceNode,
  file_source: FileSourceNode,
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
  
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [availableSources, setAvailableSources] = useState([]);
  const [availableFiles, setAvailableFiles] = useState([]);
  const [project, setProject] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [exportFormat, setExportFormat] = useState('csv');
  
  const nodesRef = useRef(nodes);
  const edgesRef = useRef(edges);
  
  useEffect(() => {
    nodesRef.current = nodes;
    edgesRef.current = edges;
  }, [nodes, edges]);

  // ===== SALVATAGGIO =====
  
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
            file_source_id: node.data?.file_source_id,
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

  // ===== CUSTOM HOOKS =====

  // Hook Execution
  const execution = useExecution(projectId, {
    onBeforeExecute: async () => {
      const currentNodes = nodesRef.current;
      
      if (currentNodes.length === 0) {
        throw new Error('Canvas vuoto. Aggiungi nodi prima di eseguire.');
      }
      
      const sourceNodes = currentNodes.filter(n => n.type === 'project_source' || n.type === 'file_source');
      if (sourceNodes.length === 0) {
        throw new Error('Aggiungi almeno una sorgente dati (progetto o file).');
      }
      
      const apiNodes = currentNodes.filter(n => n.type === 'api_enrich');
      if (apiNodes.length === 0) {
        throw new Error('Aggiungi almeno un nodo API Enrich.');
      }
      
      await handleSaveCanvas();
    }
  });

  // Wrapper per passare formato export
  const handleExecuteWithFormat = useCallback((options = {}) => {
    execution.handleExecute({
      ...options,
      format: exportFormat
    });
  }, [execution, exportFormat]);

  // Hook Keyboard (Delete/Backspace)
  useCanvasKeyboard({
    nodes,
    edges,
    onDeleteNode: handleDeleteNode,
    setEdges
  });

  // ===== EFFECTS =====

  // Propaga colonne al Result Node
  useEffect(() => {
    const resultNode = nodes.find(n => n.type === 'result');
    if (!resultNode) return;
    
    const availableColumns = [];
    
    for (const node of nodes) {
      if (node.type === 'project_source' && node.data?.columns) {
        for (const col of node.data.columns) {
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
      } else if (node.type === 'file_source' && node.data?.columns) {
        for (const col of node.data.columns) {
          if (typeof col === 'object' && col !== null) {
            availableColumns.push({
              name: col.name || 'unknown',
              type: col.type || 'str',
              source: node.data?.file_source_id || 'file'
            });
          }
        }
      } else if (node.type === 'api_enrich' && node.data?.columns) {
        for (const col of node.data.columns) {
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
    
    const uniqueColumns = [...new Map(availableColumns.map(col => [col.name, col])).values()];
    
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

  useEffect(() => {
    loadProjectAndSources();
  }, [projectId]);

  // ===== DATA LOADING =====

  const loadProjectAndSources = async () => {
    try {
      setLoading(true);
      
      const projectData = await getProject(projectId);
      setProject(projectData);
      
      if (projectData.canvas && projectData.canvas.nodes) {
        const loadedNodes = projectData.canvas.nodes.map(node => {
          if (node.type === 'project_source') {
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
          } else if (node.type === 'file_source') {
            return {
              ...node,
              data: {
                ...node.data,
                file_source_id: node.data?.file_source_id || '',
                columns: node.data?.columns || [],
                onUpdateColumns: handleUpdateFileSourceColumns,
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
          } else if (node.type === 'result') {
            return {
              ...node,
              data: {
                ...node.data,
                isArricchisci: true,
                fileName: node.data?.fileName || 'output',
                format: node.data?.format || 'csv',
                availableColumns: [],
                onUpdateFileName: handleUpdateResultFileName,
                onUpdateFormat: handleUpdateResultFormat,
                onUpdateGeomColumn: handleUpdateResultGeomColumn,
                onExecute: handleExecuteWithFormat,
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
      const sourceProjects = (projectsData.projects || []).filter(
        p => ['estrai', 'manipola'].includes(p.project_type || 'estrai')
      );
      setAvailableSources(sourceProjects);
      
      try {
        const filesData = await getFiles();
        setAvailableFiles(filesData.files || []);
      } catch (err) {
        console.warn("[ArricchisciEditor] Errore caricamento file esterni:", err);
        setAvailableFiles([]);
      }
      
    } catch (err) {
      console.error("[ArricchisciEditor] Errore caricamento:", err);
      alert('Errore caricamento progetto');
    } finally {
      setLoading(false);
    }
  };

  // ===== NODE HANDLERS =====

  const handleUpdateProjectSource = useCallback((nodeId, projectSourceId) => {
    setNodes((nds) =>
      nds.map((node) => {
        if (node.id === nodeId) {
          return { ...node, data: { ...node.data, source_project_id: projectSourceId } };
        }
        return node;
      })
    );
  }, [setNodes]);

  const handleUpdateProjectSourceColumns = useCallback((nodeId, columns) => {
    setNodes((nds) =>
      nds.map((node) => {
        if (node.id === nodeId) {
          return { ...node, data: { ...node.data, columns } };
        }
        return node;
      })
    );
  }, [setNodes]);

  const handleUpdateFileSourceColumns = useCallback((nodeId, columns) => {
    setNodes((nds) =>
      nds.map((node) => {
        if (node.id === nodeId) {
          return { ...node, data: { ...node.data, columns } };
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
    setExportFormat(newFormat);
  }, [setNodes]);

  const handleUpdateResultGeomColumn = useCallback((nodeId, geomColumn) => {
    setNodes((nds) =>
      nds.map((node) => {
        if (node.id === nodeId) {
          return { ...node, data: { ...node.data, geomColumn } };
        }
        return node;
      })
    );
  }, [setNodes]);

  function handleDeleteNode(nodeId) {
    setNodes((nds) => nds.filter((node) => node.id !== nodeId));
    setEdges((eds) => eds.filter((edge) => edge.source !== nodeId && edge.target !== nodeId));
  }

  const handleAddApiEnrichNode = useCallback(() => {
    const newNodeId = `api_enrich_${Date.now()}`;
    const newNode = {
      id: newNodeId,
      type: 'api_enrich',
      position: { x: Math.random() * 300 + 200, y: Math.random() * 200 + 100 },
      data: {
        api_id: '',
        param_mapping: {},
        columns: [],
        onDelete: handleDeleteNode
      }
    };
    setNodes((nds) => nds.concat(newNode));
  }, [setNodes]);

  const handleAddResultNode = useCallback(() => {
    if (nodesRef.current.some(n => n.type === 'result')) {
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
        onExecute: handleExecuteWithFormat,
        onDelete: handleDeleteNode
      }
    };

    setNodes((nds) => nds.concat(newNode));
  }, [setNodes, handleUpdateResultFileName, handleUpdateResultFormat, handleUpdateResultGeomColumn, handleExecuteWithFormat]);

  // ===== EDGE & DRAG HANDLERS =====

  const onConnect = useCallback((params) => {
    setEdges((eds) => addEdge({ ...params, ...defaultEdgeOptions }, eds));
  }, [setEdges]);

  const onEdgeContextMenu = useCallback((event, edge) => {
    event.preventDefault();
    if (window.confirm('Eliminare questo collegamento?')) {
      setEdges((eds) => eds.filter((e) => e.id !== edge.id));
    }
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
      const position = screenToFlowPosition({ x: event.clientX, y: event.clientY });
      const newNodeId = `${nodeType}_${Date.now()}`;
      let newNode;

      if (nodeType === 'project_source') {
        newNode = {
          id: newNodeId,
          type: 'project_source',
          position,
          data: { 
            source_project_id: nodeData.source_project_id || nodeData.id,
            columns: [],
            onChange: (val) => handleUpdateProjectSource(newNodeId, val),
            onUpdateColumns: handleUpdateProjectSourceColumns,
            onDelete: handleDeleteNode
          }
        };
      } else if (nodeType === 'file_source') {
        newNode = {
          id: newNodeId,
          type: 'file_source',
          position,
          data: { 
            file_source_id: nodeData.file_source_id || nodeData.id,
            columns: [],
            onUpdateColumns: handleUpdateFileSourceColumns,
            onDelete: handleDeleteNode
          }
        };
      } else if (nodeType === 'api_enrich') {
        newNode = {
          id: newNodeId,
          type: 'api_enrich',
          position,
          data: { api_id: '', param_mapping: {}, columns: [], onDelete: handleDeleteNode }
        };
      }

      if (newNode) {
        setNodes((nds) => nds.concat(newNode));
      }
    },
    [screenToFlowPosition, setNodes, handleUpdateProjectSource, handleUpdateProjectSourceColumns, handleUpdateFileSourceColumns]
  );

  // ===== RENDER =====

  const handleBack = () => navigate('/');

  if (loading) {
    return <div className="arricchisci-loading">Caricamento progetto...</div>;
  }

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
      {/* TOOLBAR */}
      <div className="arricchisci-toolbar">
        <div className="arricchisci-toolbar__left">
          <button className="arricchisci-toolbar__btn arricchisci-toolbar__btn--back" onClick={handleBack}>
            ← Indietro
          </button>
          <button className="arricchisci-toolbar__btn arricchisci-toolbar__btn--api" onClick={() => navigate('/api-connections')}>
            🔌 Gestione API
          </button>
        </div>
        
        <div className="arricchisci-toolbar__center">
          <h2 className="arricchisci-toolbar__title">{project.metadata?.name}</h2>
          <span className="arricchisci-toolbar__badge">ARRICCHISCI</span>
        </div>
        
        <div className="arricchisci-toolbar__right">
          <button className="arricchisci-toolbar__btn arricchisci-toolbar__btn--api-node" onClick={handleAddApiEnrichNode}>
            🔗 + API Enrich
          </button>
          <button className="arricchisci-toolbar__btn arricchisci-toolbar__btn--output" onClick={handleAddResultNode}>
            📁 Output
          </button>
          <button className="arricchisci-toolbar__btn arricchisci-toolbar__btn--save" onClick={handleSaveCanvas} disabled={saving}>
            {saving ? 'Salvataggio...' : '💾 Salva'}
          </button>
        </div>
      </div>

      {/* MAIN */}
      <div className="arricchisci-main">
        {/* Sidebar */}
        <div className="arricchisci-sidebar">
          <div className="arricchisci-sidebar__header">
            <h3 className="arricchisci-sidebar__title">📊 Sorgenti Dati</h3>
            <p className="arricchisci-sidebar__subtitle">Trascina un progetto nel canvas</p>
          </div>
          
          <div className="arricchisci-sidebar__sources">
            {availableSources.length === 0 ? (
              <div className="arricchisci-sidebar__empty">
                Nessun progetto disponibile.<br />Crea prima un progetto Estrai o Manipola.
              </div>
            ) : (
              availableSources.map((proj) => (
                <div
                  key={proj.id}
                  className="arricchisci-sidebar__source-item"
                  draggable
                  onDragStart={(e) => onDragStart(e, 'project_source', { source_project_id: proj.id, id: proj.id })}
                >
                  <span className="arricchisci-sidebar__source-icon">{proj.project_type === 'manipola' ? '🔄' : '📊'}</span>
                  <span className="arricchisci-sidebar__source-name">{proj.name}</span>
                  <span className={`arricchisci-sidebar__source-badge arricchisci-sidebar__source-badge--${proj.project_type || 'estrai'}`}>
                    {(proj.project_type || 'estrai').toUpperCase()}
                  </span>
                </div>
              ))
            )}
          </div>

          <div className="arricchisci-sidebar__header arricchisci-sidebar__header--files">
            <h3 className="arricchisci-sidebar__title">📁 File Esterni</h3>
            <p className="arricchisci-sidebar__subtitle">Trascina un file nel canvas</p>
          </div>
          
          <div className="arricchisci-sidebar__sources">
            {availableFiles.length === 0 ? (
              <div className="arricchisci-sidebar__empty">
                Nessun file disponibile.<br /><a href="/files" style={{color: '#FF6B35'}}>Carica file esterni</a>
              </div>
            ) : (
              availableFiles.map((file) => (
                <div
                  key={file.id}
                  className="arricchisci-sidebar__source-item arricchisci-sidebar__source-item--file"
                  draggable
                  onDragStart={(e) => onDragStart(e, 'file_source', { file_source_id: file.id, id: file.id })}
                >
                  <span className="arricchisci-sidebar__source-icon">
                    {file.format === 'csv' ? '📄' : file.format === 'xlsx' ? '📊' : ['geojson', 'gpkg', 'shp', 'gdb'].includes(file.format) ? '🗺️' : '📁'}
                  </span>
                  <span className="arricchisci-sidebar__source-name">{file.name}</span>
                  <span className="arricchisci-sidebar__source-badge arricchisci-sidebar__source-badge--file">{file.format?.toUpperCase()}</span>
                </div>
              ))
            )}
          </div>

          <div className="arricchisci-sidebar__actions">
            <h4 className="arricchisci-sidebar__actions-title">Azioni Rapide</h4>
            <button className="arricchisci-sidebar__action-btn arricchisci-sidebar__action-btn--api" onClick={handleAddApiEnrichNode}>
              🔗 Aggiungi API Enrich
            </button>
            <button className="arricchisci-sidebar__action-btn arricchisci-sidebar__action-btn--output" onClick={handleAddResultNode}>
              📁 Aggiungi Output
            </button>
          </div>
        </div>

        {/* Canvas */}
        <div className="arricchisci-canvas" onDrop={onDrop} onDragOver={onDragOver}>
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
                if (node.type === 'file_source') return '#3F51B5';
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

      {/* EXECUTION MODAL */}
      <ExecutionModal
        isOpen={execution.showModal}
        onClose={() => execution.closeModal()}
        isExecuting={execution.isExecuting}
        result={execution.result}
        downloadUrl={execution.downloadUrl}
        title="🔗 Esegui Arricchimento"
        projectType="arricchisci"
      />
    </div>
  );
};

const ArricchisciEditor = () => (
  <ReactFlowProvider>
    <ArricchisciEditorContent />
  </ReactFlowProvider>
);

export default ArricchisciEditor;