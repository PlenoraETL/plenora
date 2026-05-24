const { execFileSync } = require('node:child_process');
const path = require('node:path');

const {
  createConnection,
  createProject,
  deleteConnections,
  deleteProjects,
  edge,
  expect,
  postgresFixtureConnectionPayload,
  previewProject,
  test,
  uniqueName,
  waitTask,
} = require('./fixtures');

test.describe.configure({ timeout: 240000 });

const REPO_ROOT = path.resolve(__dirname, '../../../..');

function sourceColumn(name, { type = 'str', alias = null, visible = true } = {}) {
  return { name, type, alias, visible };
}

function resultNode({
  nodeId = 'result-1',
  fileName = 'pw_scheduler_carica_output',
  position = { x: 620, y: 80 },
  orderBy = null,
} = {}) {
  const data = {
    fileName,
    format: 'csv',
    distinct: false,
    limit: 100,
    columns: [],
    group_by_enabled: false,
    group_by_columns: [],
    aggregations: [],
    having_enabled: false,
    having_conditions: [],
    order_by_enabled: Boolean(orderBy),
    order_by_columns: orderBy ? [orderBy] : [],
  };
  return {
    id: nodeId,
    type: 'result',
    position,
    ...data,
    data,
  };
}

function sourceNode({ connectionId, columns, filters = [], tableName = 'customers', position = { x: 80, y: 80 } }) {
  return {
    id: 'customers',
    type: 'source',
    position,
    table_name: tableName,
    alias: 'c',
    columns,
    filters,
    connection_id: connectionId,
    data: {
      table_name: tableName,
      alias: 'c',
      columns,
      filters,
      connection_id: connectionId,
    },
  };
}

function activeCustomersCanvas(connectionId) {
  const columns = [
    sourceColumn('id', { type: 'int', alias: 'customer_id' }),
    sourceColumn('full_name', { type: 'str', alias: 'customer_name' }),
    sourceColumn('age', { type: 'int' }),
    sourceColumn('status', { type: 'str' }),
  ];
  return {
    nodes: [
      sourceNode({
        connectionId,
        columns,
        filters: [{ column: 'status', operator: '=', value: 'active' }],
      }),
      resultNode({
        fileName: 'pw_scheduler_carica_active',
        orderBy: { column: 'id', direction: 'ASC' },
      }),
    ],
    edges: [edge('e-customers-result', 'customers', 'result-1')],
  };
}

function verifyTableCanvas(connectionId, tableName) {
  const columns = [
    sourceColumn('customer_id', { type: 'int' }),
    sourceColumn('customer_name', { type: 'str' }),
    sourceColumn('age', { type: 'int' }),
    sourceColumn('status', { type: 'str' }),
  ];
  return {
    nodes: [
      sourceNode({
        connectionId,
        columns,
        filters: [],
        tableName,
      }),
      resultNode({
        fileName: 'pw_scheduler_carica_verify',
        orderBy: { column: 'customer_id', direction: 'ASC' },
      }),
    ],
    edges: [edge('e-loaded-result', 'customers', 'result-1')],
  };
}

async function createSchedule(api, data) {
  const resp = await api.post('schedules', { data });
  expect(resp.ok(), `create schedule: ${resp.status()} ${await resp.text()}`).toBeTruthy();
  const body = await resp.json();
  expect(body?.id, `schedule id missing: ${JSON.stringify(body).slice(0, 300)}`).toBeTruthy();
  return body;
}

async function deleteSchedules(api, scheduleIds) {
  for (const id of [...scheduleIds].filter(Boolean).reverse()) {
    await api.delete(`schedules/${id}`).catch(() => {});
  }
}

async function runScheduleNow(api, scheduleId) {
  const resp = await api.post(`schedules/${scheduleId}/run`);
  expect(resp.ok(), `run schedule ${scheduleId}: ${resp.status()} ${await resp.text()}`).toBeTruthy();
  const body = await resp.json();
  expect(body?.status).toBe('queued');
  expect(body?.task_id, `task_id missing: ${JSON.stringify(body).slice(0, 300)}`).toBeTruthy();
  return body.task_id;
}

