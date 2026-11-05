/**
 * useTransformRegistry — il catalogo delle operazioni di Manipola per i
 * componenti (Fase 5 T2, audit R4.20, N13).
 *
 * Sopra hooks/useResource e la cache condivisa di utils/transformRegistry:
 * se il catalogo non arriva lo stato e' `error` con «Riprova» (mai
 * "Caricamento operazioni…" per sempre); quando un componente lo riprova e
 * arriva, lo ricevono tutti quelli montati.
 */
import { useEffect } from 'react';
import { fetchTransformRegistry, getCachedTransformRegistry } from '../utils/transformRegistry';
import { useResource } from './useResource';

const listeners = new Set();

export const REGISTRY_UNAVAILABLE = 'Il catalogo delle operazioni non è stato caricato.';

async function loadRegistry() {
  const registry = await fetchTransformRegistry();
  if (!registry) throw new Error(REGISTRY_UNAVAILABLE);
  listeners.forEach(fn => fn(registry));
  return registry;
}

export function useTransformRegistry({ enabled = true } = {}) {
  const resource = useResource(loadRegistry, {
    key: 'transform-registry',
    enabled,
    initialData: () => getCachedTransformRegistry() || undefined,
  });
  const { setData } = resource;
  useEffect(() => {
    listeners.add(setData);
    return () => { listeners.delete(setData); };
  }, [setData]);
  return resource;
}

export default useTransformRegistry;
