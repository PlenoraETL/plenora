/**
 * RunHistoryModal.jsx — Feature inspection e confronto run
 *
 * Aperto dal footer di ogni nodo canvas. Mostra:
 * - Lista run per il canvas corrente (sidebar sinistra)
 * - Dati del nodo per la run selezionata (area centrale)
 * - Confronto L1/L2 tra due run selezionate
 *
 * Struttura:
 *   [sidebar: lista run] | [area principale: dati / confronto]
 */

import React, { useState, useEffect } from 'react';
import { useResource } from '../../hooks/useResource';
import { useParams } from 'react-router-dom';
import ModalWrapper from '../common/ModalWrapper';
import { listRuns, getNodeData, compareRunsL1, compareRunsL2, pinRun } from '../../api/runs';
import './RunHistoryModal.css';
import { term } from '../../utils/glossary';
import { requestErrorMessage, backendMessage } from '../../utils/errorMessages';
import { errorReason } from '../../api/errors';
import { formatDateTime as formatTimestamp, formatNumber as formatRows } from '../../utils/format';
import { pressable } from '../../utils/pressable';
import { READ_ONLY_SAFE } from '../common/readOnlySafe';

/**
 * Mappa un errore di /compare/l2 al messaggio user-facing in base al
 * `detail.reason` ritornato dal backend (intermediate/router.py).
 *
 * Reason noti:
 *   - 'metadata_only_no_rows': almeno una run usa persistence_mode=metadata_only
 *   - 'persistence_mode_mismatch': le due run hanno mode diverso (full_pg vs preview_only)
 *
 * Per qualsiasi altra reason (o errore generico) ritorna un fallback.
 * Esportato per test isolato (PR-A UX Tier 1).
 */
export function mapCompareErrorReason(reason, beMessage) {
  const safeBe = typeof beMessage === 'string' && beMessage ? beMessage : null;
  if (reason === 'metadata_only_no_rows') {
    return safeBe
      || "Confronto riga per riga non disponibile: almeno una delle due esecuzioni conserva solo i metadati. Per rilevare le modifiche usa il confronto per impronta.";
  }
  if (reason === 'persistence_mode_mismatch') {
    return safeBe
      || "Confronto non disponibile: le due esecuzioni conservano i risultati in modi diversi. Usa due esecuzioni con la stessa modalità oppure il confronto per impronta.";
  }
  return safeBe || 'Errore durante il confronto';
}


const formatDuration = (secs) => {
  if (secs == null) return '—';
  if (secs < 60) return `${secs.toFixed(1)}s`;
  return `${Math.floor(secs / 60)}m ${Math.round(secs % 60)}s`;
};


const StatusBadge = ({ status }) => {
  const map = {
    completed: { label: 'OK', cls: 'rh-badge--ok' },
    failed:    { label: 'ERRORE', cls: 'rh-badge--fail' },
    running:   { label: '...', cls: 'rh-badge--running' },
  };
  const { label, cls } = map[status] || { label: status, cls: '' };
  return <span className={`rh-badge ${cls}`}>{label}</span>;
};

