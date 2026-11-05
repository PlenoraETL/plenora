/**
 * TableNode.jsx — Nodo canvas per tabella database
 *
 * Sorgente dati da una tabella o vista del database connesso.
 * Lista colonne con checkbox visibilità, filtro WHERE, column builder
 * per colonne calcolate. Supporta tabelle spaziali (PostGIS, Oracle Spatial).
 *
 * Usato in: EstraiEditor (trascina dalla sidebar catalogo)
 */
import React, { useState, memo } from 'react';
import { Position } from '@xyflow/react';
import FilterModal from '../modals/FilterModal';
import ColumnBuilderModal from '../modals/ColumnBuilderModal';
import { getColumnTypeIcon, getColumnTypeLabel, getColumnSRID, isColumnVisible, isGeometryType } from '../../utils/columnUtils';
import useCanvasStore from '../../store/useCanvasStore';
import { useStoreDraft } from '../../hooks/useStoreDraft';
import './TableNode.css';
import NodeHandle from '../canvas/NodeHandle';

/**
 * Componente nodo tabella personalizzato
 * Mostra nome tabella, alias, colonne editabili con FilterModal
 * La connessione DB è gestita a livello progetto
 */
const TableNode = memo(({ data, id }) => {
  // State per gestire collapse colonne
  const [isExpanded, setIsExpanded] = useState(false);
  
  // State per ricerca colonne
  const [searchTerm, setSearchTerm] = useState('');
  
  // State per editing alias tabella
  // Bozze dei campi solo durante la modifica, derivate dallo store
  // (hooks/useStoreDraft, R1.9): un Ctrl+Z le scarta.
  const aliasDraft = useStoreDraft(data.alias || '');
  const isEditingAlias = aliasDraft.editing;

  // State per editing alias colonna
  const [editingColumnIndex, setEditingColumnIndex] = useState(null);
  const columnAliasDraft = useStoreDraft(editingColumnIndex === null ? null : ((data.columns || [])[editingColumnIndex]?.alias || ''));

  // State per modale filtri WHERE
  const [isModalOpen, setIsModalOpen] = useState(false);

  // State per modale colonne calcolate
  const [isColumnBuilderOpen, setIsColumnBuilderOpen] = useState(false);

  // State per Run History modal

  const tableName = data.table_name;
  const alias = data.alias;
  const columns = data.columns || [];
  const filters = data.filters || [];
  const calculatedColumns = data.calculated_columns || [];
  const dbType = data.db_type || 'postgres';
  
  // Filtra colonne per ricerca
  const filteredColumns = searchTerm
    ? columns.map((col, idx) => ({ ...col, _origIndex: idx }))
        .filter(col => col.name.toLowerCase().includes(searchTerm.toLowerCase()))
    : columns.map((col, idx) => ({ ...col, _origIndex: idx }));
  
  // Mostra max 5 colonne se collapsed (e nessuna ricerca attiva)
  const displayColumns = (isExpanded || searchTerm) ? filteredColumns : filteredColumns.slice(0, 5);
  const hasMoreColumns = !searchTerm && columns.length > 5;
  
  // Conteggio colonne visibili
  const visibleCount = columns.filter(isColumnVisible).length;
  const allVisible = visibleCount === columns.length;
  const noneVisible = visibleCount === 0;

  // Selective subscription avoids re-renders when unrelated store state changes.
  const projectId            = useCanvasStore(s => s.projectId);
  const updateNodeData       = useCanvasStore(s => s.updateNodeData);
  const toggleColumn         = useCanvasStore(s => s.toggleColumn);
  const toggleAllColumns     = useCanvasStore(s => s.toggleAllColumns);
  const updateColumnAlias    = useCanvasStore(s => s.updateColumnAlias);
  const addCalculatedColumn  = useCanvasStore(s => s.addCalculatedColumn);
  const removeCalculatedColumn = useCanvasStore(s => s.removeCalculatedColumn);

  const handleSaveAlias = () => {
    // Solo l'alias SQL: il titolo del passo ne e' derivato (nodeDescriptors).
    aliasDraft.commit(value => updateNodeData(id, { alias: String(value).trim() || null }));
  };

  const handleToggleColumn    = (columnIndex) => toggleColumn(id, columnIndex);
  const handleToggleAll       = (makeVisible) => toggleAllColumns(id, makeVisible);

  const handleStartEditColumnAlias = (columnIndex) => {
    setEditingColumnIndex(columnIndex);
    columnAliasDraft.beginWith(columns[columnIndex].alias || '');
  };

  const handleSaveColumnAlias = () => {
    columnAliasDraft.commit(value => updateColumnAlias(id, editingColumnIndex, String(value).trim() || null));
    setEditingColumnIndex(null);
  };

  const handleCancelEditColumnAlias = () => {
    setEditingColumnIndex(null);
    columnAliasDraft.cancel();
  };

  const handleSaveFilters             = (newFilters) => updateNodeData(id, { filters: newFilters });
  const handleOpenFilterModal         = (e) => { e.preventDefault(); e.stopPropagation(); setIsModalOpen(true); };
  const handleSaveCalculatedColumn    = (columnDef) => addCalculatedColumn(id, columnDef);
  const handleDeleteCalculatedColumn  = (index) => removeCalculatedColumn(id, index);

  return (
    <div className="table-node">
      {/* Handle input (sinistra) */}
      <NodeHandle
        type="target"
        position={Position.Left}
        className="table-node__handle"
      />

      {/* Tabella e alias SQL. Titolo, chiusura ed eliminazione sono del
          pannello (NodeInspector, regola 6 del Blocco A). */}
      <div className="table-node__info">
        <div className="table-node__info-content">
          <div className={`table-node__title ${alias ? 'table-node__title--with-alias' : ''}`}>
            <span className="table-node__title-icon" aria-hidden="true">📊</span>
            {tableName}
          </div>
          
          {/* Alias editabile */}
          {isEditingAlias ? (
            <input
              type="text"
              value={aliasDraft.value}
              onChange={(e) => aliasDraft.change(e.target.value)}
              onBlur={handleSaveAlias}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleSaveAlias();
                if (e.key === 'Escape') {
                  aliasDraft.cancel();
                }
              }}
              autoFocus
              placeholder="Alias..."
              className="table-node__alias-input"
            />
          ) : (
            alias && (
              <div 
                className="table-node__alias-display"
                onClick={() => aliasDraft.begin()}
                title="Clic per modificare l'alias"
              >
                → {alias}
              </div>
            )
          )}
          
          {/* Bottone aggiungi alias se non c'è */}
          {!alias && !isEditingAlias && (
            <button
              type="button"
              onClick={() => aliasDraft.begin()}
              className="table-node__add-alias-btn"
            >
              + Aggiungi alias
            </button>
          )}
        </div>
      </div>

      {/* Body - Lista colonne */}
      <div className="table-node__body">
        {/* Toolbar: ricerca + seleziona/deseleziona tutto */}
        {columns.length > 3 && (
          <div className="table-node__toolbar">
            <div className="table-node__search">
              <input
                type="text"
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                placeholder="🔍 Cerca colonne..."
                className="table-node__search-input"
              />
              {searchTerm && (
                <button
                  type="button"
                  className="table-node__search-clear"
                  onClick={() => setSearchTerm('')}
                >
                  ×
                </button>
              )}
            </div>
            <div className="table-node__toggle-all">
              <label className="table-node__toggle-all-label">
                <input
                  type="checkbox"
                  checked={allVisible}
                  ref={(el) => { if (el) el.indeterminate = !allVisible && !noneVisible; }}
                  onChange={() => handleToggleAll(noneVisible || !allVisible)}
                  className="table-node__toggle-all-checkbox"
                />
                <span className="table-node__toggle-all-text">
                  {visibleCount}/{columns.length}
                </span>
              </label>
            </div>
          </div>
        )}

        {/* Risultati ricerca */}
        {searchTerm && (
          <div className="table-node__search-info">
            {filteredColumns.length} di {columns.length} colonne
          </div>
        )}

        {displayColumns.length === 0 ? (
          <div className="table-node__empty-message">
            {searchTerm ? 'Nessuna colonna trovata' : 'Nessuna colonna'}
          </div>
        ) : (
          displayColumns.map((column) => (
            <div
              key={column._origIndex}
              className={`table-node__column ${!isColumnVisible(column) ? 'table-node__column--hidden' : ''}`}
            >
              {/* Checkbox visibilità */}
              <input
                type="checkbox"
                checked={isColumnVisible(column)}
                onChange={() => handleToggleColumn(column._origIndex)}
                className="table-node__column-checkbox"
              />
              
              {/* Icona tipo colonna */}
              <span 
                className={`table-node__column-type-icon ${isGeometryType(column.type) ? 'table-node__column-type-icon--geo' : ''}`}
                title={getColumnTypeLabel(column.type)}
              >
                {getColumnTypeIcon(column.type)}
              </span>
              
              {/* Nome colonna */}
              <span className={`table-node__column-name ${!isColumnVisible(column) ? 'table-node__column-name--hidden' : ''}`}>
                {column.name}
              </span>
              
              {/* Badge SRID per geometrie */}
              {isGeometryType(column.type) && getColumnSRID(column.type) && (
                <span className="table-node__column-srid-badge" title={`Sistema di riferimento: EPSG:${getColumnSRID(column.type)}`}>
                  {getColumnSRID(column.type)}
                </span>
              )}
              
              {/* Alias colonna - editabile */}
              {editingColumnIndex === column._origIndex && columnAliasDraft.editing ? (
                <div className="table-node__column-alias-editor">
                  <span className="table-node__column-alias-arrow">→</span>
                  <input
                    type="text"
                    value={columnAliasDraft.value}
                    onChange={(e) => columnAliasDraft.change(e.target.value)}
                    onBlur={handleSaveColumnAlias}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') handleSaveColumnAlias();
                      if (e.key === 'Escape') handleCancelEditColumnAlias();
                    }}
                    autoFocus
                    placeholder="alias..."
                    className="table-node__column-alias-input"
                  />
                </div>
              ) : column.alias ? (
                <span 
                  className="table-node__column-alias"
                  onClick={() => handleStartEditColumnAlias(column._origIndex)}
                  title="Clic per modificare l'alias"
                >
                  → {column.alias}
                </span>
              ) : (
                <span
                  className="table-node__column-add-alias"
                  onClick={() => handleStartEditColumnAlias(column._origIndex)}
                  title="Clic per aggiungere un alias"
                >
                  + alias
                </span>
              )}
            </div>
          ))
        )}

        {/* Bottone espandi/collassa */}
        {hasMoreColumns && (
          <button
            type="button"
            onClick={() => setIsExpanded(!isExpanded)}
            className="table-node__expand-btn"
          >
            {isExpanded 
              ? '▲ Nascondi' 
              : `▼ Mostra tutte (${columns.length})`
            }
          </button>
        )}

        {/* Sezione Filtri WHERE */}
        <div className="table-node__filters-section">
          <button
            type="button"
            onClick={handleOpenFilterModal}
            className={`table-node__filters-btn ${filters.length > 0 ? 'table-node__filters-btn--active' : ''}`}
          >
            <span>🔍 Filtri WHERE</span>
            {filters.length > 0 && (
              <span className="table-node__filters-badge">
                {filters.length}
              </span>
            )}
          </button>
        </div>

        {/* Sezione Colonne Calcolate */}
        <div className="table-node__calculated-section">
          <div className="table-node__calculated-header">
            <span>📐 Colonne calcolate</span>
            {calculatedColumns.length > 0 && (
              <span className="table-node__calculated-badge">
                {calculatedColumns.length}
              </span>
            )}
          </div>
          
          {/* Lista colonne calcolate */}
          {calculatedColumns.length > 0 && (
            <div className="table-node__calculated-list">
              {calculatedColumns.map((col, index) => (
                <div key={index} className="table-node__calculated-item">
                  <span className="table-node__calculated-icon">
                    {getColumnTypeIcon(col.output_type)}
                  </span>
                  <span className="table-node__calculated-name" title={col.sql}>
                    {col.name}
                  </span>
                  <button
                    type="button"
                    className="table-node__calculated-delete"
                    onClick={() => handleDeleteCalculatedColumn(index)}
                    title="Rimuovi colonna"
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
          )}
          
          {/* Bottone aggiungi */}
          <button
            type="button"
            onClick={() => setIsColumnBuilderOpen(true)}
            className="table-node__add-calculated-btn"
          >
            + Aggiungi colonna calcolata
          </button>
        </div>
      </div>

      {/* FilterModal - riceve colonne complete con tipo */}
      <FilterModal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        nodeId={id}
        filters={filters}
        columns={columns}
        tableName={tableName}
        onSave={handleSaveFilters}
      />

      {/* ColumnBuilderModal - per colonne calcolate */}
      <ColumnBuilderModal
        isOpen={isColumnBuilderOpen}
        onClose={() => setIsColumnBuilderOpen(false)}
        onSave={handleSaveCalculatedColumn}
        columns={columns}
        dbType={dbType}
        projectId={projectId}
        tableName={tableName}
        tableAlias={alias || 't'}
      />


      {/* Handle output (destra) */}
      <NodeHandle
        type="source"
        position={Position.Right}
        className="table-node__handle"
      />
    </div>
  );
});

TableNode.displayName = 'TableNode';

export default TableNode;
