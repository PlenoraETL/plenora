/**
 * ProjectList.jsx — Home page / Dashboard progetti
 * Redesign con sidebar + hex canvas background
 * Route: /
 */
import React, { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import { useToast } from '../hooks/useToast';
import { useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { listProjects, createProject, deleteProject, listConnections, listApiConnections,
         listEseguibili, deleteEseguibile } from '../api';

import { listFeatureConnections } from '../api/features';
import { getFiles } from '../api/files';
import { listSchedules } from '../api/schedules';
import { listDataSources } from '../api/datasources';
import { handleCascade409, formatCascadeDependencies } from '../api/errors';
import { useHexCanvas } from '../utils/hexCanvas';
import PublishEseguibileModal from '../components/eseguibili/PublishEseguibileModal';
import EseguibileExecuteWizard from '../components/eseguibili/EseguibileExecuteWizard';
import AppSidebar from '../components/AppSidebar';
import ModalWrapper, { ModalButton } from '../components/common/ModalWrapper';
import { can, role } from '../utils/permissions';
import '../styles/ProjectList.css';

// ── Main component ───────────────────────────────────────────────────────
function ProjectList() {
  const toast    = useToast();
  const navigate = useNavigate();
  const location = useLocation();
  const { user, logout } = useAuth();

  // State
  const [projects,          setProjects]          = useState([]);
  const [connections,       setConnections]       = useState([]);
  const [arcgisConnections, setArcgisConnections] = useState([]);
  const [apiConnections,    setApiConnections]    = useState([]);
  const [files,             setFiles]             = useState([]);
  const [schedules,         setSchedules]         = useState([]);
  const [dataSources,       setDataSources]       = useState([]);
  const [loading,           setLoading]           = useState(true);
  const [creating,          setCreating]          = useState(false);
  const [showModal,         setShowModal]         = useState(false);
  const [publishProject, setPublishProject] = useState(null); // progetto da pubblicare come eseguibile
  const [eseguibili, setEseguibili] = useState([]);            // lista eseguibili (catene on-demand)
  const [executeEseguibileId, setExecuteEseguibileId] = useState(null); // id eseguibile da lanciare nel wizard
  const [showEsegSelector, setShowEsegSelector] = useState(false);      // selettore progetto sorgente per nuovo eseguibile
  const [esegSelectorSearch, setEsegSelectorSearch] = useState('');
  const [newProjectName,    setNewProjectName]    = useState('');
  const [newProjectDesc,    setNewProjectDesc]    = useState('');
  const [newProjectType,    setNewProjectType]    = useState('estrai');
  const [newProjectConnId,  setNewProjectConnId]  = useState('');
  const [projectSources,    setProjectSources]    = useState({});
  const [projectFileSources,setProjectFileSources]= useState({});
  const [search,            setSearch]            = useState('');
  const [theme,             setTheme]             = useState(() => {
    const saved = localStorage.getItem('plenora-theme');
    const initial = saved || document.documentElement.getAttribute('data-theme') || 'dark';
    document.documentElement.setAttribute('data-theme', initial);
    return initial;
  });


  const canvasRef = useRef(null);
  useHexCanvas(canvasRef);

  const toggleTheme = () => {
    const next = theme === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('plenora-theme', next);
    setTheme(next);
  };

  // Load data
  useEffect(() => { loadData(); }, []);

  // Quando si arriva sulla dashboard con un hash (es. /#estrai), scrolliamo
  // alla sezione corrispondente. Aspettiamo che `loading` sia false così
  // le sezioni sono già renderizzate. Funziona sia all'arrivo iniziale sia
  // ai click successivi nella sidebar (che cambiano l'hash).
  useEffect(() => {
    if (loading) return;
    const hash = location.hash?.replace('#', '');
    if (!hash) return;
    // Defer di un tick per assicurarsi che il DOM sia montato
    const t = setTimeout(() => {
      const el = document.getElementById(`pl-section-${hash}`);
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 50);
    return () => clearTimeout(t);
  }, [loading, location.hash]);

  const loadData = async () => {
    try {
      setLoading(true);
      const [projectsData, connectionsData, apiConnData, filesData, schedulesData, arcgisData, eseguibiliData, dataSourcesData] =
        await Promise.all([
          listProjects(),
          listConnections(),
          listApiConnections(),
          getFiles().catch(() => ({ files: [] })),
          listSchedules().catch(() => ({ schedules: [], total: 0 })),
          listFeatureConnections().catch(() => ({ connections: [] })),
          listEseguibili().catch(() => []),
          listDataSources().catch(() => []),
        ]);

      const projectsList = projectsData.projects || [];
      setProjects(projectsList);
      setConnections(connectionsData.connections || []);
      setArcgisConnections(arcgisData.connections || arcgisData || []);
      setApiConnections(apiConnData.api_connections || []);
      setFiles(filesData.files || []);
      setSchedules(schedulesData.schedules || []);
      setEseguibili(Array.isArray(eseguibiliData) ? eseguibiliData : (eseguibiliData?.items || []));
      setDataSources(dataSourcesData?.datasources || []);

      const sourcesMap = {};
      const fileSourcesMap = {};
      projectsList.forEach(p => {
        if (p.project_type === 'manipola') {
          if (p.source_project_ids)  sourcesMap[p.id]     = p.source_project_ids;
          if (p.file_source_ids)     fileSourcesMap[p.id] = p.file_source_ids;
        }
      });
      setProjectSources(sourcesMap);
      setProjectFileSources(fileSourcesMap);
    } catch (err) {
      console.error('Errore caricamento dati:', err);
      toast.error('Errore caricamento dati');
    } finally {
      setLoading(false);
    }
  };

  const handleCreateProject = async () => {
    if (creating) return;
    if (!newProjectName.trim()) { toast.warning('Inserisci un nome'); return; }
    if (newProjectType === 'estrai' && !newProjectConnId) {
      toast.warning('Seleziona una connessione'); return;
    }
    setCreating(true);
    try {
      await createProject(newProjectName, newProjectDesc, newProjectType, newProjectConnId);
      setShowModal(false);
      setNewProjectName(''); setNewProjectDesc(''); setNewProjectConnId('');
      loadData();
    } catch (err) {
      toast.error('Errore creazione: ' + (err.response?.data?.detail || err.message));
    } finally { setCreating(false); }
  };

  const handleDeleteProject = async (id, name) => {
    if (!confirm(`Eliminare "${name}"?`)) return;
    try {
      await deleteProject(id);
      loadData();
    } catch (err) {
      // 409 cascade: il progetto ha dipendenze (eseguibili + progetti
      // downstream). Parsing via hub unificato (api/errors.js).
      const cascade = handleCascade409(err);
      if (cascade) {
        toast.error(
          `Impossibile eliminare "${name}": ${formatCascadeDependencies(cascade.dependencies)}. Rimuovili prima.`,
          10000
        );
        return;
      }
      // 500 project_cleanup_partial (Batch 9 C6): cascade cleanup cross-DB
      // fallita su step critici. Invariante: `project_preserved:true` → il
      // progetto e' ancora presente, l'utente puo' ritentare.
      const detail = err?.response?.data?.detail;
      if (
        err?.response?.status === 500 &&
        detail && typeof detail === 'object' &&
        detail.error === 'project_cleanup_partial'
      ) {
        toast.error(
          `Impossibile eliminare "${name}": ${detail.message || 'cleanup parziale'}. Ritenta o contatta admin.`,
          10000
        );
        return;
      }
      // Fallback: se detail e' oggetto (shape inattesa), usa err.message
      // per evitare "[object Object]" nel toast.
      const detailText = typeof detail === 'string' ? detail : err.message;
      toast.error('Errore eliminazione: ' + (detailText || 'errore sconosciuto'));
    }
  };

  const isAdmin       = role.isAdminOrAbove(user);
  const canCreateProj = can.createProject(user);

  const handleDeleteEseguibile = async (id, name) => {
    if (!confirm(`Eliminare l'eseguibile "${name}"?`)) return;
    try {
      await deleteEseguibile(id);
      toast.success('Eseguibile eliminato');
      loadData();
    } catch (err) {
      toast.error('Errore eliminazione: ' + (err?.response?.data?.detail || err.message));
    }
  };

  const openEsegSelector = () => {
    setEsegSelectorSearch('');
    setShowEsegSelector(true);
  };

  const handleOpenProject = (p) => {
    if (p.project_type === 'manipola')   navigate(`/editor/manipola/${p.id}`);
    else if (p.project_type === 'arricchisci') navigate(`/arricchisci/${p.id}`);
    else navigate(`/project/${p.id}`);
  };

  const openModalForType = (type) => {
    setNewProjectType(type);
    setNewProjectName(''); setNewProjectDesc(''); setNewProjectConnId('');
    setShowModal(true);
  };

  const getConn = id =>
    connections.find(c => c.id === id) || arcgisConnections.find(c => c.id === id);

  const getProjectById = id => projects.find(p => p.id === id);

  const getSourceProjects = pid => (projectSources[pid] || []).map(getProjectById).filter(Boolean);

  const getScheduleCount = useCallback(
    pid => schedules.filter(s => s.pipeline?.project_id === pid).length,
    [schedules]
  );

  const activeSchedules = useMemo(() => schedules.filter(s => s.schedule?.enabled), [schedules]);

  const formatDate = (d) => {
    if (!d) return 'N/A';
    return new Date(d).toLocaleDateString('it-IT', {
      day: '2-digit', month: 'short', year: 'numeric',
      hour: '2-digit', minute: '2-digit'
    });
  };

  const estraiProjects     = useMemo(() => projects.filter(p => p.project_type === 'estrai' || !p.project_type), [projects]);
  const manipolaProjects   = useMemo(() => projects.filter(p => p.project_type === 'manipola'), [projects]);
  const arricchisciProjects= useMemo(() => projects.filter(p => p.project_type === 'arricchisci'), [projects]);

  const filterBySearch = (list) =>
    search.trim()
      ? list.filter(p => p.name.toLowerCase().includes(search.toLowerCase()))
      : list;

  if (loading) {
    return (
      <div className="pl pl--loading">
        <div className="pl__loading-inner">
          <div className="pl__loading-icon">⬡</div>
          Caricamento...
        </div>
      </div>
    );
  }

  return (
    <div className="pl" data-theme-local={theme}>

      {/* ── SIDEBAR ───────────────────────────────────────── */}
      <AppSidebar
        counts={{
          projects: projects.length,
          connections: connections.length + arcgisConnections.length,
          apiConnections: apiConnections.length,
          datasources: dataSources.length,
          files: files.length,
          schedules: activeSchedules.length,
        }}
      />

      {/* ── MAIN ──────────────────────────────────────────── */}
      <div className="pl__main">

        {/* Topbar */}
        <header className="pl__topbar">
          <div className="pl__page-title">Dashboard</div>
          <div className="pl__topbar-right">
            <div className="pl__search-box">
              <span className="pl__search-icon">⌕</span>
              <input
                placeholder="Cerca progetti..."
                value={search}
                onChange={e => setSearch(e.target.value)}
              />
            </div>
            <div className="pl__theme-toggle">
              <div
                className={`pl__theme-opt ${theme === 'dark' ? 'pl__theme-opt--on' : ''}`}
                onClick={() => theme !== 'dark' && toggleTheme()}
              >🌙 Scuro</div>
              <div
                className={`pl__theme-opt ${theme === 'light' ? 'pl__theme-opt--on' : ''}`}
                onClick={() => theme !== 'light' && toggleTheme()}
              >☀️ Chiaro</div>
            </div>
            <button type="button" className="pl__btn pl__btn--ghost" onClick={loadData}>⟳ Aggiorna</button>
            {canCreateProj && (
              <button type="button" className="pl__btn pl__btn--primary" onClick={() => setShowModal(true)}>＋ Nuovo Progetto</button>
            )}
          </div>
        </header>

        {/* Content */}
        <div className="pl__content">
          <canvas ref={canvasRef} className="pl__hex-bg" />

          {/* Stats */}
          <div className="pl__stats-row">
            {[
              { val: projects.length,                                   label: 'Progetti',    accent: 'var(--pl-blue)' },
              { val: estraiProjects.length,                             label: 'Estrai',      accent: 'var(--pl-estrai)' },
              { val: manipolaProjects.length,                           label: 'Manipola',    accent: 'var(--pl-manipola)' },
              { val: arricchisciProjects.length,                        label: 'Arricchisci', accent: 'var(--pl-arricch)' },
              { val: eseguibili.length,                                 label: 'Eseguibili',  accent: 'var(--pl-blue)',
                onClick: () => document.getElementById('pl-section-eseguibili')?.scrollIntoView({ behavior: 'smooth' }) },
              { val: connections.length + arcgisConnections.length,     label: 'Connessioni', accent: 'var(--pl-blue)' },
              { val: activeSchedules.length,                            label: 'Schedule',    accent: 'var(--pl-estrai)', onClick: () => navigate('/schedules') },
              { val: apiConnections.length,                             label: 'API Esterne', accent: 'var(--pl-manipola)' },
              { val: files.length,                                      label: 'File',        accent: 'var(--pl-arricch)', onClick: () => navigate('/files') },
            ].map(({ val, label, accent, onClick }) => (
              <div
                key={label}
                className={`pl__stat ${onClick ? 'pl__stat--clickable' : ''}`}
                style={{ '--stat-accent': accent }}
                onClick={onClick}
              >
                <div className="pl__stat-val">{val}</div>
                <div className="pl__stat-label">{label}</div>
              </div>
            ))}
          </div>

          {/* ── ESTRAI ── */}
          <ProjectSection
            id="pl-section-estrai"
            chipClass="pl__chip--estrai"
            chipLabel="↓ Estrai"
            subtitle="Estrazione da sorgenti SQL e GIS"
            onNew={() => openModalForType('estrai')}
            canCreate={canCreateProj}
            empty={filterBySearch(estraiProjects).length === 0}
            emptyIcon="↓"
            emptyTitle="Nessun progetto Estrai"
            emptyText="Crea il tuo primo progetto per estrarre dati"
          >
            {filterBySearch(estraiProjects).map(project => {
              const conn = getConn(project.default_connection_id);
              return (
                <ProjectCard
                  key={project.id}
                  project={project}
                  accentClass="pl__card-accent--estrai"
                  modLabel="↓ Estrai"
                  meta={[
                    { key: 'Connessione', val: conn ? conn.name : 'N/A', ok: !!conn },
                    { key: 'Creato',      val: formatDate(project.created_at) },
                  ]}
                  scheduleCount={getScheduleCount(project.id)}
                  onOpen={() => handleOpenProject(project)}
                  onDelete={() => handleDeleteProject(project.id, project.name)}
                  onSchedule={() => navigate(`/schedules?project_id=${project.id}`)}
                />
              );
            })}
          </ProjectSection>

          {/* ── MANIPOLA ── */}
          <ProjectSection
            id="pl-section-manipola"
            chipClass="pl__chip--manipola"
            chipLabel="⚙ Manipola"
            subtitle="Trasformazione e join spaziale"
            onNew={() => openModalForType('manipola')}
            canCreate={canCreateProj}
            empty={filterBySearch(manipolaProjects).length === 0}
            emptyIcon="⚙"
            emptyTitle="Nessun progetto Manipola"
            emptyText="Crea un pipeline di trasformazione"
          >
            {filterBySearch(manipolaProjects).map(project => {
              const srcProjects = getSourceProjects(project.id);
              const fileIds = projectFileSources[project.id] || [];
              const fileNames = fileIds.map(id => files.find(f => f.id === id)?.name).filter(Boolean);
              const srcCount = srcProjects.length + fileNames.length;
              return (
                <ProjectCard
                  key={project.id}
                  project={project}
                  accentClass="pl__card-accent--manipola"
                  modLabel="⚙ Manipola"
                  meta={[
                    { key: 'Sorgenti', val: `${srcCount} progett${srcCount !== 1 ? 'i' : 'o'}` },
                    { key: 'Creato',   val: formatDate(project.created_at) },
                  ]}
                  scheduleCount={getScheduleCount(project.id)}
                  onOpen={() => handleOpenProject(project)}
                  onDelete={() => handleDeleteProject(project.id, project.name)}
                  onSchedule={() => navigate(`/schedules?project_id=${project.id}`)}
                />
              );
            })}
          </ProjectSection>

          {/* ── ARRICCHISCI ── */}
          <ProjectSection
            id="pl-section-arricchisci"
            chipClass="pl__chip--arricchisci"
            chipLabel="◈ Arricchisci"
            subtitle="API enrichment geospaziale"
            onNew={() => openModalForType('arricchisci')}
            canCreate={canCreateProj}
            empty={filterBySearch(arricchisciProjects).length === 0}
            emptyIcon="◈"
            emptyTitle="Nessun progetto Arricchisci"
            emptyText="Crea un nuovo pipeline di arricchimento"
          >
            {filterBySearch(arricchisciProjects).map(project => (
              <ProjectCard
                key={project.id}
                project={project}
                accentClass="pl__card-accent--arricchisci"
                modLabel="◈ Arricchisci"
                meta={[
                  { key: 'API', val: 'Enrichment' },
                  { key: 'Creato', val: formatDate(project.created_at) },
                ]}
                scheduleCount={getScheduleCount(project.id)}
                onOpen={() => handleOpenProject(project)}
                onDelete={() => handleDeleteProject(project.id, project.name)}
                onSchedule={() => navigate(`/schedules?project_id=${project.id}`)}
              />
            ))}
          </ProjectSection>

          {/* ── ESEGUIBILI ── */}
          <section id="pl-section-eseguibili" className="pl__section">
            <div className="pl__section-header">
              <div className="pl__section-title-wrap">
                <span className="pl__chip" style={{
                  background: 'rgba(124,58,237,0.15)', color: '#a78bfa',
                  borderColor: 'rgba(124,58,237,0.4)'
                }}>🚀 Eseguibili</span>
                <span className="pl__section-sub">Catene on-demand — l'utente carica un file e ottiene il risultato</span>
              </div>
              {isAdmin && (
                <button
                  type="button"
                  className="pl__btn pl__btn--ghost pl__btn--sm"
                  onClick={openEsegSelector}
                  title="Pubblica un progetto Arricchisci come eseguibile"
                >＋ Nuovo</button>
              )}
            </div>

            {filterBySearch(eseguibili).length === 0 ? (
              <div className="pl__empty">
                <canvas className="pl__empty-hex" />
                <div className="pl__empty-icon">🚀</div>
                <div className="pl__empty-title">Nessun eseguibile pubblicato</div>
                <div className="pl__empty-text">
                  {isAdmin
                    ? 'Pubblica un progetto Arricchisci come eseguibile usando il pulsante 📢 sulla sua card o "+ Nuovo" qui sopra.'
                    : 'Chiedi a un amministratore di pubblicare una catena come eseguibile.'}
                </div>
              </div>
            ) : (
              <div className="pl__cards-grid">
                {filterBySearch(eseguibili).map(item => (
                  <EseguibileCard
                    key={item.id}
                    item={item}
                    isAdmin={isAdmin}
                    onExecute={() => setExecuteEseguibileId(item.id)}
                    onDelete={() => handleDeleteEseguibile(item.id, item.name)}
                  />
                ))}
              </div>
            )}
          </section>

        </div>{/* /content */}
      </div>{/* /main */}

      {/* ── MODAL ──────────────────────────────────────────── */}
      {showModal && (
        <ModalWrapper
          isOpen={true}
          onClose={() => setShowModal(false)}
          title={`Nuovo progetto ${newProjectType === 'estrai' ? 'Estrai' : newProjectType === 'manipola' ? 'Manipola' : 'Arricchisci'}`}
          icon={newProjectType === 'estrai' ? '↓' : newProjectType === 'manipola' ? '⚙' : '◈'}
          size="md"
          closeOnOverlay={!creating}
          closeOnEsc={!creating}
          footer={
            <>
              <ModalButton variant="secondary" onClick={() => setShowModal(false)} disabled={creating}>Annulla</ModalButton>
              <ModalButton variant="primary" onClick={handleCreateProject} loading={creating}>
                Crea Progetto
              </ModalButton>
            </>
          }
        >
            {/* Tipo selector */}
            <div className="pl__modal-field">
              <label className="pl__modal-label">Tipo</label>
              <div className="pl__modal-type-row">
                {['estrai','manipola','arricchisci'].map(t => (
                  <button
                    key={t}
                    type="button"
                    className={`pl__modal-type-btn pl__modal-type-btn--${t} ${newProjectType === t ? 'pl__modal-type-btn--on' : ''}`}
                    onClick={() => setNewProjectType(t)}
                  >
                    {t === 'estrai' ? '↓ Estrai' : t === 'manipola' ? '⚙ Manipola' : '◈ Arricchisci'}
                  </button>
                ))}
              </div>
            </div>

            <div className="pl__modal-field">
              <label className="pl__modal-label">Nome *</label>
              <input
                type="text"
                className="pl__modal-input"
                value={newProjectName}
                onChange={e => setNewProjectName(e.target.value)}
                placeholder="Es. 'Catasto Campania Q2'"
                autoFocus
              />
            </div>

            <div className="pl__modal-field">
              <label className="pl__modal-label">Descrizione</label>
              <textarea
                className="pl__modal-textarea"
                value={newProjectDesc}
                onChange={e => setNewProjectDesc(e.target.value)}
                placeholder="Descrizione opzionale..."
                rows={2}
              />
            </div>

            {newProjectType === 'estrai' && (
              <div className="pl__modal-field">
                <label className="pl__modal-label">Connessione *</label>
                <select
                  className="pl__modal-select"
                  value={newProjectConnId}
                  onChange={e => setNewProjectConnId(e.target.value)}
                >
                  <option value="">-- Seleziona connessione --</option>
                  {connections.length > 0 && (
                    <optgroup label="Database">
                      {connections.map(c => <option key={c.id} value={c.id}>{c.name} ({c.database})</option>)}
                    </optgroup>
                  )}
                  {arcgisConnections.length > 0 && (
                    <optgroup label="ArcGIS Server">
                      {arcgisConnections.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
                    </optgroup>
                  )}
                </select>
                {connections.length === 0 && arcgisConnections.length === 0 && (
                  <p className="pl__modal-error">
                    Nessuna connessione.{' '}
                    <span className="pl__modal-error-link" onClick={() => { setShowModal(false); navigate('/connections'); }}>
                      Creane una
                    </span>
                  </p>
                )}
              </div>
            )}

            {newProjectType === 'manipola' && (
              <div className="pl__modal-info pl__modal-info--manipola">
                💡 I progetti Manipola usano come input i risultati dei progetti Estrai.
              </div>
            )}

            {newProjectType === 'arricchisci' && (
              <div className="pl__modal-info pl__modal-info--arricchisci">
                💡 I progetti Arricchisci chiamano API esterne per arricchire i dati estratti.
              </div>
            )}
        </ModalWrapper>
      )}

      {/* ── PUBLISH ESEGUIBILE MODAL ────────────────────────── */}
      {publishProject && (
        <PublishEseguibileModal
          project={publishProject}
          onClose={() => setPublishProject(null)}
          onSuccess={() => {
            toast.success('Eseguibile pubblicato — visibile nella sezione 🚀 Eseguibili');
            loadData();
          }}
        />
      )}

      {/* ── WIZARD ESECUZIONE ESEGUIBILE ─────────────────────── */}
      {executeEseguibileId && (
        <EseguibileExecuteWizard
          eseguibileId={executeEseguibileId}
          onClose={() => setExecuteEseguibileId(null)}
        />
      )}

      {/* ── SELETTORE PROGETTO SORGENTE PER NUOVO ESEGUIBILE ── */}
      {showEsegSelector && (
        <ModalWrapper
          isOpen={true}
          onClose={() => setShowEsegSelector(false)}
          title="Pubblica come Eseguibile"
          icon="📢"
          subtitle="Seleziona un progetto Arricchisci da pubblicare come catena on-demand"
          size="md"
          footer={
            <ModalButton variant="secondary" onClick={() => setShowEsegSelector(false)}>Annulla</ModalButton>
          }
        >
            <div className="pl__modal-field">
              <input
                type="text"
                className="pl__modal-input"
                placeholder="Cerca progetto Arricchisci..."
                value={esegSelectorSearch}
                onChange={(e) => setEsegSelectorSearch(e.target.value)}
                autoFocus
              />
            </div>

            <div style={{ maxHeight: 360, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 6 }}>
              {arricchisciProjects.length === 0 ? (
                <div style={{ padding: 24, textAlign: 'center', color: 'var(--pl-text-sub)', fontSize: 13 }}>
                  Nessun progetto Arricchisci disponibile.<br />
                  <span style={{ fontSize: 11.5 }}>Crea prima un progetto Arricchisci.</span>
                </div>
              ) : (
                arricchisciProjects
                  .filter(p => !esegSelectorSearch || (p.name || '').toLowerCase().includes(esegSelectorSearch.toLowerCase()))
                  .map(p => (
                    <div
                      key={p.id}
                      onClick={() => {
                        setShowEsegSelector(false);
                        setPublishProject(p);
                      }}
                      style={{
                        display: 'flex', alignItems: 'center', gap: 12,
                        padding: '10px 14px', borderRadius: 8, cursor: 'pointer',
                        border: '1px solid var(--pl-border)',
                        background: 'var(--pl-surface-2)',
                        transition: 'all 0.15s',
                      }}
                      onMouseEnter={e => {
                        e.currentTarget.style.borderColor = 'rgba(124,58,237,0.5)';
                        e.currentTarget.style.background = 'rgba(124,58,237,0.06)';
                      }}
                      onMouseLeave={e => {
                        e.currentTarget.style.borderColor = 'var(--pl-border)';
                        e.currentTarget.style.background = 'var(--pl-surface-2)';
                      }}
                    >
                      <span style={{ fontSize: 18, color: '#a78bfa' }}>◈</span>
                      <div style={{ flexGrow: 1, minWidth: 0 }}>
                        <div style={{ fontSize: 13.5, fontWeight: 500, color: 'var(--pl-text)' }}>{p.name}</div>
                        {p.description && (
                          <div style={{ fontSize: 11.5, color: 'var(--pl-text-sub)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                            {p.description}
                          </div>
                        )}
                      </div>
                      <span style={{ color: 'var(--pl-text-sub)', fontSize: 16 }}>→</span>
                    </div>
                  ))
              )}
            </div>

        </ModalWrapper>
      )}

    </div>
  );
}

// ── Section wrapper ──────────────────────────────────────────────────────
function ProjectSection({ id, chipClass, chipLabel, subtitle, onNew, canCreate = true, empty, emptyIcon, emptyTitle, emptyText, children }) {
  return (
    <section id={id} className="pl__section">
      <div className="pl__section-header">
        <div className="pl__section-title-wrap">
          <span className={`pl__chip ${chipClass}`}>{chipLabel}</span>
          <span className="pl__section-sub">{subtitle}</span>
        </div>
        {canCreate && (
          <button type="button" className="pl__btn pl__btn--ghost pl__btn--sm" onClick={onNew}>＋ Nuovo</button>
        )}
      </div>

      {empty ? (
        <div className="pl__empty">
          <canvas className="pl__empty-hex" />
          <div className="pl__empty-icon">{emptyIcon}</div>
          <div className="pl__empty-title">{emptyTitle}</div>
          <div className="pl__empty-text">{emptyText}</div>
        </div>
      ) : (
        <div className="pl__cards-grid">{children}</div>
      )}
    </section>
  );
}

// ── Project card ─────────────────────────────────────────────────────────
function ProjectCard({ project, accentClass, modLabel, meta, scheduleCount, onOpen, onDelete, onSchedule }) {
  const lastRun = project.last_run_at;
  const status  = project.last_status;

  const statusLabel = status === 'success' ? 'Completato'
    : status === 'running' ? 'In esecuzione'
    : status === 'error'   ? 'Errore'
    : 'Idle';
  const statusMod = status === 'success' ? 'pl__status--ok'
    : status === 'error' ? 'pl__status--err'
    : '';

  return (
    <div className="pl__card" onClick={onOpen}>
      <div className={`pl__card-accent ${accentClass}`} />
      <div className="pl__card-body">
        <div className="pl__card-header-row">
          <div className="pl__card-name">{project.name}</div>
          <span className="pl__card-mod">{modLabel}</span>
        </div>
        <div className="pl__card-desc">{project.description || 'Nessuna descrizione'}</div>
        <div className="pl__card-meta">
          {meta.map(({ key, val }) => (
            <div key={key}>
              <div className="pl__meta-key">{key}</div>
              <div className="pl__meta-val">{val}</div>
            </div>
          ))}
        </div>
        <div className="pl__card-footer">
          <span className={`pl__status ${statusMod}`}>{statusLabel}</span>
          <div className="pl__card-actions">
            {scheduleCount > 0 && (
              <button type="button" className="pl__btn pl__btn--ghost pl__btn--sm pl__btn--schedule"
                onClick={e => { e.stopPropagation(); onSchedule(); }}
                title="Schedule automatici"
              >
                ⏰ <span className="pl__schedule-badge">{scheduleCount}</span>
              </button>
            )}
            <button type="button" className="pl__btn pl__btn--ghost pl__btn--sm"
              onClick={e => { e.stopPropagation(); onDelete(); }}
            >🗑</button>
            <button type="button" className="pl__btn pl__btn--primary pl__btn--sm"
              onClick={e => { e.stopPropagation(); onOpen(); }}
            >Apri →</button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Eseguibile card ──────────────────────────────────────────────────────
function EseguibileCard({ item, isAdmin, onExecute, onDelete }) {
  const fmt = (item.default_output_format || 'xlsx').toUpperCase();
  return (
    <div className="pl__card" onClick={onExecute}>
      <div className="pl__card-accent" style={{ background: 'linear-gradient(180deg,#a78bfa,#7c3aed)' }} />
      <div className="pl__card-body">
        <div className="pl__card-header-row">
          <div className="pl__card-name">{item.name}</div>
          <span className="pl__card-mod" style={{ background: 'rgba(124,58,237,0.15)', color: '#a78bfa' }}>🚀 {fmt}</span>
        </div>
        <div className="pl__card-desc">{item.description || 'Nessuna descrizione'}</div>
        <div className="pl__card-meta">
          <div>
            <div className="pl__meta-key">Colonne attese</div>
            <div className="pl__meta-val">{item.expected_columns_count ?? '—'}</div>
          </div>
          <div>
            <div className="pl__meta-key">Output</div>
            <div className="pl__meta-val">{fmt}</div>
          </div>
        </div>
        <div className="pl__card-footer">
          <span className="pl__status">On-demand</span>
          <div className="pl__card-actions">
            {isAdmin && (
              <button type="button" className="pl__btn pl__btn--ghost pl__btn--sm"
                onClick={e => { e.stopPropagation(); onDelete(); }}
                title="Elimina eseguibile"
              >🗑</button>
            )}
            <button type="button" className="pl__btn pl__btn--primary pl__btn--sm"
              onClick={e => { e.stopPropagation(); onExecute(); }}
            >▶ Esegui</button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Empty hex canvas ─────────────────────────────────────────────────────
function useEmptyHexCanvases(theme) {
  useEffect(() => {
    const draw = (canvas) => {
      if (!canvas) return;
      const isDark = (theme || document.documentElement.getAttribute('data-theme')) !== 'light';
      const W = canvas.offsetWidth  || 280;
      const H = canvas.offsetHeight || 120;
      canvas.width  = W; canvas.height = H;
      const ctx = canvas.getContext('2d');
      ctx.clearRect(0, 0, W, H);
      const ac = isDark
        ? [[75,139,255],[52,193,122],[155,109,255]]
        : [[37,99,235],[22,163,74],[124,58,237]];
      const rng = s => { let x = Math.sin(s+1)*43758.5453; return x-Math.floor(x); };
      const R = 14;
      const hexW = R * Math.sqrt(3), hexH = R * 2;
      const cols = Math.ceil(W/hexW)+1, rows = Math.ceil(H/(hexH*0.75))+1;
      for (let col=0; col<cols; col++) for (let row=0; row<rows; row++) {
        const cx = col*hexW+(row%2===0?0:hexW/2), cy = row*hexH*0.75;
        const dx=(cx-W/2)/(W/2), dy=(cy-H/2)/(H/2);
        const dist = Math.sqrt(dx*dx+dy*dy);
        const seed = col*13+row*7;
        const jitter = (rng(seed)-0.5)*0.4;
        const op = Math.min(1, Math.max(0, (1-dist*0.85)+jitter*(1-dist)));
        if (op<0.04) continue;
        ctx.beginPath();
        for (let i=0;i<6;i++){const a=(Math.PI/180)*(60*i-30);i===0?ctx.moveTo(cx+R*Math.cos(a),cy+R*Math.sin(a)):ctx.lineTo(cx+R*Math.cos(a),cy+R*Math.sin(a));}
        ctx.closePath();
        const a=ac[Math.abs(col+row)%3];
        ctx.strokeStyle=`rgba(${a[0]},${a[1]},${a[2]},${(op*0.22).toFixed(2)})`;
        ctx.lineWidth=0.8; ctx.stroke();
      }
    };
    document.querySelectorAll('.pl__empty-hex').forEach(draw);
  });
}

// Hook invoked at module level via useEffect in the main component would be cleaner,
// but for simplicity just call it inline via a small wrapper
function EmptyHexDrawer({ theme }) {
  useEmptyHexCanvases(theme);
  return null;
}

export default ProjectList;
