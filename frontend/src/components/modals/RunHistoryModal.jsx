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

import React, { useState, useEffect, useCallback } from 'react';
import { useParams } from 'react-router-dom';
import ModalWrapper from '../common/ModalWrapper';
import { listRuns, getNodeData, compareRunsL1, compareRunsL2, pinRun } from '../../api/runs';
import './RunHistoryModal.css';

// ─── helpers ─────────────────────────────────────────────────────────────────

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
      || 'Diff riga-per-riga non disponibile: almeno una delle due run usa persistence_mode=metadata_only. Per change-detection L1: usa il confronto hash.';
  }
  if (reason === 'persistence_mode_mismatch') {
    return safeBe
      || 'Diff non disponibile: le due run hanno persistence_mode diversi (full_pg vs preview_only). Riallinea le run alla stessa mode oppure usa /compare/l1 per change-detection via hash.';
  }
  return safeBe || 'Errore durante il confronto';
}

const formatTimestamp = (ts) => {
  if (!ts) return '—';
  const d = new Date(ts);
  return d.toLocaleString('it-IT', {
    day: '2-digit', month: '2-digit', year: '2-digit',
    hour: '2-digit', minute: '2-digit',
  });
};

const formatDuration = (secs) => {
  if (secs == null) return '—';
  if (secs < 60) return `${secs.toFixed(1)}s`;
  return `${Math.floor(secs / 60)}m ${Math.round(secs % 60)}s`;
};

const formatRows = (n) => {
  if (n == null) return '—';
  return n.toLocaleString('it-IT');
};

const StatusBadge = ({ status }) => {
  const map = {
    completed: { label: 'OK', cls: 'rh-badge--ok' },
    failed:    { label: 'FAIL', cls: 'rh-badge--fail' },
    running:   { label: '...', cls: 'rh-badge--running' },
  };
  const { label, cls } = map[status] || { label: status, cls: '' };
  return <span className={`rh-badge ${cls}`}>{label}</span>;
};

// ─── component ────────────────────────────────────────────────────────────────

