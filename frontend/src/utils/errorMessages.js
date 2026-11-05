/**
 * errorMessages.js — L'UNICA normalizzazione degli errori verso l'utente.
 *
 * Regola (T3, una sola per tutto il frontend):
 *   1. messaggio del backend, se c'e' (envelope `message`, `detail` stringa,
 *      `detail.message`, oppure l'elenco dei campi di un 422 Pydantic); un
 *      testo che e' un CODICE (snake_case senza spazi: 'mfa_invalid') non e'
 *      un messaggio per l'utente e non si mostra mai (looksLikeCode);
 *   2. altrimenti il testo curato del codice di dominio (`{error: "<code>"}`,
 *      vedi `app/shared/error_codes.py`);
 *   3. altrimenti un testo italiano per famiglia: rete, timeout, 401, 403,
 *      404, 409, 410, 413, 422, 429, 5xx, altri 4xx;
 *   4. un errore locale lanciato dall'app (`new Error('...')`) mostra il suo
 *      testo; un errore di programma (TypeError, ReferenceError, ...) mai.
 *
 * Mai il testo grezzo di axios ("Network Error", "timeout of 30000ms
 * exceeded", "Request failed with status code 500").
 *
 * L'interceptor (`api/http.js`) scrive questo stesso testo in `err.message`;
 * le pagine pero' usano sempre `requestErrorMessage(err)` / `humanizeError(err)`
 * / `toastApiError(...)`: lo vieta il test
 * `tests/shared/errorMessages.contract.test.js` (niente `err.message` grezzo
 * in toast, banner e setError).
 */

