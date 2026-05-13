/**
 * CaricanNode.jsx — Nodo canvas per scrittura risultati su Database o REST.
 *
 * Nodo terminale che riceve l'output dal ResultNode e lo scrive su:
 *  - Database relazionale (PostgreSQL, Oracle, SQL Server, MySQL)
 *  - Endpoint REST (POST/PUT/PATCH)
 *
 * Orchestratore: legge data dal canvas store, delega il branch DB/REST
 * ai rispettivi DestinationSection.
 *
 * Si aspetta un edge in ingresso da ResultNode (handle "output").
 */
import React, { useState, memo } from 'react';
import { Handle, Position } from 'reactflow';
import useCanvasStore from '../../store/useCanvasStore';
import RestDestinationSection, { REST_METHODS } from './carica/RestDestinationSection';
import DbDestinationSection, { WRITE_MODES } from './carica/DbDestinationSection';
import './CaricanNode.css';

const CaricanNode = memo(({ data, id }) => {
  const [isExpanded, setIsExpanded] = useState(true);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [showMapping, setShowMapping] = useState(false);

  const connectionId = data.connection_id || '';
  const destType = data.dest_type || 'db';
  const schemaName = data.schema_name || '';
  const tableName = data.table_name || '';
  const writeMode = data.write_mode || 'create';
  const columnMappings = data.column_mappings || [];
  const matchColumns = data.match_columns || [];
  const batchSize = data.batch_size || 1000;
  const transactionMode = data.transaction_mode || 'all_or_nothing';
  const createSpatialIndex = data.create_spatial_index !== false;
  const conflictPolicy = data.conflict_policy || 'abort';
  const createUniqueIndex = data.create_unique_index || false;
  const uniqueKeyColumn = data.unique_key_column || '';
  const restUrl = data.rest_url || '';
  const restMethod = data.rest_method || 'POST';
  const restBatchMode = data.rest_batch_mode || 'batch';
  const restBatchSize = data.rest_batch_size || data.batch_size || 1000;
  const restTimeoutSeconds = data.rest_timeout_seconds || 30;
  const restHeaders = data.rest_headers || [];
  const restBodyMapping = data.rest_body_mapping || [];

  const connections = useCanvasStore(s => s.connections);
  const canvasNodes = useCanvasStore(s => s.nodes) || [];
  const canvasEdges = useCanvasStore(s => s.edges) || [];
  const projectId = useCanvasStore(s => s.projectId) || '';
  const _updateNodeData = useCanvasStore(s => s.updateNodeData);

  const availableColumns = data.availableColumns || [];

  const handleUpdate = (field, value) => {
    _updateNodeData(id, { [field]: value });
  };

  const handleMultiUpdate = (updates) => {
    _updateNodeData(id, updates);
  };

  const handleDestinationTypeChange = (newDestType) => {
    handleMultiUpdate({
      dest_type: newDestType,
      ...(newDestType === 'rest'
        ? {
            connection_id: '',
            schema_name: '',
            table_name: '',
            match_columns: [],
            column_mappings: [],
            rest_method: restMethod,
            rest_batch_mode: restBatchMode,
            rest_batch_size: restBatchSize,
            rest_timeout_seconds: restTimeoutSeconds,
          }
        : {}),
    });
  };

  const isDbValid = connectionId && schemaName && tableName;
  const needsMatchColumns = ['upsert', 'update'].includes(writeMode);
  const matchColumnsValid = !needsMatchColumns || matchColumns.length > 0;
  const isRestValid = /^https?:\/\//i.test(restUrl) && REST_METHODS.includes(restMethod);
  const isValid = destType === 'rest' ? isRestValid : (isDbValid && matchColumnsValid);

  return (
    <div className={`carica-node ${destType === 'rest' ? 'carica-node--rest' : ''} ${!isValid ? 'carica-node--invalid' : ''}`}>
      <Handle
        type="target"
        position={Position.Left}
        className="carica-node__handle carica-node__handle--input"
      />

      <div className="carica-node__header" role="button" tabIndex={0} onClick={() => setIsExpanded(!isExpanded)} onKeyDown={e => (e.key === 'Enter' || e.key === ' ') && setIsExpanded(v => !v)}>
        <div className="carica-node__header-left">
          <span className="carica-node__icon">💾</span>
          <span className="carica-node__title">{destType === 'rest' ? 'CARICA su REST' : 'CARICA su DB'}</span>
        </div>
        <div className="carica-node__header-right">
          <span className="carica-node__expand-icon">
            {isExpanded ? '▼' : '▶'}
          </span>
          <button type="button"
            onClick={(e) => { e.stopPropagation(); useCanvasStore.getState().deleteNode(id); }}
            className="carica-node__delete-btn"
            title="Elimina nodo"
          >
            ×
          </button>
        </div>
      </div>

      {isExpanded && (
        <div className="carica-node__body">

          <div className="carica-node__dest-selector" role="group" aria-label="Tipo destinazione Carica">
            <button
              type="button"
              className={`carica-node__dest-btn ${destType === 'db' ? 'carica-node__dest-btn--active' : ''}`}
              onClick={() => handleDestinationTypeChange('db')}
            >
              DB
            </button>
            <button
              type="button"
              className={`carica-node__dest-btn ${destType === 'rest' ? 'carica-node__dest-btn--active carica-node__dest-btn--rest' : ''}`}
              onClick={() => handleDestinationTypeChange('rest')}
            >
              REST
            </button>
          </div>

          {destType === 'db' && (
            <DbDestinationSection
              connectionId={connectionId}
              schemaName={schemaName}
              tableName={tableName}
              writeMode={writeMode}
              columnMappings={columnMappings}
              matchColumns={matchColumns}
              batchSize={batchSize}
              transactionMode={transactionMode}
              createSpatialIndex={createSpatialIndex}
              conflictPolicy={conflictPolicy}
              createUniqueIndex={createUniqueIndex}
              uniqueKeyColumn={uniqueKeyColumn}
              availableColumns={availableColumns}
              connections={connections}
              canvasNodes={canvasNodes}
              canvasEdges={canvasEdges}
              nodeId={id}
              projectId={projectId}
              showMapping={showMapping}
              onToggleMapping={() => setShowMapping(!showMapping)}
              showAdvanced={showAdvanced}
              onToggleAdvanced={() => setShowAdvanced(!showAdvanced)}
              onUpdate={handleUpdate}
              onMultiUpdate={handleMultiUpdate}
            />
          )}

          {destType === 'rest' && (
            <RestDestinationSection
              restUrl={restUrl}
              restMethod={restMethod}
              restBatchMode={restBatchMode}
              restBatchSize={restBatchSize}
              restTimeoutSeconds={restTimeoutSeconds}
              restHeaders={restHeaders}
              restBodyMapping={restBodyMapping}
              availableColumns={availableColumns}
              showMapping={showMapping}
              onToggleMapping={() => setShowMapping(!showMapping)}
              showAdvanced={showAdvanced}
              onToggleAdvanced={() => setShowAdvanced(!showAdvanced)}
              onUpdate={handleUpdate}
            />
          )}

          {isValid && (
            <div className="carica-node__preview">
              <div className="carica-node__preview-item">
                <span className="carica-node__preview-label">Destinazione:</span>
                <span className="carica-node__preview-value">{destType === 'rest' ? restUrl : `${schemaName}.${tableName}`}</span>
              </div>
              <div className="carica-node__preview-item">
                <span className="carica-node__preview-label">Modalità:</span>
                <span className="carica-node__preview-value">
                  {destType === 'rest' ? restMethod : WRITE_MODES.find(m => m.value === writeMode)?.label}
                </span>
              </div>
              {availableColumns.length > 0 && (
                <div className="carica-node__preview-item">
                  <span className="carica-node__preview-label">Colonne:</span>
                  <span className="carica-node__preview-value">
                    {columnMappings.length || availableColumns.length}
                  </span>
                </div>
              )}
            </div>
          )}

          <div className="carica-node__auto-info">
            ⚡ Eseguito automaticamente con <strong>Esegui Query</strong>
          </div>
        </div>
      )}

    </div>
  );
});

CaricanNode.displayName = 'CaricanNode';

export default CaricanNode;
