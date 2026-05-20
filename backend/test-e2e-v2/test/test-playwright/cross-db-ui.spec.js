const {
  assertNoFatalBrowserErrors,
  createConnection,
  createProject,
  deleteConnections,
  deleteProjects,
  edge,
  executeProject,
  expect,
  getProject,
  previewProject,
  resultNode,
  test,
  transformNode,
  uniqueName,
} = require('./fixtures');

test.describe.configure({ timeout: 180000 });
test.skip(!process.env.PLENORA_E2E_CROSS_DB, 'set PLENORA_E2E_CROSS_DB=1 to run cross-db UI tests');

const DIALECTS = ['mysql', 'mssql', 'oracle'];

function connectionPayload(dialect, name) {
  if (dialect === 'mysql') {
    return {
      name,
      description: 'Playwright E2E v2 MySQL fixture',
      type: 'mysql',
      host: process.env.PLENORA_E2E_MYSQL_HOST || 'plenora-test-mysql',
      port: Number(process.env.PLENORA_E2E_MYSQL_PORT || 3306),
      database: process.env.MYSQL_DATABASE || 'plenora_test',
      username: process.env.MYSQL_USER || 'plenora',
      password: process.env.MYSQL_PASSWORD || 'plenora_test_pwd',
    };
  }
  if (dialect === 'mssql') {
    return {
      name,
      description: 'Playwright E2E v2 SQL Server fixture',
      type: 'mssql',
      host: process.env.PLENORA_E2E_MSSQL_HOST || 'plenora-test-sqlserver',
      port: Number(process.env.PLENORA_E2E_MSSQL_PORT || 1433),
      database: process.env.MSSQL_DATABASE || 'plenora_test',
      db_schema: process.env.PLENORA_E2E_MSSQL_SCHEMA || 'dbo',
      username: process.env.MSSQL_USER || 'sa',
      password: process.env.MSSQL_SA_PASSWORD || 'PlenoraTest!2026',
    };
  }
  if (dialect === 'oracle') {
    return {
      name,
      description: 'Playwright E2E v2 Oracle fixture',
      type: 'oracle',
      host: process.env.PLENORA_E2E_ORACLE_HOST || 'plenora-test-oracle',
      port: Number(process.env.PLENORA_E2E_ORACLE_PORT || 1521),
      database: process.env.ORACLE_DATABASE || 'plenora_test',
      db_schema: process.env.PLENORA_E2E_ORACLE_SCHEMA || process.env.ORACLE_APP_USER || 'plenora',
      username: process.env.ORACLE_APP_USER || 'plenora',
      password: process.env.ORACLE_APP_USER_PASSWORD || 'plenora_test_pwd',
    };
  }
  throw new Error(`unsupported dialect ${dialect}`);
}

function sourceColumn(name, { type = 'str', alias = null, visible = true } = {}) {
  return { name, type, alias, visible };
}

function sourceNode({ connectionId, position = { x: 0, y: 0 } }) {
  const columns = [
    sourceColumn('id', { type: 'int', alias: 'customer_id' }),
    sourceColumn('full_name', { type: 'str', alias: 'customer_name' }),
    sourceColumn('age', { type: 'int' }),
    sourceColumn('status', { type: 'str' }),
  ];
  return {
    id: 'source-1',
    type: 'source',
    position,
    table_name: 'customers',
    alias: 'c',
    columns,
    filters: [{ column: 'status', operator: '=', value: 'active' }],
    connection_id: connectionId,
    data: {
      table_name: 'customers',
      alias: 'c',
      columns,
      filters: [{ column: 'status', operator: '=', value: 'active' }],
      connection_id: connectionId,
    },
  };
}

function estraiResultNode(dialect) {
  const data = {
    fileName: `pw_cross_db_${dialect}`,
    format: 'csv',
    distinct: false,
    limit: 5,
    columns: [],
    group_by_enabled: false,
    group_by_columns: [],
    aggregations: [],
    order_by_enabled: true,
    order_by_columns: [{ column: 'id', direction: 'ASC' }],
    having_enabled: false,
    having_conditions: [],
  };
  return {
    id: 'result-1',
    type: 'result',
    position: { x: 680, y: 0 },
    ...data,
    data,
  };
}

