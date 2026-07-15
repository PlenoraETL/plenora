/**
 * features.js — Client API per Feature Service ArcGIS
 * 
 * Posizione: /src/api/features.js
 * Endpoint backend: /api/features
 */

import http from './http';
import { fetchAllOffsetPages } from './pagination';

const API_BASE = '/features';

export async function listFeatureConnections() {
  return fetchAllOffsetPages(`${API_BASE}/connections`, {
    extractItems: data => data.connections,
    mergeItems: (items, lastPage) => ({ ...lastPage, connections: items }),
  });
}

export async function createFeatureConnection(data) {
  return http.post(`${API_BASE}/connections`, data).then(r => r.data);
}

export async function updateFeatureConnection(connectionId, data) {
  return http.put(`${API_BASE}/connections/${connectionId}`, data).then(r => r.data);
}

export async function deleteFeatureConnection(connectionId) {
  return http.delete(`${API_BASE}/connections/${connectionId}`).then(r => r.data);
}

export async function testFeatureConnection(connectionId) {
  return http.post(`${API_BASE}/connections/${connectionId}/test`).then(r => r.data);
}

export async function browseServices(connectionId) {
  return http.get(`${API_BASE}/connections/${connectionId}/services`).then(r => r.data);
}

export async function browseLayers(connectionId, serviceUrl) {
  return http.get(`${API_BASE}/connections/${connectionId}/layers`, { params: { service_url: serviceUrl } }).then(r => r.data);
}

export async function listFolders(connectionId) {
  return http.get(`${API_BASE}/connections/${connectionId}/folders`).then(r => r.data);
}

export async function getLayerSchema(connectionId, layerUrl) {
  return http.get(`${API_BASE}/connections/${connectionId}/schema`, { params: { layer_url: layerUrl } }).then(r => r.data);
}

// API-ready senza consumer UI (ADR-0017 Sub-B keep)
// Le 3 funzioni seguenti — queryLayer, writeFeatures,
// createFeatureService — chiamano endpoint BE live
// (/features/query/preview, /features/write, /features/create-service)
// ma NON sono attualmente consumate da alcun componente UI.
// Il contract FE↔BE è protetto dai test in
// tests/api/features.contract.test.js. Wiring UI: scope post-launch.
export async function queryLayer(connectionId, layerUrl, options = {}) {
  return http.post(`${API_BASE}/query/preview`, null, {
    params: {
      connection_id: connectionId,
      layer_url: layerUrl,
      where: options.where || '1=1',
      out_fields: options.outFields || '*',
      max_features: options.maxFeatures || 100,
    },
  }).then(r => r.data);
}

export async function writeFeatures({ connectionId, layerUrl, operation, inputPath, projectId, geometryColumn, sourceSrid, objectIds, where }) {
  return http.post(`${API_BASE}/write`, {
    connection_id: connectionId,
    layer_url: layerUrl,
    operation,
    input_path: inputPath || '',
    project_id: projectId || '',
    geometry_column: geometryColumn || 'geometry',
    source_srid: sourceSrid || null,
    object_ids: objectIds || null,
    where: where || null,
  }).then(r => r.data);
}

export async function createFeatureService({ connectionId, serviceName, layerName, inputPath, projectId, geometryColumn, sourceSrid }) {
  return http.post(`${API_BASE}/create-service`, {
    connection_id: connectionId,
    service_name: serviceName,
    layer_name: layerName || 'Layer 0',
    input_path: inputPath || '',
    project_id: projectId || '',
    geometry_column: geometryColumn || 'geometry',
    source_srid: sourceSrid || null,
  }).then(r => r.data);
}