export default function RunHistoryModal({ isOpen, onClose, canvasId, nodeId, nodeLabel }) {
  const params = useParams();
  const effectiveCanvasId = canvasId || params.projectId || params.id || params.canvasId;
  // Lista runs
  const [runs, setRuns] = useState([]);
  const [runsLoading, setRunsLoading] = useState(false);
  const [runsError, setRunsError] = useState(null);

  // Selezione run (una o due per confronto)
  const [selectedA, setSelectedA] = useState(null);
  const [selectedB, setSelectedB] = useState(null);
  const [compareMode, setCompareMode] = useState(false);

  // Dati nodo
  const [nodeData, setNodeData] = useState(null);
  const [nodeDataLoading, setNodeDataLoading] = useState(false);

  // Confronto
  const [compareL1, setCompareL1] = useState(null);
  const [compareL2, setCompareL2] = useState(null);
  const [compareLoading, setCompareLoading] = useState(false);
  const [compareError, setCompareError] = useState(null);

  // Tab area principale
  const [activeTab, setActiveTab] = useState('data'); // 'data' | 'compare'

  // ── carica runs ────────────────────────────────────────────────────────────

  const loadRuns = useCallback(async () => {
    if (!effectiveCanvasId) return;
    setRunsLoading(true);
    setRunsError(null);
    try {
      const data = await listRuns(effectiveCanvasId, 50);
      setRuns(data);
      // Seleziona automaticamente la più recente
      if (data.length > 0 && !selectedA) {
        setSelectedA(data[0]);
      }
    } catch (e) {
      setRunsError('Errore caricamento run');
    } finally {
      setRunsLoading(false);
    }
  }, [effectiveCanvasId]);

  useEffect(() => {
    if (isOpen) {
      loadRuns();
      setSelectedA(null);
      setSelectedB(null);
      setCompareMode(false);
      setNodeData(null);
      setCompareL1(null);
      setCompareL2(null);
    }
  }, [isOpen, canvasId]);

  // ── carica dati nodo quando cambia selectedA ───────────────────────────────

  const [nodeDataError, setNodeDataError] = useState(null);

  useEffect(() => {
    if (!selectedA || !nodeId) return;
    setNodeData(null);
    setNodeDataError(null);
    setNodeDataLoading(true);
    getNodeData(selectedA.run_id, nodeId, 200)
      .then(setNodeData)
      .catch((err) => {
        // Prima: catch silenzioso → l'utente vedeva area dati vuota senza spiegazione.
        console.error('[RunHistoryModal] errore caricamento dati nodo:', err);
        setNodeData(null);
        setNodeDataError(err?.message || 'Errore caricamento dati nodo');
      })
      .finally(() => setNodeDataLoading(false));
  }, [selectedA, nodeId]);

  // ── confronto quando entrambe le run sono selezionate ─────────────────────

  useEffect(() => {
    if (!compareMode || !selectedA || !selectedB) return;
    setCompareLoading(true);
    setCompareError(null);
    setCompareL1(null);
    setCompareL2(null);

    Promise.all([
      compareRunsL1(selectedA.run_id, selectedB.run_id),
      compareRunsL2(selectedA.run_id, selectedB.run_id, nodeId),
    ])
      .then(([l1, l2]) => {
        setCompareL1(l1);
        setCompareL2(l2);
        setActiveTab('compare');
      })
      .catch((err) => {
        // PR-A UX Tier 1: backend (intermediate/router.py) ritorna 422
        // con details.reason (new envelope) o detail.reason (legacy).
        const details = err?.response?.data?.details;
        const detail = err?.response?.data?.detail;
        const reason = details?.reason || detail?.reason;
        const msg = details?.message || detail?.message || err?.message;
        setCompareError(mapCompareErrorReason(reason, msg));
      })
      .finally(() => setCompareLoading(false));
  }, [compareMode, selectedA, selectedB, nodeId]);

  // ── handlers ───────────────────────────────────────────────────────────────

  const handleSelectRun = (run) => {
    if (!compareMode) {
      setSelectedA(run);
      setActiveTab('data');
    } else {
      // In compare mode: prima click → A, seconda → B
      if (!selectedA || (selectedA && selectedB)) {
        setSelectedA(run);
        setSelectedB(null);
        setCompareL1(null);
        setCompareL2(null);
      } else if (run.run_id !== selectedA.run_id) {
        setSelectedB(run);
      }
    }
  };

  const handleToggleCompareMode = () => {
    setCompareMode(!compareMode);
    setSelectedB(null);
    setCompareL1(null);
    setCompareL2(null);
    setCompareError(null);
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

  // ── render ─────────────────────────────────────────────────────────────────

  const colKeys = nodeData?.rows?.length > 0
    ? Object.keys(nodeData.rows[0]).filter(k => k !== '_row_hash')
    : [];

  return (
    <ModalWrapper
      isOpen={isOpen}
      onClose={onClose}
      title={`Cronologia run — ${nodeLabel || nodeId}`}
      icon="🕐"
      size="xl"
      closeOnOverlay={true}
    >
      <div className="rh-layout">

        {/* ── SIDEBAR SINISTRA: lista run ──────────────────────────────── */}
        <div className="rh-sidebar">
          <div className="rh-sidebar__header">
            <span className="rh-sidebar__count">{runs.length} run</span>
            <button
              className={`rh-compare-toggle ${compareMode ? 'rh-compare-toggle--active' : ''}`}
              onClick={handleToggleCompareMode}
              title="Attiva modalità confronto — seleziona due run"
            >
              {compareMode ? '✕ Annulla' : '⇄ Confronta'}
            </button>
          </div>

          {compareMode && (
            <div className="rh-compare-hint">
              {!selectedA && 'Seleziona la prima run'}
              {selectedA && !selectedB && 'Seleziona la seconda run'}
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
                  onClick={() => handleSelectRun(run)}
                >
                  <div className="rh-run-item__top">
                    <StatusBadge status={run.status} />
                    {compareMode && isA && <span className="rh-run-label">A</span>}
                    {compareMode && isB && <span className="rh-run-label rh-run-label--b">B</span>}
                    <button
                      className={`rh-pin-btn ${run.pinned ? 'rh-pin-btn--active' : ''}`}
                      onClick={(e) => handlePin(run, e)}
                      title={run.pinned ? 'Spinna (includi in retention)' : 'Pinna (escludi da retention)'}
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
              <div className="rh-empty">Nessuna run disponibile per questo canvas</div>
            )}
          </div>
        </div>

        {/* ── AREA PRINCIPALE ─────────────────────────────────────────── */}
        <div className="rh-main">

          {/* Tab bar */}
          <div className="rh-tabs">
            <button
              className={`rh-tab ${activeTab === 'data' ? 'rh-tab--active' : ''}`}
              onClick={() => setActiveTab('data')}
            >
              📊 Dati nodo
            </button>
            <button
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
                <div className="rh-placeholder">Seleziona una run dalla lista</div>
              )}
              {selectedA && nodeDataLoading && (
                <div className="rh-loading">Caricamento dati nodo...</div>
              )}
              {selectedA && !nodeDataLoading && nodeDataError && (
                <div className="rh-empty" style={{ color: 'var(--color-danger, #b91c1c)' }}>
                  ❌ {nodeDataError}
                </div>
              )}
              {selectedA && !nodeDataLoading && !nodeData && !nodeDataError && (
                <div className="rh-empty">Nessun dato disponibile per questo nodo</div>
              )}
              {selectedA && nodeData && (
                <>
                  <div className="rh-data-meta">
                    <span>{formatRows(nodeData.row_count)} righe totali</span>
                    {nodeData.truncated && (
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
                        <span className="rh-l1-card__label">Input</span>
                        <span className="rh-l1-card__value">
                          {compareL1.same_input ? '= Identico' : '≠ Cambiato'}
                        </span>
                      </div>
                      <div className={`rh-l1-card ${compareL1.same_output ? 'rh-l1-card--same' : 'rh-l1-card--diff'}`}>
                        <span className="rh-l1-card__label">Output</span>
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
                    ? 'Seleziona due run dalla sidebar per confrontarle'
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
