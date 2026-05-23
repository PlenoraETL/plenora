/**
 * useCanvasValidation.js — Validazione canvas in tempo reale.
 *
 * Osserva nodes/edges e valida continuamente il canvas, debounced.
 * Espone il contratto `CanvasValidationResult`
 * (vedi `utils/canvasValidationContract.js`) + adapter legacy.
 *
 * Editor-aware: accetta una `validator` function opzionale; il default
 * e' `validateEstraiCanvas` per backward compat con il call site
 * EstraiEditor preesistente. Manipola/Arricchisci possono opt-in
 * passando `validateManipolaCanvas` / `validateArricchisciCanvas`.
 *
 * Ritorna:
 *   - `result`: `CanvasValidationResult` (nuovo contratto, raccomandato)
 *   - `isValid`: alias di `result.ok` (KEEP_COMPAT)
 *   - `errors: string[]`: messaggi di sole severity 'error' (KEEP_COMPAT)
 *   - `issues: ValidationIssue[]`: alias di `result.issues` (KEEP_COMPAT)
 *   - `disconnectedNodeIds`: id dei nodi citati negli issue
 *   - `validate()`: trigger manuale, ritorna `CanvasValidationResult`
 *   - `clearErrors()`: reset
 *   - `getNodeStyle(nodeId)`: stile per highlight nodi disconnessi
 */
import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { validateEstraiCanvas } from '../utils/validateEstraiCanvas';
import { buildResult } from '../utils/canvasValidationContract';

const EMPTY_RESULT = buildResult([]);

function validationNodeFingerprint(node) {
  if (node.type === 'tableNode') {
    const visibleCols = (node.data?.columns || [])
      .filter(c => c.visible)
      .map(c => `${c.name}:${c.alias || ''}:${c.type || ''}`)
      .join('|');
    return `${node.id}:${node.type}:${node.data?.alias || ''}:${visibleCols}`;
  }

  if (node.type === 'carica') {
    const d = node.data || {};
    return [
      node.id,
      node.type,
      d.dest_type || 'db',
      d.connection_id || '',
      d.schema_name || '',
      d.table_name || '',
      d.write_mode || 'create',
      (d.match_columns || []).join('|'),
      d.rest_url || '',
      d.rest_method || '',
    ].join(':');
  }

  if (node.type === 'caricaArcgis' || node.type === 'carica_arcgis') {
    const d = node.data || {};
    return [
      node.id,
      node.type,
      d.arcgis_connection_id || '',
      d.arcgis_layer_url || '',
      d.arcgis_operation || '',
      d.arcgis_new_service_name || '',
      d.arcgis_match_column || '',
    ].join(':');
  }

  if (node.type === 'resultNode' || node.type === 'result') {
    const d = node.data || {};
    return `${node.id}:${node.type}:${d.format || ''}`;
  }

  return `${node.id}:${node.type || ''}`;
}

/**
 * @param {Array} nodes
 * @param {Array} edges
 * @param {Object} options
 * @param {boolean} [options.validateOnChange=true]
 * @param {number} [options.debounceMs=300]
 * @param {Function} [options.onValidationChange]
 * @param {(nodes: Array, edges: Array) => import('../utils/canvasValidationContract').CanvasValidationResult} [options.validator=validateEstraiCanvas]
 *   Validator function. Default `validateEstraiCanvas` (KEEP_COMPAT).
 */
