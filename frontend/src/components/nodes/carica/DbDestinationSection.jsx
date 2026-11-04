import React, { useState } from 'react';
import { postCaricaCheckConflicts } from '../../../api/carica';
import { WRITE_MODES as WRITE_MODE_VALUES } from '../../../constants/canvasTypes';
import { useCaricaSchemaSelect } from './useCaricaSchemaSelect';
import ColumnMappingPanel from './ColumnMappingPanel';
import DbAdvancedOptions from './DbAdvancedOptions';
import ConflictModal from './ConflictModal';
import { dataFlowEdges } from '../../../utils/runtimeBindingEdges';

// Labels e descrizioni sono UX, restano locali. I `value` arrivano da
// `canvasTypes.WRITE_MODES` (single source of truth allineata col BE);
// il contract test FE verifica che ogni entry abbia un `value` ∈
// WRITE_MODE_VALUES e che non manchi nessun valore canonico.
export const WRITE_MODES = [
  { value: WRITE_MODE_VALUES[0], label: '🆕 CREATE', desc: 'Crea nuova tabella (errore se esiste)' },
  { value: WRITE_MODE_VALUES[1], label: '🔄 REPLACE', desc: 'Elimina e ricrea la tabella' },
  { value: WRITE_MODE_VALUES[2], label: '➕ APPEND', desc: 'Aggiungi righe alla tabella esistente' },
  { value: WRITE_MODE_VALUES[3], label: '🔀 UPSERT', desc: 'Inserisci o aggiorna se esiste' },
  { value: WRITE_MODE_VALUES[4], label: '✏️ UPDATE', desc: 'Aggiorna solo righe esistenti' },
  { value: WRITE_MODE_VALUES[5], label: '🗑️ TRUNCATE + INSERT', desc: 'Svuota e reinserisci' },
];

