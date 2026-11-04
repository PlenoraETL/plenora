/**
 * branchNodes.js — dati dei passi creati da "Collega" e "Duplica ramo"
 * (Manipola, pannello laterale).
 *
 * Regola 7 del Blocco A: nessuno dei due scrive `data.title` (solo testo
 * dell'utente). Il nome del ramo e' un metadato, `branch_of`, che il
 * descrittore mostra come suffisso del titolo derivato
 * (nodeDescriptors.derivedTitle): il titolo segue l'operazione, anche dopo
 * un cambio o una conversione.
 */
import { backendAccessFlags, persistedNodeData, persistenceValue } from './nodeTypeRegistry';

/** "Collega": il Join (o Concatena) che riceve il ramo `branchName`. */
export function connectStepData(op, config, branchName) {
  return { transform_op: op, config, branch_of: branchName || undefined };
}

/**
 * "Duplica ramo": i dati di un passo copiato sulla sorgente `otherName`.
 * Senza colonne derivate (persistedNodeData non le porta: le calcola il
 * server sul nuovo ramo, Blocco A regola 4), ne' flag di accesso del
 * nodo originale (stessa regola di backendAccessFlags), ne' il titolo
 * dell'originale: la copia si distingue col suffisso del ramo.
 */
export function cloneStepData(node, otherName) {
  const accessKeys = Object.keys(backendAccessFlags(node));
  const data = Object.fromEntries(Object.entries(persistenceValue(persistedNodeData(node)))
    .filter(([k]) => k !== 'title' && !accessKeys.includes(k)));
  return { ...data, branch_of: otherName || undefined };
}