// Mappa codice backend → { title, message }. Copre i codici con valore di
// branching reale per l'utente; i restanti ricadono sul generico per status.
export const ERROR_MESSAGES = {
  task_store_unavailable: { title: 'Stato non disponibile', message: 'Lo stato delle operazioni non e temporaneamente disponibile. Riprova tra poco.' },
  // Generici / framework
  internal_error: { title: 'Errore del server', message: 'Si è verificato un errore interno. Riprova; se il problema persiste, contatta l’amministratore.' },
  canvas_corrupted: { title: 'Canvas corrotto', message: 'Il canvas del progetto non è leggibile e potrebbe essere danneggiato. Contatta l’amministratore.' },
  validation_error: { title: 'Dati non validi', message: 'Alcuni dati non sono validi. Controlla i campi e riprova.' },

  // Servizi esterni / risorse
  external_service_unreachable: { title: 'Servizio esterno non raggiungibile', message: 'Un servizio esterno (es. ArcGIS o un’API) non risponde. Verifica la connessione e riprova; se persiste, contatta l’amministratore.' },
  memory_limit_exceeded: { title: 'File troppo grande', message: 'Il file o il dataset supera il limite di memoria per l’elaborazione. Prova con un file più piccolo o contatta l’amministratore.' },
  service_not_initialized: { title: 'Sistema in avvio', message: 'Il sistema si sta avviando. Riprova tra qualche secondo; se persiste, contatta l’amministratore.' },
  access_denied: { title: 'Accesso negato', message: 'Non hai i permessi per accedere a questa risorsa.' },

  // Not found
  project_not_found: { title: 'Progetto non trovato', message: 'Il progetto non esiste o è stato rimosso.' },
  task_not_found: { title: 'Operazione non trovata', message: 'L’operazione richiesta non esiste più (potrebbe essere già terminata).' },
  schedule_not_found: { title: 'Schedule non trovato', message: 'La pianificazione non esiste o è stata rimossa.' },
  run_not_found: { title: 'Esecuzione non trovata', message: 'L’esecuzione richiesta non esiste più.' },
  bundle_not_found: { title: 'Bundle non trovato', message: 'Il bundle IMDF non è disponibile o è scaduto. Rigenera l’output.' },
  api_connection_not_found: { title: 'Connessione API non trovata', message: 'La connessione API non esiste o è stata rimossa.' },
  file_source_not_found: { title: 'File non trovato', message: 'Il file sorgente non esiste o è stato rimosso.' },
  connection_not_found: { title: 'Connessione non trovata', message: 'La connessione non esiste o è stata rimossa.' },
  datasource_not_found: { title: 'Datasource non trovato', message: 'Il datasource non esiste o è stato rimosso.' },
  user_not_found: { title: 'Utente non trovato', message: 'L’utente richiesto non esiste.' },
  group_not_found: { title: 'Gruppo non trovato', message: 'Il gruppo richiesto non esiste.' },
  ticket_not_found: { title: 'Ticket non trovato', message: 'Il ticket richiesto non esiste.' },

  // Conflitti / in uso
  project_has_dependencies: { title: 'Progetto collegato', message: 'Il progetto ha elementi collegati (eseguibili o altri progetti). Rimuovili prima di eliminarlo.' },
  project_cleanup_partial: { title: 'Eliminazione parziale', message: 'L’eliminazione è riuscita solo in parte: il progetto è ancora presente. Riprova o contatta l’amministratore.' },
  api_connection_in_use: { title: 'Connessione in uso', message: 'Non puoi eliminare questa connessione API: è ancora usata da uno o più progetti.' },
  connection_in_use: { title: 'Connessione in uso', message: 'Non puoi eliminare questa connessione: è ancora usata da uno o più progetti.' },
  datasource_in_use: { title: 'Datasource in uso', message: 'Non puoi eliminare questo datasource: è ancora usato da uno o più progetti.' },
  connection_duplicate_name: { title: 'Nome già esistente', message: 'Esiste già una connessione con questo nome. Scegline un altro.' },
  username_already_exists: { title: 'Username già in uso', message: 'Questo username è già in uso. Scegline un altro.' },
  group_id_already_exists: { title: 'Gruppo già esistente', message: 'Questo identificativo di gruppo è già in uso.' },
  group_default_not_deletable: { title: 'Operazione non consentita', message: 'Il gruppo “default” non può essere eliminato.' },

  // Carica (scrittura output)
  carica_arcgis_write_failed: { title: 'Scrittura ArcGIS fallita', message: 'La pubblicazione su ArcGIS non è riuscita. Verifica la connessione e i permessi del servizio.' },
  carica_datasource_write_failed: { title: 'Scrittura fallita', message: 'La scrittura sul datasource (DB/FTP/SharePoint) non è riuscita. Verifica la connessione.' },
  carica_rest_write_failed: { title: 'Scrittura REST fallita', message: 'La scrittura sull’endpoint REST non è riuscita. Verifica l’endpoint e i permessi.' },
  carica_launch_failed: { title: 'Avvio fallito', message: 'L’avvio del caricamento non è riuscito. Riprova; se persiste, contatta l’amministratore.' },
  carica_scheduler_failed: { title: 'Avvio pianificato fallito', message: 'L’avvio del caricamento pianificato non è riuscito. Controlla i log.' },
  schedule_execution_fatal: { title: 'Esecuzione pianificata fallita', message: 'L’esecuzione pianificata è fallita. Controlla i log del progetto.' },
  datasource_not_visible: { title: 'Datasource non visibile', message: 'Il datasource non è visibile al tuo gruppo.' },
  datasource_visibility_check_failed: { title: 'Verifica fallita', message: 'Non è stato possibile verificare la visibilità del datasource. Riprova.' },

  arcgis_connection_not_visible: { title: 'Connessione ArcGIS non visibile', message: 'La connessione ArcGIS non e disponibile per il gruppo del progetto.' },
  carica_target_locked: { title: 'Destinazione occupata', message: 'Un altro caricamento sta scrivendo sulla stessa destinazione. Attendi e riprova.' },
  carica_target_uncertain: { title: 'Verifica necessaria', message: 'Una scrittura precedente ha esito incerto. Verifica la destinazione prima di rilanciare.' },
  carica_target_lock_lost: { title: 'Scrittura da verificare', message: 'Il controllo esclusivo sulla destinazione e stato perso durante la scrittura. Verifica i dati prima di rilanciare.' },
  carica_completion_uncertain: { title: 'Conferma incompleta', message: 'La scrittura potrebbe essere riuscita, ma il sistema non ha salvato la conferma. Verifica la destinazione.' },
  carica_external_write_uncertain: { title: 'Esito non determinabile', message: 'La destinazione esterna potrebbe aver ricevuto i dati. Verificala prima di ripetere il caricamento.' },
  carica_uncertain_resolution_conflict: { title: 'Run non risolvibile', message: 'La run non e piu in uno stato incerto risolvibile. Aggiorna la pagina.' },
  carica_task_fatal: { title: 'Caricamento fallito', message: 'Il caricamento non e riuscito. Consulta i log o contatta l’amministratore.' },
  carica_unsafe_write_config: { title: 'Configurazione non sicura', message: 'La configurazione di scrittura e stata bloccata. Controlla destinazione e modalita di caricamento.' },

  // Auth
  auth_invalid_credentials: { title: 'Credenziali errate', message: 'Username o password non corretti.' },
  auth_mfa_invalid: { title: 'Codice MFA non valido', message: 'Il codice di verifica (MFA) non è corretto.' },
  auth_mfa_code_invalid: { title: 'Codice MFA non valido', message: 'Il codice di verifica (MFA) non è corretto.' },
  auth_mfa_required: { title: 'MFA richiesto', message: 'È richiesto il codice di verifica (MFA) per accedere.' },
  auth_account_disabled: { title: 'Account disabilitato', message: 'Il tuo account è disabilitato. Contatta l’amministratore.' },
  auth_rate_limit_exceeded: { title: 'Troppi tentativi', message: 'Troppi tentativi di accesso. Attendi qualche minuto e riprova.' },
  auth_token_missing: { title: 'Sessione scaduta', message: 'Sessione non valida. Effettua di nuovo l’accesso.' },
  auth_token_invalid: { title: 'Sessione scaduta', message: 'Sessione non valida o scaduta. Effettua di nuovo l’accesso.' },
  auth_token_revoked: { title: 'Sessione terminata', message: 'La sessione è stata chiusa. Effettua di nuovo l’accesso.' },
  auth_user_not_found_or_disabled: { title: 'Sessione non valida', message: 'Sessione non valida. Effettua di nuovo l’accesso.' },
  auth_role_insufficient_for_write: { title: 'Permessi insufficienti', message: 'Il tuo ruolo non consente questa operazione di modifica.' },
  auth_role_kiosk_only: { title: 'Permessi insufficienti', message: 'Il tuo ruolo consente solo l’esecuzione degli eseguibili.' },
  auth_role_admin_required: { title: 'Permessi insufficienti', message: 'Questa operazione richiede il ruolo di amministratore.' },
  auth_role_superadmin_required: { title: 'Permessi insufficienti', message: 'Questa operazione richiede il ruolo di superadmin.' },
  auth_role_create_privilege_required: { title: 'Permessi insufficienti', message: 'Solo un superadmin può creare utenti con questo ruolo.' },
  auth_field_role_restricted: { title: 'Campo riservato', message: 'Non hai i permessi per modificare questo campo.' },
  auth_owner_only: { title: 'Permessi insufficienti', message: 'Solo il proprietario della risorsa (o un amministratore) può modificarla.' },
  auth_hierarchical_owner_required: { title: 'Permessi insufficienti', message: 'Questa risorsa è stata creata da un superadmin: solo un superadmin può modificarla.' },
  auth_self_modify_denied: { title: 'Operazione non consentita', message: 'Non puoi modificare il ruolo del tuo stesso account.' },
  auth_self_disable_denied: { title: 'Operazione non consentita', message: 'Non puoi disabilitare il tuo stesso account.' },
  auth_self_delete_denied: { title: 'Operazione non consentita', message: 'Non puoi eliminare il tuo stesso account.' },
  auth_revocation_unavailable: { title: 'Logout parziale', message: 'Il servizio di logout non è momentaneamente disponibile, ma la sessione locale è stata chiusa.' },

  // Backup / object storage
  object_storage_disabled: { title: 'Storage non abilitato', message: 'Lo storage di backup (object storage) non è abilitato sul server.' },
  aws_crt_missing: { title: 'Componente mancante', message: 'Un componente AWS richiesto manca sul server. Contatta l’amministratore.' },
  aws_credentials_expired: { title: 'Credenziali scadute', message: 'Le credenziali AWS sono scadute. Contatta l’amministratore.' },
};


