/**
 * Importatore unico OpenAPI/Swagger (Fase 5, P10).
 *
 * Corpus di specifiche tipiche (tests/fixtures/openapi-import): petstore
 * Swagger 2.0 in YAML e JSON, petstore OpenAPI 3.0, una 3.1 con $ref annidati
 * e ricorsivi, una con server variabili, una con API key in header/query/cookie,
 * una con OAuth2. Per ognuna la forma interna attesa: endpoint, metodo,
 * parametri (posizione e tipo), richiesta, risposta, colonne, auth proposta,
 * avvisi. Poi gli errori bloccanti (con riga/colonna o percorso), i payload
 * delle connessioni e la guardia "un solo importatore".
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect, beforeAll } from 'vitest';
import {
  normalizeOpenApi, loadOpenApi, buildConnectionPayloads, needsBaseUrl, IMPORT_AUTH_OPTIONS, IMPORTABLE_METHODS, IMPORT_LIMITS, PAYLOAD_FIELDS,
} from '../../src/utils/openapiImport';
import { sourceFiles, stripComments } from '../helpers/sourceScan.js';

const DIR = path.resolve(import.meta.dirname, '../fixtures/openapi-import');
// js-yaml si carica al bisogno (UI-4 V3b): la UI passa da loadOpenApi.
beforeAll(() => loadOpenApi('{"openapi": "3.0.0", "paths": {}}'));

const read = (name) => fs.readFileSync(path.join(DIR, name), 'utf-8');

// Riassunto confrontabile: «posizione:nome:tipo» (! = obbligatorio), «percorso>colonna:tipo».
const summary = (spec) => ({
  format: spec.format,
  baseUrl: spec.baseUrl,
  auth: spec.auth,
  candidates: spec.authCandidates.map(c => `${c.name}:${c.auth ? c.auth.type : c.reason}`),
  warnings: spec.warnings,
  excluded: spec.excluded.map(x => `${x.id}: ${x.reason}`),
  endpoints: spec.endpoints.map(e => ({
    id: e.id,
    server: e.server,
    params: e.parameters.map(p => `${p.location}:${p.name}:${p.type}${p.required ? '!' : ''}`),
    request: e.request?.contentType ?? null,
    response: e.response ? `${e.response.status} ${e.response.contentType}` : null,
    fields: e.outputFields.map(f => `${f.path}>${f.column}:${f.type}`),
  })),
});

const PET_FIELDS = (p) => [
  `${p}id>id:integer`, `${p}category.id>category_id:integer`, `${p}category.name>category_name:string`,
  `${p}name>name:string`, `${p}photoUrls[*]>photoUrls:string`, `${p}tags[*].id>tags_id:integer`,
  `${p}tags[*].name>tags_name:string`, `${p}status>status:string`,
];

const PETSTORE_V2 = {
  format: 'swagger-2.0',
  baseUrl: 'https://petstore.swagger.io/v2',
  auth: { type: 'api_key', key_name: 'api_key', key_value: '', location: 'header' },
  candidates: ['petstore_auth:OAuth2 senza le modalità password o client credentials', 'api_key:api_key'],
  warnings: [],
  endpoints: [
    { id: 'POST /pet', server: 'https://petstore.swagger.io/v2',
      params: ['body:id:integer', 'body:category:object', 'body:name:string!', 'body:photoUrls:array!', 'body:tags:array', 'body:status:string'],
      request: 'application/json', response: null, fields: [] },
    { id: 'GET /pet/findByStatus', server: 'https://petstore.swagger.io/v2',
      params: ['query:status:array!'], request: null, response: '200 application/json', fields: PET_FIELDS('[*].') },
    { id: 'GET /pet/{petId}', server: 'https://petstore.swagger.io/v2',
      params: ['path:petId:integer!'], request: null, response: '200 application/json', fields: PET_FIELDS('') },
    { id: 'DELETE /pet/{petId}', server: 'https://petstore.swagger.io/v2',
      params: ['path:petId:integer!', 'header:api_key:string'], request: null, response: null, fields: [] },
    { id: 'POST /pet/{petId}/uploadImage', server: 'https://petstore.swagger.io/v2',
      params: ['path:petId:integer!', 'body:additionalMetadata:string', 'body:file:string'],
      request: 'multipart/form-data', response: '200 application/json',
      fields: ['code>code:integer', 'type>type:string', 'message>message:string'] },
  ],
};
// Il corpo multipart con un file non e' rappresentabile dal connettore.
PETSTORE_V2.endpoints.pop();
PETSTORE_V2.excluded = ['POST /pet/{petId}/uploadImage: campo file «file» nel corpo: il connettore non invia file (usa un Processore File)'];

const V3 = 'https://petstore3.swagger.io/api/v3';
const PETSTORE_V3 = {
  excluded: [],
  format: 'openapi-3.0',
  baseUrl: V3,
  auth: { type: 'api_key', key_name: 'api_key', key_value: '', location: 'header' },
  candidates: ['petstore_auth:OAuth2 senza le modalità password o client credentials', 'api_key:api_key'],
  warnings: [],
  endpoints: [
    { id: 'PUT /pet', server: V3,
      params: ['body:id:integer', 'body:name:string!', 'body:category:object', 'body:photoUrls:array!', 'body:tags:array', 'body:status:string'],
      request: 'application/json', response: '200 application/json',
      fields: ['id>id:integer', 'name>name:string', 'category.id>category_id:integer', 'category.name>category_name:string',
        'photoUrls[*]>photoUrls:string', 'tags[*].id>tags_id:integer', 'tags[*].name>tags_name:string', 'status>status:string'] },
    { id: 'GET /pet/findByTags', server: V3, params: ['query:tags:array'], request: null, response: '200 application/json',
      fields: ['[*].id>id:integer', '[*].name>name:string', '[*].category.id>category_id:integer', '[*].category.name>category_name:string',
        '[*].photoUrls[*]>photoUrls:string', '[*].tags[*].id>tags_id:integer', '[*].tags[*].name>tags_name:string', '[*].status>status:string'] },
    { id: 'GET /pet/{petId}', server: V3, params: ['path:petId:integer!'], request: null, response: '200 application/json',
      fields: ['id>id:integer', 'name>name:string', 'category.id>category_id:integer', 'category.name>category_name:string',
        'photoUrls[*]>photoUrls:string', 'tags[*].id>tags_id:integer', 'tags[*].name>tags_name:string', 'status>status:string'] },
    { id: 'PATCH /pet/{petId}', server: V3, params: ['path:petId:integer!', 'body:status:string'],
      request: 'application/merge-patch+json', response: null, fields: [] },
    { id: 'GET /store/inventory', server: V3, params: [], request: null, response: '200 application/json', fields: [] },
  ],
};

const CAT = 'https://catalogo.example.com/api';
const NODE_SCHEMA = 'paths./nodes/{nodeId}.get.responses.2XX.content.application/vnd.catalogo+json.schema';
const BROKEN_SCHEMA = 'paths./broken.get.responses.200.content.application/json.schema';
const NESTED_REFS = {
  excluded: [],
  format: 'openapi-3.1',
  baseUrl: CAT,
  auth: { type: 'none' },
  candidates: [],
  warnings: [
    'Parametro cookie «session» non supportato: ignorato (paths./nodes/{nodeId}.parameters[2]).',
    `Schema ricorsivo #/components/schemas/Node: il campo «children[*]» non è espanso (in ${NODE_SCHEMA}).`,
    `Schema ricorsivo #/components/schemas/Node: il campo «parent» non è espanso (in ${NODE_SCHEMA}).`,
    `Riferimento non trovato: #/components/schemas/DoesNotExist (in ${BROKEN_SCHEMA}).`,
    `Riferimento esterno non risolto: common.yaml#/components/schemas/Money (in ${BROKEN_SCHEMA}).`,
    'Riferimento circolare: #/components/schemas/LoopA → #/components/schemas/LoopB → #/components/schemas/LoopA '
      + `(in ${BROKEN_SCHEMA}).`,
  ],
  endpoints: [
    // Parametri del path item e dell'operazione uniti: `lang` dell'operazione vince.
    { id: 'GET /nodes/{nodeId}', server: CAT,
      params: ['path:nodeId:integer!', 'query:lang:string!', 'header:X-Trace:string'],
      request: null, response: '2XX application/vnd.catalogo+json',
      // $ref in catena (Alias -> AliasTarget -> Node), allOf unito, ricorsione fermata.
      fields: ['id>id:integer', 'label>label:string', 'owner.id>owner_id:string', 'owner.name>name:string',
        'owner.email>email:string', 'children[*]>children:object', 'parent>parent:object'] },
    // Path item per $ref, requestBody espanso, oneOf unito.
    { id: 'POST /items', server: CAT, params: ['body:sku:string', 'body:price:object'],
      request: 'application/json', response: '201 application/json',
      fields: ['sku>sku:string', 'price.value>value:number', 'price.currency>currency:string'] },
    { id: 'GET /broken', server: CAT, params: [], request: null, response: '200 application/json',
      fields: ['missing>missing:string', 'external>external:string', 'loop>loop:string'] },
  ],
};

const SERVER_VARIABLES = {
  excluded: [],
  format: 'openapi-3.0',
  baseUrl: 'https://api.meteo.example.com:8443/v2',
  auth: { type: 'none' },
  candidates: [],
  warnings: ['Variabile «undefinedVar» del server senza valore predefinito (in paths./status.get.servers).'],
  endpoints: [
    { id: 'GET /forecast', server: 'https://api.meteo.example.com:8443/v2', params: ['query:lat:number!', 'query:lon:number!'],
      request: null, response: '200 application/json',
      fields: ['temperature>temperature:number', 'wind.speed>speed:number', 'wind.direction>direction:string'] },
    // servers del path item e dell'operazione vincono su quelli del documento.
    { id: 'GET /alerts', server: 'https://alerts.meteo.example.com/v1', params: [], request: null,
      response: '200 application/json', fields: ['[*].level>level:integer', '[*].text>text:string'] },
    { id: 'GET /status', server: 'https://status.example.com/{undefinedVar}', params: [], request: null, response: null, fields: [] },
  ],
};

const APIKEY = {
  excluded: [],
  format: 'openapi-3.0',
  baseUrl: 'https://geo.example.com/v1',
  // Il requisito globale `security` decide: chiave in query.
  auth: { type: 'api_key', key_name: 'key', key_value: '', location: 'query' },
  candidates: ['QueryKey:api_key', 'HeaderKey:api_key', 'CookieKey:API key in «cookie» non supportata (solo header o query)'],
  warnings: [],
  endpoints: [
    { id: 'GET /geocode', server: 'https://geo.example.com/v1', params: ['query:address:string!'], request: null,
      response: '200 application/json',
      // Foglie `id` ripetute: colonne dal percorso, mai duplicate.
      fields: ['results[*].id>results_id:string', 'results[*].location.id>results_location_id:string',
        'results[*].location.lat>lat:number', 'results[*].location.lng>lng:number',
        'results[*].address.id>results_address_id:string', 'results[*].address.city>city:string'] },
    { id: 'GET /reverse', server: 'https://geo.example.com/v1', params: ['query:lat:number'], request: null,
      response: '200 application/json', fields: ['city>city:string'] },
  ],
};

const OAUTH2 = {
  excluded: [],
  format: 'openapi-3.0',
  baseUrl: 'https://anagrafe.example.org/rest',
  // tokenUrl relativo risolto sul server; scope dal requisito dell'operazione.
  auth: { type: 'oauth2_client_credentials', token_url: 'https://anagrafe.example.org/oauth/token',
    client_id: '', client_secret: '', scope: 'persone.read persone.search' },
  candidates: ['oauth:oauth2_client_credentials', 'oidc:OpenID Connect non supportato'],
  // RFC 3986: «/oauth/token» sostituisce il percorso del server; l'avviso lo dice.
  warnings: ['tokenUrl «/oauth/token» di «oauth»: risolto in https://anagrafe.example.org/oauth/token (RFC 3986), '
    + 'senza il percorso «/rest» del server https://anagrafe.example.org/rest. '
    + 'Se il token si chiede sotto il server, usa https://anagrafe.example.org/rest/oauth/token.'],
  endpoints: [
    { id: 'GET /persone', server: 'https://anagrafe.example.org/rest', params: ['query:cognome:string'], request: null,
      response: '200 application/json',
      fields: ['items[*].codiceFiscale>codiceFiscale:string', 'items[*].nascita>nascita:string'] },
  ],
};

describe('corpus di specifiche', () => {
  it.each([
    ['petstore-v2.yaml', {}, PETSTORE_V2],
    ['petstore-v2.json', {}, PETSTORE_V2],
    ['petstore-v3.yaml', { sourceUrl: 'https://petstore3.swagger.io/api/v3/openapi.json' }, PETSTORE_V3],
    ['nested-refs.json', {}, NESTED_REFS],
    ['server-variables.yaml', {}, SERVER_VARIABLES],
    ['apikey.yaml', {}, APIKEY],
    ['oauth2.yaml', {}, OAUTH2],
  ])('%s', (name, options, expected) => {
    expect(summary(normalizeOpenApi(read(name), options))).toEqual(expected);
  });

  it('testo e oggetto danno la stessa forma (il proxy restituisce l\'oggetto JSON)', () => {
    expect(normalizeOpenApi(JSON.parse(read('petstore-v2.json')))).toEqual(normalizeOpenApi(read('petstore-v2.yaml')));
  });

  it('server relativo senza URL di origine: resta relativo e serve la Base URL', () => {
    const spec = normalizeOpenApi(read('petstore-v3.yaml'));
    expect(spec.baseUrl).toBe('/api/v3');
    expect(spec.endpoints.every(needsBaseUrl)).toBe(true);
  });

  it('Swagger 2.0 senza host: host e schema dall\'URL della specifica', () => {
    const doc = JSON.parse(read('petstore-v2.json'));
    delete doc.host;
    delete doc.schemes;
    expect(normalizeOpenApi(doc, { sourceUrl: 'http://intranet.local:8080/docs/swagger.json' }).baseUrl)
      .toBe('http://intranet.local:8080/v2');
  });

  it('OAuth2 Swagger 2.0: flow password e application', () => {
    const doc = (flow) => ({
      swagger: '2.0', host: 'h.example', basePath: '/api', paths: {},
      securityDefinitions: { o: { type: 'oauth2', flow, tokenUrl: '/token', scopes: {} } },
    });
    expect(normalizeOpenApi(doc('password')).auth).toEqual({ type: 'oauth2_password', token_url: 'https://h.example/token', username: '', password: '' });
    expect(normalizeOpenApi(doc('application')).auth.type).toBe('oauth2_client_credentials');
  });

  it('HTTP basic e bearer (3.x) e basic (2.0)', () => {
    const v3 = (scheme) => normalizeOpenApi({ openapi: '3.0.0', paths: {}, components: { securitySchemes: { s: { type: 'http', scheme } } } }).auth.type;
    expect(v3('basic')).toBe('basic_auth');
    expect(v3('Bearer')).toBe('bearer');
    expect(normalizeOpenApi({ swagger: '2.0', paths: {}, securityDefinitions: { b: { type: 'basic' } } }).auth.type).toBe('basic_auth');
    const digest = normalizeOpenApi({ openapi: '3.0.0', paths: {}, components: { securitySchemes: { d: { type: 'http', scheme: 'digest' } } } });
    expect(digest.auth).toEqual({ type: 'none' });
    expect(digest.warnings).toContain(
      'Nessuno schema di sicurezza utilizzabile: «d» (schema HTTP «digest» non supportato). Scegli l\'autenticazione a mano.');
  });

  it('ogni auth proposta è un tipo del progetto', () => {
    const types = new Set(IMPORT_AUTH_OPTIONS.map(o => o.type));
    for (const name of fs.readdirSync(DIR)) {
      const spec = normalizeOpenApi(read(name));
      expect(types.has(spec.auth.type)).toBe(true);
      spec.authCandidates.filter(c => c.auth).forEach(c => expect(types.has(c.auth.type)).toBe(true));
    }
  });

  it('i metodi importabili sono quelli del backend; gli altri esclusi con il motivo', () => {
    const op = { responses: {} };
    const spec = normalizeOpenApi({ openapi: '3.0.0', paths: { '/x': {
      get: op, put: op, post: op, delete: op, patch: op, head: op, options: op, trace: op, parameters: [], summary: 's',
    } } });
    expect(spec.endpoints.map(e => e.method)).toEqual(['GET', 'PUT', 'POST', 'DELETE', 'PATCH']);
    expect(spec.excluded.map(x => `${x.id}: ${x.reason}`)).toEqual(['HEAD', 'OPTIONS', 'TRACE'].map(m =>
      `${m} /x: metodo ${m} non eseguito dai connettori (solo GET, POST, PUT, PATCH, DELETE)`));
  });

  it('colonne sempre univoche', () => {
    for (const name of fs.readdirSync(DIR)) {
      for (const ep of normalizeOpenApi(read(name)).endpoints) {
        const cols = ep.outputFields.map(f => f.column);
        expect(new Set(cols).size).toBe(cols.length);
      }
    }
  });

  it('schema troppo profondo: fermato con un avviso', () => {
    let schema = { type: 'string' };
    for (let i = 0; i < 10; i += 1) schema = { type: 'object', properties: { [`l${i}`]: schema } };
    const spec = normalizeOpenApi({ openapi: '3.0.0', paths: { '/d': { get: { responses: { 200: { content: { 'application/json': { schema } } } } } } } });
    expect(spec.endpoints[0].outputFields).toEqual([{ path: 'l9.l8.l7.l6.l5.l4.l3', name: 'l3', column: 'l3', type: 'object' }]);
    expect(spec.warnings[0]).toMatch(/^Schema oltre 6 livelli: il campo «l9\.l8\.l7\.l6\.l5\.l4\.l3» non è espanso/);
  });
});

describe('errori bloccanti in italiano', () => {
  it.each([
    ['', 'Il documento è vuoto.'],
    ['{\n  "openapi": "3.0.0",\n  "paths": {,}\n}', /^JSON non valido alla riga 3, colonna 13\.$/],
    ['openapi: 3.0.0\ninfo:\n  title: x\n   bad: indent\npaths: {}', /^YAML non valido alla riga 4, colonna \d+: /],
    ['- una\n- lista', 'Il documento non è un oggetto OpenAPI.'],
    ['title: niente versione\npaths: {}', 'Il documento non è OpenAPI né Swagger: manca il campo «openapi: 3.x» o «swagger: "2.0"».'],
    ['swagger: "1.2"\napis: []', 'Versione non supportata: swagger 1.2 (supportati OpenAPI 3.x e Swagger 2.0).'],
    ['openapi: 4.0.0\npaths: {}', 'Versione non supportata: openapi 4.0.0 (supportati OpenAPI 3.x e Swagger 2.0).'],
    ['openapi: 3.0.0\npaths: [1, 2]', '«paths» deve essere un oggetto (percorso: paths).'],
  ])('%j', (text, message) => {
    expect(() => normalizeOpenApi(text)).toThrow(message);
  });
});

describe('payload delle connessioni', () => {
  it('url, parametri, colonne scelte e auth come le vuole il backend', () => {
    const spec = normalizeOpenApi(read('petstore-v3.yaml'));
    const [payload] = buildConnectionPayloads(spec, {
      endpointIds: ['GET /pet/{petId}'],
      fieldsByEndpoint: { 'GET /pet/{petId}': new Set(['id', 'category.id']) },
      auth: { type: 'api_key', key_name: 'api_key', key_value: 'k', location: 'query' },
      manualBaseUrl: 'https://petstore3.swagger.io/',
    });
    expect(payload).toEqual({
      name: 'Find pet by ID.',
      description: 'Endpoint da Swagger Petstore - OpenAPI 3.0',
      api_type: 'enricher',
      category: 'custom',
      connection_type: 'openapi',
      url: 'https://petstore3.swagger.io/api/v3/pet/{petId}',
      method: 'GET',
      auth: { type: 'api_key', key_name: 'api_key', key_value: 'k', location: 'query' },
      parameters: [{ name: 'petId', type: 'number', required: true, mode: 'mapped', location: 'path' }],
      static_parameters: {},
      request: { body_type: 'none' },
      schema_fields: spec.endpoints[2].outputFields.map(f => ({ path: f.path, name: f.column, type: f.type })),
      output_mapping: [
        { path: 'id', column: 'id', type: 'integer' },
        { path: 'category.id', column: 'category_id', type: 'integer' },
      ],
      options: { rate_limit: 5, timeout: 30, retry_on_error: true, retry_max: 3 },
    });
  });

  it('server assoluto: la Base URL manuale non conta; senza scelta dei campi, tutti', () => {
    const spec = normalizeOpenApi(read('server-variables.yaml'));
    const [a, b] = buildConnectionPayloads(spec, { endpointIds: ['GET /forecast', 'GET /alerts'], auth: { type: 'none' }, manualBaseUrl: 'https://ignorata' });
    expect(a.url).toBe('https://api.meteo.example.com:8443/v2/forecast');
    expect(b.url).toBe('https://alerts.meteo.example.com/v1/alerts');
    expect(a.output_mapping.map(m => m.column)).toEqual(['temperature', 'speed', 'direction']);
  });
});

// ------------------------------------------------- contratto col backend ---

const BACKEND = path.resolve(import.meta.dirname, '../../../backend/app/core/arricchisci');
const backendCode = (file) => fs.readFileSync(path.join(BACKEND, file), 'utf-8');
const frozenset = (file, name) => {
  const m = new RegExp(`${name} = frozenset\\(\\{([^}]*)\\}`).exec(backendCode(file));
  return [...m[1].matchAll(/"([^"]+)"/g)].map(x => x[1]);
};
// Campi del payload che create_api_connection legge (data.get("x") / data["x"]).
const createReads = () => {
  const code = backendCode('api_connection_management.py');
  const start = code.indexOf('def create_api_connection');
  const body = code.slice(start, code.indexOf('\n    def ', start + 1));
  return new Set([...body.matchAll(/data(?:\.get\(|\[)['"]([a-z_]+)['"]/g)].map(m => m[1]));
};

const opSpec = (method, op) => ({ openapi: '3.0.0', info: { title: 't' }, servers: [{ url: 'https://h.example/api/v1' }],
  paths: { '/r': { [method]: { responses: { 200: { description: '' } }, ...op } } } });
const withBody = (contentType, schema) => ({ requestBody: { content: { [contentType]: schema === undefined ? {} : { schema } } } });
const OBJ = { type: 'object', properties: { a: { type: 'string' }, n: { type: 'integer' } } };
const NOT_REPRESENTABLE = (ct) => `corpo «${ct}» non rappresentabile: il connettore invia JSON, form-urlencoded o multipart`;

// Ogni forma di corpo: body_type del connettore o motivo dell'esclusione.
const BODY_CASES = [
  ['get', {}, 'none'],
  ['delete', {}, 'none'],
  ['post', withBody('application/json', OBJ), 'json'],
  ['put', withBody('application/json; charset=utf-8', OBJ), 'json'],
  ['patch', withBody('application/merge-patch+json', OBJ), 'json'],
  ['post', withBody('*/*', OBJ), 'json'],
  ['post', withBody('application/json'), 'json'],
  ['post', withBody('application/x-www-form-urlencoded', OBJ), 'form-urlencoded'],
  ['post', withBody('multipart/form-data', OBJ), 'multipart'],
  ['post', withBody('multipart/form-data', { type: 'object', properties: { f: { type: 'string', format: 'binary' } } }),
    'campo file «f» nel corpo: il connettore non invia file (usa un Processore File)'],
  ['post', withBody('application/xml', OBJ), NOT_REPRESENTABLE('application/xml')],
  ['post', withBody('text/plain', { type: 'string' }), NOT_REPRESENTABLE('text/plain')],
  ['post', withBody('application/octet-stream', { type: 'string', format: 'binary' }), NOT_REPRESENTABLE('application/octet-stream')],
  ['post', withBody('application/json', { type: 'array', items: { type: 'string' } }), 'corpo di tipo array: il connettore invia un oggetto con i parametri'],
  ['get', withBody('application/json', OBJ), 'GET con corpo della richiesta: il connettore invia i parametri di una GET in query'],
];

