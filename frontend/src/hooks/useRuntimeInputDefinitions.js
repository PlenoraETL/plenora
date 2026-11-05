/**
 * useRuntimeInputDefinitions — input runtime chiesti al momento
 * dell'esecuzione (nodi project_input non di tipo file), uguali nei tre
 * editor (audit R9.1: prima solo Estrai li chiedeva, Manipola e Arricchisci
 * li ignoravano). Ordinati per `order`; per il tipo "connection" le opzioni
 * vengono dal set di connessioni, caricato solo se serve.
 */
import { useMemo } from 'react';
import { loadSharedConnectionSets } from '../utils/connectionSets';
import { useResource } from './useResource';

const EMPTY = [];

// Stesso default del backend (eseguibili/manifest.py): senza tipo = file.
export const runtimeInputKind = (node) => String(node?.data?.input_kind || 'file').trim().toLowerCase();

/**
 * Input chiesti nella finestra di esecuzione dell'EDITOR: i file no (nel
 * percorso progetto si usa il file gia' nel canvas; il caricamento di un
 * file vale solo negli Eseguibili).
 */
export function buildRuntimeInputDefinitions(nodes, connectionSets = [], connectionSetsError = null) {
  return (nodes || [])
    .filter(node => node.type === 'project_input' && runtimeInputKind(node) !== 'file')
    .map((node) => {
      const definition = { ...node.data, input_kind: runtimeInputKind(node) };
      if (definition.input_kind === 'connection') {
        const set = connectionSets.find(item => item.id === definition.connection_set_id);
        // Set non caricati: l'errore arriva alla finestra (mai una scelta
        // vuota senza motivo; regola di hooks/useResource).
        if (connectionSetsError) definition.connection_options_error = connectionSetsError;
        definition.connection_options = (set?.members || [])
          .filter(member => member.active)
          .map(member => ({ key: member.selector_key, label: member.label }));
      }
      return definition;
    })
    .sort((left, right) => (left.order || 0) - (right.order || 0));
}

export default function useRuntimeInputDefinitions(nodes) {
  const inputNodes = (nodes || []).filter(n => n.type === 'project_input');
  const needsSets = inputNodes.some(n => runtimeInputKind(n) === 'connection');
  // Le definizioni cambiano solo se cambiano gli input (non a ogni modifica
  // del canvas): la finestra non perde i valori digitati.
  const inputsKey = JSON.stringify(inputNodes.map(n => [n.id, n.data]));
  // Set di connessioni condivisi (utils/connectionSets) dall'hook comune
  // (Fase 5 T2): chiesti solo se un input e' di tipo connessione; un errore
  // si riprova al prossimo cambio degli input (la chiave cambia).
  const sets = useResource(loadSharedConnectionSets, { key: ['connection-sets', inputsKey], enabled: needsSets, keepPreviousData: true });
  const connectionSets = sets.data || EMPTY;
  const setsError = sets.status === 'error' ? sets.error : null;

  return useMemo(
    () => buildRuntimeInputDefinitions(JSON.parse(inputsKey).map(([id, data]) => ({ id, type: 'project_input', data })), connectionSets, setsError),
    [inputsKey, connectionSets, setsError],
  );
}
