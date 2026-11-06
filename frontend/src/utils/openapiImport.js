/**
 * openapiImport.js — l'unico importatore di specifiche OpenAPI/Swagger del
 * frontend (Fase 5, P10).
 *
 * Regola: OpenAPI 3.0, 3.1 e Swagger 2.0, in JSON o YAML, testo o oggetto,
 * passano tutti da `normalizeOpenApi`, che produce UNA forma interna:
 *
 *   { format, title, description, baseUrl, endpoints, excluded, auth, authCandidates, warnings }
 *   endpoint: { id, method, path, server, summary, description,
 *               parameters: [{ name, location: path|query|header|body, type, required, description }],
 *               request: { contentType } | null, bodyType,
 *               response: { status, contentType } | null,
 *               outputFields: [{ path, name, column, type }] }
 *   excluded: [{ id, method, path, summary, reason }]  operazioni non importabili
 *
 * - `$ref` locali (`#/...`, anche in catena) risolti ovunque: schemi,
 *   parametri, corpi, risposte, path item, security scheme. Uno schema
 *   ricorsivo o troppo profondo si ferma (campo di tipo object) con un avviso;
 *   un riferimento esterno o mancante è un avviso col percorso; un `%`
 *   malformato nel riferimento è un errore col percorso.
 * - YAML: schema CORE più le chiavi di unione `<<: *alias`.
 * - Budget di lavoro unico per l'import (IMPORT_LIMITS): nodi di schema letti
 *   in tutto l'import e colonne per risposta; oltre, lo schema è troncato con
 *   un avviso. Alias YAML e `$ref` condivisi non possono far esplodere il
 *   lavoro (prima 866 byte di YAML bloccavano la pagina per 95 s).
 * - Metodi importabili: esattamente IMPORTABLE_METHODS, quelli che il backend
 *   esegue (http_executor.SUPPORTED_HTTP_METHODS). Le altre operazioni, e
 *   quelle il cui corpo il connettore non sa inviare (BODY_TYPE_RULES), vanno
 *   in `excluded` con il motivo.
 * - Parametri del path item e dell'operazione uniti per (in, name), vince
 *   l'operazione; corpo (requestBody 3.x, `in: body`/`formData` 2.0) espanso
 *   nei campi di primo livello.
 * - Server: `servers` (operazione > path item > documento) con le variabili
 *   sostituite dal valore predefinito; Swagger 2.0 `schemes`+`host`+`basePath`.
 *   Un server relativo si risolve sull'URL da cui è stata scaricata la
 *   specifica, altrimenti resta relativo e serve la Base URL a mano.
 * - Sicurezza: `securitySchemes`/`securityDefinitions` tradotti nei tipi di
 *   autenticazione del progetto con la tabella SECURITY_RULES; la proposta è il
 *   primo schema utilizzabile nell'ordine dei requisiti `security`. Un
 *   tokenUrl relativo si risolve come da RFC 3986 sul server (un «/token»
 *   perde quindi il percorso del server: avviso).
 * - Risposta: 200, 201, poi la prima 2xx; `default` solo se non c'è nessuna
 *   2xx, con un avviso.
 * - Colonne di output con nomi univoci (foglia, poi percorso, poi suffisso).
 * - La forma interna diventa il payload del connettore solo in
 *   `buildConnectionPayloads` (tabella PAYLOAD_FIELDS: i campi che il backend
 *   legge in create_api_connection).
 * Gli errori bloccanti sono in italiano con riga e colonna (JSON/YAML) o col
 * percorso nel documento.
 */
import { formatNumber } from './format';

// Chiavi delle operazioni di un path item (OpenAPI 3.x e Swagger 2.0).
const OPENAPI_METHODS = ['get', 'put', 'post', 'delete', 'patch', 'head', 'options', 'trace'];
/** Metodi che i connettori eseguono: http_executor.SUPPORTED_HTTP_METHODS (il test li confronta). */
export const IMPORTABLE_METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'];
// Livelli di schema espansi nelle colonne di output.
const MAX_SCHEMA_DEPTH = 6;
/** Budget di lavoro di un import: nodi di schema letti (in tutto) e colonne per risposta. */
export const IMPORT_LIMITS = { schemaNodes: 100000, fieldsPerResponse: 2000 };

/*
 * js-yaml si scarica solo al primo import di una specifica (UI-4, V3b):
 * `loadOpenApi` lo carica e poi normalizza. `normalizeOpenApi` resta
 * sincrona; con un testo YAML richiede il parser gia' caricato.
 */
let yamlParser = null;

/** Carica il parser YAML (una volta). */
async function loadYamlParser() {
  if (!yamlParser) {
    const { default: yaml } = await import('js-yaml');
    yamlParser = { yaml, schema: yaml.CORE_SCHEMA.extend({ implicit: [yaml.types.merge] }) };
  }
  return yamlParser;
}