describe('forma interna -> payload: campi letti dal backend', () => {
  it('i metodi importabili sono esattamente SUPPORTED_HTTP_METHODS del backend', () => {
    expect([...IMPORTABLE_METHODS].sort()).toEqual(frozenset('http_executor.py', 'SUPPORTED_HTTP_METHODS').sort());
  });

  it('ogni campo del payload è letto da create_api_connection (anche request -> body_type)', () => {
    const reads = createReads();
    expect(reads.has('request')).toBe(true);
    expect(Object.keys(PAYLOAD_FIELDS).filter(k => !reads.has(k))).toEqual([]);
    expect(backendCode('http_executor.py')).toMatch(/request_cfg\.get\("body_type", default\)/);
  });

  it.each(BODY_CASES.map(([m, op, out]) => [`${m.toUpperCase()} ${JSON.stringify(op.requestBody?.content ?? null)}`, m, op, out]))(
    '%s', (_label, method, op, out) => {
      const spec = normalizeOpenApi(opSpec(method, op));
      const M = method.toUpperCase();
      if (frozenset('http_executor.py', 'SUPPORTED_BODY_TYPES').includes(out)) {
        expect(spec.excluded).toEqual([]);
        const [payload] = buildConnectionPayloads(spec, { endpointIds: [spec.endpoints[0].id], auth: { type: 'none' } });
        expect(payload.request).toEqual({ body_type: out });
      } else {
        expect(spec.endpoints).toEqual([]);
        expect(spec.excluded).toEqual([{ id: `${M} /r`, method: M, path: '/r', summary: `${M} /r`, reason: out }]);
        expect(spec.warnings).toContain('Nessuna operazione importabile: vedi gli endpoint esclusi.');
      }
    });

  it('Swagger 2.0: formData senza file -> form-urlencoded, body oggetto -> json, consumes non JSON -> escluso', () => {
    const sw = (parameters, consumes) => normalizeOpenApi({ swagger: '2.0', host: 'h', paths: { '/r': { post: { consumes, parameters, responses: {} } } } });
    expect(sw([{ name: 'a', in: 'formData', type: 'string' }]).endpoints[0].bodyType).toBe('form-urlencoded');
    expect(sw([{ name: 'b', in: 'body', schema: OBJ }]).endpoints[0].bodyType).toBe('json');
    expect(sw([{ name: 'b', in: 'body', schema: OBJ }], ['application/xml']).excluded[0].reason).toBe(NOT_REPRESENTABLE('application/xml'));
  });
});

