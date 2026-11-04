/**
 * OutputSettingsPanel.jsx — Sezione "output settings" del Result node
 * Componente presentational locale: copre filename + format
 * (standard + geo) + geometry column + (solo per editor estrai)
 * distinct/limit. Niente fetch, niente API, niente global state.
 *
 * State locale (UI editing toggle): `isEditingFileName` /
 * `tempFileName`. Sono puro UI scope-locale al filename editor,
 * non escapano dal panel.
 *
 * Costanti di rendering (`FORMATS` / `STANDARD_FORMATS` /
 * `GEO_FORMATS`) co-locate qui perche' usate solo da questo panel.
 *
 * I CSS `result-node__*` restano in `ResultNode.css` (shared con
 * il parent).
 *
 * `children`: slot opzionale renderizzato tra il blocco "Formato"
 * e il blocco "Opzioni query (DISTINCT + LIMIT)". Serve a preservare
 * l'ordine visivo originale dove il parent inserisce la preview
 * "Colonne Output" (non output-setting, gestita dal parent).
 */
import React, { useState, useEffect } from 'react';

const FORMATS = [
  { value: 'csv', label: 'CSV', icon: '📊', category: 'standard' },
  { value: 'json', label: 'JSON', icon: '📄', category: 'standard' },
  { value: 'xlsx', label: 'XLSX', icon: '📗', category: 'standard' },
  { value: 'geojson', label: 'GeoJSON', icon: '🌐', category: 'geo' },
  { value: 'gpkg', label: 'GeoPackage', icon: '📦', category: 'geo' },
  { value: 'shp', label: 'Shapefile', icon: '🗺️', category: 'geo' },
];
const STANDARD_FORMATS = FORMATS.filter(f => f.category === 'standard');
const GEO_FORMATS = FORMATS.filter(f => f.category === 'geo');

export default function OutputSettingsPanel({
  data,
  id,
  updateNodeData,
  isEstrai,
  groupByEnabled,
  availableGeomColumns,
  hasGeometryColumns,
  isGeoFormat,
  children,
}) {
  const fileName = data.fileName || 'output';
  const format = data.format || 'csv';
  const distinct = data.distinct || false;
  const limit = data.limit || null;
  const selectedGeomColumn = data.geomColumn || null;

  const [isEditingFileName, setIsEditingFileName] = useState(false);
  const [tempFileName, setTempFileName] = useState(fileName);

  // Sync `tempFileName` se il `fileName` esterno cambia mentre non
  // si sta editando (es. update del nodo da un'altra fonte).
  useEffect(() => {
    if (!isEditingFileName) setTempFileName(fileName);
  }, [fileName, isEditingFileName]);

  const handleSaveFileName = () => {
    updateNodeData(id, { fileName: tempFileName.trim() || 'output' });
    setIsEditingFileName(false);
  };
  const handleChangeFormat = (newFormat) => updateNodeData(id, { format: newFormat });
  const handleChangeGeomColumn = (columnName) => updateNodeData(id, { geomColumn: columnName });
  const handleToggleDistinct = () => updateNodeData(id, { distinct: !distinct });
  const handleUpdateLimit = (newLimit) => {
    const limitValue = newLimit === '' ? null : parseInt(newLimit) || null;
    updateNodeData(id, { limit: limitValue });
  };

  return (
    <>
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
            title="Clic per modificare"
          >
            {fileName}.{format}
          </div>
        )}
      </div>

      <div className="result-node__section">
        <div className="result-node__label">Formato:</div>
        <div className="result-node__format-group">
          {STANDARD_FORMATS.map(fmt => (
            <button type="button"
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
        {hasGeometryColumns && (
          <>
            <div className="result-node__label result-node__label--geo">🌐 Formati geospaziali:</div>
            <div className="result-node__format-group">
              {GEO_FORMATS.map(fmt => (
                <button type="button"
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
              </div>
            )}

            {isGeoFormat && availableGeomColumns.length === 0 && (
              <div className="result-node__geom-warning">
                ⚠️ Nessuna colonna geometrica trovata
              </div>
            )}
          </>
        )}
      </div>

      {/* Slot per la preview "Colonne Output" gestita dal parent */}
      {children}

      {/* Opzioni Query SQL (DISTINCT + LIMIT) — solo ESTRAI */}
      {isEstrai && (
        <div className="result-node__section result-node__section--bordered">
          <div className="result-node__label">⚙️ Opzioni query:</div>

          <label className={`result-node__checkbox-label ${groupByEnabled ? 'result-node__checkbox-label--disabled' : ''}`}>
            <input
              type="checkbox"
              checked={distinct}
              onChange={handleToggleDistinct}
              disabled={groupByEnabled}
              className="result-node__checkbox"
            />
            <span>DISTINCT (rimuovi duplicati)</span>
          </label>

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
    </>
  );
}
