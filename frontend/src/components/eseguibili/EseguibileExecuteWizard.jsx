/**
 * EseguibileExecuteWizard.jsx — Wizard 3 step per eseguire un eseguibile.
 *
 * Step 1: Upload file (CSV/XLSX/GeoJSON/Shapefile...)
 * Step 2: Mapping colonne (auto + manuale) + preview
 * Step 3: Output format + nome + Esegui + progress + download
 *
 * Fase 5 T2 (R9.2, R9.3, R4.2, N12): dati dall'hook comune useResource;
 * esecuzione, esito, download e pulizia da useEseguibileRun (ciclo unico
 * hooks/useTask): "Esecuzione completata" solo con un successo vero,
 * Annulla durante l'esecuzione, pulizia dell'esecutore a ogni chiusura dopo
 * la fine. L'analisi di un file scrive solo se quel file e' ancora scelto.
 */

import React, { useState, useEffect, useRef } from 'react';
import {
  getEseguibile,
  analyzeEseguibileUpload,
  executeEseguibile,
} from '../../api';
import useDialog from '../../hooks/useDialog';
import { useResource } from '../../hooks/useResource';
import { useEseguibileRun } from './useEseguibileRun';
import PermissionRevokedBanner from '../common/PermissionRevokedBanner';
import { ResourceError } from '../common/ListState';
import { requestErrorMessage } from '../../utils/errorMessages';
import './EseguibileExecuteWizard.css';
import ExecutableDynamicRunForm from './ExecutableDynamicRunForm';

const OUTPUT_FORMATS = [
  { value: 'xlsx',    label: 'Excel (.xlsx)' },
  { value: 'csv',     label: 'CSV (.csv)' },
  { value: 'json',    label: 'JSON (.json)' },
  { value: 'geojson', label: 'GeoJSON (.geojson)' },
  { value: 'zip',     label: 'ZIP (.zip)' },
  { value: 'pdf',     label: 'PDF (.pdf)' },
];