async function getSchedule(api, scheduleId) {
  const resp = await api.get(`schedules/${scheduleId}`);
  expect(resp.ok(), `get schedule ${scheduleId}: ${resp.status()} ${await resp.text()}`).toBeTruthy();
  return resp.json();
}

async function waitScheduleRuntime(api, scheduleId, expectedStatuses, { timeoutMs = 90000 } = {}) {
  const expected = new Set(expectedStatuses);
  const deadline = Date.now() + timeoutMs;
  let lastSchedule = null;
  while (Date.now() < deadline) {
    lastSchedule = await getSchedule(api, scheduleId);
    const status = lastSchedule?.runtime?.status;
    if (expected.has(status)) {
      return lastSchedule;
    }
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  throw new Error(`schedule ${scheduleId} runtime timeout; last=${JSON.stringify(lastSchedule).slice(0, 500)}`);
}

async function waitTaskTerminal(api, taskId, { timeoutMs = 60000 } = {}) {
  const deadline = Date.now() + timeoutMs;
  let lastBody = null;
  while (Date.now() < deadline) {
    const resp = await api.get(`tasks/${taskId}`);
    if (resp.ok()) {
      lastBody = await resp.json();
      const status = String(lastBody?.status || '').toLowerCase();
      if (['success', 'failure', 'failed', 'revoked'].includes(status)) {
        return lastBody;
      }
    } else if (resp.status() !== 404) {
      throw new Error(`task ${taskId} status failed ${resp.status()}: ${await resp.text()}`);
    }
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  throw new Error(`task ${taskId} timeout; last=${JSON.stringify(lastBody).slice(0, 500)}`);
}

function sqlLiteral(value) {
  return `'${String(value).replaceAll("'", "''")}'`;
}

function sqlIdent(value) {
  return `"${String(value).replaceAll('"', '""')}"`;
}

function execAppDbSql(sql) {
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
  ], { cwd: REPO_ROOT, stdio: 'pipe' });
}

function execFixturePostgresSql(sql) {
  execFileSync('docker', [
    'exec',
    process.env.PLENORA_E2E_SOURCE_DB_CONTAINER || 'plenora-test-postgres',
    'psql',
    '-U',
    process.env.POSTGRES_USER || 'plenora',
    '-d',
    process.env.POSTGRES_DB || 'plenora_test',
    '-v',
    'ON_ERROR_STOP=1',
    '-c',
    sql,
  ], { cwd: REPO_ROOT, stdio: 'pipe' });
}

