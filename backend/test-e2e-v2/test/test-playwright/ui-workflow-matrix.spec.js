const {
  assertNoFatalBrowserErrors,
  contractColumn,
  createProject,
  deleteConnections,
  deleteProjects,
  edge,
  expect,
  executeProject,
  getProject,
  postgresFixtureConnectionPayload,
  setOutputContract,
  test,
  transformNode,
  uniqueName,
} = require('./fixtures');

test.describe.configure({ timeout: 120000 });

const FAKE_REST_BASE = (
  process.env.PLENORA_E2E_FAKE_REST_INTERNAL_URL ||
  'http://plenora-test-fake-rest:8080'
).replace(/\/$/, '');
const FAKE_REST_API_KEY = process.env.PLENORA_E2E_FAKE_REST_API_KEY || 'PLENORA_TEST_KEY';

function sourceColumn(name, { type = 'str', alias = null, visible = true } = {}) {
  return { name, type, alias, visible };
}

function sourceNode({ connectionId, position = { x: 80, y: 80 } }) {
  return {
    id: 'customers',
    type: 'source',
    position,
    table_name: 'customers',
    alias: 'c',
    columns: [
      sourceColumn('id', { type: 'int' }),
      sourceColumn('full_name', { type: 'str' }),
      sourceColumn('age', { type: 'int' }),
      sourceColumn('status', { type: 'str' }),
    ],
    filters: [],
    connection_id: connectionId,
    data: {
      table_name: 'customers',
      alias: 'c',
      columns: [
        sourceColumn('id', { type: 'int' }),
        sourceColumn('full_name', { type: 'str' }),
        sourceColumn('age', { type: 'int' }),
        sourceColumn('status', { type: 'str' }),
      ],
      filters: [],
      connection_id: connectionId,
    },
  };
}

function estraiResultNode() {
  const data = {
    fileName: 'pw_ui_matrix_customers',
    format: 'csv',
    distinct: false,
    limit: 10,
    columns: [],
    group_by_enabled: false,
    group_by_columns: [],
    aggregations: [],
    having_enabled: false,
    having_conditions: [],
    order_by_enabled: true,
    order_by_columns: [{ column: 'id', direction: 'ASC' }],
  };
  return {
    id: 'result-1',
    type: 'result',
    position: { x: 760, y: 100 },
    ...data,
    data,
  };
}

function genericResultNode({
  nodeId = 'result-1',
  position = { x: 760, y: 100 },
  fileName = 'pw_ui_matrix_output',
  format = 'csv',
  isArricchisci = false,
} = {}) {
  const data = {
    ...(isArricchisci ? { isArricchisci: true } : {}),
    fileName,
    format,
    columns: [],
    order_by_enabled: false,
    order_by_columns: [],
    having_enabled: false,
    having_conditions: [],
  };
  return {
    id: nodeId,
    type: 'result',
    position,
    ...data,
    data,
  };
}

