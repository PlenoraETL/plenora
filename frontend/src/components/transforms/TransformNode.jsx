/**
 * TransformNode.jsx - Nodo per trasformazioni dati (MANIPOLA)
 * 
 * Versione refactored: la logica UI di ogni operazione è in componenti separati
 * in /transforms/base/ e /transforms/spatial/
 * 
 * AGGIORNATO 2026-01-22: Props aggiuntive per sjoin, geo_clip per SourceSwapper
 * Posizione: /components/transforms/TransformNode.jsx
 */
import React, { memo, useState, useEffect } from 'react';
import { Handle, Position, useReactFlow } from 'reactflow';
import '../../styles/TransformNode.css';
import { getColumnTypeIcon } from '../../utils/columnUtils';

// Import utilities e hook (path relativi)
import { 
  operations, 
  getCategoryForOperation,
  getColType,
  inferFormulaType 
} from './transformOperations';
import { useTransformValidation } from './useTransformValidation';

// Import config components - Base
import JoinConfig from './base/JoinConfig';
import FormulaConfig from './base/FormulaConfig';
import FilterConfig from './base/FilterConfig';
import AggregateConfig from './base/AggregateConfig';
import ConcatConfig from './base/ConcatConfig';
import SortConfig from './base/SortConfig';
import DistinctConfig from './base/DistinctConfig';
import RenameConfig from './base/RenameConfig';
import DropColumnsConfig from './base/DropColumnsConfig';
import FillNaConfig from './base/FillNaConfig';
import ReplaceConfig from './base/ReplaceConfig';
import SplitColumnConfig from './base/SplitColumnConfig';

// Import config components - Spatial
import SpatialJoinConfig from './spatial/SpatialJoinConfig';
import BufferConfig from './spatial/BufferConfig';
import CentroidConfig from './spatial/CentroidConfig';
import AreaConfig from './spatial/AreaConfig';
import ClipConfig from './spatial/ClipConfig';
import DissolveConfig from './spatial/DissolveConfig';
import TwoInputSpatialConfig from './spatial/TwoInputSpatialConfig';
import SimplifyConfig from './spatial/SimplifyConfig';
import ConvexHullConfig from './spatial/ConvexHullConfig';
import ReprojectConfig from './spatial/ReprojectConfig';

// Mappa operazione -> componente
const configComponentMap = {
  // Base
  join: JoinConfig,
  formula: FormulaConfig,
  filter: FilterConfig,
  aggregate: AggregateConfig,
  concat: ConcatConfig,
  sort: SortConfig,
  distinct: DistinctConfig,
  rename: RenameConfig,
  drop_columns: DropColumnsConfig,
  fill_na: FillNaConfig,
  replace: ReplaceConfig,
  split_column: SplitColumnConfig,
  // Spatial
  sjoin: SpatialJoinConfig,
  geo_buffer: BufferConfig,
  geo_centroid: CentroidConfig,
  geo_area: AreaConfig,
  geo_clip: ClipConfig,
  geo_dissolve: DissolveConfig,
  geo_intersection: TwoInputSpatialConfig,
  geo_union: TwoInputSpatialConfig,
  geo_difference: TwoInputSpatialConfig,
  geo_simplify: SimplifyConfig,
  geo_convex_hull: ConvexHullConfig,
  geo_reproject: ReprojectConfig
};

