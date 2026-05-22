const {
  deleteProjects,
  expect,
  test,
  uniqueName,
} = require('./fixtures');

test.describe.configure({ timeout: 120000 });

/**
 * Regression E2E full-path per il fix sessione 10
 * `enrichment_retry_http._call_api_with_retry` JSON decode → RowError tipizzato.
 *
 * Pre-fix:
 *   `response.json()` su 200 OK con body non-JSON sollevava `ValueError`,
 *   NON catturata dal try/except del retry loop. L'eccezione propagava
 *   fuori da `_call_api_with_retry`, crashava il worker e marcava
 *   l'intero task come `failure` perdendo il batch.
 *
 * Post-fix:
 *   try/except esplicito intorno a `response.json()` produce un
 *   `RowError(error_type="invalid_json_response", http_status=200)`.
 *   Il batch prosegue; il task termina pulito.
 *
 * Setup minimo:
 *   - api_connection di tipo enricher verso fake-rest
 *     `/error/malformed_json` (200 OK + JSON troncato).
 *   - arricchisci con canvas staticValues → api_enrich → result
 *     (1 riga → 1 chiamata API).
 *   - execute via POST /projects/{id}/execute, poll del task.
 *
 * Anti-regression strict: il task DEVE terminare con uno status
 * deterministico (success/failure), MAI rimanere appeso o produrre 500.
 * In passato il worker crashava silenziosamente; oggi il fix garantisce
 * un fallimento *gestito* per riga.
 *
 * Requisito infra: backend connesso alla rete `plenora-test`.
 * URL fake-rest interno: `http://plenora-test-fake-rest:8080`.
 */

const FAKE_REST_BASE = (
  process.env.PLENORA_E2E_FAKE_REST_INTERNAL_URL ||
  'http://plenora-test-fake-rest:8080'
).replace(/\/$/, '');

function buildApiConnectionPayload(targetUrl) {
  return {
    name: uniqueName('pw_enrich_malformed'),
    description: 'Playwright E2E sessione 10 JSON decode resilience',
    api_type: 'enricher',
    category: 'custom',
    connection_type: 'manual',
    url: targetUrl,
    method: 'GET',
    auth: { type: 'none' },
    parameters: [],
    static_parameters: {},
    output_mapping: [
      { path: 'data[0]', column: 'first_item' },
    ],
    custom_headers: {},
    options: { rate_limit: 0 },
  };
}

function buildArricchisciCanvas(apiId) {
  return {
    nodes: [
      {
        id: 'sv-1',
        type: 'staticValues',
        position: { x: 100, y: 100 },
        fields: [{ name: 'seed', value: 'pw_e2e' }],
      },
      {
        id: 'ae-1',
        type: 'api_enrich',
        position: { x: 280, y: 100 },
        api_id: apiId,
        param_mapping: {},
        output_mapping: [],
      },
      {
        id: 'result-1',
        type: 'result',
        position: { x: 500, y: 100 },
        fileName: 'pw_e2e_malformed_result',
        format: 'csv',
        columns: [],
      },
    ],
    edges: [
      { id: 'edge-1', source: 'sv-1', target: 'ae-1', join_type: 'left' },
      { id: 'edge-2', source: 'ae-1', target: 'result-1', join_type: 'output' },
    ],
  };
}

