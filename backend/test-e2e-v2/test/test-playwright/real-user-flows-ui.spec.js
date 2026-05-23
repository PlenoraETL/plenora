const {
  assertNoFatalBrowserErrors,
  contractColumn,
  createConnection,
  createProject,
  deleteConnections,
  deleteProjects,
  expect,
  getProject,
  postgresFixtureConnectionPayload,
  setOutputContract,
  test,
  uniqueName,
} = require('./fixtures');

test.describe.configure({ timeout: 120000 });

async function listProjects(api) {
  const resp = await api.get('projects');
  expect(resp.ok(), `list projects: ${resp.status()} ${await resp.text()}`).toBeTruthy();
  const body = await resp.json();
  return body.projects || [];
}

async function findProjectByName(api, name) {
  const projects = await listProjects(api);
  return projects.find((project) => project.name === name);
}

async function createProjectFromDashboard(page, api, {
  name,
  type,
  connectionId = null,
}) {
  await page.goto('/', { waitUntil: 'networkidle' });
  await page.getByRole('button', { name: /Nuovo Progetto/i }).click();
  const dialog = page.getByRole('dialog').filter({ hasText: /Nuovo progetto/i }).first();
  await expect(dialog).toBeVisible();

  if (type === 'manipola') {
    await dialog.getByRole('button', { name: /Manipola/i }).click();
  } else if (type === 'arricchisci') {
    await dialog.getByRole('button', { name: /Arricchisci/i }).click();
  } else {
    await dialog.getByRole('button', { name: /Estrai/i }).click();
  }

  await dialog.locator('.pl__modal-input').fill(name);
  if (type === 'estrai') {
    await dialog.locator('#new-project-connection').selectOption(connectionId);
  }
  await dialog.getByRole('button', { name: /Crea Progetto/i }).click();
  await expect(dialog).toBeHidden({ timeout: 30000 });

  await expect.poll(async () => {
    const project = await findProjectByName(api, name);
    return project?.id || null;
  }, {
    message: `project ${name} creato via dashboard`,
    timeout: 10000,
  }).toBeTruthy();

  await page.getByPlaceholder(/Cerca progetti/i).fill(name);
  await expect(page.locator('.pl__card').filter({ hasText: name })).toBeVisible();

  const project = await findProjectByName(api, name);
  expect(project?.id, `project ${name} non trovato dopo creazione`).toBeTruthy();
  return project.id;
}

async function openProjectCard(page, name, expectedPathPattern) {
  await page.goto('/', { waitUntil: 'networkidle' });
  await page.getByPlaceholder(/Cerca progetti/i).fill(name);
  const card = page.locator('.pl__card').filter({ hasText: name }).first();
  await expect(card).toBeVisible();
  await card.getByRole('button', { name: /Apri/i }).click();
  await expect(page).toHaveURL(expectedPathPattern);
  await expect(page.locator('.react-flow').first()).toBeAttached();
}

async function dragSidebarItemToCanvas(page, itemLocator) {
  const canvas = page.locator('.react-flow__pane').first();
  await expect(canvas).toBeVisible();
  await itemLocator.dragTo(canvas, { targetPosition: { x: 420, y: 260 } });
}

function contractColumns(projectId) {
  return [
    contractColumn({ id: 'flow_col_id', source_name: 'id', physical_name: 'id', logical: 'integer', ordinal: 0 }),
    contractColumn({ id: 'flow_col_amount', source_name: 'amount', physical_name: 'amount', logical: 'real', ordinal: 1 }),
    contractColumn({ id: 'flow_col_status', source_name: 'status', physical_name: 'status', logical: 'string', ordinal: 2 }),
  ].map((column) => ({ ...column, producer_project_id: projectId }));
}

test('Dashboard UI: crea Estrai, Manipola e Arricchisci e apre le route corrette', async ({ api, page }) => {
  const createdProjects = [];
  const createdConnections = [];

  try {
    const connectionId = await createConnection(api, postgresFixtureConnectionPayload(uniqueName('pw_flow_pg')));
    createdConnections.push(connectionId);

    const estraiName = uniqueName('pw_flow_create_estrai');
    const manipolaName = uniqueName('pw_flow_create_manipola');
    const arricchisciName = uniqueName('pw_flow_create_arricchisci');

    await assertNoFatalBrowserErrors(page, async () => {
      const estraiId = await createProjectFromDashboard(page, api, { name: estraiName, type: 'estrai', connectionId });
      createdProjects.push(estraiId);
      const manipolaId = await createProjectFromDashboard(page, api, { name: manipolaName, type: 'manipola' });
      createdProjects.push(manipolaId);
      const arricchisciId = await createProjectFromDashboard(page, api, { name: arricchisciName, type: 'arricchisci' });
      createdProjects.push(arricchisciId);

      await openProjectCard(page, estraiName, new RegExp(`/project/${estraiId}$`));
      await openProjectCard(page, manipolaName, new RegExp(`/editor/manipola/${manipolaId}$`));
      await openProjectCard(page, arricchisciName, new RegExp(`/arricchisci/${arricchisciId}$`));
    });
  } finally {
    await deleteProjects(api, createdProjects);
    await deleteConnections(api, createdConnections);
  }
});

