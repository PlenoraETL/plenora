/**
 * projectRoutes — UNICA mappa tipo di progetto -> rotta dell'editor
 * (Fase 5, T4, R7.4). Le <Route> di App.jsx usano gli stessi percorsi; un
 * test vieta di costruire altrove `/project/…`, `/editor/manipola/…`,
 * `/arricchisci/…` (tests/shared/navigation.contract.test.js).
 */
export const PROJECT_EDITOR_ROUTES = {
  estrai: '/project/:projectId',
  manipola: '/editor/manipola/:projectId',
  arricchisci: '/arricchisci/:projectId',
};

/** Rotta che apre un progetto di tipo ignoto (es. dai log): legge il tipo e reindirizza. */
export const OPEN_PROJECT_ROUTE = '/open/:projectId';

const fill = (pattern, id) => pattern.replace(':projectId', encodeURIComponent(String(id)));

/** Editor giusto per il progetto (tipo assente o sconosciuto = Estrai, come il backend). */
export function projectEditorPath(project) {
  const pattern = PROJECT_EDITOR_ROUTES[project?.project_type] || PROJECT_EDITOR_ROUTES.estrai;
  return fill(pattern, project?.id);
}

/** Per chi conosce solo l'id del progetto. */
export const openProjectPath = (projectId) => fill(OPEN_PROJECT_ROUTE, projectId);
