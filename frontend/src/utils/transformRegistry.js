/**
 * transformRegistry.js — cache condivisa del registry delle trasformazioni
 * Manipola (GET /transforms/registry).
 *
 * Prima la cache viveva dentro TransformNode. La nuova interfaccia dei
 * canvas (Fase 4) la usa anche per i riassunti dei nodi compatti
 * (nome leggibile dell'operazione), quindi sta qui: una sola richiesta.
 */
import { getTransformRegistry } from '../api/transforms';
import { authBus } from './authBus';

let registryCache = null;
let registryCachePromise = null;

export const fetchTransformRegistry = async () => {
  if (registryCache) return registryCache;
  if (registryCachePromise) return registryCachePromise;

  registryCachePromise = getTransformRegistry()
    .then(data => {
      registryCache = data;
      registryCachePromise = null;
      return data;
    })
    .catch(err => {
      console.error('[transformRegistry] Errore caricamento registry:', err);
      registryCachePromise = null;
      return null;
    });

  return registryCachePromise;
};

/** Svuota la cache: al logout un altro utente non deve vedere il registry precedente. */
export const resetTransformRegistry = () => {
  registryCache = null;
  registryCachePromise = null;
};
authBus.addLogoutListener(resetTransformRegistry);

/** Registry gia' caricato (o null): lettura sincrona per i riassunti. */
export const getCachedTransformRegistry = () => registryCache;
