/**
 * RunDetail.jsx — Dettaglio esecuzione: tab Steps + Log tecnici
 * Props:
 *   runId {string} - ID della run da visualizzare
 */

import React, { useState } from 'react';
import { getLogRun } from '../../api/logRuns';
import { useResource } from '../../hooks/useResource';
import { ResourceError } from '../common/ListState';
import { formatDuration, formatNumber } from '../../utils/format';
import { formatLogTime } from '../../utils/logViewerHelpers.jsx';

const LEVEL_CONFIG = {
  INFO:    { cls: 'lv__badge--info',  label: 'INFO' },
  WARNING: { cls: 'lv__badge--warn',  label: 'WARN' },
  ERROR:   { cls: 'lv__badge--error', label: 'ERROR' },
};

const MODULE_COLORS = {
  estrai: '#34C17A', manipola: '#9B6DFF', arricchisci: '#F5A623', carica: '#4B8BFF',
  orchestrator: '#60A5FA', scheduler: '#34D399', connections: '#FBBF24', projects: '#818CF8',
  files: '#A78BFA', services: '#94A3B8', db: '#F87171', utils: '#6B7280', workers: '#F472B6',
};

// Fase 5 T2 (R4.13): dettaglio dall'hook comune; con Live (`live`) si
// aggiorna finche' la run e' in corso; un errore ha «Riprova».
export default function RunDetail({ runId, live = false }) {
  const resource = useResource(() => getLogRun(runId, true, 300), { key: runId, refreshInterval: live ? 5000 : 0 });
  const detail = resource.data || null;
  const [tab, setTab]         = useState('steps');

  if (resource.initialLoading) return <div className="lv__run-detail-loading">Caricamento...</div>;
  if (!detail) {
    return (
      <div className="lv__run-detail-loading">
        {resource.status === 'error'
          ? <ResourceError compact resource={resource} title="Dettaglio non caricato" />
          : <ResourceError compact title="Dettaglio non caricato" message="Dati non disponibili" onRetry={resource.reload} />}
      </div>
    );
  }

  const steps = Array.isArray(detail.steps) ? detail.steps : [];
  const logs  = Array.isArray(detail.logs)  ? detail.logs  : [];

  return (
    <div className="lv__run-detail">
      {/* Live: un aggiornamento non riuscito si dice sopra i dati di prima. */}
      <ResourceError compact resource={resource} title="Dettaglio non caricato" />
      <div className="lv__run-detail-tabs">
        <button
          className={`lv__run-dtab${tab === 'steps' ? ' lv__run-dtab--on' : ''}`}
          onClick={() => setTab('steps')}>
          Passi {steps.length > 0 && <span className="lv__run-dtab-badge">{steps.length}</span>}
        </button>
        <button
          className={`lv__run-dtab${tab === 'logs' ? ' lv__run-dtab--on' : ''}`}
          onClick={() => setTab('logs')}>
          Log tecnici {logs.length > 0 && <span className="lv__run-dtab-badge">{logs.length}{detail.logs_truncated ? '+' : ''}</span>}
        </button>
      </div>

      {tab === 'steps' && (
        <div className="lv__run-steps">
          {steps.length === 0 ? (
            <div className="lv__run-steps-empty">Nessun passo registrato</div>
          ) : steps.map((s, i) => {
            const isOk  = s.status === 'success';
            const color = MODULE_COLORS[s.module] || '#6B7280';
            return (
              <div key={i} className={`lv__run-step${!isOk ? ' lv__run-step--err' : ''}`}>
                <div className={`lv__run-step-icon ${isOk ? 'lv__run-step-icon--ok' : 'lv__run-step-icon--err'}`}>
                  {isOk ? '✓' : '✕'}
                </div>
                <div className="lv__run-step-module" style={{ color, background: `${color}18`, borderColor: `${color}30` }}>
                  {s.module}
                </div>
                <div className="lv__run-step-rows">{formatNumber(s.rows_out)} righe</div>
                <div className="lv__run-step-dur">{formatDuration(s.duration_ms)}</div>
                {s.error && <div className="lv__run-step-err">{s.error}</div>}
              </div>
            );
          })}
        </div>
      )}

      {tab === 'logs' && (
        <div className="lv__run-logs">
          {logs.length === 0 ? (
            <div className="lv__run-steps-empty">Nessun log tecnico per questa esecuzione</div>
          ) : (
            <table className="lv__table lv__run-logs-table">
              <thead>
                <tr>
                  <th className="lv__th lv__th--w-90">Ora</th>
                  <th className="lv__th lv__th--w-70">Livello</th>
                  <th className="lv__th lv__th--w-90">Modulo</th>
                  <th className="lv__th">Messaggio</th>
                </tr>
              </thead>
              <tbody>
                {logs.map((l, i) => {
                  const lvCfg  = LEVEL_CONFIG[l.level] || {};
                  const modClr = MODULE_COLORS[l.module] || '#6B7280';
                  return (
                    <tr key={i} className={`lv__row lv__row--${(l.level || '').toLowerCase()}`}>
                      <td className="lv__td lv__td--time lv__td--time-sm">{formatLogTime(l.ts)}</td>
                      <td className="lv__td">
                        <span className={`lv__badge lv__badge--sm ${lvCfg.cls || ''}`}>{l.level}</span>
                      </td>
                      <td className="lv__td">
                        <span className="lv__module-tag lv__module-tag--sm" style={{
                          color: modClr,
                          background: `${modClr}18`,
                          borderColor: `${modClr}30`,
                        }}>{l.module}</span>
                      </td>
                      <td className="lv__td lv__td--mono-11">{l.msg}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
          {detail.logs_truncated && (
            <div className="lv__run-steps-empty lv__run-steps-empty--sm">
              Mostrati i primi 300 log. Usa i filtri Log Tecnici per vedere tutto.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
