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
import { describe, it, expect } from 'vitest';
import { normalizeOpenApi, buildConnectionPayloads, needsBaseUrl, IMPORT_AUTH_OPTIONS } from '../../src/utils/openapiImport';
import { sourceFiles, stripComments } from '../helpers/sourceScan.js';

const DIR = path.resolve(import.meta.dirname, '../fixtures/openapi-import');
const read = (name) => fs.readFileSync(path.join(DIR, name), 'utf-8');

// Riassunto confrontabile: «posizione:nome:tipo» (! = obbligatorio), «percorso>colonna:tipo».
const summary = (spec) => ({
  format: spec.format,
  baseUrl: spec.baseUrl,
  auth: spec.auth,
  candidates: spec.authCandidates.map(c => `${c.name}:${c.auth ? c.auth.type : c.reason}`),
  warnings: spec.warnings,
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
  candidates: ['petstore_auth:OAuth2 senza flusso password o client credentials', 'api_key:api_key'],
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

const V3 = 'https://petstore3.swagger.io/api/v3';
const PETSTORE_V3 = {
  format: 'openapi-3.0',
  baseUrl: V3,
  auth: { type: 'api_key', key_name: 'api_key', key_value: '', location: 'header' },
  candidates: ['petstore_auth:OAuth2 senza flusso password o client credentials', 'api_key:api_key'],
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
  format: 'openapi-3.0',
  baseUrl: 'https://anagrafe.example.org/rest',
  // tokenUrl relativo risolto sul server; scope dal requisito dell'operazione.
  auth: { type: 'oauth2_client_credentials', token_url: 'https://anagrafe.example.org/oauth/token',
    client_id: '', client_secret: '', scope: 'persone.read persone.search' },
  candidates: ['oauth:oauth2_client_credentials', 'oidc:OpenID Connect non supportato'],
  warnings: [],
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

  it('tutti i metodi HTTP, non solo get/post/put/delete', () => {
    const op = { responses: {} };
    const spec = normalizeOpenApi({ openapi: '3.0.0', paths: { '/x': {
      get: op, put: op, post: op, delete: op, patch: op, head: op, options: op, trace: op, parameters: [], summary: 's',
    } } });
    expect(spec.endpoints.map(e => e.method)).toEqual(['GET', 'PUT', 'POST', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS', 'TRACE']);
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

describe('un solo importatore', () => {
  it('nessun altro file di src legge specifiche OpenAPI/Swagger', () => {
    const offenders = sourceFiles(/\.(jsx?)$/)
      .filter(f => f.file !== 'utils/openapiImport.js')
      .filter(f => /securitySchemes|securityDefinitions|requestBody|['"]\$ref['"]|\.\$ref\b|js-yaml/.test(stripComments(f.code)))
      .map(f => f.file);
    expect(offenders).toEqual([]);
  });
});