const GENERIC = { title: 'Errore', message: 'Si è verificato un errore. Riprova; se persiste, contatta l’amministratore.' };
const NETWORK = { title: 'Connessione assente', message: 'Impossibile contattare il server. Controlla la connessione di rete e riprova.' };
const TIMEOUT = { title: 'Timeout', message: 'Il server non ha risposto in tempo. Riprova tra poco.' };
const CANCELED = { title: 'Richiesta annullata', message: 'La richiesta è stata annullata.' };

// Testo italiano per famiglia HTTP — quando il backend non da' un messaggio e
// il codice non e' mappato.
export function statusFamilyMessage(status) {
  if (status == null) return null;
  if (status === 401) return { title: 'Sessione scaduta', message: 'Devi effettuare di nuovo l’accesso.' };
  if (status === 403) return { title: 'Accesso negato', message: 'Non hai i permessi per questa operazione.' };
  if (status === 404) return { title: 'Non trovato', message: 'La risorsa richiesta non esiste o è stata rimossa.' };
  if (status === 409) return { title: 'Conflitto', message: 'L’operazione è in conflitto con lo stato attuale dei dati.' };
  // Fase 5 T2 (R9.14): un risultato scaduto o gia' scaricato (consume-once).
  if (status === 410) return { title: 'Non più disponibile', message: 'Il file non è più disponibile: è scaduto o è già stato scaricato. Esegui di nuovo per generarlo.' };
  if (status === 413) return ERROR_MESSAGES.memory_limit_exceeded;
  if (status === 422) return { title: 'Dati non validi', message: 'Alcuni campi non sono validi. Controlla e riprova.' };
  if (status === 429) return { title: 'Troppi tentativi', message: 'Hai effettuato troppe richieste. Attendi qualche istante e riprova.' };
  if (status >= 500) return { title: 'Errore del server', message: 'Si è verificato un errore interno. Riprova; se persiste, contatta l’amministratore.' };
  if (status >= 400) return { title: 'Operazione non riuscita', message: 'La richiesta non è valida. Controlla i dati e riprova.' };
  return null;
}

