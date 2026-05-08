/**
 * CaricanNode.jsx — Nodo canvas per scrittura risultati su Database
 *
 * Nodo terminale che riceve l'output dal ResultNode e lo scrive su:
 *  - Database relazionale (PostgreSQL, Oracle, SQL Server, MySQL)
 *
 * Modalità: create_new | append | overwrite | upsert
 * Collassabile (click sull'header) per occupare meno spazio nel canvas.
 *
 * Si aspetta un edge in ingresso da ResultNode (handle "output").
 */
import React, { useState, useEffect, useRef, memo } from 'react';
import { createPortal } from 'react-dom';
import { Handle, Position } from 'reactflow';
import useCanvasStore from '../../store/useCanvasStore';
import useDialogFocus from '../../hooks/useDialogFocus';
import './CaricanNode.css';
import { getCaricaSchemas, getCaricaTables, getCaricaColumns, postCaricaAutoMapping, postCaricaCheckConflicts } from '../../api/carica';
const CaricanNode = memo(({ data, id }) => {
  // =========================================================================
  // DEBUG — rimuovere dopo fix
  // =========================================================================

  // =========================================================================
  // STATE — UI
  // =========================================================================
  const [isExpanded, setIsExpanded] = useState(true);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [showMapping, setShowMapping] = useState(false);

  // State loading DB
  const [isLoadingSchemas, setIsLoadingSchemas] = useState(false);
  const [isLoadingTables, setIsLoadingTables] = useState(false);
  const [isLoadingColumns, setIsLoadingColumns] = useState(false);

  // State dati DB
  const [schemas, setSchemas] = useState([]);
  const [tables, setTables] = useState([]);
  const [destColumns, setDestColumns] = useState([]);

  // State conflict checker
  const [showConflictModal, setShowConflictModal] = useState(false);
  const conflictDialogRef = useRef(null);
  useDialogFocus(
    showConflictModal,
    () => setShowConflictModal(false),
    conflictDialogRef,
  );
  const [conflictReport, setConflictReport] = useState(null);
  const [isCheckingConflicts, setIsCheckingConflicts] = useState(false);
  const [conflictError, setConflictError] = useState('');
  const [conflictCheckColumn, setConflictCheckColumn] = useState('');

  // =========================================================================
  // VALORI CORRENTI dalla configurazione
  // =========================================================================
  
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

  // Colonne upstream
  const availableColumns = data.availableColumns || [];
  const inputPath = data.inputPath || '';
  const executeResult = data.executeResult || null;
  const projectId = useCanvasStore(s => s.projectId) || '';

  // =========================================================================
  // OPZIONI STATICHE
  // =========================================================================

  const writeModes = [
    { value: 'create', label: '🆕 CREATE', desc: 'Crea nuova tabella (errore se esiste)' },
    { value: 'replace', label: '🔄 REPLACE', desc: 'Elimina e ricrea la tabella' },
    { value: 'append', label: '➕ APPEND', desc: 'Aggiungi righe alla tabella esistente' },
    { value: 'upsert', label: '🔀 UPSERT', desc: 'Inserisci o aggiorna se esiste' },
    { value: 'update', label: '✏️ UPDATE', desc: 'Aggiorna solo righe esistenti' },
    { value: 'truncate_insert', label: '🗑️ TRUNCATE + INSERT', desc: 'Svuota e reinserisci' },
  ];

  const conflictPolicies = [
    { value: 'abort', label: 'Interrompi', desc: 'Blocca se ci sono conflitti' },
    { value: 'skip', label: 'Salta', desc: 'Inserisci solo righe non in conflitto' },
    { value: 'force', label: 'Sovrascrivi', desc: 'Sovrascrivi righe in conflitto' },
  ];

  const transactionModes = [
    { value: 'all_or_nothing', label: 'Atomica', desc: 'Rollback totale su errore' },
    { value: 'per_batch', label: 'Per batch', desc: 'Commit ogni batch (parziale ok)' },
  ];

  const restMethods = ['POST', 'PUT', 'PATCH'];

  const restBatchModes = [
    { value: 'batch', label: 'Batch' },
    { value: 'per_record', label: 'Per riga' },
  ];

  // =========================================================================
  // EFFETTI — DB
  // =========================================================================

  useEffect(() => {
    if (destType === 'db' && connectionId) {
      loadSchemas(connectionId);
    } else {
      setSchemas([]); setTables([]); setDestColumns([]);
    }
  }, [connectionId, destType]);

  useEffect(() => {
    if (destType === 'db' && connectionId && schemaName) {
      loadTables(connectionId, schemaName);
    } else {
      setTables([]); setDestColumns([]);
    }
  }, [connectionId, schemaName, destType]);

  useEffect(() => {
    if (destType === 'db' && connectionId && schemaName && tableName
        && writeMode !== 'create' && writeMode !== 'replace') {
      loadDestColumns(connectionId, schemaName, tableName);
    } else {
      setDestColumns([]);
    }
  }, [connectionId, schemaName, tableName, writeMode, destType]);

  // =========================================================================
  // FUNZIONI API — DB
  // =========================================================================

  const loadSchemas = async (connId) => {
    setIsLoadingSchemas(true);
    try {
      const data = await getCaricaSchemas(connId);
      setSchemas(data.schemas || []);
    } catch (error) {
      console.error('Errore caricamento schemas:', error);
    } finally {
      setIsLoadingSchemas(false);
    }
  };

  const loadTables = async (connId, schema) => {
    setIsLoadingTables(true);
    try {
      const data = await getCaricaTables(connId, schema);
      setTables(data.tables || []);
    } catch (error) {
      console.error('Errore caricamento tables:', error);
    } finally {
      setIsLoadingTables(false);
    }
  };

  const loadDestColumns = async (connId, schema, table) => {
    setIsLoadingColumns(true);
    try {
      const data = await getCaricaColumns(connId, schema, table);
      setDestColumns(data.columns || []);
    } catch (error) {
      console.error('Errore caricamento colonne:', error);
    } finally {
      setIsLoadingColumns(false);
    }
  };

  const loadAutoMapping = async () => {
    if (!connectionId || availableColumns.length === 0) return;
    try {
      // Payload al backend: solo name e type (alias non accettato dal modello)
      const data = await postCaricaAutoMapping({
          connection_id: connectionId,
          schema_name: schemaName,
          table_name: tableName,
          source_columns: availableColumns.map(c => ({
            name: c.name || c,
            type: c.type || 'str',
          })),
        });
      // Normalizza formato backend → frontend.
      // source = nome colonna originale (NON alias) — il backend usa il nome originale per leggere il DataFrame
      const normalized = (data.mappings || []).map(m => ({
        source: m.source_column || m.source || '',
        dest:   m.dest_column   || m.dest   || '',
        is_pk:  m.is_primary_key || false,
        dest_type: m.dest_type || '',
        is_geometry: m.is_geometry || false,
        skip:   m.skip || false,
      }));
      handleUpdate('column_mappings', normalized);
    } catch (error) {
      console.error('Errore auto-mapping:', error);
    }
  };

  // =========================================================================
  // HANDLERS
  // =========================================================================

  const _updateNodeData = useCanvasStore(s => s.updateNodeData);

  const handleUpdate = (field, value) => {
    _updateNodeData(id, { [field]: value });
  };

  const handleMultiUpdate = (updates) => {
    _updateNodeData(id, updates);
  };

  const handleConnectionChange = (newConnectionId) => {
    handleMultiUpdate({
      connection_id: newConnectionId,
      schema_name: '',
      table_name: '',
      column_mappings: [],
    });
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

  const handleSchemaChange = (newSchema) => {
    handleMultiUpdate({
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
    handleMultiUpdate(updates);
  };

  // Column mapping handlers
  const handleMappingChange = (index, field, value) => {
    const newMappings = [...columnMappings];
    newMappings[index] = { ...newMappings[index], [field]: value };
    handleUpdate('column_mappings', newMappings);
  };

  const handleAddMapping = () => {
    handleUpdate('column_mappings', [...columnMappings, { source: '', dest: '', is_pk: false }]);
  };

  const handleRemoveMapping = (index) => {
    handleUpdate('column_mappings', columnMappings.filter((_, i) => i !== index));
  };

  const handleRestHeaderChange = (index, field, value) => {
    const nextHeaders = [...restHeaders];
    nextHeaders[index] = { ...nextHeaders[index], [field]: value };
    handleUpdate('rest_headers', nextHeaders);
  };

  const handleAddRestHeader = () => {
    handleUpdate('rest_headers', [...restHeaders, { name: '', value: '', secret: false }]);
  };

  const handleRemoveRestHeader = (index) => {
    handleUpdate('rest_headers', restHeaders.filter((_, i) => i !== index));
  };

  const handleRestBodyMappingChange = (index, field, value) => {
    const nextMapping = [...restBodyMapping];
    nextMapping[index] = { ...nextMapping[index], [field]: value };
    handleUpdate('rest_body_mapping', nextMapping);
  };

  const handleAddRestBodyMapping = () => {
    handleUpdate('rest_body_mapping', [...restBodyMapping, { source: '', target: '', required: false }]);
  };

  const handleRemoveRestBodyMapping = (index) => {
    handleUpdate('rest_body_mapping', restBodyMapping.filter((_, i) => i !== index));
  };

  const handleToggleMatchColumn = (columnName) => {
    const newMatch = matchColumns.includes(columnName)
      ? matchColumns.filter(c => c !== columnName)
      : [...matchColumns, columnName];
    handleUpdate('match_columns', newMatch);
  };

  // ℹ️ handleExecute rimosso — il caricamento viene avviato automaticamente
  // da "Esegui Query" nel nodo Output tramite l'orchestratore backend.


  // Trova il nodo ESTRAI (o altro nodo DB) connesso a questo nodo CARICA
  const getSourceNodeData = () => {
    // Cerca l'edge che entra in questo nodo
    const incomingEdge = canvasEdges.find(e => e.target === id);
    if (!incomingEdge) return null;
    const sourceNode = canvasNodes.find(n => n.id === incomingEdge.source);
    if (!sourceNode?.data) return null;
    const d = sourceNode.data;
    // Restituisce i dati di connessione se il nodo sorgente è un nodo DB (ESTRAI)
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
      setConflictError(err?.message || 'Errore durante il check conflitti');
      setShowConflictModal(true);
    } finally {
      setIsCheckingConflicts(false);
    }
  };

  // =========================================================================
  // VALIDAZIONE
  // =========================================================================

  const isDbValid = connectionId && schemaName && tableName;
  const needsMatchColumns = ['upsert', 'update'].includes(writeMode);
  const matchColumnsValid = !needsMatchColumns || matchColumns.length > 0;
  const isRestValid = /^https?:\/\//i.test(restUrl) && restMethods.includes(restMethod);
  const isValid = destType === 'rest' ? isRestValid : (isDbValid && matchColumnsValid);

  // =========================================================================
  // RENDER
  // =========================================================================

  return (
    <div className={`carica-node ${destType === 'rest' ? 'carica-node--rest' : ''} ${!isValid ? 'carica-node--invalid' : ''}`}>
      {/* Handle input — connessione dal nodo Output */}
      <Handle
        type="target"
        position={Position.Left}
        className="carica-node__handle carica-node__handle--input"
      />

      {/* Header */}
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

      {/* Body */}
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

          {/* ============ SEZIONI DB ============ */}
          {destType === 'db' && (
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

              {/* Schema */}
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
                      onChange={(e) => handleUpdate('table_name', e.target.value)}
                      placeholder="Nome nuova tabella..."
                      className="carica-node__input"
                    />
                  ) : (
                    <select
                      value={tableName}
                      onChange={(e) => handleUpdate('table_name', e.target.value)}
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
                  {writeModes.map(mode => (
                    <option key={mode.value} value={mode.value}>
                      {mode.label}
                    </option>
                  ))}
                </select>
                <div className="carica-node__hint">
                  {writeModes.find(m => m.value === writeMode)?.desc}
                </div>
              </div>

              {/* Match Columns (per UPDATE/UPSERT DB) */}
              {needsMatchColumns && (
                <div className="carica-node__section">
                  <label className="carica-node__label">🔑 Colonne Match (PK)</label>
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
                      ⚠️ Seleziona almeno una colonna per il match
                    </div>
                  )}
                </div>
              )}

              {/* Bottone Verifica Conflitti — visibile per append/upsert/update */}
              {['append', 'upsert', 'update'].includes(writeMode) && tableName && connectionId && (
                <div className="carica-node__section">
                  {writeMode === 'append' && (
                    <>
                      <label className="carica-node__label">🔍 Colonna check duplicati</label>
                      <select
                        className="carica-node__select"
                        value={conflictCheckColumn}
                        onChange={e => setConflictCheckColumn(e.target.value)}
                      >
                        <option value="">— nessun check —</option>
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

          {/* ============ MAPPING COLONNE ============ */}
          {schemaName && (
            <div className="carica-node__section">
              <div 
                className="carica-node__section-header"
                onClick={() => setShowMapping(!showMapping)}
              >
                <span>🔀 Mapping Colonne</span>
                <span className="carica-node__toggle">{showMapping ? '▼' : '▶'}</span>
              </div>

              {showMapping && (
                <div className="carica-node__mapping">
                  <div className="carica-node__mapping-actions">
                    <button type="button"
                      onClick={loadAutoMapping}
                      className="carica-node__btn carica-node__btn--secondary"
                    >
                      🪄 Auto-mapping
                    </button>
                    <button type="button"
                      onClick={handleAddMapping}
                      className="carica-node__btn carica-node__btn--secondary"
                    >
                      + Aggiungi
                    </button>
                  </div>

                  {columnMappings.length === 0 ? (
                    <div className="carica-node__hint">
                      Nessun mapping definito. Usa "Auto-mapping" o aggiungi manualmente.
                    </div>
                  ) : (
                    <div className="carica-node__mapping-list">
                      {columnMappings.map((mapping, idx) => (
                        <div key={idx} className="carica-node__mapping-row">
                          {/* Sorgente: select dalle colonne upstream */}
                          <select
                            value={mapping.source || ''}
                            onChange={(e) => handleMappingChange(idx, 'source', e.target.value)}
                            className="carica-node__mapping-select"
                          >
                            <option value="">Sorgente...</option>
                            {availableColumns.map((col, colIdx) => {
                              const originalName = col.name || col;
                              const displayName = col.alias ? col.alias + ' (' + originalName + ')' : originalName;
                              return (
                                <option key={colIdx} value={originalName}>
                                  {displayName}
                                </option>
                              );
                            })}
                          </select>
                          <span className="carica-node__mapping-arrow">→</span>
                          <input
                            type="text"
                            value={mapping.dest || ''}
                            onChange={(e) => handleMappingChange(idx, 'dest', e.target.value)}
                            placeholder="Destinazione..."
                            className="carica-node__mapping-input"
                          />
                          <button type="button"
                            onClick={() => handleRemoveMapping(idx)}
                            className="carica-node__mapping-remove"
                          >
                            ×
                          </button>
                        </div>
                      ))}
                    </div>
                  )}

                </div>
              )}
            </div>
          )}

          {/* ============ OPZIONI AVANZATE ============ */}
          <div className="carica-node__section">
            <div
              className="carica-node__section-header"
              onClick={() => setShowAdvanced(!showAdvanced)}
            >
              <span>⚡ Opzioni Avanzate</span>
              <span className="carica-node__toggle">{showAdvanced ? '▼' : '▶'}</span>
            </div>

            {showAdvanced && (
              <div className="carica-node__advanced">
                <div className="carica-node__advanced-row">
                  <label>Batch size:</label>
                  <input
                    type="number"
                    value={batchSize}
                    onChange={(e) => handleUpdate('batch_size', parseInt(e.target.value) || 1000)}
                    min={100} max={10000} step={100}
                    className="carica-node__input carica-node__input--small"
                  />
                </div>
                <div className="carica-node__advanced-row">
                  <label>Transazione:</label>
                  <select
                    value={transactionMode}
                    onChange={(e) => handleUpdate('transaction_mode', e.target.value)}
                    className="carica-node__select carica-node__select--small"
                  >
                    {transactionModes.map(mode => (
                      <option key={mode.value} value={mode.value}>{mode.label}</option>
                    ))}
                  </select>
                </div>
                {writeMode === 'append' && (
                  <div className="carica-node__advanced-row">
                    <label>Se conflitto:</label>
                    <select
                      value={conflictPolicy}
                      onChange={(e) => handleUpdate('conflict_policy', e.target.value)}
                      className="carica-node__select carica-node__select--small"
                    >
                      {conflictPolicies.map(p => (
                        <option key={p.value} value={p.value}>{p.label}</option>
                      ))}
                    </select>
                  </div>
                )}
                {(writeMode === 'create' || writeMode === 'replace') && (
                  <div className="carica-node__advanced-row">
                    <label>
                      <input
                        type="checkbox"
                        checked={createSpatialIndex}
                        onChange={(e) => handleUpdate('create_spatial_index', e.target.checked)}
                      />
                      Crea indice spaziale
                    </label>
                  </div>
                )}
                {(writeMode === 'create' || writeMode === 'replace') && (
                  <div className="carica-node__advanced-row">
                    <label>
                      <input
                        type="checkbox"
                        checked={createUniqueIndex}
                        onChange={(e) => {
                          handleUpdate('create_unique_index', e.target.checked);
                          if (!e.target.checked) handleUpdate('unique_key_column', '');
                        }}
                      />
                      Crea indice univoco (per UPSERT)
                    </label>
                    {createUniqueIndex && (
                      <select
                        value={uniqueKeyColumn}
                        onChange={(e) => handleUpdate('unique_key_column', e.target.value)}
                        className="carica-node__select"
                        style={{ marginTop: '6px' }}
                      >
                        <option value="">Seleziona colonna chiave...</option>
                        {availableColumns.map((col, idx) => {
                          const colName = col.name || col;
                          return <option key={idx} value={colName}>{colName}</option>;
                        })}
                      </select>
                    )}
                    {createUniqueIndex && !uniqueKeyColumn && (
                      <div className="carica-node__warning">
                        ⚠️ Seleziona la colonna chiave univoca
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>

            </>
          )}

          {destType === 'rest' && (
            <>
              <div className="carica-node__section">
                <label className="carica-node__label">Endpoint REST</label>
                <input
                  type="url"
                  value={restUrl}
                  onChange={(e) => handleUpdate('rest_url', e.target.value)}
                  placeholder="https://api.example.com/items"
                  className="carica-node__input"
                />
              </div>

              <div className="carica-node__section">
                <label className="carica-node__label">Metodo</label>
                <select
                  value={restMethod}
                  onChange={(e) => handleUpdate('rest_method', e.target.value)}
                  className="carica-node__select"
                >
                  {restMethods.map(method => (
                    <option key={method} value={method}>{method}</option>
                  ))}
                </select>
              </div>

              <div className="carica-node__section">
                <div
                  className="carica-node__section-header"
                  onClick={() => setShowMapping(!showMapping)}
                >
                  <span>Body Mapping</span>
                  <span className="carica-node__toggle">{showMapping ? '▼' : '▶'}</span>
                </div>

                {showMapping && (
                  <div className="carica-node__mapping">
                    <div className="carica-node__mapping-actions">
                      <button type="button" onClick={handleAddRestBodyMapping} className="carica-node__btn carica-node__btn--secondary">
                        + Campo
                      </button>
                    </div>
                    {restBodyMapping.length === 0 ? (
                      <div className="carica-node__hint">
                        Nessun mapping: il backend invia le righe con le colonne disponibili.
                      </div>
                    ) : (
                      <div className="carica-node__mapping-list">
                        {restBodyMapping.map((mapping, idx) => (
                          <div key={idx} className="carica-node__mapping-row">
                            <select
                              value={mapping.source || ''}
                              onChange={(e) => handleRestBodyMappingChange(idx, 'source', e.target.value)}
                              className="carica-node__mapping-select"
                            >
                              <option value="">Colonna...</option>
                              {availableColumns.map((col, colIdx) => {
                                const originalName = col.name || col;
                                const displayName = col.alias ? col.alias + ' (' + originalName + ')' : originalName;
                                return <option key={colIdx} value={originalName}>{displayName}</option>;
                              })}
                            </select>
                            <span className="carica-node__mapping-arrow">→</span>
                            <input
                              type="text"
                              value={mapping.target || ''}
                              onChange={(e) => handleRestBodyMappingChange(idx, 'target', e.target.value)}
                              placeholder="Campo JSON..."
                              className="carica-node__mapping-input"
                            />
                            <button type="button" onClick={() => handleRemoveRestBodyMapping(idx)} className="carica-node__mapping-remove">
                              ×
                            </button>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>

              <div className="carica-node__section">
                <div
                  className="carica-node__section-header"
                  onClick={() => setShowAdvanced(!showAdvanced)}
                >
                  <span>Headers e batch</span>
                  <span className="carica-node__toggle">{showAdvanced ? '▼' : '▶'}</span>
                </div>

                {showAdvanced && (
                  <div className="carica-node__advanced">
                    <div className="carica-node__advanced-row">
                      <label>Batch mode:</label>
                      <select
                        value={restBatchMode}
                        onChange={(e) => handleUpdate('rest_batch_mode', e.target.value)}
                        className="carica-node__select carica-node__select--small"
                      >
                        {restBatchModes.map(mode => (
                          <option key={mode.value} value={mode.value}>{mode.label}</option>
                        ))}
                      </select>
                    </div>
                    <div className="carica-node__advanced-row">
                      <label>Batch size:</label>
                      <input
                        type="number"
                        value={restBatchSize}
                        onChange={(e) => handleUpdate('rest_batch_size', parseInt(e.target.value) || 1)}
                        min={1} max={10000} step={1}
                        className="carica-node__input carica-node__input--small"
                      />
                    </div>
                    <div className="carica-node__advanced-row">
                      <label>Timeout:</label>
                      <input
                        type="number"
                        value={restTimeoutSeconds}
                        onChange={(e) => handleUpdate('rest_timeout_seconds', parseInt(e.target.value) || 30)}
                        min={1} max={600} step={1}
                        className="carica-node__input carica-node__input--small"
                      />
                    </div>
                    <div className="carica-node__rest-header-list">
                      <div className="carica-node__mapping-actions">
                        <button type="button" onClick={handleAddRestHeader} className="carica-node__btn carica-node__btn--secondary">
                          + Header
                        </button>
                      </div>
                      {restHeaders.map((header, idx) => (
                        <div key={idx} className="carica-node__mapping-row">
                          <input
                            type="text"
                            value={header.name || ''}
                            onChange={(e) => handleRestHeaderChange(idx, 'name', e.target.value)}
                            placeholder="Header"
                            className="carica-node__mapping-input"
                          />
                          <input
                            type={header.secret ? 'password' : 'text'}
                            value={header.value || ''}
                            onChange={(e) => handleRestHeaderChange(idx, 'value', e.target.value)}
                            placeholder="Valore"
                            className="carica-node__mapping-input"
                          />
                          <label className="carica-node__secret-toggle" title="Marca come segreto">
                            <input
                              type="checkbox"
                              checked={!!header.secret}
                              onChange={(e) => handleRestHeaderChange(idx, 'secret', e.target.checked)}
                            />
                            Secret
                          </label>
                          <button type="button" onClick={() => handleRemoveRestHeader(idx)} className="carica-node__mapping-remove">
                            ×
                          </button>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </>
          )}

          {/* ============ PREVIEW / STATUS ============ */}
          {isValid && (
            <div className="carica-node__preview">
              <div className="carica-node__preview-item">
                <span className="carica-node__preview-label">Destinazione:</span>
                <span className="carica-node__preview-value">{destType === 'rest' ? restUrl : `${schemaName}.${tableName}`}</span>
              </div>
              <div className="carica-node__preview-item">
                <span className="carica-node__preview-label">Modalità:</span>
                <span className="carica-node__preview-value">
                  {destType === 'rest' ? restMethod : writeModes.find(m => m.value === writeMode)?.label}
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

          {/* ℹ️ Esecuzione automatica */}
          <div className="carica-node__auto-info">
            ⚡ Eseguito automaticamente con <strong>Esegui Query</strong>
          </div>
        </div>
      )}

      {/* ============ MODAL CONFLICT CHECKER (Portal) ============ */}
      {showConflictModal && createPortal(
        <div className="carica-conflict-modal__overlay" onClick={() => setShowConflictModal(false)}>
          <div
            ref={conflictDialogRef}
            className="carica-conflict-modal"
            onClick={e => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
            aria-labelledby="ccm-title"
            tabIndex={-1}
          >
            <div className="carica-conflict-modal__header">
              <span id="ccm-title">🔍 Report Conflitti — {schemaName}.{tableName}</span>
              <button className="carica-conflict-modal__close" onClick={() => setShowConflictModal(false)} aria-label="Chiudi">✕</button>
            </div>
            <div className="carica-conflict-modal__body">
              {conflictError ? (
                <div className="carica-conflict-modal__error">❌ {conflictError}</div>
              ) : conflictReport ? (
                <>
                  <div className={`carica-conflict-modal__summary ${conflictReport.has_conflicts ? 'has-conflicts' : 'no-conflicts'}`}>
                    {conflictReport.has_conflicts ? (
                      <>
                        <div>⚠️ <strong>{conflictReport.total_conflicts}</strong> conflitti trovati</div>
                        <div>• Duplicati interni: <strong>{conflictReport.internal_duplicates}</strong></div>
                        <div>• Conflitti con DB: <strong>{conflictReport.db_conflicts}</strong></div>
                      </>
                    ) : (
                      <div>✅ Nessun conflitto trovato — scrittura sicura</div>
                    )}
                  </div>
                  {conflictReport.details && conflictReport.details.length > 0 && (
                    <div className="carica-conflict-modal__details">
                      <div className="carica-conflict-modal__details-title">Dettaglio conflitti (max 100):</div>
                      <table className="carica-conflict-modal__table">
                        <thead>
                          <tr><th>Riga</th><th>Valori match</th><th>Tipo</th></tr>
                        </thead>
                        <tbody>
                          {conflictReport.details.slice(0, 20).map((d, i) => (
                            <tr key={i}>
                              <td>{d.row_index}</td>
                              <td>{Object.entries(d.match_values || {}).map(([k,v]) => `${k}=${v}`).join(', ')}</td>
                              <td>{d.conflict_type}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      {conflictReport.details.length > 20 && (
                        <div className="carica-conflict-modal__more">...e altri {conflictReport.details.length - 20} conflitti</div>
                      )}
                    </div>
                  )}
                  <div className="carica-conflict-modal__hint">
                    Policy conflitti attiva: <strong>{conflictPolicy}</strong>
                  </div>
                </>
              ) : null}
            </div>
            <div className="carica-conflict-modal__footer">
              <button className="carica-node__btn carica-node__btn--secondary" onClick={() => setShowConflictModal(false)}>Chiudi</button>
            </div>
          </div>
        </div>,
        document.body
      )}

    </div>
  );
});

CaricanNode.displayName = 'CaricanNode';

export default CaricanNode;