describe('URL, risposte, YAML, riferimenti', () => {
  const none = { type: 'none' };

  it('il percorso «/» tiene lo slash finale; gli altri si uniscono al server', () => {
    const spec = normalizeOpenApi({ openapi: '3.0.0', servers: [{ url: 'https://h.example/api/v1/' }],
      paths: { '/': { get: { responses: {} } }, '/a/': { get: { responses: {} } }, b: { get: { responses: {} } } } });
    const urls = buildConnectionPayloads(spec, { endpointIds: spec.endpoints.map(e => e.id), auth: none }).map(p => p.url);
    expect(urls).toEqual(['https://h.example/api/v1/', 'https://h.example/api/v1/a/', 'https://h.example/api/v1/b']);
    const rel = normalizeOpenApi({ openapi: '3.0.0', servers: [{ url: '/' }], paths: { '/': { get: { responses: {} } } } });
    expect(buildConnectionPayloads(rel, { endpointIds: ['GET /'], auth: none, manualBaseUrl: 'https://m.example/x' })[0].url)
      .toBe('https://m.example/x/');
  });

  it('risposta «default» solo senza 2xx, con un avviso', () => {
    const resp = (props) => ({ description: '', content: { 'application/json': { schema: { type: 'object', properties: props } } } });
    const only = normalizeOpenApi(opSpec('get', { responses: { default: resp({ code: { type: 'integer' } }), 404: resp({ e: {} }) } }));
    expect(only.endpoints[0].response.status).toBe('default');
    expect(only.endpoints[0].outputFields.map(f => f.path)).toEqual(['code']);
    expect(only.warnings).toEqual(['Colonne prese dalla risposta «default» (nessuna risposta 2xx dichiarata): '
      + 'controlla che sia la risposta di successo (in paths./r.get.responses.default).']);
    const both = normalizeOpenApi(opSpec('get', { responses: { default: resp({ code: { type: 'integer' } }), 202: resp({ id: { type: 'string' } }) } }));
    expect(both.endpoints[0].response.status).toBe('202');
    expect(both.warnings).toEqual([]);
  });

  it('YAML: chiavi di unione «<<: *alias» (schema CORE + merge)', () => {
    const spec = normalizeOpenApi([
      'openapi: 3.0.0', 'info: {title: x}', 'servers: [{url: "https://h"}]',
      'base: &b {summary: comune, responses: {"200": {description: ok, content: {application/json: {schema: {type: object, properties: {a: {type: string}}}}}}}}',
      'paths:', '  /a:', '    get:', '      <<: *b', '      operationId: x', '  /d:', '    get: {<<: *b, summary: proprio}',
      'data: 2020-01-01',
    ].join('\n'));
    expect(spec.endpoints.map(e => [e.summary, e.outputFields.map(f => f.path)])).toEqual([['comune', ['a']], ['proprio', ['a']]]);
  });

  it('$ref con «%» malformato: errore col percorso', () => {
    const doc = opSpec('get', { responses: { 200: { description: '', content: { 'application/json': { schema: { $ref: '#/components/schemas/%E0%A4%A' } } } } } });
    expect(() => normalizeOpenApi(doc)).toThrow('Riferimento «#/components/schemas/%E0%A4%A» non valido: codifica «%» malformata '
      + '(percorso: paths./r.get.responses.200.content.application/json.schema).');
    expect(() => normalizeOpenApi(doc)).toThrow(expect.objectContaining({ name: 'OpenApiImportError' }));
  });

  it('caratteri non decodificabili (U+FFFD) nel testo o nell\'oggetto: avviso', () => {
    const msg = 'Il documento contiene caratteri non decodificabili (sostituiti con «\uFFFD»): controlla la codifica della specifica (attesa UTF-8).';
    expect(normalizeOpenApi('openapi: 3.0.0\ninfo: {title: caf\uFFFD}\npaths: {}\n').warnings).toContain(msg);
    expect(normalizeOpenApi({ openapi: '3.0.0', info: { title: 'caf\uFFFD' }, paths: {} }).warnings).toContain(msg);
    expect(normalizeOpenApi({ openapi: '3.0.0', info: { title: 'caffè' }, paths: {} }).warnings).not.toContain(msg);
  });

  it('tokenUrl assoluto di percorso su un server con prefisso: RFC 3986 e avviso; senza prefisso nessun avviso', () => {
    const doc = (url, tokenUrl = '/token') => ({ openapi: '3.0.0', servers: [{ url }], paths: {},
      components: { securitySchemes: { o: { type: 'oauth2', flows: { password: { tokenUrl, scopes: {} } } } } } });
    const withPrefix = normalizeOpenApi(doc('https://h.example/api'));
    expect(withPrefix.auth.token_url).toBe('https://h.example/token');
    const tokenWarnings = (spec) => spec.warnings.filter(w => w.startsWith('tokenUrl'));
    expect(tokenWarnings(withPrefix)).toEqual(['tokenUrl «/token» di «o»: risolto in https://h.example/token (RFC 3986), senza il percorso «/api» '
      + 'del server https://h.example/api. Se il token si chiede sotto il server, usa https://h.example/api/token.']);
    expect(tokenWarnings(normalizeOpenApi(doc('https://h.example')))).toEqual([]);
    const relative = normalizeOpenApi(doc('https://h.example/api', 'token'));
    expect([relative.auth.token_url, tokenWarnings(relative)]).toEqual(['https://h.example/api/token', []]);
  });
});

