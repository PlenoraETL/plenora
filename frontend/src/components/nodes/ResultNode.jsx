/**
 * ResultNode.jsx — Nodo output unificato (ESTRAI, MANIPOLA, ARRICCHISCI)
 *
 * Nodo terminale della pipeline. Delega la view a ResultNodeStandardView.
 * (Post-collasso IMDF a una-run: niente piu' vista IMDF dedicata; l'output
 * IMDF e' un bundle JSON come ogni altro artefatto, pubblicato via il nodo
 * carica_arcgis downstream.)
 *
 * Owna:
 *   - subscriptions canvas (hasOnPreview/Execute)
 *   - colonne in ingresso e in uscita dalla fonte unica (hooks/useColumns,
 *     Blocco A regola 4), con gli id: nessun calcolo qui
 *   - pulsanti Valida/Esegui/Anteprima: delegano al percorso unico
 *     dell'editor (editor/PipelineRunner, Fase 4 M7)
 *
 * Intestazione, chiusura ed eliminazione sono del pannello (NodeInspector,
 * regola 6 del Blocco A).
 */
import React, { useEffect, useRef, memo, useMemo } from 'react';
import { Position } from '@xyflow/react';
import { useShallow } from 'zustand/react/shallow';
import { saveFailedResult } from '../../utils/backendValidationResult';
import { saveBeforeServerRead } from '../../utils/serverActions';
import useCanvasStore from '../../store/useCanvasStore';
import ResultNodeStandardView from './result/ResultNodeStandardView';
import ResultInlinePreview from './result/ResultInlinePreview';
import useInlinePreview from '../../hooks/useInlinePreview';
import { useColumnsOf } from '../../hooks/useColumns';
import { estraiSourceLabel } from '../../utils/columnSource';
import './ResultNode.css';
import NodeHandle from '../canvas/NodeHandle';

// #5: righe caricate nell'anteprima inline auto post-run (la modale completa
// resta a 100). Volutamente piccolo: gira su cache subito dopo l'esecuzione.
const INLINE_PREVIEW_LIMIT = 10;
const NO_LABELS = [];

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

  // Fonte unica: ingresso dell'Output (Estrai: tabelle e layer a monte) e
  // uscita prevista dal server (dopo "Colonne in uscita" e GROUP BY).
  const input = useColumnsOf(id, { side: 'input' });
  const output = useColumnsOf(id);
  const mode = useCanvasStore(s => s.mode);
  // Estrai: il nome di ogni tabella o layer in ingresso (selettore per nodo:
  // solo i nomi, non le posizioni; un trascinamento non ridisegna).
  const inputLabels = useCanvasStore(useShallow(s => (mode !== 'estrai' ? NO_LABELS
    : input.inputs.map(i => estraiSourceLabel((s.nodes || []).find(n => n.id === i.nodeId)) || null))));
  const upstreamColumns = useMemo(() => {
    if (mode !== 'estrai') return input.columns;
    // Estrai: ogni colonna sa da quale tabella o layer viene (tabella.colonna).
    const labels = new Map(input.inputs.map((i, idx) => [i.nodeId, inputLabels[idx]]));
    return input.columns.map(c => ({ ...c, source_table: labels.get(c.input_node) || null }));
  }, [input, inputLabels, mode]);

  // Variante della vista (stili per editor)
  const isManipulate = data.isManipulate || false;
  const isArricchisci = data.isArricchisci || false;
  const nodeVariant = isArricchisci ? 'arricchisci' : (isManipulate ? 'manipola' : 'estrai');

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

      <div className="result-node__body">
        <ResultNodeStandardView
          data={data}
          id={id}
          upstreamColumns={upstreamColumns}
          outputColumns={output.columns}
          columnsResult={input}
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
