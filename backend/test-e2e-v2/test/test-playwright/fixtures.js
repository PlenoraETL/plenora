const { execFileSync } = require('node:child_process');
const { existsSync, readFileSync } = require('node:fs');
const { request: pwRequest, test: base, expect } = require('../../../../frontend/node_modules/@playwright/test');

loadEnvFile(`${__dirname}/../../.env`);
loadEnvFile(`${__dirname}/../.env`);

const API_ORIGIN = process.env.PLENORA_API_ORIGIN || process.env.PLENORA_API_URL || 'http://127.0.0.1:5004';
const RAW_API_BASE = process.env.PLENORA_E2E_API_BASE_URL || API_ORIGIN;
const API_BASE_ROOT = RAW_API_BASE.replace(/\/$/, '').endsWith('/api')
  ? RAW_API_BASE.replace(/\/$/, '')
  : `${RAW_API_BASE.replace(/\/$/, '')}/api`;
const API_BASE = `${API_BASE_ROOT}/`;
const ADMIN_USER = process.env.PLENORA_E2E_USERNAME || process.env.PLENORA_TEST_USER || 'admin_plenora';
const ADMIN_PASS = process.env.PLENORA_E2E_PASSWORD || process.env.PLENORA_TEST_PASS || 'admin_plenora_2026';

const TOKEN_KEY = 'urby_token';
const USER_KEY = 'urby_user';

