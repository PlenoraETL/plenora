/**
 * ConfirmContext — Provider centralizzato per ConfirmDialog promise-based
 * accessibile dai nodi figli (es. ReactFlow nodes) tramite hook
 * `useConfirm()` senza prop drilling.
 *
 * Motivazione (PR-B UX Tier 1):
 *   I nodi canvas (TransformNode, MultiJoinNode, FeatureLayerNode,
 *   TableNode, ResultNode) e il pannello laterale sono renderizzati internamente
 *   da ReactFlow via `nodeTypes` config, quindi non possono ricevere
 *   props direttamente dall'editor. Il pattern Context risolve senza
 *   forking di ReactFlow.
 *
 * Uso:
 *   // Editor page (root):
 *   <ConfirmProvider>
 *     <CanvasContent />
 *   </ConfirmProvider>
 *
 *   // Child node (anywhere inside provider):
 *   const confirm = useConfirm();
 *   const handleDelete = async () => {
 *     const ok = await confirm({
 *       title: 'Elimina nodo',
 *       message: 'Eliminare questo nodo?',
 *       variant: 'danger',
 *       confirmText: 'Elimina',
 *     });
 *     if (ok) deleteNode(id);
 *   };
 *
 * Mount: il <ConfirmDialog /> e' montato UNA SOLA VOLTA per Provider
 * (non per ogni nodo). Tutti i nodi figli condividono lo stesso modal
 * tramite la promise-based API di useConfirmModal (richieste in coda).
 *
 * Fase 5, T4 (R6.10): un ConfirmProvider avvolge TUTTA l'app (App.jsx),
 * quindi ogni componente ha il dialog del progetto; niente piu' ripiego su
 * window.confirm (test: tests/shared/noNativeDialogs.contract.test.js).
 */

import React, { createContext, useContext } from 'react';
import ConfirmDialog from '../components/common/ConfirmDialog';
import { useConfirmModal } from '../hooks/useConfirmModal';

const ConfirmContext = createContext(null);

export function ConfirmProvider({ children }) {
  const modal = useConfirmModal();
  return (
    <ConfirmContext.Provider value={modal.confirm}>
      {children}
      <ConfirmDialog {...modal} />
    </ConfirmContext.Provider>
  );
}

/**
 * Hook per evocare il ConfirmDialog promise-based dai nodi figli.
 * @returns {(config: object) => Promise<boolean>} la funzione confirm,
 *   da chiamare con `await confirm({title, message, variant, ...})`.
 *   Risolve true su conferma, false su annulla.
 * @throws {Error} se chiamato fuori da <ConfirmProvider>.
 */
export function useConfirm() {
  const confirmFn = useContext(ConfirmContext);
  if (!confirmFn) {
    throw new Error(
      'useConfirm() richiede <ConfirmProvider> nell\'albero React'
    );
  }
  return confirmFn;
}

/**
 * Come useConfirm, ma senza provider ritorna null invece di lanciare
 * (EditorShell: nei test montato da solo).
 */
export function useOptionalConfirm() {
  return useContext(ConfirmContext);
}

// Senza provider (componente montato da solo, es. nei test) nessuna azione
// che chiede conferma procede: fail-closed, mai window.confirm (R6.10).
const denyWithoutProvider = () => Promise.resolve(false);

/**
 * Conferma del dialog del progetto; senza provider rifiuta.
 * @returns {(config: object) => Promise<boolean>}
 */
export function useConfirmOrDeny() {
  return useContext(ConfirmContext) || denyWithoutProvider;
}