function titleForStatus(status) {
  if (status >= 500) return 'Errore del server';
  if (status === 401) return 'Sessione scaduta';
  if (status === 403) return 'Accesso negato';
  return 'Operazione non riuscita';
}

// Estrae il codice backend strutturato, escludendo i codici interni di axios
// (ERR_NETWORK, ECONNABORTED, …) che non sono codici di dominio.
export function getErrorCode(err) {
  if (!err || typeof err !== 'object') return null;
  const data = err.response?.data;
  const c = err.code || data?.error || data?.detail?.error || data?.details?.error;
  if (typeof c === 'string' && c && !/^(ERR_|ECONN|ETIMEDOUT|EAI_)/.test(c)) return c;
  return null;
}

// Elenco dei campi di un 422 Pydantic (`detail: [{loc, msg, type}, ...]`).
function formatValidationList(list) {
  const parts = list
    .map((d) => {
      if (typeof d === 'string') return d;
      if (!d || typeof d !== 'object') return null;
      const loc = Array.isArray(d.loc) ? d.loc.join('.') : d.loc;
      const msg = typeof d.msg === 'string' && d.msg ? d.msg : 'valore non valido';
      return loc ? `${loc}: ${msg}` : msg;
    })
    .filter(Boolean);
  return parts.length ? parts.join('; ') : null;
}

