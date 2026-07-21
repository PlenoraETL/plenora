import http from './http';


export async function getTaskRuntime() {
  const response = await http.get('/admin/task-runtime');
  return response.data;
}


export async function resolveUncertainCaricaRun(runId, resolution, note) {
  const response = await http.post(
    `/admin/task-runtime/uncertain-carica/${encodeURIComponent(runId)}/resolve`,
    { resolution, note },
  );
  return response.data;
}
