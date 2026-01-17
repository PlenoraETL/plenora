import React, { useState, memo } from 'react';
import { Handle, Position } from 'reactflow';
import { getColumnTypeIcon, getColumnTypeLabel, getColumnSRID, isGeometryType } from '../utils/columnUtils';
import '../styles/ResultNode.css';

/**
 * Componente nodo risultato (output) UNIFICATO
 * Funziona sia per progetti SQL (estrai) che Pandas (manipola) che API (arricchisci)
 * 
 * Props:
 * - data.isManipulate: true per progetti Manipola
 * - data.isArricchisci: true per progetti Arricchisci
 * - data.fileName: nome file output
 * - data.format: formato (csv, json, xlsx)
 * - data.distinct: (solo SQL) usa DISTINCT
 * - data.limit: (solo SQL) limite righe
 * - data.columns: (solo SQL) colonne output calcolate (con type)
 * - data.onUpdateFileName: callback modifica nome
 * - data.onUpdateFormat: callback modifica formato
 * - data.onToggleDistinct: callback toggle DISTINCT
 * - data.onUpdateLimit: callback modifica LIMIT
 * - data.onExecute: callback esecuzione
 * - data.onDelete: callback eliminazione
 */
const ResultNode = memo(({ data, id }) => {
  const [isEditingFileName, setIsEditingFileName] = useState(false);
  const [tempFileName, setTempFileName] = useState(data.fileName || 'output');
  const [executionMode, setExecutionMode] = useState('cache');

  // Determina il tipo di progetto
  const isManipulate = data.isManipulate || false;
  const isArricchisci = data.isArricchisci || false;
  const isEstrai = !isManipulate && !isArricchisci;
  
  // Manipola o Arricchisci hanno opzioni cache/ricalcola
  const hasExecutionOptions = isManipulate || isArricchisci;

  const fileName = data.fileName || 'output';
  const format = data.format || 'csv';
  const limit = data.limit || null;
  const distinct = data.distinct || false;
  const selectedGeomColumn = data.geomColumn || null;

  // Verifica se il formato selezionato è geospaziale
  const isGeoFormat = ['geojson', 'gpkg', 'shp'].includes(format);

  // Estrai colonne geometriche disponibili
  const availableGeomColumns = (data.availableColumns || [])
    .filter(col => isGeometryType(col.type))
    .map(col => col.name || col.alias || col);

  // Determina variante CSS
  const nodeVariant = isArricchisci ? 'arricchisci' : (isManipulate ? 'manipola' : 'estrai');

  // Configurazione per tipo
  const config = {
    estrai: { icon: '📁', title: 'Output', executeLabel: 'Esegui Query' },
    manipola: { icon: '🔧', title: 'Output Manipola', executeLabel: 'Esegui Pipeline' },
    arricchisci: { icon: '🔗', title: 'Output Arricchisci', executeLabel: 'Esegui Arricchimento' }
  };

  const { icon, title, executeLabel } = config[nodeVariant];

  const formats = [
    { value: 'csv', label: 'CSV', icon: '📊', category: 'standard' },
    { value: 'json', label: 'JSON', icon: '📄', category: 'standard' },
    { value: 'xlsx', label: 'XLSX', icon: '📗', category: 'standard' },
    { value: 'geojson', label: 'GeoJSON', icon: '🌐', category: 'geo' },
    { value: 'gpkg', label: 'GeoPackage', icon: '📦', category: 'geo' },
    { value: 'shp', label: 'Shapefile', icon: '🗺️', category: 'geo' }
  ];

  const standardFormats = formats.filter(f => f.category === 'standard');
  const geoFormats = formats.filter(f => f.category === 'geo');

  // Handlers
  const handleSaveFileName = () => {
    if (data.onUpdateFileName) {
      data.onUpdateFileName(id, tempFileName.trim() || 'output');
    }
    setIsEditingFileName(false);
  };

  const handleChangeFormat = (newFormat) => {
    if (data.onUpdateFormat) {
      data.onUpdateFormat(id, newFormat);
    }
  };

  const handleChangeGeomColumn = (columnName) => {
    if (data.onUpdateGeomColumn) {
      data.onUpdateGeomColumn(id, columnName);
    }
  };

  const handleToggleDistinct = () => {
    if (data.onToggleDistinct) {
      data.onToggleDistinct(id, !distinct);
    }
  };

  const handleUpdateLimit = (newLimit) => {
    if (data.onUpdateLimit) {
      const limitValue = newLimit === '' ? null : parseInt(newLimit) || null;
      data.onUpdateLimit(id, limitValue);
    }
  };

  const handleDelete = () => {
    if (data.onDelete && confirm('Eliminare il nodo risultato?')) {
      data.onDelete(id);
    }
  };

  const handleExecute = () => {
    if (data.onExecute) {
      const options = hasExecutionOptions 
        ? { force_recalculate: executionMode === 'recalculate' }
        : {};
      data.onExecute(id, options);
    }
  };

  return (
    <div className={`result-node result-node--${nodeVariant}`}>
      {/* Handle input */}
      <Handle
        type="target"
        position={Position.Left}
        className="result-node__handle"
      />

      {/* Header */}
      <div className="result-node__header">
        <div className="result-node__title">
          <span className="result-node__title-icon">{icon}</span>
          <span>{title}</span>
        </div>
        <button
          onClick={handleDelete}
          className="result-node__delete-btn"
          title="Elimina nodo"
        >
          ×
        </button>
      </div>

      {/* Body */}
      <div className="result-node__body">
        {/* Nome file */}
        <div className="result-node__section">
          <div className="result-node__label">Nome file:</div>
          {isEditingFileName ? (
            <input
              type="text"
              value={tempFileName}
              onChange={(e) => setTempFileName(e.target.value)}
              onBlur={handleSaveFileName}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleSaveFileName();
                if (e.key === 'Escape') {
                  setIsEditingFileName(false);
                  setTempFileName(fileName);
                }
              }}
              autoFocus
              placeholder="nome file..."
              className="result-node__filename-input"
            />
          ) : (
            <div
              onClick={() => {
                setIsEditingFileName(true);
                setTempFileName(fileName);
              }}
              className="result-node__filename-display"
              title="Click per modificare"
            >
              {fileName}.{format}
            </div>
          )}
        </div>

        {/* Formato */}
        <div className="result-node__section">
          <div className="result-node__label">Formato:</div>
          {/* Formati standard */}
          <div className="result-node__format-group">
            {standardFormats.map(fmt => (
              <button
                key={fmt.value}
                onClick={() => handleChangeFormat(fmt.value)}
                className={`result-node__format-btn result-node__format-btn--${fmt.value} ${
                  format === fmt.value ? 'result-node__format-btn--active' : ''
                }`}
              >
                <span className="result-node__format-icon">{fmt.icon}</span>
                <span>{fmt.label}</span>
              </button>
            ))}
          </div>
          {/* Formati geospaziali - solo per Manipola */}
          {isManipulate && (
            <>
              <div className="result-node__label result-node__label--geo">🌐 Formati geospaziali:</div>
              <div className="result-node__format-group">
                {geoFormats.map(fmt => (
                  <button
                    key={fmt.value}
                    onClick={() => handleChangeFormat(fmt.value)}
                    className={`result-node__format-btn result-node__format-btn--${fmt.value} result-node__format-btn--geo ${
                      format === fmt.value ? 'result-node__format-btn--active' : ''
                    }`}
                  >
                    <span className="result-node__format-icon">{fmt.icon}</span>
                    <span>{fmt.label}</span>
                  </button>
                ))}
              </div>
              
              {/* Selezione colonna geometria - appare quando formato geo selezionato */}
              {isGeoFormat && availableGeomColumns.length > 0 && (
                <div className="result-node__geom-select">
                  <div className="result-node__label">📍 Colonna geometria:</div>
                  <select
                    value={selectedGeomColumn || availableGeomColumns[0] || ''}
                    onChange={(e) => handleChangeGeomColumn(e.target.value)}
                    className="result-node__geom-dropdown"
                  >
                    {availableGeomColumns.map(col => (
                      <option key={col} value={col}>{col}</option>
                    ))}
                  </select>
                  {availableGeomColumns.length > 1 && (
                    <div className="result-node__geom-hint">
                      {availableGeomColumns.length} colonne geometriche disponibili
                    </div>
                  )}
                </div>
              )}
              
              {/* Warning se formato geo ma nessuna colonna geometrica */}
              {isGeoFormat && availableGeomColumns.length === 0 && (
                <div className="result-node__geom-warning">
                  ⚠️ Nessuna colonna geometrica trovata
                </div>
              )}
            </>
          )}
        </div>

        {/* Colonne Output (solo SQL/Estrai) - ORA CON TIPI */}
        {isEstrai && data.columns && data.columns.length > 0 && (
          <div className="result-node__section result-node__section--bordered">
            <div className="result-node__label result-node__label--with-badge">
              <span>📋 Colonne output:</span>
              <span className="result-node__columns-badge">
                {data.columns.length}
              </span>
            </div>
            <div className="result-node__columns-list">
              {data.columns.map((col, idx) => (
                <div key={idx} className="result-node__column-item">
                  {/* Icona tipo colonna */}
                  <span 
                    className={`result-node__column-type-icon ${isGeometryType(col.type) ? 'result-node__column-type-icon--geo' : ''}`}
                    title={getColumnTypeLabel(col.type)}
                  >
                    {getColumnTypeIcon(col.type)}
                  </span>
                  <span className="result-node__column-table">
                    {col.source_table}
                  </span>
                  <span className="result-node__column-separator">.</span>
                  <span className="result-node__column-name">
                    {col.alias || col.name}
                  </span>
                  {/* Badge SRID per geometrie */}
                  {isGeometryType(col.type) && getColumnSRID(col.type) && (
                    <span className="result-node__column-srid-badge" title={`EPSG:${getColumnSRID(col.type)}`}>
                      {getColumnSRID(col.type)}
                    </span>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Opzioni Query SQL (DISTINCT + LIMIT) - solo Estrai */}
        {isEstrai && (
          <div className="result-node__section result-node__section--bordered">
            <div className="result-node__label">⚙️ Opzioni query:</div>

            {/* DISTINCT */}
            <label className="result-node__checkbox-label">
              <input
                type="checkbox"
                checked={distinct}
                onChange={handleToggleDistinct}
                className="result-node__checkbox"
              />
              <span>DISTINCT (rimuovi duplicati)</span>
            </label>

            {/* LIMIT */}
            <div>
              <label className="result-node__limit-label">
                Limita risultati:
              </label>
              <input
                type="number"
                value={limit || ''}
                onChange={(e) => handleUpdateLimit(e.target.value)}
                placeholder="Nessun limite"
                min="1"
                className="result-node__limit-input"
              />
            </div>
          </div>
        )}

        {/* Modalità Esecuzione (Manipola/Arricchisci) */}
        {hasExecutionOptions && (
          <div className="result-node__execution-options">
            <div className="result-node__label">Esecuzione:</div>
            
            <label className={`result-node__execution-option ${
              executionMode === 'cache' ? 'result-node__execution-option--selected' : ''
            }`}>
              <input 
                type="radio" 
                name={`executionMode-${id}`}
                value="cache"
                checked={executionMode === 'cache'}
                onChange={(e) => setExecutionMode(e.target.value)}
                className="result-node__execution-radio"
              />
              <div className="result-node__execution-content">
                <div className="result-node__execution-title">
                  ⚡ Usa cache esistente
                </div>
                <div className="result-node__execution-desc">
                  Veloce - carica dati già elaborati (se disponibili)
                </div>
              </div>
            </label>
            
            <label className={`result-node__execution-option ${
              executionMode === 'recalculate' ? 'result-node__execution-option--selected' : ''
            }`}>
              <input 
                type="radio" 
                name={`executionMode-${id}`}
                value="recalculate"
                checked={executionMode === 'recalculate'}
                onChange={(e) => setExecutionMode(e.target.value)}
                className="result-node__execution-radio"
              />
              <div className="result-node__execution-content">
                <div className="result-node__execution-title">
                  🔄 Ricalcola tutto
                </div>
                <div className="result-node__execution-desc">
                  {isArricchisci 
                    ? 'Aggiornato - riesegue tutte le chiamate API' 
                    : 'Aggiornato - riesegue tutte le query SQL'}
                </div>
              </div>
            </label>
          </div>
        )}

        {/* Bottone Esegui */}
        {data.onExecute && (
          <button
            onClick={handleExecute}
            className={`result-node__execute-btn result-node__execute-btn--${nodeVariant}`}
          >
            <span className="result-node__execute-icon">▶️</span>
            <span>{executeLabel}</span>
          </button>
        )}
      </div>
    </div>
  );
});

ResultNode.displayName = 'ResultNode';

export default ResultNode;