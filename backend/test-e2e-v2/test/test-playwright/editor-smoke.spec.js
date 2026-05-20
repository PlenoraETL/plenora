const {
  assertNoFatalBrowserErrors,
  createProject,
  deleteProjects,
  expect,
  test,
  uniqueName,
} = require('./fixtures');

test('editor EstrAi e Manipola montano il canvas ReactFlow', async ({ api, page }) => {
  const created = [];
  try {
    const estraiId = await createProject(api, {
      name: uniqueName('pw_estrai_mount'),
      project_type: 'estrai',
    });
    created.push(estraiId);

    const manipolaId = await createProject(api, {
      name: uniqueName('pw_manipola_mount'),
      project_type: 'manipola',
    });
    created.push(manipolaId);

    await assertNoFatalBrowserErrors(page, async () => {
      await page.goto(`/project/${estraiId}`, { waitUntil: 'networkidle' });
      expect(new URL(page.url()).pathname).not.toBe('/login');
      await expect(page.locator('.react-flow').first()).toBeAttached();

      await page.goto(`/editor/manipola/${manipolaId}`, { waitUntil: 'networkidle' });
      expect(new URL(page.url()).pathname).not.toBe('/login');
      await expect(page.locator('.react-flow').first()).toBeAttached();
    });
  } finally {
    await deleteProjects(api, created);
  }
});
