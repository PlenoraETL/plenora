/**
 * AdminRuntimeConfigPage.jsx - configurazione runtime read-only.
 * Route: /admin/runtime-config
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import AppSidebar from '../components/AppSidebar';
import { getRuntimeConfig } from '../api/adminRuntimeConfig';
import { useToast } from '../hooks/useToast';
import { toastApiError } from '../utils/toastApiError';
import '../styles/AdminRuntimeConfigPage.css';

const ESSENTIAL_KEYS = new Set([
  'ENVIRONMENT',
  'DB_SCHEMA',
  'DATABASE_URL',
  'CELERY_BROKER_URL',
  'CELERY_RESULT_BACKEND',
  'STORAGE_POLICY',
  'OBJECT_STORAGE_ENABLED',
  'OBJECT_STORAGE_BACKEND',
  'CLOUD_PROVIDER',
  'OBJECT_STORAGE_BUCKET',
  'BACKUP_BUCKET',
  'MAX_FILE_SIZE',
  'LOAD_FILE_MAX_BYTES',
  'WORKER_RSS_MAX_BYTES',
  'RESOURCE_MAX_BYTES',
  'ARTIFACT_PREVIEW_MAX_BYTES',
  'JWT_EXPIRE_HOURS',
  'LOGIN_MAX_ATTEMPTS',
]);

function fmtDate(iso) {
  if (!iso) return '-';
  try {
    return new Date(iso).toLocaleString('it-IT', {
      day: '2-digit', month: '2-digit', year: 'numeric',
      hour: '2-digit', minute: '2-digit', second: '2-digit',
    });
  } catch {
    return iso;
  }
}

function formatValue(value) {
  if (value == null || value === '') return '-';
  if (Array.isArray(value)) return value.join(', ');
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function HealthPill({ item }) {
  return (
    <div className={`arc__pill ${item.ok ? 'arc__pill--ok' : 'arc__pill--warn'}`}>
      <span className="arc__pill-dot" />
      <span className="arc__pill-name">{item.name}</span>
      <span className="arc__pill-detail">{item.detail}</span>
    </div>
  );
}

function ConfigRow({ item }) {
  return (
    <tr>
      <td className="arc__key">{item.key}</td>
      <td>
        <code className={item.secret ? 'arc__value arc__value--secret' : 'arc__value'}>
          {formatValue(item.value)}
        </code>
      </td>
      <td>
        <span className={`arc__source arc__source--${item.source}`}>
          {item.source}
        </span>
      </td>
      <td>{item.requires_restart?.length ? item.requires_restart.join(', ') : '-'}</td>
      <td>{item.editable_later ? 'allowlist futura' : '-'}</td>
    </tr>
  );
}

function Section({ section, query, showAdvanced }) {
  const items = useMemo(() => {
    const baseItems = showAdvanced
      ? (section.items || [])
      : (section.items || []).filter((item) => ESSENTIAL_KEYS.has(item.key));
    if (!query.trim()) return baseItems;
    const q = query.trim().toLowerCase();
    return baseItems.filter((item) => {
      return item.key.toLowerCase().includes(q)
        || formatValue(item.value).toLowerCase().includes(q)
        || String(item.source || '').toLowerCase().includes(q);
    });
  }, [section.items, query, showAdvanced]);

  if (items.length === 0) return null;

  return (
    <section className="arc__section">
      <h2 className="arc__section-title">{section.name}</h2>
      <div className="arc__table-wrap">
        <table className="arc__table">
          <thead>
            <tr>
              <th>Chiave</th>
              <th>Valore effettivo</th>
              <th>Sorgente</th>
              <th>Restart</th>
              <th>Editing</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => <ConfigRow key={item.key} item={item} />)}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export default function AdminRuntimeConfigPage() {
  const toast = useToast();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');
  const [showAdvanced, setShowAdvanced] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await getRuntimeConfig());
    } catch (e) {
      toastApiError(toast, e, 'Errore caricamento configurazione');
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="arc">
      <AppSidebar />
      <main className="arc__main">
        <header className="arc__header">
          <div>
            <h1 className="arc__title">Configurazione Runtime</h1>
            <div className="arc__subtitle">
              Solo lettura · segreti redatti · API {data?.api_version || '-'}
            </div>
          </div>
          <button className="arc__btn" type="button" onClick={load} disabled={loading}>
            {loading ? 'Aggiornamento...' : 'Aggiorna'}
          </button>
        </header>

        <section className="arc__overview">
          <div className="arc__card">
            <div className="arc__card-label">Modalita'</div>
            <div className="arc__card-value">{data?.mode || 'read_only'}</div>
          </div>
          <div className="arc__card">
            <div className="arc__card-label">Snapshot</div>
            <div className="arc__card-value">{fmtDate(data?.generated_at)}</div>
          </div>
          <div className="arc__card">
            <div className="arc__card-label">Backend</div>
            <div className="arc__card-value">{data?.runtime?.hostname || '-'}</div>
          </div>
          <div className="arc__card">
            <div className="arc__card-label">Worker online</div>
            <div className="arc__card-value">
              {data?.workers?.available ? data.workers.workers_online : 'N/D'}
            </div>
          </div>
        </section>

        {data?.health && (
          <section className="arc__health">
            {data.health.map((item) => <HealthPill key={item.name} item={item} />)}
          </section>
        )}

        <section className="arc__runtime">
          <div>
            <b>Docker CLI:</b> {data?.runtime?.docker_cli_available ? 'disponibile' : 'non disponibile'}
          </div>
          <div>
            <b>Docker socket:</b> {data?.runtime?.docker_socket_mounted ? 'montato' : 'non montato'}
          </div>
          <div>
            <b>Container:</b> {data?.runtime?.container_name || '-'}
          </div>
          <div className="arc__runtime-note">{data?.runtime?.note}</div>
        </section>

        {data?.workers?.workers?.length > 0 && (
          <section className="arc__section">
            <h2 className="arc__section-title">Worker Celery</h2>
            <div className="arc__workers">
              {data.workers.workers.map((worker) => (
                <div className="arc__worker" key={worker.name}>
                  <div className="arc__worker-name">{worker.name}</div>
                  <div className="arc__worker-meta">task attivi: {worker.active_tasks}</div>
                  <div className="arc__worker-meta">code: {worker.queues?.join(', ') || '-'}</div>
                </div>
              ))}
            </div>
          </section>
        )}

        <div className="arc__toolbar">
          <input
            className="arc__search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filtra chiave, valore o sorgente..."
          />
          <button
            className={showAdvanced ? 'arc__toggle arc__toggle--active' : 'arc__toggle'}
            type="button"
            onClick={() => setShowAdvanced((value) => !value)}
          >
            {showAdvanced ? 'Nascondi avanzate' : 'Mostra avanzate'}
          </button>
        </div>

        {loading && !data ? (
          <div className="arc__loading">Caricamento...</div>
        ) : (
          (data?.sections || []).map((section) => (
            <Section
              key={section.name}
              section={section}
              query={query}
              showAdvanced={showAdvanced}
            />
          ))
        )}
      </main>
    </div>
  );
}
