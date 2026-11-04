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
 *   - pulsanti Valida/Esegui/Anteprima: delegano al percorso unico
 *     dell'editor (editor/PipelineRunner, Fase 4 M7)
 *   - header + delete
 */
import React, { useEffect, useRef, memo, useMemo } from 'react';
import { Position, useReactFlow, useStore } from '@xyflow/react';
import { saveFailedResult } from '../../utils/backendValidationResult';
import { saveBeforeServerRead } from '../../utils/serverActions';
import useCanvasStore from '../../store/useCanvasStore';
import ResultNodeStandardView from './result/ResultNodeStandardView';
import ResultInlinePreview from './result/ResultInlinePreview';
import useInlinePreview from '../../hooks/useInlinePreview';
import { useConfirm } from '../../context/ConfirmContext';
import { dataFlowEdges } from '../../utils/runtimeBindingEdges';
import './ResultNode.css';
import NodeHandle from '../canvas/NodeHandle';

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
  const { getEdges, getNodes } = useReactFlow();

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

  // Valida, Esegui e Anteprima: percorso unico dell'editor (Fase 4, M7,
  // editor/PipelineRunner, sempre montato). Il nodo li chiama soltanto.
  const action = (name) => (...args) => Promise.resolve(useCanvasStore.getState().getEditorAction(name)?.(...args))
    .catch(err => console.error(`[ResultNode] ${name}:`, err));
  const handleValidate = action('onValidate');
  const handleExecute = action('runPipeline');
  const handlePreview = () => action('previewWithValidation')(id);

  // "Aggiorna" dell'anteprima nel nodo: legge la versione salvata come le altre.
  const handleInlineRefresh = async () => {
    try {
      if (!(await saveBeforeServerRead("L'anteprima"))) return;
    } catch (e) {
      useCanvasStore.getState().getEditorAction('showValidationResult')?.(saveFailedResult(e));
      return;
    }
    inlinePreview.run(INLINE_PREVIEW_LIMIT);
  };

  return (
    <div className={`result-node result-node--${nodeVariant}`}>
      <NodeHandle
        type="target"
        position={Position.Left}
        id="input"
        className="result-node__handle result-node__handle--input"
      />

      <NodeHandle
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
          onExecute={handleExecute}
          onPreview={handlePreview}
        />

        <ResultInlinePreview
          state={inlinePreview}
          onExpand={handlePreview}
          onRefresh={handleInlineRefresh}
        />

      </div>
    </div>
  );
});

ResultNode.displayName = 'ResultNode';

export default ResultNode;
