/**
 * ProjectList.jsx — Home page / Dashboard progetti
 * Redesign con sidebar + hex canvas background
 * Route: /
 */
import React, { useState, useMemo, useCallback, useRef } from 'react';
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
import PageLayout from '../components/common/PageLayout';
import { useTheme } from '../hooks/useTheme';
import { can } from '../utils/permissions';
import { toastApiError } from '../utils/toastApiError';
import { humanizeErrorMessage } from '../utils/errorMessages';
import { useBusy } from '../hooks/useBusy';
import { ResourceError } from '../components/common/ListState';
import '../styles/ProjectList.css';
import { projectEditorPath } from '../utils/projectRoutes';
import { formatDateTime } from '../utils/format';
import SearchField, { SearchEmpty } from '../components/common/SearchField';
import { useSearch } from '../hooks/useSearch';
// Sezioni della dashboard raggiungibili dalla sidebar (hash dell'URL).
const DASHBOARD_SECTIONS = { estrai: 'Estrai', manipola: 'Manipola', arricchisci: 'Arricchisci' };

// Ricerca della dashboard: nome, descrizione e tag (hooks/useSearch).
const searchValues = (p) => [p.name, p.description, p.tags];

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
    runStatusUnavailable,
    runStatusFor,
    failures,
    resource,
    loading,
    reload: loadData,
    removeProjectLocal,
    updateProjectOptimistic,
  } = useProjectListData();
  // Clonazione per progetto (Fase 5 T2, R9.11): doppio clic = un clone.
  const cloning = useBusy();

  // UI state
  const [showModal,         setShowModal]         = useState(false);
  const [newProjectInitialType, setNewProjectInitialType] = useState('estrai');
  const [publishProject, setPublishProject] = useState(null); // progetto da pubblicare come eseguibile
  const [executeEseguibileId, setExecuteEseguibileId] = useState(null); // id eseguibile da lanciare nel wizard
  const [showEsegSelector, setShowEsegSelector] = useState(false);      // selettore progetto sorgente per nuovo eseguibile
  const search = useSearch();
  // #11 filtri smart: stato ultima run + ordinamento.
  const [statusFilter,      setStatusFilter]      = useState('all');   // all|success|failure|running|never
  const [tagFilter,         setTagFilter]         = useState('all');   // all|<tag>
  const [sortBy,            setSortBy]            = useState('recent'); // recent|oldest|name
  const { theme } = useTheme();


  const canvasRef = useRef(null);
  useHexCanvas(canvasRef);

  // DB5: le voci Estrai/Manipola/Arricchisci della sidebar (/#estrai, ...)
  // mostrano SOLO quella sezione, col suo titolo (prima scorrevano a
  // un'ancora e il titolo restava «Dashboard»).
  const sectionKey = location.hash?.replace('#', '') || '';
  const section = DASHBOARD_SECTIONS[sectionKey] ? sectionKey : null;
  const showSection = (key) => !section || section === key;

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
          `Impossibile eliminare "${name}": ${formatCascadeDependencies(cascade)}. Rimuovili prima.`,
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

  // Ottimistiche per campo (Fase 5 T2, R9.13): un errore riporta il valore
  // confermato dal server, mai sopra una modifica piu' recente.
  const handleSetTags = async (project, tags) => {
    try {
      await updateProjectOptimistic(project, { tags }, () => updateProject(project.id, { tags }));
    } catch (err) {
      toastApiError(toast, err, 'Errore aggiornamento tag');
    }
  };

  const handleRenameProject = async (project, newName) => {
    const name = (newName || '').trim();
    if (!name || name === project.name) return;
    try {
      await updateProjectOptimistic(project, { name }, () => updateProject(project.id, { name }));
    } catch (err) {
      toastApiError(toast, err, 'Errore rinomina');
    }
  };

  const handleCloneProject = async (project) => {
    try {
      const r = await cloning.run(project.id, () => cloneProject(project.id));
      if (r.skipped) return;
      const res = r.value;
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
  const filterBySearch = (list) => search.filter(list, searchValues);

  // #11: search + filtro stato ultima run + ordinamento. Applicato alle 3
  // sezioni progetto. Lo stato run viene da `runStatusByProject` (log_runs):
  // success | failure | running, oppure assente = "mai eseguito".
  const ts = (p) => {
    const d = new Date(p.updated_at || p.created_at || 0).getTime();
    return Number.isNaN(d) ? 0 : d;
  };
  const applyFilters = useCallback((list) => {
    let out = search.filter(list, searchValues);
    // Stato delle esecuzioni non caricato: il filtro per stato non si
    // applica (non si sa), lo dice l'avviso sopra le sezioni.
    if (statusFilter !== 'all' && !runStatusUnavailable) {
      out = out.filter(p => {
        const st = runStatusFor(p.id)?.status;
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
  }, [search.filter, statusFilter, tagFilter, sortBy, runStatusFor, runStatusUnavailable]);

  // Filtri non-default attivi? (per badge "azzera").
  const filtersActive = statusFilter !== 'all' || tagFilter !== 'all' || sortBy !== 'recent';
  // Lista vuota per un filtro o per la ricerca (P18): il testo lo dice.
  const narrowed = search.active || statusFilter !== 'all' || tagFilter !== 'all';

  // perf-6: i filtri si applicano UNA volta per sezione (prima due volte a
  // render: per lo stato vuoto e per le card).
  const shownEstrai = useMemo(() => applyFilters(estraiProjects), [applyFilters, estraiProjects]);
  const shownManipola = useMemo(() => applyFilters(manipolaProjects), [applyFilters, manipolaProjects]);
  const shownArricchisci = useMemo(() => applyFilters(arricchisciProjects), [applyFilters, arricchisciProjects]);
  const shownEseguibili = section ? [] : filterBySearch(eseguibili);
  // Conteggio della ricerca: le sezioni visibili (tutte, o quella della sidebar).
  const visibleSections = [
    ['estrai', shownEstrai, estraiProjects],
    ['manipola', shownManipola, manipolaProjects],
    ['arricchisci', shownArricchisci, arricchisciProjects],
  ].filter(([key]) => showSection(key));
  const searchShown = visibleSections.reduce((n, [, shown]) => n + shown.length, shownEseguibili.length);
  const searchTotal = visibleSections.reduce((n, [, , all]) => n + all.length, section ? 0 : eseguibili.length);
  const fileNameById = useMemo(() => new Map(files.map(f => [f.id, f.name])), [files]);
  const emptyFor = (what, firstText) => (narrowed
    ? { title: `Nessun progetto ${what} con questi filtri`, text: 'Cambia o azzera la ricerca e i filtri.' }
    : { title: `Nessun progetto ${what}`, text: firstText });

  if (loading) {
    return <ProjectListSkeleton />;
  }

  return (
    <PageLayout
      className="pl"
      data-theme-local={theme}
      bare
      title={section ? DASHBOARD_SECTIONS[section] : 'Dashboard'}
      subtitle={section ? 'Progetti di questa sezione — «Dashboard» nella barra laterale per tutti' : null}
      actions={(
        <>
            <SearchField
              search={search}
              label="Cerca progetti"
              className="pl__search"
              shown={searchShown}
              total={searchTotal}
            />
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
            <button type="button" className="pl__btn pl__btn--ghost" onClick={() => loadData()} disabled={resource.refreshing}>{resource.refreshing ? '⟳ Aggiornamento…' : '⟳ Aggiorna'}</button>
            {canCreateProj && (
              <button type="button" className="pl__btn pl__btn--primary" onClick={() => setShowModal(true)}>＋ Nuovo Progetto</button>
            )}
        </>
      )}
    >
        <div className="pl__content">
          <canvas ref={canvasRef} className="pl__hex-bg" />

          {resource.status === 'error' && (
            <ResourceError resource={resource} title="Progetti non caricati" />
          )}
          {failures.length > 0 && (
            <ResourceError compact title="Alcuni dati non caricati" message={failures.join(', ')} onRetry={resource.reload} />
          )}
          <SearchEmpty search={search} shown={searchShown} />

          {/* KPI (DB2, G2): solo per ciò che esiste; ognuno porta alla sua
              pagina o sezione (pulsanti veri, a11y-1). */}
          {(() => {
            const tiles = [
              { val: estraiProjects.length,                         label: 'Estrai',      accent: 'var(--pl-estrai)',   to: '/#estrai' },
              { val: manipolaProjects.length,                       label: 'Manipola',    accent: 'var(--pl-manipola)', to: '/#manipola' },
              { val: arricchisciProjects.length,                    label: 'Arricchisci', accent: 'var(--pl-arricch)',  to: '/#arricchisci' },
              { val: eseguibili.length,                             label: 'Eseguibili',  accent: 'var(--pl-blue)',     to: '/eseguibili' },
              { val: connections.length + arcgisConnections.length, label: 'Connessioni', accent: 'var(--pl-blue)',     to: '/connections' },
              { val: activeSchedules.length,                        label: 'Schedule attivi', accent: 'var(--pl-estrai)', to: '/schedules' },
              { val: apiConnections.length,                         label: 'API Esterne', accent: 'var(--pl-manipola)', to: '/external-connectors' },
              { val: files.length,                                  label: 'File',        accent: 'var(--pl-arricch)',  to: '/files' },
            ].filter(t => t.val > 0);
            if (tiles.length === 0) return null;
            return (
              <div className="pl__stats-row">
                {tiles.map(({ val, label, accent, to }) => (
                  <button
                    type="button"
                    key={label}
                    className="pl__stat pl__stat--clickable"
                    style={{ '--stat-accent': accent }}
                    onClick={() => navigate(to)}
                  >
                    <span className="pl__stat-val">{val}</span>
                    <span className="pl__stat-label">{label}</span>
                  </button>
                ))}
              </div>
            );
          })()}
          {projects.length === 0 && !section ? (
            <QuickStart
              hasDataSource={connections.length + arcgisConnections.length + apiConnections.length + dataSources.length > 0}
              canCreate={canCreateProj}
              onNewProject={openModalForType}
            />
          ) : (
            <>
{showSection('estrai') && <ProjectSection
            id="pl-section-estrai"
            chipClass="pl__chip--estrai"
            chipLabel="↓ Estrai"
            subtitle="Estrazione da sorgenti SQL e GIS"
            onNew={() => openModalForType('estrai')}
            canCreate={canCreateProj}
            empty={shownEstrai.length === 0}
            emptyIcon="↓"
            emptyTitle={emptyFor('Estrai').title}
            emptyText={emptyFor('Estrai', 'Crea il tuo primo progetto per estrarre dati').text}
          >
            {shownEstrai.map(project => {
              const conn = getConn(project.default_connection_id);
              return (
                <ProjectCard
                  key={project.id}
                  project={project}
                  runStatus={runStatusFor(project.id)}
                  accentClass="pl__card-accent--estrai"
                  modLabel="↓ Estrai"
                  meta={[
                    { key: 'Connessione', val: conn ? conn.name : 'N/A', ok: !!conn },
                    { key: 'Creato',      val: formatDateTime(project.created_at) },
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
          </ProjectSection>}
{showSection('manipola') && <ProjectSection
            id="pl-section-manipola"
            chipClass="pl__chip--manipola"
            chipLabel="⚙ Manipola"
            subtitle="Trasformazione e join spaziale"
            onNew={() => openModalForType('manipola')}
            canCreate={canCreateProj}
            empty={shownManipola.length === 0}
            emptyIcon="⚙"
            emptyTitle={emptyFor('Manipola').title}
            emptyText={emptyFor('Manipola', 'Crea un pipeline di trasformazione').text}
          >
            {shownManipola.map(project => {
              const srcProjects = getSourceProjects(project.id);
              const fileIds = projectFileSources[project.id] || [];
              const fileNames = fileIds.map(id => fileNameById.get(id)).filter(Boolean);
              const srcCount = srcProjects.length + fileNames.length;
              return (
                <ProjectCard
                  key={project.id}
                  project={project}
                  runStatus={runStatusFor(project.id)}
                  accentClass="pl__card-accent--manipola"
                  modLabel="⚙ Manipola"
                  meta={[
                    { key: 'Sorgenti', val: `${srcCount} progett${srcCount !== 1 ? 'i' : 'o'}` },
                    { key: 'Creato',   val: formatDateTime(project.created_at) },
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
          </ProjectSection>}
{showSection('arricchisci') && <ProjectSection
            id="pl-section-arricchisci"
            chipClass="pl__chip--arricchisci"
            chipLabel="◈ Arricchisci"
            subtitle="API enrichment geospaziale"
            onNew={() => openModalForType('arricchisci')}
            canCreate={canCreateProj}
            empty={shownArricchisci.length === 0}
            emptyIcon="◈"
            emptyTitle={emptyFor('Arricchisci').title}
            emptyText={emptyFor('Arricchisci', 'Crea un nuovo pipeline di arricchimento').text}
          >
            {shownArricchisci.map(project => (
              <ProjectCard
                key={project.id}
                project={project}
                runStatus={runStatusFor(project.id)}
                accentClass="pl__card-accent--arricchisci"
                modLabel="◈ Arricchisci"
                meta={[
                  { key: 'API', val: 'Enrichment' },
                  { key: 'Creato', val: formatDateTime(project.created_at) },
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
          </ProjectSection>}
            </>
          )}
          {/* P17: la sezione Eseguibili c'è anche senza progetti. */}
          {!section && (eseguibili.length > 0 || projects.length > 0) && (
          <EseguibiliSection
            items={shownEseguibili}
            filtered={search.active}
            canCreate={can.createEseguibile(user)}
            canDelete={(item) => can.manageEseguibile(user, item)}
            onNew={openEsegSelector}
            onExecute={(item) => setExecuteEseguibileId(item.id)}
            onDelete={(item) => handleDeleteEseguibile(item.id, item.name)}
          />
          )}

        </div>{/* /content */}
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
            // Il toast lo mostra PublishEseguibileModal (P18: niente doppione).
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
    </PageLayout>
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