const TransformNode = memo(({ data, id }) => {
  const { getEdges, getNodes } = useReactFlow();
  
  const transform_op = data.transform_op || 'join';
  const config = data.config || {};

  // Stato tab attiva
  const [activeTab, setActiveTab] = useState(() => getCategoryForOperation(transform_op));
  
  // Aggiorna tab quando cambia operazione
  useEffect(() => {
    setActiveTab(getCategoryForOperation(transform_op));
  }, [transform_op]);

  // Hook validazione - calcola tutte le validazioni e colonne
  const validationData = useTransformValidation({
    transform_op,
    nodeId: id,
    getEdges,
    getNodes
  });

  // Filtra operazioni per tab attiva
  const filteredOperations = operations.filter(op => op.category === activeTab);

  // === HANDLERS ===
  
  const handleTabChange = (newTab) => {
    setActiveTab(newTab);
    
    // Se l'operazione corrente non è nella nuova categoria, seleziona la prima
    const currentOpCategory = getCategoryForOperation(transform_op);
    if (currentOpCategory !== newTab) {
      const firstOpInCategory = operations.find(o => o.category === newTab);
      if (firstOpInCategory && data.onChange) {
        data.onChange({
          ...data,
          transform_op: firstOpInCategory.value,
          config: {}
        });
      }
    }
  };

  const updateConfig = (field, value) => {
    if (data.onChange) {
      data.onChange({
        ...data,
        config: { ...config, [field]: value }
      });
    }
  };

  const onOpChange = (evt) => {
    if (data.onChange) {
      data.onChange({ 
        ...data, 
        transform_op: evt.target.value, 
        config: {} 
      });
    }
  };

  const handleDelete = () => {
    if (data.onDelete && confirm('Eliminare questo nodo?')) {
      data.onDelete(id);
    }
  };

  // === CALCOLO COLONNE OUTPUT ===
  const calculateOutputColumns = () => {
    const { leftColumns, rightColumns, inputColumns } = validationData;
    let outputColumns = [];

    if (transform_op === 'join') {
      if (leftColumns.length > 0 && rightColumns.length > 0) {
        const leftOn = config.left_on;
        const rightOn = config.right_on;
        
        outputColumns = leftColumns.map(c => ({
          name: c.name === leftOn ? c.name : c.name + '_L',
          type: getColType(c),
          visible: true
        }));
        
        const outputColNames = outputColumns.map(c => c.name);
        
        rightColumns.forEach(rc => {
          const rightName = rc.name === rightOn ? rc.name : rc.name + '_R';
          if (!outputColNames.includes(rightName)) {
            outputColumns.push({ 
              name: rightName, 
              type: getColType(rc),
              visible: true 
            });
          }
        });
      }
    } else if (transform_op === 'formula') {
      if (inputColumns.length > 0) {
        outputColumns = inputColumns.map(c => ({ 
          name: c.name,
          type: getColType(c),
          visible: true 
        }));
        if (config.new_column && !outputColumns.find(c => c.name === config.new_column)) {
          const inferredType = inferFormulaType(config.formula);
          outputColumns.push({ 
            name: config.new_column, 
            type: inferredType,
            visible: true 
          });
        }
      }
    } else if (['filter', 'sort', 'distinct'].includes(transform_op)) {
      if (inputColumns.length > 0) {
        outputColumns = inputColumns.map(c => ({ 
          name: c.name,
          type: getColType(c),
          visible: true 
        }));
      }
    } 
    // === OPERAZIONI SPAZIALI SINGOLO INPUT ===
    else if (['geo_buffer', 'geo_centroid', 'geo_area', 'geo_convex_hull'].includes(transform_op)) {
      if (inputColumns.length > 0) {
        // Copia tutte le colonne esistenti
        outputColumns = inputColumns.map(c => ({ 
          name: c.name,
          type: getColType(c),
          visible: true 
        }));
        // Aggiungi la nuova colonna output come geometry
        const outputColName = config.output_column || 
          (transform_op === 'geo_buffer' ? 'buffer_geom' : 
           transform_op === 'geo_centroid' ? 'centroid' :
           transform_op === 'geo_area' ? 'area_m2' :
           transform_op === 'geo_convex_hull' ? 'convex_hull' : 'new_geom');
        
        if (!outputColumns.find(c => c.name === outputColName)) {
          outputColumns.push({ 
            name: outputColName, 
            type: transform_op === 'geo_area' ? 'real' : 'geometry',
            visible: true 
          });
        }
      }
    }
    // === OPERAZIONI SPAZIALI CHE MANTENGONO GEOMETRIA ===
    else if (['geo_simplify', 'geo_reproject', 'geo_dissolve'].includes(transform_op)) {
      if (inputColumns.length > 0) {
        outputColumns = inputColumns.map(c => ({ 
          name: c.name,
          type: getColType(c),
          visible: true 
        }));
      }
    }
    // === SPATIAL JOIN E OPERAZIONI 2 INPUT ===
    else if (['sjoin', 'geo_clip', 'geo_intersection', 'geo_union', 'geo_difference'].includes(transform_op)) {
      const { leftColumns, rightColumns } = validationData;
      if (leftColumns.length > 0) {
        // Prendi colonne da sinistra
        outputColumns = leftColumns.map(c => ({ 
          name: c.name,
          type: getColType(c),
          visible: true 
        }));
        // Per sjoin aggiungi anche colonne da destra (senza geometry)
        if (transform_op === 'sjoin' && rightColumns.length > 0) {
          rightColumns.forEach(rc => {
            const colType = getColType(rc);
            if (!colType.startsWith('geometry') && !outputColumns.find(c => c.name === rc.name)) {
              outputColumns.push({ 
                name: rc.name,
                type: colType,
                visible: true 
              });
            }
          });
        }
      }
    }

    return outputColumns;
  };

  // Effect: aggiorna colonne output
  useEffect(() => {
    const outputColumns = calculateOutputColumns();
    if (data.onUpdateColumns && outputColumns.length > 0) {
      data.onUpdateColumns(id, outputColumns);
    }
  }, [transform_op, config, validationData]);

  // === RENDER CONFIG COMPONENT ===
  const renderConfigComponent = () => {
    const ConfigComponent = configComponentMap[transform_op];
    
    if (!ConfigComponent) {
      return (
        <div className="transform-node__message transform-node__message--info">
          ℹ️ Configurazione per "{transform_op}" in sviluppo
        </div>
      );
    }

    // Props comuni
    const commonProps = {
      config,
      updateConfig,
      validation: validationData.validation,
      nodeId: id
    };

    // Props specifiche per tipo operazione
    switch (transform_op) {
      case 'join':
        return (
          <ConfigComponent
            {...commonProps}
            leftNode={validationData.leftNode}
            rightNode={validationData.rightNode}
            leftColumns={validationData.leftColumns}
            rightColumns={validationData.rightColumns}
            getNodeLabel={validationData.getNodeLabel}
          />
        );

      case 'formula':
      case 'filter':
        return (
          <ConfigComponent
            {...commonProps}
            inputColumns={validationData.inputColumns}
          />
        );

      case 'sort':
      case 'distinct':
      case 'rename':
      case 'drop_columns':
      case 'fill_na':
      case 'replace':
      case 'split_column':
        return (
          <ConfigComponent
            {...commonProps}
            inputColumns={validationData.inputColumns}
          />
        );

      case 'aggregate':
        return (
          <ConfigComponent
            {...commonProps}
            inputColumns={validationData.inputColumns}
          />
        );

      case 'concat':
        return (
          <ConfigComponent
            {...commonProps}
            incomingEdges={validationData.incomingEdges}
          />
        );

      // Spatial - single input
      case 'geo_buffer':
      case 'geo_centroid':
      case 'geo_area':
      case 'geo_simplify':
      case 'geo_convex_hull':
      case 'geo_reproject':
        return (
          <ConfigComponent
            {...commonProps}
            geometryColumns={validationData.geometryColumns}
            inputColumns={validationData.inputColumns}
          />
        );

      case 'geo_dissolve':
        return (
          <ConfigComponent
            {...commonProps}
            geometryColumns={validationData.geometryColumns}
            inputColumns={validationData.inputColumns}
          />
        );

      // Spatial - two inputs (AGGIORNATO: props aggiuntive per SourceSwapper)
      case 'sjoin':
        return (
          <ConfigComponent
            {...commonProps}
            leftNode={validationData.leftNode}
            rightNode={validationData.rightNode}
            leftColumns={validationData.leftColumns}
            rightColumns={validationData.rightColumns}
            leftGeomColumns={validationData.leftGeomColumns}
            rightGeomColumns={validationData.rightGeomColumns}
            getNodeLabel={validationData.getNodeLabel}
          />
        );

      case 'geo_clip':
        return (
          <ConfigComponent
            {...commonProps}
            leftNode={validationData.leftNode}
            rightNode={validationData.rightNode}
            leftColumns={validationData.leftColumns}
            rightColumns={validationData.rightColumns}
            leftGeomColumns={validationData.leftGeomColumns}
            rightGeomColumns={validationData.rightGeomColumns}
            getNodeLabel={validationData.getNodeLabel}
          />
        );

      case 'geo_intersection':
      case 'geo_union':
      case 'geo_difference':
        return (
          <ConfigComponent
            {...commonProps}
            leftNode={validationData.leftNode}
            rightNode={validationData.rightNode}
            leftColumns={validationData.leftColumns}
            rightColumns={validationData.rightColumns}
            leftGeomColumns={validationData.leftGeomColumns}
            rightGeomColumns={validationData.rightGeomColumns}
            getNodeLabel={validationData.getNodeLabel}
            operationType={transform_op}
          />
        );

      default:
        return (
          <ConfigComponent {...commonProps} />
        );
    }
  };

  // === RENDER ===
  return (
    <div className={`transform-node transform-node--${transform_op}`}>
      <Handle
        type="target"
        position={Position.Left}
        className="transform-node__handle"
      />

      {/* Header */}
      <div className="transform-node__header">
        <div className="transform-node__title">
          <span>⚙️ Trasformazione</span>
        </div>
        <button
          onClick={handleDelete}
          className="transform-node__delete-btn"
          title="Elimina nodo"
        >
          ×
        </button>
      </div>

      {/* Body */}
      <div className="transform-node__body">
        {/* Tabs per categoria */}
        <div className="transform-node__tabs">
          <button
            className={`transform-node__tab ${activeTab === 'base' ? 'transform-node__tab--active' : ''}`}
            onClick={() => handleTabChange('base')}
          >
            📊 Base
          </button>
          <button
            className={`transform-node__tab ${activeTab === 'spatial' ? 'transform-node__tab--active' : ''}`}
            onClick={() => handleTabChange('spatial')}
          >
            🌍 Spaziale
          </button>
        </div>

        {/* Selezione tipo operazione */}
        <div className="transform-node__section">
          <label className="transform-node__label">Tipo operazione:</label>
          <select 
            value={transform_op} 
            onChange={onOpChange}
            className="transform-node__select"
          >
            {filteredOperations.map(op => (
              <option key={op.value} value={op.value}>
                {op.label}
              </option>
            ))}
          </select>
        </div>

        {/* Config component dinamico */}
        {renderConfigComponent()}
      </div>

      <Handle
        type="source"
        position={Position.Right}
        className="transform-node__handle"
      />
    </div>
  );
});

TransformNode.displayName = 'TransformNode';

export default TransformNode;
