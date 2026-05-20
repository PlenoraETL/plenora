const {
  assertNoFatalBrowserErrors,
  contractColumn,
  createConnection,
  createProject,
  deleteConnections,
  deleteProjects,
  edge,
  expect,
  getProject,
  postgresFixtureConnectionPayload,
  resultNode,
  setOutputContract,
  test,
  transformNode,
  uniqueName,
} = require('./fixtures');

test.describe.configure({ timeout: 120000 });

function sourceColumn(name, { type = 'str', alias = null, visible = true } = {}) {
  return { name, type, alias, visible };
}

function sourceNode({ nodeId, tableName, alias, connectionId, columns, position = { x: 0, y: 0 } }) {
  return {
    id: nodeId,
    type: 'source',
    position,
    table_name: tableName,
    alias,
    columns,
    filters: [],
    connection_id: connectionId,
    data: {
      table_name: tableName,
      alias,
      columns,
      filters: [],
      connection_id: connectionId,
    },
  };
}

function estraiResultNode({
  nodeId = 'result-1',
  fileName = 'pw_modal_output',
  format = 'csv',
  limit = 12,
  order_by_enabled = false,
  order_by_columns = [],
  position = { x: 760, y: 0 },
} = {}) {
  const data = {
    fileName,
    format,
    distinct: false,
    limit,
    columns: [],
    group_by_enabled: false,
    group_by_columns: [],
    aggregations: [],
    having_enabled: false,
    having_conditions: [],
    order_by_enabled,
    order_by_columns,
  };
  return {
    id: nodeId,
    type: 'result',
    position,
    ...data,
    data,
  };
}

function customersCanvas(connectionId) {
  return {
    nodes: [
      sourceNode({
        nodeId: 'customers',
        tableName: 'customers',
        alias: 'c',
        connectionId,
        columns: [
          sourceColumn('id', { type: 'int' }),
          sourceColumn('city_id', { type: 'int' }),
          sourceColumn('full_name', { type: 'str' }),
          sourceColumn('status', { type: 'str' }),
          sourceColumn('age', { type: 'int' }),
        ],
      }),
      estraiResultNode({
        order_by_enabled: true,
        order_by_columns: [{ column: 'id', direction: 'ASC' }],
      }),
    ],
    edges: [edge('e-customers-result', 'customers', 'result-1')],
  };
}

