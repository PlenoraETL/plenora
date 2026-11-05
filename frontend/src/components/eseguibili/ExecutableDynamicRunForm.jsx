/**
 * ExecutableDynamicRunForm — esecuzione di un Eseguibile versionato
 * (manifest con input tipizzati).
 *
 * Fase 5 T2 (BA2, R9.2, R9.3, R4.2, N12): lo stesso percorso dell'editor.
 *  1. «Prima di avviare» (editor/BeforeRunModal, regola unica utils/beforeRun):
 *     input del manifest, file da analizzare con la mappatura delle colonne,
 *     formato e nome del risultato; per Arricchisci la stima delle chiamate
 *     (POST /eseguibili/{id}/preflight con GLI STESSI input dell'avvio) e
 *     l'impronta all'avvio; 409 preparation_changed -> stima rifatta e motivo.
 *  2. Esecuzione ed esito (modals/ExecutionModal) dal ciclo unico
 *     hooks/useTask: progresso, Annulla, esito vero (permessi revocati e
 *     task perso non sono mai "completata"), download con errore mostrato,
 *     pulizia a fine esecuzione per l'esecutore (useEseguibileRun).
 *
 * L'analisi di un file scrive solo se quel file e' ancora quello scelto
 * (id di richiesta: mai l'analisi di A sul file B). Fase 5 T5 (R8.10):
 * l'analisi parte da sola alla scelta del file (prima era un passo manuale
 * per ogni file); «Rianalizza» resta per ripeterla dopo un errore.
 */
import React, { useCallback, useMemo, useRef, useState } from 'react';
import { analyzeEseguibileUpload, preflightEseguibile, runEseguibile } from '../../api';
import { requestErrorMessage, backendMessage } from '../../utils/errorMessages';
import { isPreparationChanged, needsPreflight } from '../../utils/beforeRun';
import BeforeRunModal from '../editor/BeforeRunModal';
import ExecutionModal from '../modals/ExecutionModal';
import { executionResult } from '../../hooks/useExecution';
import { useEseguibileRun } from './useEseguibileRun';
import { initialOutputFormat, outputFormatOptions } from '../../constants/outputFormats';
import './EseguibileExecuteWizard.css';


/** Definizioni del manifest nel formato della finestra «Prima di avviare». */
export const scalarDefinitions = (manifest) => [...(manifest?.inputs || [])]
  .sort((a, b) => (a.order || 0) - (b.order || 0))
  .filter(d => d.kind !== 'file')
  .map(d => ({ ...d, input_key: d.key, input_kind: d.kind }));

const fileDefinitions = (manifest) => [...(manifest?.inputs || [])]
  .sort((a, b) => (a.order || 0) - (b.order || 0))
  .filter(d => d.kind === 'file');

/** Payload dei file analizzati, come lo vuole POST /runs. */
export function filePayloads(definitions, files) {
  const inputs = {};
  definitions.forEach(definition => {
    const items = (files[definition.key] || []).filter(item => item.analysis);
    if (items.length === 0) return;
    const payloads = items.map(item => ({
      file_id: item.analysis.file_id,
      column_mapping: Object.entries(item.mapping || {})
        .filter(([, value]) => value)
        .map(([chain_column, file_column]) => ({ chain_column, file_column })),
    }));
    inputs[definition.key] = definition.multiple ? payloads : payloads[0];
  });
  return inputs;
}

/** Ogni file obbligatorio scelto, analizzato e con le colonne obbligatorie mappate. */
export function filesComplete(definitions, files) {
  return definitions.every(definition => {
    const items = files[definition.key] || [];
    if (definition.required && items.length === 0) return false;
    if (!items.every(item => Boolean(item.analysis))) return false;
    const requiredColumns = (definition.expected_columns || []).filter(column => column.required);
    return items.every(item => requiredColumns.every(column => Boolean(item.mapping?.[column.name])));
  });
}

