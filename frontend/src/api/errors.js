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
 *   { error: string, message: string,
 *     dependencies: Array<{kind, id, name, project_type?}>,
 *     hiddenCount: number }
 *   oppure null.
 *
 * PR-δ (2026-04-30): aggiunto `hiddenCount`. Il backend filtra ora le
 * dependencies non visibili al chiamante (cross-tenant leak fix) e popola
 * `detail.hidden_count` con il numero di progetti bloccanti nascosti.
 * Default 0 per backward-compatibility con response shape pre-PR-δ.
 */
export function handleCascade409(err) {
  if (!err || err.response?.status !== 409) return null;
  const detail = err.response?.data?.detail;
  if (!detail || typeof detail !== 'object') return null;
  if (typeof detail.error !== 'string' || !detail.error) return null;
  if (!Array.isArray(detail.dependencies)) return null;
  const hiddenCount = (
    typeof detail.hidden_count === 'number' && detail.hidden_count > 0
      ? detail.hidden_count
      : 0
  );
  return {
    error: detail.error,
    message: typeof detail.message === 'string' ? detail.message : '',
    dependencies: detail.dependencies,
    hiddenCount,
  };
}

/**
 * Formatta un array di dependencies in stringa user-facing, raggruppando
 * per kind. Esempio: "2 eseguibile/i (foo, bar) e 1 progetto/i (baz)".
 * Ritorna stringa vuota se l'input è vuoto o invalido.
 *
 * PR-δ: se passato `hiddenCount > 0`, appende "+ N altri" alla sezione
 * project/i per segnalare progetti bloccanti non visibili al chiamante.
 * Backward compat: chiamate senza secondo argomento si comportano come
 * prima.
 */
export function formatCascadeDependencies(dependencies, hiddenCount = 0) {
  const safeHidden = (typeof hiddenCount === 'number' && hiddenCount > 0)
    ? hiddenCount
    : 0;
  const list = Array.isArray(dependencies) ? dependencies : [];
  if (list.length === 0 && safeHidden === 0) return '';

  const byKind = {};
  for (const d of list) {
    const kind = d?.kind || 'altro';
    (byKind[kind] ||= []).push(d);
  }
  const parts = [];
  if (byKind.eseguibile?.length) {
    const names = byKind.eseguibile.map(e => e.name).filter(Boolean).join(', ');
    parts.push(`${byKind.eseguibile.length} eseguibile/i (${names})`);
  }

  const projects = byKind.project || [];
  if (projects.length || safeHidden > 0) {
    const names = projects.map(p => p.name).filter(Boolean).join(', ');
    const totalProjects = projects.length + safeHidden;
    let segment = `${totalProjects} progetto/i`;
    if (names) segment += ` (${names}`;
    if (safeHidden > 0) {
      segment += names
        ? `, + ${safeHidden} altro/i non visibili)`
        : ` (${safeHidden} non visibili)`;
    } else if (names) {
      segment += ')';
    }
    parts.push(segment);
  }
  return parts.join(' e ');
}
