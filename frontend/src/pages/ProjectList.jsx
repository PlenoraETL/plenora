/**
 * ProjectList.jsx — Home page / Dashboard progetti
 * Redesign con sidebar + hex canvas background
 * Route: /
 */
import React, { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import { useToast } from '../hooks/useToast';
import { useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { deleteProject, deleteEseguibile, cloneProject, updateProject } from '../api';
import { markProjectDeleted } from '../utils/localDrafts';
import NewProjectModal from '../components/projects/NewProjectModal';
import EsegSelectorModal from '../components/projects/EsegSelectorModal';
import ProjectSection from '../components/projects/ProjectSection';
import ProjectCard from '../components/projects/ProjectCard';
import ProjectListSkeleton from '../components/projects/ProjectListSkeleton';
import QuickStart from '../components/projects/QuickStart';
import EseguibiliSection from '../components/projects/EseguibiliSection';
import { useProjectListData } from '../components/projects/useProjectListData';
import { handleCascade409, formatCascadeDependencies } from '../api/errors';
import ConfirmDialog from '../components/common/ConfirmDialog';
import { useConfirmModal } from '../hooks/useConfirmModal';
import { useHexCanvas } from '../utils/hexCanvas';
import PublishEseguibileModal from '../components/eseguibili/PublishEseguibileModal';
import EseguibileExecuteWizard from '../components/eseguibili/EseguibileExecuteWizard';
import AppSidebar from '../components/AppSidebar';
import ThemeToggle from '../components/common/ThemeToggle';
import { useTheme } from '../hooks/useTheme';
import { can } from '../utils/permissions';
import { toastApiError } from '../utils/toastApiError';
import { humanizeErrorMessage } from '../utils/errorMessages';
import '../styles/ProjectList.css';
import { projectEditorPath } from '../utils/projectRoutes';
function ProjectList() {
  const toast    = useToast();
  const confirmModal = useConfirmModal();
  const navigate = useNavigate();
  const location = useLocation();
  const { user } = useAuth();

  // Data caricati via hook condiviso (8 fetch parallele + indici manipola).
  const {
    projects,
    connections,
    arcgisConnections,
    apiConnections,
    files,
    schedules,
    dataSources,
    eseguibili,
    projectSources,
    projectFileSources,
    runStatusByProject,
    loading,
    reload: loadData,
    removeProjectLocal,
    updateProjectLocal,
  } = useProjectListData();

  // UI state
  const [showModal,         setShowModal]         = useState(false);
  const [newProjectInitialType, setNewProjectInitialType] = useState('estrai');
  const [publishProject, setPublishProject] = useState(null); // progetto da pubblicare come eseguibile
  const [executeEseguibileId, setExecuteEseguibileId] = useState(null); // id eseguibile da lanciare nel wizard
  const [showEsegSelector, setShowEsegSelector] = useState(false);      // selettore progetto sorgente per nuovo eseguibile
  const [search,            setSearch]            = useState('');
  // #11 filtri smart: stato ultima run + ordinamento.
  const [statusFilter,      setStatusFilter]      = useState('all');   // all|success|failure|running|never
  const [tagFilter,         setTagFilter]         = useState('all');   // all|<tag>
  const [sortBy,            setSortBy]            = useState('recent'); // recent|oldest|name
  const { theme } = useTheme();


  const canvasRef = useRef(null);
  useHexCanvas(canvasRef);

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

  const handleDeleteProject = async (id, name) => {
    const ok = await confirmModal.confirm({
      title: 'Elimina progetto',
      message: `Eliminare "${name}"?`,
      confirmText: 'Elimina',
      variant: 'danger',
    });
    if (!ok) return;
    // Optimistic (Epic C): la card sparisce subito; su errore ripristiniamo
    // lo stato reale con loadData(). Niente attesa del round-trip.
    removeProjectLocal(id);
    try {
      await deleteProject(id);
      markProjectDeleted(id);
      toast.success(`"${name}" eliminato`);
      return;
    } catch (err) {
      loadData();   // ripristina la card eliminata ottimisticamente
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
      // Cascade cleanup cross-DB fallita su step critici.
      // Invariante: `project_preserved:true` → il
      // progetto e' ancora presente, l'utente puo' ritentare.
      // Preferisce err.code (new envelope) con fallback a detail.error (legacy).
      const detail = err?.response?.data?.detail;
      const errorCode = err?.code || (detail && typeof detail === 'object' ? detail.error : null);
      if (err?.response?.status === 500 && errorCode === 'project_cleanup_partial') {
        const cleanupMsg = humanizeErrorMessage(err, 'cleanup parziale');
        toast.error(
          `Impossibile eliminare "${name}": ${cleanupMsg}. Ritenta o contatta admin.`,
          10000
        );
        return;
      }
      // Fallback: se detail e' oggetto (shape inattesa), usa err.message
      // per evitare "[object Object]" nel toast.
      toast.error('Errore eliminazione: ' + humanizeErrorMessage(err));
    }
  };

  const handleSetTags = async (project, tags) => {
    const prev = project.tags || [];
    updateProjectLocal(project.id, { tags });   // optimistic
    try {
      await updateProject(project.id, { tags });
    } catch (err) {
      updateProjectLocal(project.id, { tags: prev });  // rollback
      toastApiError(toast, err, 'Errore aggiornamento tag');
    }
  };

  const handleRenameProject = async (project, newName) => {
    const name = (newName || '').trim();
    if (!name || name === project.name) return;
    updateProjectLocal(project.id, { name });   // optimistic, niente flash
    try {
      await updateProject(project.id, { name });
    } catch (err) {
      updateProjectLocal(project.id, { name: project.name });  // rollback
      toastApiError(toast, err, 'Errore rinomina');
    }
  };

  const handleCloneProject = async (project) => {
    try {
      const res = await cloneProject(project.id);
      const newName = res?.metadata?.name || `Copia di ${project.name}`;
      toast.success(`Progetto clonato: "${newName}"`);
      loadData();
    } catch (err) {
      toastApiError(toast, err, 'Errore clonazione');
    }
  };

  const canCreateProj = can.createProject(user);

  const handleDeleteEseguibile = async (id, name) => {
    const ok = await confirmModal.confirm({
      title: 'Elimina eseguibile',
      message: `Eliminare l'eseguibile "${name}"?`,
      confirmText: 'Elimina',
      variant: 'danger',
    });
    if (!ok) return;
    try {
      await deleteEseguibile(id);
      toast.success('Eseguibile eliminato');
      loadData();
    } catch (err) {
      toastApiError(toast, err, 'Errore eliminazione');
    }
  };

  const openEsegSelector = () => {
    setShowEsegSelector(true);
  };

  // Mappa unica tipo -> editor (utils/projectRoutes, R7.4).
  const handleOpenProject = (p) => navigate(projectEditorPath(p));

  const openModalForType = (type) => {
    setNewProjectInitialType(type);
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

  // #10: unione di tutti i tag presenti (per il dropdown di filtro).
  const allTags = useMemo(() => {
    const s = new Set();
    projects.forEach(p => (p.tags || []).forEach(t => s.add(t)));
    return Array.from(s).sort((a, b) => a.localeCompare(b));
  }, [projects]);

  // Search testuale (usata anche dalla sezione Eseguibili, che non ha run/stato).
  const filterBySearch = (list) =>
    search.trim()
      ? list.filter(p => p.name.toLowerCase().includes(search.toLowerCase()))
      : list;

  // #11: search + filtro stato ultima run + ordinamento. Applicato alle 3
  // sezioni progetto. Lo stato run viene da `runStatusByProject` (log_runs):
  // success | failure | running, oppure assente = "mai eseguito".
  const ts = (p) => {
    const d = new Date(p.updated_at || p.created_at || 0).getTime();
    return Number.isNaN(d) ? 0 : d;
  };
  const applyFilters = useCallback((list) => {
    let out = list;
    if (search.trim()) {
      const q = search.toLowerCase();
      out = out.filter(p => p.name.toLowerCase().includes(q));
    }
    if (statusFilter !== 'all') {
      out = out.filter(p => {
        const st = runStatusByProject[p.id]?.status;
        return statusFilter === 'never' ? !st : st === statusFilter;
      });
    }
    if (tagFilter !== 'all') {
      out = out.filter(p => (p.tags || []).includes(tagFilter));
    }
    const sorted = [...out];
    if (sortBy === 'name')        sorted.sort((a, b) => a.name.localeCompare(b.name));
    else if (sortBy === 'oldest') sorted.sort((a, b) => ts(a) - ts(b));
    else                          sorted.sort((a, b) => ts(b) - ts(a)); // recent
    return sorted;
  }, [search, statusFilter, tagFilter, sortBy, runStatusByProject]);

  // Filtri non-default attivi? (per badge "azzera").
  const filtersActive = statusFilter !== 'all' || tagFilter !== 'all' || sortBy !== 'recent';

  if (loading) {
    return <ProjectListSkeleton />;
  }

  return (
    <div className="pl" data-theme-local={theme}>
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
<div className="pl__main">

        {/* Topbar */}
        <header className="pl__topbar">
          <div className="pl__page-title">Dashboard</div>
          <div className="pl__topbar-right">
            <div className="pl__search-box">
              <span className="pl__search-icon">⌕</span>
              <input
                placeholder="Cerca progetti..."
                aria-label="Cerca progetti"
                value={search}
                onChange={e => setSearch(e.target.value)}
              />
            </div>
            {/* #11 filtri smart: stato ultima run + ordinamento */}
            <select
              className={`pl__filter-select${statusFilter !== 'all' ? ' pl__filter-select--active' : ''}`}
              aria-label="Filtra per stato"
              value={statusFilter}
              onChange={e => setStatusFilter(e.target.value)}
            >
              <option value="all">Tutti gli stati</option>
              <option value="success">✓ Completati</option>
              <option value="failure">✕ Falliti</option>
              <option value="running">⟳ In esecuzione</option>
              <option value="never">○ Mai eseguiti</option>
            </select>
            {allTags.length > 0 && (
              <select
                className={`pl__filter-select${tagFilter !== 'all' ? ' pl__filter-select--active' : ''}`}
                aria-label="Filtra per tag"
                value={tagFilter}
                onChange={e => setTagFilter(e.target.value)}
              >
                <option value="all">Tutti i tag</option>
                {allTags.map(t => <option key={t} value={t}>🏷 {t}</option>)}
              </select>
            )}
            <select
              className="pl__filter-select"
              aria-label="Ordina"
              value={sortBy}
              onChange={e => setSortBy(e.target.value)}
            >
              <option value="recent">Più recenti</option>
              <option value="oldest">Meno recenti</option>
              <option value="name">Nome A–Z</option>
            </select>
            {filtersActive && (
              <button
                type="button"
                className="pl__btn pl__btn--ghost pl__btn--sm"
                onClick={() => { setStatusFilter('all'); setTagFilter('all'); setSortBy('recent'); }}
                title="Azzera filtri"
              >
                ✕ Filtri
              </button>
            )}
            <ThemeToggle classPrefix="pl" />
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
          {projects.length === 0 ? (
            <QuickStart
              hasDataSource={connections.length + arcgisConnections.length + apiConnections.length + dataSources.length > 0}
              canCreate={canCreateProj}
              onNewProject={openModalForType}
            />
          ) : (
            <>
<ProjectSection
            id="pl-section-estrai"
            chipClass="pl__chip--estrai"
            chipLabel="↓ Estrai"
            subtitle="Estrazione da sorgenti SQL e GIS"
            onNew={() => openModalForType('estrai')}
            canCreate={canCreateProj}
            empty={applyFilters(estraiProjects).length === 0}
            emptyIcon="↓"
            emptyTitle="Nessun progetto Estrai"
            emptyText="Crea il tuo primo progetto per estrarre dati"
          >
            {applyFilters(estraiProjects).map(project => {
              const conn = getConn(project.default_connection_id);
              return (
                <ProjectCard
                  key={project.id}
                  project={project}
                  runStatus={runStatusByProject[project.id]}
                  accentClass="pl__card-accent--estrai"
                  modLabel="↓ Estrai"
                  meta={[
                    { key: 'Connessione', val: conn ? conn.name : 'N/A', ok: !!conn },
                    { key: 'Creato',      val: formatDate(project.created_at) },
                  ]}
                  scheduleCount={getScheduleCount(project.id)}
                  onOpen={() => handleOpenProject(project)}
                  onDelete={can.deleteProject(user, project) ? () => handleDeleteProject(project.id, project.name) : null}
                  onClone={canCreateProj ? () => handleCloneProject(project) : null}
                  onRename={can.editProjectMeta(user, project) ? (newName) => handleRenameProject(project, newName) : null}
                  onSetTags={can.editProjectMeta(user, project) ? (tags) => handleSetTags(project, tags) : null}
                  onSchedule={() => navigate(`/schedules?project_id=${project.id}`)}
                />
              );
            })}
          </ProjectSection>
<ProjectSection
            id="pl-section-manipola"
            chipClass="pl__chip--manipola"
            chipLabel="⚙ Manipola"
            subtitle="Trasformazione e join spaziale"
            onNew={() => openModalForType('manipola')}
            canCreate={canCreateProj}
            empty={applyFilters(manipolaProjects).length === 0}
            emptyIcon="⚙"
            emptyTitle="Nessun progetto Manipola"
            emptyText="Crea un pipeline di trasformazione"
          >
            {applyFilters(manipolaProjects).map(project => {
              const srcProjects = getSourceProjects(project.id);
              const fileIds = projectFileSources[project.id] || [];
              const fileNames = fileIds.map(id => files.find(f => f.id === id)?.name).filter(Boolean);
              const srcCount = srcProjects.length + fileNames.length;
              return (
                <ProjectCard
                  key={project.id}
                  project={project}
                  runStatus={runStatusByProject[project.id]}
                  accentClass="pl__card-accent--manipola"
                  modLabel="⚙ Manipola"
                  meta={[
                    { key: 'Sorgenti', val: `${srcCount} progett${srcCount !== 1 ? 'i' : 'o'}` },
                    { key: 'Creato',   val: formatDate(project.created_at) },
                  ]}
                  scheduleCount={getScheduleCount(project.id)}
                  onOpen={() => handleOpenProject(project)}
                  onDelete={can.deleteProject(user, project) ? () => handleDeleteProject(project.id, project.name) : null}
                  onClone={canCreateProj ? () => handleCloneProject(project) : null}
                  onRename={can.editProjectMeta(user, project) ? (newName) => handleRenameProject(project, newName) : null}
                  onSetTags={can.editProjectMeta(user, project) ? (tags) => handleSetTags(project, tags) : null}
                  onSchedule={() => navigate(`/schedules?project_id=${project.id}`)}
                />
              );
            })}
          </ProjectSection>
<ProjectSection
            id="pl-section-arricchisci"
            chipClass="pl__chip--arricchisci"
            chipLabel="◈ Arricchisci"
            subtitle="API enrichment geospaziale"
            onNew={() => openModalForType('arricchisci')}
            canCreate={canCreateProj}
            empty={applyFilters(arricchisciProjects).length === 0}
            emptyIcon="◈"
            emptyTitle="Nessun progetto Arricchisci"
            emptyText="Crea un nuovo pipeline di arricchimento"
          >
            {applyFilters(arricchisciProjects).map(project => (
              <ProjectCard
                key={project.id}
                project={project}
                runStatus={runStatusByProject[project.id]}
                accentClass="pl__card-accent--arricchisci"
                modLabel="◈ Arricchisci"
                meta={[
                  { key: 'API', val: 'Enrichment' },
                  { key: 'Creato', val: formatDate(project.created_at) },
                ]}
                scheduleCount={getScheduleCount(project.id)}
                onOpen={() => handleOpenProject(project)}
                onDelete={can.deleteProject(user, project) ? () => handleDeleteProject(project.id, project.name) : null}
                onClone={canCreateProj ? () => handleCloneProject(project) : null}
                onRename={can.editProjectMeta(user, project) ? (newName) => handleRenameProject(project, newName) : null}
                onSetTags={can.editProjectMeta(user, project) ? (tags) => handleSetTags(project, tags) : null}
                onSchedule={() => navigate(`/schedules?project_id=${project.id}`)}
              />
            ))}
          </ProjectSection>
<EseguibiliSection
            items={filterBySearch(eseguibili)}
            canCreate={can.createEseguibile(user)}
            canDelete={(item) => can.manageEseguibile(user, item)}
            onNew={openEsegSelector}
            onExecute={(item) => setExecuteEseguibileId(item.id)}
            onDelete={(item) => handleDeleteEseguibile(item.id, item.name)}
          />
            </>
          )}

        </div>{/* /content */}
      </div>{/* /main */}
      <NewProjectModal
        isOpen={showModal}
        initialType={newProjectInitialType}
        connections={connections}
        arcgisConnections={arcgisConnections}
        onClose={() => setShowModal(false)}
        onCreated={loadData}
      />
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
{executeEseguibileId && (
        <EseguibileExecuteWizard
          eseguibileId={executeEseguibileId}
          onClose={() => setExecuteEseguibileId(null)}
        />
      )}
      <EsegSelectorModal
        isOpen={showEsegSelector}
        arricchisciProjects={arricchisciProjects}
        projects={projects}
        onClose={() => setShowEsegSelector(false)}
        onSelect={(p) => {
          setShowEsegSelector(false);
          setPublishProject(p);
        }}
      />

      <ConfirmDialog {...confirmModal} />
    </div>
  );
}
// Pre-fix (P2 frontend-pages): `useEmptyHexCanvases` + `EmptyHexDrawer`
// erano definiti qui ma `EmptyHexDrawer` non veniva mai renderizzato
// nel JSX di ProjectList → i `<canvas class="pl__empty-hex">` in
// ProjectSection/EseguibiliSection restavano sempre vuoti (il loop
// `querySelectorAll` non scattava mai). Dead code di ~35 righe.
// Post-fix: rimossi. I canvas restano nel DOM come placeholder vuoti
// (allineato al comportamento osservato a runtime) — eventuali
// disegni saranno reintrodotti con un componente esplicito quando/se
// servira'.

export default ProjectList;
