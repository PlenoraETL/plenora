/**
 * BeforeRunModal — la finestra UNICA «Prima di avviare» dei tre editor
 * (Blocco A, regola 5). La apre solo il percorso unico editor/PipelineRunner,
 * e solo se serve (utils/beforeRun.beforeRunReasons): input runtime da
 * compilare, opzioni dello staff, stima di Arricchisci da confermare, avvisi
 * che bloccano, preparazione cambiata dopo la stima.
 *
 * Sezioni:
 *  - Input: i valori chiesti all'esecuzione (project_input non di tipo file).
 *  - Stima (Arricchisci): per ogni nodo API righe in ingresso (esatte,
 *    stimate o non conoscibili, campionate), chiamate stimate e motivi. La
 *    stima usa GLI STESSI input dell'avvio: cambiare un input la rifa'.
 *    "Avvia" manda l'impronta della stima mostrata. Stima fallita (rete,
 *    4xx, 5xx): messaggio in italiano (requestErrorMessage) e «Riprova», con o
 *    senza input da compilare.
 *  - Avvisi: preparazione rifiutata (Avvia spento) o cambiata dopo la stima.
 *  - Credenziali da migrare: nodi Carica REST con intestazioni segrete nel
 *    canvas (utils/restSecrets). Avvia spento finche' non si migra.
 *  - Opzioni dello staff (superadmin): intermediate_mode.
 *
 * Fase 5 T2 (BA2): la stessa finestra serve gli Eseguibili. Chi la apre puo'
 * aggiungere input propri (`extra`: file da analizzare, formato di uscita)
 * con il loro payload (`extraInputs`, nella stessa chiave della stima e
 * nello stesso avvio) e la loro completezza (`extraComplete`).
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ModalWrapper, { ModalButton } from '../common/ModalWrapper';
import { role } from '../../utils/permissions';
import { term } from '../../utils/glossary';
import { requestErrorMessage } from '../../utils/errorMessages';
import {
  describeApiCalls, describeInputRows, initialInputValues, inputsComplete, inputsKey, needsPreflight,
  preflightBlocks, runInputs,
} from '../../utils/beforeRun';
import { restMigrationMessage } from '../../utils/restSecrets';
import './BeforeRunModal.css';

// Valori ammessi dal backend per intermediate_mode; '' = non mandarlo
// (vale l'impostazione globale).
export const INTERMEDIATE_MODE_OPTIONS = Object.freeze([
  { value: '', label: 'Predefinita (impostazioni globali)' },
  { value: 'metadata_only', label: 'Solo metadati' },
  { value: 'full_artifact', label: 'Risultati completi' },
]);

// Pausa prima di rifare la stima dopo un input cambiato.
export const PREFLIGHT_DELAY_MS = 300;

function InputField({ definition, value, onChange }) {
  const id = `before-run-input-${definition.input_key}`;
  const change = (e) => onChange(e.target.value);
  let control;
  if (definition.input_kind === 'connection' || definition.input_kind === 'select' || definition.input_kind === 'boolean') {
    const options = definition.input_kind === 'connection'
      ? (definition.connection_options || []).map(o => ({ value: o.key, label: o.label }))
      : definition.input_kind === 'select'
        ? (definition.validation?.options || []).map(o => ({ value: o, label: o }))
        : [{ value: 'true', label: 'Sì' }, { value: 'false', label: 'No' }];
    control = (
      <select id={id} className="before-run__field" value={value ?? ''} onChange={change}>
        <option value="">Seleziona…</option>
        {options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    );
  } else {
    const inputType = definition.input_kind === 'integer' || definition.input_kind === 'number' ? 'number'
      : definition.input_kind === 'datetime' ? 'datetime-local'
        : definition.input_kind || 'text';
    const v = definition.validation || {};
    control = (
      <input
        id={id}
        className="before-run__field"
        type={inputType}
        value={value ?? ''}
        onChange={change}
        min={v.minimum}
        max={v.maximum}
        minLength={v.min_length}
        maxLength={v.max_length}
        pattern={v.pattern || undefined}
        step={definition.input_kind === 'number' ? 'any' : definition.input_kind === 'integer' ? '1' : undefined}
      />
    );
  }
  return (
    <div className="before-run__row">
      <label htmlFor={id} className="before-run__label">
        {definition.label || definition.input_key}{definition.required !== false ? ' *' : ''}
      </label>
      {control}
      {definition.connection_options_error && (
        <span className="before-run__hint before-run__notice--error" role="alert">
          Set di connessioni non caricato: {definition.connection_options_error}.
          {definition.retry_connection_options && (
            <>
              {' '}
              <button type="button" className="before-run__link" onClick={definition.retry_connection_options}>Riprova</button>
            </>
          )}
        </span>
      )}
      {definition.description && <span className="before-run__hint">{definition.description}</span>}
    </div>
  );
}

const nodeProblems = (node) => [
  node?.message,
  ...(Array.isArray(node?.errors) ? node.errors.map(item => (typeof item === 'string' ? item : item?.message)) : []),
].filter(Boolean);

function EstimateSection({ state, nodeLabel, onRetry }) {
  if (state.loading) return <p className="before-run__hint" role="status">Calcolo della stima con questi input…</p>;
  if (state.error) {
    return (
      <div className="before-run__notice before-run__notice--error" role="alert">
        Stima non riuscita: {state.error}
        {onRetry && (
          <>
            {' '}
            <ModalButton variant="secondary" onClick={onRetry}>Riprova</ModalButton>
          </>
        )}
      </div>
    );
  }
  const data = state.data;
  if (!data) return <p className="before-run__hint">Compila gli input per vedere la stima.</p>;
  const nodes = Array.isArray(data.nodes) ? data.nodes : [];
  const blocks = preflightBlocks(data);
  return (
    <>
      {blocks && (
        <div className="before-run__notice before-run__notice--error" role="alert">
          La preparazione ha trovato problemi che impediscono di avviare.
          {nodeProblems(data).length > 0 && ` ${nodeProblems(data).join(' ')}`}
        </div>
      )}
      <ul className="before-run__nodes">
        {nodes.map(node => (
          <li key={node.node_id} className={`before-run__node${node.valid === false ? ' before-run__node--invalid' : ''}`}>
            <span className="before-run__node-title">{nodeLabel(node.node_id)}</span>
            <span>{describeInputRows(node)}</span>
            {node.effective_rows != null && (
              <span>{Number(node.effective_rows).toLocaleString('it-IT')} righe da arricchire</span>
            )}
            <span className="before-run__node-calls">{describeApiCalls(node)}</span>
            {node.reason && <span className="before-run__hint">{node.reason}</span>}
            {nodeProblems(node).map(p => <span key={p} className="before-run__hint">⚠ {p}</span>)}
          </li>
        ))}
      </ul>
      {data.checks?.external_dependencies === 'not_guaranteed' && (
        <p className="before-run__hint">Rete e servizi esterni non si possono garantire prima di avviare: la stima non comprende ripetizioni, cache esterna o pagine aggiuntive.</p>
      )}
    </>
  );
}

/**
 * @param {Object|null} request  null = chiusa. { id, mode, inputDefinitions,
 *   user, preflight, preflightError, preflightKey, changedMessage, values }
 * @param {(inputs) => Promise<Object>} onPreflight  stima con questi input (Arricchisci)
 * @param {({inputs, intermediateMode, fingerprint}) => void} onLaunch
 * @param {() => void} onCancel
 * @param {(nodeId) => string} nodeLabel
 */