class OpenApiImportError extends Error {
  constructor(message) {
    super(message);
    this.name = 'OpenApiImportError';
  }
}

/** Tipi di autenticazione del progetto proposti dall'importatore (stessi del wizard). */
export const IMPORT_AUTH_OPTIONS = [
  { type: 'none', icon: '🔓', label: 'Nessuna', blank: {} },
  { type: 'api_key', icon: '🔑', label: 'API Key', blank: { key_name: 'X-API-Key', key_value: '', location: 'header' } },
  { type: 'bearer', icon: '🎫', label: 'Bearer', blank: { token: '' } },
  { type: 'basic_auth', icon: '👤', label: 'Username/Pass', blank: { username: '', password: '' } },
  { type: 'oauth2_password', icon: '🔐', label: 'OAuth2 Pwd', blank: { token_url: '', username: '', password: '' } },
  { type: 'oauth2_client_credentials', icon: '🏢', label: 'OAuth2 CC', blank: { token_url: '', client_id: '', client_secret: '', scope: '' } },
];
const blankAuth = (type) => ({ type, ...IMPORT_AUTH_OPTIONS.find(o => o.type === type).blank });

// ---------------------------------------------------------------- testo ---

function lineColumn(text, index) {
  const before = text.slice(0, index).split('\n');
  return { line: before.length, column: before[before.length - 1].length + 1 };
}

