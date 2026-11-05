import React, { useState } from 'react';
import AppSidebar from '../components/AppSidebar';
import {
  cleanupOrphanStorage,
  deleteAllStoragePreviews,
  deleteStorageObject,
  downloadStorageObject,
  listObjectStorage,
  listObjectStorageProjects,
} from '../api/adminObjectStorage';
import ConfirmDialog from '../components/common/ConfirmDialog';
import { useConfirmModal } from '../hooks/useConfirmModal';
import { humanizeErrorMessage } from '../utils/errorMessages';
import { useAuth } from '../context/AuthContext';
import { can } from '../utils/permissions';
import { useResource } from '../hooks/useResource';
import { useBusy } from '../hooks/useBusy';
import { useDownload } from '../hooks/useDownload';
import '../styles/AdminObjectStoragePage.css';

const DOMAINS = [
  ['results', 'Risultati'],
  ['file-sources', 'File caricati'],
  ['previews', 'Preview'],
  ['intermediate', 'Intermedi'],
  ['eseguibili-outputs', 'Output eseguibili'],
  ['imdf-bundles', 'Bundle IMDF'],
];

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
}

export default function AdminObjectStoragePage() {
  // Fase 5 T2 (R4.5, R9.10): pagina dall'hook comune con filtri e cursore
  // nella chiave (vince l'ultima richiesta), errore di caricamento distinto
  // da quello di un'azione, download ed eliminazioni per oggetto.
  const [scope, setScopeRaw] = useState('canonical');
  const [groupId, setGroupIdRaw] = useState('');
  const [domain, setDomainRaw] = useState('results');
  const [cursor, setCursor] = useState(null);
  const [history, setHistory] = useState([]);
  const [actionError, setActionError] = useState('');
  const [notice, setNotice] = useState('');
  const [purgingPreviews, setPurgingPreviews] = useState(false);
  const [cleaningOrphans, setCleaningOrphans] = useState(false);
  const confirmModal = useConfirmModal();
  const { user } = useAuth();
  const canManage = can.manageObjectStorage(user);
  const groupedResults = scope === 'canonical' && domain === 'results';
  // Un filtro nuovo riparte dalla prima pagina.
  const resetPaging = () => { setCursor(null); setHistory([]); };
  const setScope = (v) => { resetPaging(); setScopeRaw(v); };
  const setGroupId = (v) => { resetPaging(); setGroupIdRaw(v); };
  const setDomain = (v) => { resetPaging(); setDomainRaw(v); };

  const resource = useResource(async () => {
    const group = groupId.trim();
    const objectRequest = listObjectStorage({
      scope,
      group_id: scope === 'canonical' && group ? group : undefined,
      domain: domain || undefined,
      limit: 100,
      cursor: cursor || undefined,
    });
    if (scope === 'canonical' && domain === 'results') {
      const [data, catalog] = await Promise.all([
        objectRequest,
        listObjectStorageProjects({ group_id: group || undefined }),
      ]);
      return { page: data, catalog, catalogGroup: group };
    }
    return { page: await objectRequest, catalog: null, catalogGroup: '' };
  }, { key: { scope, groupId, domain, cursor }, keepPreviousData: true, errorMessage: humanizeErrorMessage });
  const page = resource.data?.page || null;
  const projectCatalog = resource.data?.catalog || null;
  // Gruppo del catalogo caricato: e' il perimetro mostrato e chiesto.
  const catalogGroup = resource.data?.catalogGroup || '';
  const loading = resource.loading;
  const load = () => resource.reload();
  const error = resource.error || actionError;
  // Orfano = il server dice che il progetto non esiste piu' (per id, non per
  // il nome mostrato). Il catalogo e' gia' quello del filtro di gruppo.
  const orphanProjects = (projectCatalog?.projects || []).filter((project) => project.orphan === true);
  const busy = useBusy();
  const downloads = useDownload({ onError: (message) => setActionError(message) });

  const next = () => {
    if (!page?.next_cursor) return;
    setHistory((items) => [...items, cursor]);
    setCursor(page.next_cursor);
  };

  const previous = () => {
    if (!history.length) return;
    const items = [...history];
    const previousCursor = items.pop() || null;
    setHistory(items);
    setCursor(previousCursor);
  };

  const download = (key) => {
    setActionError('');
    return downloads.run(key, () => downloadStorageObject(key));
  };
  const downloading = (key) => downloads.isBusy(key);
  const deleting = (key) => busy.isBusy(key);

  const remove = async (item) => {
    const confirmed = await confirmModal.confirm({
      title: 'Eliminare definitivamente l\'oggetto?',
      message: (
        `Verrà cancellato “${item.key}”. L'operazione non è reversibile `
        + 'e gli eventuali riferimenti applicativi potrebbero non essere più utilizzabili.'
      ),
      confirmText: 'Elimina definitivamente',
      cancelText: 'Annulla',
      variant: 'danger',
    });
    if (!confirmed) return;
    setActionError('');
    try {
      const r = await busy.run(item.key, () => deleteStorageObject(item.key));
      if (!r.skipped) await load();
    } catch (err) {
      setActionError(humanizeErrorMessage(err));
    }
  };

  const purgePreviews = async () => {
    const scopedGroup = scope === 'canonical' ? groupId.trim() : '';
    const target = scopedGroup
      ? `del gruppo ${scopedGroup}`
      : scope === 'legacy' ? 'legacy' : 'di tutti i tenant';
    const confirmed = await confirmModal.confirm({
      title: 'Eliminare tutte le preview?',
      message: `Saranno eliminate definitivamente tutte le preview ${target}. I risultati finali non verranno toccati.`,
      confirmText: 'Elimina tutte le preview',
      cancelText: 'Annulla',
      variant: 'danger',
    });
    if (!confirmed) return;
    setPurgingPreviews(true);
    setActionError('');
    setNotice('');
    try {
      const result = await deleteAllStoragePreviews({
        scope,
        group_id: scopedGroup || undefined,
      });
      setNotice(`Preview eliminate: ${result.deleted_count}`);
      resetPaging();
      await load();
    } catch (err) {
      setActionError(humanizeErrorMessage(err));
    } finally {
      setPurgingPreviews(false);
    }
  };

  const cleanupOrphans = async () => {
    // Perimetro = quello mostrato: gli orfani del catalogo caricato (filtro di
    // gruppo compreso). La richiesta porta esattamente questi project_id.
    const scopeIds = orphanProjects.map((project) => project.project_id);
    if (!scopeIds.length) return;
    // Il catalogo porta TUTTI gli orfani che la bonifica toccherebbe (anche
    // quelli con sole esecuzioni intermedie, senza risultati): stessa fonte
    // del server (results.orphan_cleanup.find_orphan_runs).
    const orphanResults = orphanProjects.reduce(
      (count, project) => count + project.results.length, 0,
    );
    const orphanRuns = orphanProjects.reduce(
      (count, project) => count + Number(project.intermediate_runs || 0), 0,
    );
    const perimeter = catalogGroup ? `del gruppo ${catalogGroup}` : 'di tutti i gruppi';
    const confirmed = await confirmModal.confirm({
      title: 'Bonificare gli orfani?',
      message: (
        `Saranno rimossi ${orphanResults} risultati e ${orphanRuns} esecuzioni intermedie ${perimeter} `
        + `appartenenti a ${orphanProjects.length} progetti non più esistenti, inclusi i relativi file. `
        + 'Nessun altro progetto viene toccato.'
      ),
      confirmText: 'Bonifica orfani',
      cancelText: 'Annulla',
      variant: 'danger',
    });
    if (!confirmed) return;
    setCleaningOrphans(true);
    setActionError('');
    setNotice('');
    try {
      const report = await cleanupOrphanStorage(scopeIds);
      const deleted = report?.orphan_results?.rows_deleted || 0;
      const runsDeleted = report?.orphan_runs?.runs_deleted || 0;
      const errors = report?.errors?.length || 0;
      setNotice(
        errors
          ? `Bonifica parziale: ${deleted} risultati e ${runsDeleted} esecuzioni intermedie eliminati, ${errors} errori`
          : `Bonifica completata: ${deleted} risultati e ${runsDeleted} esecuzioni intermedie orfani eliminati`,
      );
      resetPaging();
      await load();
    } catch (err) {
      setActionError(humanizeErrorMessage(err));
    } finally {
      setCleaningOrphans(false);
    }
  };

  const totalBytes = (page?.objects || []).reduce(
    (sum, item) => sum + Number(item.size_bytes || 0), 0,
  );

  return (
    <div className="storage-admin">
      <AppSidebar />
      <main className="storage-admin__main">
        <header className="storage-admin__header">
          <div><h1>Object Storage</h1><p>Explorer del bucket runtime, riservato ai superadmin.</p></div>
          <div className="storage-admin__header-actions">
            {canManage && (
              <button type="button" className="storage-admin__purge" onClick={purgePreviews} disabled={loading || purgingPreviews}>
                {purgingPreviews ? 'Eliminazione preview...' : 'Elimina tutte le preview'}
              </button>
            )}
            <button type="button" onClick={() => load()} disabled={loading}>
              {loading ? 'Caricamento…' : 'Aggiorna'}
            </button>
          </div>
        </header>

        <section className="storage-admin__filters">
          <label>Namespace<select value={scope} onChange={(event) => setScope(event.target.value)}><option value="canonical">Tenant canonici</option><option value="legacy">Chiavi legacy</option></select></label>
          <label>Gruppo<input value={groupId} onChange={(event) => setGroupId(event.target.value)} placeholder="group_id" disabled={scope === 'legacy'} /></label>
          <label>Tipo<select value={domain} onChange={(event) => setDomain(event.target.value)}><option value="">Tutti</option>{DOMAINS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        </section>

        {error && <div className="storage-admin__error" role="alert">{error}</div>}
        {notice && <div className="storage-admin__notice">{notice}</div>}

        {groupedResults && orphanProjects.length > 0 && (
          <div className="storage-admin__orphan-warning">
            <div>
              <strong>Rilevati dati orfani</strong>
              <span>
                {orphanProjects.length} progetti eliminati conservano ancora risultati o esecuzioni intermedie.
              </span>
            </div>
            {canManage && (
              <button type="button" onClick={cleanupOrphans} disabled={loading || cleaningOrphans}>
                {cleaningOrphans ? 'Bonifica in corso...' : 'Bonifica orfani'}
              </button>
            )}
          </div>
        )}

        <div className="storage-admin__summary">
          <span>Bucket: <b>{page?.bucket || '—'}</b></span>
          <span>Oggetti nella pagina: <b>{page?.objects?.length || 0}</b></span>
          <span>Dimensione pagina: <b>{formatBytes(totalBytes)}</b></span>
        </div>

        {groupedResults ? (
          <section className="storage-admin__projects">
            <div className="storage-admin__project-summary">
              <span>Progetti: <b>{projectCatalog?.projects?.length || 0}</b></span>
              <span>Risultati conservati: <b>{projectCatalog?.results_count || 0}</b></span>
              <span>Dimensione risultati: <b>{formatBytes(projectCatalog?.size_bytes)}</b></span>
            </div>
            {projectCatalog?.intermediate_runs_unavailable && (
              <div className="storage-admin__notice" role="status">
                Le esecuzioni intermedie non si sono potute leggere: l'elenco mostra solo i risultati conservati.
              </div>
            )}
            {(projectCatalog?.projects || []).map((project, index) => (
              <details className="storage-admin__project" key={`${project.group_id}:${project.project_id}`} open={index === 0}>
                <summary>
                  <div><strong>{project.project_name}</strong><span>{project.project_type?.toUpperCase() || 'PROGETTO ELIMINATO'} - {project.project_id}</span></div>
                  <div>
                    <span>{project.group_id}</span>
                    <b>{project.results.length} risultati</b>
                    {project.intermediate_runs > 0 && <b>{project.intermediate_runs} esecuzioni intermedie</b>}
                  </div>
                </summary>
                <div className="storage-admin__result-list">
                  {project.results.map((result) => (
                    <div className="storage-admin__result" key={result.id}>
                      <div><strong>{result.filename}</strong><span>{result.format.toUpperCase()} · {result.rows_count ?? '—'} righe · {formatBytes(result.size_bytes)}</span><small>{new Date(result.created_at).toLocaleString('it-IT')}</small></div>
                      <button type="button" className="storage-admin__download" onClick={() => download(result.key)} disabled={downloading(result.key)}>{downloading(result.key) ? '…' : 'Scarica'}</button>
                    </div>
                  ))}
                </div>
              </details>
            ))}
            {projectCatalog && !(projectCatalog.projects || []).length && <div className="storage-admin__empty">Nessun risultato conservato</div>}
          </section>
        ) : (
          <div className="storage-admin__table-wrap">
            <table>
              <thead><tr><th>Tenant</th><th>Tipo</th><th>Risorsa</th><th>Chiave</th><th>Dimensione</th><th>Modificato</th><th /></tr></thead>
              <tbody>
                {(page?.objects || []).map((item) => (
                  <tr key={item.key}>
                    <td>{item.tenant_group_id || <span className="storage-admin__legacy">legacy</span>}</td><td>{item.domain}</td><td>{item.resource_id || '—'}</td>
                    <td className="storage-admin__key" title={item.key}>{item.key}</td><td>{formatBytes(item.size_bytes)}</td><td>{new Date(item.last_modified).toLocaleString('it-IT')}</td>
                    <td className="storage-admin__actions">
                      <button type="button" className="storage-admin__download" onClick={() => download(item.key)} disabled={downloading(item.key) || deleting(item.key)}>{downloading(item.key) ? '…' : 'Scarica'}</button>
                      {canManage && <button type="button" className="storage-admin__delete" onClick={() => remove(item)} disabled={deleting(item.key) || downloading(item.key)}>{deleting(item.key) ? 'Eliminazione…' : 'Elimina'}</button>}
                    </td>
                  </tr>
                ))}
                {page && !(page.objects || []).length && <tr><td colSpan="7" className="storage-admin__empty">Nessun oggetto</td></tr>}
              </tbody>
            </table>
          </div>
        )}

        {!groupedResults && <footer className="storage-admin__pager"><button type="button" onClick={previous} disabled={!history.length || loading}>Indietro</button><button type="button" onClick={next} disabled={!page?.next_cursor || loading}>Avanti</button></footer>}
        <ConfirmDialog {...confirmModal} />
      </main>
    </div>
  );
}
