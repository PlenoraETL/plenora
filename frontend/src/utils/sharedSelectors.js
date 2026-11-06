/**
 * sharedSelectors.js — dalla risposta di una risorsa condivisa
 * (utils/sharedResources) alla lista che serve al componente (UI-1).
 * Funzioni di modulo: stabili tra i render (hooks/useSharedResource `select`).
 */
const list = (value, field) => {
  if (Array.isArray(value)) return value;
  return (value && Array.isArray(value[field])) ? value[field] : [];
};

export const arcgisConnectionsOf = (d) => list(d, 'connections');
export const apiConnectionsOf = (d) => {
  if (Array.isArray(d)) return d;
  return d?.api_connections || d?.connections || d?.items || [];
};
export const filesOf = (d) => list(d, 'files');
export const datasourcesOf = (d) => list(d, 'datasources');
export const eseguibiliOf = (d) => (Array.isArray(d) ? d : (d?.items || []));
export const connectionSetsOf = (d) => list(d, 'connection_sets');
