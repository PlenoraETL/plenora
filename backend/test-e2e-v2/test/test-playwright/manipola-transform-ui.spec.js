const {
  assertNoFatalBrowserErrors,
  contractColumn,
  createProject,
  deleteProjects,
  edge,
  expect,
  getProject,
  projectSourceNode,
  resultNode,
  setOutputContract,
  test,
  transformNode,
  uniqueName,
} = require('./fixtures');

const COL_ID = 'pw_col_id';
const COL_AMOUNT = 'pw_col_amount';
const COL_STATUS = 'pw_col_status';

function contractColumns(projectId) {
  return [
    contractColumn({ id: COL_ID, source_name: 'id', physical_name: 'id', logical: 'integer', ordinal: 0 }),
    contractColumn({ id: COL_AMOUNT, source_name: 'amount', physical_name: 'amount', logical: 'real', ordinal: 1 }),
    contractColumn({ id: COL_STATUS, source_name: 'status', physical_name: 'status', logical: 'string', ordinal: 2 }),
  ];
}

function projectColumns() {
  return [
    { id: COL_ID, column_id: COL_ID, name: 'id', source_name: 'id', display_name: 'id', type: 'integer', visible: true },
    { id: COL_AMOUNT, column_id: COL_AMOUNT, name: 'amount', source_name: 'amount', display_name: 'amount', type: 'real', visible: true },
    { id: COL_STATUS, column_id: COL_STATUS, name: 'status', source_name: 'status', display_name: 'status', type: 'string', visible: true },
  ];
}

async function createManipolaFixture(api, { transformOp, config = {}, inputColumns = projectColumns() }) {
  const created = [];
  const upstreamId = await createProject(api, {
    name: uniqueName(`pw_src_${transformOp}`),
    project_type: 'estrai',
  });
  created.push(upstreamId);
  setOutputContract(upstreamId, contractColumns(upstreamId));

  const canvas = {
    nodes: [
      projectSourceNode({
        nodeId: 'source-1',
        sourceProjectId: upstreamId,
        columnId: COL_ID,
        name: 'id',
        sourceName: 'id',
        position: { x: 0, y: 0 },
      }),
      transformNode({
        nodeId: 'transform-1',
        transformOp,
        config,
        inputColumns,
        position: { x: 320, y: 0 },
      }),
      resultNode('result-1', { x: 720, y: 0 }),
    ],
    edges: [
      edge('e-source-transform', 'source-1', 'transform-1'),
      edge('e-transform-result', 'transform-1', 'result-1'),
    ],
  };

  const downstreamId = await createProject(api, {
    name: uniqueName(`pw_manipola_${transformOp}`),
    project_type: 'manipola',
    canvas,
  });
  created.push(downstreamId);

  return { created, downstreamId };
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

test('Manipola UI: modifica formula, salva e mantiene il valore dopo reload', async ({ api, page }) => {
  const { created, downstreamId } = await createManipolaFixture(api, {
    transformOp: 'formula',
    config: {
      formula: 'id*2',
      new_column: 'calculated',
      formula_column_refs: [{ id: COL_ID, name: 'id' }],
    },
  });

  try {
    await assertNoFatalBrowserErrors(page, async () => {
      await openManipola(page, downstreamId);
      const formula = page.locator('.parameter-input__field--textarea').first();
      await expect(formula).toHaveValue('id*2');
      await formula.fill('amount + 10');
      await expect(formula).toHaveValue('amount + 10');
      await saveManipola(page);

      await page.reload({ waitUntil: 'networkidle' });
      await expect(page.locator('.parameter-input__field--textarea').first()).toHaveValue('amount + 10');
      await expect(page.locator('.parameter-input__expression-help').first()).toContainText('amount');
    });

    const saved = await getProject(api, downstreamId);
    const transform = nodeById(saved.canvas, 'transform-1');
    expect(transform.config.formula).toBe('amount + 10');
    expect(transform.config.formula_column_refs).toEqual([{ id: COL_AMOUNT, name: 'amount' }]);
  } finally {
    await deleteProjects(api, created);
  }
});

test('Manipola UI: drop_columns seleziona colonne, salva e mantiene i tag dopo reload', async ({ api, page }) => {
  const { created, downstreamId } = await createManipolaFixture(api, {
    transformOp: 'drop_columns',
    config: {},
  });

  try {
    await assertNoFatalBrowserErrors(page, async () => {
      await openManipola(page, downstreamId);
      const selector = page.locator('.transform-node--drop_columns .column-selector--multiple select').first();
      await selector.selectOption('status');
      await expect(page.locator('.transform-node--drop_columns .column-selector__tag')).toContainText('status');
      await selector.selectOption('amount');
      await expect(page.locator('.transform-node--drop_columns .column-selector__tag')).toContainText(['status', 'amount']);
      await saveManipola(page);

      await page.reload({ waitUntil: 'networkidle' });
      await expect(page.locator('.transform-node--drop_columns .column-selector__tag')).toContainText(['status', 'amount']);
    });

    const saved = await getProject(api, downstreamId);
    const transform = nodeById(saved.canvas, 'transform-1');
    expect(transform.config.columns).toEqual(['status', 'amount']);
    expect(transform.config.columns_column_ids).toEqual([COL_STATUS, COL_AMOUNT]);
  } finally {
    await deleteProjects(api, created);
  }
});

test('Manipola UI: rename configura vecchio/nuovo nome, salva e mantiene dopo reload', async ({ api, page }) => {
  const { created, downstreamId } = await createManipolaFixture(api, {
    transformOp: 'rename',
    config: {},
  });

  try {
    await assertNoFatalBrowserErrors(page, async () => {
      await openManipola(page, downstreamId);
      await page.locator('.rename-list-input__add').filter({ hasText: 'Aggiungi rinomina' }).click();
      await page.locator('.rename-list-input__select').first().selectOption('amount');
      await page.locator('.rename-list-input__input').first().fill('total_amount');
      await expect(page.locator('.rename-list-input__select').first()).toHaveValue('amount');
      await expect(page.locator('.rename-list-input__input').first()).toHaveValue('total_amount');
      await saveManipola(page);

      await page.reload({ waitUntil: 'networkidle' });
      await expect(page.locator('.rename-list-input__select').first()).toHaveValue('amount');
      await expect(page.locator('.rename-list-input__input').first()).toHaveValue('total_amount');
    });

    const saved = await getProject(api, downstreamId);
    const transform = nodeById(saved.canvas, 'transform-1');
    expect(transform.config.renames).toEqual([
      { old_name: 'amount', old_name_id: COL_AMOUNT, new_name: 'total_amount' },
    ]);
  } finally {
    await deleteProjects(api, created);
  }
});
