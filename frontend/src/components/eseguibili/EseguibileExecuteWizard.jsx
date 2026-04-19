/**
 * EseguibileExecuteWizard.jsx — Wizard 3 step per eseguire un eseguibile.
 *
 * Step 1: Upload file (CSV/XLSX/GeoJSON/Shapefile...)
 * Step 2: Mapping colonne (auto + manuale) + preview
 * Step 3: Output format + nome + Esegui + progress + download
 */

import React, { useState, useEffect, useRef } from 'react';
import {
  getEseguibile,
  analyzeEseguibileUpload,
  executeEseguibile,
  getTaskStatus,
  downloadTaskOutput,
} from '../../api';
import { useToast } from '../../hooks/useToast';
import './EseguibileExecuteWizard.css';

const OUTPUT_FORMATS = [
  { value: 'xlsx',    label: 'Excel (.xlsx)' },
  { value: 'csv',     label: 'CSV (.csv)' },
  { value: 'json',    label: 'JSON (.json)' },
  { value: 'geojson', label: 'GeoJSON (.geojson)' },
  { value: 'zip',     label: 'ZIP (.zip)' },
  { value: 'pdf',     label: 'PDF (.pdf)' },
];

function EseguibileExecuteWizard({ eseguibileId, onClose, onAfterDownload = null }) {
  const toast = useToast();
  const [step, setStep] = useState(1);
  const [loading, setLoading] = useState(true);
  const [eseguibile, setEseguibile] = useState(null);
  const [error, setError] = useState(null);

  // Step 1
  const [file, setFile] = useState(null);
  const [uploadResult, setUploadResult] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [isDragging, setIsDragging] = useState(false);

  // Step 2
  const [mapping, setMapping] = useState({}); // {chain_column: file_column}

  // Step 3
  const [outputFormat, setOutputFormat] = useState('xlsx');
  const [outputName, setOutputName] = useState('');
  const [executing, setExecuting] = useState(false);
  const [taskId, setTaskId] = useState(null);
  const [taskStatus, setTaskStatus] = useState(null);
  const [taskProgress, setTaskProgress] = useState(0);
  const [taskMessage, setTaskMessage] = useState('');
  const [taskResult, setTaskResult] = useState(null);
  const pollRef = useRef(null);

  // Load eseguibile details
  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        const data = await getEseguibile(eseguibileId);
        if (!mounted) return;
        setEseguibile(data);
        // Se il flusso è file-based (rest_file/executable come ultimo
        // nodo), l'estensione è decisa dal connettore: forziamo
        // outputFormat all'estensione inferita dal backend, ignorando
        // default_output_format (il selettore verrà disabilitato).
        if (data.output_is_file_based && data.inferred_output_extension) {
          setOutputFormat(data.inferred_output_extension);
        } else {
          setOutputFormat(data.default_output_format || 'xlsx');
        }
        setOutputName(data.default_output_name || '');
      } catch (e) {
        if (!mounted) return;
        setError(e?.response?.data?.detail || e.message);
      } finally {
        if (mounted) setLoading(false);
      }
    })();
    return () => { mounted = false; };
  }, [eseguibileId]);

  // Cleanup polling on unmount
  useEffect(() => () => {
    if (pollRef.current) clearInterval(pollRef.current);
  }, []);

  // Handle file upload & analyze
  const handleUpload = async () => {
    if (!file) return;
    setUploading(true);
    setError(null);
    try {
      const result = await analyzeEseguibileUpload(eseguibileId, file);
      setUploadResult(result);
      // Applica auto-mapping suggerito
      setMapping({ ...result.auto_mapping });
      setStep(2);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message);
    } finally {
      setUploading(false);
    }
  };

  // Handle execute
  const handleExecute = async () => {
    setExecuting(true);
    setError(null);
    try {
      const column_mapping = Object.entries(mapping)
        .filter(([, v]) => v)
        .map(([chain_column, file_column]) => ({ chain_column, file_column }));

      const resp = await executeEseguibile(eseguibileId, {
        file_id: uploadResult.file_id,
        column_mapping,
        output_format: outputFormat,
        output_name: outputName || undefined,
      });

      setTaskId(resp.task_id);
      setTaskStatus('pending');
      setTaskMessage('Task in coda...');
      startPolling(resp.task_id);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message);
      setExecuting(false);
    }
  };

  const startPolling = (tid) => {
    const poll = async () => {
      try {
        const data = await getTaskStatus(tid);
        setTaskStatus(data.status);
        setTaskProgress(data.progress || 0);
        setTaskMessage(data.message || '');
        if (data.status === 'success') {
          setTaskResult(data.result);
          setExecuting(false);
          clearInterval(pollRef.current);
          toast.success('Esecuzione completata!');
        } else if (data.status === 'failure' || data.status === 'revoked') {
          setExecuting(false);
          setError(data.error || 'Esecuzione fallita');
          clearInterval(pollRef.current);
        }
      } catch (e) {
        // Task not registered yet — keep polling
        if (!e.message?.includes('non trovato')) {
          console.error('[Wizard] Polling error:', e);
        }
      }
    };
    poll();
    pollRef.current = setInterval(poll, 1500);
  };

  const handleDownload = async () => {
    try {
      // Costruisci un fallback ragionevole: il backend invia comunque il nome
      // corretto via Content-Disposition (che ora è esposto via CORS), ma se
      // qualcosa non funziona usiamo questo come ultima spiaggia.
      const cleanBase = (outputName || eseguibile.name || 'risultato')
        .trim()
        .replace(/\.[a-zA-Z0-9]{1,5}$/, '')   // togli estensione
        .replace(/[\\/:*?"<>|]/g, '_')        // sanitizza
        || 'risultato';
      const fallbackName = `${cleanBase}.${outputFormat}`;
      await downloadTaskOutput(taskId, fallbackName);

      // Hook post-download: usato dalla kiosk page degli esecutori per
      // chiamare cleanupTaskCompletely(taskId) e cancellare ogni traccia
      // dell'esecuzione (privacy / GDPR). Per gli altri ruoli è no-op.
      if (onAfterDownload && taskId) {
        try { await onAfterDownload(taskId); } catch (e) { /* best-effort */ }
      }

      // Chiudi il modal dopo aver avviato il download: il blob e' gia' stato
      // consegnato al browser (fire-and-forget), quindi la chiusura non interrompe
      // il trasferimento.
      if (onClose) onClose();
    } catch (e) {
      toast.error('Errore download: ' + (e?.response?.data?.detail || e.message));
    }
  };

  // Validazione mapping: tutte le required mappate?
  const allRequiredMapped = eseguibile?.expected_columns
    ?.filter(c => c.required)
    ?.every(c => mapping[c.name]) ?? false;

  const missingRequired = eseguibile?.expected_columns
    ?.filter(c => c.required && !mapping[c.name])
    ?.map(c => c.name) || [];

  return (
    <div className="esw-overlay" onClick={(e) => e.target === e.currentTarget && !executing && onClose()}>
      <div className="esw-modal">
        <div className="esw-header">
          <div>
            <h2 className="esw-title">▶ {loading ? 'Caricamento...' : eseguibile?.name}</h2>
            {eseguibile?.description && (
              <p className="esw-subtitle">{eseguibile.description}</p>
            )}
          </div>
          <button type="button" className="esw-close" onClick={onClose} disabled={executing}>×</button>
        </div>

        {/* Stepper */}
        <div className="esw-stepper">
          {['📁 Upload file', '🔗 Mappa colonne', '▶ Esegui'].map((label, idx) => {
            const num = idx + 1;
            const active = step === num;
            const done = step > num;
            return (
              <div key={num} className={`esw-step ${active ? 'esw-step--active' : ''} ${done ? 'esw-step--done' : ''}`}>
                <span className="esw-step__num">{done ? '✓' : num}</span>
                <span className="esw-step__label">{label}</span>
              </div>
            );
          })}
        </div>

        {error && (
          <div className="esw-error">❌ {error}</div>
        )}

        {loading ? (
          <div className="esw-loading">Caricamento eseguibile...</div>
        ) : (
          <div className="esw-body">

            {/* ───────── STEP 1: UPLOAD ───────── */}
            {step === 1 && eseguibile && (
              <div className="esw-step-content">
                <h3>Carica il file con i dati da processare</h3>
                <p className="esw-hint">
                  La catena si aspetta queste colonne:
                </p>
                <ul className="esw-expected">
                  {eseguibile.expected_columns.map(c => (
                    <li key={c.name}>
                      <code>{c.name}</code>
                      <span className={c.required ? 'esw-required' : 'esw-optional'}>
                        {c.required ? '• obbligatoria' : '• opzionale'}
                      </span>
                      {c.description && <em>— {c.description}</em>}
                    </li>
                  ))}
                </ul>

                <div
                  className={`esw-dropzone ${isDragging ? 'esw-dropzone--dragging' : ''}`}
                  onDragEnter={(e) => { e.preventDefault(); e.stopPropagation(); setIsDragging(true); }}
                  onDragOver={(e) => { e.preventDefault(); e.stopPropagation(); setIsDragging(true); }}
                  onDragLeave={(e) => {
                    e.preventDefault(); e.stopPropagation();
                    // Solo se davvero esce dal dropzone (non da un figlio)
                    if (e.currentTarget.contains(e.relatedTarget)) return;
                    setIsDragging(false);
                  }}
                  onDrop={(e) => {
                    e.preventDefault(); e.stopPropagation();
                    setIsDragging(false);
                    const dropped = e.dataTransfer.files?.[0];
                    if (dropped) setFile(dropped);
                  }}
                >
                  <input
                    type="file"
                    id="esw-file-input"
                    accept=".csv,.xlsx,.xls,.json,.geojson,.gpkg,.zip"
                    onChange={(e) => setFile(e.target.files[0] || null)}
                  />
                  <label htmlFor="esw-file-input" className="esw-dropzone-label">
                    {file ? (
                      <>
                        <div className="esw-dropzone-icon">📄</div>
                        <div className="esw-dropzone-text">{file.name}</div>
                        <div className="esw-dropzone-hint">{(file.size / 1024).toFixed(1)} KB — Click o trascina per cambiare</div>
                      </>
                    ) : (
                      <>
                        <div className="esw-dropzone-icon">{isDragging ? '⬇' : '📁'}</div>
                        <div className="esw-dropzone-text">
                          {isDragging ? 'Rilascia il file qui' : 'Trascina un file qui o click per selezionarlo'}
                        </div>
                        <div className="esw-dropzone-hint">CSV, XLSX, JSON, GeoJSON, GeoPackage, Shapefile</div>
                      </>
                    )}
                  </label>
                </div>

                <div className="esw-actions">
                  <button type="button" className="esw-btn esw-btn--ghost" onClick={onClose}>Annulla</button>
                  <button
                    type="button"
                    className="esw-btn esw-btn--primary"
                    disabled={!file || uploading}
                    onClick={handleUpload}
                  >{uploading ? 'Analisi...' : 'Avanti →'}</button>
                </div>
              </div>
            )}

            {/* ───────── STEP 2: MAPPING ───────── */}
            {step === 2 && uploadResult && eseguibile && (
              <div className="esw-step-content">
                <h3>Mappa le colonne del tuo file alle colonne della catena</h3>
                <p className="esw-hint">
                  File caricato: <strong>{uploadResult.row_count}</strong> righe, <strong>{uploadResult.detected_columns.length}</strong> colonne
                </p>

                <div className="esw-mapping">
                  <div className="esw-mapping-header">
                    <div>Colonna catena</div>
                    <div>Colonna file</div>
                  </div>
                  {eseguibile.expected_columns.map(c => (
                    <div key={c.name} className="esw-mapping-row">
                      <div className="esw-mapping-chain">
                        <code>{c.name}</code>
                        <span className="esw-mapping-type">{c.type}</span>
                        {c.required && <span className="esw-required-badge">*</span>}
                      </div>
                      <div>
                        <select
                          className="esw-select"
                          value={mapping[c.name] || ''}
                          onChange={(e) => setMapping({ ...mapping, [c.name]: e.target.value })}
                        >
                          <option value="">— Seleziona colonna —</option>
                          {uploadResult.detected_columns.map(dc => (
                            <option key={dc.name} value={dc.name}>
                              {dc.name} ({dc.type})
                            </option>
                          ))}
                        </select>
                      </div>
                    </div>
                  ))}
                </div>

                {uploadResult.preview_rows && uploadResult.preview_rows.length > 0 && (
                  <details className="esw-preview-details">
                    <summary>👁 Anteprima dati ({uploadResult.preview_rows.length} righe)</summary>
                    <div className="esw-preview-table-wrap">
                      <table className="esw-preview-table">
                        <thead>
                          <tr>
                            {uploadResult.detected_columns.map(dc => (
                              <th key={dc.name}>{dc.name}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {uploadResult.preview_rows.map((row, i) => (
                            <tr key={i}>
                              {uploadResult.detected_columns.map(dc => (
                                <td key={dc.name}>{row[dc.name] != null ? String(row[dc.name]) : ''}</td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </details>
                )}

                {!allRequiredMapped && (
                  <div className="esw-warning">
                    ⚠ Colonne obbligatorie non mappate: <strong>{missingRequired.join(', ')}</strong>
                  </div>
                )}

                <div className="esw-actions">
                  <button type="button" className="esw-btn esw-btn--ghost" onClick={() => setStep(1)}>← Indietro</button>
                  <button
                    type="button"
                    className="esw-btn esw-btn--primary"
                    disabled={!allRequiredMapped}
                    onClick={() => setStep(3)}
                  >Avanti →</button>
                </div>
              </div>
            )}

            {/* ───────── STEP 3: EXECUTE ───────── */}
            {step === 3 && eseguibile && (
              <div className="esw-step-content">
                {!executing && !taskResult ? (
                  <>
                    <h3>Configura l'output ed esegui</h3>
                    <div className="esw-form">
                      <div className="esw-field">
                        <label>Formato output</label>
                        {eseguibile.output_is_file_based ? (
                          <div className="esw-format-fixed">
                            <span className="esw-format-fixed__ext">
                              .{eseguibile.inferred_output_extension || 'zip'}
                            </span>
                            <span className="esw-format-fixed__note">
                              Determinato dal connettore finale · non modificabile
                            </span>
                          </div>
                        ) : (
                          <select className="esw-select" value={outputFormat} onChange={(e) => setOutputFormat(e.target.value)}>
                            {OUTPUT_FORMATS.map(f => (
                              <option key={f.value} value={f.value}>{f.label}</option>
                            ))}
                          </select>
                        )}
                      </div>
                      <div className="esw-field">
                        <label>Nome file output (opzionale)</label>
                        <input
                          type="text"
                          className="esw-input"
                          value={outputName}
                          onChange={(e) => setOutputName(e.target.value)}
                          placeholder={eseguibile.default_output_name || `${eseguibile.name}_result`}
                        />
                      </div>
                    </div>
                    <div className="esw-actions">
                      <button type="button" className="esw-btn esw-btn--ghost" onClick={() => setStep(2)}>← Indietro</button>
                      <button type="button" className="esw-btn esw-btn--primary" onClick={handleExecute}>
                        ▶ Esegui
                      </button>
                    </div>
                  </>
                ) : executing ? (
                  <div className="esw-executing">
                    <div className="esw-progress-icon">⚙️</div>
                    <h3>Esecuzione in corso...</h3>
                    <div className="esw-progress-bar">
                      <div className="esw-progress-fill" style={{ width: `${taskProgress}%` }} />
                    </div>
                    <div className="esw-progress-text">{taskProgress}% — {taskMessage || 'Avvio...'}</div>
                    <p className="esw-hint">Non chiudere questa finestra fino al completamento.</p>
                  </div>
                ) : taskResult ? (
                  <div className="esw-success">
                    <div className="esw-success-icon">✅</div>
                    <h3>Esecuzione completata!</h3>
                    <div className="esw-success-stats">
                      {taskResult.rows_processed != null && (
                        <div>Righe processate: <strong>{taskResult.rows_processed}</strong></div>
                      )}
                      {taskResult.output_file && (
                        <div>File: <code>{taskResult.output_file}</code></div>
                      )}
                    </div>
                    <div className="esw-actions">
                      <button type="button" className="esw-btn esw-btn--ghost" onClick={onClose}>Chiudi</button>
                      <button type="button" className="esw-btn esw-btn--primary" onClick={handleDownload}>
                        ⬇ Scarica Risultato
                      </button>
                    </div>
                  </div>
                ) : null}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export default EseguibileExecuteWizard;
