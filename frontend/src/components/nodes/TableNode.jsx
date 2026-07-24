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
import { Handle, Position } from 'reactflow';
import FilterModal from '../modals/FilterModal';
import ColumnBuilderModal from '../modals/ColumnBuilderModal';
import { getColumnTypeIcon, getColumnTypeLabel, getColumnSRID, isGeometryType } from '../../utils/columnUtils';
import useCanvasStore from '../../store/useCanvasStore';
import { useConfirm } from '../../context/ConfirmContext';
import './TableNode.css';

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
  const [isEditingAlias, setIsEditingAlias] = useState(false);
  const [tempAlias, setTempAlias] = useState(data.alias || '');

  // State per editing alias colonna
  const [editingColumnIndex, setEditingColumnIndex] = useState(null);
  const [tempColumnAlias, setTempColumnAlias] = useState('');

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
  const visibleCount = columns.filter(c => c.visible).length;
  const allVisible = visibleCount === columns.length;
  const noneVisible = visibleCount === 0;

  // Selective subscription avoids re-renders when unrelated store state changes.
  const projectId            = useCanvasStore(s => s.projectId);
  const updateNodeData       = useCanvasStore(s => s.updateNodeData);
  const deleteNode           = useCanvasStore(s => s.deleteNode);
  const confirm              = useConfirm();
  const toggleColumn         = useCanvasStore(s => s.toggleColumn);
  const toggleAllColumns     = useCanvasStore(s => s.toggleAllColumns);
  const updateColumnAlias    = useCanvasStore(s => s.updateColumnAlias);
  const addCalculatedColumn  = useCanvasStore(s => s.addCalculatedColumn);
  const removeCalculatedColumn = useCanvasStore(s => s.removeCalculatedColumn);

  const handleSaveAlias = () => {
    updateNodeData(id, { alias: tempAlias.trim() || null, label: tempAlias.trim() || data.table_name });
    setIsEditingAlias(false);
  };

  const handleToggleColumn    = (columnIndex) => toggleColumn(id, columnIndex);
  const handleToggleAll       = (makeVisible) => toggleAllColumns(id, makeVisible);
  const handleDelete          = async () => {
    const ok = await confirm({
      title: 'Elimina nodo',
      message: 'Eliminare questo nodo?',
      confirmText: 'Elimina',
      variant: 'danger',
    });
    if (ok) deleteNode(id);
  };

  const handleStartEditColumnAlias = (columnIndex) => {
    setEditingColumnIndex(columnIndex);
    setTempColumnAlias(columns[columnIndex].alias || '');
  };

  const handleSaveColumnAlias = () => {
    updateColumnAlias(id, editingColumnIndex, tempColumnAlias.trim() || null);
    setEditingColumnIndex(null);
    setTempColumnAlias('');
  };

  const handleCancelEditColumnAlias = () => {
    setEditingColumnIndex(null);
    setTempColumnAlias('');
  };

  const handleSaveFilters             = (newFilters) => updateNodeData(id, { filters: newFilters });
  const handleOpenFilterModal         = (e) => { e.preventDefault(); e.stopPropagation(); setIsModalOpen(true); };
  const handleSaveCalculatedColumn    = (columnDef) => addCalculatedColumn(id, columnDef);
  const handleDeleteCalculatedColumn  = (index) => removeCalculatedColumn(id, index);

  return (
    <div className="table-node">
      {/* Handle input (sinistra) */}
      <Handle
        type="target"
        position={Position.Left}
        className="table-node__handle"
      />

      {/* Header */}
      <div className="table-node__header">
        <div className="table-node__header-content">
          <div className={`table-node__title ${alias ? 'table-node__title--with-alias' : ''}`}>
            <span className="table-node__title-icon">📊</span>
            {tableName}
          </div>
          
          {/* Alias editabile */}
          {isEditingAlias ? (
            <input
              type="text"
              value={tempAlias}
              onChange={(e) => setTempAlias(e.target.value)}
              onBlur={handleSaveAlias}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleSaveAlias();
                if (e.key === 'Escape') {
                  setIsEditingAlias(false);
                  setTempAlias(alias || '');
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
                onClick={() => setIsEditingAlias(true)}
                title="Click per modificare alias"
              >
                → {alias}
              </div>
            )
          )}
          
          {/* Bottone aggiungi alias se non c'è */}
          {!alias && !isEditingAlias && (
            <button
              type="button"
              onClick={() => setIsEditingAlias(true)}
              className="table-node__add-alias-btn"
            >
              + Aggiungi alias
            </button>
          )}
        </div>

        {/* Bottone elimina */}
        <button
          type="button"
          onClick={handleDelete}
          className="table-node__delete-btn"
          title="Elimina nodo"
        >
          ×
        </button>
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
              className={`table-node__column ${!column.visible ? 'table-node__column--hidden' : ''}`}
            >
              {/* Checkbox visibilità */}
              <input
                type="checkbox"
                checked={column.visible}
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
              <span className={`table-node__column-name ${!column.visible ? 'table-node__column-name--hidden' : ''}`}>
                {column.name}
              </span>
              
              {/* Badge SRID per geometrie */}
              {isGeometryType(column.type) && getColumnSRID(column.type) && (
                <span className="table-node__column-srid-badge" title={`Sistema di riferimento: EPSG:${getColumnSRID(column.type)}`}>
                  {getColumnSRID(column.type)}
                </span>
              )}
              
              {/* Alias colonna - editabile */}
              {editingColumnIndex === column._origIndex ? (
                <div className="table-node__column-alias-editor">
                  <span className="table-node__column-alias-arrow">→</span>
                  <input
                    type="text"
                    value={tempColumnAlias}
                    onChange={(e) => setTempColumnAlias(e.target.value)}
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
                  title="Click per modificare alias"
                >
                  → {column.alias}
                </span>
              ) : (
                <span
                  className="table-node__column-add-alias"
                  onClick={() => handleStartEditColumnAlias(column._origIndex)}
                  title="Click per aggiungere alias"
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
      <Handle
        type="source"
        position={Position.Right}
        className="table-node__handle"
      />
    </div>
  );
});

TableNode.displayName = 'TableNode';

export default TableNode;
