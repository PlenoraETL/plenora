/**
 * JoinModal.jsx — Configurazione join tra due nodi
 *
 * Aperto automaticamente quando si trascina un edge tra due nodi nel canvas.
 * Configura: tipo join (INNER/LEFT/RIGHT/FULL), colonne di join con funzioni
 * opzionali (LOWER, TRIM...), filtro post-join, suffissi per colonne duplicate.
 *
 * Usato in: EstraiEditor, ManipolaEditor
 */
import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { useToast } from '../../hooks/useToast';
import { getColumnTypeIcon, getColumnTypeLabel } from '../../utils/columnUtils';
import ModalWrapper, { ModalButton } from '../common/ModalWrapper';
import { useConfirm } from '../../context/ConfirmContext';
import './JoinModal.css';

/**
 * Modal per configurare join tra due nodi
 * Supporta sia join standard (su colonne chiave) che join spaziali (predicati geometrici)
 * Supporta condizioni multiple con AND per join standard
 * 
 * REFACTORED: Usa ModalWrapper per struttura base
 */
function JoinModal({ edge, sourceNode, targetNode, onSave, onCancel, isEditing = false, onDelete }) {
  const toast = useToast();
  // State comune
  const [joinMode, setJoinMode] = useState(edge?.data?.is_spatial ? 'spatial' : 'standard');
  const [joinType, setJoinType] = useState(edge?.data?.join_type || 'left');
  
  // State per join standard - condizioni multiple (array di {source_column, target_column, source_func, target_func})
  const [joinConditions, setJoinConditions] = useState(() => {
    // Inizializza da edge esistente (nuovo formato)
    if (edge?.data?.join_conditions && edge.data.join_conditions.length > 0) {
      return edge.data.join_conditions.map(c => ({
        source_column: c.source_column || '',
        target_column: c.target_column || '',
        source_func: c.source_func || '',
        target_func: c.target_func || ''
      }));
    }
    // Backward compatibility: singola coppia
    if (edge?.data?.source_column && edge?.data?.target_column) {
      return [{ source_column: edge.data.source_column, target_column: edge.data.target_column, source_func: '', target_func: '' }];
    }
    // Default: una condizione vuota
    return [{ source_column: '', target_column: '', source_func: '', target_func: '' }];
  });
  
  // State per join spaziale
  const [spatialPredicate, setSpatialPredicate] = useState(edge?.data?.spatial_predicate || 'ST_Intersects');
  const [sourceGeomColumn, setSourceGeomColumn] = useState(edge?.data?.source_geom_column || '');
  const [targetGeomColumn, setTargetGeomColumn] = useState(edge?.data?.target_geom_column || '');
  const [spatialDistance, setSpatialDistance] = useState(edge?.data?.spatial_distance || 100);

  // State per filtri ON aggiuntivi (condizioni extra nella clausola ON)
  const [onFilters, setOnFilters] = useState(() => {
    if (edge?.data?.on_filters && edge.data.on_filters.length > 0) {
      return edge.data.on_filters;
    }
    return [];
  });

  // Estrai colonne visibili dai nodi
  const sourceColumns = sourceNode?.data?.columns || [];
  const targetColumns = targetNode?.data?.columns || [];
  
  // Colonne calcolate
  const sourceCalculated = sourceNode?.data?.calculated_columns || [];
  const targetCalculated = targetNode?.data?.calculated_columns || [];

  // Filtra solo colonne geometriche (incluse calcolate)
  const sourceGeomColumns = useMemo(() => {
    const normalGeom = sourceColumns.filter(c => (c.type || '').startsWith('geometry'));
    const calcGeom = sourceCalculated.filter(c => 
      (c.output_type || '').startsWith('geometry')
    ).map(c => ({ name: c.name, type: c.output_type, isCalculated: true }));
    return [...normalGeom, ...calcGeom];
  }, [sourceColumns, sourceCalculated]);

  const targetGeomColumns = useMemo(() => {
    const normalGeom = targetColumns.filter(c => (c.type || '').startsWith('geometry'));
    const calcGeom = targetCalculated.filter(c => 
      (c.output_type || '').startsWith('geometry')
    ).map(c => ({ name: c.name, type: c.output_type, isCalculated: true }));
    return [...normalGeom, ...calcGeom];
  }, [targetColumns, targetCalculated]);

  // Auto-seleziona colonne se non impostate
  useEffect(() => {
    if (joinMode === 'standard') {
      // Auto-seleziona solo per la prima condizione se vuota
      if (joinConditions.length === 1 && !joinConditions[0].source_column && !joinConditions[0].target_column) {
        const newConditions = [...joinConditions];
        if (sourceColumns.length > 0) {
          newConditions[0] = { ...newConditions[0], source_column: sourceColumns[0].name };
        }
        if (targetColumns.length > 0) {
          newConditions[0] = { ...newConditions[0], target_column: targetColumns[0].name };
        }
        if (newConditions[0].source_column || newConditions[0].target_column) {
          setJoinConditions(newConditions);
        }
      }
    } else {
      if (!sourceGeomColumn && sourceGeomColumns.length > 0) {
        setSourceGeomColumn(sourceGeomColumns[0].name);
      }
      if (!targetGeomColumn && targetGeomColumns.length > 0) {
        setTargetGeomColumn(targetGeomColumns[0].name);
      }
    }
  }, [joinMode, sourceColumns, targetColumns, sourceGeomColumns, targetGeomColumns]);
  const updateCondition = useCallback((index, field, value) => {
    setJoinConditions(prev => {
      const updated = [...prev];
      updated[index] = { ...updated[index], [field]: value };
      return updated;
    });
  }, []);

  const addCondition = useCallback(() => {
    setJoinConditions(prev => [...prev, { source_column: '', target_column: '', source_func: '', target_func: '' }]);
  }, []);

  const removeCondition = useCallback((index) => {
    setJoinConditions(prev => {
      if (prev.length <= 1) return prev; // Almeno una condizione
      return prev.filter((_, i) => i !== index);
    });
  }, []);
  // Tutte le colonne della tabella target (non solo visibili)
  const allTargetColumns = targetNode?.data?.columns || [];

  const onFilterOperators = [
    { value: '=', label: '=', needsValue: true },
    { value: '<>', label: '≠ (<>)', needsValue: true },
    { value: '>', label: '>', needsValue: true },
    { value: '<', label: '<', needsValue: true },
    { value: '>=', label: '≥ (>=)', needsValue: true },
    { value: '<=', label: '≤ (<=)', needsValue: true },
    { value: 'IS NULL', label: 'IS NULL', needsValue: false },
    { value: 'IS NOT NULL', label: 'IS NOT NULL', needsValue: false },
    { value: 'LIKE', label: 'LIKE', needsValue: true },
    { value: 'IN', label: 'IN', needsValue: true }
  ];

  const onFilterFunctions = [
    { value: '', label: 'Nessuna' },
    { value: 'TRIM', label: 'TRIM()' },
    { value: 'LTRIM_RTRIM', label: 'LTRIM(RTRIM())' },
    { value: 'UPPER', label: 'UPPER()' },
    { value: 'LOWER', label: 'LOWER()' },
    { value: 'COALESCE_EMPTY', label: "COALESCE(, '')" }
  ];

  const addOnFilter = useCallback(() => {
    setOnFilters(prev => [...prev, { column: '', operator: 'IS NULL', value: '', func: '' }]);
  }, []);

  const updateOnFilter = useCallback((index, field, value) => {
    setOnFilters(prev => {
      const updated = [...prev];
      updated[index] = { ...updated[index], [field]: value };
      return updated;
    });
  }, []);

  const removeOnFilter = useCallback((index) => {
    setOnFilters(prev => prev.filter((_, i) => i !== index));
  }, []);

  const handleSave = () => {
    // Valida filtri ON (rimuovi quelli senza colonna)
    const validOnFilters = onFilters.filter(f => f.column);

    if (joinMode === 'standard') {
      // Filtra solo condizioni complete
      const validConditions = joinConditions.filter(c => c.source_column && c.target_column);
      if (validConditions.length === 0) {
        toast.warning('Seleziona entrambe le colonne per almeno una condizione di join');
        return;
      }
      onSave({
        join_type: joinType,
        // Backward compatibility: prima condizione nei campi legacy
        source_column: validConditions[0].source_column,
        target_column: validConditions[0].target_column,
        // Tutte le condizioni nel nuovo campo
        join_conditions: validConditions,
        // Filtri ON aggiuntivi
        on_filters: validOnFilters.length > 0 ? validOnFilters : null,
        is_spatial: false
      });
    } else {
      if (!sourceGeomColumn || !targetGeomColumn) {
        toast.warning('Seleziona entrambe le colonne geometriche per il join spaziale');
        return;
      }
      onSave({
        join_type: joinType,
        is_spatial: true,
        spatial_predicate: spatialPredicate,
        source_geom_column: sourceGeomColumn,
        target_geom_column: targetGeomColumn,
        // spatial_distance solo per ST_DWithin — per gli altri predicati non serve
        // e causerebbe un ST_Buffer indesiderato nella query generata
        spatial_distance: spatialPredicate === 'ST_DWithin' ? spatialDistance : null,
        // Filtri ON aggiuntivi (anche per join spaziali)
        on_filters: validOnFilters.length > 0 ? validOnFilters : null
      });
    }
  };

  const confirm = useConfirm();

  const handleDelete = async () => {
    const ok = await confirm({
      title: 'Elimina relazione join',
      message: 'Eliminare questa relazione join?',
      confirmText: 'Elimina',
      variant: 'danger',
    });
    if (ok) {
      onDelete(edge.id);
    }
  };

  // Nomi tabelle
  // Nomi tabelle (alias > table_name > fallback)
  const sourceName = sourceNode?.data?.alias || sourceNode?.data?.table_name || sourceNode?.data?.tableName || 'Sorgente';
  const targetName = targetNode?.data?.alias || targetNode?.data?.table_name || targetNode?.data?.tableName || 'Target';

  // Lista funzioni applicabili alle condizioni JOIN
  const joinFunctions = [
    { value: '', label: '—' },
    { value: 'TRIM', label: 'TRIM()' },
    { value: 'LTRIM_RTRIM', label: 'LTRIM(RTRIM())' },
    { value: 'UPPER', label: 'UPPER()' },
    { value: 'LOWER', label: 'LOWER()' },
    { value: 'COALESCE_EMPTY', label: "COALESCE(, '')" }
  ];

  // Tipi di join disponibili
  const joinTypes = [
    { value: 'inner', label: 'INNER', description: 'Solo righe con match in entrambe' },
    { value: 'left', label: 'LEFT', description: 'Tutte da sinistra + match da destra' },
    { value: 'right', label: 'RIGHT', description: 'Tutte da destra + match da sinistra' },
    { value: 'full', label: 'FULL', description: 'Tutte le righe da entrambe' }
  ];

  // Predicati spaziali
  const spatialPredicates = [
    { value: 'ST_Intersects', label: 'Interseca', description: 'Le geometrie si toccano o sovrappongono' },
    { value: 'ST_Contains', label: 'Contiene', description: 'La prima contiene completamente la seconda' },
    { value: 'ST_Within', label: 'Dentro', description: 'La prima è completamente dentro la seconda' },
    { value: 'ST_Touches', label: 'Tocca', description: 'Le geometrie si toccano al bordo' },
    { value: 'ST_Crosses', label: 'Attraversa', description: 'Le geometrie si incrociano' },
    { value: 'ST_Overlaps', label: 'Sovrappone', description: 'Le geometrie si sovrappongono parzialmente' },
    { value: 'ST_DWithin', label: 'Entro distanza', description: 'Geometrie entro una distanza specificata' }
  ];

  // Helper per preview filtro ON
  const renderFilterExpression = (filter) => {
    let col = filter.column;
    if (filter.func === 'TRIM') col = `TRIM(${col})`;
    else if (filter.func === 'LTRIM_RTRIM') col = `LTRIM(RTRIM(${col}))`;
    else if (filter.func === 'UPPER') col = `UPPER(${col})`;
    else if (filter.func === 'LOWER') col = `LOWER(${col})`;
    else if (filter.func === 'COALESCE_EMPTY') col = `COALESCE(${col}, '')`;

    const op = filter.operator;
    if (op === 'IS NULL' || op === 'IS NOT NULL') {
      return `${targetName}.${col} ${op}`;
    }
    return `${targetName}.${col} ${op} '${filter.value}'`;
  };

  // Helper per wrappare colonna con funzione
  const wrapWithFunc = (expr, func) => {
    if (!func) return expr;
    if (func === 'TRIM') return `TRIM(${expr})`;
    if (func === 'LTRIM_RTRIM') return `LTRIM(RTRIM(${expr}))`;
    if (func === 'UPPER') return `UPPER(${expr})`;
    if (func === 'LOWER') return `LOWER(${expr})`;
    if (func === 'COALESCE_EMPTY') return `COALESCE(${expr}, '')`;
    return expr;
  };

  // Preview SQL
  const renderSqlPreview = () => {
    const validOnFilters = onFilters.filter(f => f.column);

    if (joinMode === 'standard') {
      const validConditions = joinConditions.filter(c => c.source_column && c.target_column);
      if (validConditions.length === 0 && validOnFilters.length === 0) return null;
      return (
        <>
          <span className="join-modal__sql-keyword"> {joinType.toUpperCase()} JOIN </span>
          {targetName}
          <span className="join-modal__sql-on"> ON </span>
          {validConditions.map((cond, idx) => (
            <React.Fragment key={idx}>
              {idx > 0 && <span className="join-modal__sql-and"> AND </span>}
              {wrapWithFunc(`${sourceName}.${cond.source_column}`, cond.source_func)}
              <span className="join-modal__sql-on"> = </span>
              {wrapWithFunc(`${targetName}.${cond.target_column}`, cond.target_func)}
            </React.Fragment>
          ))}
          {validOnFilters.map((filter, idx) => (
            <React.Fragment key={`f_${idx}`}>
              <span className="join-modal__sql-and"> AND </span>
              <span className="join-modal__sql-filter">{renderFilterExpression(filter)}</span>
            </React.Fragment>
          ))}
        </>
      );
    } else {
      if (!sourceGeomColumn || !targetGeomColumn) return null;
      let predicate = `${spatialPredicate}(${sourceName}.${sourceGeomColumn}, ${targetName}.${targetGeomColumn})`;
      if (spatialPredicate === 'ST_DWithin') {
        predicate = `${spatialPredicate}(${sourceName}.${sourceGeomColumn}, ${targetName}.${targetGeomColumn}, ${spatialDistance})`;
      }
      return (
        <>
          <span className="join-modal__sql-keyword"> {joinType.toUpperCase()} JOIN </span>
          {targetName}
          <span className="join-modal__sql-on"> ON </span>
          <span className="join-modal__sql-spatial">{predicate}</span>
        </>
      );
    }
  };

  // Verifica se ci sono colonne geometriche disponibili
  const hasSpatialColumns = sourceGeomColumns.length > 0 && targetGeomColumns.length > 0;
  
  const isDisabled = joinMode === 'standard' 
    ? joinConditions.every(c => !c.source_column || !c.target_column)
    : (!sourceGeomColumn || !targetGeomColumn);

  // Footer con pulsanti
  const footer = (
    <div className={`join-modal__actions ${isEditing ? 'join-modal__actions--with-delete' : ''}`}>
      {isEditing && onDelete && (
        <ModalButton variant="danger" onClick={handleDelete}>
          🗑️ Elimina Join
        </ModalButton>
      )}
      
      <div className="join-modal__actions-right">
        <ModalButton variant="secondary" onClick={onCancel}>
          Annulla
        </ModalButton>
        <ModalButton variant="primary" onClick={handleSave} disabled={isDisabled}>
          {isEditing ? 'Salva Modifiche' : 'Conferma Join'}
        </ModalButton>
      </div>
    </div>
  );

  return (
    <ModalWrapper
      isOpen={true}
      onClose={onCancel}
      title={isEditing ? 'Modifica Join' : 'Configura Join'}
      icon="🔗"
      size="md"
      variant="estrai"
      footer={footer}
      className="join-modal"
    >
      {/* Info nodi */}
      <div className="join-modal__nodes-info">
        <div className="join-modal__node-row">
          <span className="join-modal__node-label">Da:</span> {sourceName}
          <span className="join-modal__node-count">
            ({sourceColumns.length} colonne{sourceGeomColumns.length > 0 && `, ${sourceGeomColumns.length} geom`})
          </span>
        </div>
        <div className="join-modal__node-row">
          <span className="join-modal__node-label">A:</span> {targetName}
          <span className="join-modal__node-count">
            ({targetColumns.length} colonne{targetGeomColumns.length > 0 && `, ${targetGeomColumns.length} geom`})
          </span>
        </div>
      </div>

      {/* Toggle Standard / Spaziale */}
      <div className="join-modal__mode-toggle">
        <button
          type="button"
          className={`join-modal__mode-btn ${joinMode === 'standard' ? 'join-modal__mode-btn--active' : ''}`}
          onClick={() => setJoinMode('standard')}
        >
          🔗 Standard
        </button>
        <button
          type="button"
          className={`join-modal__mode-btn ${joinMode === 'spatial' ? 'join-modal__mode-btn--active' : ''}`}
          onClick={() => setJoinMode('spatial')}
          disabled={!hasSpatialColumns}
          title={!hasSpatialColumns ? 'Nessuna colonna geometrica disponibile' : ''}
        >
          🌐 Spaziale
        </button>
      </div>

      {/* Tipo Join */}
      <div className="join-modal__section">
        <label className="join-modal__section-label">Tipo Join</label>
        <div className="join-modal__join-types">
          {joinTypes.map(type => (
            <label
              key={type.value}
              className={`join-modal__join-type ${
                joinType === type.value ? 'join-modal__join-type--selected' : ''
              }`}
            >
              <input
                type="radio"
                name="joinType"
                value={type.value}
                checked={joinType === type.value}
                onChange={(e) => setJoinType(e.target.value)}
              />
              <div className="join-modal__join-type-content">
                <span className="join-modal__join-type-label">{type.label}</span>
                <span className="join-modal__join-type-desc">{type.description}</span>
              </div>
            </label>
          ))}
        </div>
      </div>

      {/* Configurazione Standard - Condizioni multiple */}
      {joinMode === 'standard' && (
        <div className="join-modal__section">
          <label className="join-modal__section-label">Colonne di Join</label>
          
          {joinConditions.map((condition, index) => (
            <div key={index} className="join-modal__condition-row">
              {/* Label AND per condizioni successive */}
              {index > 0 && (
                <div className="join-modal__condition-and-label">AND</div>
              )}
              
              <div className="join-modal__columns-grid join-modal__columns-grid--with-func">
                {/* SOURCE: funzione + colonna */}
                <div className="join-modal__column-select">
                  {index === 0 && <label>Colonna sorgente ({sourceName})</label>}
                  <div className="join-modal__col-with-func">
                    <select
                      className="join-modal__func-select"
                      value={condition.source_func || ''}
                      onChange={(e) => updateCondition(index, 'source_func', e.target.value)}
                      title="Funzione da applicare"
                    >
                      {joinFunctions.map(f => (
                        <option key={f.value} value={f.value}>{f.label}</option>
                      ))}
                    </select>
                    <select 
                      value={condition.source_column}
                      onChange={(e) => updateCondition(index, 'source_column', e.target.value)}
                    >
                      <option value="">-- Seleziona --</option>
                      {sourceColumns.map((col, idx) => (
                        <option key={idx} value={col.name}>
                          {getColumnTypeIcon(col.type)} {sourceName}.{col.name}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
                
                <div className="join-modal__column-equals">=</div>
                
                {/* TARGET: funzione + colonna */}
                <div className="join-modal__column-select">
                  {index === 0 && <label>Colonna target ({targetName})</label>}
                  <div className="join-modal__col-with-func">
                    <select
                      className="join-modal__func-select"
                      value={condition.target_func || ''}
                      onChange={(e) => updateCondition(index, 'target_func', e.target.value)}
                      title="Funzione da applicare"
                    >
                      {joinFunctions.map(f => (
                        <option key={f.value} value={f.value}>{f.label}</option>
                      ))}
                    </select>
                    <select 
                      value={condition.target_column}
                      onChange={(e) => updateCondition(index, 'target_column', e.target.value)}
                    >
                      <option value="">-- Seleziona --</option>
                      {targetColumns.map((col, idx) => (
                        <option key={idx} value={col.name}>
                          {getColumnTypeIcon(col.type)} {targetName}.{col.name}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>

                {/* Pulsante rimuovi condizione (solo se più di una) */}
                {joinConditions.length > 1 && (
                  <button
                    type="button"
                    className="join-modal__condition-remove"
                    onClick={() => removeCondition(index)}
                    title="Rimuovi condizione"
                  >
                    ✕
                  </button>
                )}
              </div>
            </div>
          ))}

          {/* Pulsante aggiungi condizione */}
          <button
            type="button"
            className="join-modal__add-condition"
            onClick={addCondition}
          >
            + Aggiungi condizione (AND)
          </button>
        </div>
      )}

      {/* Filtri ON aggiuntivi (per join standard e spaziale) */}
      {joinMode === 'standard' && (
        <div className="join-modal__section">
          <label className="join-modal__section-label">
            Condizioni aggiuntive ON
            <span className="join-modal__section-hint">
              Filtri applicati nella clausola ON del join (diverso da WHERE con LEFT JOIN)
            </span>
          </label>

          {onFilters.map((filter, index) => (
            <div key={index} className="join-modal__on-filter-row">
              {index > 0 && (
                <div className="join-modal__condition-and-label">AND</div>
              )}
              
              <div className="join-modal__on-filter-grid">
                {/* Funzione opzionale */}
                <div className="join-modal__on-filter-func">
                  <select
                    value={filter.func || ''}
                    onChange={(e) => updateOnFilter(index, 'func', e.target.value)}
                    title="Funzione da applicare alla colonna"
                  >
                    {onFilterFunctions.map(f => (
                      <option key={f.value} value={f.value}>{f.label}</option>
                    ))}
                  </select>
                </div>

                {/* Colonna target */}
                <div className="join-modal__on-filter-col">
                  <select
                    value={filter.column}
                    onChange={(e) => updateOnFilter(index, 'column', e.target.value)}
                  >
                    <option value="">-- Colonna ({targetName}) --</option>
                    {allTargetColumns.map((col, idx) => (
                      <option key={idx} value={col.name}>
                        {getColumnTypeIcon(col.type)} {col.name}
                      </option>
                    ))}
                  </select>
                </div>
                
                {/* Operatore */}
                <div className="join-modal__on-filter-op">
                  <select
                    value={filter.operator}
                    onChange={(e) => updateOnFilter(index, 'operator', e.target.value)}
                  >
                    {onFilterOperators.map(op => (
                      <option key={op.value} value={op.value}>{op.label}</option>
                    ))}
                  </select>
                </div>
                
                {/* Valore (solo se operatore richiede valore) */}
                {onFilterOperators.find(op => op.value === filter.operator)?.needsValue && (
                  <div className="join-modal__on-filter-value">
                    <input
                      type="text"
                      value={filter.value || ''}
                      onChange={(e) => updateOnFilter(index, 'value', e.target.value)}
                      placeholder="Valore..."
                    />
                  </div>
                )}

                {/* Rimuovi */}
                <button
                  type="button"
                  className="join-modal__condition-remove"
                  onClick={() => removeOnFilter(index)}
                  title="Rimuovi filtro"
                >
                  ✕
                </button>
              </div>
            </div>
          ))}

          <button
            type="button"
            className="join-modal__add-on-filter"
            onClick={addOnFilter}
          >
            + Aggiungi filtro ON
          </button>
        </div>
      )}

      {/* Configurazione Spaziale */}
      {joinMode === 'spatial' && (
        <>
          <div className="join-modal__section">
            <label className="join-modal__section-label">Predicato Spaziale</label>
            <div className="join-modal__spatial-predicates">
              {spatialPredicates.map(pred => (
                <label
                  key={pred.value}
                  className={`join-modal__spatial-predicate ${
                    spatialPredicate === pred.value ? 'join-modal__spatial-predicate--selected' : ''
                  }`}
                >
                  <input
                    type="radio"
                    name="spatialPredicate"
                    value={pred.value}
                    checked={spatialPredicate === pred.value}
                    onChange={(e) => setSpatialPredicate(e.target.value)}
                  />
                  <div className="join-modal__spatial-predicate-content">
                    <span className="join-modal__spatial-predicate-label">{pred.label}</span>
                    <span className="join-modal__spatial-predicate-desc">{pred.description}</span>
                  </div>
                </label>
              ))}
            </div>
          </div>

          {/* Distanza per ST_DWithin */}
          {spatialPredicate === 'ST_DWithin' && (
            <div className="join-modal__section">
              <label className="join-modal__section-label">
                Distanza (in unità del sistema di riferimento)
              </label>
              <input
                type="number"
                value={spatialDistance}
                onChange={(e) => setSpatialDistance(Number(e.target.value))}
                min="0"
                step="1"
                className="join-modal__distance-input"
              />
            </div>
          )}

          <div className="join-modal__section">
            <label className="join-modal__section-label">Colonne Geometriche</label>
            <div className="join-modal__columns-grid">
              <div className="join-modal__column-select">
                <label>Geometria sorgente ({sourceName})</label>
                <select 
                  value={sourceGeomColumn}
                  onChange={(e) => setSourceGeomColumn(e.target.value)}
                >
                  <option value="">-- Seleziona --</option>
                  {sourceGeomColumns.map((col, idx) => (
                    <option key={idx} value={col.name}>
                      🌐 {col.name} {col.isCalculated && '(calc)'}
                    </option>
                  ))}
                </select>
              </div>
              
              <div className="join-modal__column-equals">⟷</div>
              
              <div className="join-modal__column-select">
                <label>Geometria target ({targetName})</label>
                <select 
                  value={targetGeomColumn}
                  onChange={(e) => setTargetGeomColumn(e.target.value)}
                >
                  <option value="">-- Seleziona --</option>
                  {targetGeomColumns.map((col, idx) => (
                    <option key={idx} value={col.name}>
                      🌐 {col.name} {col.isCalculated && '(calc)'}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </div>
        </>
      )}

      {/* Preview SQL */}
      <div className="join-modal__preview">
        <div className="join-modal__preview-label">Preview SQL:</div>
        <div className="join-modal__preview-sql">
          {sourceName}
          {renderSqlPreview()}
        </div>
      </div>
    </ModalWrapper>
  );
}

export default JoinModal;
