/**
 * useTask.js — IL ciclo di vita di un task asincrono in un componente
 * (Fase 5 T2, audit R4.6, R4.11, R4.12, R9.2, R9.4, N2, N12).
 *
 *   const task = useTask({ scope: projectId });
 *   await task.run(() => api.start(...), { onDone });
 *   task.state  // { phase, taskId, progress, message, status, result, error, code, cancelling, cancelError }
 *   task.cancel()  task.reset()
 *
 * Regole:
 *  - ogni run() ha un id: l'avvio o il polling di una richiesta superata
 *    (nuovo run, reset, cambio di `scope`, smontaggio) non scrive mai;
 *  - avvio asincrono (`{async: true, task_id}`) -> polling di utils/taskPoller
 *    (404, 5xx, 401/403 con esito `lost`, mai "completata" falsa); avvio con
 *    il risultato subito -> esito dello stesso utils/taskOutcome;
 *  - errore dell'avvio -> fase `failure` con il testo unico di
 *    utils/errorMessages, a meno che `onLaunchError(err)` dica di averlo
 *    gestito (es. 409 preparation_changed: si torna a `idle`);
 *  - cancel(): DELETE /tasks/{id}; il polling continua fino a `revoked` (o
 *    all'esito che arriva prima); 409 = gia' terminato, nessun errore;
 *  - cambio di `scope` (es. progetto) = reset.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { cancelTask as cancelTaskApi } from '../api';
import { pollTask } from '../utils/taskPoller';
import { syncOutcome, isActivePhase, isTerminalPhase } from '../utils/taskOutcome';
import { requestErrorMessage } from '../utils/errorMessages';

const IDLE_TASK = Object.freeze({
  phase: 'idle', taskId: null, progress: 0, message: '', status: null,
  result: null, error: null, code: null, cancelling: false, cancelError: null,
});

const fromOutcome = (o) => ({
  phase: o.phase, progress: o.progress, message: o.message, status: o.status,
  result: o.result, error: o.error, code: o.code,
});

export function useTask({ scope = null, interval, notFoundLimit, errorLimit, outcome } = {}) {
  const [state, setState] = useState(IDLE_TASK);
  const runIdRef = useRef(0);
  const stopRef = useRef(null);
  const taskIdRef = useRef(null);
  const optsRef = useRef({ interval, notFoundLimit, errorLimit, outcome });
  optsRef.current = { interval, notFoundLimit, errorLimit, outcome };

  const stop = useCallback(() => {
    runIdRef.current += 1;
    stopRef.current?.();
    stopRef.current = null;
  }, []);

  const reset = useCallback(() => {
    stop();
    taskIdRef.current = null;
    setState(IDLE_TASK);
  }, [stop]);

  // Smontaggio: nessuna scrittura dopo.
  useEffect(() => () => stop(), [stop]);
  // Cambio di contesto (es. progetto): il task di prima non scrive piu' qui.
  const scopeRef = useRef(scope);
  useEffect(() => {
    if (scopeRef.current === scope) return;
    scopeRef.current = scope;
    reset();
  }, [scope, reset]);

  /** Segue un task gia' avviato. */
  const follow = useCallback((taskId, { onUpdate, onDone, message = '' } = {}) => {
    stop();
    const id = runIdRef.current;
    taskIdRef.current = taskId;
    setState({ ...IDLE_TASK, phase: 'running', taskId, message });
    const { interval: iv, notFoundLimit: nf, errorLimit: el, outcome: oo } = optsRef.current;
    stopRef.current = pollTask(taskId, {
      interval: iv, notFoundLimit: nf, errorLimit: el, outcome: oo,
      onUpdate: (o, data) => {
        if (id !== runIdRef.current) return;
        if (!o.done) setState(s => ({ ...s, ...fromOutcome(o), message: o.message || s.message }));
        onUpdate?.(o, data);
      },
      onDone: (o) => {
        if (id !== runIdRef.current) return;
        stopRef.current = null;
        setState(s => ({ ...s, ...fromOutcome(o), cancelling: false }));
        onDone?.(o, taskId);
      },
    });
  }, [stop]);

  /**
   * Avvia: `launch()` restituisce la risposta dell'avvio.
   * @returns {Promise<{superseded?: boolean, taskId?: string, outcome?: Object, error?: any}>}
   */
  const run = useCallback(async (launch, { onUpdate, onDone, onLaunchError, startMessage = 'Avvio...' } = {}) => {
    stop();
    const id = runIdRef.current;
    taskIdRef.current = null;
    setState({ ...IDLE_TASK, phase: 'starting', message: startMessage });
    let response;
    try {
      response = await launch();
    } catch (err) {
      if (id !== runIdRef.current) return { superseded: true };
      if (onLaunchError?.(err)) {
        setState(IDLE_TASK);
        return { error: err, handled: true };
      }
      const failed = { phase: 'failure', error: requestErrorMessage(err), code: err?.response?.data?.error || null };
      setState({ ...IDLE_TASK, ...failed });
      onDone?.({ ...failed, done: true, result: null }, null);
      return { error: err };
    }
    if (id !== runIdRef.current) return { superseded: true };
    if (response?.async && response.task_id) {
      follow(response.task_id, { onUpdate, onDone, message: response.message || '' });
      return { taskId: response.task_id };
    }
    const o = syncOutcome(response);
    setState({ ...IDLE_TASK, ...fromOutcome(o) });
    onDone?.(o, null);
    return { outcome: o };
  }, [stop, follow]);

  const cancel = useCallback(async () => {
    const taskId = taskIdRef.current;
    if (!taskId) return;
    const id = runIdRef.current;
    setState(s => ({ ...s, cancelling: true, cancelError: null, message: 'Annullamento richiesto...' }));
    try {
      await cancelTaskApi(taskId);
    } catch (err) {
      if (id !== runIdRef.current) return;
      // 409: il task e' gia' terminato, l'esito arriva dal polling.
      if (err?.response?.status === 409) return;
      setState(s => ({ ...s, cancelling: false, cancelError: requestErrorMessage(err) }));
    }
  }, []);

  return {
    state,
    isActive: isActivePhase(state.phase),
    isDone: isTerminalPhase(state.phase),
    run, follow, cancel, reset, stop,
  };
}

export default useTask;
