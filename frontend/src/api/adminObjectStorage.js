import http from './http';
import { filenameFromDisposition, saveBlob } from '../utils/downloadBlob';
import { downloadProgress } from '../utils/transferProgress';

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

// Il perimetro della bonifica e' la lista di project_id mostrata e confermata
// (audit R10.4): il server tocca solo quelli, e solo se sono orfani.
export async function cleanupOrphanStorage(projectIds) {
  if (!Array.isArray(projectIds)) throw new Error('cleanupOrphanStorage: perimetro (project_ids) obbligatorio');
  const response = await http.post('/projects/admin/cleanup-orphans', { project_ids: projectIds }, {
    params: { dry_run: false },
  });
  return response.data;
}

export async function downloadStorageObject(key, { onProgress = null } = {}) {
  const response = await http.get('/admin/object-storage/download', {
    params: { key },
    responseType: 'blob',
    ...downloadProgress(onProgress),
  });
  const filename = filenameFromDisposition(response.headers?.['content-disposition'])
    || key.split('/').pop() || 'object.bin';
  saveBlob(response.data, filename);
}

export async function deleteStorageObject(key) {
  const response = await http.delete('/admin/object-storage', {
    params: { key },
  });
  return response.data;
}
