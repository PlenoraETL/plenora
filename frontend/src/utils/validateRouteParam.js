/**
 * utils/validateRouteParam.js — Validazione parametri di route
 *
 * Protegge i componenti da input malevoli nell'URL prima che vengano
 * passati alle chiamate API.
 *
 * Scenari bloccati:
 *   - Path traversal:  /project/../../../etc/passwd
 *   - Injection chars: /project/<script>alert(1)</script>
 *   - ID vuoto/null:   /project/  o  /project/undefined
 *   - ID troppo lungo: impossibile per UUID/intero legittimo
 *
 * L'ID progetto nel backend è un intero positivo (PostgreSQL SERIAL).
 * Gli UUID sono accettati per compatibilità con eventuali migrazioni future.
 *
 * Uso nei componenti:
 *   const { projectId } = useParams();
 *   if (!isValidProjectId(projectId)) {
 *     navigate('/', { replace: true });
 *     return null;
 *   }
 */

// Intero positivo (1–2147483647)
const RE_INTEGER = /^\d{1,10}$/;

// UUID v4 standard
const RE_UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

// Slug alfanumerico con trattini e underscore (formato usato dal backend URBY)
// Esempio: test_nuovo_progetto_20260226_053153_5f7429f3
const RE_SLUG = /^[a-z0-9][a-z0-9_-]{0,126}[a-z0-9]$/i;

/**
 * Verifica che un parametro di route sia un ID progetto valido.
 * Accetta: intero positivo, UUID v4, slug alfanumerico.
 * Rifiuta: undefined, null, stringa vuota, path traversal, HTML injection.
 *
 * @param {string|undefined} id
 * @returns {boolean}
 */
export function isValidProjectId(id) {
  if (!id || typeof id !== 'string') return false;

  // Blocca path traversal e caratteri non ammessi
  if (id.includes('..') || id.includes('/') || id.includes('\\')) return false;
  if (id.includes('<') || id.includes('>') || id.includes('"')) return false;
  if (id.includes('%') || id.includes('&') || id.includes(';'))  return false;

  // Lunghezza massima difensiva
  if (id.length > 64) return false;

  return RE_INTEGER.test(id) || RE_UUID.test(id) || RE_SLUG.test(id);
}
