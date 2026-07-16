import http from './http';

export async function listObjectStorage(params = {}) {
  const response = await http.get('/admin/object-storage', { params });
  return response.data;
}

export async function listObjectStorageProjects(params = {}) {
  const response = await http.get('/admin/object-storage/projects', { params });
  return response.data;
}

export async function deleteAllStoragePreviews(params = {}) {
  const response = await http.delete('/admin/object-storage/previews', { params });
  return response.data;
}

export async function cleanupOrphanStorage() {
  const response = await http.post('/projects/admin/cleanup-orphans', null, {
    params: { dry_run: false },
  });
  return response.data;
}

export async function downloadStorageObject(key) {
  const response = await http.get('/admin/object-storage/download', {
    params: { key },
    responseType: 'blob',
  });
  const disposition = response.headers?.['content-disposition'] || '';
  const match = disposition.match(/filename="?([^";]+)"?/i);
  const filename = match?.[1] || key.split('/').pop() || 'object.bin';
  const url = URL.createObjectURL(response.data);
  try {
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    URL.revokeObjectURL(url);
  }
}

export async function deleteStorageObject(key) {
  const response = await http.delete('/admin/object-storage', {
    params: { key },
  });
  return response.data;
}
