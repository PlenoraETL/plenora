/**
 * errors.js — Hub di normalizzazione per gli errori API.
 *
 * Contiene helper per parsare in modo uniforme le response di errore del
 * backend, a partire dal 409 cascade-delete. Prima del Batch Fix 6.1
 * (error-handling-governance, Phase 2 Hardening), 4 endpoint DELETE
 * emettevano 3 sub-shape diverse di `detail`:
 *   - projects             → {error, message, eseguibili, dependent_projects}
 *   - connections          → {error, message, projects}
 *   - datasources          → {error, message, projects}
 *   - api-connections      → {message, in_use_by}   (senza `error`)
 * Impossibile scrivere un handler unico. Post 6.1 la shape è canonica:
 *   { error, message, dependencies:[{kind, id, name, project_type?}] }
 * Questo modulo è l'unico posto che la interpreta lato FE.
 */

/**
 * Riconosce un AxiosError 409 cascade-delete e ritorna i campi normalizzati,
 * o null se non è un 409 cascade riconosciuto.
 *
 * Ritorna:
 *   { error: string, message: string, dependencies: Array<{kind, id, name, project_type?}> }
 *   oppure null.
 */
export function handleCascade409(err) {
  if (!err || err.response?.status !== 409) return null;
  const detail = err.response?.data?.detail;
  if (!detail || typeof detail !== 'object') return null;
  if (typeof detail.error !== 'string' || !detail.error) return null;
  if (!Array.isArray(detail.dependencies)) return null;
  return {
    error: detail.error,
    message: typeof detail.message === 'string' ? detail.message : '',
    dependencies: detail.dependencies,
  };
}

/**
 * Formatta un array di dependencies in stringa user-facing, raggruppando
 * per kind. Esempio: "2 eseguibile/i (foo, bar) e 1 progetto/i (baz)".
 * Ritorna stringa vuota se l'input è vuoto o invalido.
 */
export function formatCascadeDependencies(dependencies) {
  if (!Array.isArray(dependencies) || dependencies.length === 0) return '';
  const byKind = {};
  for (const d of dependencies) {
    const kind = d?.kind || 'altro';
    (byKind[kind] ||= []).push(d);
  }
  const parts = [];
  if (byKind.eseguibile?.length) {
    const names = byKind.eseguibile.map(e => e.name).filter(Boolean).join(', ');
    parts.push(`${byKind.eseguibile.length} eseguibile/i (${names})`);
  }
  if (byKind.project?.length) {
    const names = byKind.project.map(p => p.name).filter(Boolean).join(', ');
    parts.push(`${byKind.project.length} progetto/i (${names})`);
  }
  return parts.join(' e ');
}
