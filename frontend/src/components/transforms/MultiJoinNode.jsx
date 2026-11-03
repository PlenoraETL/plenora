/**
 * MultiJoinNode.jsx - Nodo per join multipli (N sorgenti)
 * 
 * VERSIONE SEMPLIFICATA: Nessuna propagazione.
 * Legge data.inputColumns popolato da useColumnPropagation.
 */
import React, { memo, useState, useMemo, useCallback } from 'react';
import { Position, useReactFlow } from '@xyflow/react';
import useCanvasStore from '../../store/useCanvasStore';
import './MultiJoinNode.css';
import RunHistoryModal from '../modals/RunHistoryModal';
import { useConfirm } from '../../context/ConfirmContext';
import { dataFlowEdges } from '../../utils/runtimeBindingEdges';
import NodeHandle from '../canvas/NodeHandle';

const MultiJoinNode = memo(({ data, id }) => {
  const { getEdges, getNodes } = useReactFlow();
  
  // Configurazione
  const config = data.config || {};
  const joinKeys = config.join_keys || [];
  const joinKeyIds = config.join_key_ids || [];
  const joinPairs = Array.isArray(config.join_pairs) ? config.join_pairs : [];
  const joinType = config.join_type || 'inner';
  const [isRunHistoryOpen, setIsRunHistoryOpen] = useState(false);
  const projectId = useCanvasStore(s => s.projectId);

  // useColumnPropagation popola data.inputColumns con tutte le colonne da tutti i predecessori
  const inputColumns = useMemo(() => {
    return data.inputColumns || [];
  }, [data.inputColumns]);

  // Leggiamo info sulle sorgenti per mostrare nella UI
  const sources = useMemo(() => {
    const edges = dataFlowEdges(getEdges());
    const nodes = getNodes();
    const incomingEdges = edges.filter(e => e.target === id);
    
    return incomingEdges.map((edge, idx) => {
      const sourceNode = nodes.find(n => n.id === edge.source);
      if (!sourceNode) return null;
      
      // Colonne del nodo sorgente (output del predecessore)
      const cols = sourceNode.data?.columns || [];
      const columnItems = cols.map(c => (
        typeof c === 'string'
          ? { name: c, id: null }
          : { ...c, name: c.alias || c.name, id: c.id || c.column_id || null }
      ));
      
      // Nome sorgente
      let name = sourceNode.data?.source_project_id || 
                 sourceNode.data?.file_name || 
                 sourceNode.data?.label ||
                 `Sorgente ${idx + 1}`;
      
      if (name.length > 25) {
        name = name.substring(0, 22) + '...';
      }
      
      return {
        id: sourceNode.id,
        name,
        type: sourceNode.type,
        columns: columnItems
      };
    }).filter(Boolean);
  }, [id, getEdges, getNodes, data.inputColumns]); // Trigger anche su inputColumns change

  const commonColumns = useMemo(() => {
    if (sources.length < 2) return [];
    
    // Trova colonne presenti in TUTTE le sorgenti
    const firstCols = sources[0]?.columns || [];
    return firstCols.filter(col =>
      sources.every(src => src.columns.some(c => c.name === col.name))
    );
  }, [sources]);

  const updateNodeData = useCanvasStore(s => s.updateNodeData);
  const deleteNode     = useCanvasStore(s => s.deleteNode);
  const confirm        = useConfirm();

  const updateConfig = useCallback((field, value) => {
    const patch = typeof field === 'object' && field !== null
      ? field
      : { [field]: value };
    updateNodeData(id, { config: { ...config, ...patch } });
  }, [id, updateNodeData, config]);

  const toggleJoinKey = useCallback((col) => {
    const colName = typeof col === 'string' ? col : col.name;
    const colId = typeof col === 'string' ? null : col.id;
    const newKeys = joinKeys.includes(colName)
      ? joinKeys.filter(k => k !== colName)
      : [...joinKeys, colName];
    const newIds = joinKeys.includes(colName)
      ? joinKeyIds.filter((_, idx) => joinKeys[idx] !== colName)
      : [...joinKeyIds, colId].filter(Boolean);
    updateConfig({ join_keys: newKeys, join_key_ids: newIds });
  }, [joinKeys, joinKeyIds, updateConfig]);

  const handleDelete = useCallback(async (e) => {
    e.stopPropagation();
    e.preventDefault();
    const ok = await confirm({
      title: 'Elimina nodo',
      message: 'Eliminare questo nodo?',
      confirmText: 'Elimina',
      variant: 'danger',
    });
    if (!ok) return;
    deleteNode(id);
  }, [id, deleteNode, confirm]);

  // Mostra preview delle colonne che usciranno (per info utente)
  const outputPreview = useMemo(() => {
    const validPairs = joinPairs.filter(p => p && p.left && p.right);
    const pairMode = sources.length === 2 && validPairs.length > 0;
    const leftKeys = pairMode ? validPairs.map(p => p.left) : joinKeys;
    const rightKeys = pairMode ? validPairs.map(p => p.right) : joinKeys;
    if (sources.length === 0 || leftKeys.length === 0) return [];
    
    const seen = new Set();
    const result = [];
    
    // Prima le chiavi di join
    leftKeys.forEach(key => {
      if (!seen.has(key)) {
        seen.add(key);
        result.push(key);
      }
    });
    
    // Poi le altre colonne da ogni sorgente
    sources.forEach((src, sourceIdx) => {
      const keysForSource = pairMode && sourceIdx === 1 ? rightKeys : leftKeys;
      src.columns.forEach(col => {
        if (keysForSource.includes(col.name)) {
          return;
        }
        if (!seen.has(col.name)) {
          seen.add(col.name);
          result.push(col.name);
        }
      });
    });
    
    return result;
  }, [sources, joinKeys, joinPairs]);

  // Handle multipli (10 fissi)
  const MAX_HANDLES = 10;
  const visibleHandleCount = Math.max(sources.length + 1, 2);
  
  return (
    <div className="multi-join-node">
      {/* Handle multipli per input */}
      {[...Array(MAX_HANDLES)].map((_, idx) => {
        const topPercent = 8 + (idx * 84 / (MAX_HANDLES - 1));
        const isVisible = idx < visibleHandleCount;
        
        return (
          <NodeHandle
            key={`input-${idx}`}
            type="target"
            position={Position.Left}
            id={`input-${idx}`}
            className={`multi-join-node__handle multi-join-node__handle--input ${!isVisible ? 'multi-join-node__handle--hidden' : ''}`}
            style={{ top: `${topPercent}%` }}
          />
        );
      })}

      {/* Header */}
      <div className="multi-join-node__header">
        <div className="multi-join-node__title">
          <span className="multi-join-node__icon">🔗</span>
          <span>Multi-Join</span>
          <span className="multi-join-node__count">({sources.length} sorgenti)</span>
        </div>
        <button type="button"
          onClick={handleDelete}
          onMouseDown={(e) => e.stopPropagation()}
          className="multi-join-node__delete-btn nodrag nopan"
          title="Elimina nodo"
        >
          ×
        </button>
      </div>

      {/* Body */}
      <div className="multi-join-node__body nodrag">
        {/* Sorgenti collegate */}
        <div className="multi-join-node__section">
          <label className="multi-join-node__label">Sorgenti collegate:</label>
          {sources.length === 0 ? (
            <div className="multi-join-node__empty">
              Collega almeno 2 sorgenti
            </div>
          ) : (
            <div className="multi-join-node__sources">
              {sources.map((src, idx) => {
                const sideLabel = sources.length === 2
                  ? (idx === 0 ? '⬅ SX' : '➡ DX')
                  : `${idx + 1}`;
                const sideClass = sources.length === 2
                  ? (idx === 0 ? 'multi-join-node__source--left' : 'multi-join-node__source--right')
                  : '';
                return (
                  <div key={src.id} className={`multi-join-node__source ${sideClass}`}>
                    <span className="multi-join-node__source-index">{sideLabel}</span>
                    <span className="multi-join-node__source-name" title={src.name}>
                      {src.name}
                    </span>
                    <span className="multi-join-node__source-cols">
                      ({src.columns.length} col)
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Colonne chiave */}
        {sources.length >= 2 && (
          <div className="multi-join-node__section">
            {sources.length === 2 ? (
              <>
                <label className="multi-join-node__label">
                  Colonne join:
                  <span className="multi-join-node__hint">
                    Puoi usare colonne con nomi diversi
                  </span>
                </label>

                {/* Righe di mapping left_col → right_col */}
                {(config.join_pairs || []).map((pair, idx) => (
                  <div key={idx} className="multi-join-node__pair-row nodrag">
                    <select
                      value={pair.left || ''}
                      onChange={(e) => {
                        const pairs = [...(config.join_pairs || [])];
                        pairs[idx] = { ...pairs[idx], left: e.target.value };
                        updateConfig('join_pairs', pairs);
                      }}
                      className="multi-join-node__pair-select multi-join-node__pair-select--left nodrag"
                      title="Colonna sinistra"
                    >
                      <option value="">⬅ Sinistra</option>
                      {(sources[0]?.columns || []).map(col => (
                        <option key={col.id || col.name} value={col.name}>{col.name}</option>
                      ))}
                    </select>
                    <span className="multi-join-node__pair-arrow">=</span>
                    <select
                      value={pair.right || ''}
                      onChange={(e) => {
                        const pairs = [...(config.join_pairs || [])];
                        pairs[idx] = { ...pairs[idx], right: e.target.value };
                        updateConfig('join_pairs', pairs);
                      }}
                      className="multi-join-node__pair-select multi-join-node__pair-select--right nodrag"
                      title="Colonna destra"
                    >
                      <option value="">➡ Destra</option>
                      {(sources[1]?.columns || []).map(col => (
                        <option key={col.id || col.name} value={col.name}>{col.name}</option>
                      ))}
                    </select>
                    <button
                      type="button"
                      onClick={() => {
                        const pairs = (config.join_pairs || []).filter((_, i) => i !== idx);
                        updateConfig('join_pairs', pairs);
                      }}
                      className="multi-join-node__pair-remove nodrag"
                      title="Rimuovi"
                    >
                      ×
                    </button>
                  </div>
                ))}

                <button
                  type="button"
                  onClick={() => {
                    const pairs = [...(config.join_pairs || []), { left: '', right: '' }];
                    updateConfig('join_pairs', pairs);
                  }}
                  className="multi-join-node__pair-add nodrag"
                >
                  + Aggiungi coppia colonne
                </button>

                {/* Warning quando l'utente non ha configurato pairs:
                    senza, il backend MANIPOLA cade sul fallback
                    "trova colonne comuni" che e' silente e fragile. */}
                {(() => {
                  const pairs = joinPairs;
                  const complete = pairs.filter(p => p && p.left && p.right);
                  if (pairs.length === 0 || complete.length === 0) {
                    return (
                      <div className="multi-join-node__warning">
                        ⚠️ Nessuna chiave di join configurata. Il
                        backend usera' le colonne comuni in
                        automatico (rischio risultati inattesi se
                        gli alias cambiano).
                      </div>
                    );
                  }
                  return null;
                })()}
              </>
            ) : (
              <>
                <label className="multi-join-node__label">
                  Colonne chiave (comuni): *
                  <span className="multi-join-node__hint">
                    Seleziona le colonne per il join
                  </span>
                </label>
                
                {commonColumns.length === 0 ? (
                  <div className="multi-join-node__warning">
                    ⚠️ Nessuna colonna comune trovata
                  </div>
                ) : (
                  <div className="multi-join-node__keys nodrag">
                    {commonColumns.map(col => (
                      <label 
                        key={col.id || col.name} 
                        className="multi-join-node__key-item nodrag"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <input
                          type="checkbox"
                          checked={joinKeys.includes(col.name)}
                          onChange={(e) => {
                            e.stopPropagation();
                            toggleJoinKey(col);
                          }}
                          onClick={(e) => e.stopPropagation()}
                          onMouseDown={(e) => e.stopPropagation()}
                          className="nodrag nopan"
                        />
                        <span className="multi-join-node__key-name">{col.name}</span>
                      </label>
                    ))}
                  </div>
                )}
                
                {joinKeys.length > 0 && (
                  <div className="multi-join-node__selected-keys">
                    Chiavi selezionate: <strong>{joinKeys.join(' + ')}</strong>
                  </div>
                )}
              </>
            )}
          </div>
        )}

        {/* Tipo join */}
        {sources.length >= 2 && (joinKeys.length > 0 || (config.join_pairs || []).length > 0) && (
          <div className="multi-join-node__section">
            <label className="multi-join-node__label">Tipo join:</label>
            <select
              value={joinType}
              onChange={(e) => updateConfig('join_type', e.target.value)}
              className="multi-join-node__select nodrag"
            >
              <option value="inner">Inner (solo corrispondenze)</option>
              <option value="left">Left (tutte da prima sorgente)</option>
              <option value="outer">Outer (tutte le righe)</option>
            </select>
          </div>
        )}

        {/* Preview colonne output */}
        {outputPreview.length > 0 && (joinKeys.length > 0 || (config.join_pairs || []).length > 0) && (
          <div className="multi-join-node__section">
            <label className="multi-join-node__label">
              Colonne output ({outputPreview.length}):
            </label>
            <div className="multi-join-node__output-preview">
              {outputPreview.slice(0, 8).map(col => (
                <span key={col} className="multi-join-node__output-col">
                  {col}
                </span>
              ))}
              {outputPreview.length > 8 && (
                <span className="multi-join-node__output-more">
                  +{outputPreview.length - 8} altre
                </span>
              )}
            </div>
          </div>
        )}

        {/* Messaggio validazione */}
        {sources.length >= 2 && sources.length === 2 && (config.join_pairs || []).length === 0 && (
          <div className="multi-join-node__validation">
            ⚠️ Aggiungi almeno una coppia di colonne per il join
          </div>
        )}
        {sources.length > 2 && joinKeys.length === 0 && commonColumns.length > 0 && (
          <div className="multi-join-node__validation">
            ⚠️ Seleziona almeno una colonna chiave
          </div>
        )}
      </div>

      {/* Handle output */}
      <NodeHandle
        type="source"
        position={Position.Right}
        className="multi-join-node__handle multi-join-node__handle--output"
        style={{ top: '50%' }}
      />

      {/* Cronologia run */}
      <div className="multi-join-node__footer">
        <button
          type="button"
          className="multi-join-node__history-btn"
          onClick={(e) => { e.stopPropagation(); setIsRunHistoryOpen(true); }} onMouseDown={(e) => e.stopPropagation()}
          title="Cronologia esecuzioni"
        >
          🕐 Cronologia run
        </button>
      </div>
      <RunHistoryModal
        isOpen={isRunHistoryOpen}
        onClose={() => setIsRunHistoryOpen(false)}
        canvasId={projectId}
        nodeId={id}
        nodeLabel={data.label || 'Multi Join'}
      />
    </div>
  );
});

MultiJoinNode.displayName = 'MultiJoinNode';

export default MultiJoinNode;
