import React, { useMemo, useRef, useState } from 'react';
import { analyzeEseguibileUpload, downloadTaskOutput, runEseguibile } from '../../api';
import { useTaskPolling } from '../../hooks/useTaskPolling';
import useDialogFocus from '../../hooks/useDialogFocus';
import { useToast } from '../../hooks/useToast';
import { humanizeError } from '../../utils/errorMessages';
import PermissionRevokedBanner from '../common/PermissionRevokedBanner';

const OUTPUT_FORMATS = ['xlsx', 'csv', 'json', 'geojson', 'gpkg', 'shp'];

function ExecutableDynamicRunForm({ eseguibile, onClose, onAfterDownload }) {
  const toast = useToast();
  const dialogRef = useRef(null);
  const manifest = eseguibile.manifest;
  const definitions = useMemo(
    () => [...(manifest?.inputs || [])].sort((a, b) => (a.order || 0) - (b.order || 0)),
    [manifest],
  );
  const [values, setValues] = useState(() => Object.fromEntries(
    definitions.filter(item => item.kind !== 'file').map(item => [item.key, item.default_value ?? '']),
  ));
  const [files, setFiles] = useState({});
  const [outputFormat, setOutputFormat] = useState(eseguibile.default_output_format || 'xlsx');
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
  useDialogFocus(true, onClose, dialogRef, { closeOnEsc: !executing });

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
      return items.every(item => Boolean(item.analysis));
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
    const base = (outputName || eseguibile.name || 'risultato').replace(/\.[^.]+$/, '');
    await downloadTaskOutput(taskId, `${base}.${outputFormat}`);
    if (onAfterDownload) await onAfterDownload(taskId);
    onClose();
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

  return (
    <div className="esw-overlay" onClick={event => event.target === event.currentTarget && !executing && onClose()}>
      <div className="esw-modal" ref={dialogRef} role="dialog" aria-modal="true" tabIndex={-1}>
        <div className="esw-header">
          <div><h2 className="esw-title">▶ {eseguibile.name}</h2><p className="esw-subtitle">Versione {eseguibile.active_version} · {String(eseguibile.project_type).toUpperCase()}</p></div>
          <button type="button" className="esw-close" onClick={onClose} disabled={executing}>×</button>
        </div>
        <div className="esw-body esw-dynamic">
          {taskResult?.permissionRevoked && <PermissionRevokedBanner message={taskResult.message} />}
          {error && <div className="esw-error">❌ {error}</div>}

          {!taskId && definitions.map(definition => (
            <section className="esw-dynamic__input" key={definition.key}>
              <div className="esw-dynamic__label">
                <strong>{definition.label}</strong>{definition.required && <span>*</span>}
                <code>{definition.key}</code>
              </div>
              {definition.description && <p>{definition.description}</p>}
              {definition.kind === 'file' ? (
                <>
                  <input
                    type="file"
                    multiple={Boolean(definition.multiple)}
                    accept={(definition.validation?.accepted_extensions || []).map(ext => `.${String(ext).replace(/^\./, '')}`).join(',')}
                    disabled={executing}
                    onChange={event => selectFiles(definition, event.target.files)}
                  />
                  {definition.multiple && <small>{(files[definition.key] || []).length} file selezionati · saranno uniti per righe</small>}
                  {(files[definition.key] || []).map((state, index) => {
                    const itemKey = `${definition.key}:${index}`;
                    return (
                      <div className="esw-dynamic__file" key={`${state.file?.name || 'file'}:${index}`}>
                        <div className="esw-dynamic__file-title">
                          <strong>{state.file?.name}</strong>
                          <button type="button" className="esw-btn esw-btn--secondary" disabled={!state.file || busyKey === itemKey} onClick={() => analyzeFile(definition, index)}>
                            {busyKey === itemKey ? 'Analisi…' : state.analysis ? 'Rianalizza' : 'Analizza'}
                          </button>
                        </div>
                        {state.analysis && (
                          <div className="esw-dynamic__analysis">
                            <span>✓ {state.analysis.row_count} righe</span>
                            {(definition.expected_columns || []).map(column => (
                              <label key={column.name}>
                                <code>{column.name}</code>
                                <select
                                  value={state.mapping?.[column.name] || ''}
                                  onChange={event => updateFile(definition.key, index, { mapping: { ...(state.mapping || {}), [column.name]: event.target.value } })}
                                >
                                  <option value="">Non mappata</option>
                                  {state.analysis.detected_columns.map(item => <option key={item.name} value={item.name}>{item.name}</option>)}
                                </select>
                              </label>
                            ))}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </>
              ) : renderScalar(definition)}
            </section>
          ))}

          {!taskId && (
            <div className="esw-dynamic__output">
              <label>Formato output<select value={outputFormat} disabled={eseguibile.output_is_file_based} onChange={event => setOutputFormat(event.target.value)}>{OUTPUT_FORMATS.map(format => <option key={format} value={format}>{format.toUpperCase()}</option>)}</select></label>
              <label>Nome output<input value={outputName} onChange={event => setOutputName(event.target.value)} placeholder={eseguibile.name} /></label>
            </div>
          )}

          {taskId && taskStatus !== 'success' && (
            <div className="esw-progress"><div className="esw-progress-icon">⚙</div><div className="esw-progress-bar"><div className="esw-progress-fill" style={{ width: `${taskProgress}%` }} /></div><div className="esw-progress-text">{taskMessage || taskStatus}</div></div>
          )}
          {taskStatus === 'success' && <div className="esw-success"><div className="esw-success-icon">✓</div><h3>Esecuzione completata</h3></div>}
        </div>
        <div className="esw-actions">
          <button type="button" className="esw-btn esw-btn--secondary" onClick={onClose} disabled={executing}>Chiudi</button>
          {!taskId && <button type="button" className="esw-btn esw-btn--primary" onClick={execute} disabled={!canRun || executing}>{executing ? 'Avvio…' : '▶ Esegui'}</button>}
          {taskStatus === 'success' && <button type="button" className="esw-btn esw-btn--primary" onClick={download}>Scarica risultato</button>}
        </div>
      </div>
    </div>
  );
}

export default ExecutableDynamicRunForm;
