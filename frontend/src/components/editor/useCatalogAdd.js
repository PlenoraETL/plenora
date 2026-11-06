/**
 * useCatalogAdd — aggiunta di un nodo del catalogo unico (UI-3, P30, U11):
 * menu "⋯", palette, barra laterale, trascinamento e guida del canvas vuoto
 * passano da qui.
 *
 *  - addNode(key, { afterId }): una voce del catalogo (editorNodeCatalog).
 *    Il nodo nasce al centro della vista visibile (mai in una posizione
 *    fissa), entra nello store come un blocco con il suo collegamento (un
 *    solo Ctrl+Z) e resta selezionato: il pannello si apre su di lui. Un
 *    vincolo non rispettato (una sola uscita, prima l'Output) si dice con
 *    lo stesso motivo della voce spenta (availability). Le voci `chain`
 *    aperte dalla palette dopo un nodo entrano nel flusso con
 *    `insertAfter(node, afterId)` dell'editor.
 *  - addSource(kind, item, position?): un progetto o un file sorgente, con
 *    il nome (U9), al centro della vista o dove e' stato rilasciato.
 */
import { useCallback, useEffect, useRef } from 'react';
import useCanvasStore from '../../store/useCanvasStore';
import { useCanvasDerived } from '../../hooks/useCanvasDerived';
import { useToast } from '../../hooks/useToast';
import { NODES, buildCatalogNode, buildSourceNode, isUnresolvedSource, withResolvedSourceNames } from './editorNodeCatalog';

export default function useCatalogAdd(mode, getPosition, { insertAfter = null } = {}) {
  const toast = useToast();
  const ref = useRef({ getPosition, insertAfter });
  ref.current = { getPosition, insertAfter };

  const addNode = useCallback((key, { afterId = null } = {}) => {
    const st = useCanvasStore.getState();
    if (st.isReadOnly) return null;
    const built = buildCatalogNode(key, { mode, nodes: st.nodes, position: ref.current.getPosition() });
    if (built.error) { toast.warning(built.error); return null; }
    if (afterId && ref.current.insertAfter && NODES[key].chain) {
      ref.current.insertAfter(built.node, afterId);
    } else {
      st.addSubgraph({ nodes: [built.node], edges: built.edges, selectId: built.node.id });
    }
    return built.node;
  }, [mode, toast]);

  const addSource = useCallback((kind, item, position = null) => {
    const st = useCanvasStore.getState();
    if (st.isReadOnly || !item) return null;
    const node = buildSourceNode(kind, item, position || ref.current.getPosition());
    st.addSubgraph({ nodes: [node], selectId: node.id });
    return node;
  }, []);

  return { addNode, addSource };
}

/**
 * U9, B-K9: i nodi sorgente prendono il nome attuale dal catalogo delle
 * sorgenti dell'editor (campo transitorio `_sourceName`, applicato come dato
 * DERIVATO: niente modifica, niente salvataggio, niente Ctrl+Z; regola in
 * editorNodeCatalog.withResolvedSourceNames). Riparte quando arriva un
 * elenco nuovo o compare un nodo non ancora risolto; un nodo fuori
 * dall'elenco e' risolto (`null`) e non la riaccende.
 */
const hasUnresolvedSources = (s) => (s.nodes || []).some(isUnresolvedSource);

export function useResolvedSourceNames(applyDerivedNodes, projects, files) {
  const unresolved = useCanvasDerived(hasUnresolvedSources);
  useEffect(() => {
    if (!projects?.length && !files?.length) return;
    applyDerivedNodes(nodes => withResolvedSourceNames(nodes, { projects, files }));
  }, [applyDerivedNodes, projects, files, unresolved]);
}
