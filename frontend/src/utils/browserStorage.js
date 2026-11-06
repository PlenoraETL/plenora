/**
 * browserStorage — UNICO modulo che conosce le chiavi di localStorage e
 * sessionStorage dell'app (conferma finale G8, C-K11, B-K10; 06/11/2026).
 *
 * Prima le chiavi avevano cinque forme (`plenora-theme`, `plenora-layout:…`,
 * `plenora.palette.…`, `plenora:draft:…`, `plenora:chunk-reload-at`) decise
 * in cinque file. Ora:
 *  - un prefisso unico, `plenora:`; ogni chiave e' una riga di STORAGE_KEYS
 *    (chiave fissa o prefisso), con l'area (local / session), di chi e'
 *    (`browser` = preferenza del dispositivo; `user` = porta lo user_id della
 *    sessione nella chiave, chi entra dopo non la vede), cosa succede
 *    all'uscita dall'account (`logout`: keep / clear) e il motivo;
 *  - all'uscita (`resetSession`) si tolgono le righe `clear` (dati personali
 *    di memoria d'uso), di TUTTI gli utenti del browser;
 *  - le chiavi vecchie (`legacy`) si spostano sulla chiave nuova alla prima
 *    lettura (migrazione una volta, poi si tolgono); quelle di proprietario
 *    sconosciuto (la palette per browser di prima di B-K10) si tolgono
 *    senza darle a nessuno (`legacyDrop`): passarle al primo che entra
 *    rifarebbe il difetto di B-K10.
 * Ogni accesso e' protetto (finestra privata, dati del sito bloccati): senza
 * storage le funzioni ritornano il valore di ripiego e non lanciano.
 * Il test tests/shared/browserStorage.contract.test.js vieta letture e
 * scritture di localStorage/sessionStorage fuori da qui (salvo il token di
 * sessione di api/http e AuthContext, dichiarati) e chiavi fuori tabella.
 */
import { registerSessionReset } from './session';

const STORAGE_PREFIX = 'plenora:';

export const STORAGE_KEYS = Object.freeze({
  theme: Object.freeze({
    key: 'plenora:theme', area: 'local', owner: 'browser', logout: 'keep', legacy: ['plenora-theme'],
    why: 'tema chiaro/scuro: preferenza del dispositivo (letta anche da public/theme-init.js prima del render)',
  }),
  layout: Object.freeze({
    prefix: 'plenora:layout:', area: 'local', owner: 'browser', logout: 'keep', legacyPrefix: 'plenora-layout:',
    why: 'barre chiuse e larghezza del pannello: preferenza del dispositivo (hooks/useStoredState)',
  }),
  palette: Object.freeze({
    prefix: 'plenora:palette:', area: 'local', owner: 'user', logout: 'clear',
    legacyUserKey: (user, scope) => `plenora.palette.user:${user}.${scope}`,
    legacyDrop: (scope) => `plenora.palette.${scope}`,
    why: 'preferiti e recenti della palette per utente: memoria d\'uso con nomi di tabelle, si toglie all\'uscita',
  }),
  draft: Object.freeze({
    prefix: 'plenora:draft:', area: 'local', owner: 'user', logout: 'keep',
    why: 'copie di sicurezza delle modifiche non salvate (utils/localDrafts): servono a non perdere lavoro, scadono in 14 giorni',
  }),
  draftTab: Object.freeze({
    prefix: 'plenora:tab:', area: 'local', owner: 'browser', logout: 'keep',
    why: 'segnale di vita delle schede aperte (utils/localDrafts)',
  }),
  draftSaved: Object.freeze({
    prefix: 'plenora:saved:', area: 'local', owner: 'browser', logout: 'keep',
    why: 'annuncio «salvato in un\'altra scheda» (utils/localDrafts)',
  }),
  draftDeleted: Object.freeze({
    prefix: 'plenora:deleted:', area: 'local', owner: 'browser', logout: 'keep',
    why: 'lapide di un progetto eliminato: nessuna scheda fa rinascere la copia (utils/localDrafts)',
  }),
  tabId: Object.freeze({
    key: 'plenora:tab-id', area: 'session', owner: 'browser', logout: 'keep',
    why: 'id di questa scheda, stabile al ricaricamento (utils/localDrafts)',
  }),
  chunkReload: Object.freeze({
    key: 'plenora:chunk-reload-at', area: 'session', owner: 'browser', logout: 'keep',
    why: 'ultima ricarica dopo un deploy: niente cicli (utils/chunkReload)',
  }),
  exitCleanup: Object.freeze({
    prefix: 'plenora:exit-cleanup:', area: 'local', owner: 'user', logout: 'keep',
    why: 'pulizie dell\'esecutore partite chiudendo la pagina (o rimandate dalla cache del browser): ripartono alla prossima apertura finche\' il server non risponde; solo identificativi, nessun dato',
  }),
});

