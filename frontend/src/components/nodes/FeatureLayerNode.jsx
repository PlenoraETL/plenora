/**
 * FeatureLayerNode.jsx — Nodo canvas per layer ArcGIS Feature Service
 * 
 * Posizione: /src/components/FeatureLayerNode.jsx
 * 
 * Simile a TableNode ma per sorgenti ArcGIS:
 * - Header arancione/esri con icona geometria
 * - Lista campi con checkbox visibilità
 * - Filtro WHERE (sintassi Esri SQL)
 * - Badge connessione, conteggio features, tipo geometria
 * - Nessuna colonna calcolata (non applicabile)
 */

import React, { useState, useEffect, memo } from 'react';
import { Handle, Position } from 'reactflow';
import { getLayerSchema } from '../../api/features';
import useCanvasStore from '../../store/useCanvasStore';
import { useConfirm } from '../../context/ConfirmContext';
import './FeatureLayerNode.css';

// ============================================================
// HELPER
// ============================================================

const FIELD_ICONS = {
  'int': '🔢',
  'real': '🔢',
  'str': '🔤',
  'date': '📅',
  'datetime': '📅',
  'bool': '☑️',
  'oid': '🔑',
  'globalid': '🆔',
  'guid': '🆔',
};

const GEOM_ICONS = {
  'point': '📍',
  'multipoint': '📍',
  'linestring': '〰️',
  'multilinestring': '〰️',
  'polygon': '⬡',
  'multipolygon': '⬡',
};

function getFieldIcon(type) {
  if (!type) return '❓';
  const key = type.toLowerCase().replace('geometry:', '');
  if (type.toLowerCase().startsWith('geometry')) return GEOM_ICONS[key] || '🌐';
  return FIELD_ICONS[key] || '📝';
}

function getGeomShortLabel(geomType) {
  if (!geomType) return '';
  return geomType.replace('geometry:', '');
}

// ============================================================
// COMPONENTE
// ============================================================

