/**
 * mocks/handlers.js — handler MSW per il dev server FE senza backend. #50.
 *
 * Set di PARTENZA: copre auth + le liste/letture che fanno bootstrappare la
 * shell (login, /auth/me, progetti, connessioni, un progetto vuoto). Estendere
 * man mano che servono altri endpoint per testare flussi specifici offline.
 *
 * Match con prefisso wildcard (es. star-slash-path) perché l'axios client usa
 * baseURL=VITE_API_URL. Attivo SOLO con VITE_USE_MOCKS=true — mai in produzione.
 */
import { http, HttpResponse } from 'msw';

// D6c: stessa forma del backend (auth/router.py) e di quello che legge
// AuthContext.login: campi del profilo in radice, accanto al token.
const MOCK_USER = {
  user_id: 'u-mock', username: 'dev', role: 'admin', group_id: 'g-mock', email: 'dev@plenora.local',
};

const MOCK_PROJECTS = [
  { id: 'p-1', name: 'Demo Estrai',     project_type: 'estrai',      group_id: 'g-mock', updated_at: '2026-06-01T10:00:00Z', created_at: '2026-06-01T09:00:00Z' },
  { id: 'p-2', name: 'Demo Manipola',   project_type: 'manipola',    group_id: 'g-mock', updated_at: '2026-06-02T10:00:00Z', created_at: '2026-06-02T09:00:00Z' },
  { id: 'p-3', name: 'Demo Arricchisci', project_type: 'arricchisci', group_id: 'g-mock', updated_at: '2026-06-03T10:00:00Z', created_at: '2026-06-03T09:00:00Z' },
];

export const handlers = [
  http.post('*/auth/login', () =>
    HttpResponse.json({ access_token: 'mock-token', token_type: 'bearer', ...MOCK_USER })),
  http.post('*/auth/logout', () => HttpResponse.json({ success: true })),
  http.get('*/auth/me', () => HttpResponse.json(MOCK_USER)),

  http.get('*/projects', () => HttpResponse.json({ projects: MOCK_PROJECTS })),
  http.get('*/projects/:id', ({ params }) =>
    HttpResponse.json({
      metadata: {
        id: params.id, name: 'Demo', project_type: 'manipola',
        group_id: 'g-mock', canvas_validated: false,
      },
      canvas: { nodes: [], edges: [] },
      output_contract: null,
      applied_migrations: [],
    })),

  http.get('*/connections', () => HttpResponse.json({ connections: [] })),
  http.get('*/features/connections', () => HttpResponse.json({ connections: [] })),
  http.get('*/files', () => HttpResponse.json({ files: [] })),
];
