/**
 * restSecrets.js — i segreti della destinazione REST non stanno nel canvas
 * (Fase 5 T1, audit R10.8; correzione dopo la conferma T1+T3, punto 2).
 *
 * Regola: le credenziali di una scrittura REST si danno con un RIFERIMENTO a
 * un connettore API (`rest_auth_api_id`), che il server conserva cifrato e
 * risolve al momento della scrittura (backend carica/rest_auth: stessa
 * origine dell'URL del connettore, utente che puo' usarlo).
 *
 * NIENTE SI TOGLIE IN SILENZIO. Un canvas salvato prima del riferimento puo'
 * avere intestazioni segrete: al caricamento restano nel canvas in memoria e
 * il nodo e' "da migrare" (restMigrationIssues):
 *  - il nodo lo dice (RestDestinationSection) e cosi' la finestra «Prima di
 *    avviare» (BeforeRunModal, motivo 'migrate');
 *  - salvataggio ed esecuzione sono bloccati con un messaggio chiaro
 *    (RestSecretsMigrationError da useCanvasStore.saveCanvasSerialized, il
 *    punto unico di scrittura del canvas; PipelineRunner prima di avviare);
 *  - si sblocca quando l'utente sceglie un connettore in «Autenticazione»
 *    (le righe segrete sono sostituite dal connettore, e il nodo lo dice) o
 *    rimuove le righe in modo esplicito.
 * La copia locale (useLocalDraft) non porta i VALORI segreti: tiene il nome
 * della riga con valore vuoto e `redacted: true`, cosi' il nodo ripristinato
 * resta "da migrare" invece di perdere la riga.
 * Il backend, finche' il canvas salvato non e' migrato, scrive come prima
 * con le intestazioni del canvas (nessuna esecuzione non presidiata cambia).
 *
 * Nomi di credenziale (regola larga, nel dubbio e' una credenziale): le
 * intestazioni marcate `secret: true`, l'elenco dichiarato
 * SECRET_HEADER_NAMES e ogni nome che contiene una parola di credenziale
 * (CREDENTIAL_WORDS: key, token, secret, password, auth, cookie, signature,
 * credential...). Fanno eccezione SOLO i nomi dichiarati in
 * NON_SECRET_HEADER_NAMES (identificativi di richiesta/sessione e intestazioni
 * di servizio), con il motivo: un nome nuovo sconosciuto con una parola di
 * credenziale blocca il salvataggio invece di finire in chiaro nel canvas.
 */
export const REST_AUTH_FIELD = 'rest_auth_api_id';

export const SECRET_HEADER_NAMES = Object.freeze([
  'authorization',
  'proxy-authorization',
  'x-api-key',
  'api-key',
  'cookie',
  'set-cookie',
  'x-auth-token',
]);
// Parole che rendono un nome una credenziale (apikey, api_key, X-Goog-Api-Key,
// Ocp-Apim-Subscription-Key, X-Api-Secret, X-Client-Secret, X-Access-Key,
// X-Password, X-Functions-Key, Token, X-Amz-Security-Token, X-Hub-Signature...).
const CREDENTIAL_WORDS = /(key|token|secret|passw|pwd|auth|cookie|credential|signature|bearer|jwt|session)/i;

// Nomi con una parola di credenziale che NON portano credenziali: elenco
// dichiarato, ognuno con il motivo.
export const NON_SECRET_HEADER_NAMES = Object.freeze({
  'x-session-id': 'identificativo di correlazione della sessione del client, non un segreto',
  'x-request-id': 'identificativo della richiesta',
  'idempotency-key': 'chiave di idempotenza generata per la richiesta, non una credenziale',
  'x-idempotency-key': 'chiave di idempotenza generata per la richiesta, non una credenziale',
});

/** True se l'intestazione porta (o puo' portare) una credenziale. */
export function isSecretHeader(header) {
  if (!header || typeof header !== 'object') return false;
  if (header.secret === true) return true;
  const name = String(header.name || '').trim().toLowerCase();
  if (!name) return false;
  if (SECRET_HEADER_NAMES.includes(name)) return true;
  if (Object.prototype.hasOwnProperty.call(NON_SECRET_HEADER_NAMES, name)) return false;
  return CREDENTIAL_WORDS.test(name);
}

/** Nomi delle intestazioni segrete ancora nel nodo (vuoto = niente da migrare). */
export function restSecretHeaderNames(data) {
  const headers = Array.isArray(data?.rest_headers) ? data.rest_headers : [];
  return headers.filter(isSecretHeader).map((header) => String(header.name || '').trim() || '(senza nome)');
}

/** Nodi "da migrare": [{ nodeId, headers }] per ogni nodo con intestazioni segrete. */
export function restMigrationIssues(nodes) {
  return (nodes || [])
    .map((node) => ({ nodeId: node?.id, headers: restSecretHeaderNames(node?.data) }))
    .filter((item) => item.nodeId && item.headers.length > 0);
}

/** Testo unico del blocco (salvataggio, esecuzione, nodo). */
export function restMigrationMessage(issues) {
  const names = [...new Set((issues || []).flatMap((item) => item.headers))].join(', ');
  return `Credenziali REST nel canvas da migrare (${names}): nel nodo Carica scegli un connettore API in `
    + '«Autenticazione» (destinazione REST), che le sostituisce, oppure rimuovi le righe '
    + '(anche con «Rimuovi credenziali» se la destinazione è il database). '
    + 'Finché restano, il progetto non si salva e non si esegue.';
}

/** Intestazioni senza le righe segrete (rimozione esplicita dell'utente). */
export function withoutSecretHeaders(headers) {
  return Array.isArray(headers) ? headers.filter((header) => !isSecretHeader(header)) : headers;
}

/** Errore del salvataggio bloccato da un nodo da migrare. */
class RestSecretsMigrationError extends Error {
  constructor(issues) {
    super(restMigrationMessage(issues));
    this.name = 'RestSecretsMigrationError';
    this.issues = issues;
  }
}

/** Lancia RestSecretsMigrationError se un nodo e' da migrare. */
export function assertNoRestSecrets(nodes) {
  const issues = restMigrationIssues(nodes);
  if (issues.length) throw new RestSecretsMigrationError(issues);
}

/** Intestazioni per la copia locale: le righe segrete restano, senza valore. */
export function redactRestHeaders(headers) {
  return Array.isArray(headers)
    ? headers.map((header) => (isSecretHeader(header) ? { ...header, value: '', redacted: true } : header))
    : headers;
}

/** Scelta di un connettore: le righe segrete sono sostituite; ritorna { headers, replaced }. */
export function replaceSecretsWithConnector(headers) {
  const list = Array.isArray(headers) ? headers : [];
  return {
    headers: list.filter((header) => !isSecretHeader(header)),
    replaced: list.filter(isSecretHeader).map((header) => String(header.name || '').trim()),
  };
}
