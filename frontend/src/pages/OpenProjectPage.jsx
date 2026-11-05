/**
 * OpenProjectPage — apre un progetto conoscendone solo l'id (es. dai log):
 * legge il tipo dal server e va nell'editor giusto (Fase 5, T4, R7.4;
 * mappa unica in utils/projectRoutes).
 */
import React, { useEffect } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { getProject } from '../api/projects';
import { useResource } from '../hooks/useResource';
import { projectEditorPath } from '../utils/projectRoutes';
import { isValidProjectId } from '../utils/validateRouteParam';
import { requestErrorMessage } from '../utils/errorMessages';
import '../components/common/ErrorBoundary.css';

export default function OpenProjectPage() {
  const { projectId } = useParams();
  const navigate = useNavigate();
  const valid = isValidProjectId(projectId);
  // Lettura del tipo dall'hook comune (T2): id di richiesta, una risposta
  // superata (altro id, smontaggio) non porta in un editor sbagliato.
  const resource = useResource(() => getProject(projectId), {
    key: projectId,
    enabled: valid,
    errorMessage: (err) => requestErrorMessage(err, 'Progetto non disponibile.'),
  });
  const project = resource.data;

  useEffect(() => {
    if (!project) return;
    navigate(projectEditorPath({ ...project, id: project.id || projectId }), { replace: true });
  }, [project, projectId, navigate]);

  const error = !valid ? 'Identificativo del progetto non valido.'
    : resource.error || (resource.status === 'empty' ? 'Progetto non disponibile.' : null);

  if (!error) {
    return <div className="error-boundary error-boundary--app" role="status">Apertura del progetto…</div>;
  }
  return (
    <div className="error-boundary error-boundary--app" role="alert">
      <div className="error-boundary__card">
        <h2 className="error-boundary__title">Impossibile aprire il progetto</h2>
        <p className="error-boundary__message">{error}</p>
        <div className="error-boundary__actions">
          <Link className="error-boundary__btn error-boundary__btn--primary" to="/">Vai ai progetti</Link>
        </div>
      </div>
    </div>
  );
}
