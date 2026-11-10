/**
 * nodeActions — azioni e «Visibile a» di ogni tipo di nodo della home per
 * catene (10/11/2026, richiesta dell'utente: nel pannello le stesse azioni
 * delle pagine di origine, per tutti i tipi di nodo).
 *
 * UNA tabella dichiarativa (NODE_ACTIONS): per ogni tipo di nodo le azioni,
 * con etichetta, posto nel pannello e permesso. Regole:
 *  - i permessi sono RULES, le STESSE funzioni che usano le pagine di
 *    origine (pages/EseguibiliList, pages/ConnectionManager, le schede
 *    dell'elenco di pages/ProjectList): le pagine li leggono da qui, cosi'
 *    pannello e pagina non possono divergere;
 *  - i gestori li da' chi monta la home (pages/ProjectList), e sono gli
 *    stessi delle pagine (utils/resourceActions, components/shared/ShareDialog,
 *    components/eseguibili/EseguibileVersionsModal): qui nessuna logica;
 *  - un'azione si mostra se ha il gestore, il permesso e (`shown`) lo stato
 *    che la rende sensata (es. «Pubblica nuova versione» solo se il progetto
 *    e' cambiato dopo la pubblicazione, come il badge della pagina);
 *  - posti: `primary` (pulsante principale), `button` (pulsante), `menu`
 *    (menu «⋯»); `inline` = la rinomina nel pannello.
 *
 * «Visibile a» (visibilityOf): dai campi `access` e `groups` di
 * GET /dashboard/graph. Progetti: tutto il loro gruppo (non si condividono
 * tra gruppi). Eseguibili e origini: gruppo proprietario + gruppi della
 * condivisione («Tutti i gruppi» per «*»); l'elenco delle condivisioni
 * arriva solo al superadmin (chi le gestisce, come la finestra
 * «Condividi»), agli altri il server lo dice non dichiarato (`null`).
 */
import { can } from './permissions';
import { ICON } from './icons';

/** Tipo del nodo: 'project' | 'eseguibile' | 'connection' | 'file' | 'datasource'. */
function nodeKind(node) {
  if (!node) return null;
  if (node.kind === 'project' || node.kind === 'eseguibile') return node.kind;
  return node.type || 'connection';
}

/** Tabella condivisibile di una connessione per categoria (pagina Connessioni: 'database' | 'arcgis'). */
export const connectionShareType = (category) => (category === 'arcgis' ? 'arcgis_connections' : 'connections');

/**
 * Tabella condivisibile del nodo (chiavi di `_SHAREABLE` del backend e
 * `resourceType` di AllowedGroupsSelector), o null per i progetti.
 */
function shareResourceOf(node) {
  switch (nodeKind(node)) {
    case 'eseguibile': return 'eseguibili';
    case 'connection': return connectionShareType(node.connectionKind === 'arcgis' ? 'arcgis' : 'database');
    case 'file': return 'file_sources';
    case 'datasource': return 'datasources';
    default: return null;
  }
}

/**
 * I permessi, uno per significato: le pagine di origine usano questi.
 * (Il backend resta l'autorita': `can_edit` del server entra in `can.*`.)
 */
export const RULES = Object.freeze({
  always: () => true,
  editProjectMeta: (user, project) => can.editProjectMeta(user, project),
  createProject: (user) => can.createProject(user),
  deleteProject: (user, project) => can.deleteProject(user, project),
  createEseguibile: (user) => can.createEseguibile(user),
  runEseguibile: (user) => can.runEseguibile(user),
  manageEseguibile: (user, item) => can.manageEseguibile(user, item),
  // pages/EseguibiliList: solo superadmin, e non su una risorsa bloccata dal server.
  shareEseguibile: (user, item) => can.shareResources(user) && item?.can_edit !== false,
  editConnection: (user, connection) => can.editConnection(user, connection),
  // pages/ConnectionManager: il pulsante 🔗 e' solo del superadmin.
  shareConnection: (user) => can.shareResources(user),
});

const sourceChanged = (item) => item?.source_changed === true;

/**
 * Le azioni del pannello per tipo di nodo. `rule`: chiave di RULES.
 * `label` puo' essere una funzione del nodo.
 */
