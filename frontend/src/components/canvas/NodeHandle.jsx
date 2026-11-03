/**
 * NodeHandle — <Handle> di React Flow che si disegna SOLO dentro un nodo
 * del canvas.
 *
 * Nuova interfaccia (Fase 4, M1): il form completo di un nodo viene mostrato
 * anche nel pannello laterale (NodeInspector), fuori dal canvas. Li' un
 * <Handle> non ha un nodo di riferimento e React Flow lo segnalerebbe come
 * errore. I componenti dei nodi usano NodeHandle al posto di Handle: sul
 * canvas e' identico, nel pannello non disegna nulla.
 */
import React from 'react';
import { Handle, useNodeId } from '@xyflow/react';

export default function NodeHandle(props) {
  const nodeId = useNodeId();
  if (!nodeId) return null;
  return <Handle {...props} />;
}
