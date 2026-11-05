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
 * modalita' -> salva (utils/serverActions) -> valida -> prepara -> lancia.
 * "Prepara" e' la finestra UNICA «Prima di avviare» (BeforeRunModal, Blocco
 * A regola 5), aperta solo se serve (utils/beforeRun.beforeRunReasons):
 * input runtime, opzioni dello staff, stima di Arricchisci da confermare
 * (POST /preflight con gli stessi input dell'avvio), avvisi che bloccano.
 * Altrimenti Esegui parte con un clic. L'avvio di Arricchisci porta
 * l'impronta della stima; se il server risponde 409 preparation_changed la
 * stima si rifa' e la finestra mostra il motivo.
 * Con runState 'starting' (anche a finestra aperta) o 'running' non parte
 * niente (un'esecuzione alla volta). REGOLA: runState e' 'starting' dall'inizio
 * di OGNI preparazione dell'avvio fino all'apertura della finestra (che lo
 * tiene) o all'avvio vero ('running', useExecution). Le preparazioni sono:
 * Esegui (salva, valida, stima) e la stima rifatta dopo un 409
 * preparation_changed (avvio diretto o dalla finestra: l'esecuzione rifiutata
 * ha gia' riportato lo stato a 'idle'). L'UNICO punto che mette 'starting' e'
 * `beginPreparation`; `openWindow` (UNICO punto che apre la finestra) la
 * chiama. Gli errori mostrati (stima, avvio) passano da requestErrorMessage.
 * Valida e Anteprima leggono la versione salvata: con modifiche non salvate
 * chiedono prima "Salva e continua".
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ValidationModal from '../modals/ValidationModal';
import BeforeRunModal from './BeforeRunModal';
import { validateProjectCanvas } from '../../api/projects';
import { preflightEnrichment } from '../../api/enrichment';
import {
  accessDeniedResult, backendReportToResult, saveFailedResult,
  unsupportedInputsResult, validationRequestFailedResult,
} from '../../utils/backendValidationResult';
import { describeAccessDeniedNodes } from '../../utils/canvasAccessControl';
import { unsupportedInputNodes } from '../../utils/runtimeInputKinds';
import { saveBeforeServerRead, saveForRun } from '../../utils/serverActions';
import {
  beforeRunReasons, initialInputValues, inputsKey, isPreparationChanged, needsPreflight, runInputs,
} from '../../utils/beforeRun';
import useRuntimeInputDefinitions from '../../hooks/useRuntimeInputDefinitions';
import { nodeTitle } from '../canvas/nodeDescriptors';
import useCanvasStore from '../../store/useCanvasStore';
import { requestErrorMessage, backendMessage } from '../../utils/errorMessages';
import { restMigrationIssues } from '../../utils/restSecrets';

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
  return unsupported.length ? unsupportedInputsResult(unsupported, mode) : null;
}

// Input runtime del canvas: si ricalcolano solo quando cambiano i nodi di input.
const inputNodesKey = (s) => JSON.stringify((s.nodes || []).filter(n => n.type === 'project_input').map(n => [n.id, n.data]));

// UNICO punto che mette 'starting': inizio di una preparazione dell'avvio.
const beginPreparation = () => useCanvasStore.getState().setRunState('starting');

const setIdleIfStarting = () => {
  if (useCanvasStore.getState().runState === 'starting') useCanvasStore.getState().setRunState('idle');
};

const nodeLabel = (id) => {
  const node = useCanvasStore.getState().nodes.find(n => n.id === id);
  return node ? nodeTitle(node.type, node.data) : id;
};

/** Stima di Arricchisci con gli input dell'avvio: { preflight, preflightError }. */
async function preflightFor(inputs, runOptions) {
  try {
    const preflight = await preflightEnrichment(useCanvasStore.getState().projectId, {
      inputs, force_recalculate: Boolean(runOptions?.force_recalculate),
    });
    return { preflight, preflightError: null };
  } catch (e) {
    console.error('Stima prima di avviare non riuscita:', e);
    return { preflight: null, preflightError: requestErrorMessage(e) };
  }
}

export default function PipelineRunner({ user = null }) {
  const [shown, setShown] = useState(null);
  // Finestra «Prima di avviare»: null = chiusa.
  const [request, setRequest] = useState(null);
  const inputsSignature = useCanvasStore(inputNodesKey);
  const inputNodes = useMemo(
    () => JSON.parse(inputsSignature).map(([id, data]) => ({ id, type: 'project_input', data })),
    [inputsSignature],
  );
  const inputDefinitions = useRuntimeInputDefinitions(inputNodes);
  const definitionsRef = useRef(inputDefinitions);
  definitionsRef.current = inputDefinitions;

  const showResult = useCallback((result) => {
    broadcastHighlights(result);
    setShown(result);
  }, []);

  // UNICO punto che apre «Prima di avviare»: finestra aperta = 'starting'.
  const openWindow = useCallback((next) => {
    beginPreparation();
    setRequest(next);
  }, []);

  // Fase 5 T2 (R9.7): una lettura del server alla volta (Valida, Anteprima):
  // un secondo clic durante la prima non apre un altro "Salva e continua".
  const readingRef = useRef(false);
  const oneRead = useCallback(async (fn) => {
    if (readingRef.current) return;
    readingRef.current = true;
    try { await fn(); } finally { readingRef.current = false; }
  }, []);

  const validate = useCallback(() => oneRead(async () => {
    const result = await validateViaBackend(() => saveBeforeServerRead('La validazione'));
    if (result) showResult(result);
  }), [showResult, oneRead]);

  // Avvio vero: le scelte della finestra (o nessuna) vanno a onExecute.
  // 409 preparation_changed: stima rifatta e finestra riaperta col motivo.
  const launch = useCallback(async ({ inputs = {}, intermediateMode = null, fingerprint = null } = {}, runOptions) => {
    const opts = { ...runOptions };
    if (inputs && Object.keys(inputs).length) opts.inputs = inputs;
    if (intermediateMode) opts.intermediate_mode = intermediateMode;
    if (fingerprint) opts.preparation_fingerprint = fingerprint;
    const onExecute = useCanvasStore.getState().getEditorAction('onExecute');
    if (!onExecute) return false;
    try {
      await onExecute(outputId(useCanvasStore.getState().nodes), opts);
      return false;
    } catch (e) {
      if (!isPreparationChanged(e)) throw e;
      // Nuova preparazione: 'starting' anche durante la stima rifatta.
      beginPreparation();
      const st = useCanvasStore.getState();
      const definitions = definitionsRef.current;
      const estimate = await preflightFor(inputs || {}, runOptions);
      openWindow({
        id: Date.now(), mode: st.mode, inputDefinitions: definitions, user, runOptions,
        values: { ...initialInputValues(definitions), ...(inputs || {}) },
        preflightKey: inputsKey(runInputs(definitions, { ...initialInputValues(definitions), ...(inputs || {}) })),
        ...estimate,
        changedMessage: backendMessage(e?.response?.data) || 'La preparazione è cambiata dalla stima.',
      });
      return true;
    }
  }, [user, openWindow]);

  const run = useCallback(async (options) => {
    const store = useCanvasStore.getState();
    if (store.runState === 'starting' || store.runState === 'running') return;
    const runOptions = options || store.runOptions || { force_recalculate: false };
    // Credenziali REST nel canvas da migrare: la finestra «Prima di avviare»
    // lo dice e Avvia resta spento (utils/restSecrets). Niente salvataggio.
    const migration = restMigrationIssues(store.nodes);
    if (migration.length) {
      openWindow({
        id: Date.now(), mode: store.mode, inputDefinitions: [], user, runOptions, values: {},
        preflight: null, preflightError: null, preflightKey: null, changedMessage: null, migration,
      });
      return;
    }
    const blocked = runBlockedResult(store.nodes, store.mode);
    if (blocked) { showResult(blocked); return; }
    beginPreparation();
    // true = la finestra «Prima di avviare» resta aperta: lo stato resta
    // 'starting' finche' l'utente avvia o annulla.
    let windowOpen = false;
    try {
      const result = await validateViaBackend(saveForRun);
      if (!result) return;
      broadcastHighlights(result);
      if (!result.isValid) { setShown(result); return; }
      const st = useCanvasStore.getState();
      const definitions = definitionsRef.current;
      const values = initialInputValues(definitions);
      // Senza input da chiedere la stima si fa subito (con input, la fa la
      // finestra con i valori scelti).
      const estimate = needsPreflight(st.mode) && definitions.length === 0
        ? { ...(await preflightFor({}, runOptions)), preflightKey: inputsKey({}) }
        : { preflight: null, preflightError: null, preflightKey: null };
      const reasons = beforeRunReasons({ inputDefinitions: definitions, user, ...estimate });
      if (!reasons.length) {
        windowOpen = await launch({ fingerprint: estimate.preflight?.preparation_fingerprint || null }, runOptions);
        return;
      }
      windowOpen = true;
      openWindow({ id: Date.now(), mode: st.mode, inputDefinitions: definitions, user, runOptions, values, ...estimate, changedMessage: null });
    } catch (e) {
      console.error('Avvio esecuzione fallito:', e);
      showResult(validationRequestFailedResult(`Avvio non riuscito: ${requestErrorMessage(e)}`));
    } finally {
      // L'esecuzione vera tiene runState 'running' (useExecution).
      if (!windowOpen) setIdleIfStarting();
    }
  }, [showResult, launch, user, openWindow]);

  // Dalla finestra: avvia con le scelte fatte, o annulla.
  const launchFromWindow = useCallback(async (choice) => {
    const runOptions = request?.runOptions || useCanvasStore.getState().runOptions || { force_recalculate: false };
    setRequest(null);
    let windowOpen = false;
    try {
      windowOpen = await launch(choice, runOptions);
    } catch (e) {
      console.error('Avvio esecuzione fallito:', e);
      showResult(validationRequestFailedResult(`Avvio non riuscito: ${requestErrorMessage(e)}`));
    } finally {
      if (!windowOpen) setIdleIfStarting();
    }
  }, [request, launch, showResult]);
  const cancelWindow = useCallback(() => { setRequest(null); setIdleIfStarting(); }, []);
  const preflightFromWindow = useCallback(async (inputs) => {
    const res = await preflightFor(inputs, request?.runOptions);
    if (res.preflightError) throw new Error(res.preflightError);
    return res.preflight;
  }, [request]);

  const preview = useCallback((id) => oneRead(async () => {
    const result = await validateViaBackend(() => saveBeforeServerRead("L'anteprima"));
    if (!result) return;
    broadcastHighlights(result);
    if (!result.isValid) { setShown(result); return; }
    useCanvasStore.getState().getEditorAction('onPreview')?.(id || outputId(useCanvasStore.getState().nodes));
  }), [oneRead]);

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
    <>
      <ValidationModal
        isOpen={Boolean(shown)}
        onClose={() => setShown(null)}
        result={shown || { isValid: true, issues: [] }}
        onFocusNode={focusNode}
      />
      <BeforeRunModal
        request={request}
        onPreflight={preflightFromWindow}
        onLaunch={launchFromWindow}
        onCancel={cancelWindow}
        nodeLabel={nodeLabel}
      />
    </>
  );
}
