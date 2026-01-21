/**
 * ReprojectConfig.jsx - Configurazione per operazione Reproject
 */
import React from 'react';

const ReprojectConfig = ({ 
  config, 
  updateConfig, 
  validation,
  geometryColumns
}) => {
  const commonCRS = [
    { value: 'EPSG:4326', label: 'WGS 84 (EPSG:4326)' },
    { value: 'EPSG:3857', label: 'Web Mercator (EPSG:3857)' },
    { value: 'EPSG:32632', label: 'UTM Zone 32N (EPSG:32632)' },
    { value: 'EPSG:32633', label: 'UTM Zone 33N (EPSG:32633)' },
    { value: 'EPSG:25832', label: 'ETRS89 / UTM 32N (EPSG:25832)' },
    { value: 'EPSG:25833', label: 'ETRS89 / UTM 33N (EPSG:25833)' },
    { value: 'EPSG:6706', label: 'RDN2008 (EPSG:6706)' },
    { value: 'EPSG:6875', label: 'RDN2008 / UTM 32N (EPSG:6875)' },
    { value: 'EPSG:6876', label: 'RDN2008 / UTM 33N (EPSG:6876)' }
  ];

  if (!validation.valid) {
    return (
      <div className={`transform-node__message transform-node__message--${validation.type}`}>
        ⚠️ {validation.message}
      </div>
    );
  }

  return (
    <div className="transform-node__config">
      <div className="transform-node__config-group">
        <label className="transform-node__label">Colonna geometria:</label>
        <select
          value={config.geom_column || geometryColumns[0]?.name || ''}
          onChange={(e) => updateConfig('geom_column', e.target.value)}
          className="transform-node__select"
        >
          {geometryColumns.map((col, idx) => (
            <option key={idx} value={col.name}>🌐 {col.name}</option>
          ))}
        </select>
      </div>

      <div className="transform-node__config-group">
        <label className="transform-node__label">CRS di destinazione:</label>
        <select
          value={config.target_crs || 'EPSG:4326'}
          onChange={(e) => updateConfig('target_crs', e.target.value)}
          className="transform-node__select"
        >
          {commonCRS.map((crs, idx) => (
            <option key={idx} value={crs.value}>{crs.label}</option>
          ))}
        </select>
      </div>

      <div className="transform-node__config-group">
        <label className="transform-node__label">Oppure inserisci EPSG:</label>
        <input
          type="text"
          value={config.target_crs || ''}
          onChange={(e) => updateConfig('target_crs', e.target.value)}
          placeholder="EPSG:4326"
          className="transform-node__input"
        />
      </div>

      <div className="transform-node__confirm-box transform-node__confirm-box--spatial">
        <div className="transform-node__confirm-title">🌍 Configurazione Reproject:</div>
        <div className="transform-node__confirm-content">
          CRS destinazione: {config.target_crs || 'EPSG:4326'}
        </div>
      </div>
    </div>
  );
};

export default ReprojectConfig;