function activeContractColumns(project) {
  return (project.output_contract?.columns || [])
    .filter(column => column.status !== 'removed')
    .map(column => ({
      id: column.id,
      column_id: column.id,
      name: column.physical_name || column.display_name || column.source_name,
      source_name: column.source_name || column.physical_name || column.display_name,
      display_name: column.display_name || column.physical_name || column.source_name,
      type: column.type?.logical || 'str',
      visible: true,
    }));
}

function projectSourceNode({ sourceProjectId, columns }) {
  return {
    id: 'project-source-1',
    type: 'project_source',
    position: { x: 0, y: 0 },
    source_project_id: sourceProjectId,
    columns,
    data: {
      source_project_id: sourceProjectId,
      columns,
    },
  };
}

test('cross-db GUI: ESTRAI e MANIPOLA aprono progetti MySQL, SQL Server e Oracle', async ({ api, page }) => {
  test.setTimeout(180000);
  const createdProjects = [];
  const createdConnections = [];

  try {
    for (const dialect of DIALECTS) {
      const connectionId = await createConnection(api, connectionPayload(dialect, uniqueName(`pw_cross_db_${dialect}`)));
      createdConnections.push(connectionId);

      const estraiId = await createProject(api, {
        name: uniqueName(`pw_cross_db_${dialect}_estrai`),
        project_type: 'estrai',
        default_connection_id: connectionId,
        canvas: {
          nodes: [sourceNode({ connectionId }), estraiResultNode(dialect)],
          edges: [edge('e-source-result', 'source-1', 'result-1')],
        },
      });
      createdProjects.push(estraiId);

      const preview = await previewProject(api, estraiId, { limit: 10, timeoutMs: 90000 });
      expect(preview.rows?.length || 0, `${dialect}: preview ESTRAI must return rows`).toBeGreaterThan(0);

      await assertNoFatalBrowserErrors(page, async () => {
        await page.goto(`/project/${estraiId}`, { waitUntil: 'networkidle' });
        expect(new URL(page.url()).pathname).not.toBe('/login');
        await expect(page.locator('.react-flow').first()).toBeAttached();
        await expect(page.locator('body')).toContainText('customers');
        await expect(page.locator('body')).toContainText('customer_name');
      });

      await executeProject(api, estraiId, { timeoutMs: 90000 });
      const columns = activeContractColumns(await getProject(api, estraiId));
      const ageColumn = columns.find(column => column.name === 'age');
      expect(ageColumn, `${dialect}: output_contract must contain age`).toBeTruthy();

      const manipolaId = await createProject(api, {
        name: uniqueName(`pw_cross_db_${dialect}_manipola`),
        project_type: 'manipola',
        canvas: {
          nodes: [
            projectSourceNode({ sourceProjectId: estraiId, columns }),
            transformNode({
              nodeId: 'formula-1',
              transformOp: 'formula',
              config: {
                formula: 'age * 2',
                new_column: 'double_age',
                formula_column_refs: [{ id: ageColumn.id, name: 'age' }],
              },
              inputColumns: columns,
              position: { x: 360, y: 0 },
            }),
            resultNode('result-1', { x: 760, y: 0 }),
          ],
          edges: [
            edge('e-source-formula', 'project-source-1', 'formula-1'),
            edge('e-formula-result', 'formula-1', 'result-1'),
          ],
        },
      });
      createdProjects.push(manipolaId);

      await assertNoFatalBrowserErrors(page, async () => {
        await page.goto(`/editor/manipola/${manipolaId}`, { waitUntil: 'networkidle' });
        expect(new URL(page.url()).pathname).not.toBe('/login');
        await expect(page.locator('.react-flow').first()).toBeAttached();
        await expect(page.locator('.project-source-node').first()).toContainText('customer_name');
        const formulaFields = page.locator('.transform-node--formula .parameter-input__field');
        await expect(formulaFields.first()).toHaveValue('double_age');
        await expect(formulaFields.nth(1)).toHaveValue('age * 2');
      });
    }
  } finally {
    await deleteProjects(api, createdProjects);
    await deleteConnections(api, createdConnections);
  }
});
