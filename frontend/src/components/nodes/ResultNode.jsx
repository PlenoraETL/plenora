/**
 * ResultNode.jsx — Nodo output unificato (ESTRAI, MANIPOLA, ARRICCHISCI)
 *
 * Nodo terminale della pipeline. Decide la modalita' (IMDF vs standard) in
 * base all'upstream e delega la view corrispondente a un subcomponente.
 *
 * Owna (post Batch 2 split):
 *   - subscriptions canvas (edges, upstream node, hasOnPreview/Execute)
 *   - calcolo upstreamColumns (richiede useReactFlow)
 *   - detection isImdfMode (upstream=api_enrich con output_kind='imdf')
 *   - logica validazione: runValidation + handleValidate/Execute/Preview
 *   - ValidationModal (condivisa tra IMDF view e standard view)
 *   - header + delete (comune a entrambi i rami)
 *
 * Delega:
 *   - ResultNodeImdfView: view quando isImdfMode=true (Batch 2 step 2)
 *   - ResultNodeStandardView: view altrimenti (Batch 2 step 3)
 */
import React, { useState, memo, useMemo } from 'react';
import { Handle, Position, useReactFlow, useStore } from 'reactflow';
import ValidationModal from '../modals/ValidationModal';
import { validateManipolaCanvas } from '../../utils/validateManipolaCanvas';
import { validateEstraiCanvas } from '../../utils/validateEstraiCanvas';
import { validateArricchisciCanvas } from '../../utils/validateArricchisciCanvas';
import useCanvasStore from '../../store/useCanvasStore';
import ResultNodeImdfView from './result/ResultNodeImdfView';
import ResultNodeStandardView from './result/ResultNodeStandardView';
import { useConfirm } from '../../context/ConfirmContext';
import './ResultNode.css';

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
  const { getEdges, getNodes } = useReactFlow();

  // Sottoscrizioni reattive per i bottoni Preview/Esegui (propagate alle view)
  const hasOnPreview = useCanvasStore(s => !!s.editorActionsRef?.current?.onPreview);
  const hasOnExecute = useCanvasStore(s => !!s.editorActionsRef?.current?.onExecute);

  // Conta edge in entrata — utile per invalidare il memo di upstreamColumns
  const incomingEdgeCount = useStore((state) => {
    const edges = state.edges || [];
    return edges.filter(e => e.target === id).length;
  });

  // Signature delle colonne upstream — triggera re-calc quando cambia
  const sourceColumnsSignature = useStore((state) => {
    const edges = state.edges || [];
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
    const edges = getEdges();
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

  // Detection: upstream api_enrich con output_kind? Se sì, result si specializza (IMDF).
  // Legge dallo store per reattivita' a updateNodeData sull'upstream.
  const upstreamOutputKind = useCanvasStore((state) => {
    const edges = state.edges || [];
    const nodes = state.nodes || [];
    const incomingEdges = edges.filter(e => e.target === id);
    if (incomingEdges.length === 0) return null;
    const sourceNode = nodes.find(n => n.id === incomingEdges[0].source);
    if (!sourceNode || sourceNode.type !== 'api_enrich') return null;
    return sourceNode.data?.output_kind || null;
  });
  const isImdfMode = upstreamOutputKind === 'imdf';

  // Tipi progetto (per header icon/title + runValidation)
  const isManipulate = data.isManipulate || false;
  const isArricchisci = data.isArricchisci || false;

  // Header icon/title/variant (comuni a IMDF + standard; executeLabel passato a IMDF)
  const nodeVariant = isArricchisci ? 'arricchisci' : (isManipulate ? 'manipola' : 'estrai');
  const config = {
    estrai: { icon: '📁', title: 'Output', executeLabel: 'Esegui Query' },
    manipola: { icon: '🔧', title: 'Output Manipola', executeLabel: 'Esegui Pipeline' },
    arricchisci: { icon: '🔗', title: 'Output Arricchisci', executeLabel: 'Esegui Arricchimento' },
  };
  const { icon, title, executeLabel } = config[nodeVariant];

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

  const runValidation = (availableConnectionIds = null) => {
    const state = useCanvasStore.getState();
    const allNodes = state.nodes || [];
    const allEdges = state.edges || [];
    if (isManipulate) {
      const registry = state.transformsRegistry || null;
      return validateManipolaCanvas(allNodes, allEdges, registry);
    }
    if (isArricchisci) {
      return validateArricchisciCanvas(allNodes, allEdges, availableConnectionIds);
    }
    return validateEstraiCanvas(allNodes, allEdges);
  };

  const handleValidate = async () => {
    let availableConnectionIds = null;
    if (isArricchisci) {
      try {
        const { listApiConnections } = await import('../../api/apiConnections');
        const res = await listApiConnections();
        availableConnectionIds = new Set((res.api_connections || res.connections || []).map(c => c.id));
      } catch (e) {
        console.warn('Impossibile caricare connessioni per validazione:', e);
      }
    }
    const result = runValidation(availableConnectionIds);
    setValidationResult(result);
    setShowValidation(true);
  };

  // options={ force_recalculate } dal chiamante. StandardView passa valore reale;
  // ImdfView chiama onExecute() senza args → default force_recalculate=false
  // (equivalente al comportamento pre-split, executionMode='cache' iniziale).
  const handleExecuteWithValidation = async (options = { force_recalculate: false }) => {
    let availableConnectionIds = null;
    if (isArricchisci) {
      try {
        const { listApiConnections } = await import('../../api/apiConnections');
        const res = await listApiConnections();
        availableConnectionIds = new Set((res.api_connections || res.connections || []).map(c => c.id));
      } catch (e) { /* fallback senza check */ }
    }
    const result = runValidation(availableConnectionIds);
    if (!result.isValid) {
      setValidationResult(result);
      setShowValidation(true);
      return;
    }
    const onExecute = useCanvasStore.getState().editorActionsRef.current.onExecute;
    if (onExecute) onExecute(id, options);
  };

  const handlePreviewWithValidation = () => {
    const result = runValidation();
    if (!result.isValid) {
      setValidationResult(result);
      setShowValidation(true);
      return;
    }
    useCanvasStore.getState().editorActionsRef.current.onPreview?.(id);
  };

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

      {/* Header (comune ai due rami) */}
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

      {/* Body: ramo IMDF o standard */}
      <div className="result-node__body">
        {isImdfMode ? (
          <ResultNodeImdfView
            hasOnExecute={hasOnExecute}
            onExecute={handleExecuteWithValidation}
            nodeVariant={nodeVariant}
            executeLabel={executeLabel}
          />
        ) : (
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
        )}

        <ValidationModal
          isOpen={showValidation}
          onClose={() => setShowValidation(false)}
          result={validationResult}
        />
      </div>
    </div>
  );
});

ResultNode.displayName = 'ResultNode';

export default ResultNode;
