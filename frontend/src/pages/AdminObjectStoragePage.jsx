import React, { useCallback, useEffect, useState } from 'react';
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
  const [scope, setScope] = useState('canonical');
  const [groupId, setGroupId] = useState('');
  const [domain, setDomain] = useState('results');
  const [page, setPage] = useState(null);
  const [projectCatalog, setProjectCatalog] = useState(null);
  const [cursor, setCursor] = useState(null);
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [downloading, setDownloading] = useState('');
  const [deleting, setDeleting] = useState('');
  const [purgingPreviews, setPurgingPreviews] = useState(false);
  const [cleaningOrphans, setCleaningOrphans] = useState(false);
  const confirmModal = useConfirmModal();
  const groupedResults = scope === 'canonical' && domain === 'results';
  const orphanProjects = (projectCatalog?.projects || []).filter(
    (project) => project.project_name === 'Progetto eliminato',
  );

  const load = useCallback(async (requestedCursor = null) => {
    setLoading(true);
    setError('');
    try {
      const objectRequest = listObjectStorage({
        scope,
        group_id: scope === 'canonical' && groupId.trim() ? groupId.trim() : undefined,
        domain: domain || undefined,
        limit: 100,
        cursor: requestedCursor || undefined,
      });
      if (scope === 'canonical' && domain === 'results') {
        const [data, catalog] = await Promise.all([
          objectRequest,
          listObjectStorageProjects({ group_id: groupId.trim() || undefined }),
        ]);
        setPage(data);
        setProjectCatalog(catalog);
      } else {
        setPage(await objectRequest);
        setProjectCatalog(null);
      }
      setCursor(requestedCursor);
    } catch (err) {
      setError(humanizeErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [scope, groupId, domain]);

  useEffect(() => {
    setHistory([]);
    load(null);
  }, [load]);

  const next = () => {
    if (!page?.next_cursor) return;
    setHistory((items) => [...items, cursor]);
    load(page.next_cursor);
  };

  const previous = () => {
    if (!history.length) return;
    const items = [...history];
    const previousCursor = items.pop() || null;
    setHistory(items);
    load(previousCursor);
  };

  const download = async (key) => {
    setDownloading(key);
    setError('');
    try {
      await downloadStorageObject(key);
    } catch (err) {
      setError(humanizeErrorMessage(err));
    } finally {
      setDownloading('');
    }
  };

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
    setDeleting(item.key);
    setError('');
    try {
      await deleteStorageObject(item.key);
      await load(cursor);
    } catch (err) {
      setError(humanizeErrorMessage(err));
    } finally {
      setDeleting('');
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
    setError('');
    setNotice('');
    try {
      const result = await deleteAllStoragePreviews({
        scope,
        group_id: scopedGroup || undefined,
      });
      setNotice(`Preview eliminate: ${result.deleted_count}`);
      await load(null);
    } catch (err) {
      setError(humanizeErrorMessage(err));
    } finally {
      setPurgingPreviews(false);
    }
  };

  const cleanupOrphans = async () => {
    const orphanResults = orphanProjects.reduce(
      (count, project) => count + project.results.length, 0,
    );
    const confirmed = await confirmModal.confirm({
      title: 'Bonificare i risultati orfani?',
      message: (
        `Saranno rimossi ${orphanResults} risultati appartenenti a `
        + `${orphanProjects.length} progetti non piu esistenti, inclusi i relativi file. `
        + 'I progetti attivi non verranno modificati.'
      ),
      confirmText: 'Bonifica orfani',
      cancelText: 'Annulla',
      variant: 'danger',
    });
    if (!confirmed) return;
    setCleaningOrphans(true);
    setError('');
    setNotice('');
    try {
      const report = await cleanupOrphanStorage();
      const deleted = report?.orphan_results?.rows_deleted || 0;
      const errors = report?.errors?.length || 0;
      setNotice(
        errors
          ? `Bonifica parziale: ${deleted} risultati eliminati, ${errors} errori`
          : `Bonifica completata: ${deleted} risultati orfani eliminati`,
      );
      await load(null);
    } catch (err) {
      setError(humanizeErrorMessage(err));
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
            <button type="button" className="storage-admin__purge" onClick={purgePreviews} disabled={loading || purgingPreviews}>
              {purgingPreviews ? 'Eliminazione preview...' : 'Elimina tutte le preview'}
            </button>
            <button type="button" onClick={() => load(cursor)} disabled={loading}>
              {loading ? 'Caricamento…' : 'Aggiorna'}
            </button>
          </div>
        </header>

        <section className="storage-admin__filters">
          <label>Namespace<select value={scope} onChange={(event) => setScope(event.target.value)}><option value="canonical">Tenant canonici</option><option value="legacy">Chiavi legacy</option></select></label>
          <label>Gruppo<input value={groupId} onChange={(event) => setGroupId(event.target.value)} placeholder="group_id" disabled={scope === 'legacy'} /></label>
          <label>Tipo<select value={domain} onChange={(event) => setDomain(event.target.value)}><option value="">Tutti</option>{DOMAINS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        </section>

        {error && <div className="storage-admin__error">{error}</div>}
        {notice && <div className="storage-admin__notice">{notice}</div>}

        {groupedResults && orphanProjects.length > 0 && (
          <div className="storage-admin__orphan-warning">
            <div>
              <strong>Rilevati risultati orfani</strong>
              <span>
                {orphanProjects.length} progetti eliminati conservano ancora risultati nello storage.
              </span>
            </div>
            <button type="button" onClick={cleanupOrphans} disabled={loading || cleaningOrphans}>
              {cleaningOrphans ? 'Bonifica in corso...' : 'Bonifica orfani'}
            </button>
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
            {(projectCatalog?.projects || []).map((project, index) => (
              <details className="storage-admin__project" key={`${project.group_id}:${project.project_id}`} open={index === 0}>
                <summary>
                  <div><strong>{project.project_name}</strong><span>{project.project_type?.toUpperCase() || 'PROGETTO ELIMINATO'} - {project.project_id}</span></div>
                  <div><span>{project.group_id}</span><b>{project.results.length} risultati</b></div>
                </summary>
                <div className="storage-admin__result-list">
                  {project.results.map((result) => (
                    <div className="storage-admin__result" key={result.id}>
                      <div><strong>{result.filename}</strong><span>{result.format.toUpperCase()} · {result.rows_count ?? '—'} righe · {formatBytes(result.size_bytes)}</span><small>{new Date(result.created_at).toLocaleString('it-IT')}</small></div>
                      <button type="button" className="storage-admin__download" onClick={() => download(result.key)} disabled={downloading === result.key}>{downloading === result.key ? '…' : 'Scarica'}</button>
                    </div>
                  ))}
                </div>
              </details>
            ))}
            {!loading && !(projectCatalog?.projects || []).length && <div className="storage-admin__empty">Nessun risultato conservato</div>}
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
                      <button type="button" className="storage-admin__download" onClick={() => download(item.key)} disabled={downloading === item.key || deleting === item.key}>{downloading === item.key ? '…' : 'Scarica'}</button>
                      <button type="button" className="storage-admin__delete" onClick={() => remove(item)} disabled={deleting === item.key || downloading === item.key}>{deleting === item.key ? 'Eliminazione…' : 'Elimina'}</button>
                    </td>
                  </tr>
                ))}
                {!loading && !(page?.objects || []).length && <tr><td colSpan="7" className="storage-admin__empty">Nessun oggetto</td></tr>}
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