export function useCanvasValidation(nodes, edges, options = {}) {
  const {
    validateOnChange = true,
    debounceMs = 300,
    onValidationChange,
    validator = validateEstraiCanvas,
  } = options;

  // State: il `result` e' la fonte di verita'; `disconnectedNodeIds` e' un
  // derivato cached usato per il highlight dei nodi.
  const [state, setState] = useState({
    result: EMPTY_RESULT,
    disconnectedNodeIds: [],
  });

  // Ref per evitare stale closure dentro al debounce
  const nodesRef = useRef(nodes);
  const edgesRef = useRef(edges);
  // Pre-fix `onValidationChange` era nelle deps di `validate`:
  // se il caller passava una callback inline (pattern comune in JSX),
  // ad ogni render del parent `onValidationChange` cambiava identity,
  // `validate` cambiava identity, l'effect di debounce ricalcolava e
  // re-armava il timer — il debounce di 300ms non scattava mai. Fix:
  // proxare la callback via ref (pattern di `useDialogFocus`).
  const onValidationChangeRef = useRef(onValidationChange);

  useEffect(() => {
    nodesRef.current = nodes;
    edgesRef.current = edges;
  }, [nodes, edges]);

  useEffect(() => {
    onValidationChangeRef.current = onValidationChange;
  }, [onValidationChange]);

  const validate = useCallback(() => {
    const currentNodes = nodesRef.current;
    const currentEdges = edgesRef.current;

    const result = validator(currentNodes, currentEdges);
    const disconnectedNodeIds = result.issues
      .filter(i => i.nodeId)
      .map(i => i.nodeId);
    setState({ result, disconnectedNodeIds });

    const cb = onValidationChangeRef.current;
    if (cb) {
      cb(result);
    }

    return result;
  }, [validator]);

  const clearErrors = useCallback(() => {
    setState({ result: EMPTY_RESULT, disconnectedNodeIds: [] });
  }, []);

  // Fingerprint che include i DATI rilevanti per la validazione
  const nodesFingerprint = useMemo(() => {
    return nodes.map(validationNodeFingerprint).sort().join(',');
  }, [nodes]);

  const edgesFingerprint = useMemo(() => {
    return edges.map(e => {
      const joinType = e.data?.join_type || 'left';
      const srcCol = e.data?.source_column || '';
      const tgtCol = e.data?.target_column || '';
      const joinConds = e.data?.join_conditions ? JSON.stringify(e.data.join_conditions) : '';
      const onFilters = e.data?.on_filters ? JSON.stringify(e.data.on_filters) : '';
      const isSpatial = e.data?.is_spatial ? 'S' : 'N';
      const spatialPred = e.data?.spatial_predicate || '';
      const srcGeom = e.data?.source_geom_column || '';
      const tgtGeom = e.data?.target_geom_column || '';

      return `${e.source}-${e.target}:${joinType}:${srcCol}:${tgtCol}:${joinConds}:${onFilters}:${isSpatial}:${spatialPred}:${srcGeom}:${tgtGeom}`;
    }).sort().join(',');
  }, [edges]);

  useEffect(() => {
    if (!validateOnChange || nodes.length === 0) return;

    const timeoutId = setTimeout(() => {
      validate();
    }, debounceMs);

    return () => clearTimeout(timeoutId);
    // Pre-fix (P1 frontend-hooks): `nodes.length` era nelle deps insieme
    // a `nodesFingerprint`. Ridondante: `nodesFingerprint` e' costruito
    // da `nodes.map(validationNodeFingerprint).sort().join(',')` quindi
    // cambia ogni volta che cambia la length (e tutti gli altri field
    // rilevanti). Includere `nodes.length` separato non aggiunge segnale
    // ma duplica la dep. Rimosso per chiarezza — il check `nodes.length
    // === 0` nel body usa il valore corrente del render, non la dep.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodesFingerprint, edgesFingerprint, validateOnChange, debounceMs, validate]);

  const getNodeStyle = useCallback((nodeId) => {
    if (state.disconnectedNodeIds.includes(nodeId)) {
      return {
        border: '2px solid #f44336',
        boxShadow: '0 0 10px rgba(244, 67, 54, 0.3)',
      };
    }
    return undefined;
  }, [state.disconnectedNodeIds]);

  // Adapter: il return preserva i field legacy (`isValid`, `errors`, `issues`)
  // accanto al nuovo `result`. KEEP_COMPAT per call site esistenti.
  return useMemo(() => {
    const { result } = state;
    return {
      result,
      isValid: result.ok,
      errors: result.bySeverity.error.map(i => i.message),
      issues: result.issues,
      disconnectedNodeIds: state.disconnectedNodeIds,
      validate,
      clearErrors,
      getNodeStyle,
    };
  }, [state, validate, clearErrors, getNodeStyle]);
}

export default useCanvasValidation;
