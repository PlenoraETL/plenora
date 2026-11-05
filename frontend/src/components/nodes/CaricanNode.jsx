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
import { Position } from '@xyflow/react';
import useCanvasStore from '../../store/useCanvasStore';
import { useColumnsOf } from '../../hooks/useColumns';
import ColumnStatusNote from '../common/ColumnStatusNote';
import RestDestinationSection, { REST_METHODS } from './carica/RestDestinationSection';
import DbDestinationSection, { WRITE_MODES } from './carica/DbDestinationSection';
import AutoRunNote from './carica/autoRunNote';
import { restSecretHeaderNames, withoutSecretHeaders } from '../../utils/restSecrets';
import './CaricanNode.css';
import NodeHandle from '../canvas/NodeHandle';

const CaricanNode = memo(({ data, id }) => {
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
  const restAuthApiId = data.rest_auth_api_id || '';
  const restBodyMapping = data.rest_body_mapping || [];

  const connections = useCanvasStore(s => s.connections);
  const projectId = useCanvasStore(s => s.projectId) || '';
  const _updateNodeData = useCanvasStore(s => s.updateNodeData);
  const readOnly = useCanvasStore(s => s.isReadOnly);

  // Colonne che escono dall'Output (ingresso di questo nodo), con gli id:
  // dalla fonte unica (Blocco A, regola 4), non salvate nel nodo.
  const inputResult = useColumnsOf(id, { side: 'input' });
  const availableColumns = inputResult.columns;

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
  // Credenziali nel canvas = nodo da migrare (utils/restSecrets): non valido.
  const isRestValid = /^https?:\/\//i.test(restUrl) && REST_METHODS.includes(restMethod)
    && restSecretHeaderNames(data).length === 0;
  const isValid = destType === 'rest' ? isRestValid : (isDbValid && matchColumnsValid);

  return (
    <div className={`carica-node ${destType === 'rest' ? 'carica-node--rest' : ''} ${!isValid ? 'carica-node--invalid' : ''}`}>
      <NodeHandle
        type="target"
        position={Position.Left}
        className="carica-node__handle carica-node__handle--input"
      />

      {/* Titolo, chiusura ed eliminazione sono del pannello (NodeInspector,
          regola 6 del Blocco A); il modulo resta sempre aperto. */}
      <div className="carica-node__body">
        <ColumnStatusNote result={inputResult} />

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

          {destType !== 'rest' && restSecretHeaderNames(data).length > 0 && (
            <div className="carica-node__hint carica-node__hint--warn" role="alert" data-testid="rest-secrets-migrate-db">
              Da migrare: sono rimaste credenziali REST da una configurazione precedente
              ({restSecretHeaderNames(data).join(', ')}). Finché restano, il progetto non si salva e non si esegue.
              {' '}
              <button
                type="button"
                className="carica-node__btn carica-node__btn--secondary"
                disabled={readOnly}
                onClick={() => _updateNodeData(id, { rest_headers: withoutSecretHeaders(data.rest_headers) })}
              >
                Rimuovi credenziali
              </button>
            </div>
          )}

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
              restAuthApiId={restAuthApiId}
              restBodyMapping={restBodyMapping}
              availableColumns={availableColumns}
              showMapping={showMapping}
              onToggleMapping={() => setShowMapping(!showMapping)}
              showAdvanced={showAdvanced}
              onToggleAdvanced={() => setShowAdvanced(!showAdvanced)}
              onUpdate={handleUpdate}
              onMultiUpdate={handleMultiUpdate}
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

          <AutoRunNote className="carica-node__auto-info" />
      </div>

    </div>
  );
});

CaricanNode.displayName = 'CaricanNode';

export default CaricanNode;
