/**
 * FileSourceNode.jsx — Nodo canvas per file caricato dall'utente
 *
 * Rappresenta un file (CSV, Excel, GeoJSON, Shapefile, GeoPackage) come
 * sorgente nel canvas. Mostra schema colonne e permette di selezionare
 * layer/sheet per formati multi-layer.
 *
 * Usato in: ManipolaEditor, ArricchisciEditor
 */
import React, { useState, useEffect, memo } from 'react';
import { Handle, Position } from 'reactflow';
import { getFile } from '../../api/files';
import { getColumnTypeIcon, getColumnTypeLabel, normalizeColumnType } from '../../utils/columnUtils';
import useCanvasStore from '../../store/useCanvasStore';
import './FileSourceNode.css';

/**
 * FileSourceNode - Nodo sorgente per file esterni in MANIPOLA
 * 
 * Simile a ProjectSourceNode ma per file CSV, Excel, GeoJSON, GDB, etc.
 * Il file viene selezionato dal drag&drop dalla sidebar.
 */
const FileSourceNode = ({ id, data }) => {
  const [fileInfo, setFileInfo] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // Carica dettagli file quando cambia file_source_id
  useEffect(() => {
    if (data.file_source_id) {
      loadFileDetails(data.file_source_id);
    }
  }, [data.file_source_id]);


  const loadFileDetails = async (fileId) => {
    if (!fileId) return;
    
    setLoading(true);
    setError(null);
    
    try {
      const fileData = await getFile(fileId);
      
      setFileInfo(fileData);
      
      // Costruisci colonne con tipi normalizzati
      let columnsWithTypes = [];
      
      if (fileData.columns && fileData.columns.length > 0) {
        columnsWithTypes = fileData.columns.map(col => ({
          name: col.name,
          type: normalizeColumnType(col),
          visible: true
        }));
      }
      
      // Usa updateNodeData dallo store direttamente
      if (columnsWithTypes.length > 0) {
        useCanvasStore.getState().updateNodeData(id, { columns: columnsWithTypes });
      }
      
    } catch (err) {
      console.error(`[FileSourceNode] Errore caricamento file ${fileId}:`, err);
      setError(`Errore: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  const handleToggleColumn = (colName) => {
    if (!data.columns) return;
    const updatedColumns = data.columns.map(col => 
      col.name === colName ? { ...col, visible: !col.visible } : col
    );
    useCanvasStore.getState().updateNodeData(id, { columns: updatedColumns });
  };

  const handleDelete = () => {
    useCanvasStore.getState().deleteNode(id);
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
      {/* Header */}
      <div className="file-source-node__header">
        <div className="file-source-node__title">
          📁 File Esterno
        </div>
        <button type="button"
          onClick={handleDelete}
          className="file-source-node__delete-btn"
          title="Elimina nodo"
        >
          ×
        </button>
      </div>
      
      {/* Nome file */}
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
            {fileInfo.row_count?.toLocaleString()} righe
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

      {/* Colonne */}
      {data.columns && data.columns.length > 0 && !loading && (
        <div className="file-source-node__columns-section">
          <div className="file-source-node__columns-header">
            <span>Colonne ({visibleCount}/{totalCount}):</span>
            <span className="file-source-node__columns-hint">
              click per escludere
            </span>
          </div>
          
          <div className="file-source-node__columns-list nowheel">
            {data.columns.map((col, idx) => {
              const colType = normalizeColumnType(col);
              return (
                <div
                  key={idx}
                  onClick={() => handleToggleColumn(col.name)}
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
          {fileInfo.sheet && <span>Sheet: {fileInfo.sheet}</span>}
        </div>
      )}

      {/* Handle output */}
      <Handle 
        type="source" 
        position={Position.Right}
        className="file-source-node__handle"
      />
    </div>
  );
};

export default memo(FileSourceNode);