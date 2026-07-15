/**
 * FileManager/helpers.js — costanti/helper condivisi tra FileManager.jsx
 * (page orchestrator) e i subcomponents (FileCard, UploadModal, LayerModal,
 * PreviewModal).
 */

export const FORMAT_CONFIG = {
  csv:     { icon: '📄', label: 'CSV',     color: '#34C17A', rgb: '52,193,122' },
  xlsx:    { icon: '📊', label: 'XLSX',    color: '#34C17A', rgb: '52,193,122' },
  xls:     { icon: '📊', label: 'XLS',     color: '#34C17A', rgb: '52,193,122' },
  json:    { icon: '📋', label: 'JSON',    color: '#4B8BFF', rgb: '75,139,255' },
  geojson: { icon: '🗺',  label: 'GeoJSON', color: '#4B8BFF', rgb: '75,139,255' },
  gpkg:    { icon: '📦', label: 'GPKG',    color: '#F5A623', rgb: '245,166,35' },
  shp:     { icon: '🔷', label: 'SHP',     color: '#9B6DFF', rgb: '155,109,255' },
  gdb:     { icon: '🗄',  label: 'GDB',     color: '#F87171', rgb: '248,113,113' },
};

export const getFormatConfig = (fmt) =>
  FORMAT_CONFIG[fmt] || {
    icon: '📁',
    label: (fmt || '').toUpperCase(),
    color: '#6B7280',
    rgb: '107,114,128',
  };

export const formatFileSize = (bytes) => {
  if (!bytes) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
};

export const SPATIAL_FORMATS = ['gpkg', 'shp', 'gdb', 'geojson'];

export const resolveMultiSourcePreview = (preview = {}) => {
  const sheets = preview.sheets || [];
  const layers = preview.layers || [];
  const sourceKind = sheets.length > 0 ? 'sheet' : (layers.length > 0 ? 'layer' : null);
  return {
    sourceKind,
    itemList: sourceKind === 'sheet' ? sheets : layers,
  };
};

export const buildMultiSourceSelection = ({
  checkedLayers,
  layerColumns,
  layerSelectedCols,
  isSheet,
}) => {
  const checkedNames = Object.entries(checkedLayers)
    .filter(([, checked]) => checked)
    .map(([name]) => name);
  const emptySelection = checkedNames.find(
    name => (layerColumns[name]?.length || 0) > 0
      && (layerSelectedCols[name]?.length || 0) === 0
  );
  const config = checkedNames.map(name => ({
    [isSheet ? 'sheet' : 'layer']: name,
    selected_columns: (layerColumns[name]?.length || 0) > 0
      ? layerSelectedCols[name]
      : null,
  }));
  return { config, emptySelection };
};