function loadEnvFile(path) {
  if (!existsSync(path)) return;
  for (const rawLine of readFileSync(path, 'utf8').split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#') || !line.includes('=')) continue;
    const [rawKey, ...rawValueParts] = line.split('=');
    const key = rawKey.trim();
    if (!key || process.env[key] !== undefined) continue;
    const value = rawValueParts.join('=').trim().replace(/^['"]|['"]$/g, '');
    process.env[key] = value;
  }
}

async function fetchAdminSession() {
  const api = await pwRequest.newContext({ baseURL: API_BASE });
  try {
    const resp = await api.post('auth/login', {
      data: { username: ADMIN_USER, password: ADMIN_PASS },
    });
    if (!resp.ok()) {
      throw new Error(`login admin fallito ${resp.status()}: ${await resp.text()}`);
    }
    const data = await resp.json();
    return {
      access_token: data.access_token,
      user: {
        user_id: data.user_id,
        username: data.username,
        group_id: data.group_id,
        role: data.role,
      },
    };
  } finally {
    await api.dispose();
  }
}

const test = base.extend({
  adminSession: [
    async ({}, use) => {
      await use(await fetchAdminSession());
    },
    { scope: 'worker' },
  ],

  api: async ({ adminSession }, use) => {
    const api = await pwRequest.newContext({
      baseURL: API_BASE,
      extraHTTPHeaders: { Authorization: `Bearer ${adminSession.access_token}` },
    });
    try {
      await use(api);
    } finally {
      await api.dispose();
    }
  },

  page: async ({ page, adminSession }, use) => {
    await page.addInitScript(
      ({ token, user, tokenKey, userKey }) => {
        sessionStorage.setItem(tokenKey, token);
        sessionStorage.setItem(userKey, JSON.stringify(user));
      },
      {
        token: adminSession.access_token,
        user: adminSession.user,
        tokenKey: TOKEN_KEY,
        userKey: USER_KEY,
      },
    );
    await use(page);
  },
});

function uniqueName(prefix) {
  return `${prefix}_${new Date().toISOString().replace(/[-:.TZ]/g, '').slice(0, 14)}_${Math.random().toString(16).slice(2, 10)}`;
}

async function createProject(api, { name, project_type, canvas = { nodes: [], edges: [] }, default_connection_id = null }) {
  const resp = await api.post('projects', {
    data: {
      name,
      project_type,
      default_connection_id,
      canvas,
    },
  });
  expect(resp.ok(), `create project ${name}: ${resp.status()} ${await resp.text()}`).toBeTruthy();
  const body = await resp.json();
  const id = body?.metadata?.id || body?.id || body?.project_id;
  expect(id, `project id mancante in response ${JSON.stringify(body).slice(0, 300)}`).toBeTruthy();
  return id;
}

async function createConnection(api, data) {
  const resp = await api.post('connections', { data });
  expect(resp.ok(), `create connection ${data.name}: ${resp.status()} ${await resp.text()}`).toBeTruthy();
  const body = await resp.json();
  expect(body?.id, `connection id mancante in response ${JSON.stringify(body).slice(0, 300)}`).toBeTruthy();
  return body.id;
}

async function deleteConnections(api, connectionIds) {
  for (const id of [...connectionIds].filter(Boolean).reverse()) {
    await api.delete(`connections/${id}`).catch(() => {});
  }
}

async function deleteProjects(api, projectIds) {
  for (const id of [...projectIds].filter(Boolean).reverse()) {
    await api.delete(`projects/${id}`).catch(() => {});
  }
}

async function getProject(api, projectId) {
  const resp = await api.get(`projects/${projectId}`);
  expect(resp.ok(), `get project ${projectId}: ${resp.status()} ${await resp.text()}`).toBeTruthy();
  return resp.json();
}

function postgresFixtureConnectionPayload(name) {
  return {
    name,
    description: 'Playwright E2E v2 PostgreSQL fixture',
    type: 'postgres',
    host: process.env.PLENORA_E2E_SOURCE_DB_HOST || 'plenora-test-postgres',
    port: Number(process.env.PLENORA_E2E_SOURCE_DB_PORT || 5432),
    database: process.env.POSTGRES_DB || 'plenora_test',
    db_schema: process.env.PLENORA_E2E_SOURCE_DB_SCHEMA || 'public',
    username: process.env.POSTGRES_USER || 'plenora',
    password: process.env.POSTGRES_PASSWORD || 'plenora_test_pwd',
  };
}

async function waitTask(api, taskId, { timeoutMs = 60000 } = {}) {
  const deadline = Date.now() + timeoutMs;
  let lastBody = null;
  while (Date.now() < deadline) {
    const resp = await api.get(`tasks/${taskId}`);
    if (resp.ok()) {
      lastBody = await resp.json();
      const status = String(lastBody?.status || '').toLowerCase();
      if (['success', 'failure', 'failed', 'revoked'].includes(status)) {
        expect(status, `task ${taskId} ended with ${status}: ${JSON.stringify(lastBody).slice(0, 500)}`).toBe('success');
        return lastBody;
      }
    } else if (resp.status() !== 404) {
      throw new Error(`task ${taskId} status failed ${resp.status()}: ${await resp.text()}`);
    }
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  throw new Error(`task ${taskId} timeout; last=${JSON.stringify(lastBody).slice(0, 500)}`);
}

async function previewProject(api, projectId, { limit = 100, timeoutMs = 60000 } = {}) {
  const resp = await api.get(`projects/${projectId}/preview?limit=${limit}`);
  expect(resp.ok(), `preview project ${projectId}: ${resp.status()} ${await resp.text()}`).toBeTruthy();
  const body = await resp.json();
  const taskId = body?.task_id;
  expect(taskId, `preview task_id mancante in response ${JSON.stringify(body).slice(0, 300)}`).toBeTruthy();
  const status = await waitTask(api, taskId, { timeoutMs });
  const result = status?.result;
  expect(result?.success, `preview failed: ${JSON.stringify(result).slice(0, 500)}`).not.toBe(false);
  return result;
}

async function executeProject(api, projectId, { force_recalculate = true, timeoutMs = 60000 } = {}) {
  const resp = await api.post(`projects/${projectId}/execute`, {
    data: { force_recalculate },
  });
  expect(resp.ok(), `execute project ${projectId}: ${resp.status()} ${await resp.text()}`).toBeTruthy();
  const body = await resp.json();
  const taskId = body?.task_id;
  expect(taskId, `execute task_id mancante in response ${JSON.stringify(body).slice(0, 300)}`).toBeTruthy();
  const status = await waitTask(api, taskId, { timeoutMs });
  const result = status?.result;
  expect(result?.success, `execute failed: ${JSON.stringify(result).slice(0, 500)}`).not.toBe(false);
  return { taskId, status, result };
}

function setOutputContract(projectId, columns) {
  const contract = {
    version: '1.0',
    producer_project_id: projectId,
    revision: Math.floor(Date.now() / 1000),
    columns,
  };
  const raw = JSON.stringify(contract);
  const sql = [
    'UPDATE plenora.projects',
    `SET output_contract = $contract$${raw}$contract$::jsonb`,
    `WHERE id = ${sqlLiteral(projectId)};`,
  ].join(' ');

  execFileSync('docker', [
    'exec',
    process.env.PLENORA_E2E_DB_CONTAINER || 'plenora-postgres-1',
    'psql',
    '-U',
    process.env.PLENORA_E2E_DB_USER || 'plenora',
    '-d',
    process.env.PLENORA_E2E_DB_NAME || 'plenora',
    '-v',
    'ON_ERROR_STOP=1',
    '-c',
    sql,
  ], { stdio: 'pipe' });
}

function sqlLiteral(value) {
  return `'${String(value).replaceAll("'", "''")}'`;
}

function contractColumn({ id, source_name, physical_name, logical = 'integer', ordinal = 0 }) {
  return {
    id,
    source_name,
    physical_name,
    display_name: physical_name,
    type: { logical, nullable: true },
    ordinal,
    status: 'active',
  };
}

function projectSourceNode({ nodeId, sourceProjectId, columnId, name, sourceName, position = { x: 0, y: 0 } }) {
  const column = {
    id: columnId,
    column_id: columnId,
    name,
    source_name: sourceName,
    display_name: name,
    type: 'integer',
    visible: true,
  };
  return {
    id: nodeId,
    type: 'project_source',
    position,
    source_project_id: sourceProjectId,
    columns: [column],
    data: {
      source_project_id: sourceProjectId,
      columns: [column],
    },
  };
}

function transformNode({ nodeId, transformOp, config, inputColumns = null, position = { x: 280, y: 0 } }) {
  const data = {
    transform_op: transformOp,
    config,
  };
  if (inputColumns) data.inputColumns = inputColumns;
  return {
    id: nodeId,
    type: 'transform',
    position,
    transform_op: transformOp,
    config,
    data,
  };
}

function resultNode(nodeId = 'result-1', position = { x: 620, y: 0 }) {
  return {
    id: nodeId,
    type: 'result',
    position,
    columns: [],
    data: {},
  };
}

function edge(id, source, target, extra = {}) {
  return { id, source, target, ...extra };
}

async function assertNoFatalBrowserErrors(page, action) {
  const consoleErrors = [];
  const pageErrors = [];
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  page.on('pageerror', (error) => pageErrors.push(error.message));

  await action();

  const fatalConsole = consoleErrors.filter(
    (message) =>
      !/ResizeObserver|favicon|net::ERR_ABORTED/i.test(message) &&
      /TypeError|ReferenceError|SyntaxError|Cannot read|undefined is not|is not a function/i.test(message),
  );
  expect(fatalConsole, `console fatal errors:\n${fatalConsole.join('\n')}`).toEqual([]);
  expect(pageErrors, `page errors:\n${pageErrors.join('\n')}`).toEqual([]);
}

module.exports = {
  ADMIN_PASS,
  ADMIN_USER,
  API_BASE,
  API_ORIGIN,
  assertNoFatalBrowserErrors,
  contractColumn,
  createConnection,
  createProject,
  deleteConnections,
  deleteProjects,
  edge,
  expect,
  executeProject,
  fetchAdminSession,
  getProject,
  postgresFixtureConnectionPayload,
  previewProject,
  projectSourceNode,
  resultNode,
  setOutputContract,
  test,
  transformNode,
  uniqueName,
  waitTask,
};
