/**
 * useMountedRef — hook utility per evitare setState post-unmount.
 *
 * Pattern condiviso tra gli hook async (useSchemaInference,
 * useEnrichmentValidation, useProjectListData, ecc.): ogni callback
 * `async` che fa setState dopo `await` deve verificare che il
 * componente sia ancora montato per evitare leak di reference + warning
 * React. Estrarre il pattern in un singolo hook elimina ~30 righe di
 * boilerplate duplicato e blinda l'invariante.
 *
 * Uso:
 * ```jsx
 * const mountedRef = useMountedRef();
 *
 * const fetchData = useCallback(async () => {
 *   const data = await api.get(...);
 *   if (!mountedRef.current) return;  // unmounted while in-flight
 *   setState(data);
 * }, [mountedRef]);
 * ```
 *
 * `mountedRef.current` parte da `true` (sincrono al mount), passa a
 * `false` nel cleanup dell'effect. Inserire `mountedRef` nelle deps di
 * useCallback e' sicuro: la ref e' stabile per la vita del componente.
 */
import { useEffect, useRef } from 'react';

export function useMountedRef() {
  const ref = useRef(true);
  useEffect(() => {
    // Al mount/remount React 18 strict-mode il cleanup viene chiamato
    // due volte; il `ref.current = true` qui ripristina lo stato dopo
    // l'unmount fittizio dello strict-mode.
    ref.current = true;
    return () => {
      ref.current = false;
    };
  }, []);
  return ref;
}

export default useMountedRef;
