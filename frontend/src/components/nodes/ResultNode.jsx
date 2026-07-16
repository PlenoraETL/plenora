/**
 * ResultNode.jsx — Nodo output unificato (ESTRAI, MANIPOLA, ARRICCHISCI)
 *
 * Nodo terminale della pipeline. Delega la view a ResultNodeStandardView.
 * (Post-collasso IMDF a una-run: niente piu' vista IMDF dedicata; l'output
 * IMDF e' un bundle JSON come ogni altro artefatto, pubblicato via il nodo
 * carica_arcgis downstream.)
 *
 * Owna:
 *   - subscriptions canvas (edges, upstream node, hasOnPreview/Execute)
 *   - calcolo upstreamColumns (richiede useReactFlow)
 *   - logica validazione: validateViaBackend + handleValidate/Execute/Preview
 *   - ValidationModal
 *   - header + delete
 */
import React, { useState, useCallback, useEffect, useRef, memo, useMemo } from 'react';
import { Handle, Position, useReactFlow, useStore } from 'reactflow';
import ValidationModal from '../modals/ValidationModal';
import { validateProjectCanvas } from '../../api/projects';
import { backendReportToResult, validationRequestFailedResult } from '../../utils/backendValidationResult';
import useCanvasStore from '../../store/useCanvasStore';
import ResultNodeStandardView from './result/ResultNodeStandardView';
import ResultInlinePreview from './result/ResultInlinePreview';
import useInlinePreview from '../../hooks/useInlinePreview';
import { useConfirm } from '../../context/ConfirmContext';
import { dataFlowEdges } from '../../utils/runtimeBindingEdges';
import './ResultNode.css';

// #5: righe caricate nell'anteprima inline auto post-run (la modale completa
// resta a 100). Volutamente piccolo: gira su cache subito dopo l'esecuzione.
const INLINE_PREVIEW_LIMIT = 10;

/**
 * Componente nodo risultato (output) UNIFICATO
 * Funziona sia per progetti SQL (estrai) che Pandas (manipola) che API (arricchisci)
 *
 * Props:
 * - data.isManipulate: true per progetti Manipola
 * - data.isArricchisci: true per progetti Arricchisci
 * - data.fileName / .format / .distinct / .limit / .columns / ...
 * Tutti gli aggiornamenti avvengono tramite useCanvasStore (updateNodeData, deleteNode).
 */