function FileInputs({ definitions, files, setFiles, eseguibileId }) {
  const [analysis, setAnalysis] = useState({}); // itemId -> { busy, error }
  // Id di ogni file scelto: l'analisi scrive solo sull'elemento analizzato.
  const selectionSeq = useRef(0);

  const selectFiles = (definition, fileList) => {
    const selected = Array.from(fileList || []);
    const items = selected.map(file => ({ id: `f${++selectionSeq.current}`, file, analysis: null, mapping: {} }));
    setFiles(current => ({ ...current, [definition.key]: items }));
    items.forEach(item => { analyzeFile(definition, item); });
  };

  const updateItem = (key, itemId, patch) => setFiles(current => ({
    ...current,
    [key]: (current[key] || []).map(item => (item.id === itemId ? { ...item, ...patch } : item)),
  }));

  const analyzeFile = async (definition, item) => {
    setAnalysis(a => ({ ...a, [item.id]: { busy: true, error: null } }));
    try {
      const result = await analyzeEseguibileUpload(eseguibileId, item.file, definition.key);
      // Scrive solo sull'elemento analizzato: se nel frattempo e' stato
      // scelto un altro file, quell'elemento non c'e' piu' e non cambia niente.
      updateItem(definition.key, item.id, { analysis: result, mapping: { ...(result?.auto_mapping || {}) } });
      setAnalysis(a => ({ ...a, [item.id]: { busy: false, error: null } }));
    } catch (exc) {
      setAnalysis(a => ({ ...a, [item.id]: { busy: false, error: requestErrorMessage(exc) } }));
    }
  };

  return definitions.map(definition => (
    <section className="before-run__section" key={definition.key} aria-label={definition.label}>
      <h3 className="before-run__heading">
        {definition.label}{definition.required ? ' *' : ''}
      </h3>
      {definition.description && <p className="before-run__hint">{definition.description}</p>}
      <label className="esw-run__file-picker">
        <input
          type="file"
          multiple={Boolean(definition.multiple)}
          accept={(definition.validation?.accepted_extensions || []).map(ext => `.${String(ext).replace(/^\./, '')}`).join(',')}
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
      {(files[definition.key] || []).map(item => {
        const a = analysis[item.id] || {};
        return (
          <article className="esw-run__file" key={item.id}>
            <div className="esw-run__file-head">
              <div className="esw-run__file-name">
                <span>▤</span>
                <div>
                  <strong title={item.file?.name}>{item.file?.name}</strong>
                  <small>{item.file ? `${Math.max(1, Math.round(item.file.size / 1024)).toLocaleString()} KB` : ''}</small>
                </div>
              </div>
              <button
                type="button"
                className="esw-run__analyze"
                disabled={!item.file || a.busy}
                onClick={() => analyzeFile(definition, item)}
              >
                {a.busy ? 'Analisi…' : item.analysis ? 'Rianalizza' : 'Analizza file'}
              </button>
            </div>
            {a.error && <p className="before-run__notice before-run__notice--error" role="alert">Analisi non riuscita: {a.error}</p>}
            {item.analysis && (
              <div className="esw-run__analysis">
                <div className="esw-run__analysis-summary">
                  <span className="esw-run__check">✓</span>
                  <strong>{item.analysis.row_count.toLocaleString()} righe rilevate</strong>
                  <span>{item.analysis.detected_columns.length} colonne</span>
                </div>
                {(definition.expected_columns || []).length > 0 && (
                  <div className="esw-run__mapping">
                    {(definition.expected_columns || []).map(column => (
                      <label key={column.name}>
                        <span><code>{column.name}</code>{column.required && ' *'}</span>
                        <select
                          value={item.mapping?.[column.name] || ''}
                          onChange={event => updateItem(definition.key, item.id, { mapping: { ...(item.mapping || {}), [column.name]: event.target.value } })}
                        >
                          <option value="">Non mappata</option>
                          {item.analysis.detected_columns.map(col => (
                            <option key={col.name} value={col.name}>{col.name}</option>
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
    </section>
  ));
}

function ExecutableDynamicRunForm({ eseguibile, onClose, onAfterDownload }) {
  const manifest = eseguibile.manifest;
  const projectType = String(eseguibile.project_type || 'estrai').toLowerCase();
  const modalVariant = ['estrai', 'manipola', 'arricchisci'].includes(projectType) ? projectType : 'estrai';
  const scalars = useMemo(() => scalarDefinitions(manifest), [manifest]);
  const fileDefs = useMemo(() => fileDefinitions(manifest), [manifest]);
  const fileBased = Boolean(eseguibile.output_is_file_based && eseguibile.inferred_output_extension);

  const [files, setFiles] = useState({});
  const [outputFormat, setOutputFormat] = useState(
    fileBased ? eseguibile.inferred_output_extension : initialOutputFormat(eseguibile.default_output_format),
  );
  const [outputName, setOutputName] = useState(eseguibile.default_output_name || '');
  const [launched, setLaunched] = useState(false);
  const run = useEseguibileRun({ eseguibileId: eseguibile.id, onCleanup: onAfterDownload, onClose });

  const newRequest = (extra = {}) => ({
    id: Date.now(),
    mode: projectType,
    title: eseguibile.name,
    subtitle: `Versione ${eseguibile.active_version} · ${projectType.toUpperCase()}`,
    inputDefinitions: scalars,
    omitEmptyOptional: true,
    user: null,
    values: Object.fromEntries(scalars.map(d => [d.input_key, d.default_value ?? ''])),
    preflight: null, preflightError: null, preflightKey: null, changedMessage: null,
    ...extra,
  });
  const [request, setRequest] = useState(newRequest);

  const fileInputs = useMemo(() => filePayloads(fileDefs, files), [fileDefs, files]);
  const fileReady = filesComplete(fileDefs, files);

  const onPreflight = useCallback(
    (inputs) => preflightEseguibile(eseguibile.id, { version_id: eseguibile.active_version_id, inputs }),
    [eseguibile.id, eseguibile.active_version_id],
  );

  const launch = async ({ inputs, fingerprint }) => {
    setLaunched(true);
    let changed = null;
    await run.start(() => runEseguibile(eseguibile.id, {
      version_id: eseguibile.active_version_id,
      inputs,
      output_format: outputFormat,
      output_name: outputName || undefined,
      ...(fingerprint ? { preparation_fingerprint: fingerprint } : {}),
    }), {
      onLaunchError: (err) => {
        if (!isPreparationChanged(err)) return false;
        changed = err;
        return true;
      },
    });
    if (changed) {
      // Preparazione cambiata dalla stima: la finestra si riapre con il
      // motivo e la stima si rifa' con gli stessi input.
      setLaunched(false);
      const scalarValues = Object.fromEntries(scalars.map(d => [d.input_key, inputs[d.input_key] ?? d.default_value ?? '']));
      setRequest(newRequest({
        values: scalarValues,
        preflightKey: null,
        changedMessage: backendMessage(changed?.response?.data) || 'La preparazione è cambiata dalla stima.',
      }));
    }
  };

  const extra = (
    <>
      <FileInputs definitions={fileDefs} files={files} setFiles={setFiles} eseguibileId={eseguibile.id} />
      <section className="before-run__section" aria-label="Risultato">
        <h3 className="before-run__heading">File di output</h3>
        <div className="before-run__row">
          <label htmlFor="esw-output-format" className="before-run__label">Formato</label>
          {fileBased ? (
            <div className="esw-run__fixed-format">.{outputFormat}</div>
          ) : (
            <select id="esw-output-format" className="before-run__field" value={outputFormat} onChange={event => setOutputFormat(event.target.value)}>
              {outputFormatOptions(outputFormat).map(f => <option key={f.value} value={f.value} disabled={f.disabled}>{f.label}</option>)}
            </select>
          )}
        </div>
        <div className="before-run__row">
          <label htmlFor="esw-output-name" className="before-run__label">Nome file</label>
          <input
            id="esw-output-name"
            className="before-run__field"
            value={outputName}
            onChange={event => setOutputName(event.target.value)}
            placeholder={eseguibile.name}
          />
        </div>
      </section>
    </>
  );

  if (!launched) {
    return (
      <BeforeRunModal
        request={request}
        onPreflight={needsPreflight(projectType) ? onPreflight : null}
        onLaunch={launch}
        onCancel={onClose}
        nodeLabel={(id) => id}
        extra={extra}
        extraInputs={fileInputs}
        extraComplete={fileReady}
      />
    );
  }

  const { state } = run;
  const base = (outputName || eseguibile.name || 'risultato').replace(/\.[^.]+$/, '');
  const terminal = run.task.isDone;
  return (
    <ExecutionModal
      isOpen
      onClose={() => run.close()}
      title={`▶ ${eseguibile.name}`}
      projectType={modalVariant}
      isExecuting={run.task.isActive}
      result={terminal ? executionResult(state, state.taskId) : null}
      taskStatus={state.phase === 'running' ? (state.status || 'pending') : state.phase === 'revoked' ? 'revoked' : state.phase === 'success' ? 'success' : state.phase === 'starting' ? 'pending' : 'failure'}
      progress={state.progress}
      progressMessage={state.message}
      onCancel={run.task.isActive && state.taskId ? run.cancel : null}
      cancelling={state.cancelling}
      cancelError={state.cancelError}
      onDownload={state.phase === 'success' ? () => run.download(`${base}.${outputFormat}`) : null}
      downloading={run.downloading}
      downloadError={run.downloadError}
      backgroundable={false}
    />
  );
}

export default ExecutableDynamicRunForm;