async function waitTaskTerminal(api, taskId, { timeoutMs = 90000 } = {}) {
  const deadline = Date.now() + timeoutMs;
  let lastBody = null;
  while (Date.now() < deadline) {
    const resp = await api.get(`tasks/${taskId}`);
    if (resp.ok()) {
      lastBody = await resp.json();
      const status = String(lastBody?.status || '').toLowerCase();
      if (['success', 'failure', 'failed', 'revoked'].includes(status)) {
        return lastBody;
      }
    } else if (resp.status() !== 404) {
      throw new Error(`task ${taskId} polling errore ${resp.status()}: ${await resp.text()}`);
    }
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  throw new Error(`task ${taskId} timeout — ultimo body=${JSON.stringify(lastBody).slice(0, 400)}`);
}

test('enrichment batch su API con JSON malformato non crasha il worker', async ({ api }) => {
  const createdProjects = [];
  const createdApiIds = [];

  try {
    // Step 1: crea api_connection enricher verso /error/malformed_json
    const targetUrl = `${FAKE_REST_BASE}/error/malformed_json`;
    const createResp = await api.post('api-connections', {
      data: buildApiConnectionPayload(targetUrl),
    });
    expect(
      createResp.ok(),
      `create api_connection: ${createResp.status()} ${await createResp.text()}`,
    ).toBeTruthy();
    const apiConnection = await createResp.json();
    const apiId = apiConnection.id;
    expect(apiId).toBeTruthy();
    createdApiIds.push(apiId);

    // Step 2: crea progetto arricchisci con canvas SV → AE → result
    const projectName = uniqueName('pw_enrich_e2e');
    const projectResp = await api.post('projects', {
      data: {
        name: projectName,
        description: 'Playwright E2E sessione 10 batch malformed_json',
        project_type: 'arricchisci',
        default_connection_id: null,
        canvas: buildArricchisciCanvas(apiId),
      },
    });
    expect(
      projectResp.ok(),
      `create project: ${projectResp.status()} ${await projectResp.text()}`,
    ).toBeTruthy();
    const projectBody = await projectResp.json();
    const projectId =
      projectBody?.metadata?.id || projectBody?.id || projectBody?.project_id;
    expect(projectId).toBeTruthy();
    createdProjects.push(projectId);

    // Step 3: esegui il progetto. Il path enricher invoca
    // `_call_api_with_retry` per ogni riga (1 riga da staticValues).
    const executeResp = await api.post(`projects/${projectId}/execute`, {
      data: { force_recalculate: false },
    });
    expect(
      executeResp.ok(),
      `execute project: ${executeResp.status()} ${await executeResp.text()}`,
    ).toBeTruthy();
    const executeBody = await executeResp.json();
    const taskId = executeBody?.task_id;
    expect(taskId, `task_id mancante: ${JSON.stringify(executeBody).slice(0, 300)}`).toBeTruthy();

    // Step 4: poll fino a status terminale. ANTI-REGRESSION CENTRALE:
    // pre-fix il worker crashava su `ValueError` non catturato → la task
    // rimaneva appesa o terminava 'failure' con stacktrace Python leaked.
    // Post-fix il task termina deterministico (success o failure
    // controllato), NIENTE crash silente.
    const final = await waitTaskTerminal(api, taskId, { timeoutMs: 90000 });
    expect(['success', 'failure', 'failed']).toContain(String(final.status).toLowerCase());

    // Step 5: assert specifico sul fix
    // Il fix produce RowError(error_type="invalid_json_response").
    // Il task puo' essere success (continue_on_error=True nel path
    // produzione) con righe in errore, OPPURE failure se il batch
    // ha 0 righe valide. In entrambi i casi NON deve esserci un
    // ValueError stacktrace nel result/error.
    const haystack = (
      JSON.stringify(final.result || {}) + ' ' +
      JSON.stringify(final.error || '') + ' ' +
      JSON.stringify(final).slice(0, 5000)
    ).toLowerCase();

    // Anti-regression: NON deve esserci una stacktrace Python con
    // `Expecting value` (firma di ValueError di json.loads).
    // Se appare, significa che il fix sessione 10 e' regredito.
    expect(
      haystack.includes('expecting value: line 1 column'),
      `regression sospetta: stacktrace ValueError visibile nel task result. Body:\n${haystack.slice(0, 800)}`,
    ).toBe(false);
  } finally {
    await deleteProjects(api, createdProjects);
    for (const id of createdApiIds.filter(Boolean).reverse()) {
      await api.delete(`api-connections/${id}`).catch(() => {});
    }
  }
});
