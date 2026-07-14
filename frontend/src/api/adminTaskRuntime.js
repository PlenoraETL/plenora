import http from './http';


export async function getTaskRuntime() {
  const response = await http.get('/admin/task-runtime');
  return response.data;
}