// Un testo del backend che e' un CODICE (snake_case senza spazi, es.
// 'mfa_invalid', 'auth_mfa_required') non e' un messaggio per l'utente: si
// scarta e decide il codice curato o la famiglia HTTP.
export const looksLikeCode = (v) => typeof v === 'string' && /^[a-z][a-z0-9]*(_[a-z0-9]+)+$/.test(v.trim());
const nonEmpty = (v) => (typeof v === 'string' && v.trim() && !looksLikeCode(v) ? v : null);

/**
 * Messaggio fornito dal backend nel corpo dell'errore, o null.
 * Precedenza: envelope `message` → `detail` stringa → elenco 422 →
 * `detail.message` → `details.message`.
 */
export function backendMessage(data) {
  if (!data || typeof data !== 'object') return null;
  const detail = data.detail;
  return nonEmpty(data.message)
    || nonEmpty(detail)
    || (Array.isArray(detail) ? formatValidationList(detail) : null)
    || (detail && typeof detail === 'object' ? nonEmpty(detail.message) : null)
    || (data.details && typeof data.details === 'object' ? nonEmpty(data.details.message) : null);
}

const PROGRAM_ERRORS = new Set(['TypeError', 'ReferenceError', 'SyntaxError', 'RangeError', 'URIError', 'EvalError']);

/**
 * Traduce un errore in { title, message } sempre umano (regola in testa al file).
 * @param {unknown} err - AxiosError, Error JS, stringa, o oggetto risultato.
 * @returns {{title: string, message: string}}
 */
export function humanizeError(err) {
  if (err == null) return GENERIC;
  if (typeof err === 'string') return err.trim() ? { title: 'Errore', message: err } : GENERIC;
  if (typeof err !== 'object') return GENERIC;

  const resp = err.response;
  const status = resp?.status ?? (typeof err.status === 'number' ? err.status : null);
  const code = getErrorCode(err);
  const curated = code ? ERROR_MESSAGES[code] : null;

  // 1) Messaggio del backend.
  const fromBackend = backendMessage(resp?.data);
  if (fromBackend) {
    return { title: curated?.title || titleForStatus(status), message: fromBackend };
  }
  // 2) Codice di dominio mappato.
  if (curated) return curated;

  // 3) Nessuna risposta HTTP: rete, timeout, annullata, oppure errore locale.
  if (!resp && status == null) {
    const axiosCode = err.code;
    if (axiosCode === 'ECONNABORTED' || axiosCode === 'ETIMEDOUT') return TIMEOUT;
    if (axiosCode === 'ERR_CANCELED' || err.name === 'CanceledError') return CANCELED;
    if (axiosCode === 'ERR_NETWORK' || err.isAxiosError) return NETWORK;
    if (PROGRAM_ERRORS.has(err.name)) return GENERIC;
    const local = nonEmpty(err.message);
    return local ? { title: 'Errore', message: local } : GENERIC;
  }

  // 4) Famiglia HTTP.
  return statusFamilyMessage(status) || GENERIC;
}

/**
 * Il testo da mostrare per un errore (toast, banner, campo di errore).
 * E' `humanizeError(err).message`: una sola regola per tutte le pagine.
 * `fallback` (facoltativo) sostituisce solo il testo generico finale, quando
 * l'errore non dice nulla di piu' (per esempio "cURL non valido").
 */
export function requestErrorMessage(err, fallback) {
  const h = humanizeError(err);
  return h === GENERIC && fallback ? fallback : h.message;
}

/** Il testo come frase chiusa (per comporlo con altre frasi). */
export function asSentence(text) {
  const t = String(text ?? '').trim();
  return !t || /[.!?…]$/.test(t) ? t : `${t}.`;
}

/** Stesso testo di requestErrorMessage (nome storico usato dalle pagine admin). */
export const humanizeErrorMessage = requestErrorMessage;

export default humanizeError;
