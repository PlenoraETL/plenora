/**
 * PipelineRunner — UNICO percorso di validazione, anteprima ed esecuzione
 * dell'editor (Fase 4, M7). Montato da EditorShell, quindi sempre presente:
 * "▶ Esegui" della barra, il badge di validazione e il pannello dell'Output
 * funzionano anche con l'Output non aperto nel pannello, e l'esito (finestra
 * di validazione) non si perde se il pannello si chiude durante l'avvio.
 *
 * Azioni registrate nello store:
 *   onValidate()                 Valida (badge, pannello)
 *   runPipeline(options?)        Esegui (barra, pannello); senza opzioni usa runOptions
 *   previewWithValidation(id?)   Anteprima completa dell'Output
 *   showValidationResult(result) mostra un esito (es. salvataggio fallito)
 *
 * Ordine di Esegui: risorse accessibili e tipi di input eseguibili in questa
 * modalita' -> salva (utils/serverActions) -> valida -> lancia. Con runState
 * 'starting' o 'running' non parte niente (un'esecuzione alla volta).
 * Valida e Anteprima leggono la versione salvata: con modifiche non salvate
 * chiedono prima "Salva e continua".
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import ValidationModal from '../modals/ValidationModal';
import { validateProjectCanvas } from '../../api/projects';
import {
  accessDeniedResult, backendReportToResult, saveFailedResult,
  unsupportedInputsResult, validationRequestFailedResult,
} from '../../utils/backendValidationResult';
import { describeAccessDeniedNodes } from '../../utils/canvasAccessControl';
import { unsupportedInputNodes } from '../../utils/runtimeInputKinds';
import { saveBeforeServerRead, saveForRun } from '../../utils/serverActions';
import useCanvasStore from '../../store/useCanvasStore';

const OUTPUT_TYPES = new Set(['result', 'resultNode']);
const outputId = (nodes) => (nodes || []).find(n => OUTPUT_TYPES.has(n.type))?.id || null;

// Il backend valida il canvas SALVATO; `before` decide come ci si arriva.
// null = l'utente ha annullato.
async function validateViaBackend(before) {
  try {
    if ((await before()) === false) return null;
  } catch (e) {
    console.error('Salvataggio prima della validazione fallito:', e);
    return saveFailedResult(e);
  }
  try {
    return backendReportToResult(await validateProjectCanvas(useCanvasStore.getState().projectId));
  } catch (e) {
    console.error('Validazione backend fallita:', e);
    return validationRequestFailedResult();
  }
}

// Evidenziazione dei nodi e sommario per il badge.
function broadcastHighlights(result) {
  const store = useCanvasStore.getState();
  const issues = result.issues || [];
  store.setValidationHighlights?.(issues.filter(i => i.nodeId).map(i => i.nodeId));
  store.setLastValidationSummary?.({
    errorCount: result.bySeverity?.error?.length ?? issues.filter(i => i.severity === 'error').length,
    warningCount: result.bySeverity?.warning?.length ?? issues.filter(i => i.severity === 'warning').length,
    infoCount: result.bySeverity?.info?.length ?? issues.filter(i => i.severity === 'info').length,
  });
}

/** Controlli prima di salvare: risorse non accessibili, tipi di input. */
export function runBlockedResult(nodes, mode) {
  const denied = describeAccessDeniedNodes(nodes);
  if (denied) {
    console.warn(`[run-blocked] access_denied nodes:\n${denied.summary}`);
    return accessDeniedResult(denied);
  }
  const unsupported = unsupportedInputNodes(nodes, mode);
  return unsupported.length ? unsupportedInputsResult(unsupported) : null;
}

export default function PipelineRunner() {
  const [shown, setShown] = useState(null);

  const showResult = useCallback((result) => {
    broadcastHighlights(result);
    setShown(result);
  }, []);

  const validate = useCallback(async () => {
    const result = await validateViaBackend(() => saveBeforeServerRead('La validazione'));
    if (result) showResult(result);
  }, [showResult]);

  const run = useCallback(async (options) => {
    const store = useCanvasStore.getState();
    if (store.runState === 'starting' || store.runState === 'running') return;
    const blocked = runBlockedResult(store.nodes, store.mode);
    if (blocked) { showResult(blocked); return; }
    const runOptions = options || store.runOptions || { force_recalculate: false };
    store.setRunState('starting');
    try {
      const result = await validateViaBackend(saveForRun);
      if (!result) return;
      broadcastHighlights(result);
      if (!result.isValid) { setShown(result); return; }
      const onExecute = useCanvasStore.getState().getEditorAction('onExecute');
      if (onExecute) await onExecute(outputId(useCanvasStore.getState().nodes), runOptions);
    } catch (e) {
      console.error('Avvio esecuzione fallito:', e);
      showResult(validationRequestFailedResult(`Avvio non riuscito: ${e?.message || 'errore inatteso'}.`));
    } finally {
      // L'esecuzione vera tiene runState 'running' (useExecution).
      if (useCanvasStore.getState().runState === 'starting') useCanvasStore.getState().setRunState('idle');
    }
  }, [showResult]);

  const preview = useCallback(async (id) => {
    const result = await validateViaBackend(() => saveBeforeServerRead("L'anteprima"));
    if (!result) return;
    broadcastHighlights(result);
    if (!result.isValid) { setShown(result); return; }
    useCanvasStore.getState().getEditorAction('onPreview')?.(id || outputId(useCanvasStore.getState().nodes));
  }, []);

  // Riferimenti stabili: le azioni registrate non cambiano a ogni render.
  const ref = useRef({});
  ref.current = { validate, run, preview, showResult };
  useEffect(() => {
    const st = useCanvasStore.getState();
    st.setEditorActions({
      onValidate: (...a) => ref.current.validate(...a),
      runPipeline: (...a) => ref.current.run(...a),
      previewWithValidation: (...a) => ref.current.preview(...a),
      showValidationResult: (...a) => ref.current.showResult(...a),
    });
    st.setRunReady?.(true);
    return () => {
      useCanvasStore.getState().setEditorActions({ onValidate: null, runPipeline: null, previewWithValidation: null, showValidationResult: null });
      useCanvasStore.getState().setRunReady?.(false);
    };
  }, []);

  // "Vai al nodo": chiude la finestra e porta al nodo NELLA VISTA APERTA
  // (canvas: centrato; Passi: scorre al passo), con lo stesso meccanismo di
  // "Vedi nel canvas" / "Vedi in Passi" (store.setCanvasView + focusNodeId).
  const focusNode = useCallback((nodeId) => {
    const st = useCanvasStore.getState();
    if (!st.nodes.some(n => n.id === nodeId)) return;
    setShown(null);
    st.setCanvasView(st.canvasView, nodeId);
  }, []);

  return (
    <ValidationModal
      isOpen={Boolean(shown)}
      onClose={() => setShown(null)}
      result={shown || { isValid: true, issues: [] }}
      onFocusNode={focusNode}
    />
  );
}