function staticValuesNode({ fields, nodeId = 'static-1', position = { x: 0, y: 0 } }) {
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

function caricaNode({ nodeId = 'carica-1', dest_type = 'db', position = { x: 1060, y: 0 } } = {}) {
  const availableColumns = [
    { id: 'field-id', column_id: 'field-id', name: 'id', type: 'integer', visible: true },
    { id: 'field-amount', column_id: 'field-amount', name: 'amount', type: 'float', visible: true },
    { id: 'field-status', column_id: 'field-status', name: 'status', type: 'string', visible: true },
  ];
  return {
    id: nodeId,
    type: 'carica',
    position,
    dest_type,
    data: {
      dest_type,
      availableColumns,
      connection_id: '',
      schema_name: '',
      table_name: '',
      write_mode: 'create',
      match_columns: [],
      column_mappings: [],
      rest_url: '',
      rest_method: 'POST',
      rest_headers: [],
      rest_body_mapping: [],
      rest_batch_mode: 'batch',
      rest_batch_size: 1000,
      rest_timeout_seconds: 30,
    },
  };
}

function contractColumns(projectId) {
  return [
    contractColumn({ id: 'field-id', source_name: 'id', physical_name: 'id', logical: 'integer', ordinal: 0 }),
    contractColumn({ id: 'field-amount', source_name: 'amount', physical_name: 'amount', logical: 'float', ordinal: 1 }),
    contractColumn({ id: 'field-status', source_name: 'status', physical_name: 'status', logical: 'string', ordinal: 2 }),
  ].map(column => ({ ...column, producer_project_id: projectId }));
}

function projectColumns() {
  return [
    { id: 'field-id', column_id: 'field-id', name: 'id', source_name: 'id', display_name: 'id', type: 'integer', visible: true },
    { id: 'field-amount', column_id: 'field-amount', name: 'amount', source_name: 'amount', display_name: 'amount', type: 'float', visible: true },
    { id: 'field-status', column_id: 'field-status', name: 'status', source_name: 'status', display_name: 'status', type: 'string', visible: true },
  ];
}

function projectSourceNode({ sourceProjectId, nodeId = 'project-source-1', position = { x: 0, y: 0 } }) {
  const columns = projectColumns();
  return {
    id: nodeId,
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

async function createPostgresConnection(api, createdConnections) {
  const connectionId = await createConnection(api, postgresFixtureConnectionPayload(uniqueName('pw_modal_pg')));
  createdConnections.push(connectionId);
  return connectionId;
}

async function openEstrai(page, projectId) {
  await page.goto(`/project/${projectId}`, { waitUntil: 'networkidle' });
  expect(new URL(page.url()).pathname).not.toBe('/login');
  await expect(page.locator('.react-flow').first()).toBeAttached();
}

async function openManipola(page, projectId) {
  await page.goto(`/editor/manipola/${projectId}`, { waitUntil: 'networkidle' });
  expect(new URL(page.url()).pathname).not.toBe('/login');
  await expect(page.locator('.react-flow').first()).toBeAttached();
}

async function saveManipola(page) {
  await page.locator('.manipola-toolbar__btn--save').click();
  await expect(page.locator('.manipola-toolbar__btn--save')).toBeEnabled();
}

async function clickResultAction(page, selector) {
  const button = page.locator(selector).first();
  await expect(button).toBeEnabled();
  await button.evaluate(el => el.click());
}

test('Preview Modal: ricerca, sort, limite, refresh e tab statistiche su EstrAi', async ({ api, page }) => {
  const createdProjects = [];
  const createdConnections = [];

  try {
    const connectionId = await createPostgresConnection(api, createdConnections);
    const projectId = await createProject(api, {
      name: uniqueName('pw_modal_preview'),
      project_type: 'estrai',
      default_connection_id: connectionId,
      canvas: customersCanvas(connectionId),
    });
    createdProjects.push(projectId);

    await assertNoFatalBrowserErrors(page, async () => {
      await openEstrai(page, projectId);
      await clickResultAction(page, '.result-node__preview-btn');

      const dialog = page.getByRole('dialog').filter({ hasText: /Tabella/i }).first();
      await expect(dialog).toBeVisible({ timeout: 60000 });
      await expect(dialog.locator('.preview-modal__table')).toBeVisible();
      await expect(dialog.locator('.preview-modal__th-name', { hasText: 'full_name' })).toBeVisible();

      await dialog.locator('.preview-modal__search-input').fill('active');
      await expect(dialog.locator('.preview-modal__filter-info')).toContainText(/risultati/i);

      await dialog.locator('.preview-modal__search-clear').click();
      await dialog.locator('.preview-modal__th-name').filter({ hasText: /^id$/ }).first().click();
      await expect(dialog.locator('.preview-modal__sort-indicator').first()).toBeVisible();

      await dialog.locator('.preview-modal__limit-select').selectOption('50');
      await expect(dialog.locator('.preview-modal__refresh-btn')).toBeVisible();
      await dialog.locator('.preview-modal__refresh-btn').click();
      await expect(dialog.locator('.preview-modal__table')).toBeVisible({ timeout: 60000 });

      await dialog.locator('.pm-tab').filter({ hasText: /Statistiche/i }).click();
      await expect(dialog.locator('.pm-stat-card').filter({ hasText: 'id' }).first()).toBeVisible();
      await dialog.locator('.modal-wrapper__footer button').filter({ hasText: /Chiudi/i }).click();
    });
  } finally {
    await deleteProjects(api, createdProjects);
    await deleteConnections(api, createdConnections);
  }
});

test('Execution Modal: configurazione superadmin, lancio e riepilogo completamento', async ({ api, page }) => {
  const createdProjects = [];

  try {
    const projectId = await createProject(api, {
      name: uniqueName('pw_modal_execution'),
      project_type: 'manipola',
      canvas: {
        nodes: [
          staticValuesNode({
            fields: [
              { id: 'field-id', name: 'id', value: '1', field_type: 'integer' },
              { id: 'field-amount', name: 'amount', value: '7', field_type: 'float' },
            ],
          }),
          transformNode({
            nodeId: 'transform-formula',
            transformOp: 'formula',
            config: {
              formula: 'amount * 2',
              new_column: 'amount_double',
              formula_column_refs: [{ id: 'field-amount', name: 'amount' }],
            },
            inputColumns: [
              { id: 'field-id', column_id: 'field-id', name: 'id', type: 'integer' },
              { id: 'field-amount', column_id: 'field-amount', name: 'amount', type: 'float' },
            ],
            position: { x: 340, y: 0 },
          }),
          resultNode('result-1', { x: 760, y: 0 }),
        ],
        edges: [
          edge('e-static-formula', 'static-1', 'transform-formula'),
          edge('e-formula-result', 'transform-formula', 'result-1'),
        ],
      },
    });
    createdProjects.push(projectId);

    await assertNoFatalBrowserErrors(page, async () => {
      await openManipola(page, projectId);
      await clickResultAction(page, '.result-node__execute-btn');

      const dialog = page.getByRole('dialog').filter({ hasText: /Avvia esecuzione|intermediate_mode|Righe processate/i }).first();
      await expect(dialog).toBeVisible();
      await expect(dialog.locator('#execution-modal-intermediate-mode')).toBeVisible();
      await dialog.getByRole('button', { name: /Avvia esecuzione/i }).click();

      await expect(dialog).toContainText(/Esecuzione completata|Righe processate/i, { timeout: 60000 });
      await expect(dialog.locator('.execution-modal__stats')).toContainText(/Colonne output/i);
      await expect(dialog.locator('.execution-modal__download-btn')).toBeVisible();
      await dialog.locator('.modal-wrapper__footer button').filter({ hasText: /Chiudi/i }).click();
    });
  } finally {
    await deleteProjects(api, createdProjects);
  }
});

test('Validation Modal: sezioni errori, righe cliccabili e focus nodo', async ({ api, page }) => {
  const createdProjects = [];

  try {
    const projectId = await createProject(api, {
      name: uniqueName('pw_modal_validation'),
      project_type: 'manipola',
      canvas: {
        nodes: [
          transformNode({
            nodeId: 'transform-orphan',
            transformOp: 'formula',
            config: { formula: 'id * 2', new_column: 'double_id' },
            inputColumns: [{ id: 'field-id', column_id: 'field-id', name: 'id', type: 'integer' }],
            position: { x: 120, y: 0 },
          }),
          resultNode('result-1', { x: 620, y: 160 }),
        ],
        edges: [],
      },
    });
    createdProjects.push(projectId);

    await assertNoFatalBrowserErrors(page, async () => {
      await openManipola(page, projectId);
      await clickResultAction(page, '.result-node__validate-btn');

      const dialog = page.getByRole('dialog').filter({ hasText: /Problemi rilevati nel canvas/i }).first();
      await expect(dialog).toBeVisible();
      await expect(dialog.locator('.validation-modal__section-title--error')).toContainText(/Errori/i);
      expect(await dialog.locator('.validation-modal__issue').count()).toBeGreaterThan(0);

      const clickableIssue = dialog.locator('.validation-modal__issue[role="button"]').first();
      await expect(clickableIssue).toBeVisible();
      await clickableIssue.click();
      await expect(dialog).toBeHidden();
    });
  } finally {
    await deleteProjects(api, createdProjects);
  }
});

test('Carica DB/REST UI: switch destinazione, mapping REST, header e match columns DB', async ({ api, page }) => {
  const createdProjects = [];
  const createdConnections = [];

  try {
    const connectionId = await createPostgresConnection(api, createdConnections);
    const upstreamId = await createProject(api, {
      name: uniqueName('pw_modal_carica_upstream'),
      project_type: 'estrai',
      canvas: { nodes: [], edges: [] },
    });
    createdProjects.push(upstreamId);
    setOutputContract(upstreamId, contractColumns(upstreamId));

    const projectId = await createProject(api, {
      name: uniqueName('pw_modal_carica'),
      project_type: 'manipola',
      canvas: {
        nodes: [
          projectSourceNode({ sourceProjectId: upstreamId }),
          resultNode('result-1', { x: 520, y: 0 }),
          caricaNode(),
        ],
        edges: [
          edge('e-source-result', 'project-source-1', 'result-1'),
          edge('e-result-carica', 'result-1', 'carica-1'),
        ],
      },
    });
    createdProjects.push(projectId);

    await assertNoFatalBrowserErrors(page, async () => {
      await openManipola(page, projectId);
      const carica = page.locator('.carica-node').first();
      await expect(carica).toBeVisible();

      await carica.locator('.carica-node__dest-selector button', { hasText: 'REST' }).click();
      await expect(carica).toHaveClass(/carica-node--rest/);
      await carica.locator('input[placeholder="https://api.example.com/items"]').fill('https://example.test/items');
      await carica.locator('.carica-node__section').filter({ hasText: /^Metodo/ }).locator('select').selectOption('PATCH');

      await carica.locator('.carica-node__section-header').filter({ hasText: /Body Mapping/i }).click();
      await carica.getByRole('button', { name: /\+ Campo/i }).click();
      await carica.locator('.carica-node__mapping-select').first().selectOption('id');
      await carica.locator('input[placeholder="Campo JSON..."]').fill('customer_id');

      await carica.locator('.carica-node__section-header').filter({ hasText: /Headers e batch/i }).click();
      await carica.getByRole('button', { name: /\+ Header/i }).click();
      await carica.locator('input[placeholder="Header"]').fill('X-Test-Run');
      await carica.locator('input[placeholder="Valore"]').fill('playwright');
      await expect(carica.locator('.carica-node__preview')).toContainText('PATCH');

      await carica.locator('.carica-node__dest-selector button', { hasText: 'DB' }).click();
      await expect(carica).not.toHaveClass(/carica-node--rest/);
      await carica.locator('.carica-node__section').filter({ hasText: /Connessione Database/ }).locator('select').selectOption(connectionId);
      await expect(carica.locator('.carica-node__section').filter({ hasText: /Schema/ }).locator('select option[value="public"]')).toBeAttached({ timeout: 30000 });
      await carica.locator('.carica-node__section').filter({ hasText: /Schema/ }).locator('select').selectOption('public');
      await carica.locator('input[placeholder="Nome nuova tabella..."]').fill(uniqueName('pw_carica_target').toLowerCase());
      await carica.locator('.carica-node__section').filter({ hasText: /Modalita|Modalit/ }).locator('select').selectOption('upsert');
      await expect(carica.locator('.carica-node__warning')).toContainText(/Seleziona almeno una colonna/i);
      await carica.locator('.carica-node__match-item').filter({ hasText: /^id$/ }).locator('input[type="checkbox"]').check();
      await expect(carica.locator('.carica-node__warning')).toHaveCount(0);

      await saveManipola(page);
      const saved = await getProject(api, projectId);
      const savedCarica = (saved.canvas.nodes || []).find(node => node.id === 'carica-1');
      expect(savedCarica?.write_mode || savedCarica?.data?.write_mode).toBe('upsert');
      expect(savedCarica?.match_columns || savedCarica?.data?.match_columns || []).toContain('id');
    });
  } finally {
    await deleteProjects(api, createdProjects);
    await deleteConnections(api, createdConnections);
  }
});

test('Project List UX: ricerca progetto, conferma cancellazione annulla e poi elimina', async ({ api, page }) => {
  const createdProjects = [];

  try {
    const projectName = uniqueName('pw_modal_project_list');
    const projectId = await createProject(api, {
      name: projectName,
      project_type: 'manipola',
      canvas: { nodes: [], edges: [] },
    });
    createdProjects.push(projectId);

    await assertNoFatalBrowserErrors(page, async () => {
      await page.goto('/', { waitUntil: 'networkidle' });
      await page.getByLabel('Cerca progetti').fill(projectName);
      const card = page.locator('.pl__card').filter({ hasText: projectName }).first();
      await expect(card).toBeVisible();

      await card.locator('button').filter({ hasText: '🗑' }).click();
      let dialog = page.getByRole('dialog').filter({ hasText: /Elimina progetto/i }).first();
      await expect(dialog).toContainText(projectName);
      await dialog.getByRole('button', { name: /Annulla/i }).click();
      await expect(card).toBeVisible();

      await card.locator('button').filter({ hasText: '🗑' }).click();
      dialog = page.getByRole('dialog').filter({ hasText: /Elimina progetto/i }).first();
      await dialog.getByRole('button', { name: /^Elimina$/i }).click();
      await expect(card).toBeHidden({ timeout: 30000 });
      createdProjects.pop();
    });
  } finally {
    await deleteProjects(api, createdProjects);
  }
});
