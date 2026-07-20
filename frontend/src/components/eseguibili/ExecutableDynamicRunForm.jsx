import React, { useMemo, useState } from 'react';
import { analyzeEseguibileUpload, downloadEseguibileOutput, runEseguibile } from '../../api';
import { useTaskPolling } from '../../hooks/useTaskPolling';
import { useSmoothProgress } from '../../hooks/useSmoothProgress';
import { useToast } from '../../hooks/useToast';
import { humanizeError } from '../../utils/errorMessages';
import ModalWrapper, { ModalButton } from '../common/ModalWrapper';
import PermissionRevokedBanner from '../common/PermissionRevokedBanner';
import './EseguibileExecuteWizard.css';

const OUTPUT_FORMATS = ['xlsx', 'csv', 'json', 'geojson', 'gpkg', 'shp'];

function ExecutableDynamicRunForm({ eseguibile, onClose, onAfterDownload }) {
  const toast = useToast();
  const manifest = eseguibile.manifest;
  const definitions = useMemo(
    () => [...(manifest?.inputs || [])].sort((a, b) => (a.order || 0) - (b.order || 0)),
    [manifest],
  );
  const [values, setValues] = useState(() => Object.fromEntries(
    definitions.filter(item => item.kind !== 'file').map(item => [item.key, item.default_value ?? '']),
  ));
  const [files, setFiles] = useState({});
  const [outputFormat, setOutputFormat] = useState(
    eseguibile.output_is_file_based && eseguibile.inferred_output_extension
      ? eseguibile.inferred_output_extension
      : eseguibile.default_output_format || 'xlsx',
  );
  const [outputName, setOutputName] = useState(eseguibile.default_output_name || '');
  const [error, setError] = useState(null);
  const [busyKey, setBusyKey] = useState(null);
  const [executing, setExecuting] = useState(false);
  const [taskId, setTaskId] = useState(null);
  const [taskStatus, setTaskStatus] = useState(null);
  const [taskProgress, setTaskProgress] = useState(0);
  const [taskMessage, setTaskMessage] = useState('');
  const [taskResult, setTaskResult] = useState(null);
  const { startPolling } = useTaskPolling({ interval: 1500 });
  const isRunning = executing || ['pending', 'progress', 'started', 'retry'].includes(taskStatus);
  const smoothProgress = useSmoothProgress(taskProgress, isRunning);
  const projectType = String(eseguibile.project_type || 'estrai').toLowerCase();
  const modalVariant = ['estrai', 'manipola', 'arricchisci'].includes(projectType)
    ? projectType
    : 'estrai';

  const updateFile = (key, index, patch) => setFiles(current => {
    const items = [...(current[key] || [])];
    items[index] = { ...(items[index] || {}), ...patch };
    return { ...current, [key]: items };
  });

  const selectFiles = (definition, fileList) => {
    const selected = Array.from(fileList || []);
    setFiles(current => ({
      ...current,
      [definition.key]: selected.map(file => ({ file, analysis: null, mapping: {} })),
    }));
  };

  const analyzeFile = async (definition, index) => {
    const state = files[definition.key]?.[index];
    if (!state?.file) return;
    const itemKey = `${definition.key}:${index}`;
    setBusyKey(itemKey);
    setError(null);
    try {
      const analysis = await analyzeEseguibileUpload(
        eseguibile.id,
        state.file,
        definition.key,
      );
      updateFile(definition.key, index, {
        analysis,
        mapping: { ...(analysis.auto_mapping || {}) },
      });
    } catch (exc) {
      setError(humanizeError(exc).message);
    } finally {
      setBusyKey(null);
    }
  };

  const startTaskPolling = tid => startPolling(
    tid,
    data => {
      setTaskStatus(data.status);
      setTaskProgress(data.progress || 0);
      setTaskMessage(data.message || '');
      if (data.status === 'success') {
        setTaskResult(data.result || {});
        setExecuting(false);
        toast.success('Esecuzione completata');
        return true;
      }
      if (data.status === 'failure' || data.status === 'revoked') {
        setExecuting(false);
        if (data.result?.status === 'permission_revoked') {
          setTaskResult({ ...data.result, permissionRevoked: true });
        } else {
          setError(data.error || data.result?.message || 'Esecuzione fallita');
        }
        return true;
      }
      return false;
    },
    { immediate: true },
  );

  const canRun = definitions.every(definition => {
    if (definition.kind === 'file') {
      const items = files[definition.key] || [];
      if (definition.required && items.length === 0) return false;
      const allAnalyzed = items.every(item => Boolean(item.analysis));
      if (!allAnalyzed) return false;
      const requiredColumns = (definition.expected_columns || [])
        .filter(column => column.required);
      return items.every(item => requiredColumns.every(
        column => Boolean(item.mapping?.[column.name]),
      ));
    }
    if (!definition.required) return true;
    return values[definition.key] !== '' && values[definition.key] != null;
  });

  const execute = async () => {
    if (!canRun) return;
    setExecuting(true);
    setError(null);
    try {
      const inputs = {};
      definitions.forEach(definition => {
        if (definition.kind === 'file') {
          const items = (files[definition.key] || []).filter(item => item.analysis);
          if (items.length === 0) return;
          const payloads = items.map(item => ({
            file_id: item.analysis.file_id,
            column_mapping: Object.entries(item.mapping || {})
              .filter(([, value]) => value)
              .map(([chain_column, file_column]) => ({ chain_column, file_column })),
          }));
          inputs[definition.key] = definition.multiple ? payloads : payloads[0];
        } else if (values[definition.key] !== '' || definition.required) {
          inputs[definition.key] = values[definition.key];
        }
      });
      const response = await runEseguibile(eseguibile.id, {
        version_id: eseguibile.active_version_id,
        inputs,
        output_format: outputFormat,
        output_name: outputName || undefined,
      });
      setTaskId(response.task_id);
      setTaskStatus('pending');
      startTaskPolling(response.task_id);
    } catch (exc) {
      setError(humanizeError(exc).message);
      setExecuting(false);
    }
  };

  const download = async () => {
    try {
      const base = (outputName || eseguibile.name || 'risultato').replace(/\.[^.]+$/, '');
      await downloadEseguibileOutput(
        eseguibile.id,
        taskId,
        `${base}.${outputFormat}`,
      );
      if (onAfterDownload) await onAfterDownload(taskId);
      onClose();
    } catch (exc) {
      toast.error(humanizeError(exc).message);
    }
  };

  const returnToInputs = () => {
    setTaskId(null);
    setTaskStatus(null);
    setTaskProgress(0);
    setTaskMessage('');
    setTaskResult(null);
    setError(null);
  };

  const renderScalar = definition => {
    const common = {
      value: values[definition.key] ?? '',
      onChange: event => setValues(current => ({ ...current, [definition.key]: event.target.value })),
      disabled: executing,
      min: definition.validation?.minimum,
      max: definition.validation?.maximum,
      minLength: definition.validation?.min_length,
      maxLength: definition.validation?.max_length,
      pattern: definition.validation?.pattern || undefined,
      step: definition.kind === 'number' ? 'any' : definition.kind === 'integer' ? '1' : undefined,
    };
    if (definition.kind === 'boolean') {
      return <select {...common}><option value="">Seleziona…</option><option value="true">Sì</option><option value="false">No</option></select>;
    }
    if (definition.kind === 'select') {
      return <select {...common}><option value="">Seleziona…</option>{(definition.validation?.options || []).map(option => <option key={option} value={option}>{option}</option>)}</select>;
    }
    const type = definition.kind === 'integer' || definition.kind === 'number'
      ? 'number'
      : definition.kind === 'datetime' ? 'datetime-local' : definition.kind;
    return <input {...common} type={type === 'text' ? 'text' : type} />;
  };

  const footer = (
    <>
      {taskId && !isRunning && taskStatus !== 'success' && (
        <ModalButton variant="secondary" onClick={returnToInputs}>
          Torna agli input
        </ModalButton>
      )}
      <ModalButton variant="secondary" onClick={onClose} disabled={isRunning}>
        {taskId ? 'Chiudi' : 'Annulla'}
      </ModalButton>
      {!taskId && (
        <ModalButton variant="primary" onClick={execute} disabled={!canRun} loading={executing}>
          Esegui progetto
        </ModalButton>
      )}
      {taskStatus === 'success' && (
        <ModalButton variant="primary" onClick={download}>
          Scarica risultato
        </ModalButton>
      )}
    </>
  );

  return (
    <ModalWrapper
      isOpen
      onClose={onClose}
      title={eseguibile.name}
      subtitle={`Versione ${eseguibile.active_version} · ${projectType.toUpperCase()}`}
      icon="▶"
      size="lg"
      variant={modalVariant}
      className="esw-run-modal"
      footer={footer}
      closeOnOverlay={!isRunning}
      closeOnEsc={!isRunning}
      showCloseButton={!isRunning}
    >
      <div className="esw-run">
        {taskResult?.permissionRevoked && (
          <PermissionRevokedBanner message={taskResult.message} />
        )}
        {error && !taskResult?.permissionRevoked && (
          <div className="esw-run__alert" role="alert">
            <span className="esw-run__alert-icon">!</span>
            <div>
              <strong>Esecuzione non completata</strong>
              <p>{error}</p>
            </div>
          </div>
        )}

        {!taskId && (
          <>
            <div className="esw-run__intro">
              <div>
                <span className="esw-run__eyebrow">Configurazione esecuzione</span>
                <h3>Compila gli input richiesti</h3>
                <p>I valori inseriti saranno applicati ai nodi collegati nel progetto.</p>
              </div>
              <span className={`esw-run__type esw-run__type--${modalVariant}`}>
                {projectType}
              </span>
            </div>

            <div className="esw-run__inputs">
              {definitions.map((definition, definitionIndex) => (
                <section className="esw-run__input-card" key={definition.key}>
                  <div className="esw-run__input-heading">
                    <span className="esw-run__input-index">{definitionIndex + 1}</span>
                    <div className="esw-run__input-copy">
                      <div className="esw-run__input-title">
                        <strong>{definition.label}</strong>
                        {definition.required && <span className="esw-run__required">Obbligatorio</span>}
                      </div>
                      {definition.description && <p>{definition.description}</p>}
                    </div>
                    <span className="esw-run__kind">{definition.kind}</span>
                  </div>

                  <div className="esw-run__control">
                    {definition.kind === 'file' ? (
                      <>
                        <label className="esw-run__file-picker">
                          <input
                            type="file"
                            multiple={Boolean(definition.multiple)}
                            accept={(definition.validation?.accepted_extensions || []).map(ext => `.${String(ext).replace(/^\./, '')}`).join(',')}
                            disabled={executing}
                            onChange={event => selectFiles(definition, event.target.files)}
                          />
                          <span className="esw-run__file-picker-icon">＋</span>
                          <span>
                            <strong>{definition.multiple ? 'Seleziona uno o più file' : 'Seleziona un file'}</strong>
                            <small>
                              {(definition.validation?.accepted_extensions || []).length
                                ? (definition.validation.accepted_extensions || []).join(', ').toUpperCase()
                                : 'Formati supportati dal progetto'}
                            </small>
                          </span>
                        </label>
                        {definition.multiple && (files[definition.key] || []).length > 0 && (
                          <div className="esw-run__selection-summary">
                            {(files[definition.key] || []).length} file selezionati · unione per righe
                          </div>
                        )}
                        {(files[definition.key] || []).map((state, index) => {
                          const itemKey = `${definition.key}:${index}`;
                          return (
                            <article className="esw-run__file" key={`${state.file?.name || 'file'}:${index}`}>
                              <div className="esw-run__file-head">
                                <div className="esw-run__file-name">
                                  <span>▤</span>
                                  <div>
                                    <strong title={state.file?.name}>{state.file?.name}</strong>
                                    <small>{state.file ? `${Math.max(1, Math.round(state.file.size / 1024)).toLocaleString()} KB` : ''}</small>
                                  </div>
                                </div>
                                <button
                                  type="button"
                                  className="esw-run__analyze"
                                  disabled={!state.file || busyKey === itemKey}
                                  onClick={() => analyzeFile(definition, index)}
                                >
                                  {busyKey === itemKey ? 'Analisi…' : state.analysis ? 'Rianalizza' : 'Analizza file'}
                                </button>
                              </div>
                              {state.analysis && (
                                <div className="esw-run__analysis">
                                  <div className="esw-run__analysis-summary">
                                    <span className="esw-run__check">✓</span>
                                    <strong>{state.analysis.row_count.toLocaleString()} righe rilevate</strong>
                                    <span>{state.analysis.detected_columns.length} colonne</span>
                                  </div>
                                  {(definition.expected_columns || []).length > 0 && (
                                    <div className="esw-run__mapping">
                                      {(definition.expected_columns || []).map(column => (
                                        <label key={column.name}>
                                          <span><code>{column.name}</code>{column.required && ' *'}</span>
                                          <select
                                            value={state.mapping?.[column.name] || ''}
                                            onChange={event => updateFile(definition.key, index, { mapping: { ...(state.mapping || {}), [column.name]: event.target.value } })}
                                          >
                                            <option value="">Non mappata</option>
                                            {state.analysis.detected_columns.map(item => (
                                              <option key={item.name} value={item.name}>{item.name}</option>
                                            ))}
                                          </select>
                                        </label>
                                      ))}
                                    </div>
                                  )}
                                </div>
                              )}
                            </article>
                          );
                        })}
                      </>
                    ) : renderScalar(definition)}
                  </div>
                </section>
              ))}
            </div>

            <section className="esw-run__output-card">
              <div className="esw-run__section-heading">
                <div>
                  <span className="esw-run__eyebrow">Risultato</span>
                  <h3>File di output</h3>
                </div>
                <span className="esw-run__output-icon">↓</span>
              </div>
              <div className="esw-run__output-grid">
                <label>
                  <span>Formato</span>
                  {eseguibile.output_is_file_based ? (
                    <div className="esw-run__fixed-format">.{outputFormat}</div>
                  ) : (
                    <select value={outputFormat} onChange={event => setOutputFormat(event.target.value)}>
                      {OUTPUT_FORMATS.map(format => (
                        <option key={format} value={format}>{format.toUpperCase()}</option>
                      ))}
                    </select>
                  )}
                </label>
                <label>
                  <span>Nome file</span>
                  <input
                    value={outputName}
                    onChange={event => setOutputName(event.target.value)}
                    placeholder={eseguibile.name}
                  />
                </label>
              </div>
            </section>
          </>
        )}

        {taskId && isRunning && (
          <section className="esw-run__state" aria-live="polite">
            <div className={`esw-run__state-mark esw-run__state-mark--${modalVariant}`}>
              <span />
            </div>
            <span className="esw-run__eyebrow">Elaborazione in corso</span>
            <h3>Stiamo eseguendo il progetto</h3>
            <p>{taskMessage || 'Preparazione dei dati…'}</p>
            <div className="esw-run__progress-row">
              <div className="esw-run__progress-bar">
                <div
                  className={`esw-run__progress-fill esw-run__progress-fill--${modalVariant}`}
                  style={{ width: `${smoothProgress}%` }}
                />
              </div>
              <strong>{smoothProgress}%</strong>
            </div>
            <div className="esw-run__task-meta">
              <span>Task</span><code>{taskId}</code>
            </div>
          </section>
        )}

        {taskStatus === 'success' && (
          <section className="esw-run__state esw-run__state--success">
            <div className="esw-run__success-mark">✓</div>
            <span className="esw-run__eyebrow">Operazione completata</span>
            <h3>Il risultato è pronto</h3>
            <p>Puoi scaricare il file generato oppure chiudere questa finestra.</p>
            <div className="esw-run__result-grid">
              {taskResult?.rows_processed != null && (
                <div><span>Righe processate</span><strong>{taskResult.rows_processed.toLocaleString()}</strong></div>
              )}
              <div><span>Formato</span><strong>{outputFormat.toUpperCase()}</strong></div>
              {taskResult?.output_file && (
                <div className="esw-run__result-file"><span>File</span><code>{taskResult.output_file}</code></div>
              )}
            </div>
          </section>
        )}
      </div>
    </ModalWrapper>
  );
}

export default ExecutableDynamicRunForm;
