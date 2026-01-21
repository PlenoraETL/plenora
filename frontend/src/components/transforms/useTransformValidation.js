/**
 * useTransformValidation.js
 * Hook per gestire tutte le validazioni dei nodi Transform
 */

import { useMemo } from 'react';
import { 
  twoInputOperations, 
  singleInputSpatialOps, 
  twoInputSpatialOps,
  filterGeometryColumns,
  filterVisibleColumns 
} from './transformOperations';

/**
 * Hook che calcola validazioni e colonne disponibili per un nodo Transform
 */
export const useTransformValidation = ({
  transform_op,
  nodeId,
  getEdges,
  getNodes
}) => {
  return useMemo(() => {
    const edges = getEdges();
    const nodes = getNodes();
    
    // Edge in ingresso ordinati
    const incomingEdges = edges
      .filter(e => e.target === nodeId)
      .sort((a, b) => a.id.localeCompare(b.id));
    
    // Helper per ottenere label nodo
    const getNodeLabel = (node) => {
      if (!node) return 'Unknown';
      if (node.type === 'project_source') {
        const projectId = node.data?.source_project_id;
        return projectId ? `Progetto ${projectId.substring(0, 8)}...` : 'Progetto non selezionato';
      }
      if (node.type === 'transform') {
        return `Transform (${node.data?.transform_op || 'unknown'})`;
      }
      return node.data?.label || node.id;
    };

    // Risultato base
    const result = {
      incomingEdges,
      getNodeLabel,
      // Nodi
      leftNode: null,
      rightNode: null,
      inputNode: null,
      // Colonne
      leftColumns: [],
      rightColumns: [],
      inputColumns: [],
      geometryColumns: [],
      leftGeomColumns: [],
      rightGeomColumns: [],
      // Validazioni
      validation: { valid: true, message: '', type: 'info' }
    };

    // === JOIN (2 input) ===
    if (transform_op === 'join') {
      if (incomingEdges.length < 2) {
        result.validation = {
          valid: false,
          type: 'warning',
          message: 'Collega 2 sorgenti dati per configurare il join'
        };
      } else if (incomingEdges.length > 2) {
        result.validation = {
          valid: false,
          type: 'error',
          message: `Join supporta solo 2 input! Hai ${incomingEdges.length} connessioni.`
        };
      } else {
        result.leftNode = nodes.find(n => n.id === incomingEdges[0].source);
        result.rightNode = nodes.find(n => n.id === incomingEdges[1].source);
        
        if (result.leftNode?.data?.columns) {
          result.leftColumns = filterVisibleColumns(result.leftNode.data.columns);
        }
        if (result.rightNode?.data?.columns) {
          result.rightColumns = filterVisibleColumns(result.rightNode.data.columns);
        }
        
        if (result.leftColumns.length === 0 || result.rightColumns.length === 0) {
          result.validation = {
            valid: false,
            type: 'warning',
            message: 'Uno o più input non hanno colonne disponibili.'
          };
        }
      }
    }
    
    // === FORMULA / FILTER (1 input) ===
    else if (transform_op === 'formula' || transform_op === 'filter') {
      if (incomingEdges.length === 0) {
        result.validation = {
          valid: false,
          type: 'warning',
          message: `Collega 1 sorgente dati per configurare ${transform_op === 'formula' ? 'la formula' : 'il filtro'}`
        };
      } else if (incomingEdges.length > 1) {
        result.validation = {
          valid: false,
          type: 'error',
          message: `${transform_op === 'formula' ? 'Formula' : 'Filter'} supporta solo 1 input!`
        };
      } else {
        result.inputNode = nodes.find(n => n.id === incomingEdges[0].source);
        if (result.inputNode?.data?.columns) {
          result.inputColumns = filterVisibleColumns(result.inputNode.data.columns);
        }
        if (result.inputColumns.length === 0) {
          result.validation = {
            valid: false,
            type: 'warning',
            message: 'Input non ha colonne disponibili.'
          };
        }
      }
    }
    
    // === SORT / DISTINCT / RENAME / DROP_COLUMNS / FILL_NA / REPLACE / SPLIT_COLUMN (1 input) ===
    else if (['sort', 'distinct', 'rename', 'drop_columns', 'fill_na', 'replace', 'split_column'].includes(transform_op)) {
      const opLabels = {
        'sort': "l'ordinamento",
        'distinct': 'la rimozione duplicati',
        'rename': 'la rinomina',
        'drop_columns': "l'eliminazione colonne",
        'fill_na': 'il riempimento nulli',
        'replace': 'la sostituzione',
        'split_column': 'la divisione colonna'
      };
      
      if (incomingEdges.length === 0) {
        result.validation = {
          valid: false,
          type: 'warning',
          message: `Collega 1 sorgente dati per configurare ${opLabels[transform_op]}`
        };
      } else if (incomingEdges.length > 1) {
        result.validation = {
          valid: false,
          type: 'error',
          message: `Questa operazione supporta solo 1 input!`
        };
      } else {
        result.inputNode = nodes.find(n => n.id === incomingEdges[0].source);
        if (result.inputNode?.data?.columns) {
          result.inputColumns = filterVisibleColumns(result.inputNode.data.columns);
        }
        if (result.inputColumns.length === 0) {
          result.validation = {
            valid: false,
            type: 'warning',
            message: 'Input non ha colonne disponibili.'
          };
        }
      }
    }
    
    // === CONCAT (N input) ===
    else if (transform_op === 'concat') {
      if (incomingEdges.length < 2) {
        result.validation = {
          valid: false,
          type: 'warning',
          message: 'Concat richiede almeno 2 sorgenti dati'
        };
      } else {
        // Raccogli tutte le colonne da tutti gli input
        const allInputColumns = [];
        incomingEdges.forEach(edge => {
          const node = nodes.find(n => n.id === edge.source);
          if (node?.data?.columns) {
            allInputColumns.push(filterVisibleColumns(node.data.columns));
          }
        });
        result.inputColumns = allInputColumns;
      }
    }
    
    // === AGGREGATE (1 input) ===
    else if (transform_op === 'aggregate') {
      if (incomingEdges.length === 0) {
        result.validation = {
          valid: false,
          type: 'warning',
          message: 'Collega 1 sorgente dati per configurare aggregazione'
        };
      } else {
        result.inputNode = nodes.find(n => n.id === incomingEdges[0].source);
        if (result.inputNode?.data?.columns) {
          result.inputColumns = filterVisibleColumns(result.inputNode.data.columns);
        }
      }
    }
    
    // === OPERAZIONI SPAZIALI SINGOLO INPUT ===
    else if (singleInputSpatialOps.includes(transform_op)) {
      if (incomingEdges.length === 0) {
        result.validation = {
          valid: false,
          type: 'warning',
          message: 'Collega una sorgente con colonna geometria'
        };
      } else {
        result.inputNode = nodes.find(n => n.id === incomingEdges[0].source);
        if (result.inputNode?.data?.columns) {
          result.inputColumns = filterVisibleColumns(result.inputNode.data.columns);
          result.geometryColumns = filterGeometryColumns(result.inputNode.data.columns);
        }
        if (result.geometryColumns.length === 0) {
          result.validation = {
            valid: false,
            type: 'warning',
            message: 'Nessuna colonna geometrica trovata nell\'input'
          };
        }
      }
    }
    
    // === OPERAZIONI SPAZIALI 2 INPUT ===
    else if (twoInputSpatialOps.includes(transform_op)) {
      const opLabels = {
        'sjoin': 'Spatial Join',
        'geo_clip': 'Clip',
        'geo_intersection': 'Intersezione',
        'geo_union': 'Unione Geometrica',
        'geo_difference': 'Differenza'
      };
      
      if (incomingEdges.length < 2) {
        result.validation = {
          valid: false,
          type: 'warning',
          message: `${opLabels[transform_op] || transform_op} richiede 2 input con geometrie`
        };
      } else {
        result.leftNode = nodes.find(n => n.id === incomingEdges[0].source);
        result.rightNode = nodes.find(n => n.id === incomingEdges[1].source);
        
        if (result.leftNode?.data?.columns) {
          result.leftColumns = filterVisibleColumns(result.leftNode.data.columns);
          result.leftGeomColumns = filterGeometryColumns(result.leftNode.data.columns);
        }
        if (result.rightNode?.data?.columns) {
          result.rightColumns = filterVisibleColumns(result.rightNode.data.columns);
          result.rightGeomColumns = filterGeometryColumns(result.rightNode.data.columns);
        }
        
        if (result.leftGeomColumns.length === 0 || result.rightGeomColumns.length === 0) {
          result.validation = {
            valid: false,
            type: 'warning',
            message: 'Entrambi gli input devono avere colonne geometriche'
          };
        }
      }
    }
    
    return result;
  }, [transform_op, nodeId, getEdges, getNodes]);
};

export default useTransformValidation;
