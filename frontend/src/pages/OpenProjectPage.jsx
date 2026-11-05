/**
 * OpenProjectPage — apre un progetto conoscendone solo l'id (es. dai log):
 * legge il tipo dal server e va nell'editor giusto (Fase 5, T4, R7.4;
 * mappa unica in utils/projectRoutes).
 */
import React, { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { getProject } from '../api/projects';
import { projectEditorPath } from '../utils/projectRoutes';
import { isValidProjectId } from '../utils/validateRouteParam';
import { requestErrorMessage } from '../utils/errorMessages';
import '../components/common/ErrorBoundary.css';

export default function OpenProjectPage() {
  const { projectId } = useParams();
  const navigate = useNavigate();
  const valid = isValidProjectId(projectId);
  const [error, setError] = useState(valid ? null : 'Identificativo del progetto non valido.');

  useEffect(() => {
    if (!valid) return undefined;
    let live = true;
    getProject(projectId)
      .then((project) => {
        if (live) navigate(projectEditorPath({ ...project, id: project?.id || projectId }), { replace: true });
      })
      .catch((err) => { if (live) setError(requestErrorMessage(err, 'Progetto non disponibile.')); });
    return () => { live = false; };
  }, [projectId, valid, navigate]);

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
