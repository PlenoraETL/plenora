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
import { displayIcon } from './displayIcon';
import { applyTransformTerms } from './glossary';

let registryCache = null;
let registryCachePromise = null;

// Una sola normalizzazione per tutti i consumatori: icone sempre glifi
// (utils/displayIcon) e nomi del glossario (utils/glossary, regola 10 del
// Blocco A) al posto dei termini inglesi del catalogo.
export function normalizeRegistry(data) {
  if (!data?.transforms) return data;
  const transforms = Object.fromEntries(Object.entries(data.transforms)
    .map(([op, def]) => [op, def && typeof def === 'object'
      ? applyTransformTerms(op, { ...def, icon: displayIcon(def.icon) })
      : def]));
  return { ...data, transforms };
}

export const fetchTransformRegistry = async () => {
  if (registryCache) return registryCache;
  if (registryCachePromise) return registryCachePromise;

  registryCachePromise = getTransformRegistry()
    .then(data => {
      registryCache = normalizeRegistry(data);
      registryCachePromise = null;
      return registryCache;
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

// Operazioni a due input (`inputs: "multi"` nel registry). Il registry e' la
// fonte; questa lista vale solo finche' non e' caricato, e un test la tiene
// identica al registry del backend (tests/shared/canvasPaletteLayout).
export const KNOWN_MULTI_INPUT_OPS = [
  'concat', 'cross_join', 'geo_clip', 'geo_count_points_in_polygons', 'geo_difference', 'geo_distance',
  'geo_intersection', 'geo_nearest', 'geo_overlay', 'geo_symmetric_difference', 'geo_union', 'geo_within',
  'join', 'sjoin', 'table_diff',
];

/** True se l'operazione ha due ingressi (input-0 / input-1). */
export const isMultiInputOp = (op, registry = registryCache) => {
  const def = registry?.transforms?.[op];
  if (def) return def.inputs === 'multi';
  return KNOWN_MULTI_INPUT_OPS.includes(op);
};
