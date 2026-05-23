const {
  assertNoFatalBrowserErrors,
  createConnection,
  createProject,
  deleteConnections,
  deleteProjects,
  edge,
  expect,
  postgresFixtureConnectionPayload,
  previewProject,
  test,
  uniqueName,
} = require('./fixtures');

test.describe.configure({ timeout: 180000 });

const FAKE_REST_HOST = (process.env.PLENORA_E2E_FAKE_REST_HOST_URL || 'http://127.0.0.1:58080').replace(/\/$/, '');
const FAKE_REST_INTERNAL = (
  process.env.PLENORA_E2E_FAKE_REST_CONTAINER_SINK_URL ||
  'http://plenora-test-fake-rest:8080/data/sink/records'
).replace(/\/$/, '');
const FAKE_REST_API_KEY = process.env.PLENORA_E2E_FAKE_REST_API_KEY || 'PLENORA_TEST_KEY';

function sourceColumn(name, { type = 'str', alias = null, visible = true } = {}) {
  return { name, type, alias, visible };
}

function sourceNode({ connectionId, columns, filters = [], position = { x: 80, y: 80 } }) {
  return {
    id: 'customers',
    type: 'source',
    position,
    table_name: 'customers',
    alias: 'c',
    columns,
    filters,
    connection_id: connectionId,
    data: {
      table_name: 'customers',
      alias: 'c',
      columns,
      filters,
      connection_id: connectionId,
    },
  };
}

function resultNode({
  nodeId = 'result-1',
  fileName = 'pw_carica_runtime_output',
  position = { x: 620, y: 80 },
  orderBy = null,
} = {}) {
  const data = {
    fileName,
    format: 'csv',
    distinct: false,
    limit: 100,
    columns: [],
    group_by_enabled: false,
    group_by_columns: [],
    aggregations: [],
    having_enabled: false,
    having_conditions: [],
    order_by_enabled: Boolean(orderBy),
    order_by_columns: orderBy ? [orderBy] : [],
  };
  return {
    id: nodeId,
    type: 'result',
    position,
    ...data,
    data,
  };
}

function activeCustomersCanvas(connectionId) {
  const columns = [
    sourceColumn('id', { type: 'int', alias: 'customer_id' }),
    sourceColumn('full_name', { type: 'str', alias: 'customer_name' }),
    sourceColumn('age', { type: 'int' }),
    sourceColumn('status', { type: 'str' }),
  ];
  return {
    nodes: [
      sourceNode({
        connectionId,
        columns,
        filters: [{ column: 'status', operator: '=', value: 'active' }],
      }),
      resultNode({
        fileName: 'pw_carica_runtime_active',
        orderBy: { column: 'id', direction: 'ASC' },
      }),
    ],
    edges: [edge('e-customers-result', 'customers', 'result-1')],
  };
}

function verifyTableCanvas(connectionId, tableName) {
  const columns = [
    sourceColumn('customer_id', { type: 'int' }),
    sourceColumn('customer_name', { type: 'str' }),
    sourceColumn('age', { type: 'int' }),
  ];
  return {
    nodes: [
      {
        id: 'loaded',
        type: 'source',
        position: { x: 80, y: 80 },
        table_name: tableName,
        alias: 'l',
        columns,
        filters: [],
        connection_id: connectionId,
        data: {
          table_name: tableName,
          alias: 'l',
          columns,
          filters: [],
          connection_id: connectionId,
        },
      },
      resultNode({
        fileName: 'pw_carica_runtime_verify',
        orderBy: { column: 'customer_id', direction: 'ASC' },
      }),
    ],
    edges: [edge('e-loaded-result', 'loaded', 'result-1')],
  };
}

function caricaDbNode({ connectionId, tableName, writeMode = 'create', position = { x: 980, y: 80 } }) {
  const availableColumns = [
    { id: 'customer_id', column_id: 'customer_id', name: 'customer_id', type: 'integer', visible: true },
    { id: 'customer_name', column_id: 'customer_name', name: 'customer_name', type: 'string', visible: true },
    { id: 'age', column_id: 'age', name: 'age', type: 'integer', visible: true },
  ];
  const data = {
    dest_type: 'db',
    connection_id: connectionId,
    schema_name: 'public',
    table_name: tableName,
    write_mode: writeMode,
    if_table_exists: 'error',
    batch_size: 100,
    transaction_mode: 'all_or_nothing',
    column_mappings: [
      { source_column: 'customer_id', dest_column: 'customer_id', dest_type: 'INTEGER' },
      { source_column: 'customer_name', dest_column: 'customer_name', dest_type: 'VARCHAR(128)' },
      { source_column: 'age', dest_column: 'age', dest_type: 'INTEGER' },
    ],
    match_columns: [],
    availableColumns,
  };
  return {
    id: 'carica-1',
    type: 'carica',
    position,
    ...data,
    data,
  };
}