const FeatureLayerNode = memo(({ data, id }) => {
  // State
  const [isExpanded, setIsExpanded] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [showWhereEditor, setShowWhereEditor] = useState(false);
  const [whereClause, setWhereClause] = useState(data.where || '');
  const [tempWhere, setTempWhere] = useState(data.where || '');
  const [loadingSchema, setLoadingSchema] = useState(false);

  // Props dal nodo
  const layerName = data.layer_name || 'Layer';
  const serviceName = data.service_name || '';
  const connectionName = data.connection_name || '';
  const geometryType = data.geometry_type || '';
  const featureCount = data.feature_count;
  const columns = data.columns || [];
  const relationships = data.relationships || [];
  const [addingRelation, setAddingRelation] = useState(null);
  
  // ============================================================
  // CARICA SCHEMA AL MOUNT (se colonne non ancora caricate)
  // ============================================================
  
  useEffect(() => {
    if (columns.length === 0 && data.layer_url && data.connection_id) {
      loadSchema();
    }
  }, []);

  const loadSchema = async () => {
    setLoadingSchema(true);
    try {
      const schema = await getLayerSchema(data.connection_id, data.layer_url);
      if (schema && schema.fields) {
        const columns = schema.fields.map(f => ({
          name:    f.name,
          type:    f.type || 'str',
          alias:   f.alias || null,
          visible: true,
        }));
        updateNodeData(id, { columns });
      }
    } catch (err) {
      console.error('Errore caricamento schema layer:', err);
    } finally {
      setLoadingSchema(false);
    }
  };

  // ============================================================
  // HANDLERS
  // ============================================================

  const toggleColumn     = useCanvasStore(s => s.toggleColumn);
  const toggleAllColumns = useCanvasStore(s => s.toggleAllColumns);
  const deleteNode       = useCanvasStore(s => s.deleteNode);
  const updateNodeData   = useCanvasStore(s => s.updateNodeData);
  const confirm          = useConfirm();

  const handleToggleColumn = (columnIndex) => toggleColumn(id, columnIndex);
  const handleToggleAll    = (makeVisible)  => toggleAllColumns(id, makeVisible);
  const handleDelete       = async () => {
    const ok = await confirm({
      title: 'Elimina nodo',
      message: 'Eliminare questo nodo?',
      confirmText: 'Elimina',
      variant: 'danger',
    });
    if (ok) deleteNode(id);
  };

  const handleSaveWhere = () => {
    const trimmed = tempWhere.trim();
    setWhereClause(trimmed);
    updateNodeData(id, { where: trimmed });
    setShowWhereEditor(false);
  };

  const handleCancelWhere = () => {
    setTempWhere(whereClause);
    setShowWhereEditor(false);
  };

  const handleAddRelated = async (rel) => {
    const onAddRelatedLayer = useCanvasStore.getState().editorActionsRef.current.onAddRelatedLayer;
    if (!onAddRelatedLayer) return;
    setAddingRelation(rel.id);
    try {
      await onAddRelatedLayer(id, rel);
    } catch (err) {
      console.error('Errore aggiunta related:', err);
    } finally {
      setAddingRelation(null);
    }
  };

  // ============================================================
  // COLUMN FILTERING & DISPLAY
  // ============================================================

  const filteredColumns = searchTerm
    ? columns.map((col, idx) => ({ ...col, _origIndex: idx }))
        .filter(col => col.name.toLowerCase().includes(searchTerm.toLowerCase()))
    : columns.map((col, idx) => ({ ...col, _origIndex: idx }));

  const displayColumns = (isExpanded || searchTerm) ? filteredColumns : filteredColumns.slice(0, 5);
  const hasMoreColumns = !searchTerm && columns.length > 5;

  const visibleCount = columns.filter(c => c.visible).length;
  const allVisible = visibleCount === columns.length;
  const noneVisible = visibleCount === 0;

  // ============================================================
  // RENDER
  // ============================================================

  return (
    <div className="fl-node">
      {/* Handle input (sinistra) */}
      <Handle
        type="target"
        position={Position.Left}
        className="fl-node__handle"
      />

      {/* Header */}
      <div className="fl-node__header">
        <div className="fl-node__header-content">
          <div className="fl-node__title">
            <span className="fl-node__title-icon">
              {GEOM_ICONS[getGeomShortLabel(geometryType)] || '🌐'}
            </span>
            {layerName}
          </div>
          <div className="fl-node__subtitle">
            {serviceName && <span className="fl-node__service-name">{serviceName}</span>}
            {geometryType && (
              <span className="fl-node__geom-badge">
                {getGeomShortLabel(geometryType)}
              </span>
            )}
            {featureCount != null && (
              <span className="fl-node__count-badge">
                {featureCount.toLocaleString()}
              </span>
            )}
          </div>
        </div>

        {/* Bottone elimina */}
        <button
          type="button"
          onClick={handleDelete}
          className="fl-node__delete-btn"
          title="Elimina nodo"
        >
          ×
        </button>
      </div>

      {/* Badge connessione */}
      <div className="fl-node__connection-badge">
        🌐 {connectionName}
      </div>

      {/* Body */}
      <div className="fl-node__body">
        {loadingSchema ? (
          <div className="fl-node__loading">Caricamento campi...</div>
        ) : (
          <>
            {/* Toolbar ricerca + toggle all */}
            {columns.length > 3 && (
              <div className="fl-node__toolbar">
                <div className="fl-node__search">
                  <input
                    type="text"
                    value={searchTerm}
                    onChange={(e) => setSearchTerm(e.target.value)}
                    placeholder="🔍 Cerca campi..."
                    className="fl-node__search-input"
                  />
                  {searchTerm && (
                    <button
                      type="button"
                      className="fl-node__search-clear"
                      onClick={() => setSearchTerm('')}
                    >
                      ×
                    </button>
                  )}
                </div>
                <div className="fl-node__toggle-all">
                  <label className="fl-node__toggle-all-label">
                    <input
                      type="checkbox"
                      checked={allVisible}
                      ref={(el) => { if (el) el.indeterminate = !allVisible && !noneVisible; }}
                      onChange={() => handleToggleAll(noneVisible || !allVisible)}
                    />
                    <span className="fl-node__toggle-all-text">
                      {visibleCount}/{columns.length}
                    </span>
                  </label>
                </div>
              </div>
            )}

            {/* Search results count */}
            {searchTerm && (
              <div className="fl-node__search-info">
                {filteredColumns.length} di {columns.length} campi
              </div>
            )}

            {/* Lista campi */}
            {displayColumns.length === 0 ? (
              <div className="fl-node__empty">
                {searchTerm ? 'Nessun campo trovato' : 'Nessun campo'}
              </div>
            ) : (
              displayColumns.map((column) => (
                <div
                  key={column._origIndex}
                  className={`fl-node__field ${!column.visible ? 'fl-node__field--hidden' : ''}`}
                >
                  <input
                    type="checkbox"
                    checked={column.visible}
                    onChange={() => handleToggleColumn(column._origIndex)}
                    className="fl-node__field-checkbox"
                  />
                  <span className="fl-node__field-icon" title={column.type}>
                    {getFieldIcon(column.type)}
                  </span>
                  <span className={`fl-node__field-name ${!column.visible ? 'fl-node__field-name--hidden' : ''}`}>
                    {column.name}
                  </span>
                </div>
              ))
            )}

            {/* Bottone espandi/collassa */}
            {hasMoreColumns && (
              <button
                type="button"
                onClick={() => setIsExpanded(!isExpanded)}
                className="fl-node__expand-btn"
              >
                {isExpanded 
                  ? '▲ Nascondi' 
                  : `▼ Mostra tutti (${columns.length})`
                }
              </button>
            )}

            {/* Sezione WHERE */}
            <div className="fl-node__where-section">
              {showWhereEditor ? (
                <div className="fl-node__where-editor">
                  <label className="fl-node__where-label">Filtro WHERE (Esri SQL):</label>
                  <textarea
                    value={tempWhere}
                    onChange={(e) => setTempWhere(e.target.value)}
                    placeholder="Es. ANNO > 2000 AND TIPO = 'residenziale'"
                    className="fl-node__where-textarea"
                    rows={3}
                  />
                  <div className="fl-node__where-actions">
                    <button type="button" 
                      className="fl-node__where-btn fl-node__where-btn--cancel"
                      onClick={handleCancelWhere}
                    >
                      Annulla
                    </button>
                    <button type="button" 
                      className="fl-node__where-btn fl-node__where-btn--save"
                      onClick={handleSaveWhere}
                    >
                      ✓ Applica
                    </button>
                  </div>
                </div>
              ) : (
                <button
                  type="button"
                  onClick={() => { setTempWhere(whereClause); setShowWhereEditor(true); }}
                  className={`fl-node__where-toggle ${whereClause ? 'fl-node__where-toggle--active' : ''}`}
                >
                  <span>🔍 Filtro WHERE</span>
                  {whereClause && (
                    <span className="fl-node__where-badge">✓</span>
                  )}
                </button>
              )}
            </div>

            {/* Sezione Relazioni (Related Tables) */}
            {relationships.length > 0 && (
              <div className="fl-node__relations-section">
                <div className="fl-node__relations-header">
                  <span>🔗 Relazioni</span>
                  <span className="fl-node__relations-badge">{relationships.length}</span>
                </div>
                <div className="fl-node__relations-list">
                  {relationships.map(rel => (
                    <div key={rel.id} className="fl-node__relation-item">
                      <div className="fl-node__relation-info">
                        <span className="fl-node__relation-name">{rel.name}</span>
                        <span className="fl-node__relation-detail">
                          {rel.cardinality} · {rel.key_field} → {rel.key_field_in_relationship_table || rel.key_field}
                        </span>
                      </div>
                      <button
                        type="button"
                        className="fl-node__relation-add-btn"
                        onClick={() => handleAddRelated(rel)}
                        disabled={addingRelation === rel.id}
                        title="Aggiungi tabella relata con join automatico"
                      >
                        {addingRelation === rel.id ? '⏳' : '+ Join'}
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>

      {/* Handle output (destra) */}
      <Handle
        type="source"
        position={Position.Right}
        className="fl-node__handle"
      />
    </div>
  );
});

FeatureLayerNode.displayName = 'FeatureLayerNode';

export default FeatureLayerNode;