// Ogni chiave e prefisso della tabella comincia con il prefisso unico.
for (const [name, row] of Object.entries(STORAGE_KEYS)) {
  if (!String(row.key || row.prefix).startsWith(STORAGE_PREFIX)) throw new Error(`[browserStorage] ${name} fuori dal prefisso unico`);
}

const areaOf = (row) => {
  try { return row.area === 'session' ? window.sessionStorage : window.localStorage; } catch { return null; }
};

/** Chiave di una riga: fissa, oppure prefisso + parti (`user` = utente in testa). */
export function storageKey(name, ...parts) {
  const row = STORAGE_KEYS[name];
  if (!row) throw new Error(`[browserStorage] chiave non dichiarata: ${name}`);
  if (row.key) return row.key;
  if (parts.some(p => p == null || p === '')) return null;
  return `${row.prefix}${parts.join(':')}`;
}

export function readRaw(name, key) {
  const store = areaOf(STORAGE_KEYS[name]);
  try { return store && key ? store.getItem(key) : null; } catch { return null; }
}

export function writeRaw(name, key, value) {
  const store = areaOf(STORAGE_KEYS[name]);
  if (!store || !key) return false;
  try { store.setItem(key, value); return true; } catch { return false; }
}

export function removeKey(name, key) {
  const store = areaOf(STORAGE_KEYS[name]);
  try { if (store && key) store.removeItem(key); } catch { /* niente */ }
}

/** Le chiavi presenti con il prefisso della riga. */
function keysOf(name) {
  const row = STORAGE_KEYS[name];
  const store = areaOf(row);
  const out = [];
  if (!store || !row.prefix) return out;
  try {
    for (let i = 0; i < store.length; i += 1) {
      const k = store.key(i);
      if (k && k.startsWith(row.prefix)) out.push(k);
    }
  } catch { /* storage non disponibile */ }
  return out;
}

/** JSON di una chiave (ripiego se assente o illeggibile). */
export function readJson(name, key, fallback = null) {
  const raw = readRaw(name, key);
  if (raw == null) return fallback;
  try { return JSON.parse(raw); } catch { return fallback; }
}

export const writeJson = (name, key, value) => writeRaw(name, key, JSON.stringify(value));

/**
 * Migrazione una volta: se la chiave nuova non c'e' e una vecchia si', il
 * valore passa alla nuova e la vecchia si toglie. `drop` = chiavi vecchie
 * di proprietario sconosciuto, tolte senza copiarle.
 */
export function migrateLegacy(name, key, legacyKeys = [], drop = []) {
  const store = areaOf(STORAGE_KEYS[name]);
  if (!store || !key) return;
  try {
    for (const old of drop) if (old) store.removeItem(old);
    for (const old of legacyKeys) {
      if (!old || old === key) continue;
      const value = store.getItem(old);
      if (value == null) continue;
      if (store.getItem(key) == null) store.setItem(key, value);
      store.removeItem(old);
    }
  } catch { /* storage non disponibile: la chiave nuova resta com'e' */ }
}

/** Uscita dall'account: via le righe `clear`, di tutti gli utenti del browser. */
function clearOnLogout() {
  for (const [name, row] of Object.entries(STORAGE_KEYS)) {
    if (row.logout !== 'clear') continue;
    if (row.key) removeKey(name, row.key);
    else keysOf(name).forEach(k => removeKey(name, k));
  }
}

registerSessionReset('browserStorage', clearOnLogout);
