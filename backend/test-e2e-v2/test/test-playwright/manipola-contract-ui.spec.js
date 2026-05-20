const {
  assertNoFatalBrowserErrors,
  contractColumn,
  createProject,
  deleteProjects,
  edge,
  expect,
  projectSourceNode,
  resultNode,
  setOutputContract,
  test,
  transformNode,
  uniqueName,
} = require('./fixtures');

test('Manipola UI: alias upstream rinominato aggiorna project_source e formula visibile', async ({ api, page }) => {
  const created = [];
  const columnId = 'pw_col_id_alias_contract';

  try {
    const upstreamId = await createProject(api, {
      name: uniqueName('pw_upstream_alias'),
      project_type: 'estrai',
    });
    created.push(upstreamId);

    setOutputContract(upstreamId, [
      contractColumn({
        id: columnId,
        source_name: 'id',
        physical_name: 'id',
      }),
    ]);

    const staleColumn = {
      id: columnId,
      column_id: columnId,
      name: 'id',
      source_name: 'id',
      display_name: 'id',
      type: 'integer',
      visible: true,
    };
    const formulaConfig = {
      formula: 'id*2',
      new_column: 'calculated',
      formula_column_refs: [{ id: columnId, name: 'id' }],
    };
    const canvas = {
      nodes: [
        projectSourceNode({
          nodeId: 'source-1',
          sourceProjectId: upstreamId,
          columnId,
          name: 'id',
          sourceName: 'id',
          position: { x: 0, y: 0 },
        }),
        transformNode({
          nodeId: 'formula-1',
          transformOp: 'formula',
          config: formulaConfig,
          inputColumns: [staleColumn],
          position: { x: 320, y: 0 },
        }),
        resultNode('result-1', { x: 700, y: 0 }),
      ],
      edges: [
        edge('e-source-formula', 'source-1', 'formula-1'),
        edge('e-formula-result', 'formula-1', 'result-1'),
      ],
    };

    const downstreamId = await createProject(api, {
      name: uniqueName('pw_downstream_formula'),
      project_type: 'manipola',
      canvas,
    });
    created.push(downstreamId);

    setOutputContract(upstreamId, [
      contractColumn({
        id: columnId,
        source_name: 'id',
        physical_name: 'myid',
      }),
    ]);

    await assertNoFatalBrowserErrors(page, async () => {
      await page.goto(`/editor/manipola/${downstreamId}`, { waitUntil: 'networkidle' });
      expect(new URL(page.url()).pathname).not.toBe('/login');
      await expect(page.locator('.react-flow').first()).toBeAttached();

      await expect(page.locator('.project-source-node').first()).toContainText('myid');
      await expect(page.locator('.parameter-input__field--textarea').first()).toHaveValue('myid*2');
      await expect(page.locator('.parameter-input__expression-help').first()).toContainText('myid');
    });
  } finally {
    await deleteProjects(api, created);
  }
});
