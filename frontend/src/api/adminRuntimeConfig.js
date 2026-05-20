/**
 * api/adminRuntimeConfig.js - configurazione runtime read-only (superadmin).
 */

import http from './http';

export const getRuntimeConfig = async () => {
  const response = await http.get('/admin/runtime-config');
  return response.data;
};