export const NODE_ACTIONS = Object.freeze({
  project: Object.freeze([
    { id: 'open', label: 'Apri', place: 'primary', rule: 'always' },
    { id: 'run', label: `${ICON.run} Esegui`, place: 'button', rule: 'always' },
    { id: 'history', label: `${ICON.run_history} Cronologia`, place: 'button', rule: 'always' },
    { id: 'schedules', label: `${ICON.schedule} Pianificazioni`, place: 'button', rule: 'always' },
    { id: 'rename', label: '✎ Rinomina', place: 'menu', inline: true, rule: 'editProjectMeta' },
    { id: 'clone', label: '⧉ Clona', place: 'menu', rule: 'createProject' },
    { id: 'publish', label: `${ICON.executable} Pubblica come Eseguibile`, place: 'menu', rule: 'createEseguibile' },
    { id: 'remove', label: `${ICON.delete} Elimina`, place: 'menu', danger: true, rule: 'deleteProject' },
  ]),
  eseguibile: Object.freeze([
    { id: 'openSource', label: 'Apri il progetto', place: 'primary', rule: 'always' },
    { id: 'run', label: `${ICON.run} Esegui`, place: 'button', rule: 'runEseguibile' },
    { id: 'publishVersion', label: 'Pubblica nuova versione', place: 'button', rule: 'manageEseguibile', shown: sourceChanged },
    { id: 'share', label: '🔗 Condividi', place: 'menu', rule: 'shareEseguibile', title: 'Gestisci condivisione cross-gruppo (solo superadmin)' },
    { id: 'rename', label: '✎ Rinomina', place: 'menu', inline: true, rule: 'manageEseguibile' },
    { id: 'versions', label: 'Versioni', place: 'menu', rule: 'manageEseguibile' },
    { id: 'remove', label: `${ICON.delete} Elimina`, place: 'menu', danger: true, rule: 'manageEseguibile' },
  ]),
  connection: Object.freeze([
    { id: 'edit', label: '✏️ Modifica', place: 'primary', rule: 'editConnection' },
    { id: 'openPage', label: 'Apri Connessioni', place: 'button', rule: 'always' },
    { id: 'test', label: `${ICON.connection} Test`, place: 'button', rule: 'always' },
    { id: 'share', label: '🔗 Condividi', place: 'menu', rule: 'shareConnection', title: 'Gestisci condivisione cross-gruppo' },
  ]),
  file: Object.freeze([
    { id: 'openPage', label: 'Apri File', place: 'primary', rule: 'always' },
  ]),
  datasource: Object.freeze([
    { id: 'openPage', label: 'Apri Data Source', place: 'primary', rule: 'always' },
  ]),
});

/**
 * Le azioni da mostrare: [{ id, label, place, danger, inline, title, onSelect }].
 * `handlers`: id -> funzione (assente/null = l'azione non c'e' per questo nodo).
 */
export function panelActions(node, { user, item, handlers = {} } = {}) {
  const kind = nodeKind(node);
  return (NODE_ACTIONS[kind] || [])
    .filter((spec) => typeof handlers[spec.id] === 'function')
    .filter((spec) => RULES[spec.rule](user, item))
    .filter((spec) => !spec.shown || spec.shown(item))
    .map((spec) => ({ ...spec, onSelect: handlers[spec.id] }));
}

const MAGIC_ALL = '*';

/** Indice di `access` della risposta del grafo: `${resource_type}:${id}` -> riga. */
export function accessIndex(raw) {
  const rows = Array.isArray(raw?.access) ? raw.access : [];
  return new Map(rows.map((a) => [`${a.resource_type}:${a.id}`, a]));
}

const groupName = (groups, id) => (groups && groups[id]) || id;

/**
 * «Visibile a» di un nodo: { lines: string[], note: string|null }.
 * ctx: { access (accessIndex), groups (id -> nome), user, unavailable (Set) }.
 */
export function visibilityOf(node, { access = new Map(), groups = {}, user = null, unavailable = new Set() } = {}) {
  if (!node) return { lines: [], note: null };
  if (node.kind === 'project') {
    const gid = node.raw?.group_id;
    return {
      lines: [gid ? `Tutto il gruppo ${groupName(groups, gid)}` : 'Tutto il gruppo del progetto'],
      note: 'I progetti non si condividono con altri gruppi.',
    };
  }
  const type = shareResourceOf(node);
  const row = type ? access.get(`${type}:${node.id}`) : null;
  if (!row) {
    return unavailable.has('access')
      ? { lines: ['Non caricato'], note: 'I dati di condivisione non sono stati caricati.' }
      : { lines: ['Non disponibile'], note: 'Non è tra le risorse visibili al tuo gruppo.' };
  }
  const owner = row.group_id ? `Gruppo ${groupName(groups, row.group_id)} (proprietario)` : 'Gruppo proprietario non noto';
  const shared = row.shared_with;
  if (shared == null) {
    const lines = [owner];
    if (row.group_id && user?.group_id && row.group_id !== user.group_id) lines.push('Il tuo gruppo, per condivisione');
    return { lines, note: 'Le condivisioni con gli altri gruppi le vede e le gestisce il superadmin.' };
  }
  if (shared.includes(MAGIC_ALL)) return { lines: [owner, 'Tutti i gruppi'], note: null };
  const others = shared.filter((g) => g !== row.group_id).map((g) => `Gruppo ${groupName(groups, g)}`)
    .sort((a, b) => a.localeCompare(b, 'it'));
  return { lines: [owner, ...others], note: others.length ? null : 'Non condiviso con altri gruppi.' };
}
