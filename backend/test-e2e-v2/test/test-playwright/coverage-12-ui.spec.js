const {
  assertNoFatalBrowserErrors,
  contractColumn,
  createConnection,
  createProject,
  deleteConnections,
  deleteProjects,
  edge,
  expect,
  executeProject,
  getProject,
  postgresFixtureConnectionPayload,
  previewProject,
  resultNode,
  setOutputContract,
  test,
  transformNode,
  uniqueName,
} = require('./fixtures');

test.describe.configure({ timeout: 120000 });

const COL_ID = 'pw_cov_col_id';
const COL_AMOUNT = 'pw_cov_col_amount';
const COL_STATUS = 'pw_cov_col_status';
const COL_CITY_ID = 'pw_cov_col_city_id';

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
  fileName = 'pw_output',
  format = 'csv',
  distinct = false,
  limit = null,
  columns = [],
  group_by_enabled = false,
  group_by_columns = [],
  aggregations = [],
  having_enabled = false,
  having_conditions = [],
  order_by_enabled = false,
  order_by_columns = [],
  position = { x: 760, y: 0 },
} = {}) {
  const data = {
    fileName,
    format,
    distinct,
    limit,
    columns,
    group_by_enabled,
    group_by_columns,
    aggregations,
    having_enabled,
    having_conditions,
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

function standardColumns() {
  return [
    { id: COL_ID, name: 'id', source_name: 'id', type: 'integer' },
    { id: COL_AMOUNT, name: 'amount', source_name: 'amount', type: 'real', logical: 'real' },
    { id: COL_STATUS, name: 'status', source_name: 'status', type: 'string', logical: 'string' },
    { id: COL_CITY_ID, name: 'city_id', source_name: 'city_id', type: 'integer' },
  ];
}

function contractColumns(projectId, columns = standardColumns()) {
  return columns.map((column, ordinal) => contractColumn({
    id: column.id,
    source_name: column.source_name || column.name,
    physical_name: column.name,
    logical: column.logical || column.type || 'integer',
    ordinal,
  }));
}

function projectColumns(columns = standardColumns()) {
  return columns.map((column) => ({
    id: column.id,
    column_id: column.id,
    name: column.name,
    source_name: column.source_name || column.name,
    display_name: column.name,
    type: column.type || column.logical || 'integer',
    visible: true,
  }));
}

function projectSourceNode({ nodeId, sourceProjectId, columns, position = { x: 0, y: 0 } }) {
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

function staticValuesNode({ nodeId = 'static-1', fields, position = { x: 0, y: 0 } }) {
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

function fileSourceNode({ nodeId = 'file-1', position = { x: 0, y: 0 } } = {}) {
  const columns = [
    { id: 'file-id', name: 'file_id', type: 'integer', visible: true },
    { id: 'file-name', name: 'file_name', type: 'string', visible: true },
  ];
  return {
    id: nodeId,
    type: 'file_source',
    position,
    file_source_id: '',
    file_name: 'fixture.csv',
    columns,
    data: {
      file_source_id: '',
      file_name: 'fixture.csv',
      columns,
      access_denied: true,
      access_denied_reason: 'fixture UI smoke',
    },
  };
}

function dataSourceNode({ nodeId = 'datasource-1', mode = 'source', position = { x: 0, y: 230 } } = {}) {
  const columns = [
    { id: 'ds-id', name: 'ds_id', type: 'integer', visible: true },
    { id: 'ds-value', name: 'ds_value', type: 'string', visible: true },
  ];
  return {
    id: nodeId,
    type: 'datasource_node',
    position,
    mode,
    datasource_id: '',
    file_path: '',
    columns,
    data: {
      mode,
      datasource_id: '',
      file_path: '',
      columns,
    },
  };
}

function caricaNode({ nodeId = 'carica-1', dest_type = 'db', position = { x: 1060, y: 0 } } = {}) {
  return {
    id: nodeId,
    type: 'carica',
    position,
    dest_type,
    data: {
      dest_type,
      connection_id: '',
      schema_name: '',
      table_name: '',
      write_mode: 'create',
      rest_url: '',
      rest_method: 'POST',
    },
  };
}

function customersCanvas(connectionId, { result = estraiResultNode(), extraEdges = [] } = {}) {
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
      result,
    ],
    edges: [edge('e-customers-result', 'customers', result.id), ...extraEdges],
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
        position: { x: 0, y: 260 },
      }),
      estraiResultNode({
        fileName: 'pw_cov_join',
        limit: 12,
        position: { x: 790, y: 120 },
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
        data: {
          join_type: 'left',
          source_column: 'city_id',
          target_column: 'id',
        },
      },
      edge('e-cities-result', 'cities', 'result-1'),
    ],
  };
}