function staticValuesNode({ position = { x: 80, y: 80 } } = {}) {
  const fields = [
    { id: 'field-id', name: 'id', value: '1', field_type: 'integer' },
    { id: 'field-amount', name: 'amount', value: '42.5', field_type: 'float' },
    { id: 'field-status', name: 'status', value: 'ready', field_type: 'string' },
  ];
  const columns = fields.map(field => ({
    id: field.id,
    column_id: field.id,
    name: field.name,
    type: field.field_type,
    visible: true,
  }));
  return {
    id: 'static-1',
    type: 'staticValues',
    position,
    fields,
    columns,
    data: {
      label: 'Valori Statici',
      fields,
      columns,
    },
  };
}

function staticResultNode({ fileName = 'pw_carica_runtime_static', position = { x: 540, y: 80 } } = {}) {
  return resultNode({ fileName, position });
}

function caricaRestNode() {
  const data = {
    dest_type: 'rest',
    rest_url: FAKE_REST_INTERNAL,
    rest_method: 'POST',
    rest_headers: [{ name: 'X-API-Key', value: FAKE_REST_API_KEY, secret: true }],
    rest_body_mapping: [],
    rest_batch_mode: 'batch',
    rest_batch_size: 1,
    rest_timeout_seconds: 15,
    availableColumns: [
      { id: 'field-id', column_id: 'field-id', name: 'id', type: 'integer', visible: true },
      { id: 'field-amount', column_id: 'field-amount', name: 'amount', type: 'float', visible: true },
      { id: 'field-status', column_id: 'field-status', name: 'status', type: 'string', visible: true },
    ],
  };
  return {
    id: 'carica-rest-1',
    type: 'carica',
    position: { x: 900, y: 80 },
    ...data,
    data,
  };
}

function datasourceCaricaNode({ datasourceId, datasourceName, filename, destFormat = 'csv' }) {
  const data = {
    mode: 'carica',
    datasource_id: datasourceId,
    datasource_name: datasourceName,
    dest_folder: 'e2e_final_write',
    dest_filename: filename,
    dest_format: destFormat,
    columns: [],
  };
  return {
    id: 'datasource-carica-1',
    type: 'datasource_node',
    position: { x: 900, y: 80 },
    ...data,
    data,
  };
}

function staticToCaricaCanvas(caricaNode, edgeId = 'e-result-carica') {
  return {
    nodes: [
      staticValuesNode(),
      staticResultNode(),
      caricaNode,
    ],
    edges: [
      edge('e-static-result', 'static-1', 'result-1'),
      edge(edgeId, 'result-1', caricaNode.id, { data: { join_type: 'carica' } }),
    ],
  };
}

async function openEstrai(page, projectId) {
  await page.goto(`/project/${projectId}`, { waitUntil: 'networkidle' });
  expect(new URL(page.url()).pathname).toBe(`/project/${projectId}`);
  await expect(page.locator('.react-flow').first()).toBeAttached();
}

async function openManipola(page, projectId) {
  await page.goto(`/editor/manipola/${projectId}`, { waitUntil: 'networkidle' });
  expect(new URL(page.url()).pathname).toBe(`/editor/manipola/${projectId}`);
  await expect(page.locator('.react-flow').first()).toBeAttached();
}

async function launchIfNeeded(page) {
  const launchButton = page
    .locator('.execution-modal__actions button, .modal-wrapper__footer button')
    .filter({ hasText: /Avvia esecuzione|Esegui/i })
    .first();
  try {
    await launchButton.waitFor({ state: 'visible', timeout: 3000 });
    await launchButton.click();
  } catch {
    // Some execution paths start immediately.
  }
}

async function executeFromUiAndWaitCarica(page, { expectedRowsText = /righe scritte/i } = {}) {
  await page.locator('.result-node__execute-btn').first().evaluate(button => button.click());
  const dialog = page.getByRole('dialog').filter({ hasText: /Esecuzione|Avvia esecuzione|Righe processate/i }).first();
  await expect(dialog).toBeVisible({ timeout: 30000 });
  await launchIfNeeded(page);

  await expect(dialog).toContainText(/Esecuzione completata|Righe processate/i, { timeout: 90000 });
  const caricaTask = dialog.locator('.execution-modal__carica-task').first();
  await expect(caricaTask).toBeVisible({ timeout: 60000 });
  await expect(caricaTask).toHaveClass(/execution-modal__carica-task--success/, { timeout: 90000 });
  await expect(caricaTask).toContainText(expectedRowsText);
  return dialog;
}

async function createPostgresConnection(api, createdConnections, namePrefix = 'pw_carica_runtime_pg') {
  const connectionId = await createConnection(api, postgresFixtureConnectionPayload(uniqueName(namePrefix)));
  createdConnections.push(connectionId);
  return connectionId;
}

