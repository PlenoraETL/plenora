const {
  assertNoFatalBrowserErrors,
  createConnection,
  createProject,
  deleteConnections,
  deleteProjects,
  edge,
  expect,
  getProject,
  postgresFixtureConnectionPayload,
  previewProject,
  projectSourceNode,
  resultNode: manipolaResultNode,
  test,
  transformNode,
  uniqueName,
} = require('./fixtures');

test.describe.configure({ timeout: 90000 });

function sourceColumn(name, { type = 'str', alias = null, visible = true } = {}) {
  return { name, type, alias, visible };
}

function sourceNode({ nodeId, tableName, alias, connectionId, columns, filters = [], position = { x: 0, y: 0 } }) {
  return {
    id: nodeId,
    type: 'source',
    position,
    table_name: tableName,
    alias,
    columns,
    filters,
    connection_id: connectionId,
    data: {
      table_name: tableName,
      alias,
      columns,
      filters,
      connection_id: connectionId,
    },
  };
}

function estraiResultNode({
  nodeId = 'result-1',
  fileName = 'output',
  limit = null,
  order_by_enabled = false,
  order_by_columns = [],
  group_by_enabled = false,
  group_by_columns = [],
  aggregations = [],
  position = { x: 700, y: 0 },
} = {}) {
  const data = {
    fileName,
    format: 'csv',
    distinct: false,
    limit,
    columns: [],
    group_by_enabled,
    group_by_columns,
    aggregations,
    order_by_enabled,
    order_by_columns,
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

function customersColumns({ aliases = false } = {}) {
  return [
    sourceColumn('id', { type: 'int', alias: aliases ? 'customer_id' : null }),
    sourceColumn('full_name', { type: 'str', alias: aliases ? 'customer_name' : null }),
    sourceColumn('age', { type: 'int' }),
    sourceColumn('status', { type: 'str' }),
  ];
}

function customersCanvas(connectionId, { aliases = false, filters = [], limit = 5 } = {}) {
  return {
    nodes: [
      sourceNode({
        nodeId: 'customers',
        tableName: 'customers',
        alias: 'c',
        connectionId,
        columns: customersColumns({ aliases }),
        filters,
      }),
      estraiResultNode({
        fileName: 'pw_customers',
        limit,
        order_by_enabled: true,
        order_by_columns: [{ column: 'id', direction: 'ASC' }],
      }),
    ],
    edges: [edge('e-customers-result', 'customers', 'result-1')],
  };
}

function joinCanvas(connectionId) {
  return {
    nodes: [
      sourceNode({
        nodeId: 'customers',
        tableName: 'customers',
        alias: 'c',
        connectionId,
        columns: [
          sourceColumn('id', { type: 'int', alias: 'customer_id' }),
          sourceColumn('city_id', { type: 'int' }),
          sourceColumn('full_name', { type: 'str', alias: 'customer_name' }),
        ],
        position: { x: 0, y: 0 },
      }),
      sourceNode({
        nodeId: 'cities',
        tableName: 'cities',
        alias: 'ci',
        connectionId,
        columns: [
          sourceColumn('id', { type: 'int', visible: false }),
          sourceColumn('name', { type: 'str', alias: 'city_name' }),
          sourceColumn('region_id', { type: 'int', visible: false }),
        ],
        position: { x: 0, y: 240 },
      }),
      estraiResultNode({
        fileName: 'pw_join',
        limit: 12,
        order_by_enabled: true,
        order_by_columns: [{ column: 'id', direction: 'ASC' }],
        position: { x: 760, y: 120 },
      }),
    ],
    edges: [
      {
        id: 'e-customers-cities',
        source: 'customers',
        target: 'cities',
        join_type: 'left',
        source_column: 'city_id',
        target_column: 'id',
      },
      edge('e-cities-result', 'cities', 'result-1'),
    ],
  };
}

function contractToProjectSourceColumns(contract) {
  return (contract?.columns || [])
    .filter(column => column?.status !== 'removed')
    .map(column => ({
      id: column.id,
      source_name: column.source_name || column.physical_name,
      name: column.physical_name || column.display_name,
      type: column.type?.logical || 'str',
    }));
}

async function createPostgresConnection(api, createdConnections) {
  const connectionId = await createConnection(
    api,
    postgresFixtureConnectionPayload(uniqueName('pw_estrai_pg')),
  );
  createdConnections.push(connectionId);
  return connectionId;
}

async function openEstrai(page, projectId) {
  await page.goto(`/project/${projectId}`, { waitUntil: 'networkidle' });
  expect(new URL(page.url()).pathname).not.toBe('/login');
  await expect(page.locator('.react-flow').first()).toBeAttached();
}

async function saveEstrai(page) {
  await page.locator('.estrai-toolbar__btn--save').click();
  await expect(page.locator('.estrai-toolbar__btn--save')).toBeEnabled();
}

async function executeFromUi(page) {
  const executeButton = page.locator('.result-node__execute-btn');
  await expect(executeButton).toBeEnabled();
  await executeButton.evaluate(button => button.click());
  const launchButton = page.locator('.execution-modal__actions button').filter({ hasText: /Avvia esecuzione/i });
  try {
    await launchButton.waitFor({ state: 'visible', timeout: 1500 });
    await launchButton.click();
  } catch {
    // EstrAi puo' partire direttamente se il modal di conferma non e' abilitato.
  }
  await expect(page.locator('body')).toContainText(/Esecuzione completata|Operazione eseguita|Righe processate/i, { timeout: 60000 });
  const closeButton = page.locator('.execution-modal__actions button').filter({ hasText: 'Chiudi' });
  if (await closeButton.count()) {
    await closeButton.click();
  }
}

test('EstrAi UI: alias colonna salva, ricarica e produce output_contract pubblico', async ({ api, page }) => {
  const createdProjects = [];
  const createdConnections = [];

  try {
    const connectionId = await createPostgresConnection(api, createdConnections);
    const projectId = await createProject(api, {
      name: uniqueName('pw_estrai_alias'),
      project_type: 'estrai',
      default_connection_id: connectionId,
      canvas: customersCanvas(connectionId),
    });
    createdProjects.push(projectId);

    await assertNoFatalBrowserErrors(page, async () => {
      await openEstrai(page, projectId);
      const idRow = page.locator('.table-node__column').filter({ hasText: 'id' }).first();
      await idRow.locator('.table-node__column-add-alias').click();
      await idRow.locator('.table-node__column-alias-input').fill('customer_id');
      await idRow.locator('.table-node__column-alias-input').press('Enter');
      await expect(idRow.locator('.table-node__column-alias')).toContainText('customer_id');
      await saveEstrai(page);

      await page.reload({ waitUntil: 'networkidle' });
      await expect(page.locator('.table-node__column').filter({ hasText: 'id' }).first()).toContainText('customer_id');
      await executeFromUi(page);
    });

    const contract = (await getProject(api, projectId)).output_contract || {};
    expect((contract.columns || []).map(column => column.physical_name)).toContain('customer_id');
  } finally {
    await deleteProjects(api, createdProjects);
    await deleteConnections(api, createdConnections);
  }
});

test('EstrAi UI: colonna esclusa non entra nel contract e non si propaga a Manipola', async ({ api, page }) => {
  const createdProjects = [];
  const createdConnections = [];

  try {
    const connectionId = await createPostgresConnection(api, createdConnections);
    const estraiId = await createProject(api, {
      name: uniqueName('pw_estrai_hidden_col'),
      project_type: 'estrai',
      default_connection_id: connectionId,
      canvas: customersCanvas(connectionId, { aliases: true }),
    });
    createdProjects.push(estraiId);

    await assertNoFatalBrowserErrors(page, async () => {
      await openEstrai(page, estraiId);
      const statusRow = page.locator('.table-node__column').filter({ hasText: 'status' }).first();
      await statusRow.locator('.table-node__column-checkbox').uncheck();
      await saveEstrai(page);
      await executeFromUi(page);
    });

    const contract = (await getProject(api, estraiId)).output_contract || {};
    expect((contract.columns || []).map(column => column.physical_name)).not.toContain('status');

    const sourceColumns = contractToProjectSourceColumns(contract);
    const manipolaId = await createProject(api, {
      name: uniqueName('pw_hidden_col_manipola'),
      project_type: 'manipola',
      canvas: {
        nodes: [
          projectSourceNode({
            nodeId: 'source-1',
            sourceProjectId: estraiId,
            columnId: sourceColumns[0].id,
            name: sourceColumns[0].name,
            sourceName: sourceColumns[0].source_name,
          }),
          manipolaResultNode('result-1', { x: 480, y: 0 }),
        ],
        edges: [edge('e-source-result', 'source-1', 'result-1')],
      },
    });
    createdProjects.push(manipolaId);

    await assertNoFatalBrowserErrors(page, async () => {
      await page.goto(`/editor/manipola/${manipolaId}`, { waitUntil: 'networkidle' });
      await expect(page.locator('.react-flow').first()).toBeAttached();
      await expect(page.locator('.project-source-node').first()).not.toContainText('status');
    });
  } finally {
    await deleteProjects(api, createdProjects);
    await deleteConnections(api, createdConnections);
  }
});

test('EstrAi UI: join standard LEFT produce preview e contract attesi', async ({ api, page }) => {
  const createdProjects = [];
  const createdConnections = [];

  try {
    const connectionId = await createPostgresConnection(api, createdConnections);
    const projectId = await createProject(api, {
      name: uniqueName('pw_estrai_join'),
      project_type: 'estrai',
      default_connection_id: connectionId,
      canvas: joinCanvas(connectionId),
    });
    createdProjects.push(projectId);

    await assertNoFatalBrowserErrors(page, async () => {
      await openEstrai(page, projectId);
      await expect(page.locator('.table-node').filter({ hasText: 'customers' })).toBeVisible();
      await expect(page.locator('.table-node').filter({ hasText: 'cities' })).toBeVisible();
      await executeFromUi(page);
    });

    const preview = await previewProject(api, projectId, { limit: 20 });
    const rows = preview.rows || [];
    expect(rows).toHaveLength(12);
    expect(rows.slice(0, 5).map(row => [row.customer_id, row.customer_name, row.city_name])).toEqual([
      [1, 'Anna Rossi', 'Milano'],
      [2, 'Marco Bianchi', 'Milano'],
      [3, 'Giulia Verdi', 'Milano'],
      [4, 'Paolo Ferrari', 'Brescia'],
      [5, 'Laura Romano', 'Roma'],
    ]);
    expect(rows[11].city_name).toBeNull();

    const contract = (await getProject(api, projectId)).output_contract || {};
    expect((contract.columns || []).map(column => column.physical_name)).toEqual([
      'customer_id',
      'city_id',
      'customer_name',
      'city_name',
    ]);
  } finally {
    await deleteProjects(api, createdProjects);
    await deleteConnections(api, createdConnections);
  }
});

test('EstrAi UI: filtro WHERE configurato da modale produce righe attese', async ({ api, page }) => {
  const createdProjects = [];
  const createdConnections = [];

  try {
    const connectionId = await createPostgresConnection(api, createdConnections);
    const projectId = await createProject(api, {
      name: uniqueName('pw_estrai_filter'),
      project_type: 'estrai',
      default_connection_id: connectionId,
      canvas: customersCanvas(connectionId, { aliases: true, limit: 5 }),
    });
    createdProjects.push(projectId);

    await assertNoFatalBrowserErrors(page, async () => {
      await openEstrai(page, projectId);
      await page.locator('.table-node__filters-btn').click();
      await page.locator('.filter-modal__filter-item select').nth(0).selectOption('status');
      await page.locator('.filter-modal__filter-item select').nth(1).selectOption('=');
      await page.locator('.filter-modal__filter-item input').fill('active');
      await expect(page.locator('.filter-modal__preview-sql')).toContainText("customers.status = 'active'");
      await page.getByRole('button', { name: /Salva filtri/i }).click();
      await expect(page.locator('.table-node__filters-badge')).toHaveText('1');
      await saveEstrai(page);
    });

    const preview = await previewProject(api, projectId, { limit: 10 });
    expect((preview.rows || []).map(row => row.customer_id)).toEqual([1, 2, 4, 6, 7]);
  } finally {
    await deleteProjects(api, createdProjects);
    await deleteConnections(api, createdConnections);
  }
});

test('EstrAi UI: GROUP BY e COUNT configurati da Output producono aggregati attesi', async ({ api, page }) => {
  const createdProjects = [];
  const createdConnections = [];

  try {
    const connectionId = await createPostgresConnection(api, createdConnections);
    const projectId = await createProject(api, {
      name: uniqueName('pw_estrai_groupby'),
      project_type: 'estrai',
      default_connection_id: connectionId,
      canvas: {
        nodes: [
          sourceNode({
            nodeId: 'customers',
            tableName: 'customers',
            alias: 'c',
            connectionId,
            columns: [
              sourceColumn('id', { type: 'int' }),
              sourceColumn('age', { type: 'int' }),
              sourceColumn('status', { type: 'str' }),
            ],
          }),
          estraiResultNode({
            fileName: 'pw_groupby',
            order_by_enabled: true,
            order_by_columns: [{ column: 'status', direction: 'ASC' }],
          }),
        ],
        edges: [edge('e-customers-result', 'customers', 'result-1')],
      },
    });
    createdProjects.push(projectId);

    await assertNoFatalBrowserErrors(page, async () => {
      await openEstrai(page, projectId);
      await page.locator('.result-node__groupby-header').filter({ hasText: 'GROUP BY' }).click({ force: true });
      const groupByCheckbox = page.locator('.result-node__groupby-content .result-node__checkbox');
      await groupByCheckbox.evaluate(checkbox => checkbox.click());
      await expect(groupByCheckbox).toBeChecked();
      await page.locator('.result-node__groupby-dropdown').selectOption('status');
      await page.locator('.result-node__add-agg-btn').click();
      await page.locator('.result-node__agg-alias').first().fill('customer_count');
      await expect(page.locator('.result-node__groupby-preview')).toContainText('customer_count');
      await saveEstrai(page);
    });

    const preview = await previewProject(api, projectId, { limit: 10 });
    const counts = Object.fromEntries((preview.rows || []).map(row => [row.status || '<null>', Number(row.customer_count)]));
    expect(counts).toEqual({
      active: 17,
      inactive: 2,
      pending: 2,
      '<null>': 1,
    });
  } finally {
    await deleteProjects(api, createdProjects);
    await deleteConnections(api, createdConnections);
  }
});

test('EstrAi -> Manipola UI: output_contract completo diventa sorgente Manipola', async ({ api, page }) => {
  const createdProjects = [];
  const createdConnections = [];

  try {
    const connectionId = await createPostgresConnection(api, createdConnections);
    const estraiId = await createProject(api, {
      name: uniqueName('pw_estrai_to_manipola_src'),
      project_type: 'estrai',
      default_connection_id: connectionId,
      canvas: customersCanvas(connectionId, { aliases: true, limit: 5 }),
    });
    createdProjects.push(estraiId);

    await assertNoFatalBrowserErrors(page, async () => {
      await openEstrai(page, estraiId);
      await executeFromUi(page);
    });

    const contract = (await getProject(api, estraiId)).output_contract || {};
    const sourceColumns = contractToProjectSourceColumns(contract);
    expect(sourceColumns.map(column => column.name)).toEqual(['customer_id', 'customer_name', 'age', 'status']);

    const projectSource = projectSourceNode({
      nodeId: 'source-1',
      sourceProjectId: estraiId,
      columnId: sourceColumns[0].id,
      name: sourceColumns[0].name,
      sourceName: sourceColumns[0].source_name,
    });
    const fullColumns = sourceColumns.map(column => ({
      id: column.id,
      column_id: column.id,
      name: column.name,
      source_name: column.source_name,
      display_name: column.name,
      type: column.type,
      visible: true,
    }));
    projectSource.columns = fullColumns;
    projectSource.data.columns = fullColumns;

    const manipolaId = await createProject(api, {
      name: uniqueName('pw_estrai_to_manipola'),
      project_type: 'manipola',
      canvas: {
        nodes: [
          projectSource,
          transformNode({
            nodeId: 'transform-formula',
            transformOp: 'formula',
            config: {
              formula: 'age + 1',
              new_column: 'age_next',
              formula_column_refs: [{ id: sourceColumns.find(column => column.name === 'age').id, name: 'age' }],
            },
            inputColumns: fullColumns,
            position: { x: 340, y: 0 },
          }),
          manipolaResultNode('result-1', { x: 760, y: 0 }),
        ],
        edges: [
          edge('e-source-formula', 'source-1', 'transform-formula'),
          edge('e-formula-result', 'transform-formula', 'result-1'),
        ],
      },
    });
    createdProjects.push(manipolaId);

    await assertNoFatalBrowserErrors(page, async () => {
      await page.goto(`/editor/manipola/${manipolaId}`, { waitUntil: 'networkidle' });
      await expect(page.locator('.react-flow').first()).toBeAttached();
      const source = page.locator('.project-source-node').first();
      await expect(source).toContainText('customer_id');
      await expect(source).toContainText('customer_name');
      await expect(source).toContainText('age');
      await expect(source).toContainText('status');
      await expect(page.locator('.transform-node--formula .parameter-input__expression-help').first()).toContainText('customer_id');
      await expect(page.locator('.transform-node--formula .parameter-input__expression-help').first()).toContainText('status');
    });
  } finally {
    await deleteProjects(api, createdProjects);
    await deleteConnections(api, createdConnections);
  }
});
