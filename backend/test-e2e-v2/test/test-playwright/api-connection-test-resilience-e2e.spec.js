const {
  expect,
  test,
} = require('./fixtures');

test.describe.configure({ timeout: 60000 });

/**
 * Regression E2E per la robustezza del parser response su
 * `POST /api/api-connections/test`.
 *
 * Coperture:
 *  - body 200 OK ma JSON troncato (fake-rest `/error/malformed_json`):
 *    pre-fix il path enrichment batch crashava il worker su
 *    `response.json()` perche' non catturava `ValueError`. L'endpoint
 *    sincrono di test gia' aveva un fallback, ma teniamo il contratto
 *    sotto regression — qualsiasi response non-JSON deve risultare
 *    `success=false` con `error` parlante, MAI 500.
 *  - 200 OK con `Content-Type: text/plain` (`/error/wrong_content_type`):
 *    body JSON valido ma content-type sbagliato — il parser deve
 *    riconoscerlo senza crash.
 *  - 429 con Retry-After (`/error/429`): il backend non deve interpretare
 *    il body come success, deve riportare HTTP 429 nel result.
 *
 * Scope architetturale (sessione 10):
 *   `enrichment_retry_http._call_api_with_retry` (path produzione batch)
 *   ora cattura `ValueError`/`JSONDecodeError` e produce
 *   `RowError(error_type="invalid_json_response", http_status=200)`.
 *   Questo spec copre la sorella sincrona (endpoint test) per assicurare
 *   parita' di contratto user-facing tra i due path.
 *
 * Requisito infra: il backend Plenora deve essere connesso alla rete
 *   `plenora-test` per risolvere `plenora-test-fake-rest`. Se la rete
 *   non e' raggiungibile, il test si auto-skip (gli E2E v2 si avviano
 *   col profilo default che gia' include fake-rest).
 *
 * URL fake-rest interno DNS docker: `http://plenora-test-fake-rest:8080`.
 * Override via env: `PLENORA_E2E_FAKE_REST_INTERNAL_URL`.
 */

const FAKE_REST_BASE = (
  process.env.PLENORA_E2E_FAKE_REST_INTERNAL_URL ||
  'http://plenora-test-fake-rest:8080'
).replace(/\/$/, '');

async function callTestEndpoint(api, url, { method = 'GET' } = {}) {
  return api.post('api-connections/test', {
    data: {
      url,
      method,
      auth: { type: 'api_key', key_name: 'api_key', key_value: 'PLENORA_TEST_KEY', location: 'query' },
      custom_headers: {},
      test_params: {},
    },
  });
}

test('test endpoint reagisce graceful a body JSON malformato', async ({ api }) => {
  const url = `${FAKE_REST_BASE}/error/malformed_json`;
  const resp = await callTestEndpoint(api, url);

  // Il backend non deve mai propagare un 500 al client — il fix sessione 10
  // assicura che `ValueError` su `response.json()` venga catturato e
  // tradotto in payload tipizzato.
  expect(resp.status(), 'malformed_json: backend deve rispondere 200, non 5xx').toBeLessThan(500);

  const body = await resp.json();
  // Pre-fix: rischio di success=true se nessuno valida il JSON parsing.
  // Post-fix: success=false con error parlante.
  expect(body, `body: ${JSON.stringify(body).slice(0, 300)}`).toHaveProperty('success', false);
  expect(typeof body.error || typeof body.response_preview).toBe('string');
});

test('test endpoint gestisce content-type sbagliato senza crash', async ({ api }) => {
  // `/error/wrong_content_type` ritorna JSON valido con Content-Type: text/plain.
  // Il parser robusto deve riconoscerlo e popolare available_fields, oppure
  // ritornare success=false con error informativo. In nessun caso 5xx.
  const url = `${FAKE_REST_BASE}/error/wrong_content_type`;
  const resp = await callTestEndpoint(api, url);

  expect(resp.status(), 'wrong_content_type: backend non deve 5xx').toBeLessThan(500);

  const body = await resp.json();
  expect(body, `body: ${JSON.stringify(body).slice(0, 300)}`).toHaveProperty('success');
  // Indipendentemente dal flag success, NON deve essere una stacktrace leak.
  if (body.error) {
    expect(body.error).not.toMatch(/Traceback|File ".*\.py"/);
  }
});

test('test endpoint riporta HTTP 429 senza considerarlo success', async ({ api }) => {
  const url = `${FAKE_REST_BASE}/error/429`;
  const resp = await callTestEndpoint(api, url);

  expect(resp.status()).toBeLessThan(500);

  const body = await resp.json();
  expect(body.success).toBe(false);
  // Il backend deve preservare lo status code in `status_code`.
  if (body.status_code !== undefined) {
    expect(body.status_code).toBe(429);
  }
});