describe('budget di lavoro dell\'import', () => {
  const timed = (fn) => { const t = performance.now(); const r = fn(); return [r, performance.now() - t]; };
  const one = (schema, components = {}) => ({ openapi: '3.0.0', components,
    paths: { '/a': { get: { responses: { 200: { description: '', content: { 'application/json': { schema } } } } } } } });

  it.each([
    // Alias YAML annidati: 866 byte che si espandono in 5^8 campi (prima 95 s).
    ['alias YAML 5^8', () => {
      let y = 'openapi: 3.0.0\ninfo: {title: x}\nx0: &a0 {type: string}\n';
      for (let i = 1; i <= 8; i += 1) y += `x${i}: &a${i} {type: object, properties: {${Array.from({ length: 5 }, (_, k) => `p${k}: *a${i - 1}`).join(', ')}}}\n`;
      return `${y}paths:\n  /a:\n    get:\n      responses:\n        "200":\n          description: ok\n          content:\n            application/json:\n              schema: *a8\n`;
    }],
    // $ref condivisi: 6 livelli x 6 proprietà (prima 10 s).
    ['$ref condivisi 6^6', () => {
      const schemas = {};
      for (let l = 0; l < 6; l += 1) {
        schemas[`L${l}`] = { type: 'object', properties: Object.fromEntries(Array.from({ length: 6 }, (_, w) => [`p${w}`, l === 5 ? { type: 'string' } : { $ref: `#/components/schemas/L${l + 1}` }])) };
      }
      return one({ $ref: '#/components/schemas/L0' }, { schemas });
    }],
    // allOf a ventaglio: 2^22 visite senza budget.
    ['allOf 2^22', () => {
      const schemas = {};
      for (let l = 0; l < 22; l += 1) {
        schemas[`A${l}`] = l === 21 ? { type: 'object', properties: { z: { type: 'string' } } }
          : { allOf: [{ $ref: `#/components/schemas/A${l + 1}` }, { $ref: `#/components/schemas/A${l + 1}` }] };
      }
      return one({ $ref: '#/components/schemas/A0' }, { schemas });
    }],
  ])('%s: sotto 500 ms, con «schema troncato»', (_label, make) => {
    const source = make();
    const [spec, ms] = timed(() => normalizeOpenApi(source));
    expect(ms).toBeLessThan(500);
    const fields = spec.endpoints[0].outputFields;
    expect(fields.length).toBeLessThanOrEqual(IMPORT_LIMITS.fieldsPerResponse);
    expect(new Set(fields.map(f => f.column)).size).toBe(fields.length);
    expect(spec.warnings.some(w => w.startsWith('Schema troncato:'))).toBe(true);
  });

  it('il budget dei nodi vale per tutto l\'import: tanti endpoint con lo stesso schema grande si fermano', () => {
    const big = { type: 'object', properties: Object.fromEntries(Array.from({ length: 1500 }, (_, i) => [`f${i}`, { type: 'string' }])) };
    const ok = { 200: { description: '', content: { 'application/json': { schema: { $ref: '#/components/schemas/Big' } } } } };
    const paths = Object.fromEntries(Array.from({ length: 100 }, (_, i) => [`/e${i}`, { get: { responses: ok } }]));
    const [spec, ms] = timed(() => normalizeOpenApi({ openapi: '3.0.0', components: { schemas: { Big: big } }, paths }));
    expect(ms).toBeLessThan(1500);
    expect(spec.endpoints[0].outputFields).toHaveLength(1500);
    expect(spec.endpoints.at(-1).outputFields).toEqual([]);
    expect(spec.warnings.filter(w => w.startsWith('Schema troncato: superato il limite di 100.000 nodi'))).toHaveLength(1);
  });
});

describe('un solo importatore', () => {
  it('nessun altro file di src legge specifiche OpenAPI/Swagger', () => {
    const offenders = sourceFiles(/\.(jsx?)$/)
      .filter(f => f.file !== 'utils/openapiImport.js')
      .filter(f => /securitySchemes|securityDefinitions|requestBody|['"]\$ref['"]|\.\$ref\b|js-yaml/.test(stripComments(f.code)))
      .map(f => f.file);
    expect(offenders).toEqual([]);
  });
});
