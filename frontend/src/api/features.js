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