function DbDestinationSection({
  connectionId,
  schemaName,
  tableName,
  writeMode,
  columnMappings,
  matchColumns,
  batchSize,
  transactionMode,
  createSpatialIndex,
  conflictPolicy,
  createUniqueIndex,
  uniqueKeyColumn,
  availableColumns,
  connections,
  canvasNodes,
  canvasEdges,
  nodeId,
  projectId,
  showMapping,
  onToggleMapping,
  showAdvanced,
  onToggleAdvanced,
  onUpdate,
  onMultiUpdate,
}) {
  const [showConflictModal, setShowConflictModal] = useState(false);
  const [conflictReport, setConflictReport] = useState(null);
  const [isCheckingConflicts, setIsCheckingConflicts] = useState(false);
  const [conflictError, setConflictError] = useState('');
  const [conflictCheckColumn, setConflictCheckColumn] = useState('');

  const {
    schemas,
    tables,
    isLoadingSchemas,
    isLoadingTables,
  } = useCaricaSchemaSelect({ connectionId, destType: 'db', schemaName });

  const handleConnectionChange = (newConnectionId) => {
    onMultiUpdate({
      connection_id: newConnectionId,
      schema_name: '',
      table_name: '',
      column_mappings: [],
    });
  };

  const handleSchemaChange = (newSchema) => {
    onMultiUpdate({
      schema_name: newSchema,
      table_name: '',
      column_mappings: [],
    });
  };

  const handleWriteModeChange = (newMode) => {
    const updates = { write_mode: newMode };
    if (!['upsert', 'update'].includes(newMode)) {
      updates.match_columns = [];
    }
    onMultiUpdate(updates);
  };

  const handleToggleMatchColumn = (columnName) => {
    const newMatch = matchColumns.includes(columnName)
      ? matchColumns.filter(c => c !== columnName)
      : [...matchColumns, columnName];
    onUpdate('match_columns', newMatch);
  };

  // Trova il nodo ESTRAI (o altro nodo DB) connesso a questo nodo CARICA
  const getSourceNodeData = () => {
    const incomingEdge = dataFlowEdges(canvasEdges).find(e => e.target === nodeId);
    if (!incomingEdge) return null;
    const sourceNode = canvasNodes.find(n => n.id === incomingEdge.source);
    if (!sourceNode?.data) return null;
    const d = sourceNode.data;
    if (d.connection_id && (d.table_name || d.tableName)) {
      return {
        source_connection_id: d.connection_id,
        source_schema: d.schema_name || d.schema || 'public',
        source_table: d.table_name || d.tableName,
      };
    }
    return null;
  };

  const handleCheckConflicts = async () => {
    // In APPEND il check usa conflictCheckColumn, non matchColumns (che è vuoto)
    const hasEnoughToCheck = writeMode === 'append'
      ? !!conflictCheckColumn
      : matchColumns.length > 0;
    if (!connectionId || !schemaName || !tableName || !hasEnoughToCheck) return;
    setIsCheckingConflicts(true);
    setConflictError('');
    setConflictReport(null);
    try {
      // Mappa alias: nome DB → nome colonna nel DataFrame
      const aliasLookup = {};
      availableColumns.forEach(c => {
        if (c.name) aliasLookup[c.name] = c.alias || c.name;
      });

      // Per append usa conflictCheckColumn, per upsert/update usa matchColumns
      const rawMatchCols = writeMode === 'append'
        ? (conflictCheckColumn ? [conflictCheckColumn] : [])
        : matchColumns;
      if (rawMatchCols.length === 0) return;

      // match_columns: nomi colonne nella tabella DB destinazione (nome originale)
      // df_match_columns: nomi colonne nel DataFrame (possono essere alias)
      // B-FE-67: non usare l'alias come nome DB — tenerli separati.
      const effectiveMatchCols = rawMatchCols;
      const dfMatchCols = rawMatchCols.map(col => aliasLookup[col] || col);

      const sourceNodeData = getSourceNodeData();
      const result = await postCaricaCheckConflicts({
        connection_id: connectionId,
        schema_name: schemaName,
        table_name: tableName,
        match_columns: effectiveMatchCols,
        df_match_columns: dfMatchCols,
        // Sorgente diretta: se disponibile, il backend interroga il DB sorgente
        // senza richiedere l'esecuzione previa della pipeline
        ...(sourceNodeData || {}),
        source_match_columns: effectiveMatchCols,
        // Fallback al result store se la sorgente non è un DB
        project_id: sourceNodeData ? undefined : (projectId || undefined),
        sample_values: [],
      });
      setConflictReport(result);
      setShowConflictModal(true);
    } catch (err) {
      setConflictError(err?.message || 'Errore durante il controllo dei conflitti');
      setShowConflictModal(true);
    } finally {
      setIsCheckingConflicts(false);
    }
  };

  const needsMatchColumns = ['upsert', 'update'].includes(writeMode);
  const matchColumnsValid = !needsMatchColumns || matchColumns.length > 0;

  return (
    <>
      {/* Connessione Database */}
      <div className="carica-node__section">
        <label className="carica-node__label">🔌 Connessione Database</label>
        <select
          value={connectionId}
          onChange={(e) => handleConnectionChange(e.target.value)}
          className="carica-node__select"
        >
          <option value="">Seleziona connessione...</option>
          {connections.map(conn => (
            <option key={conn.id} value={conn.id}>
              {conn.name} ({conn.type})
            </option>
          ))}
        </select>
      </div>

      {connectionId && (
        <div className="carica-node__section">
          <label className="carica-node__label">📁 Schema</label>
          <select
            value={schemaName}
            onChange={(e) => handleSchemaChange(e.target.value)}
            className="carica-node__select"
            disabled={isLoadingSchemas}
          >
            <option value="">
              {isLoadingSchemas ? 'Caricamento...' : 'Seleziona schema...'}
            </option>
            {schemas.map(schema => (
              <option key={schema} value={schema}>{schema}</option>
            ))}
          </select>
        </div>
      )}

      {/* Tabella */}
      {schemaName && (
        <div className="carica-node__section">
          <label className="carica-node__label">📋 Tabella Destinazione</label>
          {writeMode === 'create' || writeMode === 'replace' ? (
            <input
              type="text"
              value={tableName}
              onChange={(e) => onUpdate('table_name', e.target.value)}
              placeholder="Nome nuova tabella..."
              className="carica-node__input"
            />
          ) : (
            <select
              value={tableName}
              onChange={(e) => onUpdate('table_name', e.target.value)}
              className="carica-node__select"
              disabled={isLoadingTables}
            >
              <option value="">
                {isLoadingTables ? 'Caricamento...' : 'Seleziona tabella...'}
              </option>
              {tables.map(table => (
                <option key={table} value={table}>{table}</option>
              ))}
            </select>
          )}
        </div>
      )}

      {/* Modalità Scrittura DB */}
      <div className="carica-node__section">
        <label className="carica-node__label">⚙️ Modalità Scrittura</label>
        <select
          value={writeMode}
          onChange={(e) => handleWriteModeChange(e.target.value)}
          className="carica-node__select"
        >
          {WRITE_MODES.map(mode => (
            <option key={mode.value} value={mode.value}>
              {mode.label}
            </option>
          ))}
        </select>
        <div className="carica-node__hint">
          {WRITE_MODES.find(m => m.value === writeMode)?.desc}
        </div>
      </div>

      {/* Match Columns (per UPDATE/UPSERT DB) */}
      {needsMatchColumns && (
        <div className="carica-node__section">
          <label className="carica-node__label">🔑 Colonne di corrispondenza (chiave primaria)</label>
          <div className="carica-node__match-columns">
            {availableColumns.map((col, idx) => {
              const colName = col.name || col;
              const isSelected = matchColumns.includes(colName);
              return (
                <label key={idx} className="carica-node__match-item">
                  <input
                    type="checkbox"
                    checked={isSelected}
                    onChange={() => handleToggleMatchColumn(colName)}
                  />
                  <span>{colName}</span>
                </label>
              );
            })}
          </div>
          {!matchColumnsValid && (
            <div className="carica-node__warning">
              ⚠️ Seleziona almeno una colonna di corrispondenza
            </div>
          )}
        </div>
      )}

      {/* Bottone Verifica Conflitti — visibile per append/upsert/update */}
      {['append', 'upsert', 'update'].includes(writeMode) && tableName && connectionId && (
        <div className="carica-node__section">
          {writeMode === 'append' && (
            <>
              <label className="carica-node__label">🔍 Colonna per il controllo dei duplicati</label>
              <select
                className="carica-node__select"
                value={conflictCheckColumn}
                onChange={e => setConflictCheckColumn(e.target.value)}
              >
                <option value="">— nessun controllo —</option>
                {availableColumns.map((col, i) => {
                  const name = col.name || col;
                  return <option key={i} value={name}>{name}</option>;
                })}
              </select>
            </>
          )}
          {(writeMode !== 'append' || conflictCheckColumn) && (!needsMatchColumns || matchColumns.length > 0) && (
            <button
              className="carica-node__btn carica-node__btn--check"
              onClick={handleCheckConflicts}
              disabled={isCheckingConflicts}
              title="Verifica conflitti con la tabella destinazione prima di eseguire"
            >
              {isCheckingConflicts ? '⏳ Verifica in corso...' : '🔍 Verifica Conflitti'}
            </button>
          )}
        </div>
      )}

      {schemaName && (
        <ColumnMappingPanel
          columnMappings={columnMappings}
          availableColumns={availableColumns}
          connectionId={connectionId}
          schemaName={schemaName}
          tableName={tableName}
          showMapping={showMapping}
          onToggleMapping={onToggleMapping}
          onUpdate={onUpdate}
        />
      )}

      <DbAdvancedOptions
        batchSize={batchSize}
        transactionMode={transactionMode}
        conflictPolicy={conflictPolicy}
        createSpatialIndex={createSpatialIndex}
        createUniqueIndex={createUniqueIndex}
        uniqueKeyColumn={uniqueKeyColumn}
        writeMode={writeMode}
        availableColumns={availableColumns}
        showAdvanced={showAdvanced}
        onToggleAdvanced={onToggleAdvanced}
        onUpdate={onUpdate}
      />

      <ConflictModal
        isOpen={showConflictModal}
        onClose={() => setShowConflictModal(false)}
        report={conflictReport}
        error={conflictError}
        policy={conflictPolicy}
        schemaName={schemaName}
        tableName={tableName}
      />
    </>
  );
}

export default DbDestinationSection;
