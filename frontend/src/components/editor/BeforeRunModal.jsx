/**
 * BeforeRunModal — la finestra UNICA «Prima di avviare» dei tre editor
 * (Blocco A, regola 5). La apre solo il percorso unico editor/PipelineRunner,
 * e solo se serve (utils/beforeRun.beforeRunReasons): input runtime da
 * compilare, opzioni dello staff, stima di Arricchisci da confermare, avvisi
 * che bloccano, preparazione cambiata dopo la stima.
 *
 * Sezioni:
 *  - Input: i valori chiesti all'esecuzione (project_input non di tipo file).
 *    «Utente che esegue» (tipo impostato dal server) e' una riga in sola
 *    lettura con lo username di chi e' collegato: non si compila e non si
 *    invia, lo mette il server (utils/beforeRun). Eccezione: con
 *    `admin_override`, admin e superadmin negli Eseguibili vedono un campo
 *    facoltativo per scegliere il valore («Vuoto: si usa il tuo nome
 *    utente»; utils/beforeRun.canChooseIdentity).
 *  - Esecuzione di prova (`request.testRun`, regola H del backend): sezione
 *    in cima con l'utente di prova (quello della stima del server, se c'e'),
 *    dove finisce il risultato e cosa non fa; senza username di prova o con
 *    due diversi, Avvia spento.
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
 * nello stesso avvio) e la loro completezza (`extraComplete`), e un avviso
 * NON bloccante in cima (`notice`: non cambia Avvia).
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ModalWrapper, { ModalButton } from '../common/ModalWrapper';
import { role } from '../../utils/permissions';
import { inputKindLabel, term } from '../../utils/glossary';
import { isServerInjectedKind } from '../../utils/runtimeInputKinds';
import { useAuth } from '../../context/AuthContext';
import { requestErrorMessage } from '../../utils/errorMessages';
import {
  ADMIN_VALUE_MAX_LENGTH, canChooseIdentity, chosenIdentityInputs,
  describeApiCalls, describeInputRows, initialInputValues, inputsComplete, inputsKey, needsPreflight,
  preflightBlocks, runInputErrors, runInputs,
} from '../../utils/beforeRun';
import '../common/FieldInput.css';
import { restMigrationMessage } from '../../utils/restSecrets';
import './BeforeRunModal.css';
import { formatNumber } from '../../utils/format';

// Valori ammessi dal backend per intermediate_mode; '' = non mandarlo
// (vale l'impostazione globale).
const INTERMEDIATE_MODE_OPTIONS = Object.freeze([
  { value: '', label: 'Predefinita (impostazioni globali)' },
  { value: 'metadata_only', label: 'Solo metadati' },
  { value: 'full_artifact', label: 'Risultati completi' },
]);

// Pausa prima di rifare la stima dopo un input cambiato.
const PREFLIGHT_DELAY_MS = 300;

// Fase 5 T5 (R8.10): min/max/pattern/lunghezze non sono solo attributi
// HTML: il valore passa dal convertitore del suo tipo (stessa regola del
// backend), l'errore si vede sul campo e Avvia resta spento.
function InputField({ definition, value, onChange, error, onBlur }) {
  const id = `before-run-input-${definition.input_key}`;
  const errorId = `${id}-error`;
  const change = (e) => onChange(e.target.value);
  const a11y = { 'aria-invalid': error ? true : undefined, 'aria-describedby': error ? errorId : undefined, onBlur };
  let control;
  if (definition.input_kind === 'connection' || definition.input_kind === 'select' || definition.input_kind === 'boolean') {
    const options = definition.input_kind === 'connection'
      ? (definition.connection_options || []).map(o => ({ value: o.key, label: o.label }))
      : definition.input_kind === 'select'
        ? (definition.validation?.options || []).map(o => ({ value: o, label: o }))
        : [{ value: 'true', label: 'Sì' }, { value: 'false', label: 'No' }];
    control = (
      <select id={id} className="before-run__field" value={value === true ? 'true' : value === false ? 'false' : (value ?? '')} onChange={change} {...a11y}>
        <option value="">Seleziona…</option>
        {options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    );
  } else {
    const inputType = definition.input_kind === 'datetime' ? 'datetime-local'
      : definition.input_kind === 'date' ? 'date'
        : 'text';
    control = (
      <input
        id={id}
        className="before-run__field"
        type={inputType}
        inputMode={inputType === 'text' && (definition.input_kind === 'integer' || definition.input_kind === 'number') ? 'decimal' : undefined}
        value={value ?? ''}
        onChange={change}
        {...a11y}
      />
    );
  }
  return (
    <div className="before-run__row">
      <label htmlFor={id} className="before-run__label">
        {definition.label || definition.input_key}{definition.required !== false ? ' *' : ''}
      </label>
      {control}
      {error && <span id={errorId} className="field-input__error" role="alert">{error}</span>}
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

// Nome utente di chi e' collegato (null fuori da AuthProvider: la finestra
// e' condivisa e si usa anche senza sessione nei test).
function useSignedInUsername() {
  try {
    return useAuth()?.user?.username || null;
  } catch {
    return null;
  }
}

// Utente collegato (null fuori da AuthProvider): ruolo per il valore scelto.
function useSignedInUser() {
  try {
    return useAuth()?.user || null;
  } catch {
    return null;
  }
}

// «Utente che esegue» con «Gli amministratori possono scegliere il valore»,
// per admin e superadmin negli Eseguibili: campo facoltativo, il valore e'
// gia' quello finale (nessuna trasformazione); vuoto = il proprio nome utente.
function ChosenIdentityField({ definition, value, onChange, username }) {
  const id = `before-run-input-${definition.input_key}`;
  return (
    <div className="before-run__row">
      <label htmlFor={id} className="before-run__label">
        {definition.admin_override_label || definition.label || definition.input_key}
      </label>
      <input
        id={id}
        className="before-run__field"
        type="text"
        value={value ?? ''}
        maxLength={ADMIN_VALUE_MAX_LENGTH}
        placeholder={username || ''}
        onChange={(e) => onChange(e.target.value)}
      />
      <span className="before-run__hint">
        Vuoto: si usa il tuo nome utente{definition.transform ? ' (con la trasformazione dell’input)' : ''}.
        Un valore scritto qui si usa così com’è e resta nella cronologia dell’esecuzione.
      </span>
      {definition.description && <span className="before-run__hint">{definition.description}</span>}
    </div>
  );
}

// «Utente che esegue»: sola lettura, nessun valore inviato (il server usa
// l'utente autenticato che avvia, o nella prova lo username di prova salvato).
function IdentityField({ definition, username, testRun = false }) {
  const id = `before-run-input-${definition.input_key}`;
  return (
    <div className="before-run__row">
      <label htmlFor={id} className="before-run__label">
        {definition.label || definition.input_key}
      </label>
      <input id={id} className="before-run__field" type="text" value={username || ''} readOnly aria-readonly="true" />
      <span className="before-run__hint">
        {testRun
          ? `${inputKindLabel('current_user')}: nell'esecuzione di prova il server usa il nome utente di prova salvato nell'input; non si può cambiare qui.`
          : `${inputKindLabel('current_user')}: lo imposta il server con l'utente che avvia l'esecuzione${username ? '' : ' (utente non disponibile in questa finestra)'}; non si può cambiare.`}
      </span>
      {definition.description && <span className="before-run__hint">{definition.description}</span>}
    </div>
  );
}

const TEST_RUN_PROBLEMS = Object.freeze({
  missing: "Indica un nome utente di prova nell'input «Utente che esegue» e salva il progetto: senza, l'esecuzione di prova non parte.",
  conflict: 'Gli input «Utente che esegue» hanno nomi utente di prova diversi: usane uno solo.',
});

// «Esegui di prova» (regola H del backend): con chi, dove finisce il risultato.
function TestRunNotice({ username, problem, recalculateIgnored = false }) {
  return (
    <section className="before-run__section" aria-label="Esecuzione di prova">
      <h3 className="before-run__heading">Esecuzione di prova</h3>
      {problem ? (
        <div className="before-run__notice before-run__notice--error" role="alert">{TEST_RUN_PROBLEMS[problem]}</div>
      ) : (
        <div className="before-run__notice" role="status">
          Il progetto viene eseguito come se lo avviasse l&apos;utente di prova <strong>{username}</strong>.
          {' '}Il risultato si scarica una volta dalla finestra di esecuzione: non diventa l&apos;Output del
          progetto, non aggiorna chi lo usa come sorgente e non avvia Carica. Non ricalcola i progetti a monte:
          usa il loro risultato salvato e, se manca o non è aggiornato, si ferma e chiede di eseguire prima il
          progetto a monte. Pianificazioni e progetti collegati restano bloccati: per gli utenti veri pubblica il progetto
          come Eseguibile.
          {recalculateIgnored && (
            <>
              {' '}<strong>«Ricalcola tutto» non vale per la prova</strong>: si usa la cache.
            </>
          )}
        </div>
      )}
    </section>
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
              <span>{formatNumber(node.effective_rows)} righe da arricchire</span>
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
  extra = null, extraInputs = null, extraComplete = true, notice = null,
}) {
  const definitions = request?.inputDefinitions || [];
  const username = useSignedInUsername();
  const signedIn = useSignedInUser();
  // Valore scelto: mai nell'esecuzione di prova (vale il valore salvato nell'input).
  const chooser = request?.testRun ? null : signedIn;
  const migration = request?.migration || [];
  // Esecuzione di prova senza un utente di prova valido: nessuna stima (si blocca prima).
  const withEstimate = Boolean(request) && migration.length === 0 && needsPreflight(request.mode) && typeof onPreflight === 'function'
    && !request.testRun?.problem;
  const [values, setValues] = useState({});
  const [chosen, setChosen] = useState({});
  const [intermediateMode, setIntermediateMode] = useState('');
  const [touched, setTouched] = useState({});
  const [estimate, setEstimate] = useState({ key: null, data: null, error: null, loading: false });
  const seq = useRef(0);

  // Nuova apertura: valori e stima di partenza dalla richiesta.
  useEffect(() => {
    if (!request) return;
    seq.current += 1;
    setValues(request.values || initialInputValues(request.inputDefinitions || []));
    // Valore scelto ripreso dalla richiesta (finestra riaperta dopo la stima).
    setChosen(Object.fromEntries((request.inputDefinitions || [])
      .filter(d => isServerInjectedKind(d.input_kind))
      .map(d => [d.input_key, String(request.values?.[d.input_key] ?? '')])));
    setIntermediateMode('');
    setTouched({});
    setEstimate({
      key: request.preflightKey ?? null,
      data: request.preflight || null,
      error: request.preflightError || null,
      loading: false,
    });
  }, [request]);

  const omitEmptyOptional = Boolean(request?.omitEmptyOptional);
  const inputs = useMemo(
    () => ({
      ...runInputs(definitions, values, { omitEmptyOptional }),
      ...chosenIdentityInputs(definitions, chosen, chooser),
      ...(extraInputs || {}),
    }),
    [definitions, values, omitEmptyOptional, extraInputs, chosen, chooser],
  );
  const key = inputsKey(inputs);
  const inputErrors = runInputErrors(definitions, values);
  const complete = inputsComplete(definitions, values) && extraComplete;
  // Errore visibile: valore scritto non valido subito, obbligatorio vuoto
  // dopo il primo blur.
  const shownError = (d) => {
    const err = inputErrors[d.input_key];
    if (!err) return null;
    const raw = values[d.input_key];
    return (raw !== '' && raw != null) || touched[d.input_key] ? err : null;
  };

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
  // Esecuzione di prova: l'utente che il SERVER usera' (la stima di
  // Arricchisci lo dice; altrimenti la stessa regola sul canvas salvato).
  const testRun = request.testRun || null;
  const testUsername = testRun ? (estimate.data?.test_run?.username || testRun.username) : null;
  const canLaunch = complete && estimateReady && migration.length === 0 && !testRun?.problem;
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
        {run.icon} {testRun
          ? (withEstimate ? 'Conferma e avvia la prova' : "Avvia l'esecuzione di prova")
          : (withEstimate ? 'Conferma e avvia' : "Avvia l'esecuzione")}
      </ModalButton>
    </div>
  );

  return (
    <ModalWrapper
      isOpen
      onClose={onCancel}
      title={request.title || term('before_run').name}
      subtitle={request.subtitle || (testRun && testUsername ? `Esecuzione di prova (utente: ${testUsername})` : undefined)}
      icon={term('before_run').icon}
      size="md"
      variant={request.mode || 'default'}
      footer={footer}
    >
      <div className="before-run">
        {testRun && (
          <TestRunNotice
            username={testUsername}
            problem={testRun.problem}
            recalculateIgnored={Boolean(request.runOptions?.force_recalculate)}
          />
        )}
        {notice}
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
            {definitions.map(d => (canChooseIdentity(d, chooser) ? (
              <ChosenIdentityField
                key={d.input_key}
                definition={d}
                value={chosen[d.input_key]}
                username={username}
                onChange={(v) => setChosen(cur => ({ ...cur, [d.input_key]: v }))}
              />
            ) : isServerInjectedKind(d.input_kind) ? (
              <IdentityField key={d.input_key} definition={d} username={testRun ? testUsername : username} testRun={Boolean(testRun)} />
            ) : (
              <InputField
                key={d.input_key}
                definition={d}
                value={values[d.input_key]}
                error={shownError(d)}
                onBlur={() => setTouched(cur => ({ ...cur, [d.input_key]: true }))}
                onChange={(v) => setValues(cur => ({ ...cur, [d.input_key]: v }))}
              />
            )))}
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
        {isSuper && !testRun && (
          <section className="before-run__section" aria-label="Opzioni dello staff">
            <div className="before-run__row">
              <label htmlFor="before-run-intermediate-mode" className="before-run__label">🔧 Dati intermedi</label>
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