export default function BeforeRunModal({
  request, onPreflight, onLaunch, onCancel, nodeLabel = (id) => id,
  extra = null, extraInputs = null, extraComplete = true,
}) {
  const definitions = request?.inputDefinitions || [];
  const migration = request?.migration || [];
  const withEstimate = Boolean(request) && migration.length === 0 && needsPreflight(request.mode) && typeof onPreflight === 'function';
  const [values, setValues] = useState({});
  const [intermediateMode, setIntermediateMode] = useState('');
  const [estimate, setEstimate] = useState({ key: null, data: null, error: null, loading: false });
  const seq = useRef(0);

  // Nuova apertura: valori e stima di partenza dalla richiesta.
  useEffect(() => {
    if (!request) return;
    seq.current += 1;
    setValues(request.values || initialInputValues(request.inputDefinitions || []));
    setIntermediateMode('');
    setEstimate({
      key: request.preflightKey ?? null,
      data: request.preflight || null,
      error: request.preflightError || null,
      loading: false,
    });
  }, [request]);

  const omitEmptyOptional = Boolean(request?.omitEmptyOptional);
  const inputs = useMemo(
    () => ({ ...runInputs(definitions, values, { omitEmptyOptional }), ...(extraInputs || {}) }),
    [definitions, values, omitEmptyOptional, extraInputs],
  );
  const key = inputsKey(inputs);
  const complete = inputsComplete(definitions, values) && extraComplete;

  // UNA richiesta di stima (input cambiati o «Riprova»): l'ultima vince.
  const runEstimate = useCallback((forInputs, forKey) => {
    const mine = ++seq.current;
    setEstimate({ key: forKey, data: null, error: null, loading: true });
    Promise.resolve().then(() => onPreflight(forInputs)).then(
      (data) => { if (seq.current === mine) setEstimate({ key: forKey, data, error: null, loading: false }); },
      (err) => { if (seq.current === mine) setEstimate({ key: forKey, data: null, error: requestErrorMessage(err), loading: false }); },
    );
  }, [onPreflight]);

  // La stima segue gli input: compilati e diversi da quelli della stima
  // mostrata -> si rifa' (dopo una pausa), con gli stessi input dell'avvio.
  useEffect(() => {
    if (!withEstimate || !complete || estimate.key === key) return undefined;
    ++seq.current;
    const timer = setTimeout(() => runEstimate(inputs, key), PREFLIGHT_DELAY_MS);
    return () => clearTimeout(timer);
  }, [withEstimate, complete, key, estimate.key, inputs, runEstimate]);

  if (!request) return null;

  const estimateReady = !withEstimate || (
    estimate.key === key && !estimate.loading && !estimate.error && estimate.data && !preflightBlocks(estimate.data)
  );
  const canLaunch = complete && estimateReady && migration.length === 0;
  const isSuper = role.isSuperadmin(request.user);
  const run = term('run');

  const launch = () => {
    if (!canLaunch) return;
    onLaunch({
      inputs,
      intermediateMode: intermediateMode || null,
      fingerprint: withEstimate ? (estimate.data?.preparation_fingerprint || null) : null,
    });
  };

  const footer = (
    <div className="before-run__actions">
      <ModalButton variant="secondary" onClick={onCancel}>Annulla</ModalButton>
      <ModalButton variant="primary" onClick={launch} disabled={!canLaunch}>
        {run.icon} {withEstimate ? 'Conferma e avvia' : "Avvia l'esecuzione"}
      </ModalButton>
    </div>
  );

  return (
    <ModalWrapper
      isOpen
      onClose={onCancel}
      title={request.title || term('before_run').name}
      subtitle={request.subtitle}
      icon={term('before_run').icon}
      size="md"
      variant={request.mode || 'default'}
      footer={footer}
    >
      <div className="before-run">
        {migration.length > 0 && (
          <section className="before-run__section" aria-label="Credenziali da migrare">
            <h3 className="before-run__heading">Credenziali da migrare</h3>
            <div className="before-run__notice before-run__notice--error" role="alert">
              {restMigrationMessage(migration)}
            </div>
            <ul className="before-run__nodes">
              {migration.map(item => (
                <li key={item.nodeId} className="before-run__node before-run__node--invalid">
                  <span className="before-run__node-title">{nodeLabel(item.nodeId)}</span>
                  <span>{item.headers.join(', ')}</span>
                </li>
              ))}
            </ul>
          </section>
        )}
        {request.changedMessage && (
          <div className="before-run__notice" role="alert">
            {request.changedMessage} La stima qui sotto è stata rifatta: controllala e conferma di nuovo.
          </div>
        )}
        {definitions.length > 0 && (
          <section className="before-run__section" aria-label="Input">
            <h3 className="before-run__heading">Input</h3>
            {definitions.map(d => (
              <InputField
                key={d.input_key}
                definition={d}
                value={values[d.input_key]}
                onChange={(v) => setValues(cur => ({ ...cur, [d.input_key]: v }))}
              />
            ))}
          </section>
        )}
        {extra}
        {withEstimate && (
          <section className="before-run__section" aria-label="Stima">
            <h3 className="before-run__heading">Stima delle chiamate API</h3>
            <EstimateSection
              state={estimate.key === key || estimate.loading ? estimate : (complete ? { loading: true } : { data: null })}
              nodeLabel={nodeLabel}
              onRetry={complete ? () => runEstimate(inputs, key) : null}
            />
          </section>
        )}
        {isSuper && (
          <section className="before-run__section" aria-label="Opzioni dello staff">
            <div className="before-run__row">
              <label htmlFor="before-run-intermediate-mode" className="before-run__label">🔧 intermediate_mode (staff)</label>
              <select
                id="before-run-intermediate-mode"
                className="before-run__field"
                value={intermediateMode}
                onChange={(e) => setIntermediateMode(e.target.value)}
              >
                {INTERMEDIATE_MODE_OPTIONS.map(o => <option key={o.value || 'predefinita'} value={o.value}>{o.label}</option>)}
              </select>
              <span className="before-run__hint">Come conservare i risultati intermedi di questa esecuzione. &quot;Predefinita&quot; usa le impostazioni globali.</span>
            </div>
          </section>
        )}
      </div>
    </ModalWrapper>
  );
}