async function countRowsInTable(api, createdProjects, connectionId, tableName) {
  const verifyId = await createProject(api, {
    name: uniqueName('pw_carica_runtime_verify'),
    project_type: 'estrai',
    default_connection_id: connectionId,
    canvas: verifyTableCanvas(connectionId, tableName),
  });
  createdProjects.push(verifyId);
  const rows = (await previewProject(api, verifyId, { limit: 500, timeoutMs: 90000 })).rows || [];
  return rows.length;
}

async function createDatasource(api, payload) {
  const resp = await api.post('datasources', { data: payload });
  expect(resp.ok(), `create datasource ${payload.name}: ${resp.status()} ${await resp.text()}`).toBeTruthy();
  const body = await resp.json();
  expect(body?.id, `datasource id mancante: ${JSON.stringify(body).slice(0, 300)}`).toBeTruthy();
  return body.id;
}

async function deleteDatasources(api, datasourceIds) {
  for (const id of [...datasourceIds].filter(Boolean).reverse()) {
    await api.delete(`datasources/${id}`).catch(() => {});
  }
}

function datasourcePayload(kind, name) {
  if (kind === 'ftp') {
    return {
      name,
      description: 'Playwright CARICA runtime FTP fixture',
      type: 'ftp',
      host: process.env.PLENORA_E2E_FTP_HOST || 'plenora-test-ftp',
      port: Number(process.env.PLENORA_E2E_FTP_PORT || 21),
      username: process.env.FTP_USER || 'tester',
      password: process.env.FTP_PASSWORD || 'plenora_ftp_pwd',
      root_path: '',
      tls: false,
      passive: true,
    };
  }
  if (kind === 'sftp') {
    return {
      name,
      description: 'Playwright CARICA runtime SFTP fixture',
      type: 'sftp',
      host: process.env.PLENORA_E2E_SFTP_HOST || 'plenora-test-sftp',
      port: Number(process.env.PLENORA_E2E_SFTP_PORT || 22),
      username: process.env.SFTP_USER || 'tester',
      password: process.env.SFTP_PASSWORD || 'plenora_sftp_pwd',
      root_path: '',
      host_key_policy: 'auto_add',
    };
  }
  throw new Error(`Unsupported datasource kind: ${kind}`);
}

async function assertDatasourceWorks(api, datasourceId, label) {
  const resp = await api.post(`datasources/${datasourceId}/test`);
  expect(resp.ok(), `${label}: datasource test HTTP ${resp.status()} ${await resp.text()}`).toBeTruthy();
  const body = await resp.json();
  expect(body?.success, `${label}: datasource test must succeed: ${JSON.stringify(body).slice(0, 300)}`).toBeTruthy();
}

async function fetchDatasourceSchema(api, datasourceId, path) {
  const resp = await api.get(`datasources/${datasourceId}/files/schema?path=${encodeURIComponent(path)}`);
  expect(resp.ok(), `datasource schema ${path}: ${resp.status()} ${await resp.text()}`).toBeTruthy();
  return resp.json();
}

async function httpJson(url, { method = 'GET', headers = {}, body = null, expected = [200] } = {}) {
  const resp = await fetch(url, {
    method,
    headers: body ? { 'Content-Type': 'application/json', ...headers } : headers,
    body: body ? JSON.stringify(body) : null,
  });
  const text = await resp.text();
  expect(expected, `${method} ${url} returned ${resp.status}: ${text}`).toContain(resp.status);
  return text ? JSON.parse(text) : {};
}

