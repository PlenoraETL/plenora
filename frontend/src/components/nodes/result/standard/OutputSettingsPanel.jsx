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
import React, { useState } from 'react';
import OptionSelect from '../../../common/OptionSelect';
import FieldInput from '../../../common/FieldInput';
import { fileStem, integer } from '../../../../utils/fieldCodecs';
import { OUTPUT_FORMATS } from '../../../../constants/outputFormats';

// Catalogo unico dei formati (constants/outputFormats, R8.11).
const STANDARD_FORMATS = OUTPUT_FORMATS.filter(f => !f.geo);
const FILE_STEM = fileStem({ extensions: OUTPUT_FORMATS.map(f => f.value), optional: true, empty: 'output' });
const LIMIT = integer({ min: 1, optional: true });
const GEO_FORMATS = OUTPUT_FORMATS.filter(f => f.geo);

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

  // Nome file e limite dal campo unico (FieldInput + utils/fieldCodecs,
  // R8.23): niente separatori di percorso, l'estensione scritta si toglie,
  // il limite e' un intero >= 1 o vuoto (nessun limite), mai negativo.
  const [isEditingFileName, setIsEditingFileName] = useState(false);
  const handleSaveFileName = (stem) => updateNodeData(id, { fileName: stem });
  // Si chiude solo con un nome valido; con un nome non valido resta aperto
  // con l'errore (FieldInput). Esc chiude senza salvare.
  const closeIfValid = (text) => { if (!FILE_STEM.parse(text).error) setIsEditingFileName(false); };
  const handleChangeFormat = (newFormat) => updateNodeData(id, { format: newFormat });
  const handleChangeGeomColumn = (columnName) => updateNodeData(id, { geomColumn: columnName });
  const handleToggleDistinct = () => updateNodeData(id, { distinct: !distinct });
  const handleUpdateLimit = (limitValue) => updateNodeData(id, { limit: limitValue });


  return (
    <>
      <div className="result-node__section">
        <div className="result-node__label">Nome file:</div>
        {isEditingFileName ? (
          <FieldInput
            codec={FILE_STEM}
            value={fileName}
            onCommit={handleSaveFileName}
            commitOn="blur"
            onBlur={(e) => closeIfValid(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') closeIfValid(e.target.value);
              if (e.key === 'Escape') setIsEditingFileName(false);
            }}
            autoFocus
            placeholder="nome file..."
            aria-label="Nome file"
            className="result-node__filename-input"
          />
        ) : (
          <div
            onClick={() => setIsEditingFileName(true)}
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
                <OptionSelect
                  value={selectedGeomColumn || ''}
                  options={availableGeomColumns}
                  placeholder="Scegli la colonna geometria…"
                  onChange={(e) => handleChangeGeomColumn(e.target.value)}
                  className="result-node__geom-dropdown"
                />
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
            <FieldInput
              codec={LIMIT}
              value={limit}
              onCommit={handleUpdateLimit}
              placeholder="Nessun limite"
              aria-label="Limita risultati"
              className="result-node__limit-input"
            />
          </div>
        </div>
      )}
    </>
  );
}