function staticValuesNode({ nodeId = 'static-1', position = { x: 80, y: 90 }, fields }) {
  const columns = fields.map(field => ({
    id: field.id,
    column_id: field.id,
    name: field.name,
    type: field.field_type || 'string',
    visible: true,
  }));
  return {
    id: nodeId,
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

function customersCanvas(connectionId) {
  return {
    nodes: [sourceNode({ connectionId }), estraiResultNode()],
    edges: [edge('e-customers-result', 'customers', 'result-1')],
  };
}

function contractColumns(projectId) {
  return [
    contractColumn({ id: 'pw_ui_col_id', source_name: 'id', physical_name: 'id', logical: 'integer', ordinal: 0 }),
    contractColumn({ id: 'pw_ui_col_amount', source_name: 'amount', physical_name: 'amount', logical: 'real', ordinal: 1 }),
    contractColumn({ id: 'pw_ui_col_status', source_name: 'status', physical_name: 'status', logical: 'string', ordinal: 2 }),
  ].map(column => ({ ...column, producer_project_id: projectId }));
}

function columnsFromContract(contract) {
  return (contract?.columns || [])
    .filter(column => column.status !== 'removed')
    .map(column => ({
      id: column.id,
      column_id: column.id,
      name: column.physical_name || column.display_name,
      source_name: column.source_name || column.physical_name || column.display_name,
      display_name: column.display_name || column.physical_name,
      type: column.type?.logical || 'string',
      visible: true,
    }));
}

function projectSourceNode({ sourceProjectId, columns, position = { x: 80, y: 90 } }) {
  return {
    id: 'source-1',
    type: 'project_source',
    position,
    source_project_id: sourceProjectId,
    columns,
    data: {
      source_project_id: sourceProjectId,
      columns,
    },
  };
}

function manipolaCanvas({ sourceProjectId, columns }) {
  return {
    nodes: [
      projectSourceNode({ sourceProjectId, columns }),
      transformNode({
        nodeId: 'transform-formula',
        transformOp: 'formula',
        config: {
          formula: 'amount * 2',
          new_column: 'amount_x2',
          formula_column_refs: [{ id: 'pw_ui_col_amount', name: 'amount' }],
        },
        inputColumns: columns,
        position: { x: 420, y: 90 },
      }),
      genericResultNode({ fileName: 'pw_ui_matrix_manipola', position: { x: 820, y: 90 } }),
    ],
    edges: [
      edge('e-source-formula', 'source-1', 'transform-formula'),
      edge('e-formula-result', 'transform-formula', 'result-1'),
    ],
  };
}

function apiEnrichNode(apiId) {
  const outputMapping = [
    { path: 'email', column: 'api_email' },
    { path: 'status', column: 'api_status' },
  ];
  return {
    id: 'api-1',
    type: 'api_enrich',
    position: { x: 360, y: 90 },
    api_id: apiId,
    param_mapping: { item_id: 'id' },
    output_mapping: outputMapping,
    data: {
      api_id: apiId,
      param_mapping: { item_id: 'id' },
      output_mapping: outputMapping,
      parameters: [],
      static_parameters: {},
    },
  };
}

function arricchisciCanvas(apiId) {
  return {
    nodes: [
      staticValuesNode({
        fields: [
          { id: 'field-id', name: 'id', value: '1', field_type: 'integer' },
          { id: 'field-city', name: 'city', value: 'Roma', field_type: 'string' },
        ],
      }),
      apiEnrichNode(apiId),
      genericResultNode({
        fileName: 'pw_ui_matrix_arricchisci',
        isArricchisci: true,
        position: { x: 760, y: 90 },
      }),
    ],
    edges: [
      edge('e-static-api', 'static-1', 'api-1'),
      edge('e-api-result', 'api-1', 'result-1'),
    ],
  };
}

async function listConnections(api) {
  const resp = await api.get('connections');
  expect(resp.ok(), `list connections: ${resp.status()} ${await resp.text()}`).toBeTruthy();
  const body = await resp.json();
  return body.connections || body || [];
}

async function findConnectionByName(api, name) {
  return (await listConnections(api)).find(connection => connection.name === name);
}

async function listProjects(api) {
  const resp = await api.get('projects');
  expect(resp.ok(), `list projects: ${resp.status()} ${await resp.text()}`).toBeTruthy();
  const body = await resp.json();
  return body.projects || [];
}

async function findProjectByName(api, name) {
  return (await listProjects(api)).find(project => project.name === name);
}

async function createFakeRestApiConnection(api) {
  const resp = await api.post('api-connections', {
    data: {
      name: uniqueName('pw_ui_matrix_fake_rest'),
      description: 'Playwright UI matrix fake REST enricher',
      api_type: 'enricher',
      category: 'e2e',
      connection_type: 'manual',
      url: `${FAKE_REST_BASE}/data/by_id/{item_id}`,
      method: 'GET',
      auth: {
        type: 'api_key',
        location: 'query',
        key_name: 'api_key',
        key_value: FAKE_REST_API_KEY,
      },
      parameters: [],
      static_parameters: {},
      output_mapping: [
        { path: 'email', column: 'api_email' },
        { path: 'status', column: 'api_status' },
      ],
      custom_headers: {},
      options: { rate_limit: 0 },
    },
  });
  expect(resp.ok(), `create api connection: ${resp.status()} ${await resp.text()}`).toBeTruthy();
  const body = await resp.json();
  expect(body?.id, `api connection id mancante: ${JSON.stringify(body).slice(0, 300)}`).toBeTruthy();
  return body.id;
}

function fieldInput(dialog, label) {
  return dialog
    .locator('.cm__modal-field')
    .filter({ hasText: label })
    .locator('input')
    .first();
}

async function createConnectionFromUi(page, api, connectionName) {
  const payload = postgresFixtureConnectionPayload(connectionName);
  await page.goto('/connections', { waitUntil: 'networkidle' });
  await page.getByRole('button', { name: /Nuova Connessione/i }).click();

  const dialog = page.getByRole('dialog', { name: /Nuova Connessione/i });
  await expect(dialog).toBeVisible();
  await fieldInput(dialog, /Nome connessione/i).fill(connectionName);
  await fieldInput(dialog, /Descrizione/i).fill(payload.description);
  await fieldInput(dialog, /Host/i).fill(payload.host);
  await fieldInput(dialog, /Porta/i).fill(String(payload.port));
  await fieldInput(dialog, /Database/i).fill(payload.database);
  await fieldInput(dialog, /Schema/i).fill(payload.db_schema);
  await fieldInput(dialog, /Username/i).fill(payload.username);
  await fieldInput(dialog, /Password/i).fill(payload.password);
  await dialog.getByRole('button', { name: /Crea Connessione/i }).click();
  await expect(dialog).toBeHidden({ timeout: 30000 });

  await expect.poll(async () => (await findConnectionByName(api, connectionName))?.id || null, {
    message: `connection ${connectionName} creata via UI`,
    timeout: 15000,
  }).toBeTruthy();

  const connection = await findConnectionByName(api, connectionName);
  expect(connection?.id, `connection ${connectionName} non trovata dopo creazione UI`).toBeTruthy();

  const row = page.locator('.cm__tr').filter({ hasText: connectionName }).first();
  await expect(row).toBeVisible();
  await row.getByRole('button', { name: /Test/i }).click();
  await expect(row.locator('.cm__status-dot')).toContainText(/OK/i, { timeout: 30000 });

  return connection.id;
}

async function createProjectFromDashboard(page, api, { name, connectionId }) {
  await page.goto('/', { waitUntil: 'networkidle' });
  await page.getByRole('button', { name: /Nuovo Progetto/i }).click();
  await expect(page.getByRole('dialog', { name: /Nuovo progetto/i })).toBeVisible();
  await page.getByRole('button', { name: /Estrai/i }).click();
  await page.locator('.pl__modal-input').fill(name);
  if (connectionId) {
    await page.locator('#new-project-connection').selectOption(connectionId);
  }
  await page.getByRole('button', { name: /Crea Progetto/i }).click();
  await expect(page.getByRole('dialog', { name: /Nuovo progetto/i })).toHaveCount(0);

  await expect.poll(async () => (await findProjectByName(api, name))?.id || null, {
    message: `project ${name} creato via dashboard`,
    timeout: 15000,
  }).toBeTruthy();
  const project = await findProjectByName(api, name);
  expect(project?.id, `project ${name} non trovato dopo creazione UI`).toBeTruthy();
  return project.id;
}

async function createTypedProjectFromDashboard(page, api, { name, type, connectionId = null }) {
  await page.goto('/', { waitUntil: 'networkidle' });
  await page.getByRole('button', { name: /Nuovo Progetto/i }).click();
  await expect(page.getByRole('dialog', { name: /Nuovo progetto/i })).toBeVisible();
  await page.getByRole('button', { name: new RegExp(type, 'i') }).click();
  await page.locator('.pl__modal-input').fill(name);
  if (type === 'Estrai' && connectionId) {
    await page.locator('#new-project-connection').selectOption(connectionId);
  }
  await page.getByRole('button', { name: /Crea Progetto/i }).click();
  await expect(page.getByRole('dialog', { name: /Nuovo progetto/i })).toHaveCount(0);

  await expect.poll(async () => (await findProjectByName(api, name))?.id || null, {
    message: `project ${name} creato via dashboard`,
    timeout: 15000,
  }).toBeTruthy();
  return (await findProjectByName(api, name)).id;
}

async function dragSidebarItemToCanvas(page, itemLocator) {
  const canvas = page.locator('.react-flow__pane').first();
  await expect(canvas).toBeVisible();
  await itemLocator.dragTo(canvas, { targetPosition: { x: 420, y: 260 } });
}

async function updateCanvas(api, projectId, canvas) {
  const resp = await api.put(`projects/${projectId}/canvas`, { data: canvas });
  expect(resp.ok(), `update canvas ${projectId}: ${resp.status()} ${await resp.text()}`).toBeTruthy();
  return resp.json();
}

async function openProject(page, projectId) {
  await page.goto(`/project/${projectId}`, { waitUntil: 'networkidle' });
  expect(new URL(page.url()).pathname).toBe(`/project/${projectId}`);
  await expect(page.locator('.react-flow').first()).toBeAttached();
}

async function openManipola(page, projectId) {
  await page.goto(`/editor/manipola/${projectId}`, { waitUntil: 'networkidle' });
  expect(new URL(page.url()).pathname).toBe(`/editor/manipola/${projectId}`);
  await expect(page.locator('.react-flow').first()).toBeAttached();
}

async function openArricchisci(page, projectId) {
  await page.goto(`/arricchisci/${projectId}`, { waitUntil: 'networkidle' });
  expect(new URL(page.url()).pathname).toBe(`/arricchisci/${projectId}`);
  await expect(page.locator('.react-flow').first()).toBeAttached();
}

async function launchIfNeeded(page) {
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const launchButton = page
      .locator('.execution-modal__actions button, .modal-wrapper__footer button')
      .filter({ hasText: /Avvia esecuzione|^.*Esegui$/i });
    try {
      await launchButton.waitFor({ state: 'visible', timeout: 1500 });
      await launchButton.first().click();
    } catch {
      return;
    }
  }
}

async function closePreview(dialog) {
  await dialog.locator('.modal-wrapper__footer button').filter({ hasText: /Chiudi/i }).click();
}

async function executeAndDownloadFromUi(page, expectedNamePattern) {
  await page.locator('.result-node__execute-btn').evaluate(button => button.click());
  const executionDialog = page.getByRole('dialog').filter({ hasText: /Avvia esecuzione|Esecuzione|Righe processate/i }).first();
  await expect(executionDialog).toBeVisible();
  await launchIfNeeded(page);
  await expect(executionDialog).toContainText(/Esecuzione completata|Arricchimento completato|Righe processate/i, { timeout: 60000 });
  await expect(executionDialog.locator('.execution-modal__download-btn')).toBeVisible();

  const [download] = await Promise.all([
    page.waitForEvent('download'),
    executionDialog.locator('.execution-modal__download-btn').click(),
  ]);
  expect(download.suggestedFilename()).toMatch(expectedNamePattern);
}

test('UI workflow matrix: connessione, canvas, preview, execute e download da browser', async ({ api, page }) => {
  const createdProjects = [];
  const createdConnections = [];

  try {
    const connectionName = uniqueName('pw_ui_matrix_pg');
    const projectName = uniqueName('pw_ui_matrix_estrai');

    await assertNoFatalBrowserErrors(page, async () => {
      const connectionId = await createConnectionFromUi(page, api, connectionName);
      createdConnections.push(connectionId);

      const projectId = await createProjectFromDashboard(page, api, { name: projectName, connectionId });
      createdProjects.push(projectId);
      await openProject(page, projectId);

      await page.getByPlaceholder(/Cerca tabelle e viste/i).fill('customers');
      await dragSidebarItemToCanvas(
        page,
        page.locator('.estrai-sidebar__table-item').filter({ hasText: 'customers' }).first(),
      );
      await expect(page.locator('.table-node').filter({ hasText: 'customers' })).toBeVisible();
      await page.getByRole('button', { name: /Output/i }).click();
      await expect(page.locator('.result-node').filter({ hasText: /Output/i })).toBeVisible();

      await updateCanvas(api, projectId, customersCanvas(connectionId));
      const saved = await getProject(api, projectId);
      expect(saved.canvas.nodes || []).toHaveLength(2);
      expect(saved.canvas.edges || []).toHaveLength(1);
      await openProject(page, projectId);

      await page.locator('.result-node__preview-btn').evaluate(button => button.click());
      const previewDialog = page.getByRole('dialog').filter({ hasText: /Tabella/i }).first();
      await expect(previewDialog).toBeVisible({ timeout: 60000 });
      await expect(previewDialog.locator('.preview-modal__table')).toContainText('full_name');
      await expect(previewDialog.locator('.preview-modal__table')).toContainText('Anna Rossi');
      await closePreview(previewDialog);
      await executeAndDownloadFromUi(page, /pw_ui_matrix_customers|output|csv/i);
    });
  } finally {
    await deleteProjects(api, createdProjects);
    await deleteConnections(api, createdConnections);
  }
});

test('UI workflow matrix: Manipola crea canvas, preview, execute e download da browser', async ({ api, page }) => {
  const createdProjects = [];

  try {
    const upstreamId = await createProject(api, {
      name: uniqueName('pw_ui_matrix_manipola_upstream'),
      project_type: 'manipola',
      canvas: {
        nodes: [
          staticValuesNode({
            fields: [
              { id: 'pw_ui_col_id', name: 'id', value: '1', field_type: 'integer' },
              { id: 'pw_ui_col_amount', name: 'amount', value: '7', field_type: 'float' },
              { id: 'pw_ui_col_status', name: 'status', value: 'active', field_type: 'string' },
            ],
          }),
          genericResultNode({ position: { x: 540, y: 90 }, fileName: 'pw_ui_matrix_source' }),
        ],
        edges: [edge('e-static-result', 'static-1', 'result-1')],
      },
    });
    createdProjects.push(upstreamId);
    setOutputContract(upstreamId, contractColumns(upstreamId));
    await executeProject(api, upstreamId, { force_recalculate: true });
    const upstream = await getProject(api, upstreamId);
    const columns = columnsFromContract(upstream.output_contract);

    await assertNoFatalBrowserErrors(page, async () => {
      const projectId = await createTypedProjectFromDashboard(page, api, {
        name: uniqueName('pw_ui_matrix_manipola'),
        type: 'Manipola',
      });
      createdProjects.push(projectId);

      await openManipola(page, projectId);
      await page.getByPlaceholder(/Cerca progetto/i).fill(upstream.metadata?.name || upstream.name);
      await dragSidebarItemToCanvas(
        page,
        page.locator('.sources-sidebar__item').filter({ hasText: upstream.metadata?.name || upstream.name }).first(),
      );
      await expect(page.locator('.project-source-node')).toContainText('amount');
      await page.getByRole('button', { name: /\+ Transform/i }).click();
      const transform = page.locator('.transform-node').first();
      await expect(transform).toBeVisible();
      await transform.locator('select').first().selectOption('formula');
      await page.locator('.transform-node--formula input[placeholder="Inserisci nome nuova colonna"]').fill('amount_x2');
      await page.locator('.transform-node--formula textarea').fill('amount * 2');
      await page.locator('.manipola-toolbar__btn--output').click();
      await expect(page.locator('.result-node')).toContainText(/Output Manipola|Output/i);

      await updateCanvas(api, projectId, manipolaCanvas({ sourceProjectId: upstreamId, columns }));
      await openManipola(page, projectId);

      await page.locator('.result-node__preview-btn').evaluate(button => button.click());
      const previewDialog = page.getByRole('dialog').filter({ hasText: /Tabella/i }).first();
      await expect(previewDialog).toBeVisible({ timeout: 60000 });
      await expect(previewDialog.locator('.preview-modal__table')).toContainText('amount_x2');
      await expect(previewDialog.locator('.preview-modal__table')).toContainText('14');
      await closePreview(previewDialog);
      await executeAndDownloadFromUi(page, /pw_ui_matrix_manipola|output|csv/i);
    });
  } finally {
    await deleteProjects(api, createdProjects);
  }
});

test('UI workflow matrix: Arricchisci crea canvas statico, preview, execute e download da browser', async ({ api, page }) => {
  const createdProjects = [];
  const createdApiIds = [];

  try {
    const apiId = await createFakeRestApiConnection(api);
    createdApiIds.push(apiId);

    await assertNoFatalBrowserErrors(page, async () => {
      const projectId = await createTypedProjectFromDashboard(page, api, {
        name: uniqueName('pw_ui_matrix_arricchisci'),
        type: 'Arricchisci',
      });
      createdProjects.push(projectId);

      await openArricchisci(page, projectId);
      await page.getByRole('button', { name: /Valori Statici/i }).click();
      const staticNode = page.locator('.svn').first();
      await expect(staticNode).toContainText(/Valori Statici/i);
      await staticNode.getByRole('button', { name: /Aggiungi campo/i }).click();
      await staticNode.locator('.svn__row--add .svn__input--name').fill('city');
      await staticNode.locator('.svn__row--add .svn__input--value').fill('Roma');
      await staticNode.locator('.svn__row--add .svn__del--confirm').click();
      await expect(staticNode).toContainText('city');
      await page.locator('.arricchisci-toolbar__btn--output').click();
      await expect(page.locator('.result-node')).toContainText(/Output/i);

      await updateCanvas(api, projectId, arricchisciCanvas(apiId));
      await openArricchisci(page, projectId);

      await page.locator('.result-node__preview-btn').evaluate(button => button.click());
      const previewDialog = page.getByRole('dialog').filter({ hasText: /Tabella/i }).first();
      await expect(previewDialog).toBeVisible({ timeout: 60000 });
      await expect(previewDialog.locator('.preview-modal__table')).toContainText('api_email');
      await expect(previewDialog.locator('.preview-modal__table')).toContainText('anna.rossi@example.com');
      await closePreview(previewDialog);
      await executeAndDownloadFromUi(page, /pw_ui_matrix_arricchisci|output|csv/i);
    });
  } finally {
    await deleteProjects(api, createdProjects);
    for (const id of createdApiIds.filter(Boolean).reverse()) {
      await api.delete(`api-connections/${id}`).catch(() => {});
    }
  }
});