test('CARICA DB UI runtime: create e append scrivono e rileggono la tabella target', async ({ api, page }) => {
  const createdProjects = [];
  const createdConnections = [];

  try {
    const connectionId = await createPostgresConnection(api, createdConnections);
    const tableName = uniqueName('pw_carica_db_target').toLowerCase().slice(0, 48);
    const activeCanvas = activeCustomersCanvas(connectionId);

    const expectedId = await createProject(api, {
      name: uniqueName('pw_carica_runtime_expected'),
      project_type: 'estrai',
      default_connection_id: connectionId,
      canvas: activeCanvas,
    });
    createdProjects.push(expectedId);
    const expectedRows = (await previewProject(api, expectedId, { limit: 500, timeoutMs: 90000 })).rows || [];
    expect(expectedRows.length, 'source fixture active rows required').toBeGreaterThan(0);

    const createProjectId = await createProject(api, {
      name: uniqueName('pw_carica_runtime_db_create'),
      project_type: 'estrai',
      default_connection_id: connectionId,
      canvas: {
        nodes: [...activeCanvas.nodes, caricaDbNode({ connectionId, tableName, writeMode: 'create' })],
        edges: [...activeCanvas.edges, edge('e-result-carica-create', 'result-1', 'carica-1', { data: { join_type: 'carica' } })],
      },
    });
    createdProjects.push(createProjectId);

    await assertNoFatalBrowserErrors(page, async () => {
      await openEstrai(page, createProjectId);
      await expect(page.locator('.carica-node')).toContainText('CARICA su DB');
      await expect(page.locator('.carica-node')).toContainText(tableName);
      await executeFromUiAndWaitCarica(page, { expectedRowsText: new RegExp(`${expectedRows.length} righe scritte`) });
    });

    expect(await countRowsInTable(api, createdProjects, connectionId, tableName)).toBe(expectedRows.length);

    const appendProjectId = await createProject(api, {
      name: uniqueName('pw_carica_runtime_db_append'),
      project_type: 'estrai',
      default_connection_id: connectionId,
      canvas: {
        nodes: [...activeCanvas.nodes, caricaDbNode({ connectionId, tableName, writeMode: 'append' })],
        edges: [...activeCanvas.edges, edge('e-result-carica-append', 'result-1', 'carica-1', { data: { join_type: 'carica' } })],
      },
    });
    createdProjects.push(appendProjectId);

    await assertNoFatalBrowserErrors(page, async () => {
      await openEstrai(page, appendProjectId);
      await executeFromUiAndWaitCarica(page, { expectedRowsText: new RegExp(`${expectedRows.length} righe scritte`) });
    });

    expect(await countRowsInTable(api, createdProjects, connectionId, tableName)).toBe(expectedRows.length * 2);
  } finally {
    await deleteProjects(api, createdProjects);
    await deleteConnections(api, createdConnections);
  }
});

test('CARICA REST UI runtime: execute da browser scrive sul fake REST sink', async ({ api, page }) => {
  const createdProjects = [];

  try {
    await httpJson(`${FAKE_REST_HOST}/data/sink/records`, {
      method: 'POST',
      headers: { 'X-API-Key': FAKE_REST_API_KEY, 'X-Test-Reset': '1' },
      body: { records: [] },
      expected: [201],
    });

    const projectId = await createProject(api, {
      name: uniqueName('pw_carica_runtime_rest'),
      project_type: 'manipola',
      canvas: staticToCaricaCanvas(caricaRestNode(), 'e-result-carica-rest'),
    });
    createdProjects.push(projectId);

    await assertNoFatalBrowserErrors(page, async () => {
      await openManipola(page, projectId);
      await expect(page.locator('.carica-node--rest')).toContainText('CARICA su REST');
      await expect(page.locator('.carica-node--rest')).toContainText('POST');
      await executeFromUiAndWaitCarica(page, { expectedRowsText: /1 righe scritte|1 riga scritta/i });
    });
  } finally {
    await deleteProjects(api, createdProjects);
  }
});

for (const kind of ['ftp', 'sftp']) {
  test(`CARICA datasource UI runtime: execute da browser scrive file ${kind.toUpperCase()} leggibile`, async ({ api, page }) => {
    const createdProjects = [];
    const createdDatasources = [];

    try {
      const datasourceName = uniqueName(`pw_carica_runtime_${kind}`);
      const datasourceId = await createDatasource(api, datasourcePayload(kind, datasourceName));
      createdDatasources.push(datasourceId);
      await assertDatasourceWorks(api, datasourceId, kind);

      const filename = uniqueName(`pw_${kind}_carica`).toLowerCase().slice(0, 48);
      const projectId = await createProject(api, {
        name: uniqueName(`pw_carica_runtime_${kind}`),
        project_type: 'manipola',
        canvas: staticToCaricaCanvas(
          datasourceCaricaNode({ datasourceId, datasourceName, filename, destFormat: 'csv' }),
          `e-result-carica-${kind}`,
        ),
      });
      createdProjects.push(projectId);

      await assertNoFatalBrowserErrors(page, async () => {
        await openManipola(page, projectId);
        await expect(page.locator('.dsn--carica')).toContainText('Carica');
        await expect(page.locator('.dsn--carica')).toContainText(datasourceName);
        await executeFromUiAndWaitCarica(page, { expectedRowsText: /1 righe scritte|1 riga scritta/i });
      });

      const writtenPath = `e2e_final_write/${filename}.csv`;
      const schema = await fetchDatasourceSchema(api, datasourceId, writtenPath);
      const columns = new Set((schema.columns || []).map(column => String(column.name)));
      expect(columns.has('id'), `${kind}: written file schema must include id`).toBeTruthy();
      expect(columns.has('amount'), `${kind}: written file schema must include amount`).toBeTruthy();
      expect(columns.has('status'), `${kind}: written file schema must include status`).toBeTruthy();
    } finally {
      await deleteProjects(api, createdProjects);
      await deleteDatasources(api, createdDatasources);
    }
  });
}
