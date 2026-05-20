const {
  assertNoFatalBrowserErrors,
  contractColumn,
  createProject,
  deleteProjects,
  edge,
  expect,
  getProject,
  resultNode,
  setOutputContract,
  test,
  transformNode,
  uniqueName,
} = require('./fixtures');

const COL_ID = 'pw_adv_col_id';
const COL_AMOUNT = 'pw_adv_col_amount';
const COL_STATUS = 'pw_adv_col_status';
const COL_LEFT_VALUE = 'pw_adv_col_left_value';
const COL_RIGHT_VALUE = 'pw_adv_col_right_value';

function contractColumns(projectId, columns = standardColumns()) {
  return columns.map((column, ordinal) => contractColumn({
    id: column.id,
    source_name: column.source_name || column.name,
    physical_name: column.name,
    logical: column.logical || column.type || 'integer',
    ordinal,
  }));
}

function standardColumns() {
  return [
    { id: COL_ID, name: 'id', source_name: 'id', type: 'integer' },
    { id: COL_AMOUNT, name: 'amount', source_name: 'amount', type: 'real', logical: 'real' },
    { id: COL_STATUS, name: 'status', source_name: 'status', type: 'string', logical: 'string' },
  ];
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

async function createUpstream(api, { namePrefix, columns = standardColumns() }) {
  const id = await createProject(api, {
    name: uniqueName(namePrefix),
    project_type: 'estrai',
  });
  setOutputContract(id, contractColumns(id, columns));
  return id;
}

async function createManipola(api, { namePrefix, canvas }) {
  return createProject(api, {
    name: uniqueName(namePrefix),
    project_type: 'manipola',
    canvas,
  });
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

function nodeById(canvas, nodeId) {
  return canvas.nodes.find((node) => node.id === nodeId);
}

test('Manipola UI: join configura chiavi SX/DX, tipo join e persiste gli id colonna', async ({ api, page }) => {
  const created = [];
  const leftColumns = [
    { id: COL_ID, name: 'id', type: 'integer' },
    { id: COL_LEFT_VALUE, name: 'left_value', type: 'string', logical: 'string' },
  ];
  const rightColumns = [
    { id: COL_ID, name: 'id', type: 'integer' },
    { id: COL_RIGHT_VALUE, name: 'right_value', type: 'string', logical: 'string' },
  ];

  try {
    const leftId = await createUpstream(api, { namePrefix: 'pw_join_left', columns: leftColumns });
    const rightId = await createUpstream(api, { namePrefix: 'pw_join_right', columns: rightColumns });
    created.push(leftId, rightId);

    const canvas = {
      nodes: [
        projectSourceNode({ nodeId: 'source-left', sourceProjectId: leftId, columns: leftColumns, position: { x: 0, y: 0 } }),
        projectSourceNode({ nodeId: 'source-right', sourceProjectId: rightId, columns: rightColumns, position: { x: 0, y: 230 } }),
        transformNode({ nodeId: 'transform-join', transformOp: 'join', config: {}, position: { x: 360, y: 100 } }),
        resultNode('result-1', { x: 780, y: 100 }),
      ],
      edges: [
        edge('e-left-join', 'source-left', 'transform-join', { targetHandle: 'input-0' }),
        edge('e-right-join', 'source-right', 'transform-join', { targetHandle: 'input-1' }),
        edge('e-join-result', 'transform-join', 'result-1'),
      ],
    };
    const downstreamId = await createManipola(api, { namePrefix: 'pw_join_manipola', canvas });
    created.push(downstreamId);

    await assertNoFatalBrowserErrors(page, async () => {
      await openManipola(page, downstreamId);
      const selects = page.locator('.transform-node--join .parameter-input__field');
      await expect(selects).toHaveCount(3);
      await selects.nth(0).selectOption('id');
      await selects.nth(1).selectOption('id');
      await selects.nth(2).selectOption('left');
      await saveManipola(page);

      await page.reload({ waitUntil: 'networkidle' });
      await expect(page.locator('.transform-node--join .parameter-input__field').nth(0)).toHaveValue('id');
      await expect(page.locator('.transform-node--join .parameter-input__field').nth(1)).toHaveValue('id');
      await expect(page.locator('.transform-node--join .parameter-input__field').nth(2)).toHaveValue('left');
    });

    const saved = await getProject(api, downstreamId);
    const transform = nodeById(saved.canvas, 'transform-join');
    expect(transform.config.left_on).toBe('id');
    expect(transform.config.right_on).toBe('id');
    expect(transform.config.how).toBe('left');
    expect(transform.config.left_on_column_id).toBe(COL_ID);
    expect(transform.config.right_on_column_id).toBe(COL_ID);
  } finally {
    await deleteProjects(api, created);
  }
});

test('Manipola UI: aggregate configura group_by e aggregazioni con column_id', async ({ api, page }) => {
  const created = [];

  try {
    const upstreamId = await createUpstream(api, { namePrefix: 'pw_aggregate_src' });
    created.push(upstreamId);
    const canvas = {
      nodes: [
        projectSourceNode({ nodeId: 'source-1', sourceProjectId: upstreamId, columns: standardColumns() }),
        transformNode({ nodeId: 'transform-aggregate', transformOp: 'aggregate', config: {}, inputColumns: projectColumns(), position: { x: 340, y: 0 } }),
        resultNode('result-1', { x: 760, y: 0 }),
      ],
      edges: [
        edge('e-source-aggregate', 'source-1', 'transform-aggregate'),
        edge('e-aggregate-result', 'transform-aggregate', 'result-1'),
      ],
    };
    const downstreamId = await createManipola(api, { namePrefix: 'pw_aggregate_manipola', canvas });
    created.push(downstreamId);

    await assertNoFatalBrowserErrors(page, async () => {
      await openManipola(page, downstreamId);
      await page.locator('.transform-node--aggregate .column-selector--multiple select').selectOption('status');
      await expect(page.locator('.transform-node--aggregate .column-selector__tag')).toContainText('status');
      await page.locator('.transform-node--aggregate .aggregation-list-input__add').click();
      await page.locator('.transform-node--aggregate .aggregation-list-input__row select').nth(0).selectOption('amount');
      await page.locator('.transform-node--aggregate .aggregation-list-input__row select').nth(1).selectOption('sum');
      await page.locator('.transform-node--aggregate .aggregation-list-input__row input[type="text"]').first().fill('amount_sum');
      await expect(page.locator('.transform-node--aggregate .aggregation-list-input__row select').nth(0)).toHaveValue('amount');
      await expect(page.locator('.transform-node--aggregate .aggregation-list-input__row select').nth(1)).toHaveValue('sum');
      await expect(page.locator('.transform-node--aggregate .aggregation-list-input__row input[type="text"]').first()).toHaveValue('amount_sum');
      await saveManipola(page);

      await page.reload({ waitUntil: 'networkidle' });
      await expect(page.locator('.transform-node--aggregate .column-selector__tag')).toContainText('status');
      await expect(page.locator('.transform-node--aggregate .aggregation-list-input__row select').nth(0)).toHaveValue('amount');
      await expect(page.locator('.transform-node--aggregate .aggregation-list-input__row select').nth(1)).toHaveValue('sum');
      await expect(page.locator('.transform-node--aggregate .aggregation-list-input__row input[type="text"]').first()).toHaveValue('amount_sum');
    });

    const saved = await getProject(api, downstreamId);
    const transform = nodeById(saved.canvas, 'transform-aggregate');
    expect(transform.config.group_by).toEqual(['status']);
    expect(transform.config.group_by_column_ids).toEqual([COL_STATUS]);
    expect(transform.config.aggregations).toEqual([
      { column: 'amount', column_id: COL_AMOUNT, function: 'sum', alias: 'amount_sum' },
    ]);
  } finally {
    await deleteProjects(api, created);
  }
});

test('Manipola UI: formula non valida mostra errore senza crash del canvas', async ({ api, page }) => {
  const created = [];

  try {
    const fields = [
      { id: 'field-id', name: 'id', value: '1', field_type: 'integer' },
      { id: 'field-amount', name: 'amount', value: '7', field_type: 'float' },
    ];
    const canvas = {
      nodes: [
        staticValuesNode({ fields }),
        transformNode({
          nodeId: 'transform-formula',
          transformOp: 'formula',
          config: { formula: 'amount *** 2', new_column: 'bad_calc' },
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
    };
    const downstreamId = await createManipola(api, { namePrefix: 'pw_formula_error', canvas });
    created.push(downstreamId);

    await assertNoFatalBrowserErrors(page, async () => {
      await openManipola(page, downstreamId);
      await page.locator('.result-node__execute-btn').click();
      await expect(page.locator('body')).toContainText(/Formula|non valida|Errore|Problemi rilevati/i, { timeout: 60000 });
      await expect(page.locator('.react-flow').first()).toBeAttached();
    });
  } finally {
    await deleteProjects(api, created);
  }
});

test('Manipola UI: contract upstream rimuove colonna e pulisce selezioni stale', async ({ api, page }) => {
  const created = [];

  try {
    const upstreamId = await createUpstream(api, { namePrefix: 'pw_stale_src' });
    created.push(upstreamId);
    const canvas = {
      nodes: [
        projectSourceNode({ nodeId: 'source-1', sourceProjectId: upstreamId, columns: standardColumns() }),
        transformNode({
          nodeId: 'transform-drop',
          transformOp: 'drop_columns',
          config: { columns: ['status'], columns_column_ids: [COL_STATUS] },
          inputColumns: projectColumns(),
          position: { x: 340, y: 0 },
        }),
        resultNode('result-1', { x: 760, y: 0 }),
      ],
      edges: [
        edge('e-source-drop', 'source-1', 'transform-drop'),
        edge('e-drop-result', 'transform-drop', 'result-1'),
      ],
    };
    const downstreamId = await createManipola(api, { namePrefix: 'pw_stale_manipola', canvas });
    created.push(downstreamId);

    setOutputContract(upstreamId, contractColumns(upstreamId, standardColumns().filter((column) => column.id !== COL_STATUS)));

    await assertNoFatalBrowserErrors(page, async () => {
      await openManipola(page, downstreamId);
      await expect(page.locator('.transform-node--drop_columns .column-selector__tag')).toHaveCount(0);
      await expect(page.locator('.project-source-node').first()).not.toContainText('status');
      await saveManipola(page);
    });

    const saved = await getProject(api, downstreamId);
    const transform = nodeById(saved.canvas, 'transform-drop');
    expect(transform.config.columns || []).toEqual([]);
    expect(transform.config.columns_column_ids || []).toEqual([]);
  } finally {
    await deleteProjects(api, created);
  }
});

test('Manipola UI: alias propagato non torna al nome legacy dopo reload e attesa', async ({ api, page }) => {
  const created = [];

  try {
    const upstreamId = await createUpstream(api, { namePrefix: 'pw_refresh_src' });
    created.push(upstreamId);
    const canvas = {
      nodes: [
        projectSourceNode({ nodeId: 'source-1', sourceProjectId: upstreamId, columns: standardColumns() }),
        transformNode({
          nodeId: 'transform-formula',
          transformOp: 'formula',
          config: {
            formula: 'id*2',
            new_column: 'calculated',
            formula_column_refs: [{ id: COL_ID, name: 'id' }],
          },
          inputColumns: projectColumns(),
          position: { x: 340, y: 0 },
        }),
        resultNode('result-1', { x: 760, y: 0 }),
      ],
      edges: [
        edge('e-source-formula', 'source-1', 'transform-formula'),
        edge('e-formula-result', 'transform-formula', 'result-1'),
      ],
    };
    const downstreamId = await createManipola(api, { namePrefix: 'pw_refresh_manipola', canvas });
    created.push(downstreamId);
    setOutputContract(upstreamId, contractColumns(upstreamId, [
      { id: COL_ID, name: 'myid', source_name: 'id', type: 'integer' },
      { id: COL_AMOUNT, name: 'amount', source_name: 'amount', type: 'real', logical: 'real' },
      { id: COL_STATUS, name: 'status', source_name: 'status', type: 'string', logical: 'string' },
    ]));

    await assertNoFatalBrowserErrors(page, async () => {
      await openManipola(page, downstreamId);
      const formula = page.locator('.transform-node--formula .parameter-input__field--textarea').first();
      await expect(formula).toHaveValue('myid*2');
      await page.waitForTimeout(1500);
      await expect(formula).toHaveValue('myid*2');

      await page.reload({ waitUntil: 'networkidle' });
      await expect(page.locator('.transform-node--formula .parameter-input__field--textarea').first()).toHaveValue('myid*2');
      await expect(page.locator('.transform-node--formula .parameter-input__expression-help').first()).toContainText('myid');
    });
  } finally {
    await deleteProjects(api, created);
  }
});

test('Manipola UI: esecuzione reale da staticValues completa e popola cronologia run', async ({ api, page }) => {
  test.setTimeout(90000);
  const created = [];

  try {
    const fields = [
      { id: 'field-id', name: 'id', value: '1', field_type: 'integer' },
      { id: 'field-amount', name: 'amount', value: '7', field_type: 'float' },
    ];
    const canvas = {
      nodes: [
        staticValuesNode({ fields }),
        resultNode('result-1', { x: 430, y: 0 }),
      ],
      edges: [
        edge('e-static-result', 'static-1', 'result-1'),
      ],
    };
    const downstreamId = await createManipola(api, { namePrefix: 'pw_real_execute', canvas });
    created.push(downstreamId);

    await assertNoFatalBrowserErrors(page, async () => {
      await openManipola(page, downstreamId);
      await page.locator('.result-node__execute-btn').click();
      await page.locator('.execution-modal__actions button').filter({ hasText: /Avvia esecuzione/i }).click();
      await expect(page.locator('body')).toContainText(/Esecuzione completata|Operazione eseguita|Righe processate/i, { timeout: 60000 });
      await page.locator('.execution-modal__actions button').filter({ hasText: 'Chiudi' }).click();

      await page.locator('.result-node__history-btn').click();
      await expect(page.locator('body')).toContainText(/Cronologia run|1 run|OK|righe/i, { timeout: 30000 });
    });
  } finally {
    await deleteProjects(api, created);
  }
});
