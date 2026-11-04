/**
 * useRuntimeInputDefinitions — input runtime chiesti al momento
 * dell'esecuzione (nodi project_input non di tipo file), uguali nei tre
 * editor (audit R9.1: prima solo Estrai li chiedeva, Manipola e Arricchisci
 * li ignoravano). Ordinati per `order`; per il tipo "connection" le opzioni
 * vengono dal set di connessioni, caricato solo se serve.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { listConnectionSets } from '../api';

// Stesso default del backend (eseguibili/manifest.py): senza tipo = file.
export const runtimeInputKind = (node) => String(node?.data?.input_kind || 'file').trim().toLowerCase();

/**
 * Input chiesti nella finestra di esecuzione dell'EDITOR: i file no (nel
 * percorso progetto si usa il file gia' nel canvas; il caricamento di un
 * file vale solo negli Eseguibili).
 */
export function buildRuntimeInputDefinitions(nodes, connectionSets = []) {
  return (nodes || [])
    .filter(node => node.type === 'project_input' && runtimeInputKind(node) !== 'file')
    .map((node) => {
      const definition = { ...node.data, input_kind: runtimeInputKind(node) };
      if (definition.input_kind === 'connection') {
        const set = connectionSets.find(item => item.id === definition.connection_set_id);
        definition.connection_options = (set?.members || [])
          .filter(member => member.active)
          .map(member => ({ key: member.selector_key, label: member.label }));
      }
      return definition;
    })
    .sort((left, right) => (left.order || 0) - (right.order || 0));
}

export default function useRuntimeInputDefinitions(nodes) {
  const [connectionSets, setConnectionSets] = useState([]);
  const loadRef = useRef('idle'); // idle | loading | done | failed
  const mountedRef = useRef(true);
  useEffect(() => () => { mountedRef.current = false; }, []);
  const inputNodes = (nodes || []).filter(n => n.type === 'project_input');
  const needsSets = inputNodes.some(n => runtimeInputKind(n) === 'connection');
  // Le definizioni cambiano solo se cambiano gli input (non a ogni modifica
  // del canvas): la finestra non perde i valori digitati.
  const inputsKey = JSON.stringify(inputNodes.map(n => [n.id, n.data]));

  useEffect(() => {
    if (!needsSets || loadRef.current === 'loading' || loadRef.current === 'done') return;
    loadRef.current = 'loading';
    listConnectionSets()
      .then(data => {
        loadRef.current = 'done';
        if (mountedRef.current) setConnectionSets(data.connection_sets || []);
      })
      // Fallito: si riprova al prossimo cambio degli input.
      .catch(() => { loadRef.current = 'failed'; });
  }, [needsSets, inputsKey]);

  return useMemo(
    () => buildRuntimeInputDefinitions(JSON.parse(inputsKey).map(([id, data]) => ({ id, type: 'project_input', data })), connectionSets),
    [inputsKey, connectionSets],
  );
}
