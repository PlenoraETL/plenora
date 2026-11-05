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

import { useResource } from '../../hooks/useResource';
import '../common/ListState.css';
import React, { useState, useEffect, memo } from 'react';
import { Position } from '@xyflow/react';
import { getLayerSchema } from '../../api/features';
import useCanvasStore from '../../store/useCanvasStore';
import { useStoreDraft } from '../../hooks/useStoreDraft';
import { featureLayerColumns } from '../../utils/featureLayers';
import { isColumnVisible } from '../../utils/columnUtils';
import './FeatureLayerNode.css';
import NodeHandle from '../canvas/NodeHandle';
import { READ_ONLY_SAFE } from '../common/readOnlySafe';
import SearchField, { SearchEmpty } from '../common/SearchField';
import { useSearch } from '../../hooks/useSearch';
import { formatNumber } from '../../utils/format';

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

const FeatureLayerNode = memo(({ data, id }) => {
  // State
  const [isExpanded, setIsExpanded] = useState(false);
  const search = useSearch();
  // WHERE: il valore e' quello dello store; la bozza vive solo mentre
  // l'editor e' aperto (hooks/useStoreDraft, R1.9: un Ctrl+Z la scarta).
  const whereClause = data.where || '';
  const whereDraft = useStoreDraft(whereClause);
  const showWhereEditor = whereDraft.editing;

  // Props dal nodo
  const serviceName = data.service_name || '';
  const connectionName = data.connection_name || '';
  const geometryType = data.geometry_type || '';
  const featureCount = data.feature_count;
  const columns = data.columns || [];
  const relationships = data.relationships || [];
  const [addingRelation, setAddingRelation] = useState(null);
  
  // Fase 5 T2 (N10): schema del layer dall'hook comune, solo se il layer
  // non ha ancora colonne. Errore visibile con «Riprova» (prima solo in
  // console, e il layer restava con "Nessun campo").
  const schemaResource = useResource(() => getLayerSchema(data.connection_id, data.layer_url), {
    key: ['layer-schema', data.connection_id || null, data.layer_url || null],
    enabled: columns.length === 0 && Boolean(data.layer_url && data.connection_id),
  });
  const loadingSchema = schemaResource.initialLoading;
  const schemaData = schemaResource.data;
  useEffect(() => {
    if (!schemaData?.fields) return;
    // Layer salvati senza colonne (prima di T6 il trascinamento le lasciava
    // vuote): stessa costruzione del trascinamento (utils/featureLayers).
    // Schema caricato dal servizio: dato derivato, non dirty (audit R1.8).
    updateNodeData(id, (d) => ({
      columns: featureLayerColumns(schemaData.fields),
      ...(d.relationships?.length ? {} : { relationships: schemaData.relationships || [] }),
    }), { derived: true });
  }, [schemaData]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggleColumn     = useCanvasStore(s => s.toggleColumn);
  const toggleAllColumns = useCanvasStore(s => s.toggleAllColumns);
  const updateNodeData   = useCanvasStore(s => s.updateNodeData);

  const handleToggleColumn = (columnIndex) => toggleColumn(id, columnIndex);
  const handleToggleAll    = (makeVisible)  => toggleAllColumns(id, makeVisible);

  const handleSaveWhere = () => {
    whereDraft.commit(value => updateNodeData(id, { where: String(value).trim() }));
  };

  const handleCancelWhere = () => {
    whereDraft.cancel();
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

  const filteredColumns = search.filter(columns.map((col, idx) => ({ ...col, _origIndex: idx })), col => col.name);

  const displayColumns = (isExpanded || search.active) ? filteredColumns : filteredColumns.slice(0, 5);
  const hasMoreColumns = !search.active && columns.length > 5;

  const visibleCount = columns.filter(isColumnVisible).length;
  const allVisible = visibleCount === columns.length;
  const noneVisible = visibleCount === 0;

  return (
    <div className="fl-node">
      {/* Handle input (sinistra) */}
      <NodeHandle
        type="target"
        position={Position.Left}
        className="fl-node__handle"
      />

      {/* Servizio, geometria e numero di elementi. Titolo, chiusura ed
          eliminazione sono del pannello (NodeInspector, regola 6). */}
      <div className="fl-node__info">
        <div className="fl-node__info-content">
          <div className="fl-node__subtitle">
            <span className="fl-node__title-icon" aria-hidden="true">
              {GEOM_ICONS[getGeomShortLabel(geometryType)] || '🌐'}
            </span>
            {serviceName && <span className="fl-node__service-name">{serviceName}</span>}
            {geometryType && (
              <span className="fl-node__geom-badge">
                {getGeomShortLabel(geometryType)}
              </span>
            )}
            {featureCount != null && (
              <span className="fl-node__count-badge">
                {formatNumber(featureCount)}
              </span>
            )}
          </div>
        </div>
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
                {/* Ricerca e Mostra tutti: stato di VISTA locale, azioni
                    di lettura (SearchField e' READ_ONLY_SAFE). */}
                <SearchField
                  search={search}
                  label="Cerca campi"
                  className="fl-node__search"
                  compact
                  shown={filteredColumns.length}
                  total={columns.length}
                />
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

            {/* Lista campi */}
            {/* Errore sempre visibile, anche sopra i campi gia' noti. */}
            {schemaResource.status === 'error' && (
              <div className="fl-node__empty" role="alert">
                {displayColumns.length > 0 ? 'Campi del layer non aggiornati' : 'Campi del layer non caricati'}: {schemaResource.error}{' '}
                <button type="button" {...READ_ONLY_SAFE} className="nodrag list-state__retry" onClick={() => schemaResource.reload()}>Riprova</button>
              </div>
            )}
            {displayColumns.length === 0 && schemaResource.status === 'error' ? null : displayColumns.length === 0 && search.active ? (
              <SearchEmpty search={search} shown={0} />
            ) : displayColumns.length === 0 ? (
              <div className="fl-node__empty">Nessun campo</div>
            ) : (
              displayColumns.map((column) => (
                <div
                  key={column._origIndex}
                  className={`fl-node__field ${!isColumnVisible(column) ? 'fl-node__field--hidden' : ''}`}
                >
                  <input
                    type="checkbox"
                    checked={isColumnVisible(column)}
                    onChange={() => handleToggleColumn(column._origIndex)}
                    className="fl-node__field-checkbox"
                  />
                  <span className="fl-node__field-icon" title={column.type}>
                    {getFieldIcon(column.type)}
                  </span>
                  <span className={`fl-node__field-name ${!isColumnVisible(column) ? 'fl-node__field-name--hidden' : ''}`}>
                    {column.name}
                  </span>
                </div>
              ))
            )}

            {/* Bottone espandi/collassa */}
            {hasMoreColumns && (
              <button
                type="button"
                {...READ_ONLY_SAFE}
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
                    value={whereDraft.value}
                    onChange={(e) => whereDraft.change(e.target.value)}
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
                  onClick={() => whereDraft.begin()}
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
                        title="Aggiungi la tabella correlata con un Join automatico"
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
      <NodeHandle
        type="source"
        position={Position.Right}
        className="fl-node__handle"
      />
    </div>
  );
});

FeatureLayerNode.displayName = 'FeatureLayerNode';

export default FeatureLayerNode;
