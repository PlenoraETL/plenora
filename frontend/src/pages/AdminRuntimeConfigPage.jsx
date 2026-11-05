/**
 * AdminRuntimeConfigPage.jsx - configurazione runtime read-only.
 * Route: /admin/runtime-config
 */
import React, { useMemo, useState } from 'react';
import PageLayout from '../components/common/PageLayout';
import { getRuntimeConfig } from '../api/adminRuntimeConfig';
import { useResource } from '../hooks/useResource';
import ListState from '../components/common/ListState';
import '../styles/AdminRuntimeConfigPage.css';
import { formatDateTime } from '../utils/format';
import SearchField, { SearchEmpty } from '../components/common/SearchField';
import { useSearch } from '../hooks/useSearch';


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

// Voci della sezione nella vista (avanzate o essenziali).
const baseItemsOf = (section, showAdvanced) => (showAdvanced
  ? (section.items || [])
  : (section.items || []).filter((item) => item.essential === true));
// Ricerca: chiave, valore o sorgente (hooks/useSearch).
const searchValuesOf = (item) => [item.key, formatValue(item.value), item.source];

function Section({ section, items }) {
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
  // Fase 5 T2 (R4.8): hook comune, errore come errore con «Riprova».
  const resource = useResource(getRuntimeConfig, { key: 'runtime-config' });
  const data = resource.data || null;
  const loading = resource.loading;
  const load = () => resource.reload();
  const search = useSearch();
  const [showAdvanced, setShowAdvanced] = useState(false);
  const sections = useMemo(() => (data?.sections || []).map((section) => {
    const base = baseItemsOf(section, showAdvanced);
    return { section, base, items: search.filter(base, searchValuesOf) };
  }), [data, showAdvanced, search.filter]);
  const searchTotal = sections.reduce((n, s) => n + s.base.length, 0);
  const searchShown = sections.reduce((n, s) => n + s.items.length, 0);
  const topology = data?.runtime?._dev_topology;


  return (
    <PageLayout
      className="arc"
      title="Configurazione Runtime"
      subtitle={<>Solo lettura · segreti redatti · API {data?.api_version || '-'}</>}
      actions={(
        <button className="arc__btn" type="button" onClick={load} disabled={loading}>
          {loading ? 'Aggiornamento...' : 'Aggiorna'}
        </button>
      )}
    >

        <section className="arc__overview">
          <div className="arc__card">
            <div className="arc__card-label">Modalita'</div>
            <div className="arc__card-value">{data?.mode || 'read_only'}</div>
          </div>
          <div className="arc__card">
            <div className="arc__card-label">Snapshot</div>
            <div className="arc__card-value">{formatDateTime(data?.generated_at, { seconds: true })}</div>
          </div>
          <div className="arc__card">
            <div className="arc__card-label">Backend</div>
            <div className="arc__card-value">{topology?.hostname || 'protetto'}</div>
          </div>
          <div className="arc__card">
            <div className="arc__card-label">Task attivi</div>
            <div className="arc__card-value">
              {data?.workers?.available ? data.workers.running_tasks : 'N/D'}
            </div>
          </div>
        </section>

        {data?.health && (
          <section className="arc__health">
            {data.health.map((item) => <HealthPill key={item.name} item={item} />)}
          </section>
        )}

        <section className="arc__runtime">
          {topology && <>
            <div>
              <b>Docker CLI:</b> {topology.docker_cli_available ? 'disponibile' : 'non disponibile'}
            </div>
            <div>
              <b>Docker socket:</b> {topology.docker_socket_mounted ? 'montato' : 'non montato'}
            </div>
            <div>
              <b>Container:</b> {topology.container_name || '-'}
            </div>
          </>}
          <div className="arc__runtime-note">{data?.runtime?.note}</div>
        </section>

        {/* Plenora 2.0: il BE deriva lo stato worker da PostgreSQL
            ({available, running_tasks}) — niente piu' lista di nodi
            (era l'inspect Celery). */}
        {data?.workers?.available && (
          <section className="arc__section">
            <h2 className="arc__section-title">Worker</h2>
            <div className="arc__workers">
              <div className="arc__worker">
                <div className="arc__worker-meta">
                  task in esecuzione: {data.workers.running_tasks}
                </div>
              </div>
            </div>
          </section>
        )}

        <div className="arc__toolbar">
          <SearchField
            search={search}
            label="Cerca chiave, valore o sorgente"
            className="arc__search"
            shown={searchShown}
            total={searchTotal}
          />
          <button
            className={showAdvanced ? 'arc__toggle arc__toggle--active' : 'arc__toggle'}
            type="button"
            onClick={() => setShowAdvanced((value) => !value)}
          >
            {showAdvanced ? 'Nascondi avanzate' : 'Mostra avanzate'}
          </button>
        </div>

        <ListState
          resource={resource}
          loading={<div className="arc__loading">Caricamento...</div>}
          errorTitle="Configurazione non caricata"
          empty="Nessuna configurazione."
        >
          <SearchEmpty search={search} shown={searchShown} />
          {sections.map(({ section, items }) => (
            <Section
              key={section.name}
              section={section}
              items={items}
            />
          ))}
        </ListState>
    </PageLayout>
  );
}