function EseguibileExecuteWizard({ eseguibileId, onClose, onAfterDownload = null }) {
  const dialogRef = useRef(null);
  const [step, setStep] = useState(1);
  const [error, setError] = useState(null);
  const resource = useResource(() => getEseguibile(eseguibileId), { key: eseguibileId });
  const eseguibile = resource.data || null;
  const loading = resource.initialLoading;

  // Step 1
  const [file, setFile] = useState(null);
  const [uploadResult, setUploadResult] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const uploadSeq = useRef(0);

  // Step 2
  const [mapping, setMapping] = useState({}); // {chain_column: file_column}

  // Step 3
  const [outputFormat, setOutputFormat] = useState('xlsx');
  const [outputName, setOutputName] = useState('');
  const run = useEseguibileRun({ eseguibileId, onCleanup: onAfterDownload, onClose });
  const { state } = run;
  const executing = run.task.isActive;
  const close = () => { run.close(); };
  // Stack unico delle finestre: durante l'esecuzione niente Esc, overlay o ×.
  // Con il manifest la finestra e' quella di ExecutableDynamicRunForm.
  const dialog = useDialog({
    isOpen: !eseguibile?.manifest, onClose: close, ref: dialogRef, busy: executing,
  });

  // Formato e nome di partenza quando arriva l'eseguibile. Se il flusso e'
  // file-based l'estensione e' decisa dal connettore (selettore spento).
  useEffect(() => {
    if (!eseguibile) return;
    if (eseguibile.output_is_file_based && eseguibile.inferred_output_extension) {
      setOutputFormat(eseguibile.inferred_output_extension);
    } else {
      setOutputFormat(eseguibile.default_output_format || 'xlsx');
    }
    setOutputName(eseguibile.default_output_name || '');
  }, [eseguibile]);

  // Un file nuovo annulla l'analisi del precedente (id di richiesta).
  const chooseFile = (next) => {
    uploadSeq.current += 1;
    setUploading(false);
    setUploadResult(null);
    setFile(next);
  };

  // Handle file upload & analyze
  const handleUpload = async () => {
    if (!file) return;
    const mine = ++uploadSeq.current;
    setUploading(true);
    setError(null);
    try {
      const result = await analyzeEseguibileUpload(eseguibileId, file);
      if (mine !== uploadSeq.current) return;
      setUploadResult(result);
      // Applica auto-mapping suggerito
      setMapping({ ...result.auto_mapping });
      setStep(2);
    } catch (e) {
      if (mine !== uploadSeq.current) return;
      setError(requestErrorMessage(e));
    } finally {
      if (mine === uploadSeq.current) setUploading(false);
    }
  };

  // Handle execute
  const handleExecute = async () => {
    setError(null);
    const column_mapping = Object.entries(mapping)
      .filter(([, v]) => v)
      .map(([chain_column, file_column]) => ({ chain_column, file_column }));
    await run.start(() => executeEseguibile(eseguibileId, {
      file_id: uploadResult.file_id,
      column_mapping,
      output_format: outputFormat,
      output_name: outputName || undefined,
    }), { startMessage: 'Task in coda...' });
  };

  const handleDownload = () => {
    // Il backend invia il nome via Content-Disposition; questo e' il ripiego.
    const cleanBase = (outputName || eseguibile.name || 'risultato')
      .trim()
      .replace(/\.[a-zA-Z0-9]{1,5}$/, '')   // togli estensione
      .replace(/[\\/:*?"<>|]/g, '_')        // sanitizza
      || 'risultato';
    return run.download(`${cleanBase}.${outputFormat}`);
  };

  const succeeded = state.phase === 'success';
  const permissionRevoked = state.phase === 'permission_revoked';
  const runFailed = ['failure', 'lost', 'revoked'].includes(state.phase);
  const shownError = error || (runFailed ? state.error : null);

  // Validazione mapping: tutte le required mappate?
  const allRequiredMapped = eseguibile?.expected_columns
    ?.filter(c => c.required)
    ?.every(c => mapping[c.name]) ?? false;

  const missingRequired = eseguibile?.expected_columns
    ?.filter(c => c.required && !mapping[c.name])
    ?.map(c => c.name) || [];

  if (eseguibile?.manifest) {
    return (
      <ExecutableDynamicRunForm
        eseguibile={eseguibile}
        onClose={onClose}
        onAfterDownload={onAfterDownload}
      />
    );
  }

  return (
    <div className="esw-overlay" {...dialog.overlayProps}>
      <div
        className="esw-modal"
        ref={dialogRef}
        {...dialog.dialogProps}
      >
        <div className="esw-header">
          <div>
            <h2 className="esw-title" id={dialog.titleId}>▶ {loading ? 'Caricamento...' : eseguibile?.name}</h2>
            {eseguibile?.description && (
              <p className="esw-subtitle">{eseguibile.description}</p>
            )}
          </div>
          <button type="button" className="esw-close" {...dialog.closeButtonProps}>×</button>
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

        {/* PR-A UX Tier 1: banner inline quando permessi revocati
            mid-esecuzione. Prevale sul generic error rendering. */}
        {permissionRevoked && (
          <PermissionRevokedBanner message={state.error} />
        )}
        {shownError && !permissionRevoked && (
          <div className="esw-error" role="alert">❌ {shownError}</div>
        )}

        {loading ? (
          <div className="esw-loading">Caricamento eseguibile...</div>
        ) : resource.status === 'error' ? (
          <ResourceError title="Eseguibile non caricato" message={resource.error} onRetry={resource.reload} />
        ) : (
          <div className="esw-body">
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
                    if (dropped) chooseFile(dropped);
                  }}
                >
                  <input
                    type="file"
                    id="esw-file-input"
                    accept=".csv,.xlsx,.xls,.json,.geojson,.gpkg,.zip"
                    onChange={(e) => chooseFile(e.target.files[0] || null)}
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
                  <button type="button" className="esw-btn esw-btn--ghost" onClick={close}>Annulla</button>
                  <button
                    type="button"
                    className="esw-btn esw-btn--primary"
                    disabled={!file || uploading}
                    onClick={handleUpload}
                  >{uploading ? 'Analisi...' : 'Avanti →'}</button>
                </div>
              </div>
            )}
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
            {step === 3 && eseguibile && (
              <div className="esw-step-content">
                {state.phase === 'idle' || runFailed || permissionRevoked ? (
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
                      <div className="esw-progress-fill" style={{ width: `${state.progress}%` }} />
                    </div>
                    <div className="esw-progress-text">{state.progress}% — {state.message || 'Avvio...'}</div>
                    <p className="esw-hint">Non chiudere questa finestra fino al completamento.</p>
                    {state.cancelError && <div className="esw-error" role="alert">Annullamento non riuscito: {state.cancelError}</div>}
                    {state.taskId && (
                      <div className="esw-actions">
                        <button type="button" className="esw-btn esw-btn--ghost" onClick={run.cancel} disabled={state.cancelling}>
                          {state.cancelling ? 'Annullamento…' : '🛑 Annulla'}
                        </button>
                      </div>
                    )}
                  </div>
                ) : succeeded ? (
                  <div className="esw-success">
                    <div className="esw-success-icon">✅</div>
                    <h3>Esecuzione completata!</h3>
                    <div className="esw-success-stats">
                      {state.result?.rows_processed != null && (
                        <div>Righe processate: <strong>{state.result.rows_processed}</strong></div>
                      )}
                      {state.result?.output_file && (
                        <div>File: <code>{state.result.output_file}</code></div>
                      )}
                    </div>
                    {run.downloadError && <div className="esw-error" role="alert">❌ {run.downloadError}</div>}
                    <div className="esw-actions">
                      <button type="button" className="esw-btn esw-btn--ghost" onClick={close}>Chiudi</button>
                      <button type="button" className="esw-btn esw-btn--primary" onClick={handleDownload} disabled={run.downloading}>
                        {run.downloading ? 'Scaricamento…' : '⬇ Scarica Risultato'}
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
