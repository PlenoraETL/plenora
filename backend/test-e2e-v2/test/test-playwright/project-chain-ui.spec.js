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
  previewProject,
  resultNode,
  setOutputContract,
  test,
  transformNode,
  uniqueName,
} = require('./fixtures');

test.describe.configure({ timeout: 90000 });

const COL_ID = 'pw_chain_col_id';
const COL_AMOUNT = 'pw_chain_col_amount';
const COL_STATUS = 'pw_chain_col_status';
const COL_EXTRA = 'pw_chain_col_extra';

function chainContract(projectId, columns) {
  return columns.map((column, ordinal) => contractColumn({
    id: column.id,
    source_name: column.source_name || column.name,
    physical_name: column.name,
    logical: column.logical || column.type || 'integer',
    ordinal,
  }));
}

function projectColumns(columns) {
  return columns.map(column => ({
    id: column.id,
    column_id: column.id,
    name: column.name,
    source_name: column.source_name || column.name,
    display_name: column.name,
    type: column.type || column.logical || 'integer',
    visible: true,
  }));
}

function fullProjectSourceNode({ nodeId = 'source-1', sourceProjectId, columns, position = { x: 0, y: 0 } }) {
  const nodeColumns = projectColumns(columns);
  return {
    id: nodeId,
    type: 'project_source',
    position,
    source_project_id: sourceProjectId,
    columns: nodeColumns,
    data: {
      source_project_id: sourceProjectId,
      columns: nodeColumns,
    },
  };
}

function staticValuesNode({ fields, nodeId = 'static-1', position = { x: 0, y: 0 } }) {
  return {
    id: nodeId,
    type: 'staticValues',
    position,
    fields,
    data: {
      label: 'Valori Statici',
      fields,
    },
  };
}

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

function estraiResultNode({ limit = null, orderBy = [], position = { x: 700, y: 0 } } = {}) {
  const data = {
    fileName: 'pw_chain_output',
    format: 'csv',
    distinct: false,
    limit,
    columns: [],
    group_by_enabled: false,
    group_by_columns: [],
    aggregations: [],
    order_by_enabled: orderBy.length > 0,
    order_by_columns: orderBy,
    having_enabled: false,
    having_conditions: [],
  };
  return {
    id: 'result-1',
    type: 'result',
    position,
    ...data,
    data,
  };
}

function contractColumnsFromProject(project) {
  return (project.output_contract?.columns || [])
    .filter(column => column.status !== 'removed')
    .map(column => ({
      id: column.id,
      name: column.physical_name || column.display_name,
      source_name: column.source_name || column.physical_name,
      type: column.type?.logical || 'str',
    }));
}

async function openManipola(page, projectId) {
  await page.goto(`/editor/manipola/${projectId}`, { waitUntil: 'networkidle' });
  expect(new URL(page.url()).pathname).not.toBe('/login');
  await expect(page.locator('.react-flow').first()).toBeAttached();
}

async function openEstrai(page, projectId) {
  await page.goto(`/project/${projectId}`, { waitUntil: 'networkidle' });
  expect(new URL(page.url()).pathname).not.toBe('/login');
  await expect(page.locator('.react-flow').first()).toBeAttached();
}

async function saveManipola(page) {
  await page.locator('.manipola-toolbar__btn--save').click();
  await expect(page.locator('.manipola-toolbar__btn--save')).toBeEnabled();
}

async function saveEstrai(page) {
  await page.locator('.estrai-toolbar__btn--save').click();
  await expect(page.locator('.estrai-toolbar__btn--save')).toBeEnabled();
}

async function executeCurrentCanvas(page) {
  const button = page.locator('.result-node__execute-btn');
  await expect(button).toBeEnabled();
  await button.evaluate(el => el.click());
  const launch = page.locator('.execution-modal__actions button').filter({ hasText: /Avvia esecuzione/i });
  try {
    await launch.waitFor({ state: 'visible', timeout: 1500 });
    await launch.click();
  } catch {
    // Il modal di conferma puo' non essere presente in alcuni ruoli/config.
  }
  await expect(page.locator('body')).toContainText(/Esecuzione completata|Operazione eseguita|Righe processate/i, { timeout: 60000 });
  const close = page.locator('.execution-modal__actions button').filter({ hasText: 'Chiudi' });
  if (await close.count()) await close.click();
}

