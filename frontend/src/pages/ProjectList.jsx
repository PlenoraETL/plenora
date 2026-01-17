import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { listProjects, createProject, deleteProject, listConnections, getProject, listApiConnections } from '../api/client';
import '../styles/ProjectList.css';

function ProjectList() {
  const [projects, setProjects] = useState([]);
  const [connections, setConnections] = useState([]);
  const [apiConnections, setApiConnections] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [newProjectName, setNewProjectName] = useState('');
  const [newProjectDesc, setNewProjectDesc] = useState('');
  const [newProjectType, setNewProjectType] = useState('estrai');
  const [newProjectConnectionId, setNewProjectConnectionId] = useState('');
  const [projectSources, setProjectSources] = useState({});
  const navigate = useNavigate();

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    try {
      setLoading(true);
      const [projectsData, connectionsData, apiConnectionsData] = await Promise.all([
        listProjects(),
        listConnections(),
        listApiConnections()
      ]);
      
      const projectsList = projectsData.projects || [];
      setProjects(projectsList);
      setConnections(connectionsData.connections || []);
      setApiConnections(apiConnectionsData.api_connections || []);
      
      // Carica origini per progetti manipola
      const manipolaProjects = projectsList.filter(p => p.project_type === 'manipola');
      
      if (manipolaProjects.length > 0) {
        const sourcesMap = {};
        
        await Promise.all(
          manipolaProjects.map(async (project) => {
            try {
              const projectData = await getProject(project.id);
              
              if (projectData.canvas?.nodes) {
                const sourceNodes = projectData.canvas.nodes.filter(
                  n => n.type === 'project_source'
                );
                
                const sourceIds = sourceNodes
                  .map(n => n.data?.source_project_id || n.source_project_id)
                  .filter(Boolean);
                
                sourcesMap[project.id] = sourceIds;
              }
            } catch (err) {
              console.error(`Errore caricamento origini per ${project.id}:`, err);
              sourcesMap[project.id] = [];
            }
          })
        );
        
        setProjectSources(sourcesMap);
      }
      
    } catch (error) {
      console.error('Errore caricamento dati:', error);
      alert('Errore caricamento dati');
    } finally {
      setLoading(false);
    }
  };

  const handleCreateProject = async () => {
    if (!newProjectName.trim()) {
      alert('Inserisci un nome per il progetto');
      return;
    }
    
    if (newProjectType === 'estrai' && !newProjectConnectionId) {
      alert('Seleziona una connessione per il progetto SQL');
      return;
    }
    
    try {
      await createProject(
        newProjectName, 
        newProjectDesc, 
        newProjectType, 
        newProjectConnectionId
      );
      
      setShowModal(false);
      setNewProjectName('');
      setNewProjectDesc('');
      setNewProjectConnectionId('');
      loadData();
    } catch (error) {
      alert('Errore durante la creazione: ' + (error.response?.data?.detail || error.message));
    }
  };

  const handleDeleteProject = async (projectId, projectName) => {
    if (!confirm(`Eliminare il progetto "${projectName}"?`)) return;
    try {
      await deleteProject(projectId);
      loadData();
    } catch (error) {
      console.error('Errore eliminazione progetto:', error);
      alert('Errore eliminazione progetto');
    }
  };

  const handleOpenProject = (project) => {
    if (project.project_type === 'manipola') {
      navigate(`/editor/manipola/${project.id}`);
    } else if (project.project_type === 'arricchisci') {
      navigate(`/arricchisci/${project.id}`);
    } else {
      navigate(`/project/${project.id}`);
    }
  };

  const handleManageConnections = () => {
    navigate('/connections');
  };

  const openModalForType = (type) => {
    setNewProjectType(type);
    setNewProjectName('');
    setNewProjectDesc('');
    setNewProjectConnectionId('');
    setShowModal(true);
  };

  // Helpers
  const getConnectionForProject = (connectionId) => {
    return connections.find(c => c.id === connectionId);
  };

  const getProjectById = (projectId) => {
    return projects.find(p => p.id === projectId);
  };

  const getSourceProjectsForManipola = (projectId) => {
    const sourceIds = projectSources[projectId] || [];
    return sourceIds
      .map(id => getProjectById(id))
      .filter(Boolean);
  };

  const formatDate = (dateString) => {
    if (!dateString) return 'N/A';
    const date = new Date(dateString);
    return date.toLocaleDateString('it-IT', { 
      day: '2-digit', 
      month: 'short', 
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    });
  };

  // Separa progetti per tipo
  const estraiProjects = projects.filter(p => p.project_type === 'estrai' || (!p.project_type));
  const manipolaProjects = projects.filter(p => p.project_type === 'manipola');
  const arricchisciProjects = projects.filter(p => p.project_type === 'arricchisci');

  // Loading
  if (loading) {
    return (
      <div className="loading">
        <div className="loading__content">
          <div className="loading__icon">⏳</div>
          Caricamento progetti...
        </div>
      </div>
    );
  }

  return (
    <div className="project-list-page">
      {/* ===== HEADER ===== */}
      <header className="header">
        <div className="header__container">
          <div>
            <h1 className="header__title">
              <span className="header__title-icon">🔮</span>
              Visual ETL Builder
            </h1>
            <p className="header__subtitle">
              Estrai, trasforma e manipola i tuoi dati visualmente
            </p>
          </div>
          
          <div className="header__actions">
            <button
              className="header__btn"
              onClick={() => navigate('/api-connections')}
            >
              🔗 API Esterne ({apiConnections.length})
            </button>
            <button
              className="header__btn"
              onClick={handleManageConnections}
            >
              🔌 Connessioni ({connections.length})
            </button>
          </div>
        </div>
      </header>

      {/* ===== MAIN CONTENT ===== */}
      <main className="main-content">
        
        {/* ===== STATISTICHE RAPIDE ===== */}
        <div className="stats-grid">
          <div className="stat-card stat-card--total">
            <div className="stat-card__label">Progetti Totali</div>
            <div className="stat-card__value">{projects.length}</div>
          </div>
          <div className="stat-card stat-card--estrai">
            <div className="stat-card__label">Progetti Estrai</div>
            <div className="stat-card__value">{estraiProjects.length}</div>
          </div>
          <div className="stat-card stat-card--manipola">
            <div className="stat-card__label">Progetti Manipola</div>
            <div className="stat-card__value">{manipolaProjects.length}</div>
          </div>
          <div className="stat-card stat-card--arricchisci">
            <div className="stat-card__label">Progetti Arricchisci</div>
            <div className="stat-card__value">{arricchisciProjects.length}</div>
          </div>
          <div className="stat-card stat-card--connections">
            <div className="stat-card__label">Connessioni DB</div>
            <div className="stat-card__value">{connections.length}</div>
          </div>
          <div className="stat-card stat-card--api">
            <div className="stat-card__label">API Esterne</div>
            <div className="stat-card__value">{apiConnections.length}</div>
          </div>
        </div>

        {/* ===== SEZIONE ESTRAI ===== */}
        <section className="section">
          <div className="section__header">
            <div className="section__title-wrapper">
              <div className="section__icon section__icon--estrai">📥</div>
              <div>
                <h2 className="section__title">Estrai (SQL Query)</h2>
                <p className="section__subtitle">Estrai dati dai database con query visuali</p>
              </div>
            </div>
            <button
              className="section__btn section__btn--estrai"
              onClick={() => openModalForType('estrai')}
            >
              + Nuovo Estrai
            </button>
          </div>

          {estraiProjects.length === 0 ? (
            <div className="empty-state">
              <div className="empty-state__icon">📥</div>
              <h3 className="empty-state__title">Nessun progetto Estrai</h3>
              <p className="empty-state__text">Crea il tuo primo progetto per estrarre dati dal database</p>
            </div>
          ) : (
            <div className="cards-grid">
              {estraiProjects.map((project) => {
                const conn = getConnectionForProject(project.default_connection_id);
                return (
                  <div
                    key={project.id}
                    className="project-card"
                    onClick={() => handleOpenProject(project)}
                  >
                    <div className="project-card__header project-card__header--estrai">
                      <div className="project-card__header-content">
                        <span className="project-card__badge project-card__badge--estrai">
                          SQL
                        </span>
                        <h3 className="project-card__title project-card__title--estrai">
                          {project.name}
                        </h3>
                      </div>
                    </div>
                    
                    <div className="project-card__body">
                      <p className="project-card__description">
                        {project.description || 'Nessuna descrizione'}
                      </p>
                      
                      <div className="project-card__info-grid">
                        <div>
                          <div className="project-card__info-label">Connessione</div>
                          <div className="project-card__info-value">
                            {conn ? (
                              <>
                                <span>🐘</span>
                                {conn.name}
                              </>
                            ) : (
                              <span className="project-card__info-value--muted">N/A</span>
                            )}
                          </div>
                        </div>
                        <div>
                          <div className="project-card__info-label">Creato</div>
                          <div className="project-card__info-value">
                            {formatDate(project.created_at)}
                          </div>
                        </div>
                      </div>
                    </div>
                    
                    <div className="project-card__footer">
                      <button
                        className="project-card__btn project-card__btn--open project-card__btn--open-estrai"
                        onClick={(e) => {
                          e.stopPropagation();
                          handleOpenProject(project);
                        }}
                      >
                        ✏️ Apri Editor
                      </button>
                      <button
                        className="project-card__btn project-card__btn--delete"
                        onClick={(e) => {
                          e.stopPropagation();
                          handleDeleteProject(project.id, project.name);
                        }}
                      >
                        🗑️ Elimina
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </section>

        {/* ===== SEZIONE MANIPOLA ===== */}
        <section className="section">
          <div className="section__header">
            <div className="section__title-wrapper">
              <div className="section__icon section__icon--manipola">🔄</div>
              <div>
                <h2 className="section__title">Manipola (Pipeline Pandas)</h2>
                <p className="section__subtitle">Trasforma e combina dati con operazioni visuali</p>
              </div>
            </div>
            <button
              className="section__btn section__btn--manipola"
              onClick={() => openModalForType('manipola')}
            >
              + Nuovo Manipola
            </button>
          </div>

          {manipolaProjects.length === 0 ? (
            <div className="empty-state">
              <div className="empty-state__icon">🔄</div>
              <h3 className="empty-state__title">Nessun progetto Manipola</h3>
              <p className="empty-state__text">Crea un progetto per trasformare e combinare i dati estratti</p>
            </div>
          ) : (
            <div className="cards-grid">
              {manipolaProjects.map((project) => {
                const sourceProjects = getSourceProjectsForManipola(project.id);
                
                return (
                  <div
                    key={project.id}
                    className="project-card"
                    onClick={() => handleOpenProject(project)}
                  >
                    <div className="project-card__header project-card__header--manipola">
                      <div className="project-card__header-content">
                        <span className="project-card__badge project-card__badge--manipola">
                          PANDAS
                        </span>
                        <h3 className="project-card__title project-card__title--manipola">
                          {project.name}
                        </h3>
                      </div>
                    </div>
                    
                    <div className="project-card__body">
                      <p className="project-card__description project-card__description--short">
                        {project.description || 'Nessuna descrizione'}
                      </p>
                      
                      {sourceProjects.length > 0 ? (
                        <div className="project-card__sources">
                          <div className="project-card__sources-label">
                            📥 Origini ({sourceProjects.length}):
                          </div>
                          <div className="project-card__sources-list">
                            {sourceProjects.map((src, idx) => (
                              <span key={idx} className="project-card__source-tag">
                                {src.name}
                              </span>
                            ))}
                          </div>
                        </div>
                      ) : (
                        <div className="project-card__no-sources">
                          ⚠️ Nessuna origine configurata
                        </div>
                      )}
                      
                      <div className="project-card__info-grid">
                        <div>
                          <div className="project-card__info-label">Tipo</div>
                          <div className="project-card__info-value project-card__info-value--manipola">
                            🔄 Pipeline
                          </div>
                        </div>
                        <div>
                          <div className="project-card__info-label">Creato</div>
                          <div className="project-card__info-value">
                            {formatDate(project.created_at)}
                          </div>
                        </div>
                      </div>
                    </div>
                    
                    <div className="project-card__footer">
                      <button
                        className="project-card__btn project-card__btn--open project-card__btn--open-manipola"
                        onClick={(e) => {
                          e.stopPropagation();
                          handleOpenProject(project);
                        }}
                      >
                        ✏️ Apri Editor
                      </button>
                      <button
                        className="project-card__btn project-card__btn--delete"
                        onClick={(e) => {
                          e.stopPropagation();
                          handleDeleteProject(project.id, project.name);
                        }}
                      >
                        🗑️ Elimina
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </section>

        {/* ===== SEZIONE ARRICCHISCI ===== */}
        <section className="section">
          <div className="section__header">
            <div className="section__title-wrapper">
              <div className="section__icon section__icon--arricchisci">🔗</div>
              <div>
                <h2 className="section__title">Arricchisci</h2>
                <p className="section__subtitle">Arricchisci i dati chiamando API esterne</p>
              </div>
            </div>
            <button
              className="section__btn section__btn--arricchisci"
              onClick={() => openModalForType('arricchisci')}
            >
              + Nuovo Arricchisci
            </button>
          </div>

          {arricchisciProjects.length === 0 ? (
            <div className="empty-state empty-state--arricchisci">
              <div className="empty-state__icon">🔗</div>
              <h3 className="empty-state__title">Nessun progetto Arricchisci</h3>
              <p className="empty-state__text">Arricchisci i tuoi dati con informazioni da API esterne</p>
            </div>
          ) : (
            <div className="cards-grid">
              {arricchisciProjects.map(project => (
                <div
                  key={project.id}
                  className="project-card"
                  onClick={() => handleOpenProject(project)}
                >
                  <div className="project-card__header project-card__header--arricchisci">
                    <div className="project-card__header-content project-card__header-content--arricchisci">
                      <span className="project-card__header-icon">🔗</span>
                      <h3 className="project-card__title project-card__title--arricchisci">
                        {project.name}
                      </h3>
                    </div>
                  </div>

                  <div className="project-card__body">
                    <p className="project-card__description">
                      {project.description || 'Nessuna descrizione'}
                    </p>
                    
                    <div className="project-card__info-grid">
                      <div>
                        <div className="project-card__info-label">Tipo</div>
                        <div className="project-card__info-value project-card__info-value--arricchisci">
                          🔗 API Enrichment
                        </div>
                      </div>
                      <div>
                        <div className="project-card__info-label">Creato</div>
                        <div className="project-card__info-value">
                          {formatDate(project.created_at)}
                        </div>
                      </div>
                    </div>
                  </div>
                  
                  <div className="project-card__footer">
                    <button
                      className="project-card__btn project-card__btn--open project-card__btn--open-arricchisci"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleOpenProject(project);
                      }}
                    >
                      ✏️ Apri Editor
                    </button>
                    <button
                      className="project-card__btn project-card__btn--delete"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDeleteProject(project.id, project.name);
                      }}
                    >
                      🗑️ Elimina
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>
      </main>

      {/* ===== FOOTER ===== */}
      <footer className="footer">
        Visual ETL Builder • UrbyetOrbit S.R.L.S.
      </footer>

      {/* ===== MODAL CREAZIONE ===== */}
      {showModal && (
        <div className="modal-overlay" onClick={() => setShowModal(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3 className="modal__title">
              {newProjectType === 'estrai' ? '📥' : newProjectType === 'manipola' ? '🔄' : '🔗'}
              Nuovo progetto {newProjectType === 'estrai' ? 'Estrai' : newProjectType === 'manipola' ? 'Manipola' : 'Arricchisci'}
            </h3>

            {/* Nome */}
            <div className="modal__field">
              <label className="modal__label">Nome progetto *</label>
              <input
                type="text"
                className="modal__input"
                value={newProjectName}
                onChange={(e) => setNewProjectName(e.target.value)}
                placeholder="Es. 'Estrazione Fabbricati'"
              />
            </div>

            {/* Descrizione */}
            <div className="modal__field">
              <label className="modal__label">Descrizione</label>
              <textarea
                className="modal__textarea"
                value={newProjectDesc}
                onChange={(e) => setNewProjectDesc(e.target.value)}
                placeholder="Descrizione opzionale..."
                rows={2}
              />
            </div>

            {/* Connessione (solo per Estrai) */}
            {newProjectType === 'estrai' && (
              <div className="modal__field">
                <label className="modal__label">Connessione Database *</label>
                <select
                  className="modal__select"
                  value={newProjectConnectionId}
                  onChange={(e) => setNewProjectConnectionId(e.target.value)}
                >
                  <option value="">-- Seleziona connessione --</option>
                  {connections.map(c => (
                    <option key={c.id} value={c.id}>
                      🐘 {c.name} ({c.database})
                    </option>
                  ))}
                </select>
                {connections.length === 0 && (
                  <p className="modal__error">
                    ⚠️ Nessuna connessione disponibile. 
                    <span 
                      className="modal__error-link"
                      onClick={() => {
                        setShowModal(false);
                        handleManageConnections();
                      }}
                    >
                      Creane una
                    </span>
                  </p>
                )}
              </div>
            )}

            {/* Info per Manipola */}
            {newProjectType === 'manipola' && (
              <div className="modal__info modal__info--manipola">
                💡 I progetti Manipola usano come input i risultati dei progetti Estrai.
                Assicurati di avere almeno un progetto Estrai eseguito.
              </div>
            )}

            {/* Actions */}
            <div className="modal__actions">
              <button
                className="modal__btn modal__btn--cancel"
                onClick={() => setShowModal(false)}
              >
                Annulla
              </button>
              <button
                className={`modal__btn modal__btn--submit modal__btn--submit-${newProjectType}`}
                onClick={handleCreateProject}
              >
                Crea Progetto
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default ProjectList;