async function createPostgresConnection(api, createdConnections) {
  const connectionId = await createConnection(api, postgresFixtureConnectionPayload(uniqueName('pw_cov_pg')));
  createdConnections.push(connectionId);
  return connectionId;
}

async function createContractProject(api, createdProjects, { namePrefix = 'pw_cov_contract', columns = standardColumns() } = {}) {
  const projectId = await createProject(api, {
    name: uniqueName(namePrefix),
    project_type: 'estrai',
    canvas: { nodes: [], edges: [] },
  });
  createdProjects.push(projectId);
  setOutputContract(projectId, contractColumns(projectId, columns));
  return projectId;
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

async function openArricchisci(page, projectId) {
  await page.goto(`/arricchisci/${projectId}`, { waitUntil: 'networkidle' });
  expect(new URL(page.url()).pathname).not.toBe('/login');
  await expect(page.locator('.react-flow').first()).toBeAttached();
}

async function saveEstrai(page) {
  await page.locator('.estrai-toolbar__btn--save').click();
  await expect(page.locator('.estrai-toolbar__btn--save')).toBeEnabled();
}

async function saveManipola(page) {
  await page.locator('.manipola-toolbar__btn--save').click();
  await expect(page.locator('.manipola-toolbar__btn--save')).toBeEnabled();
}

async function updateCanvas(api, projectId, canvas) {
  const resp = await api.put(`projects/${projectId}/canvas`, { data: canvas });
  expect(resp.ok(), `update canvas ${projectId}: ${resp.status()} ${await resp.text()}`).toBeTruthy();
  return resp.json();
}

function nodeById(canvas, nodeId) {
  return (canvas.nodes || []).find((node) => node.id === nodeId);
}

test('01 coverage: EstrAi join config modifica tipo join e aggiorna Query Preview', async ({ api, page }) => {
  const createdProjects = [];
  const createdConnections = [];

  try {
    const connectionId = await createPostgresConnection(api, createdConnections);
    const projectId = await createProject(api, {
      name: uniqueName('pw_cov_join_modal'),
      project_type: 'estrai',
      default_connection_id: connectionId,
      canvas: joinCanvas(connectionId),
    });
    createdProjects.push(projectId);

    await assertNoFatalBrowserErrors(page, async () => {
      await openEstrai(page, projectId);
      await expect(page.locator('.query-preview__sql')).toContainText(/LEFT JOIN/i);

      const project = await getProject(api, projectId);
      const canvas = project.canvas;
      const joinEdge = (canvas.edges || []).find((item) => item.id === 'e-customers-cities');
      expect(joinEdge, 'join edge e-customers-cities missing').toBeTruthy();
      joinEdge.join_type = 'inner';
      joinEdge.data = {
        ...(joinEdge.data || {}),
        join_type: 'inner',
      };
      await updateCanvas(api, projectId, canvas);

      await page.reload({ waitUntil: 'networkidle' });
      await expect(page.locator('.query-preview__sql')).toContainText(/INNER JOIN/i);
      await saveEstrai(page);
    });

    const saved = await getProject(api, projectId);
    const joinEdge = (saved.canvas.edges || []).find((item) => item.id === 'e-customers-cities');
    expect(joinEdge.data?.join_type || joinEdge.join_type).toBe('inner');
  } finally {
    await deleteProjects(api, createdProjects);
    await deleteConnections(api, createdConnections);
  }
});

test('02 coverage: EstrAi result options filename formato DISTINCT LIMIT salvano dopo reload', async ({ api, page }) => {
  const createdProjects = [];
  const createdConnections = [];

  try {
    const connectionId = await createPostgresConnection(api, createdConnections);
    const projectId = await createProject(api, {
      name: uniqueName('pw_cov_result_options'),
      project_type: 'estrai',
      default_connection_id: connectionId,
      canvas: customersCanvas(connectionId),
    });
    createdProjects.push(projectId);

    await assertNoFatalBrowserErrors(page, async () => {
      await openEstrai(page, projectId);
      await page.locator('.result-node__filename-display').click();
      await page.locator('.result-node__filename-input').fill('pw_cov_advanced');
      await page.locator('.result-node__filename-input').press('Enter');
      await page.locator('.result-node__format-btn--json').click();
      await page.locator('.result-node__checkbox').check({ force: true });
      await page.locator('.result-node__limit-input').fill('7');
      await saveEstrai(page);
      await page.reload({ waitUntil: 'networkidle' });
      await expect(page.locator('.result-node__filename-display')).toContainText('pw_cov_advanced.json');
      await expect(page.locator('.result-node__format-btn--json')).toHaveClass(/active/);
      await expect(page.locator('.result-node__checkbox')).toBeChecked();
      await expect(page.locator('.result-node__limit-input')).toHaveValue('7');
    });

    const result = nodeById((await getProject(api, projectId)).canvas, 'result-1');
    expect(result.fileName || result.data?.fileName).toBe('pw_cov_advanced');
    expect(result.format || result.data?.format).toBe('json');
    expect(result.distinct ?? result.data?.distinct).toBe(true);
    expect(result.limit ?? result.data?.limit).toBe(7);
  } finally {
    await deleteProjects(api, createdProjects);
    await deleteConnections(api, createdConnections);
  }
});

test('03 coverage: EstrAi HAVING renderizza UI e filtra preview aggregata', async ({ api, page }) => {
  const createdProjects = [];
  const createdConnections = [];

  try {
    const connectionId = await createPostgresConnection(api, createdConnections);
    const result = estraiResultNode({
      fileName: 'pw_cov_having',
      group_by_enabled: true,
      group_by_columns: ['status'],
      aggregations: [{ column: '*', function: 'COUNT', alias: 'customer_count' }],
      having_enabled: true,
      having_conditions: [{ function: 'COUNT', column: '*', operator: '>', value: '2' }],
      order_by_enabled: true,
      order_by_columns: [{ column: 'status', direction: 'ASC' }],
    });
    const projectId = await createProject(api, {
      name: uniqueName('pw_cov_having'),
      project_type: 'estrai',
      default_connection_id: connectionId,
      canvas: customersCanvas(connectionId, { result }),
    });
    createdProjects.push(projectId);

    await assertNoFatalBrowserErrors(page, async () => {
      await openEstrai(page, projectId);
      await expect(page.locator('.result-node')).toContainText(/HAVING/i);
      await expect(page.getByRole('checkbox', { name: /HAVING/i })).toBeChecked();
    });

    const preview = await previewProject(api, projectId, { limit: 10 });
    const rows = preview.rows || [];
    expect(rows.length).toBeGreaterThan(0);
    expect(rows.every((row) => Number(row.customer_count) > 2)).toBeTruthy();
  } finally {
    await deleteProjects(api, createdProjects);
    await deleteConnections(api, createdConnections);
  }
});

test('04 coverage: Manipola trasformazione sort resta visibile e salva config UI', async ({ api, page }) => {
  const createdProjects = [];

  try {
    const upstreamId = await createContractProject(api, createdProjects, { namePrefix: 'pw_cov_sort_upstream' });
    const columns = standardColumns();
    const projectId = await createProject(api, {
      name: uniqueName('pw_cov_sort_ui'),
      project_type: 'manipola',
      canvas: {
        nodes: [
          projectSourceNode({ nodeId: 'source-1', sourceProjectId: upstreamId, columns }),
          transformNode({
            nodeId: 'transform-sort',
            transformOp: 'sort',
            config: { columns: ['amount'], columns_column_ids: [COL_AMOUNT], ascending: false },
            inputColumns: projectColumns(columns),
            position: { x: 360, y: 0 },
          }),
          resultNode('result-1', { x: 760, y: 0 }),
        ],
        edges: [
          edge('e-source-sort', 'source-1', 'transform-sort'),
          edge('e-sort-result', 'transform-sort', 'result-1'),
        ],
      },
    });
    createdProjects.push(projectId);

    await assertNoFatalBrowserErrors(page, async () => {
      await openManipola(page, projectId);
      await expect(page.locator('.transform-node--sort')).toContainText(/Ordina|Colonne|amount/i);
      await expect(page.locator('.transform-node--sort .column-selector__tag')).toContainText('amount');
      await page.locator('.transform-node--sort .parameter-input__checkbox input').setChecked(true, { force: true });
      await saveManipola(page);
      await page.reload({ waitUntil: 'networkidle' });
      await expect(page.locator('.transform-node--sort .parameter-input__checkbox input')).toBeChecked();
    });

    const transform = nodeById((await getProject(api, projectId)).canvas, 'transform-sort');
    expect(transform.config.columns).toEqual(['amount']);
    expect(transform.config.columns_column_ids).toEqual([COL_AMOUNT]);
    expect(transform.config.ascending).toBe(true);
  } finally {
    await deleteProjects(api, createdProjects);
  }
});

test('05 coverage: Manipola Multi-Join mostra sorgenti coppie colonne e tipo join', async ({ api, page }) => {
  const createdProjects = [];

  try {
    const leftColumns = [
      { id: COL_ID, name: 'id', type: 'integer' },
      { id: 'pw_cov_left_value', name: 'left_value', type: 'string', logical: 'string' },
    ];
    const rightColumns = [
      { id: COL_ID, name: 'id', type: 'integer' },
      { id: 'pw_cov_right_value', name: 'right_value', type: 'string', logical: 'string' },
    ];
    const leftId = await createContractProject(api, createdProjects, { namePrefix: 'pw_cov_mjoin_left', columns: leftColumns });
    const rightId = await createContractProject(api, createdProjects, { namePrefix: 'pw_cov_mjoin_right', columns: rightColumns });
    const projectId = await createProject(api, {
      name: uniqueName('pw_cov_multi_join'),
      project_type: 'manipola',
      canvas: {
        nodes: [
          projectSourceNode({ nodeId: 'source-left', sourceProjectId: leftId, columns: leftColumns, position: { x: 0, y: 0 } }),
          projectSourceNode({ nodeId: 'source-right', sourceProjectId: rightId, columns: rightColumns, position: { x: 0, y: 250 } }),
          {
            id: 'multi-join-1',
            type: 'multi_join',
            position: { x: 380, y: 110 },
            config: { join_pairs: [{ left: 'id', right: 'id' }], join_keys: ['id'], join_key_ids: [COL_ID], join_type: 'inner' },
            data: {
              config: { join_pairs: [{ left: 'id', right: 'id' }], join_keys: ['id'], join_key_ids: [COL_ID], join_type: 'inner' },
              inputColumns: [...projectColumns(leftColumns), ...projectColumns(rightColumns)],
            },
          },
          resultNode('result-1', { x: 800, y: 120 }),
        ],
        edges: [
          edge('e-left-multi', 'source-left', 'multi-join-1', { targetHandle: 'input-0' }),
          edge('e-right-multi', 'source-right', 'multi-join-1', { targetHandle: 'input-1' }),
          edge('e-multi-result', 'multi-join-1', 'result-1'),
        ],
      },
    });
    createdProjects.push(projectId);

    await assertNoFatalBrowserErrors(page, async () => {
      await openManipola(page, projectId);
      await expect(page.locator('.multi-join-node')).toContainText('Multi-Join');
      await expect(page.locator('.multi-join-node')).toContainText('2 sorgenti');
      await expect(page.locator('.multi-join-node__pair-select--left')).toHaveValue('id');
      await expect(page.locator('.multi-join-node__pair-select--right')).toHaveValue('id');
      await expect(page.locator('.multi-join-node__select')).toHaveValue('inner');
    });
  } finally {
    await deleteProjects(api, createdProjects);
  }
});

test('06 coverage: Carica DB UI valida errori obbligatori senza scrivere destinazione', async ({ api, page }) => {
  const createdProjects = [];

  try {
    const upstreamId = await createContractProject(api, createdProjects, { namePrefix: 'pw_cov_carica_upstream' });
    const projectId = await createProject(api, {
      name: uniqueName('pw_cov_carica_validation'),
      project_type: 'manipola',
      canvas: {
        nodes: [
          projectSourceNode({ nodeId: 'source-1', sourceProjectId: upstreamId, columns: standardColumns() }),
          resultNode('result-1', { x: 520, y: 0 }),
          caricaNode(),
        ],
        edges: [
          edge('e-source-result', 'source-1', 'result-1'),
          edge('e-result-carica', 'result-1', 'carica-1', { data: { join_type: 'carica' } }),
        ],
      },
    });
    createdProjects.push(projectId);

    await assertNoFatalBrowserErrors(page, async () => {
      await openManipola(page, projectId);
      await expect(page.locator('.carica-node')).toContainText('CARICA su DB');
      await page.locator('.result-node__validate-btn').evaluate((button) => button.click());
      await expect(page.locator('.validation-modal__body')).toContainText('Carica DB: seleziona una connessione database');
      await expect(page.locator('.validation-modal__body')).toContainText('Carica DB: seleziona uno schema di destinazione');
      await expect(page.locator('.validation-modal__body')).toContainText('Carica DB: specifica il nome della tabella');
    });
  } finally {
    await deleteProjects(api, createdProjects);
  }
});

test('07 coverage: validazione canvas segnala Output scollegato e transform orfano', async ({ api, page }) => {
  const createdProjects = [];

  try {
    const projectId = await createProject(api, {
      name: uniqueName('pw_cov_validation_errors'),
      project_type: 'manipola',
      canvas: {
        nodes: [
          transformNode({
            nodeId: 'transform-orphan',
            transformOp: 'formula',
            config: { formula: 'id * 2', new_column: 'double_id' },
            inputColumns: projectColumns(standardColumns()),
            position: { x: 0, y: 0 },
          }),
          resultNode('result-1', { x: 520, y: 0 }),
        ],
        edges: [],
      },
    });
    createdProjects.push(projectId);

    await assertNoFatalBrowserErrors(page, async () => {
      await openManipola(page, projectId);
      await page.locator('.result-node__validate-btn').evaluate((button) => button.click());
      const modal = page.getByRole('dialog', { name: /Problemi rilevati nel canvas/i });
      await expect(modal).toBeVisible();
      await expect(page.locator('.validation-modal__body')).toContainText(/Nodo Output non connesso|Output non connesso/i);
      await expect(page.locator('.validation-modal__body')).toContainText(/orfano|non collegato/i);
    });
  } finally {
    await deleteProjects(api, createdProjects);
  }
});

test('08 coverage: Arricchisci minimo monta canvas sorgente statica e output', async ({ api, page }) => {
  const createdProjects = [];

  try {
    const projectId = await createProject(api, {
      name: uniqueName('pw_cov_arricchisci_min'),
      project_type: 'arricchisci',
      canvas: {
        nodes: [
          staticValuesNode({
            fields: [
              { id: 'field-id', name: 'id', value: '1', field_type: 'integer' },
              { id: 'field-name', name: 'name', value: 'Roma', field_type: 'string' },
            ],
          }),
          resultNode('result-1', { x: 520, y: 0 }),
        ],
        edges: [edge('e-static-result', 'static-1', 'result-1')],
      },
    });
    createdProjects.push(projectId);

    await assertNoFatalBrowserErrors(page, async () => {
      await openArricchisci(page, projectId);
      await expect(page.locator('.react-flow')).toContainText(/Valori Statici/i);
      await expect(page.locator('.react-flow')).toContainText(/id/i);
      await expect(page.locator('.react-flow')).toContainText(/name/i);
      await expect(page.locator('.result-node')).toContainText(/Esegui|Output/i);
    });
  } finally {
    await deleteProjects(api, createdProjects);
  }
});

test('09 coverage: file source e datasource node montano schema e stati UI senza backend file reale', async ({ api, page }) => {
  const createdProjects = [];

  try {
    const projectId = await createProject(api, {
      name: uniqueName('pw_cov_file_datasource'),
      project_type: 'manipola',
      canvas: {
        nodes: [
          fileSourceNode({ position: { x: 0, y: 0 } }),
          dataSourceNode({ position: { x: 0, y: 300 } }),
          resultNode('result-1', { x: 620, y: 100 }),
        ],
        edges: [edge('e-file-result', 'file-1', 'result-1')],
      },
    });
    createdProjects.push(projectId);

    await assertNoFatalBrowserErrors(page, async () => {
      await openManipola(page, projectId);
      await expect(page.locator('.file-source-node')).toContainText('File Esterno');
      await expect(page.locator('.file-source-node')).toContainText('file_id');
      await expect(page.locator('.file-source-node')).toContainText('File sorgente non accessibile');
      await expect(page.locator('.dsn')).toContainText('Data Source');
      await expect(page.locator('.dsn__select')).toBeAttached();
    });
  } finally {
    await deleteProjects(api, createdProjects);
  }
});

test('10 coverage: superadmin vede intermediate_mode staff nella conferma esecuzione', async ({ api, page }) => {
  const createdProjects = [];

  try {
    const projectId = await createProject(api, {
      name: uniqueName('pw_cov_superadmin_modal'),
      project_type: 'manipola',
      canvas: {
        nodes: [
          staticValuesNode({
            fields: [
              { id: 'field-id', name: 'id', value: '1', field_type: 'integer' },
              { id: 'field-amount', name: 'amount', value: '7', field_type: 'float' },
            ],
          }),
          resultNode('result-1', { x: 520, y: 0 }),
        ],
        edges: [edge('e-static-result', 'static-1', 'result-1')],
      },
    });
    createdProjects.push(projectId);

    await assertNoFatalBrowserErrors(page, async () => {
      await openManipola(page, projectId);
      await page.locator('.result-node__execute-btn').evaluate((button) => button.click());
      await expect(page.locator('.execution-modal__config')).toContainText('intermediate_mode (staff)');
      await expect(page.locator('#execution-modal-intermediate-mode')).toBeVisible();
      await page.locator('.execution-modal__actions button').filter({ hasText: /Annulla|Chiudi/i }).click();
    });
  } finally {
    await deleteProjects(api, createdProjects);
  }
});

test('11 coverage: persistenza aggressiva formula dopo cambi rapidi e reload multipli', async ({ api, page }) => {
  const createdProjects = [];

  try {
    const upstreamId = await createContractProject(api, createdProjects, { namePrefix: 'pw_cov_persist_upstream' });
    const columns = standardColumns();
    const projectId = await createProject(api, {
      name: uniqueName('pw_cov_persistence_reload'),
      project_type: 'manipola',
      canvas: {
        nodes: [
          projectSourceNode({ nodeId: 'source-1', sourceProjectId: upstreamId, columns }),
          transformNode({
            nodeId: 'transform-formula',
            transformOp: 'formula',
            config: {
              formula: 'id + 1',
              new_column: 'calc_fast',
              formula_column_refs: [{ id: COL_ID, name: 'id' }],
            },
            inputColumns: projectColumns(columns),
            position: { x: 360, y: 0 },
          }),
          resultNode('result-1', { x: 780, y: 0 }),
        ],
        edges: [
          edge('e-source-formula', 'source-1', 'transform-formula'),
          edge('e-formula-result', 'transform-formula', 'result-1'),
        ],
      },
    });
    createdProjects.push(projectId);

    await assertNoFatalBrowserErrors(page, async () => {
      await openManipola(page, projectId);
      const textarea = page.locator('.transform-node--formula textarea').first();
      await textarea.fill('id + 2');
      await textarea.fill('id + 3');
      await textarea.fill('id + 4');
      await saveManipola(page);
      await page.reload({ waitUntil: 'networkidle' });
      await page.reload({ waitUntil: 'networkidle' });
      await expect(page.locator('.transform-node--formula textarea').first()).toHaveValue('id + 4');
    });

    const transform = nodeById((await getProject(api, projectId)).canvas, 'transform-formula');
    expect(transform.config.formula).toBe('id + 4');
  } finally {
    await deleteProjects(api, createdProjects);
  }
});

test('12 coverage: download output da esecuzione CSV contiene header e dati attesi', async ({ api }) => {
  const createdProjects = [];
  const createdConnections = [];

  try {
    const connectionId = await createPostgresConnection(api, createdConnections);
    const projectId = await createProject(api, {
      name: uniqueName('pw_cov_download'),
      project_type: 'estrai',
      default_connection_id: connectionId,
      canvas: customersCanvas(connectionId, {
        result: estraiResultNode({
          fileName: 'pw_cov_download',
          limit: 3,
          order_by_enabled: true,
          order_by_columns: [{ column: 'id', direction: 'ASC' }],
        }),
      }),
    });
    createdProjects.push(projectId);

    await executeProject(api, projectId, { force_recalculate: true });
    const resp = await api.get(`projects/${projectId}/download`);
    expect(resp.ok(), `download project ${projectId}: ${resp.status()} ${await resp.text()}`).toBeTruthy();
    const text = await resp.text();
    expect(text.split(/\r?\n/)[0]).toContain('id');
    expect(text).toContain('full_name');
    expect(text).toMatch(/^1,/m);
  } finally {
    await deleteProjects(api, createdProjects);
    await deleteConnections(api, createdConnections);
  }
});