async function createPostgresConnection(api, createdConnections) {
  const connectionId = await createConnection(api, postgresFixtureConnectionPayload(uniqueName('pw_chain_pg')));
  createdConnections.push(connectionId);
  return connectionId;
}

test('Manipola -> Manipola UI: esecuzione upstream staticValues espone contract completo a valle', async ({ api, page }) => {
  const created = [];

  try {
    const fields = [
      { id: 'field-id', name: 'id', value: '1', field_type: 'integer' },
      { id: 'field-amount', name: 'amount', value: '7', field_type: 'float' },
    ];
    const upstreamId = await createProject(api, {
      name: uniqueName('pw_chain_upstream_exec'),
      project_type: 'manipola',
      canvas: {
        nodes: [
          staticValuesNode({ fields }),
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
    created.push(upstreamId);

    await assertNoFatalBrowserErrors(page, async () => {
      await openManipola(page, upstreamId);
      await executeCurrentCanvas(page);
    });

    const sourceColumns = contractColumnsFromProject(await getProject(api, upstreamId));
    expect(sourceColumns.map(column => column.name)).toEqual(['id', 'amount', 'amount_double']);

    const downstreamId = await createProject(api, {
      name: uniqueName('pw_chain_downstream_exec'),
      project_type: 'manipola',
      canvas: {
        nodes: [
          fullProjectSourceNode({ sourceProjectId: upstreamId, columns: sourceColumns }),
          transformNode({
            nodeId: 'transform-formula',
            transformOp: 'formula',
            config: {
              formula: 'amount_double + 1',
              new_column: 'amount_plus_one',
              formula_column_refs: [{ id: sourceColumns.find(c => c.name === 'amount_double').id, name: 'amount_double' }],
            },
            inputColumns: projectColumns(sourceColumns),
            position: { x: 340, y: 0 },
          }),
          resultNode('result-1', { x: 760, y: 0 }),
        ],
        edges: [
          edge('e-source-formula', 'source-1', 'transform-formula'),
          edge('e-formula-result', 'transform-formula', 'result-1'),
        ],
      },
    });
    created.push(downstreamId);

    await assertNoFatalBrowserErrors(page, async () => {
      await openManipola(page, downstreamId);
      await expect(page.locator('.project-source-node').first()).toContainText('amount_double');
      await expect(page.locator('.transform-node--formula .parameter-input__expression-help').first()).toContainText('amount_double');
    });
  } finally {
    await deleteProjects(api, created);
  }
});

test('Manipola -> Manipola UI: rename upstream aggiorna formula downstream via id stabile', async ({ api, page }) => {
  const created = [];

  try {
    const upstreamId = await createProject(api, { name: uniqueName('pw_chain_rename_up'), project_type: 'manipola' });
    created.push(upstreamId);
    const initialColumns = [
      { id: COL_ID, name: 'id', type: 'integer' },
      { id: COL_AMOUNT, name: 'amount', type: 'real', logical: 'real' },
    ];
    setOutputContract(upstreamId, chainContract(upstreamId, initialColumns));

    const downstreamId = await createProject(api, {
      name: uniqueName('pw_chain_rename_down'),
      project_type: 'manipola',
      canvas: {
        nodes: [
          fullProjectSourceNode({ sourceProjectId: upstreamId, columns: initialColumns }),
          transformNode({
            nodeId: 'transform-formula',
            transformOp: 'formula',
            config: {
              formula: 'amount*2',
              new_column: 'calculated',
              formula_column_refs: [{ id: COL_AMOUNT, name: 'amount' }],
            },
            inputColumns: projectColumns(initialColumns),
            position: { x: 340, y: 0 },
          }),
          resultNode('result-1', { x: 760, y: 0 }),
        ],
        edges: [
          edge('e-source-formula', 'source-1', 'transform-formula'),
          edge('e-formula-result', 'transform-formula', 'result-1'),
        ],
      },
    });
    created.push(downstreamId);
    setOutputContract(upstreamId, chainContract(upstreamId, [
      { id: COL_ID, name: 'id', type: 'integer' },
      { id: COL_AMOUNT, name: 'total_amount', source_name: 'amount', type: 'real', logical: 'real' },
    ]));

    await assertNoFatalBrowserErrors(page, async () => {
      await openManipola(page, downstreamId);
      const formula = page.locator('.transform-node--formula .parameter-input__field--textarea').first();
      await expect(formula).toHaveValue('total_amount*2');
      await page.waitForTimeout(1200);
      await expect(formula).toHaveValue('total_amount*2');
    });
  } finally {
    await deleteProjects(api, created);
  }
});

test('Manipola -> Manipola UI: colonna rimossa upstream pulisce drop_columns downstream', async ({ api, page }) => {
  const created = [];

  try {
    const upstreamId = await createProject(api, { name: uniqueName('pw_chain_remove_up'), project_type: 'manipola' });
    created.push(upstreamId);
    const initialColumns = [
      { id: COL_ID, name: 'id', type: 'integer' },
      { id: COL_AMOUNT, name: 'amount', type: 'real', logical: 'real' },
      { id: COL_STATUS, name: 'status', type: 'string', logical: 'string' },
    ];
    setOutputContract(upstreamId, chainContract(upstreamId, initialColumns));

    const downstreamId = await createProject(api, {
      name: uniqueName('pw_chain_remove_down'),
      project_type: 'manipola',
      canvas: {
        nodes: [
          fullProjectSourceNode({ sourceProjectId: upstreamId, columns: initialColumns }),
          transformNode({
            nodeId: 'transform-drop',
            transformOp: 'drop_columns',
            config: { columns: ['status'], columns_column_ids: [COL_STATUS] },
            inputColumns: projectColumns(initialColumns),
            position: { x: 340, y: 0 },
          }),
          resultNode('result-1', { x: 760, y: 0 }),
        ],
        edges: [
          edge('e-source-drop', 'source-1', 'transform-drop'),
          edge('e-drop-result', 'transform-drop', 'result-1'),
        ],
      },
    });
    created.push(downstreamId);
    setOutputContract(upstreamId, chainContract(upstreamId, initialColumns.filter(column => column.id !== COL_STATUS)));

    await assertNoFatalBrowserErrors(page, async () => {
      await openManipola(page, downstreamId);
      await expect(page.locator('.transform-node--drop_columns .column-selector__tag')).toHaveCount(0);
      await expect(page.locator('.project-source-node').first()).not.toContainText('status');
      await saveManipola(page);
    });

    const saved = await getProject(api, downstreamId);
    const transform = saved.canvas.nodes.find(node => node.id === 'transform-drop');
    expect(transform.config.columns || []).toEqual([]);
    expect(transform.config.columns_column_ids || []).toEqual([]);
  } finally {
    await deleteProjects(api, created);
  }
});

test('Manipola -> Manipola UI: colonna aggiunta upstream compare nella sorgente downstream', async ({ api, page }) => {
  const created = [];

  try {
    const upstreamId = await createProject(api, { name: uniqueName('pw_chain_add_up'), project_type: 'manipola' });
    created.push(upstreamId);
    const initialColumns = [
      { id: COL_ID, name: 'id', type: 'integer' },
      { id: COL_AMOUNT, name: 'amount', type: 'real', logical: 'real' },
    ];
    setOutputContract(upstreamId, chainContract(upstreamId, initialColumns));

    const downstreamId = await createProject(api, {
      name: uniqueName('pw_chain_add_down'),
      project_type: 'manipola',
      canvas: {
        nodes: [
          fullProjectSourceNode({ sourceProjectId: upstreamId, columns: initialColumns }),
          resultNode('result-1', { x: 480, y: 0 }),
        ],
        edges: [edge('e-source-result', 'source-1', 'result-1')],
      },
    });
    created.push(downstreamId);
    setOutputContract(upstreamId, chainContract(upstreamId, [
      ...initialColumns,
      { id: COL_EXTRA, name: 'extra_note', type: 'string', logical: 'string' },
    ]));

    await assertNoFatalBrowserErrors(page, async () => {
      await openManipola(page, downstreamId);
      const source = page.locator('.project-source-node').first();
      await expect(source).toContainText('id');
      await expect(source).toContainText('amount');
      await expect(source).toContainText('extra_note');
    });
  } finally {
    await deleteProjects(api, created);
  }
});

test('EstrAi UI: ORDER BY direction salva e preview rispetta DESC', async ({ api, page }) => {
  const createdProjects = [];
  const createdConnections = [];

  try {
    const connectionId = await createPostgresConnection(api, createdConnections);
    const projectId = await createProject(api, {
      name: uniqueName('pw_chain_orderby'),
      project_type: 'estrai',
      default_connection_id: connectionId,
      canvas: {
        nodes: [
          sourceNode({
            nodeId: 'customers',
            tableName: 'customers',
            alias: 'c',
            connectionId,
            columns: [sourceColumn('id', { type: 'int' }), sourceColumn('full_name', { type: 'str' })],
          }),
          estraiResultNode({ limit: 5, orderBy: [{ column: 'id', direction: 'ASC' }] }),
        ],
        edges: [edge('e-customers-result', 'customers', 'result-1')],
      },
    });
    createdProjects.push(projectId);

    await assertNoFatalBrowserErrors(page, async () => {
      await openEstrai(page, projectId);
      await page.locator('.result-node__groupby-header').filter({ hasText: 'ORDER BY' }).click({ force: true });
      const directionButton = page.locator('.result-node__orderby-dir-btn').first();
      await expect(directionButton).toHaveText('↑');
      await directionButton.evaluate(button => button.click());
      await expect(directionButton).toHaveText('↓');
      await saveEstrai(page);
    });

    const saved = await getProject(api, projectId);
    const result = saved.canvas.nodes.find(node => node.id === 'result-1');
    expect(result.order_by_columns).toEqual([{ column: 'id', direction: 'DESC' }]);
    const preview = await previewProject(api, projectId, { limit: 10 });
    expect((preview.rows || []).map(row => row.id)).toEqual([22, 21, 20, 19, 18]);
  } finally {
    await deleteProjects(api, createdProjects);
    await deleteConnections(api, createdConnections);
  }
});

test('EstrAi UI: LIMIT salva, ricarica e preview rispetta il limite', async ({ api, page }) => {
  const createdProjects = [];
  const createdConnections = [];

  try {
    const connectionId = await createPostgresConnection(api, createdConnections);
    const projectId = await createProject(api, {
      name: uniqueName('pw_chain_limit'),
      project_type: 'estrai',
      default_connection_id: connectionId,
      canvas: {
        nodes: [
          sourceNode({
            nodeId: 'customers',
            tableName: 'customers',
            alias: 'c',
            connectionId,
            columns: [sourceColumn('id', { type: 'int' }), sourceColumn('full_name', { type: 'str' })],
          }),
          estraiResultNode({ limit: null, orderBy: [{ column: 'id', direction: 'ASC' }] }),
        ],
        edges: [edge('e-customers-result', 'customers', 'result-1')],
      },
    });
    createdProjects.push(projectId);

    await assertNoFatalBrowserErrors(page, async () => {
      await openEstrai(page, projectId);
      const limitInput = page.locator('.result-node__limit-input').first();
      await limitInput.fill('3');
      await expect(limitInput).toHaveValue('3');
      await saveEstrai(page);
      await page.reload({ waitUntil: 'networkidle' });
      await expect(page.locator('.result-node__limit-input').first()).toHaveValue('3');
    });

    const preview = await previewProject(api, projectId, { limit: 10 });
    expect((preview.rows || []).map(row => row.id)).toEqual([1, 2, 3]);
  } finally {
    await deleteProjects(api, createdProjects);
    await deleteConnections(api, createdConnections);
  }
});