function runScheduleTaskInBackend(scheduleId, { triggeredBy = 'cron', retryAttempt = 0, scheduledFor }) {
  const script = `
import json
from workers.tasks.schedule_task import schedule_pipeline_task

result = schedule_pipeline_task.run(
    ${JSON.stringify(scheduleId)},
    triggered_by=${JSON.stringify(triggeredBy)},
    retry_attempt=${Number(retryAttempt)},
    scheduled_for=${JSON.stringify(scheduledFor)},
)
print(json.dumps(result, default=str))
`;
  const output = execFileSync('docker', [
    'compose',
    'exec',
    '-T',
    'backend',
    'python',
    '-c',
    script,
  ], {
    cwd: REPO_ROOT,
    encoding: 'utf8',
    timeout: 180000,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  const jsonLine = output
    .trim()
    .split(/\r?\n/)
    .reverse()
    .find(line => line.trim().startsWith('{'));
  expect(jsonLine, `backend task output missing JSON: ${output.slice(-1000)}`).toBeTruthy();
  return JSON.parse(jsonLine);
}

function installSchedulerLock(scheduleId, { queueSize = null } = {}) {
  const lockKey = `scheduler:lock:${scheduleId}`;
  const queueKey = `scheduler:queue:${scheduleId}`;
  const statements = [
    `CREATE TABLE IF NOT EXISTS scheduler_locks (key TEXT PRIMARY KEY, value TEXT NOT NULL, expires_at TEXT)`,
    `INSERT INTO scheduler_locks (key, value, expires_at) VALUES (${sqlLiteral(lockKey)}, 'running', '2099-01-01T00:00:00+00:00') ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, expires_at = EXCLUDED.expires_at`,
  ];
  if (queueSize !== null) {
    statements.push(
      `INSERT INTO scheduler_locks (key, value, expires_at) VALUES (${sqlLiteral(queueKey)}, ${sqlLiteral(String(queueSize))}, NULL) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, expires_at = EXCLUDED.expires_at`,
    );
  }
  execAppDbSql(`${statements.join('; ')};`);
}

function clearSchedulerLock(scheduleId) {
  execAppDbSql(
    `DELETE FROM scheduler_locks WHERE key IN (${sqlLiteral(`scheduler:lock:${scheduleId}`)}, ${sqlLiteral(`scheduler:queue:${scheduleId}`)})`,
  );
}

function schedulerDestination(connectionId, tableName, overrides = {}) {
  return {
    connection_id: connectionId,
    schema_name: 'public',
    table_name: tableName,
    write_mode: 'create',
    batch_size: 100,
    match_columns: [],
    ...overrides,
  };
}

function schedulePayload(projectId, destination, descriptionPrefix = 'pw scheduler carica') {
  return {
    project_id: projectId,
    cron_expression: '0 3 * * *',
    timezone: 'Europe/Rome',
    description: uniqueName(descriptionPrefix),
    use_cache: false,
    destination,
    max_retries: 0,
    retry_backoff_seconds: [1],
    append_conflicts: 'abort',
    log_skipped_rows: true,
    concurrency_max_queue: 1,
  };
}

async function fetchScheduleLogs(api, scheduleId) {
  const resp = await api.get(`schedules/${scheduleId}/logs?page=1&page_size=20`);
  expect(resp.ok(), `schedule logs ${scheduleId}: ${resp.status()} ${await resp.text()}`).toBeTruthy();
  return resp.json();
}

test('Scheduler CARICA DB runtime: manual run writes table, logs run, exposes download', async ({ api }) => {
  const createdProjects = [];
  const createdConnections = [];
  const createdSchedules = [];

  try {
    const connectionId = await createConnection(api, postgresFixtureConnectionPayload(uniqueName('pw_sched_carica_pg')));
    createdConnections.push(connectionId);

    const tableName = uniqueName('pw_sched_carica_target').toLowerCase().slice(0, 48);
    const projectId = await createProject(api, {
      name: uniqueName('pw_sched_carica_project'),
      project_type: 'estrai',
      default_connection_id: connectionId,
      canvas: activeCustomersCanvas(connectionId),
    });
    createdProjects.push(projectId);

    const expectedRows = (await previewProject(api, projectId, { limit: 500, timeoutMs: 90000 })).rows || [];
    expect(expectedRows.length, 'fixture must produce active customers before scheduling').toBeGreaterThan(0);

    const schedule = await createSchedule(api, schedulePayload(
      projectId,
      schedulerDestination(connectionId, tableName),
    ));
    createdSchedules.push(schedule.id);
    expect(schedule?.destination?.table_name).toBe(tableName);
    expect(schedule?.runtime?.status).toBe('idle');

    const taskId = await runScheduleNow(api, schedule.id);
    const taskStatus = await waitTask(api, taskId, { timeoutMs: 120000 });
    const taskResult = taskStatus?.result || {};
    expect(taskResult.success, `scheduler task result: ${JSON.stringify(taskResult).slice(0, 500)}`).toBeTruthy();
    expect(taskResult.status).toBe('completed');
    expect(taskResult.rows_written).toBe(expectedRows.length);

    const completedSchedule = await waitScheduleRuntime(api, schedule.id, ['completed'], { timeoutMs: 90000 });
    expect(completedSchedule.runtime.total_runs).toBeGreaterThanOrEqual(1);
    expect(completedSchedule.runtime.success_count).toBeGreaterThanOrEqual(1);
    expect(completedSchedule.runtime.last_execution.rows_written).toBe(expectedRows.length);

    const verifyProjectId = await createProject(api, {
      name: uniqueName('pw_sched_carica_verify'),
      project_type: 'estrai',
      default_connection_id: connectionId,
      canvas: verifyTableCanvas(connectionId, tableName),
    });
    createdProjects.push(verifyProjectId);

    const loadedRows = (await previewProject(api, verifyProjectId, { limit: 500, timeoutMs: 90000 })).rows || [];
    expect(loadedRows.length).toBe(expectedRows.length);
    expect(loadedRows.map(row => Number(row.customer_id))).toEqual(expectedRows.map(row => Number(row.customer_id)));

    const logs = await fetchScheduleLogs(api, schedule.id);
    expect(logs.total).toBeGreaterThanOrEqual(1);
    const latestLog = logs.logs[0];
    expect(latestLog.event).toBe('PIPELINE_COMPLETED');
    expect(latestLog.triggered_by).toBe('manual');
    expect(latestLog.execution_info?.result_id, `latest log execution_info: ${JSON.stringify(latestLog.execution_info)}`).toBeTruthy();
    const caricaStep = (latestLog.steps || []).find(step => step.step === 'CARICA' && step.write_mode === 'create');
    expect(caricaStep, `CARICA step missing: ${JSON.stringify(latestLog.steps)}`).toBeTruthy();
    expect(caricaStep.rows).toBe(expectedRows.length);
    expect(caricaStep.rows_inserted).toBe(expectedRows.length);

    const runsResp = await api.get(`schedules/${schedule.id}/runs`);
    expect(runsResp.ok(), `schedule runs ${schedule.id}: ${runsResp.status()} ${await runsResp.text()}`).toBeTruthy();
    const runs = await runsResp.json();
    expect(runs.runs_count).toBeGreaterThanOrEqual(1);
    expect(runs.runs[0].files.length).toBeGreaterThanOrEqual(1);

    const downloadResp = await api.get(`schedules/${schedule.id}/download`);
    expect(downloadResp.ok(), `schedule download ${schedule.id}: ${downloadResp.status()} ${await downloadResp.text()}`).toBeTruthy();
    const downloaded = await downloadResp.text();
    expect(downloaded).toContain('customer_id');
    expect(downloaded).toContain('customer_name');
  } finally {
    await deleteSchedules(api, createdSchedules);
    await deleteProjects(api, createdProjects);
    await deleteConnections(api, createdConnections);
  }
});

test('Scheduler CARICA DB runtime: failed write is reported in task, runtime and logs', async ({ api }) => {
  const createdProjects = [];
  const createdConnections = [];
  const createdSchedules = [];

  try {
    const connectionId = await createConnection(api, postgresFixtureConnectionPayload(uniqueName('pw_sched_carica_fail_pg')));
    createdConnections.push(connectionId);

    const projectId = await createProject(api, {
      name: uniqueName('pw_sched_carica_fail_project'),
      project_type: 'estrai',
      default_connection_id: connectionId,
      canvas: activeCustomersCanvas(connectionId),
    });
    createdProjects.push(projectId);

    const expectedRows = (await previewProject(api, projectId, { limit: 500, timeoutMs: 90000 })).rows || [];
    expect(expectedRows.length, 'fixture must produce rows before negative scheduler run').toBeGreaterThan(0);

    const schedule = await createSchedule(api, schedulePayload(
      projectId,
      schedulerDestination(connectionId, 'customers', { write_mode: 'update', match_columns: [] }),
      'pw scheduler carica fail',
    ));
    createdSchedules.push(schedule.id);

    const taskId = await runScheduleNow(api, schedule.id);
    const taskStatus = await waitTaskTerminal(api, taskId, { timeoutMs: 120000 });
    expect(String(taskStatus?.status || '').toLowerCase(), `scheduler task terminal state: ${JSON.stringify(taskStatus).slice(0, 500)}`).toBe('failure');
    expect(String(taskStatus?.message || taskStatus?.error)).toMatch(/match|CARICA|fallita|failed/i);

    const failedSchedule = await waitScheduleRuntime(api, schedule.id, ['failed'], { timeoutMs: 90000 });
    expect(failedSchedule.runtime.total_runs).toBeGreaterThanOrEqual(1);
    expect(failedSchedule.runtime.failure_count).toBeGreaterThanOrEqual(1);
    expect(failedSchedule.runtime.consecutive_failures).toBeGreaterThanOrEqual(1);
    expect(failedSchedule.runtime.last_execution.status).toBe('failed');
    expect(failedSchedule.runtime.last_execution.rows_written).toBe(0);

    const logs = await fetchScheduleLogs(api, schedule.id);
    expect(logs.total).toBeGreaterThanOrEqual(1);
    const latestLog = logs.logs[0];
    expect(['PIPELINE_FAILED', 'RETRY_EXHAUSTED']).toContain(latestLog.event);
    expect(latestLog.level).toBe('ERROR');
    expect(latestLog.triggered_by).toBe('manual');
    expect(latestLog.error_message).toMatch(/match|CARICA|fallita|failed/i);
    expect((latestLog.steps || []).some(step => step.step === 'OUTPUT' && step.status === 'success')).toBeTruthy();
    expect((latestLog.steps || []).some(step => step.step === 'CARICA' && step.status === 'success')).toBeFalsy();
  } finally {
    await deleteSchedules(api, createdSchedules);
    await deleteProjects(api, createdProjects);
    await deleteConnections(api, createdConnections);
  }
});

test('Scheduler CARICA idempotency: same scheduled tick does not append duplicate rows', async ({ api }) => {
  const createdProjects = [];
  const createdConnections = [];
  const createdSchedules = [];
  let tableName = null;

  try {
    const connectionId = await createConnection(api, postgresFixtureConnectionPayload(uniqueName('pw_sched_idem_pg')));
    createdConnections.push(connectionId);

    tableName = uniqueName('pw_sched_idem_target').toLowerCase().slice(0, 48);
    execFixturePostgresSql(
      [
        `DROP TABLE IF EXISTS public.${sqlIdent(tableName)};`,
        `CREATE TABLE public.${sqlIdent(tableName)} (`,
        'customer_id INTEGER,',
        'customer_name VARCHAR(128),',
        'age INTEGER,',
        'status VARCHAR(64)',
        ')',
      ].join(' '),
    );

    const projectId = await createProject(api, {
      name: uniqueName('pw_sched_idem_project'),
      project_type: 'estrai',
      default_connection_id: connectionId,
      canvas: activeCustomersCanvas(connectionId),
    });
    createdProjects.push(projectId);

    const expectedRows = (await previewProject(api, projectId, { limit: 500, timeoutMs: 90000 })).rows || [];
    expect(expectedRows.length, 'fixture must produce active customers before idempotency test').toBeGreaterThan(0);

    const schedule = await createSchedule(api, schedulePayload(
      projectId,
      schedulerDestination(connectionId, tableName, { write_mode: 'append' }),
      'pw scheduler idem',
    ));
    createdSchedules.push(schedule.id);

    const scheduledFor = '2026-05-23T03:00:00+00:00';
    const first = runScheduleTaskInBackend(schedule.id, { scheduledFor });
    expect(first.success, `first scheduled tick result: ${JSON.stringify(first).slice(0, 500)}`).toBeTruthy();
    expect(first.status).toBe('completed');
    expect(first.rows_written).toBe(expectedRows.length);

    const verifyProjectId = await createProject(api, {
      name: uniqueName('pw_sched_idem_verify'),
      project_type: 'estrai',
      default_connection_id: connectionId,
      canvas: verifyTableCanvas(connectionId, tableName),
    });
    createdProjects.push(verifyProjectId);

    const afterFirst = (await previewProject(api, verifyProjectId, { limit: 500, timeoutMs: 90000 })).rows || [];
    expect(afterFirst.length).toBe(expectedRows.length);

    const second = runScheduleTaskInBackend(schedule.id, { scheduledFor });
    expect(second.success, `second same-tick result: ${JSON.stringify(second).slice(0, 500)}`).toBeTruthy();
    expect(['completed', 'partial']).toContain(second.status);
    expect(second.rows_written).toBe(0);

    const afterSecond = (await previewProject(api, verifyProjectId, { limit: 500, timeoutMs: 90000 })).rows || [];
    expect(afterSecond.length).toBe(expectedRows.length);
    expect(afterSecond.map(row => Number(row.customer_id))).toEqual(expectedRows.map(row => Number(row.customer_id)));

    const logs = await fetchScheduleLogs(api, schedule.id);
    expect(logs.total).toBeGreaterThanOrEqual(2);
    const caricaSteps = logs.logs.flatMap(log => log.steps || []).filter(step => step.step === 'CARICA');
    expect(caricaSteps.some(step => step.rows === expectedRows.length)).toBeTruthy();
    expect(caricaSteps.some(step => step.rows === 0 || step.status === 'skipped')).toBeTruthy();
  } finally {
    await deleteSchedules(api, createdSchedules);
    await deleteProjects(api, createdProjects);
    await deleteConnections(api, createdConnections);
    if (tableName) {
      execFixturePostgresSql(`DROP TABLE IF EXISTS public.${sqlIdent(tableName)}`);
    }
  }
});

test('Scheduler concurrency guard: full queue skips run and records scheduler log', async ({ api }) => {
  const createdProjects = [];
  const createdConnections = [];
  const createdSchedules = [];

  try {
    const connectionId = await createConnection(api, postgresFixtureConnectionPayload(uniqueName('pw_sched_queue_pg')));
    createdConnections.push(connectionId);

    const projectId = await createProject(api, {
      name: uniqueName('pw_sched_queue_project'),
      project_type: 'estrai',
      default_connection_id: connectionId,
      canvas: activeCustomersCanvas(connectionId),
    });
    createdProjects.push(projectId);

    const schedule = await createSchedule(api, schedulePayload(
      projectId,
      null,
      'pw scheduler queue',
    ));
    createdSchedules.push(schedule.id);

    installSchedulerLock(schedule.id, { queueSize: 1 });
    const taskId = await runScheduleNow(api, schedule.id);
    const taskStatus = await waitTaskTerminal(api, taskId, { timeoutMs: 90000 });
    const taskText = JSON.stringify(taskStatus);
    expect(String(taskStatus?.status || '').toLowerCase(), taskText).toBe('failure');
    expect(taskText).toMatch(/skipped|coda piena|queue/i);

    const logs = await fetchScheduleLogs(api, schedule.id);
    expect(logs.logs.some(log => log.event === 'SCHEDULE_SKIPPED')).toBeTruthy();
    const skippedLog = logs.logs.find(log => log.event === 'SCHEDULE_SKIPPED');
    expect(skippedLog.level).toBe('WARNING');
    expect(skippedLog.triggered_by).toBe('manual');

    clearSchedulerLock(schedule.id);
    const recoveredTaskId = await runScheduleNow(api, schedule.id);
    const recovered = await waitTask(api, recoveredTaskId, { timeoutMs: 120000 });
    expect(recovered.result?.success, `recovered run result: ${JSON.stringify(recovered.result).slice(0, 500)}`).toBeTruthy();
    const completedSchedule = await waitScheduleRuntime(api, schedule.id, ['completed'], { timeoutMs: 90000 });
    expect(completedSchedule.runtime.last_execution.status).toBe('completed');
  } finally {
    for (const scheduleId of createdSchedules) {
      clearSchedulerLock(scheduleId);
    }
    await deleteSchedules(api, createdSchedules);
    await deleteProjects(api, createdProjects);
    await deleteConnections(api, createdConnections);
  }
});