const ResultNode = memo(({ data, id }) => {
  const { getEdges, getNodes, setCenter } = useReactFlow();

  // Sottoscrizioni reattive per i bottoni Preview/Esegui (propagate alle view)
  const hasOnPreview = useCanvasStore(s => !!s.editorActionsRef?.current?.onPreview);
  const hasOnExecute = useCanvasStore(s => !!s.editorActionsRef?.current?.onExecute);

  // Anteprima inline post-run (#5): il nodo reagisce a `lastTaskResult` (già
  // nello store apposta) e auto-carica le prime righe senza aprire la modale.
  const projectId = useCanvasStore(s => s.projectId);
  const lastTaskResult = useCanvasStore(s => s.lastTaskResult);
  const inlinePreview = useInlinePreview(projectId);
  const inlineRunRef = useRef(inlinePreview.run);
  inlineRunRef.current = inlinePreview.run;
  // Init al token già presente: un risultato lingering (da un progetto
  // precedente) non deve scatenare un fetch spurio al mount — solo un
  // cambio reale post-mount avvia l'anteprima.
  const lastRunTokenRef = useRef(lastTaskResult?.task_id || null);

  // Auto-trigger una sola volta per esecuzione riuscita (dedup su task_id).
  useEffect(() => {
    if (!lastTaskResult?.success) return;
    const token = lastTaskResult.task_id || null;
    if (token && token === lastRunTokenRef.current) return;
    lastRunTokenRef.current = token;
    inlineRunRef.current(INLINE_PREVIEW_LIMIT);
  }, [lastTaskResult]);

  // Cambio progetto/canvas → azzera l'anteprima e il dedup-token.
  const inlineClearRef = useRef(inlinePreview.clear);
  inlineClearRef.current = inlinePreview.clear;
  useEffect(() => {
    lastRunTokenRef.current = null;
    inlineClearRef.current();
  }, [projectId]);

  // Conta edge in entrata — utile per invalidare il memo di upstreamColumns
  const incomingEdgeCount = useStore((state) => {
    const edges = dataFlowEdges(state.edges);
    return edges.filter(e => e.target === id).length;
  });

  // Signature delle colonne upstream — triggera re-calc quando cambia
  const sourceColumnsSignature = useStore((state) => {
    const edges = dataFlowEdges(state.edges);
    const nodes = state.nodes || [];
    const incomingEdges = edges.filter(e => e.target === id);

    return incomingEdges.map(edge => {
      const sourceNode = nodes.find(n => n.id === edge.source);
      const cols = sourceNode?.data?.columns || [];
      return cols.map(c => typeof c === 'string' ? c : c.name).join(',');
    }).join('|');
  });

  // Calcola colonne upstream (passate alla view standard e ai merge in geom)
  const upstreamColumns = useMemo(() => {
    const edges = dataFlowEdges(getEdges());
    const nodes = getNodes();
    const incomingEdges = edges.filter(e => e.target === id);

    if (incomingEdges.length === 0) return [];

    const sourceEdge = incomingEdges[0];
    const sourceNode = nodes.find(n => n.id === sourceEdge.source);

    if (!sourceNode) return [];

    const cols = sourceNode.data?.columns || [];
    return cols.map(c => ({
      name: typeof c === 'string' ? c : (c.alias || c.name),
      type: typeof c === 'string' ? 'str' : (c.type || 'str'),
    }));
  }, [id, getEdges, getNodes, incomingEdgeCount, sourceColumnsSignature]);

  // Tipi progetto (per header icon/title + variante view)
  const isManipulate = data.isManipulate || false;
  const isArricchisci = data.isArricchisci || false;

  // Header icon/title/variant (comuni a IMDF + standard; executeLabel passato a IMDF)
  const nodeVariant = isArricchisci ? 'arricchisci' : (isManipulate ? 'manipola' : 'estrai');
  const config = {
    estrai: { icon: '📁', title: 'Output', executeLabel: 'Esegui Query' },
    manipola: { icon: '🔧', title: 'Output Manipola', executeLabel: 'Esegui Pipeline' },
    arricchisci: { icon: '🔗', title: 'Output Arricchisci', executeLabel: 'Esegui Arricchimento' },
  };
  const { icon, title } = config[nodeVariant];

  // Stato validazione (condiviso: ValidationModal aperta sia da standard sia da IMDF)
  const [showValidation, setShowValidation] = useState(false);
  const [validationResult, setValidationResult] = useState({ isValid: true, issues: [] });

  const confirm = useConfirm();

  const handleDelete = async () => {
    const ok = await confirm({
      title: 'Elimina nodo risultato',
      message: 'Eliminare il nodo risultato?',
      confirmText: 'Elimina',
      variant: 'danger',
    });
    if (ok) {
      useCanvasStore.getState().deleteNode(id);
    }
  };

  // Validazione orizzontale via backend (Epic CanvasVal §12): salva-poi-valida,
  // così il backend valida ESATTAMENTE il canvas che vedi (update_canvas azzera
  // canvas_validated, POST /validate lo risetta in base all'esito). Single source
  // of truth: niente più validazione JS per-famiglia.
  const validateViaBackend = async () => {
    const store = useCanvasStore.getState();
    try {
      await store.handleSaveQuiet();
      const data = await validateProjectCanvas(store.projectId);
      return backendReportToResult(data);
    } catch (e) {
      console.error('Validazione backend fallita:', e);
      return validationRequestFailedResult();
    }
  };

  // Broadcast post-validation: aggiorna lo store con i nodeId delle issue
  // (highlight amber) e il sommario severity (badge toolbar).
  const broadcastHighlights = (result) => {
    const store = useCanvasStore.getState();
    const nodeIds = result.issues
      .filter(i => i.nodeId)
      .map(i => i.nodeId);
    store.setValidationHighlights(nodeIds);
    const issues = result.issues || [];
    store.setLastValidationSummary({
      errorCount: result.bySeverity?.error?.length ?? issues.filter(i => i.severity === 'error').length,
      warningCount: result.bySeverity?.warning?.length ?? issues.filter(i => i.severity === 'warning').length,
      infoCount: result.bySeverity?.info?.length ?? issues.filter(i => i.severity === 'info').length,
    });
  };

  const handleValidate = async () => {
    const result = await validateViaBackend();
    broadcastHighlights(result);
    setValidationResult(result);
    setShowValidation(true);
  };

  // options={ force_recalculate } dal chiamante. StandardView passa valore reale;
  // ImdfView chiama onExecute() senza args → default force_recalculate=false
  // (equivalente al comportamento pre-split, executionMode='cache' iniziale).
  const handleExecuteWithValidation = async (options = { force_recalculate: false }) => {
    const result = await validateViaBackend();
    broadcastHighlights(result);
    if (!result.isValid) {
      setValidationResult(result);
      setShowValidation(true);
      return;
    }
    const onExecute = useCanvasStore.getState().editorActionsRef.current.onExecute;
    if (onExecute) onExecute(id, options);
  };

  const handlePreviewWithValidation = async () => {
    const result = await validateViaBackend();
    broadcastHighlights(result);
    if (!result.isValid) {
      setValidationResult(result);
      setShowValidation(true);
      return;
    }
    useCanvasStore.getState().editorActionsRef.current.onPreview?.(id);
  };

  // Click-to-focus: chiude il modal e centra il canvas sul nodo problematico.
  const handleFocusNode = useCallback((nodeId) => {
    const node = getNodes().find(n => n.id === nodeId);
    if (!node) return;
    setShowValidation(false);
    setCenter(
      node.position.x + (node.width || 200) / 2,
      node.position.y + (node.height || 80) / 2,
      { zoom: 1.2, duration: 400 },
    );
  }, [getNodes, setCenter]);

  // Registra onValidate nello store per il badge toolbar.
  // Usa ref stabile per evitare re-register churn (handleValidate cambia ogni render).
  const handleValidateRef = useRef(handleValidate);
  handleValidateRef.current = handleValidate;
  useEffect(() => {
    useCanvasStore.getState().setEditorActions({
      onValidate: (...args) => handleValidateRef.current(...args),
    });
  }, []);

  return (
    <div className={`result-node result-node--${nodeVariant}`}>
      <Handle
        type="target"
        position={Position.Left}
        id="input"
        className="result-node__handle result-node__handle--input"
      />

      <Handle
        type="source"
        position={Position.Right}
        id="output"
        className="result-node__handle result-node__handle--output"
      />

      <div className="result-node__header">
        <div className="result-node__title">
          <span className="result-node__title-icon">{icon}</span>
          <span>{title}</span>
        </div>
        <button type="button"
          onClick={handleDelete}
          className="result-node__delete-btn"
          title="Elimina nodo"
        >
          ×
        </button>
      </div>

      <div className="result-node__body">
        <ResultNodeStandardView
          data={data}
          id={id}
          upstreamColumns={upstreamColumns}
          hasOnPreview={hasOnPreview}
          hasOnExecute={hasOnExecute}
          onValidate={handleValidate}
          onExecute={handleExecuteWithValidation}
          onPreview={handlePreviewWithValidation}
        />

        <ResultInlinePreview
          state={inlinePreview}
          onExpand={handlePreviewWithValidation}
          onRefresh={() => inlinePreview.run(INLINE_PREVIEW_LIMIT)}
        />

        <ValidationModal
          isOpen={showValidation}
          onClose={() => setShowValidation(false)}
          result={validationResult}
          onFocusNode={handleFocusNode}
        />
      </div>
    </div>
  );
});

ResultNode.displayName = 'ResultNode';

export default ResultNode;
