/**
 * canvasAccessControl.js — Pre-flight check su nodi del canvas con
 * annotazione backend `data.access_denied=true`.
 *
 * Backend (RB.4.B / RB.7.B) marca i nodi che riferiscono risorse non
 * visibili al gruppo del progetto (file_source, source_project,
 * arcgis_connection cross-tenant). Lato frontend i deserializer
 * `nodeFactories` preservano `data.access_denied` (RB.4.C / #12-#13).
 *
 * Questi helper consentono al run globale (Arricchisci/Estrai/Manipola)
 * di:
 *   - mostrare un banner persistente quando il canvas contiene nodi denied;
 *   - bloccare l'esecuzione con un toast esplicativo prima di chiamare
 *     il backend (che fallirebbe poi a runtime con
 *     `_assert_file_source_group_access` — rete di sicurezza preservata);
 *   - lasciare l'utente libero di rimuovere/sostituire i nodi (non
 *     blocchiamo delete/edit, non rimuoviamo nodi automaticamente).
 *
 * Helper puri: zero dipendenze runtime, zero side-effects, sicuri da
 * usare in render React e in callback.
 */


/**
 * Filtra i nodi con `data.access_denied === true`.
 * @param {Array} nodes - canvas nodes (ReactFlow shape)
 * @returns {Array} sottoinsieme di nodes con flag access_denied
 */
export function getAccessDeniedNodes(nodes) {
  if (!Array.isArray(nodes)) return [];
  return nodes.filter(n => n && n.data && n.data.access_denied === true);
}


/**
 * True se esiste almeno un nodo con `data.access_denied=true`.
 * @param {Array} nodes
 * @returns {boolean}
 */
export function hasAccessDeniedNodes(nodes) {
  return getAccessDeniedNodes(nodes).length > 0;
}


/**
 * Descrive i nodi access_denied per UI/log diagnostici. Ritorna null se
 * nessun nodo denied (semantica truthy: gli editor possono usare
 * `if (describeAccessDeniedNodes(...)) { toast; return; }`).
 *
 * @param {Array} nodes
 * @returns {{ count, nodeIds, summary } | null}
 */
export function describeAccessDeniedNodes(nodes) {
  const denied = getAccessDeniedNodes(nodes);
  if (denied.length === 0) return null;
  const labels = denied.map(n => {
    const t = (n.data && n.data.inaccessible_resource_type) || n.type || 'risorsa';
    const reason = (n.data && n.data.access_denied_reason) || `id=${n.id}`;
    return `• ${t}: ${reason}`;
  });
  return {
    count: denied.length,
    nodeIds: denied.map(n => n.id),
    summary: labels.join('\n'),
  };
}
