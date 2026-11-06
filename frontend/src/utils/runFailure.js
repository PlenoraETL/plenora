/**
 * runFailure — cosa dire e cosa offrire quando un'esecuzione fallisce
 * (UI-3, U16). Prima la finestra diceva solo il messaggio: niente passo
 * fallito, niente registro, niente segnalazione.
 *
 * Una regola per i tre editor e per gli Eseguibili:
 *  - failedStepOf: il passo che ha fallito. Prima l'id che il backend mette
 *    nell'esito (`error_node_id`: Arricchisci; Manipola dalla stessa data),
 *    poi un id o un titolo di nodo citato nel messaggio. null se non si sa:
 *    mai un passo indovinato a caso.
 *  - runLogPath: il registro delle esecuzioni filtrato sul progetto e sui
 *    fallimenti (LogViewer, scheda Esecuzioni).
 *  - failureTicketDraft: il ticket precompilato (progetto, esecuzione,
 *    passo, messaggio) per «Segnala».
 */
import { nodeTitle } from '../components/canvas/nodeDescriptors';

const quoted = (text) => [...String(text || '').matchAll(/[«"'“]([^»"'”]{2,80})[»"'”]/g)].map(m => m[1].trim());

/** { id, title } del passo fallito, o null. */
export function failedStepOf(result, nodes = []) {
  if (!result || result.success) return null;
  const list = (nodes || []).filter(n => n && n.type !== 'comment');
  const byId = (id) => list.find(n => n.id === id);
  const declared = result.error_node_id || result.result?.error_node_id || null;
  let node = declared ? byId(String(declared)) : null;
  const message = String(result.message || result.error || '');
  if (!node && message) node = list.find(n => message.includes(n.id)) || null;
  if (!node && message) {
    const names = quoted(message);
    node = list.find(n => names.includes(nodeTitle(n.type, n.data))) || null;
  }
  return node ? { id: node.id, title: nodeTitle(node.type, node.data) } : null;
}

/** Registro delle esecuzioni filtrato (scheda Esecuzioni del LogViewer). */
export function runLogPath({ projectId = null, status = 'failure' } = {}) {
  const params = new URLSearchParams();
  params.set('tab', 'runs');
  if (projectId) params.set('project', String(projectId));
  if (status) params.set('status', status);
  return `/logs?${params.toString()}`;
}

/** Ticket precompilato per «Segnala»: titolo, descrizione e categoria. */
export function failureTicketDraft({ projectName = '', projectId = null, projectType = '', result = null, step = null }) {
  const message = String(result?.message || result?.error || 'Errore sconosciuto');
  const lines = [
    `Progetto: ${projectName || '—'}${projectId ? ` (id ${projectId})` : ''}${projectType ? `, ${projectType}` : ''}`,
    `Esecuzione: ${result?.task_id || '—'}`,
    step ? `Passo fallito: ${step.title} (${step.id})` : null,
    `Messaggio: ${message}`,
    result?.code ? `Codice: ${result.code}` : null,
    '',
    'Cosa stavo facendo:',
  ].filter(l => l !== null);
  return {
    title: `Esecuzione non riuscita: ${projectName || 'progetto'}`.slice(0, 200),
    description: lines.join('\n'),
    category: 'pipeline',
    severity: 'medium',
  };
}