test('EstrAi UI da zero: crea progetto, trascina tabella, aggiunge Output e valida edge mancante', async ({ api, page }) => {
  const createdProjects = [];
  const createdConnections = [];

  try {
    const connectionId = await createConnection(api, postgresFixtureConnectionPayload(uniqueName('pw_flow_estrai_pg')));
    createdConnections.push(connectionId);
    const projectName = uniqueName('pw_flow_estrai_zero');

    let projectId;
    await assertNoFatalBrowserErrors(page, async () => {
      projectId = await createProjectFromDashboard(page, api, { name: projectName, type: 'estrai', connectionId });
      createdProjects.push(projectId);
      await openProjectCard(page, projectName, new RegExp(`/project/${projectId}$`));

      await page.getByPlaceholder(/Cerca tabelle e viste/i).fill('customers');
      await dragSidebarItemToCanvas(page, page.locator('.estrai-sidebar__table-item').filter({ hasText: 'customers' }).first());
      await expect(page.locator('.table-node')).toContainText('customers');
      await page.getByRole('button', { name: /Output/i }).click();
      await expect(page.locator('.result-node')).toContainText('Output');
      await page.locator('.result-node__validate-btn').evaluate((button) => button.click());
      await expect(page.getByRole('dialog', { name: /Problemi rilevati nel canvas/i })).toBeVisible();
      await expect(page.locator('.validation-modal__body')).toContainText(/Output non connesso|Nodo Output non connesso/i);
    });

    const saved = await getProject(api, projectId);
    expect(saved.canvas.nodes || []).toEqual([]);
    expect(saved.canvas.edges || []).toEqual([]);
  } finally {
    await deleteProjects(api, createdProjects);
    await deleteConnections(api, createdConnections);
  }
});

test('Manipola UI da zero: trascina sorgente progetto, configura transform/output e valida collegamenti mancanti', async ({ api, page }) => {
  const createdProjects = [];

  try {
    const upstreamName = uniqueName('pw_flow_source_contract');
    const upstreamId = await createProject(api, {
      name: upstreamName,
      project_type: 'estrai',
      canvas: { nodes: [], edges: [] },
    });
    createdProjects.push(upstreamId);
    setOutputContract(upstreamId, contractColumns(upstreamId));

    const projectName = uniqueName('pw_flow_manipola_zero');
    let projectId;
    await assertNoFatalBrowserErrors(page, async () => {
      projectId = await createProjectFromDashboard(page, api, { name: projectName, type: 'manipola' });
      createdProjects.push(projectId);
      await openProjectCard(page, projectName, new RegExp(`/editor/manipola/${projectId}$`));

      await page.getByPlaceholder(/Cerca progetto/i).fill(upstreamName);
      await dragSidebarItemToCanvas(page, page.locator('.sources-sidebar__item').filter({ hasText: upstreamName }).first());
      await expect(page.locator('.project-source-node')).toContainText(/id|amount|status/i);

      await page.getByRole('button', { name: /\+ Transform/i }).click();
      await expect(page.locator('.transform-node')).toBeVisible();
      const opSelect = page.locator('.transform-node select').first();
      await opSelect.selectOption('formula');
      await page.locator('.transform-node--formula input[placeholder="Inserisci nome nuova colonna"]').fill('amount_x2');
      await page.locator('.transform-node--formula textarea').fill('amount * 2');

      await page.getByRole('button', { name: /Output/i }).click();
      await expect(page.locator('.result-node')).toContainText(/Output Manipola/i);
      await page.locator('.result-node__validate-btn').evaluate((button) => button.click());
      await expect(page.getByRole('dialog', { name: /Problemi rilevati nel canvas/i })).toBeVisible();
      await expect(page.locator('.validation-modal__body')).toContainText(/Output non connesso|Nodo Output non connesso|orfano|non collegato/i);
    });

    const saved = await getProject(api, projectId);
    const savedNodes = saved.canvas.nodes || [];
    if (savedNodes.length > 0) {
      const savedSource = savedNodes.find(node => node.type === 'project_source');
      expect(savedSource, 'se autosave ha persistito il drag, deve salvare la sorgente progetto').toBeTruthy();
      expect((savedSource.columns || savedSource.data?.columns || []).map(column => column.name)).toEqual(['id', 'amount', 'status']);
    }
    expect(saved.canvas.edges || []).toEqual([]);
  } finally {
    await deleteProjects(api, createdProjects);
  }
});