function parseText(raw) {
  const text = String(raw).replace(/^﻿/, '');
  if (!text.trim()) throw new OpenApiImportError('Il documento è vuoto.');
  if (/^\s*[{[]/.test(text)) {
    try {
      return JSON.parse(text);
    } catch (e) {
      const pos = /position (\d+)/.exec(e.message);
      const where = pos ? lineColumn(text, Number(pos[1])) : null;
      throw new OpenApiImportError(`JSON non valido${where ? ` alla riga ${where.line}, colonna ${where.column}` : ''}.`);
    }
  }
  if (!yamlParser) throw new Error('[openapiImport] parser YAML non caricato: usare loadOpenApi');
  try {
    return yamlParser.yaml.load(text, { schema: yamlParser.schema });
  } catch (e) {
    if (e?.name !== 'YAMLException') throw e;
    const where = e.mark ? ` alla riga ${e.mark.line + 1}, colonna ${e.mark.column + 1}` : '';
    throw new OpenApiImportError(`YAML non valido${where}: ${e.reason || 'sintassi non riconosciuta'}.`);
  }
}

const isObject = (v) => v != null && typeof v === 'object' && !Array.isArray(v);

/**
 * True se il documento contiene U+FFFD: caratteri che chi lo ha decodificato
 * (proxy o browser) non ha potuto leggere. Ogni oggetto si visita una volta
 * (gli alias YAML condivisi non si espandono).
 */
function hasReplacementChar(source, doc) {
  if (typeof source === 'string') return source.includes('�');
  const seen = new Set();
  const stack = [doc];
  while (stack.length) {
    const v = stack.pop();
    if (typeof v === 'string') { if (v.includes('�')) return true; continue; }
    if (v == null || typeof v !== 'object' || seen.has(v)) continue;
    seen.add(v);
    for (const [k, x] of Object.entries(v)) { if (k.includes('�')) return true; stack.push(x); }
  }
  return false;
}

// ----------------------------------------------------------- riferimenti ---

function makeContext(doc) {
  const warnings = [];
  const warned = new Set();
  const warn = (msg) => { if (!warned.has(msg)) { warned.add(msg); warnings.push(msg); } };

  // Budget unico dei nodi di schema: ogni shapeOf ne spende uno.
  let nodes = 0;
  const spend = (where) => {
    nodes += 1;
    if (nodes === IMPORT_LIMITS.schemaNodes + 1) {
      warn(`Schema troncato: superato il limite di ${formatNumber(IMPORT_LIMITS.schemaNodes)} nodi di schema letti per l'import; `
        + `gli schemi da qui in poi non sono espansi (primo in ${where}).`);
    }
    return nodes <= IMPORT_LIMITS.schemaNodes;
  };

  const lookup = (ref, where) => {
    if (!ref.startsWith('#')) {
      warn(`Riferimento esterno non risolto: ${ref} (in ${where}).`);
      return undefined;
    }
    let parts;
    try {
      parts = ref.slice(1).split('/').slice(1)
        .map(p => decodeURIComponent(p).replace(/~1/g, '/').replace(/~0/g, '~'));
    } catch {
      throw new OpenApiImportError(`Riferimento «${ref}» non valido: codifica «%» malformata (percorso: ${where}).`);
    }
    let cur = doc;
    for (const p of parts) {
      if (!(isObject(cur) || Array.isArray(cur)) || !Object.prototype.hasOwnProperty.call(cur, p)) {
        warn(`Riferimento non trovato: ${ref} (in ${where}).`);
        return undefined;
      }
      cur = cur[p];
    }
    return cur;
  };

  /** Segue i $ref in catena; i campi accanto al $ref (3.1) vincono. */
  const deref = (obj, where) => {
    let cur = obj;
    const refs = [];
    const seen = new Set();
    while (isObject(cur) && typeof cur.$ref === 'string') {
      const ref = cur.$ref;
      if (seen.has(ref)) {
        warn(`Riferimento circolare: ${[...refs, ref].join(' → ')} (in ${where}).`);
        return { value: undefined, refs };
      }
      refs.push(ref);
      seen.add(ref);
      const { $ref: _ref, ...siblings } = cur;
      const target = lookup(ref, where);
      if (target === undefined) return { value: undefined, refs };
      cur = isObject(target) && Object.keys(siblings).length ? { ...target, ...siblings } : target;
    }
    return { value: cur, refs };
  };
  return { doc, warn, warnings, deref, spend };
}

// ---------------------------------------------------------------- schemi ---

const declaredType = (s) => {
  const t = Array.isArray(s.type) ? s.type.find(x => x !== 'null') : s.type;
  return typeof t === 'string' ? t : undefined;
};

/** Forma di uno schema: tipo, proprietà (allOf/oneOf/anyOf uniti), item. */
function shapeOf(ctx, schema, where, ancestors, level = 0) {
  if (!ctx.spend(where)) return { truncated: true };
  // Un grafo ciclico senza $ref (alias YAML) si ferma come uno schema ricorsivo.
  if (level > 4 * MAX_SCHEMA_DEPTH) return { recursive: 'alias YAML ciclico' };
  const { value, refs } = ctx.deref(schema, where);
  const recursive = refs.find(r => ancestors.has(r));
  if (recursive) return { recursive };
  const s = isObject(value) ? value : {};
  const scope = refs.length ? new Set([...ancestors, ...refs]) : ancestors;
  let type = declaredType(s);
  let items = s.items;
  const properties = new Map(Object.entries(isObject(s.properties) ? s.properties : {}));
  const required = new Set(Array.isArray(s.required) ? s.required : []);
  for (const key of ['allOf', 'oneOf', 'anyOf']) {
    for (const part of Array.isArray(s[key]) ? s[key] : []) {
      const sub = shapeOf(ctx, part, where, scope, level + 1);
      if (sub.recursive || sub.truncated) continue;
      type = type || sub.type;
      items = items || sub.items;
      for (const [k, v] of sub.properties) if (!properties.has(k)) properties.set(k, v);
      if (key === 'allOf') sub.required.forEach(r => required.add(r));
    }
  }
  if (!type && properties.size) type = 'object';
  if (!type && items) type = 'array';
  if (type === 'file') type = 'string';
  return { type: type || 'string', properties, required, items, scope };
}

const leafName = (path) => path.replace(/\[\*\]/g, '').split('.').filter(Boolean).pop() || 'valore';

function outputFields(ctx, schema, where) {
  const fields = [];
  const seen = new Set();
  const max = IMPORT_LIMITS.fieldsPerResponse;
  const full = () => {
    if (fields.length < max) return false;
    ctx.warn(`Schema troncato: oltre ${formatNumber(max)} colonne, le altre non sono importate (in ${where}).`);
    return true;
  };
  const push = (path, type) => {
    if (!path || seen.has(path) || full()) return;
    seen.add(path);
    fields.push({ path, name: leafName(path), type });
  };
  const walk = (s, prefix, depth, ancestors) => {
    if (full()) return;
    const sh = shapeOf(ctx, s, where, ancestors);
    if (sh.truncated) return;
    if (sh.recursive) {
      ctx.warn(`Schema ricorsivo ${sh.recursive}: il campo «${prefix || 'radice'}» non è espanso (in ${where}).`);
      push(prefix, 'object');
      return;
    }
    if (depth > MAX_SCHEMA_DEPTH) {
      ctx.warn(`Schema oltre ${MAX_SCHEMA_DEPTH} livelli: il campo «${prefix}» non è espanso (in ${where}).`);
      push(prefix, 'object');
      return;
    }
    if (sh.type === 'array') {
      if (sh.items === undefined) push(`${prefix}[*]`, 'string');
      else walk(sh.items, `${prefix}[*]`, depth + 1, sh.scope);
    } else if (sh.type === 'object') {
      if (!sh.properties.size) push(prefix, 'object');
      for (const [name, prop] of sh.properties) {
        if (full()) return;
        walk(prop, prefix ? `${prefix}.${name}` : name, depth + 1, sh.scope);
      }
    } else {
      push(prefix, sh.type);
    }
  };
  walk(schema, '', 0, new Set());
  // Colonne univoche: la foglia se unica, altrimenti il percorso, poi un suffisso.
  const count = new Map();
  for (const f of fields) count.set(f.name, (count.get(f.name) || 0) + 1);
  const taken = new Set();
  const next = new Map();
  for (const f of fields) {
    const base = count.get(f.name) === 1 ? f.name : f.path.replace(/\[\*\]/g, '').split('.').filter(Boolean).join('_') || f.name;
    let column = base;
    for (let n = next.get(base) || 2; taken.has(column); n += 1) { column = `${base}_${n}`; next.set(base, n + 1); }
    taken.add(column);
    f.column = column;
  }
  return fields;
}

// ----------------------------------------------------- media, parametri ---

const mediaType = (ct) => String(ct).split(';')[0].trim().toLowerCase();
const isJsonMedia = (ct) => mediaType(ct) === 'application/json' || /[/+]json$/.test(mediaType(ct)) || mediaType(ct) === '*/*';

function pickMedia(content) {
  if (!isObject(content)) return null;
  const keys = Object.keys(content);
  const key = keys.find(k => mediaType(k) === 'application/json')
    || keys.find(k => /[/+]json\b/.test(k))
    || keys.find(k => k === '*/*')
    || keys[0];
  return key ? { contentType: key, media: content[key] } : null;
}

const isFileSchema = (s) => isObject(s) && (s.type === 'file' || s.format === 'binary'
  || (isObject(s.items) && (s.items.type === 'file' || s.items.format === 'binary')));

/**
 * Corpo -> parametri di primo livello, più il tipo del corpo e i campi file
 * (per BODY_TYPE_RULES). Senza schema: nessun parametro, corpo libero.
 */
function bodyParams(ctx, schema, where, required) {
  if (schema === undefined) return { list: [], type: null, files: [] };
  const sh = shapeOf(ctx, schema, where, new Set());
  if (sh.truncated || sh.recursive) return { list: [], type: 'object', files: [] };
  if (sh.type !== 'object') {
    return { list: [{ name: 'body', location: 'body', type: sh.type, required: !!required, description: '' }], type: sh.type, files: [] };
  }
  if (!sh.properties.size) {
    ctx.warn(`Corpo senza proprietà dichiarate: nessun parametro del corpo importato (in ${where}).`);
    return { list: [], type: 'object', files: [] };
  }
  const files = [];
  const list = [...sh.properties].map(([name, prop]) => {
    const p = shapeOf(ctx, prop, where, sh.scope);
    const { value } = ctx.deref(prop, where);
    if (isFileSchema(value)) files.push(name);
    return {
      name, location: 'body', type: p.recursive || p.truncated ? 'object' : p.type,
      required: sh.required.has(name), description: (isObject(value) && value.description) || '',
    };
  });
  return { list, type: 'object', files };
}

function parameters(ctx, pathItem, op, opWhere, pathWhere, swagger) {
  const merged = new Map();
  for (const [list, where] of [[pathItem.parameters, `${pathWhere}.parameters`], [op.parameters, `${opWhere}.parameters`]]) {
    (Array.isArray(list) ? list : []).forEach((raw, i) => {
      const at = `${where}[${i}]`;
      const { value: p } = ctx.deref(raw, at);
      if (!isObject(p) || !p.name || !p.in) { ctx.warn(`Parametro senza nome o posizione ignorato (${at}).`); return; }
      merged.set(`${p.in}:${p.name}`, { p, at });
    });
  }
  const out = [];
  let formData = null;
  let body = null;
  for (const { p, at } of merged.values()) {
    if (p.in === 'cookie') { ctx.warn(`Parametro cookie «${p.name}» non supportato: ignorato (${at}).`); continue; }
    if (p.in === 'body') {
      body = bodyParams(ctx, p.schema, at, p.required);
      out.push(...body.list);
      continue;
    }
    let type;
    if (swagger) type = p.type === 'file' ? 'string' : (p.type || 'string');
    else if (p.schema !== undefined) type = shapeOf(ctx, p.schema, at, new Set()).type || 'string';
    else type = shapeOf(ctx, pickMedia(p.content)?.media?.schema, at, new Set()).type || 'string';
    if (!['path', 'query', 'header', 'formData'].includes(p.in)) { ctx.warn(`Posizione «${p.in}» del parametro «${p.name}» non riconosciuta: ignorato (${at}).`); continue; }
    if (p.in === 'formData') {
      formData = formData || { type: 'object', files: [], encoding: 'form' };
      if (p.type === 'file') { formData.files.push(p.name); formData.encoding = 'multipart'; }
    }
    out.push({
      name: p.name,
      location: p.in === 'formData' ? 'body' : p.in,
      type: type === 'file' ? 'string' : type,
      required: p.in === 'path' ? true : !!p.required,
      description: p.description || '',
    });
  }
  return { list: out, formData, body };
}

// ------------------------------------------------------ corpo -> connettore ---

/**
 * Content-Type del corpo -> body_type del connettore
 * (http_executor.SUPPORTED_BODY_TYPES). Un corpo che non combacia con nessuna
 * regola non è rappresentabile: l'operazione è esclusa con il motivo.
 */
const BODY_TYPE_RULES = [
  { match: ct => mediaType(ct) === 'application/x-www-form-urlencoded', bodyType: 'form-urlencoded' },
  { match: ct => mediaType(ct) === 'multipart/form-data', bodyType: 'multipart' },
  { match: isJsonMedia, bodyType: 'json' },
];

/** body_type del connettore per un'operazione, o il motivo per cui non si può importare. */
function connectorBody(method, request, body) {
  if (!request) return { bodyType: 'none' };
  if (method === 'GET') return { reason: 'GET con corpo della richiesta: il connettore invia i parametri di una GET in query' };
  const rule = BODY_TYPE_RULES.find(r => r.match(request.contentType));
  if (!rule) return { reason: `corpo «${request.contentType}» non rappresentabile: il connettore invia JSON, form-urlencoded o multipart` };
  if (body?.files.length) {
    return { reason: `campo file ${body.files.map(f => `«${f}»`).join(', ')} nel corpo: il connettore non invia file (usa un Processore File)` };
  }
  if (body?.type && body.type !== 'object') {
    return { reason: `corpo di tipo ${body.type}: il connettore invia un oggetto con i parametri` };
  }
  return { bodyType: rule.bodyType };
}

// ---------------------------------------------------------------- server ---

const ABSOLUTE = /^[a-z][a-z0-9+.-]*:\/\//i;
const trimSlash = (s) => s.replace(/\/+$/, '');

function resolveUrl(url, sourceUrl) {
  if (ABSOLUTE.test(url)) return trimSlash(url);
  if (sourceUrl) {
    try { return trimSlash(new URL(url || '/', sourceUrl).href); } catch { /* resta relativo */ }
  }
  return trimSlash(url);
}

function serverUrl(ctx, servers, where, sourceUrl) {
  const server = Array.isArray(servers) && isObject(servers[0]) ? servers[0] : { url: '/' };
  const url = String(server.url ?? '/').replace(/\{([^}]+)\}/g, (m, name) => {
    const v = server.variables?.[name];
    if (v && v.default != null) return String(v.default);
    ctx.warn(`Variabile «${name}» del server senza valore predefinito (in ${where}).`);
    return m;
  });
  return resolveUrl(url, sourceUrl);
}

function swaggerBase(doc, sourceUrl) {
  let source = null;
  try { source = sourceUrl ? new URL(sourceUrl) : null; } catch { source = null; }
  const schemes = Array.isArray(doc.schemes) ? doc.schemes : [];
  const scheme = schemes.includes('https') ? 'https' : schemes[0] || source?.protocol.replace(':', '') || 'https';
  const host = doc.host || source?.host;
  const basePath = typeof doc.basePath === 'string' ? doc.basePath : '';
  return trimSlash(host ? `${scheme}://${host}${basePath}` : basePath);
}

/**
 * URL dell'endpoint: basi unite (Base URL manuale, server) e poi il percorso
 * com'è scritto, slash finale compreso (`/` sul server `.../v1` dà `.../v1/`).
 */
function joinUrl(bases, path) {
  const base = bases.filter(b => b && b !== '/')
    .reduce((acc, b) => (acc ? `${trimSlash(acc)}/${b.replace(/^\/+/, '')}` : b), '');
  if (!path) return base;
  return `${trimSlash(base)}/${path.replace(/^\/+/, '')}`;
}

// ------------------------------------------------------------- sicurezza ---

// Flussi OAuth2 utilizzabili, in ordine di preferenza: 3.x (flows) e 2.0 (flow).
const OAUTH_FLOWS = [
  { v3: 'password', v2: 'password', type: 'oauth2_password' },
  { v3: 'clientCredentials', v2: 'application', type: 'oauth2_client_credentials' },
];

/**
 * tokenUrl relativo -> assoluto, come da RFC 3986 sul server dell'API (o
 * sull'URL della specifica). Un percorso assoluto («/oauth/token») sostituisce
 * il percorso del server: se così si perde il prefisso del server, un avviso
 * mostra entrambe le letture e l'utente sceglie.
 */
function tokenUrlOf(raw, env) {
  if (!raw || ABSOLUTE.test(raw)) return raw;
  const serverBase = ABSOLUTE.test(env.base) ? env.base : null;
  const resolved = resolveUrl(raw, serverBase ? `${serverBase}/` : env.sourceUrl);
  if (serverBase && raw.startsWith('/') && !raw.startsWith('//')) {
    let prefix = '';
    try { prefix = trimSlash(new URL(`${serverBase}/`).pathname); } catch { /* server non analizzabile: nessun avviso */ }
    if (prefix) {
      env.ctx.warn(`tokenUrl «${raw}» di «${env.name}»: risolto in ${resolved} (RFC 3986), senza il percorso «${prefix}» `
        + `del server ${serverBase}. Se il token si chiede sotto il server, usa ${serverBase}${raw}.`);
    }
  }
  return resolved;
}

function oauthAuth(s, env) {
  const pick = OAUTH_FLOWS.map(f => {
    const flow = isObject(s.flows) ? s.flows[f.v3] : (s.flow === f.v2 ? s : null);
    return isObject(flow) ? { type: f.type, flow } : null;
  }).find(Boolean);
  if (!pick) return null;
  const auth = { ...blankAuth(pick.type), token_url: tokenUrlOf(String(pick.flow.tokenUrl || ''), env) };
  if (pick.type === 'oauth2_client_credentials') auth.scope = env.scopes.join(' ');
  return auth;
}

/**
 * Schema di sicurezza -> autenticazione del progetto. La prima regola che
 * combacia decide: `auth` la proposta, `unsupported` il motivo dell'esclusione.
 */
const SECURITY_RULES = [
  { match: s => s.type === 'apiKey' && (s.in === 'header' || s.in === 'query'),
    auth: s => ({ ...blankAuth('api_key'), key_name: s.name || 'X-API-Key', location: s.in }) },
  { match: s => s.type === 'apiKey', unsupported: s => `API key in «${s.in}» non supportata (solo header o query)` },
  { match: s => s.type === 'basic' || (s.type === 'http' && /^basic$/i.test(s.scheme || '')), auth: () => blankAuth('basic_auth') },
  { match: s => s.type === 'http' && /^bearer$/i.test(s.scheme || ''), auth: () => blankAuth('bearer') },
  { match: s => s.type === 'http', unsupported: s => `schema HTTP «${s.scheme}» non supportato` },
  { match: s => s.type === 'oauth2', auth: (s, env) => oauthAuth(s, env),
    unsupported: () => 'OAuth2 senza le modalità password o client credentials' },
  { match: s => s.type === 'openIdConnect', unsupported: () => 'OpenID Connect non supportato' },
  { match: () => true, unsupported: s => `tipo «${s.type}» non supportato` },
];

function securityProposal(ctx, swagger, base, sourceUrl, operations) {
  const declared = swagger ? ctx.doc.securityDefinitions : ctx.doc.components?.securitySchemes;
  const schemes = isObject(declared) ? declared : {};
  const order = [];
  const scopesOf = new Map();
  for (const req of [...(Array.isArray(ctx.doc.security) ? ctx.doc.security : []), ...operations.flatMap(op => (Array.isArray(op.security) ? op.security : []))]) {
    for (const [name, scopes] of Object.entries(isObject(req) ? req : {})) {
      if (!order.includes(name)) order.push(name);
      if (Array.isArray(scopes) && scopes.length && !scopesOf.has(name)) scopesOf.set(name, scopes.map(String));
    }
  }
  for (const name of Object.keys(schemes)) if (!order.includes(name)) order.push(name);

  const where = swagger ? 'securityDefinitions' : 'components.securitySchemes';
  const candidates = order.map(name => {
    if (!(name in schemes)) {
      ctx.warn(`Requisito di sicurezza «${name}» senza schema dichiarato in ${where}.`);
      return null;
    }
    const { value: s } = ctx.deref(schemes[name], `${where}.${name}`);
    if (!isObject(s)) return null;
    const rule = SECURITY_RULES.find(r => r.match(s));
    const auth = rule.auth ? rule.auth(s, { ctx, name, base, sourceUrl, scopes: scopesOf.get(name) || [] }) : null;
    return { name, auth, reason: auth ? null : rule.unsupported(s) };
  }).filter(Boolean);

  const chosen = candidates.find(c => c.auth);
  if (!chosen && candidates.length) {
    ctx.warn(`Nessuno schema di sicurezza utilizzabile: ${candidates.map(c => `«${c.name}» (${c.reason})`).join('; ')}. Scegli l'autenticazione a mano.`);
  }
  return { auth: chosen ? chosen.auth : blankAuth('none'), authCandidates: candidates };
}

// ------------------------------------------------------------- risposte ---

/** Risposta di successo: 200, 201, poi la prima 2xx; `default` solo se non c'è nessuna 2xx. */
function successStatus(responses) {
  const codes = Object.keys(responses);
  return ['200', '201'].find(c => c in responses) || codes.find(c => /^2(\d\d|XX)$/i.test(c))
    || ('default' in responses ? 'default' : null);
}

// ------------------------------------------------------------- pubblico ---

/** Come normalizeOpenApi, dopo aver caricato il parser YAML: la usa la UI. */
export async function loadOpenApi(source, options) {
  await loadYamlParser();
  return normalizeOpenApi(source, options);
}

/**
 * Normalizza una specifica OpenAPI 3.x o Swagger 2.0 (testo JSON/YAML o
 * oggetto). Lancia un errore in italiano se il documento non è leggibile.
 * @param {string|object} source
 * @param {{ sourceUrl?: string }} [options] URL da cui è stata scaricata.
 */
export function normalizeOpenApi(source, { sourceUrl } = {}) {
  const doc = typeof source === 'string' ? parseText(source) : source;
  if (!isObject(doc)) throw new OpenApiImportError('Il documento non è un oggetto OpenAPI.');

  let format;
  if (doc.openapi != null) {
    const v = String(doc.openapi);
    if (!/^3(\.|$)/.test(v)) throw new OpenApiImportError(`Versione non supportata: openapi ${v} (supportati OpenAPI 3.x e Swagger 2.0).`);
    format = `openapi-${v.split('.').slice(0, 2).join('.')}`;
  } else if (doc.swagger != null) {
    const v = String(doc.swagger);
    if (v !== '2.0' && v !== '2') throw new OpenApiImportError(`Versione non supportata: swagger ${v} (supportati OpenAPI 3.x e Swagger 2.0).`);
    format = 'swagger-2.0';
  } else {
    throw new OpenApiImportError('Il documento non è OpenAPI né Swagger: manca il campo «openapi: 3.x» o «swagger: "2.0"».');
  }
  const swagger = format === 'swagger-2.0';
  if (doc.paths != null && !isObject(doc.paths)) throw new OpenApiImportError('«paths» deve essere un oggetto (percorso: paths).');

  const ctx = makeContext(doc);
  if (hasReplacementChar(source, doc)) {
    ctx.warn('Il documento contiene caratteri non decodificabili (sostituiti con «�»): controlla la codifica della specifica (attesa UTF-8).');
  }
  const baseUrl = swagger ? swaggerBase(doc, sourceUrl) : serverUrl(ctx, doc.servers, 'servers', sourceUrl);
  const endpoints = [];
  const excluded = [];
  const operations = [];

  for (const [path, rawItem] of Object.entries(doc.paths || {})) {
    const pathWhere = `paths.${path}`;
    const { value: item } = ctx.deref(rawItem, pathWhere);
    if (!isObject(item)) { ctx.warn(`Percorso non leggibile ignorato (${pathWhere}).`); continue; }
    for (const method of OPENAPI_METHODS) {
      if (item[method] === undefined) continue;
      const opWhere = `${pathWhere}.${method}`;
      const op = item[method];
      if (!isObject(op)) { ctx.warn(`Operazione non leggibile ignorata (${opWhere}).`); continue; }
      const METHOD = method.toUpperCase();
      const id = `${METHOD} ${path}`;
      const summary = op.summary || op.operationId || id;
      const exclude = (reason) => excluded.push({ id, method: METHOD, path, summary, reason });
      if (!IMPORTABLE_METHODS.includes(METHOD)) {
        exclude(`metodo ${METHOD} non eseguito dai connettori (solo ${IMPORTABLE_METHODS.join(', ')})`);
        continue;
      }

      const params = parameters(ctx, item, op, opWhere, pathWhere, swagger);
      let request = null;
      let body = null;
      if (swagger) {
        const consumes = op.consumes || doc.consumes;
        if (params.formData) {
          body = params.formData;
          request = { contentType: params.formData.encoding === 'multipart' ? 'multipart/form-data' : 'application/x-www-form-urlencoded' };
        } else if (params.body) {
          body = params.body;
          request = { contentType: (Array.isArray(consumes) && consumes[0]) || 'application/json' };
        }
      } else if (op.requestBody !== undefined) {
        const at = `${opWhere}.requestBody`;
        const { value: reqBody } = ctx.deref(op.requestBody, at);
        const media = pickMedia(reqBody?.content);
        if (media) {
          body = bodyParams(ctx, media.media?.schema, `${at}.content.${media.contentType}`, reqBody.required);
          params.list.push(...body.list);
          request = { contentType: media.contentType };
        }
      }
      const conn = connectorBody(METHOD, request, body);
      if (conn.reason) { exclude(conn.reason); continue; }
      operations.push(op);

      const responses = isObject(op.responses) ? op.responses : {};
      const status = successStatus(responses);
      let response = null;
      let fields = [];
      if (status) {
        const at = `${opWhere}.responses.${status}`;
        const { value: resp } = ctx.deref(responses[status], at);
        if (swagger) {
          const produces = op.produces || doc.produces;
          if (isObject(resp) && resp.schema !== undefined) {
            response = { status, contentType: (Array.isArray(produces) && produces[0]) || 'application/json' };
            fields = outputFields(ctx, resp.schema, `${at}.schema`);
          }
        } else {
          const media = pickMedia(resp?.content);
          if (media) {
            response = { status, contentType: media.contentType };
            if (media.media?.schema !== undefined) fields = outputFields(ctx, media.media.schema, `${at}.content.${media.contentType}.schema`);
          }
        }
        if (status === 'default' && response) {
          ctx.warn(`Colonne prese dalla risposta «default» (nessuna risposta 2xx dichiarata): controlla che sia la risposta di successo (in ${at}).`);
        }
      }

      const server = swagger ? baseUrl
        : op.servers ? serverUrl(ctx, op.servers, `${opWhere}.servers`, sourceUrl)
          : item.servers ? serverUrl(ctx, item.servers, `${pathWhere}.servers`, sourceUrl) : baseUrl;
      endpoints.push({
        id,
        method: METHOD,
        path,
        server,
        summary,
        description: op.description || item.description || '',
        parameters: params.list,
        request,
        bodyType: conn.bodyType,
        response,
        outputFields: fields,
      });
    }
  }
  if (!endpoints.length) {
    ctx.warn(excluded.length ? 'Nessuna operazione importabile: vedi gli endpoint esclusi.' : 'Nessuna operazione trovata in «paths».');
  }

  const { auth, authCandidates } = securityProposal(ctx, swagger, baseUrl, sourceUrl, operations);
  return {
    format,
    title: doc.info?.title || 'API Importata',
    description: doc.info?.description || '',
    baseUrl,
    endpoints,
    excluded,
    auth,
    authCandidates,
    warnings: ctx.warnings,
  };
}

/** True se l'URL del server dell'endpoint non è assoluto (serve la Base URL a mano). */
export const needsBaseUrl = (endpoint) => !ABSOLUTE.test(endpoint.server);

/**
 * Forma interna -> payload del connettore: UNA tabella, una voce per ogni
 * campo che il backend legge in create_api_connection (il test confronta le
 * chiavi con api_connection_management.py e i body_type con http_executor.py).
 */
export const PAYLOAD_FIELDS = {
  name: ({ ep }) => ep.summary,
  description: ({ ep, spec }) => ep.description || `Endpoint da ${spec.title}`,
  api_type: () => 'enricher',
  category: () => 'custom',
  connection_type: () => 'openapi',
  url: ({ ep, manualBaseUrl }) => (needsBaseUrl(ep) ? joinUrl([manualBaseUrl, ep.server], ep.path) : joinUrl([ep.server], ep.path)),
  method: ({ ep }) => ep.method,
  auth: ({ auth }) => auth,
  parameters: ({ ep }) => ep.parameters.map(p => ({
    name: p.name, type: p.type === 'integer' ? 'number' : p.type, required: p.required, mode: 'mapped', location: p.location,
  })),
  static_parameters: () => ({}),
  schema_fields: ({ ep }) => ep.outputFields.map(f => ({ path: f.path, name: f.column, type: f.type })),
  output_mapping: ({ ep, chosen }) => ep.outputFields.filter(f => chosen.has(f.path)).map(f => ({ path: f.path, column: f.column, type: f.type })),
  request: ({ ep }) => ({ body_type: ep.bodyType }),
  options: () => ({ rate_limit: 5, timeout: 30, retry_on_error: true, retry_max: 3 }),
};

/**
 * Connessioni API da creare per gli endpoint scelti.
 * @param {object} spec risultato di normalizeOpenApi
 * @param {{ endpointIds: string[], fieldsByEndpoint: Record<string, Set<string>>, auth: object, manualBaseUrl?: string }} choice
 */
export function buildConnectionPayloads(spec, { endpointIds, fieldsByEndpoint = {}, auth, manualBaseUrl = '' }) {
  return spec.endpoints.filter(ep => endpointIds.includes(ep.id)).map(ep => {
    const env = { spec, ep, auth, manualBaseUrl, chosen: fieldsByEndpoint[ep.id] || new Set(ep.outputFields.map(f => f.path)) };
    return Object.fromEntries(Object.entries(PAYLOAD_FIELDS).map(([key, value]) => [key, value(env)]));
  });
}
