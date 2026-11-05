/**
 * FileSourceNode.jsx — Nodo canvas per file caricato dall'utente
 *
 * Rappresenta un file (CSV, Excel, GeoJSON, Shapefile, GeoPackage) come
 * sorgente nel canvas. Mostra schema colonne e permette di selezionare
 * layer/sheet per formati multi-layer.
 *
 * Usato in: ManipolaEditor, ArricchisciEditor
 */
import React, { useEffect, memo } from 'react';
import { useResource } from '../../hooks/useResource';
import { Position } from '@xyflow/react';
import { getFile } from '../../api/files';
import { getColumnTypeIcon, getColumnTypeLabel, normalizeColumnType } from '../../utils/columnUtils';
import { humanizeError } from '../../utils/errorMessages';
import useCanvasStore from '../../store/useCanvasStore';
import './FileSourceNode.css';
import NodeHandle from '../canvas/NodeHandle';
import { formatNumber } from '../../utils/format';
import { pressable } from '../../utils/pressable';

/**
 * FileSourceNode - Nodo sorgente per file esterni in MANIPOLA
 * 
 * Simile a ProjectSourceNode ma per file CSV, Excel, GeoJSON, GDB, etc.
 * Il file viene selezionato dal drag&drop dalla sidebar.
 */
const FileSourceNode = ({ id, data }) => {
  // Fase 5 T2: dettagli del file dall'hook comune (id di richiesta: il
  // file scelto prima non scrive le colonne del file scelto dopo).
  const resource = useResource(() => getFile(data.file_source_id), {
    key: ['file-source', data.file_source_id || null],
    enabled: Boolean(data.file_source_id),
    errorMessage: (err) => humanizeError(err).message,
  });
  const fileInfo = resource.data || null;
  const loading = resource.initialLoading;
  const error = resource.error;
  useEffect(() => {
    if (!fileInfo?.columns?.length) return;
    const columnsWithTypes = fileInfo.columns.map(col => ({
      name: col.name,
      type: normalizeColumnType(col),
      visible: true,
    }));
    // Schema letto dal file: dato DERIVATO (non rende il canvas modificato).
    useCanvasStore.getState().updateNodeData(id, { columns: columnsWithTypes }, { derived: true });
  }, [fileInfo, id]);

  const handleToggleColumn = (colName) => {
    if (!data.columns) return;
    const updatedColumns = data.columns.map(col => 
      col.name === colName ? { ...col, visible: col.visible === false } : col
    );
    useCanvasStore.getState().updateNodeData(id, { columns: updatedColumns });
  };

  // Conta colonne visibili
  const visibleCount = data.columns?.filter(c => c.visible !== false).length || 0;
  const totalCount = data.columns?.length || 0;

  // Icona formato
  const getFormatIcon = (format) => {
    const icons = {
      'csv': '📄',
      'xlsx': '📊',
      'json': '📋',
      'geojson': '🗺️',
      'gpkg': '🗺️',
      'shp': '🗺️',
      'gdb': '🗃️'
    };
    return icons[format] || '📁';
  };

  return (
    <div className="file-source-node">
      
      {/* Nome file. Titolo, chiusura ed eliminazione sono del pannello
          (NodeInspector, regola 6 del Blocco A). */}
      <div className="file-source-node__name">
        {fileInfo ? (
          <>
            <span className="file-source-node__format-icon">
              {getFormatIcon(fileInfo.format)}
            </span>
            {fileInfo.name}
          </>
        ) : (
          data.file_source_id?.split('_')[1] || 'Caricamento...'
        )}
      </div>

      {/* Info formato */}
      {fileInfo && (
        <div className="file-source-node__format-info">
          <span 
            className="file-source-node__format-badge"
            style={{ 
              background: fileInfo.format === 'csv' ? '#4CAF50' :
                         fileInfo.format === 'xlsx' ? '#2E7D32' :
                         fileInfo.format === 'json' ? '#FF9800' :
                         fileInfo.format === 'geojson' ? '#2196F3' :
                         fileInfo.format === 'gpkg' ? '#9C27B0' :
                         fileInfo.format === 'gdb' ? '#673AB7' : '#666'
            }}
          >
            {fileInfo.format.toUpperCase()}
          </span>
          <span className="file-source-node__row-count">
            {formatNumber(fileInfo.row_count)} righe
          </span>
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="file-source-node__loading">
          ⏳ Caricamento...
        </div>
      )}

      {/* Errore */}
      {error && (
        <div className="file-source-node__error">
          ⚠️ {error}
        </div>
      )}

      {/* RB.4.C: file_source non accessibile al gruppo del progetto.
          Backend (RB.4.B) marca data.access_denied=true sui canvas
          storici sporchi. UX: banner visibile, no rimozione automatica. */}
      {data.access_denied && (
        <div
          className="file-source-node__error"
          role="alert"
          title={data.access_denied_reason || 'File sorgente non accessibile'}
        >
          🔒 File sorgente non accessibile al tuo gruppo.
          {data.access_denied_reason ? (
            <div style={{ fontSize: '0.85em', marginTop: 4, opacity: 0.85 }}>
              {data.access_denied_reason}
            </div>
          ) : null}
        </div>
      )}

      {/* Colonne */}
      {data.columns && data.columns.length > 0 && !loading && (
        <div className="file-source-node__columns-section">
          <div className="file-source-node__columns-header">
            <span>Colonne ({visibleCount}/{totalCount}):</span>
            <span className="file-source-node__columns-hint">
              clic per escludere
            </span>
          </div>
          
          <div className="file-source-node__columns-list nowheel">
            {data.columns.map((col, idx) => {
              const colType = normalizeColumnType(col);
              return (
                <div
                  key={idx}
                  {...pressable(() => handleToggleColumn(col.name))}
                  className={`file-source-node__column ${col.visible === false ? 'file-source-node__column--hidden' : ''}`}
                  title={getColumnTypeLabel(colType)}
                >
                  <span className={`file-source-node__column-name ${col.visible === false ? 'file-source-node__column-name--hidden' : ''}`}>
                    <span className="file-source-node__column-icon">
                      {getColumnTypeIcon(colType)}
                    </span>
                    {col.name}
                  </span>
                  
                  <span className={`file-source-node__column-type file-source-node__column-type--${colType.split(':')[0]}`}>
                    {colType}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Nessuna colonna */}
      {(!data.columns || data.columns.length === 0) && !loading && !error && fileInfo && (
        <div className="file-source-node__no-columns">
          ⚠️ Nessuna colonna trovata.
        </div>
      )}

      {/* Info layer/sheet */}
      {fileInfo && (fileInfo.layer || fileInfo.sheet) && (
        <div className="file-source-node__layer-info">
          {fileInfo.layer && <span>Layer: {fileInfo.layer}</span>}
          {fileInfo.sheet && <span>Foglio: {fileInfo.sheet}</span>}
        </div>
      )}

      {/* Handle output */}
      <NodeHandle 
        type="source" 
        position={Position.Right}
        className="file-source-node__handle"
      />
    </div>
  );
};

export default memo(FileSourceNode);