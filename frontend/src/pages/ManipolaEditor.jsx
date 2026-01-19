import React, { useState, useCallback, useEffect, useRef } from 'react';
import ReactFlow, { 
  Background, 
  Controls, 
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
import TransformNode from '../components/TransformNode';
import ResultNode from '../components/ResultNode';
import PreviewModal from '../components/PreviewModal';
import ExecutionModal from '../components/ExecutionModal';
import EditorSidebar from '../components/EditorSidebar';
import { useSchemaInference, usePreview, useExecution, useCanvasKeyboard } from '../hooks';
import '../styles/ManipolaEditor.css';

const nodeTypes = {
  project_source: ProjectSourceNode,
  file_source: FileSourceNode,
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
  
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [availableSources, setAvailableSources] = useState([]);
  const [availableFiles, setAvailableFiles] = useState([]);
  const [project, setProject] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  
  // ===== SCHEMA INFERENCE =====
  const { 
    schemas, 
    loading: schemasLoading, 
    refreshSchemas
  } = useSchemaInference(projectId);
  
  const nodesRef = useRef(nodes);
  const edgesRef = useRef(edges);
  
  useEffect(() => {
    nodesRef.current = nodes;
    edgesRef.current = edges;
  }, [nodes, edges]);

  // ===== SALVATAGGIO SILENZIOSO =====
  const handleSaveQuiet = useCallback(async () => {
    try {
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
            transform_op: node.data?.transform_op,
            config: node.data?.config,
            fileName: node.data?.fileName,
            format: node.data?.format,
            geomColumn: node.data?.geomColumn,
            group_by_enabled: node.data?.group_by_enabled,
            group_by_columns: node.data?.group_by_columns,
            aggregations: node.data?.aggregations
          }
        })),
        edges: currentEdges
      };
      
      await updateCanvas(projectId, canvasToSave);
      
    } catch (error) {
      console.error('[ManipolaEditor] Errore salvataggio canvas silenzioso:', error);
    }
  }, [projectId]);

  // ===== CUSTOM HOOKS =====
  
  // Hook Preview
  const preview = usePreview(projectId, handleSaveQuiet);

  // Hook Execution
  const execution = useExecution(projectId, {
    onBeforeExecute: async () => {
      const currentNodes = nodesRef.current;
      
      if (currentNodes.length === 0) {
        throw new Error('Canvas vuoto. Aggiungi nodi prima di eseguire.');
      }
      
      const resultNode = currentNodes.find(n => n.type === 'result');
      if (!resultNode) {
        throw new Error('Aggiungi un nodo Output prima di eseguire.');
      }
      
      const sourceNodes = currentNodes.filter(n => n.type === 'project_source' || n.type === 'file_source');
      if (sourceNodes.length === 0) {
        throw new Error('Aggiungi almeno una sorgente dati (progetto o file).');
      }
      
      await handleSaveCanvas();
    }
  });

  // Hook Keyboard (Delete/Backspace)
  useCanvasKeyboard({
    nodes,
    edges,
    onDeleteNode: handleDeleteNode,
    setEdges
  });

  // ===== EFFECTS =====

  // Propaga schemi ai nodi transform
  useEffect(() => {
    if (!schemas || Object.keys(schemas).length === 0) return;
    
    setNodes((nds) =>
      nds.map((node) => {
        const nodeSchema = schemas[node.id];
        if (nodeSchema && nodeSchema.length > 0) {
          const columns = nodeSchema.map(col => ({
            name: col.name,
            type: col.type,
            visible: true,
            source_node: col.source_node,
            is_calculated: col.is_calculated
          }));
          
          const currentCols = JSON.stringify(node.data?.columns || []);
          const newCols = JSON.stringify(columns);
          
          if (currentCols !== newCols) {
            return {
              ...node,
              data: {
                ...node.data,
                columns: columns
              }
            };
          }
        }
        return node;
      })
    );
  }, [schemas, setNodes]);

  // Aggiorna availableColumns nel ResultNode
  useEffect(() => {
    const resultNode = nodes.find(n => n.type === 'result');
    if (!resultNode) return;
    
    const availableColumns = [];
    
    for (const node of nodes) {
      const nodeSchema = schemas[node.id] || [];
      for (const col of nodeSchema) {
        availableColumns.push({
          name: col.name,
          type: col.type || 'str',
          source: node.id
        });
      }
    }
    
    // Fallback ai dati locali
    if (availableColumns.length === 0) {
      for (const node of nodes) {
        if ((node.type === 'project_source' || node.type === 'file_source' || node.type === 'transform') && node.data?.columns) {
          for (const col of node.data.columns) {
            availableColumns.push({
              name: col.name || col,
              type: col.type || 'str',
              source: node.data?.source_project_id || node.data?.file_source_id || 'transform'
            });
          }
        }
      }
    }
    
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
  }, [nodes, schemas, setNodes]);

  useEffect(() => {
    loadProjectAndSources();
  }, [projectId]);

  // ===== DATA LOADING =====

  const loadProjectAndSources = async () => {
    try {
      setLoading(true);
      
      const projectData = await getProject(projectId);
      setProject(projectData);
      
      // Carica canvas se presente
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
          } else if (node.type === 'transform') {
            return {
              ...node,
              data: {
                ...node.data,
                transform_op: node.data?.transform_op || 'join',
                config: node.data?.config || {},
                columns: node.data?.columns || [],
                onChange: (newData) => handleUpdateTransform(node.id, newData),
                onUpdateColumns: handleUpdateProjectSourceColumns,
                onDelete: handleDeleteNode
              }
            };
          } else if (node.type === 'result') {
            return {
              ...node,
              data: {
                ...node.data,
                isManipulate: true,
                fileName: node.data?.fileName || 'output',
                format: node.data?.format || 'csv',
                geomColumn: node.data?.geomColumn || '',
                group_by_enabled: node.data?.group_by_enabled || false,
                group_by_columns: node.data?.group_by_columns || [],
                aggregations: node.data?.aggregations || [],
                onUpdateFileName: handleUpdateResultFileName,
                onUpdateFormat: handleUpdateResultFormat,
                onUpdateGeomColumn: handleUpdateGeomColumn,
                onUpdateGroupBy: handleUpdateGroupBy,
                onPreview: () => preview.handlePreview(),
                onExecute: (options) => execution.handleExecute(options),
                onDelete: handleDeleteNode
              }
            };
          }
          return node;
        });
        
        setNodes(loadedNodes);
        setEdges(projectData.canvas.edges || []);
      }
      
      // Carica progetti SQL disponibili
      const projectsData = await listProjects();
      const sqlProjects = (projectsData.projects || []).filter(
        p => (p.project_type || 'estrai') === 'estrai'
      );
      setAvailableSources(sqlProjects);
      
      // Carica file esterni disponibili
      try {
        const filesData = await getFiles();
        setAvailableFiles(filesData.files || []);
      } catch (err) {
        console.warn("[ManipolaEditor] Errore caricamento file esterni:", err);
        setAvailableFiles([]);
      }
      
      // Refresh schemi dopo caricamento
      setTimeout(() => {
        refreshSchemas();
      }, 100);
      
    } catch (err) {
      console.error("[ManipolaEditor] Errore caricamento:", err);
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
    
    setTimeout(() => {
      handleSaveQuiet().then(() => refreshSchemas());
    }, 100);
  }, [setNodes, refreshSchemas, handleSaveQuiet]);

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

  const handleUpdateFileSourceColumns = useCallback((nodeId, columns) => {
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
    
    setTimeout(() => {
      handleSaveQuiet().then(() => refreshSchemas());
    }, 100);
  }, [setNodes, refreshSchemas, handleSaveQuiet]);

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
    
    setTimeout(() => {
      handleSaveQuiet().then(() => refreshSchemas());
    }, 300);
  }, [setNodes, refreshSchemas, handleSaveQuiet]);

  // ===== RESULT NODE HANDLERS =====

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

  const handleUpdateGeomColumn = useCallback((nodeId, geomColumn) => {
    setNodes((nds) =>
      nds.map((node) => {
        if (node.id === nodeId) {
          return { ...node, data: { ...node.data, geomColumn } };
        }
        return node;
      })
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

  function handleDeleteNode(nodeId) {
    setNodes((nds) => nds.filter((node) => node.id !== nodeId));
    setEdges((eds) => eds.filter((edge) => 
      edge.source !== nodeId && edge.target !== nodeId
    ));
  }

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
  }, [setNodes, handleUpdateTransform, handleUpdateProjectSourceColumns]);

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
        availableColumns: [],
        group_by_enabled: false,
        group_by_columns: [],
        aggregations: [],
        onUpdateFileName: handleUpdateResultFileName,
        onUpdateFormat: handleUpdateResultFormat,
        onUpdateGeomColumn: handleUpdateGeomColumn,
        onUpdateGroupBy: handleUpdateGroupBy,
        onPreview: () => preview.handlePreview(),
        onExecute: (options) => execution.handleExecute(options),
        onDelete: handleDeleteNode
      }
    };

    setNodes((nds) => nds.concat(newNode));
  }, [setNodes, handleUpdateResultFileName, handleUpdateResultFormat, handleUpdateGeomColumn, handleUpdateGroupBy, preview, execution]);

  // ===== SAVE =====

  const handleSaveCanvas = useCallback(async () => {
    try {
      setSaving(true);
      await handleSaveQuiet();
    } catch (error) {
      console.error('[ManipolaEditor] Errore salvataggio canvas:', error);
      alert('Errore durante il salvataggio');
    } finally {
      setSaving(false);
    }
  }, [handleSaveQuiet]);

  // ===== EDGE HANDLERS =====

  const onConnect = useCallback((params) => {
    setEdges((eds) => addEdge({ ...params, ...defaultEdgeOptions }, eds));
    
    setTimeout(() => {
      handleSaveQuiet().then(() => refreshSchemas());
    }, 100);
  }, [setEdges, refreshSchemas, handleSaveQuiet]);

  const onEdgeContextMenu = useCallback((event, edge) => {
    event.preventDefault();
    if (window.confirm('Eliminare questo collegamento?')) {
      setEdges((eds) => eds.filter((e) => e.id !== edge.id));
    }
  }, [setEdges]);

  // ===== DRAG & DROP =====

  const onDragStart = (event, type, data) => {
    event.dataTransfer.setData('application/reactflow', type);
    event.dataTransfer.setData('itemData', JSON.stringify(data));
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
      const itemDataStr = event.dataTransfer.getData('itemData');

      if (!type || !itemDataStr) return;

      const itemData = JSON.parse(itemDataStr);
      const position = screenToFlowPosition({ 
        x: event.clientX, 
        y: event.clientY 
      });

      let newNode;

      if (type === 'project_source') {
        const newNodeId = `source_${Date.now()}`;
        newNode = {
          id: newNodeId,
          type: 'project_source',
          position,
          data: { 
            source_project_id: itemData.id,
            columns: [],
            onChange: (val) => handleUpdateProjectSource(newNodeId, val),
            onUpdateColumns: handleUpdateProjectSourceColumns,
            onDelete: handleDeleteNode
          }
        };
      } else if (type === 'file_source') {
        const newNodeId = `file_${Date.now()}`;
        newNode = {
          id: newNodeId,
          type: 'file_source',
          position,
          data: { 
            file_source_id: itemData.id,
            columns: [],
            onUpdateColumns: handleUpdateFileSourceColumns,
            onDelete: handleDeleteNode
          }
        };
      }

      if (newNode) {
        setNodes((nds) => nds.concat(newNode));
        
        setTimeout(() => {
          handleSaveQuiet().then(() => refreshSchemas());
        }, 100);
      }
    },
    [screenToFlowPosition, setNodes, handleUpdateProjectSource, handleUpdateProjectSourceColumns, handleUpdateFileSourceColumns, refreshSchemas, handleSaveQuiet]
  );

  // ===== RENDER =====

  const handleBack = () => {
    navigate('/');
  };

  if (loading) {
    return <div className="manipola-loading">Caricamento progetto...</div>;
  }

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
          {schemasLoading && (
            <span className="manipola-toolbar__badge" style={{background: '#FF9800'}}>
              ⏳ Aggiornamento schemi...
            </span>
          )}
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
        {/* Sidebar - Usa componente condiviso */}
        <EditorSidebar
          variant="manipola"
          projects={availableSources}
          files={availableFiles}
          onDragStart={onDragStart}
          projectsConfig={{
            title: "📊 Sorgenti Progetto",
            subtitle: "Trascina un progetto SQL nel canvas",
            emptyMessage: "Nessun progetto SQL disponibile.",
            emptyHint: "Crea prima un progetto di tipo Estrai.",
            filterTypes: ['estrai', 'manipola']
          }}
          filesConfig={{
            title: "📁 File Esterni",
            subtitle: "Trascina un file nel canvas",
            emptyMessage: "Nessun file disponibile.",
            uploadLink: "/files",
            uploadLinkText: "Carica file esterni"
          }}
          quickActions={[
            {
              label: "Aggiungi Transform",
              icon: "+",
              onClick: handleAddTransformNode,
              variant: "transform"
            },
            {
              label: "Aggiungi Output",
              icon: "📁",
              onClick: handleAddResultNode,
              variant: "output"
            }
          ]}
        />

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
      <ExecutionModal
        isOpen={execution.showModal}
        onClose={() => execution.closeModal()}
        isExecuting={execution.isExecuting}
        result={execution.result}
        downloadUrl={execution.downloadUrl}
        title="🔄 Esecuzione Pipeline Manipola"
        projectType="manipola"
      />

      {/* ===== PREVIEW MODAL ===== */}
      <PreviewModal
        isOpen={preview.showModal}
        onClose={preview.closeModal}
        onRefresh={(limit) => preview.handlePreview(limit)}
        data={preview.data}
        loading={preview.loading}
        error={preview.error}
        title="Anteprima Pipeline Manipola"
        projectType="manipola"
      />
    </div>
  );
};

const ManipolaEditor = () => (
  <ReactFlowProvider>
    <ManipolaEditorContent />
  </ReactFlowProvider>
);

export default ManipolaEditor;