export default function RunHistoryModal({ isOpen, onClose, canvasId, nodeId, nodeLabel }) {
  const params = useParams();
  const effectiveCanvasId = canvasId || params.projectId || params.id || params.canvasId;
  // Fase 5 T2 (R4.5): esecuzioni, dati del passo e confronto dall'hook
  // comune: la risposta per una run (o una coppia) superata non scrive mai
  // sulla selezione attuale; ogni apertura rilegge l'elenco.
  const [selectedA, setSelectedA] = useState(null);
  const [selectedB, setSelectedB] = useState(null);
  const [compareMode, setCompareMode] = useState(false);
  // Tab area principale
  const [activeTab, setActiveTab] = useState('data'); // 'data' | 'compare'
  const [openCount, setOpenCount] = useState(0);
  useEffect(() => {
    if (!isOpen) return;
    setOpenCount(n => n + 1);
    setSelectedA(null);
    setSelectedB(null);
    setCompareMode(false);
  }, [isOpen, canvasId]);

  const runsResource = useResource(() => listRuns(effectiveCanvasId, 50), {
    key: [effectiveCanvasId, openCount],
    enabled: Boolean(isOpen && effectiveCanvasId),
    errorMessage: (e) => `Errore nel caricamento delle esecuzioni: ${requestErrorMessage(e)}`,
  });
  const runs = runsResource.data || [];
  const setRuns = runsResource.setData;
  const runsLoading = runsResource.initialLoading;
  const runsError = runsResource.error;
  // Seleziona automaticamente la piu' recente.
  useEffect(() => {
    if (runsResource.data?.length && !selectedA) setSelectedA(runsResource.data[0]);
  }, [runsResource.data]); // eslint-disable-line react-hooks/exhaustive-deps

  const nodeResource = useResource(() => getNodeData(selectedA.run_id, nodeId, 200), {
    key: [selectedA?.run_id || null, nodeId || null],
    enabled: Boolean(selectedA && nodeId),
    errorMessage: (err) => requestErrorMessage(err, 'Errore nel caricamento dei dati del passo'),
  });
  const nodeData = nodeResource.data || null;
  const nodeDataLoading = nodeResource.loading;
  const nodeDataError = nodeResource.error;

  const compareResource = useResource(async () => {
    const [l1, l2] = await Promise.all([
      compareRunsL1(selectedA.run_id, selectedB.run_id),
      compareRunsL2(selectedA.run_id, selectedB.run_id, nodeId),
    ]);
    return { l1, l2 };
  }, {
    key: [selectedA?.run_id || null, selectedB?.run_id || null, nodeId || null],
    enabled: Boolean(compareMode && selectedA && selectedB),
    // PR-A UX Tier 1: backend (intermediate/router.py) ritorna 422
    // con details.reason (new envelope) o detail.reason (legacy).
    errorMessage: (err) => {
      const reason = errorReason(err);
      const msg = backendMessage(err?.response?.data)
        || (reason ? null : requestErrorMessage(err, 'Errore durante il confronto'));
      return mapCompareErrorReason(reason, msg);
    },
  });
  const compareL1 = compareResource.data?.l1 || null;
  const compareL2 = compareResource.data?.l2 || null;
  const compareLoading = compareResource.loading;
  const compareError = compareResource.error;
  useEffect(() => { if (compareResource.data) setActiveTab('compare'); }, [compareResource.data]);
  // Il confronto e' derivato dalla selezione (chiave): niente da azzerare a mano.

  const handleSelectRun = (run) => {
    if (!compareMode) {
      setSelectedA(run);
      setActiveTab('data');
    } else {
      // In compare mode: prima click → A, seconda → B
      if (!selectedA || (selectedA && selectedB)) {
        setSelectedA(run);
        setSelectedB(null);
      } else if (run.run_id !== selectedA.run_id) {
        setSelectedB(run);
      }
    }
  };

  const handleToggleCompareMode = () => {
    setCompareMode(!compareMode);
    setSelectedB(null);
    if (!compareMode) setActiveTab('compare');
    else setActiveTab('data');
  };

  const handlePin = async (run, e) => {
    e.stopPropagation();
    try {
      await pinRun(run.run_id, !run.pinned);
      setRuns(prev => prev.map(r =>
        r.run_id === run.run_id ? { ...r, pinned: !run.pinned } : r
      ));
    } catch {
      // silenzioso
    }
  };
const colKeys = nodeData?.rows?.length > 0
    ? Object.keys(nodeData.rows[0]).filter(k => k !== '_row_hash')
    : [];

  return (
    <ModalWrapper
      isOpen={isOpen}
      onClose={onClose}
      title={`${term('run_history').name} — ${nodeLabel || nodeId}`}
      icon="🕐"
      size="xl"
      closeOnOverlay={true}
    >
      <div className="rh-layout">
<div className="rh-sidebar">
          <div className="rh-sidebar__header">
            <span className="rh-sidebar__count">{runs.length} {runs.length === 1 ? 'esecuzione' : 'esecuzioni'}</span>
            <button
              className={`rh-compare-toggle ${compareMode ? 'rh-compare-toggle--active' : ''}`}
              onClick={handleToggleCompareMode}
              title="Attiva il confronto: seleziona due esecuzioni"
            >
              {compareMode ? '✕ Annulla' : '⇄ Confronta'}
            </button>
          </div>

          {compareMode && (
            <div className="rh-compare-hint">
              {!selectedA && 'Seleziona la prima esecuzione'}
              {selectedA && !selectedB && 'Seleziona la seconda esecuzione'}
              {selectedA && selectedB && 'Confronto in corso...'}
            </div>
          )}

          {runsLoading && <div className="rh-loading">Caricamento...</div>}
          {runsError && <div className="rh-error">{runsError}</div>}

          <div className="rh-run-list">
            {runs.map((run) => {
              const isA = selectedA?.run_id === run.run_id;
              const isB = selectedB?.run_id === run.run_id;
              return (
                <div
                  key={run.run_id}
                  className={`rh-run-item ${isA ? 'rh-run-item--selected-a' : ''} ${isB ? 'rh-run-item--selected-b' : ''}`}
                  {...pressable(() => handleSelectRun(run))}
                >
                  <div className="rh-run-item__top">
                    <StatusBadge status={run.status} />
                    {compareMode && isA && <span className="rh-run-label">A</span>}
                    {compareMode && isB && <span className="rh-run-label rh-run-label--b">B</span>}
                    <button
                      className={`rh-pin-btn ${run.pinned ? 'rh-pin-btn--active' : ''}`}
                      onClick={(e) => handlePin(run, e)}
                      title={run.pinned ? 'Sblocca (torna nella pulizia automatica)' : 'Fissa (esclusa dalla pulizia automatica)'}
                    >
                      📌
                    </button>
                  </div>
                  <div className="rh-run-item__time">{formatTimestamp(run.timestamp)}</div>
                  <div className="rh-run-item__stats">
                    <span>{formatRows(run.total_rows_out)} righe</span>
                    <span>{formatDuration(run.total_exec_time)}</span>
                  </div>
                </div>
              );
            })}
            {!runsLoading && runs.length === 0 && (
              <div className="rh-empty">Nessuna esecuzione disponibile per questo canvas</div>
            )}
          </div>
        </div>
<div className="rh-main">

          {/* Tab bar */}
          <div className="rh-tabs">
            <button {...READ_ONLY_SAFE}
              className={`rh-tab ${activeTab === 'data' ? 'rh-tab--active' : ''}`}
              onClick={() => setActiveTab('data')}
            >
              📊 Dati nodo
            </button>
            <button {...READ_ONLY_SAFE}
              className={`rh-tab ${activeTab === 'compare' ? 'rh-tab--active' : ''}`}
              onClick={() => setActiveTab('compare')}
              disabled={!compareMode}
            >
              ⇄ Confronto
              {compareL2 && (
                <span className="rh-tab-badge">
                  {compareL2.added_count + compareL2.removed_count}
                </span>
              )}
            </button>
          </div>

          {/* Tab: dati nodo */}
          {activeTab === 'data' && (
            <div className="rh-data-area">
              {!selectedA && (
                <div className="rh-placeholder">Seleziona un'esecuzione dalla lista</div>
              )}
              {selectedA && nodeDataLoading && (
                <div className="rh-loading">Caricamento dei dati del passo...</div>
              )}
              {selectedA && !nodeDataLoading && nodeDataError && (
                <div className="rh-empty" style={{ color: 'var(--color-danger, #b91c1c)' }}>
                  ❌ {nodeDataError}
                </div>
              )}
              {selectedA && !nodeDataLoading && !nodeData && !nodeDataError && (
                <div className="rh-empty">Nessun dato disponibile per questo passo</div>
              )}
              {selectedA && nodeData && (
                <>
                  <div className="rh-data-meta">
                    <span>{formatRows(nodeData.row_count)} righe totali</span>
                    {(nodeData.truncated || nodeData.preview_truncated) && (
                      <span className="rh-truncated-badge">
                        Mostrando prime 200
                      </span>
                    )}
                  </div>
                  <div className="rh-table-wrap">
                    <table className="rh-table">
                      <thead>
                        <tr>
                          {colKeys.map(k => (
                            <th key={k}>{k}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {nodeData.rows.map((row, i) => (
                          <tr key={i}>
                            {colKeys.map(k => (
                              <td key={k} title={row[k] != null ? String(row[k]) : ''}>
                                {row[k] != null ? String(row[k]) : <span className="rh-null">null</span>}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            </div>
          )}

          {/* Tab: confronto */}
          {activeTab === 'compare' && (
            <div className="rh-compare-area">
              {compareLoading && <div className="rh-loading">Confronto in corso...</div>}
              {compareError && <div className="rh-error">{compareError}</div>}

              {!compareLoading && !compareError && compareL1 && (
                <>
                  {/* L1 — summary */}
                  <div className="rh-l1">
                    <div className="rh-l1__cards">
                      <div className={`rh-l1-card ${compareL1.same_input ? 'rh-l1-card--same' : 'rh-l1-card--diff'}`}>
                        <span className="rh-l1-card__label">In ingresso</span>
                        <span className="rh-l1-card__value">
                          {compareL1.same_input ? '= Identico' : '≠ Cambiato'}
                        </span>
                      </div>
                      <div className={`rh-l1-card ${compareL1.same_output ? 'rh-l1-card--same' : 'rh-l1-card--diff'}`}>
                        <span className="rh-l1-card__label">In uscita</span>
                        <span className="rh-l1-card__value">
                          {compareL1.same_output ? '= Identico' : '≠ Cambiato'}
                        </span>
                      </div>
                      <div className="rh-l1-card">
                        <span className="rh-l1-card__label">Δ righe</span>
                        <span className={`rh-l1-card__value ${compareL1.delta_rows_out > 0 ? 'rh-delta--pos' : compareL1.delta_rows_out < 0 ? 'rh-delta--neg' : ''}`}>
                          {compareL1.delta_rows_out != null
                            ? (compareL1.delta_rows_out > 0 ? '+' : '') + formatRows(compareL1.delta_rows_out)
                            : '—'}
                        </span>
                      </div>
                      <div className="rh-l1-card">
                        <span className="rh-l1-card__label">Δ tempo</span>
                        <span className="rh-l1-card__value">
                          {compareL1.delta_exec_time != null
                            ? (compareL1.delta_exec_time > 0 ? '+' : '') + formatDuration(compareL1.delta_exec_time)
                            : '—'}
                        </span>
                      </div>
                    </div>
                  </div>

                  {/* L2 — righe aggiunte/rimosse */}
                  {compareL2 && (
                    <div className="rh-l2">
                      <div className="rh-l2__panels">

                        {/* Rimosse */}
                        <div className="rh-l2-panel rh-l2-panel--removed">
                          <div className="rh-l2-panel__header">
                            <span>🔴 Rimosse</span>
                            <span className="rh-l2-panel__count">{compareL2.removed_count}</span>
                          </div>
                          <div className="rh-table-wrap">
                            {compareL2.removed.length === 0 ? (
                              <div className="rh-empty">Nessuna riga rimossa</div>
                            ) : (
                              <table className="rh-table rh-table--removed">
                                <thead>
                                  <tr>
                                    {Object.keys(compareL2.removed[0])
                                      .filter(k => k !== '_row_hash')
                                      .map(k => <th key={k}>{k}</th>)}
                                  </tr>
                                </thead>
                                <tbody>
                                  {compareL2.removed.map((row, i) => (
                                    <tr key={i}>
                                      {Object.keys(row)
                                        .filter(k => k !== '_row_hash')
                                        .map(k => (
                                          <td key={k}>{row[k] != null ? String(row[k]) : <span className="rh-null">null</span>}</td>
                                        ))}
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            )}
                          </div>
                          {compareL2.truncated && (
                            <div className="rh-truncated-badge">Troncato a 500</div>
                          )}
                        </div>

                        {/* Aggiunte */}
                        <div className="rh-l2-panel rh-l2-panel--added">
                          <div className="rh-l2-panel__header">
                            <span>🟢 Aggiunte</span>
                            <span className="rh-l2-panel__count">{compareL2.added_count}</span>
                          </div>
                          <div className="rh-table-wrap">
                            {compareL2.added.length === 0 ? (
                              <div className="rh-empty">Nessuna riga aggiunta</div>
                            ) : (
                              <table className="rh-table rh-table--added">
                                <thead>
                                  <tr>
                                    {Object.keys(compareL2.added[0])
                                      .filter(k => k !== '_row_hash')
                                      .map(k => <th key={k}>{k}</th>)}
                                  </tr>
                                </thead>
                                <tbody>
                                  {compareL2.added.map((row, i) => (
                                    <tr key={i}>
                                      {Object.keys(row)
                                        .filter(k => k !== '_row_hash')
                                        .map(k => (
                                          <td key={k}>{row[k] != null ? String(row[k]) : <span className="rh-null">null</span>}</td>
                                        ))}
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            )}
                          </div>
                        </div>

                      </div>
                    </div>
                  )}
                </>
              )}

              {!compareLoading && !compareL1 && !compareError && (
                <div className="rh-placeholder">
                  {!selectedA || !selectedB
                    ? "Seleziona due esecuzioni dall'elenco per confrontarle"
                    : 'Avvio confronto...'}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </ModalWrapper>
  );
}
