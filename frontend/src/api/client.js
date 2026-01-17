import axios from 'axios';

const API_BASE_URL = 'http://localhost:8000/api';

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// ===== CATALOG API =====

export const uploadCatalog = async (file) => {
  const formData = new FormData();
  formData.append('file', file);
  
  const response = await axios.post(`${API_BASE_URL}/catalog/upload`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' }
  });
  
  return response.data;
};

export const getCatalog = async () => {
  const response = await api.get('/catalog/tables');
  return response.data;
};

export const getTableColumns = async (tableName) => {
  const response = await api.get(`/catalog/tables/${tableName}`);
  return response.data;
};

// ===== CONNECTIONS API =====

export const createConnection = async (data) => {
  const response = await api.post('/connections', data);
  return response.data;
};

export const listConnections = async () => {
  const response = await api.get('/connections');
  return response.data;
};

export const getConnection = async (connectionId) => {
  const response = await api.get(`/connections/${connectionId}`);
  return response.data;
};

export const updateConnection = async (connectionId, data) => {
  const response = await api.put(`/connections/${connectionId}`, data);
  return response.data;
};

export const deleteConnection = async (connectionId) => {
  const response = await api.delete(`/connections/${connectionId}`);
  return response.data;
};

export const testConnection = async (connectionId) => {
  const response = await api.post(`/connections/${connectionId}/test`);
  return response.data;
};

export const getConnectionTables = async (connectionId) => {
  const response = await api.get(`/connections/${connectionId}/tables`);
  return response.data;
};

// ===== PROJECTS API =====

export const createProject = async (name, description = '', type = 'estrai', connectionId = null) => {
  try {
    const response = await api.post('/projects', { 
      name, 
      description, 
      project_type: type,
      default_connection_id: connectionId 
    });
    
    if (type === 'estrai' && connectionId) {
      const projectId = response.data.metadata.id;
      const initialCanvas = { nodes: [], edges: [] };
      await api.put(`/projects/${projectId}/canvas`, initialCanvas);
    }
    
    return response.data;
  } catch (error) {
    console.error("Errore API createProject:", error.response?.data);
    throw error;
  }
};

export const listProjects = async () => {
  const response = await api.get('/projects');
  return response.data;
};

export const getProject = async (projectId) => {
  const response = await api.get(`/projects/${projectId}`);
  return response.data;
};

export const updateCanvas = async (projectId, canvas) => {
  const response = await api.put(`/projects/${projectId}/canvas`, canvas);
  return response.data;
};

export const deleteProject = async (projectId) => {
  const response = await api.delete(`/projects/${projectId}`);
  return response.data;
};

// ✅ MODIFICATO: Supporta sia connectionId (per SQL) che options (per Manipola)
export const executeProjectQuery = async (projectId, optionsOrConnectionId = null) => {
  let payload = {};
  
  // Se è un oggetto, sono options da Manipola
  if (optionsOrConnectionId && typeof optionsOrConnectionId === 'object') {
    const { force_recalculate = false } = optionsOrConnectionId;
    payload = { force_recalculate };
  } 
  // Se è una stringa, è connectionId da SQL
  else if (optionsOrConnectionId && typeof optionsOrConnectionId === 'string') {
    payload = { connection_id: optionsOrConnectionId };
  }
  
  const response = await api.post(`/projects/${projectId}/execute`, payload);
  return response.data;
};

export const listApiConnections = async () => {
  const response = await api.get('/api-connections');
  return response.data;
};

export default api;