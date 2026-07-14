import React, { useCallback, useEffect, useState } from 'react';
import AppSidebar from '../components/AppSidebar';
import {
  downloadStorageObject,
  listObjectStorage,
} from '../api/adminObjectStorage';
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
  const [domain, setDomain] = useState('');
  const [page, setPage] = useState(null);
  const [cursor, setCursor] = useState(null);
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [downloading, setDownloading] = useState('');

  const load = useCallback(async (requestedCursor = null) => {
    setLoading(true);
    setError('');
    try {
      const data = await listObjectStorage({
        scope,
        group_id: scope === 'canonical' && groupId.trim() ? groupId.trim() : undefined,
        domain: domain || undefined,
        limit: 100,
        cursor: requestedCursor || undefined,
      });
      setPage(data);
      setCursor(requestedCursor);
    } catch (err) {
      setError(err.message || 'Errore caricamento object storage');
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
      setError(err.message || 'Download non riuscito');
    } finally {
      setDownloading('');
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
          <div>
            <h1>Object Storage</h1>
            <p>Explorer read-only del bucket runtime, riservato ai superadmin.</p>
          </div>
          <button type="button" onClick={() => load(cursor)} disabled={loading}>
            {loading ? 'Caricamento…' : 'Aggiorna'}
          </button>
        </header>

        <section className="storage-admin__filters">
          <label>
            Namespace
            <select value={scope} onChange={(event) => setScope(event.target.value)}>
              <option value="canonical">Tenant canonici</option>
              <option value="legacy">Chiavi legacy</option>
            </select>
          </label>
          <label>
            Gruppo
            <input
              value={groupId}
              onChange={(event) => setGroupId(event.target.value)}
              placeholder="group_id"
              disabled={scope === 'legacy'}
            />
          </label>
          <label>
            Tipo
            <select value={domain} onChange={(event) => setDomain(event.target.value)}>
              <option value="">Tutti</option>
              {DOMAINS.map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
          </label>
        </section>

        {error && <div className="storage-admin__error">{error}</div>}

        <div className="storage-admin__summary">
          <span>Bucket: <b>{page?.bucket || '—'}</b></span>
          <span>Oggetti nella pagina: <b>{page?.objects?.length || 0}</b></span>
          <span>Dimensione pagina: <b>{formatBytes(totalBytes)}</b></span>
        </div>

        <div className="storage-admin__table-wrap">
          <table>
            <thead>
              <tr>
                <th>Tenant</th>
                <th>Tipo</th>
                <th>Risorsa</th>
                <th>Chiave</th>
                <th>Dimensione</th>
                <th>Modificato</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {(page?.objects || []).map((item) => (
                <tr key={item.key}>
                  <td>{item.tenant_group_id || <span className="storage-admin__legacy">legacy</span>}</td>
                  <td>{item.domain}</td>
                  <td>{item.resource_id || '—'}</td>
                  <td className="storage-admin__key" title={item.key}>{item.key}</td>
                  <td>{formatBytes(item.size_bytes)}</td>
                  <td>{new Date(item.last_modified).toLocaleString('it-IT')}</td>
                  <td>
                    <button
                      type="button"
                      className="storage-admin__download"
                      onClick={() => download(item.key)}
                      disabled={downloading === item.key}
                    >
                      {downloading === item.key ? '…' : 'Scarica'}
                    </button>
                  </td>
                </tr>
              ))}
              {!loading && !(page?.objects || []).length && (
                <tr><td colSpan="7" className="storage-admin__empty">Nessun oggetto</td></tr>
              )}
            </tbody>
          </table>
        </div>

        <footer className="storage-admin__pager">
          <button type="button" onClick={previous} disabled={!history.length || loading}>Indietro</button>
          <button type="button" onClick={next} disabled={!page?.next_cursor || loading}>Avanti</button>
        </footer>
      </main>
    </div>
  );
}
