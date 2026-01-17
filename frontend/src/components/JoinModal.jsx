import React, { useState, useEffect, useMemo } from 'react';
import { getColumnTypeIcon, getColumnTypeLabel } from '../utils/columnUtils';
import '../styles/JoinModal.css';

/**
 * Modal per configurare join tra due nodi
 * Supporta sia join standard (su colonne chiave) che join spaziali (predicati geometrici)
 */
function JoinModal({ edge, sourceNode, targetNode, onSave, onCancel, isEditing = false, onDelete }) {
  // State comune
  const [joinMode, setJoinMode] = useState(edge?.data?.is_spatial ? 'spatial' : 'standard');
  const [joinType, setJoinType] = useState(edge?.data?.join_type || 'left');
  
  // State per join standard
  const [sourceColumn, setSourceColumn] = useState(edge?.data?.source_column || '');
  const [targetColumn, setTargetColumn] = useState(edge?.data?.target_column || '');
  
  // State per join spaziale
  const [spatialPredicate, setSpatialPredicate] = useState(edge?.data?.spatial_predicate || 'ST_Intersects');
  const [sourceGeomColumn, setSourceGeomColumn] = useState(edge?.data?.source_geom_column || '');
  const [targetGeomColumn, setTargetGeomColumn] = useState(edge?.data?.target_geom_column || '');
  const [spatialDistance, setSpatialDistance] = useState(edge?.data?.spatial_distance || 100);

  // Estrai colonne visibili dai nodi
  const sourceColumns = sourceNode?.data?.columns?.filter(col => col.visible) || [];
  const targetColumns = targetNode?.data?.columns?.filter(col => col.visible) || [];
  
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
      if (!sourceColumn && sourceColumns.length > 0) {
        setSourceColumn(sourceColumns[0].name);
      }
      if (!targetColumn && targetColumns.length > 0) {
        setTargetColumn(targetColumns[0].name);
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

  const handleSave = () => {
    if (joinMode === 'standard') {
      if (!sourceColumn || !targetColumn) {
        alert('Seleziona entrambe le colonne per il join');
        return;
      }
      onSave({
        join_type: joinType,
        source_column: sourceColumn,
        target_column: targetColumn,
        is_spatial: false
      });
    } else {
      if (!sourceGeomColumn || !targetGeomColumn) {
        alert('Seleziona entrambe le colonne geometriche per il join spaziale');
        return;
      }
      onSave({
        join_type: joinType,
        is_spatial: true,
        spatial_predicate: spatialPredicate,
        source_geom_column: sourceGeomColumn,
        target_geom_column: targetGeomColumn,
        spatial_distance: spatialPredicate === 'ST_DWithin' ? spatialDistance : null
      });
    }
  };

  const handleDelete = () => {
    if (confirm('Eliminare questa connessione?')) {
      onDelete();
    }
  };

  const joinTypes = [
    { value: 'left', label: 'LEFT JOIN', description: 'Tutti i record da sinistra' },
    { value: 'inner', label: 'INNER JOIN', description: 'Solo record che matchano' },
    { value: 'right', label: 'RIGHT JOIN', description: 'Tutti i record da destra' },
    { value: 'outer', label: 'FULL OUTER', description: 'Tutti i record da entrambi' }
  ];

  const spatialPredicates = [
    { value: 'ST_Intersects', label: 'Intersects', description: 'Geometrie che si toccano o sovrappongono' },
    { value: 'ST_Within', label: 'Within', description: 'A completamente dentro B' },
    { value: 'ST_Contains', label: 'Contains', description: 'A contiene completamente B' },
    { value: 'ST_DWithin', label: 'DWithin', description: 'A entro una distanza da B' },
    { value: 'ST_Touches', label: 'Touches', description: 'Geometrie che si toccano al bordo' },
    { value: 'ST_Overlaps', label: 'Overlaps', description: 'Geometrie che si sovrappongono parzialmente' }
  ];

  const sourceName = sourceNode?.data?.alias || sourceNode?.data?.table_name;
  const targetName = targetNode?.data?.alias || targetNode?.data?.table_name;

  // Helper per ottenere il tipo della colonna selezionata
  const getSelectedColumnType = (columns, columnName) => {
    const col = columns.find(c => c.name === columnName);
    return col?.type || 'str';
  };

  const sourceColType = getSelectedColumnType(sourceColumns, sourceColumn);
  const targetColType = getSelectedColumnType(targetColumns, targetColumn);

  // Genera preview SQL
  const getSqlPreview = () => {
    if (joinMode === 'standard') {
      if (!sourceColumn || !targetColumn) return null;
      return (
        <>
          {sourceName}
          <span className="join-modal__sql-keyword"> {joinType.toUpperCase()} JOIN </span>
          {targetName}
          <span className="join-modal__sql-on"> ON </span>
          {sourceName}.{sourceColumn}
          <span className="join-modal__sql-on"> = </span>
          {targetName}.{targetColumn}
        </>
      );
    } else {
      if (!sourceGeomColumn || !targetGeomColumn) return null;
      const srcGeom = `${sourceName}.${sourceGeomColumn}`;
      const tgtGeom = `${targetName}.${targetGeomColumn}`;
      
      let predicate;
      if (spatialPredicate === 'ST_DWithin') {
        predicate = `ST_DWithin(${srcGeom}, ${tgtGeom}, ${spatialDistance})`;
      } else {
        predicate = `${spatialPredicate}(${srcGeom}, ${tgtGeom})`;
      }
      
      return (
        <>
          {sourceName}
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

  return (
    <div className="modal-overlay" onClick={onCancel}>
      <div className="modal join-modal" onClick={(e) => e.stopPropagation()}>
        <h3 className="join-modal__title">
          {isEditing ? 'Modifica Join' : 'Configura Join'}
        </h3>

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
                  className="join-modal__join-type-radio"
                />
                <div className="join-modal__join-type-content">
                  <div className="join-modal__join-type-name">{type.label}</div>
                  <div className="join-modal__join-type-desc">{type.description}</div>
                </div>
              </label>
            ))}
          </div>
        </div>

        {/* === JOIN STANDARD === */}
        {joinMode === 'standard' && (
          <>
            {/* Colonna sorgente */}
            <div className="form-group">
              <label>Colonna sorgente ({sourceName})</label>
              <select
                value={sourceColumn}
                onChange={(e) => setSourceColumn(e.target.value)}
                className="join-modal__select"
              >
                <option value="">-- Seleziona colonna --</option>
                {sourceColumns.map((col, idx) => (
                  <option key={idx} value={col.name}>
                    {getColumnTypeIcon(col.type)} {col.name}{col.alias ? ` → ${col.alias}` : ''} ({getColumnTypeLabel(col.type)})
                  </option>
                ))}
              </select>
            </div>

            {/* Colonna destinazione */}
            <div className="form-group">
              <label>Colonna destinazione ({targetName})</label>
              <select
                value={targetColumn}
                onChange={(e) => setTargetColumn(e.target.value)}
                className="join-modal__select"
              >
                <option value="">-- Seleziona colonna --</option>
                {targetColumns.map((col, idx) => (
                  <option key={idx} value={col.name}>
                    {getColumnTypeIcon(col.type)} {col.name}{col.alias ? ` → ${col.alias}` : ''} ({getColumnTypeLabel(col.type)})
                  </option>
                ))}
              </select>
            </div>

            {/* Warning se tipi non corrispondono */}
            {sourceColumn && targetColumn && sourceColType !== targetColType && (
              <div className="join-modal__type-warning">
                ⚠️ Attenzione: stai facendo join tra tipi diversi 
                ({getColumnTypeLabel(sourceColType)} ↔ {getColumnTypeLabel(targetColType)})
              </div>
            )}
          </>
        )}

        {/* === JOIN SPAZIALE === */}
        {joinMode === 'spatial' && (
          <>
            {/* Predicato spaziale */}
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
                      className="join-modal__spatial-predicate-radio"
                    />
                    <div className="join-modal__spatial-predicate-content">
                      <div className="join-modal__spatial-predicate-name">{pred.label}</div>
                      <div className="join-modal__spatial-predicate-desc">{pred.description}</div>
                    </div>
                  </label>
                ))}
              </div>
            </div>

            {/* Distanza (solo per ST_DWithin) */}
            {spatialPredicate === 'ST_DWithin' && (
              <div className="form-group">
                <label>Distanza (metri)</label>
                <input
                  type="number"
                  value={spatialDistance}
                  onChange={(e) => setSpatialDistance(parseFloat(e.target.value) || 0)}
                  min="0"
                  step="10"
                  className="join-modal__input"
                />
              </div>
            )}

            {/* Colonna geometria sorgente */}
            <div className="form-group">
              <label>Geometria sorgente ({sourceName})</label>
              <select
                value={sourceGeomColumn}
                onChange={(e) => setSourceGeomColumn(e.target.value)}
                className="join-modal__select"
              >
                <option value="">-- Seleziona geometria --</option>
                {sourceGeomColumns.map((col, idx) => (
                  <option key={idx} value={col.name}>
                    {getColumnTypeIcon(col.type)} {col.name} 
                    {col.isCalculated && ' ⚡'}
                    ({getColumnTypeLabel(col.type)})
                  </option>
                ))}
              </select>
            </div>

            {/* Colonna geometria destinazione */}
            <div className="form-group">
              <label>Geometria destinazione ({targetName})</label>
              <select
                value={targetGeomColumn}
                onChange={(e) => setTargetGeomColumn(e.target.value)}
                className="join-modal__select"
              >
                <option value="">-- Seleziona geometria --</option>
                {targetGeomColumns.map((col, idx) => (
                  <option key={idx} value={col.name}>
                    {getColumnTypeIcon(col.type)} {col.name}
                    {col.isCalculated && ' ⚡'}
                    ({getColumnTypeLabel(col.type)})
                  </option>
                ))}
              </select>
            </div>

            {/* Info sulle colonne calcolate */}
            {(sourceGeomColumns.some(c => c.isCalculated) || targetGeomColumns.some(c => c.isCalculated)) && (
              <div className="join-modal__calc-info">
                ⚡ Le colonne con questo simbolo sono colonne calcolate
              </div>
            )}
          </>
        )}

        {/* Preview SQL */}
        {getSqlPreview() && (
          <div className="join-modal__preview">
            <div className="join-modal__preview-label">Preview SQL:</div>
            <div className="join-modal__preview-sql">
              {getSqlPreview()}
            </div>
          </div>
        )}

        {/* Azioni */}
        <div className={`join-modal__actions ${isEditing ? 'join-modal__actions--with-delete' : ''}`}>
          {/* Bottone elimina (solo in edit mode) */}
          {isEditing && onDelete && (
            <button 
              className="join-modal__btn join-modal__btn--danger" 
              onClick={handleDelete}
            >
              🗑️ Elimina Join
            </button>
          )}
          
          <div className="join-modal__actions-right">
            <button 
              className="join-modal__btn join-modal__btn--cancel" 
              onClick={onCancel}
            >
              Annulla
            </button>
            <button 
              className="join-modal__btn join-modal__btn--primary" 
              onClick={handleSave}
              disabled={
                joinMode === 'standard' 
                  ? (!sourceColumn || !targetColumn)
                  : (!sourceGeomColumn || !targetGeomColumn)
              }
            >
              {isEditing ? 'Salva Modifiche' : 'Conferma Join'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default JoinModal;