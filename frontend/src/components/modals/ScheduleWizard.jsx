/**
 * ScheduleWizard.jsx — Wizard multi-step schedule
 * Redesign Plenora design system
 */
import { useResource } from '../../hooks/useResource';
import React, { useState, useEffect } from 'react';
import { useToast } from '../../hooks/useToast';
import { humanizeError } from '../../utils/errorMessages';
import { readShared } from '../../utils/sharedResources';
import { createSchedule, updateSchedule, getSchedule } from '../../api/schedules';
import ModalWrapper from '../common/ModalWrapper';
import Wizard from '../common/Wizard';
import { toastApiError } from '../../utils/toastApiError';
import * as codecs from '../../utils/fieldCodecs';
import { formErrors, formPayload } from '../../utils/formSchema';
import { DEFAULT_TIMEZONE, timezoneOptions } from '../../utils/timezones';
import { cronWarning, describeCron, nextCronRuns, formatCronRun } from '../../utils/cron';
import './ScheduleWizard.css';
import { pressable } from '../../utils/pressable';

const STEPS = [
  { num: 1, label: 'Progetto',     icon: '⬡' },
  { num: 2, label: 'Frequenza',    icon: '◷' },
  { num: 3, label: 'Esecuzione',   icon: '◈' },
  { num: 4, label: 'Destinazione', icon: '⬢' },
  { num: 5, label: 'Errori',       icon: '⟳' },
  { num: 6, label: 'Riepilogo',    icon: '◉' },
];

/*
 * UI-4 (U31): l'espressione in parole e le prossime 5 esecuzioni, calcolate
 * qui (utils/cron, stessa regola dello scheduler): niente sito esterno per
 * capire un cron. UI conferma C-K2: le esecuzioni sono nel fuso in cui il
 * beat valuta OGNI cron (BEAT_TIMEZONE, GET /schedules/healthcheck →
 * `timezone`), non nel fuso salvato nella pianificazione, che il beat non usa.
 *
 * Conferma finale G7: il fuso dice da dove viene (`timezone_source` del
 * healthcheck): scritto dal beat, dalla configurazione del server (beat non
 * attivo o non aggiornato) o, se lo stato dello scheduler non si e' letto,
 * il fuso predefinito: una stima, e il wizard lo dice.
 * Conferma finale G6: una forma che lo scheduler accetta ma esegue in modo
 * diverso da come si legge (utils/cron.CRON_WARNINGS) si salva, con l'avviso
 * al posto di descrizione ed esecuzioni.
 */
const TIMEZONE_SOURCE_NOTES = Object.freeze({
  beat: (tz) => `Orari nel fuso in cui il server esegue le pianificazioni (${tz}).`,
  api_config: (tz) => `Orari nel fuso ${tz} della configurazione del server: il servizio che esegue le pianificazioni non lo ha ancora confermato (non attivo o non aggiornato).`,
  fallback: (tz) => `Orari nel fuso ${tz} come stima: lo stato del servizio delle pianificazioni non è stato letto, quindi il fuso in cui esegue non è noto.`,
});

const timezoneSourceOf = (timeZone, source) => {
  if (!timeZone) return 'fallback';
  return source === 'beat' ? 'beat' : 'api_config';
};

function CronPreview({ expression, timeZone, tzSource }) {
  const warning = cronWarning(expression);
  if (warning) {
    return (
      <div className="wiz-cron-preview" aria-live="polite">
        <div className="wiz-hint" role="note" data-cron-warning>{warning}</div>
      </div>
    );
  }
  const words = describeCron(expression);
  if (!words) return null;
  const runs = nextCronRuns(expression, { count: 5, timeZone });
  return (
    <div className="wiz-cron-preview" aria-live="polite">
      <div className="wiz-cron-preview__words">{words}</div>
      {runs.length ? (
        <ol className="wiz-cron-preview__runs" aria-label="Prossime esecuzioni">
          {runs.map(r => <li key={formatCronRun(r)}>{formatCronRun(r)}</li>)}
        </ol>
      ) : (
        <div className="wiz-cron-preview__never">Questa espressione non capita nei prossimi anni.</div>
      )}
      <div className="wiz-hint" data-tz-source={tzSource}>{TIMEZONE_SOURCE_NOTES[tzSource](timeZone)}</div>
    </div>
  );
}

const CRON_PRESETS = [
  { label: 'Ogni 15 min',           value: '*/15 * * * *' },
  { label: 'Ogni 30 min',           value: '*/30 * * * *' },
  { label: 'Ogni ora',              value: '0 * * * *' },
  { label: 'Ogni notte 03:00',      value: '0 3 * * *' },
  { label: 'Ogni mattina 06:00',    value: '0 6 * * *' },
  { label: 'Mezzanotte',            value: '0 0 * * *' },
  { label: 'Ogni lunedì',           value: '0 0 * * 1' },
  { label: 'Primo del mese',        value: '0 0 1 * *' },
  { label: 'Personalizzato...',     value: 'custom' },
];

// Subset CRON-safe dei WRITE_MODES canvas: i 3 modi senza interazione
// utente (no match_columns picker / no conflitti UI). Il `value` di
// ciascuna entry deve essere ∈ canvasTypes.WRITE_MODES — verificato dal
// contract test `tests/contract/canvasTypes.contract.test.js`.
const WRITE_MODES = [
  { value: 'append',  label: 'Append',  desc: 'aggiungi righe' },
  { value: 'replace', label: 'Replace', desc: 'svuota e riscrivi' },
  { value: 'upsert',  label: 'Upsert',  desc: 'aggiorna o inserisci' },
];

const RETRY_OPTIONS = [
  { value: '0', label: '0 — nessun retry' },
  { value: '1', label: '1 retry' },
  { value: '2', label: '2 retry' },
  { value: '3', label: '3 retry (consigliato)' },
  { value: '5', label: '5 retry' },
];

const withDestination = (v) => Boolean(v.enable_destination);

/**
 * Schema del modulo (Fase 5 T5, R8.13, R8.4, N13): per ogni campo lo step,
 * il convertitore e quando esiste. Validazione di ogni step e payload
 * vengono da qui: il cron con la regola dello scheduler (utils/cron), il fuso
 * dall'elenco unico (o quello gia' salvato, mostrato com'e'), numeri ed
 * elenchi senza default silenziosi (prima "parseInt(v) || 1000" e i backoff
 * non numerici o 0 scartati).
 */
export const scheduleFields = ({ keepTimezone } = {}) => [
  { name: 'project_id', step: 1, required: true, codec: codecs.text({ optional: false }), requiredMessage: 'Seleziona un progetto' },
  { name: 'cron_expression', step: 2, required: true, codec: codecs.cron() },
  { name: 'description', step: 2, codec: codecs.text() },
  { name: 'timezone', step: 2, required: true, codec: codecs.timezone({ keep: keepTimezone }) },
  { name: 'connection_id', step: 4, visible: withDestination, required: true, codec: codecs.text({ optional: false }), requiredMessage: 'Seleziona una connessione' },
  { name: 'schema_name', step: 4, visible: withDestination, codec: codecs.text() },
  { name: 'table_name', step: 4, visible: withDestination, required: true, codec: codecs.text({ optional: false }), requiredMessage: 'Nome tabella richiesto' },
  { name: 'match_columns', step: 4, visible: (v) => withDestination(v) && v.write_mode === 'upsert', required: true, codec: codecs.list({ item: codecs.text() }), requiredMessage: 'Colonne di match richieste per upsert' },
  { name: 'batch_size', step: 4, visible: withDestination, required: true, codec: codecs.integer({ min: 100, max: 50000 }) },
  { name: 'max_retries', step: 5, required: true, codec: codecs.integer({ min: 0, max: 10 }) },
  { name: 'retry_backoff_seconds', step: 5, codec: codecs.list({ item: codecs.integer({ min: 0, max: 86400 }), unique: false }) },
];

/** Payload dello schedule dai valori convertiti dello schema. */
export function schedulePayload(form, fields) {
  const v = formPayload(fields, form);
  return {
    project_id: v.project_id,
    schedule: { cron_expression: v.cron_expression, description: v.description || '', timezone: v.timezone, enabled: form.enabled },
    execution: { use_cache: form.use_cache, concurrency_max_queue: form.concurrency_max_queue },
    destination: form.enable_destination ? {
      connection_id: v.connection_id,
      schema_name: v.schema_name || 'public',
      table_name: v.table_name,
      write_mode: form.write_mode,
      match_columns: form.write_mode === 'upsert' ? v.match_columns : [],
      batch_size: v.batch_size,
      verify_destination_connection: form.verify_destination_connection,
    } : null,
    on_error: {
      max_retries: v.max_retries,
      retry_backoff_seconds: v.retry_backoff_seconds || [],
      append_conflicts: form.append_conflicts,
      log_skipped_rows: form.log_skipped_rows,
    },
  };
}

function ScheduleWizard({ onClose, onCreated, editId = null, defaultProjectId = null, schedulerTimeZone = null, schedulerTimeZoneSource = null }) {
  const toast = useToast();
  // Fuso del beat; finche' non e' noto, il predefinito del backend (lo stesso).
  const schedulerTz = schedulerTimeZone || DEFAULT_TIMEZONE;
  const tzSource = timezoneSourceOf(schedulerTimeZone, schedulerTimeZoneSource);
  const [step, setStep] = useState(1);
  const [saving, setSaving] = useState(false);
  // Fase 5 T2: dati del modulo dall'hook comune (id di richiesta). Il
  // modulo parte dallo schedule caricato con un effetto senza richieste.
  // UI-1 (C9): progetti e connessioni dalla cache condivisa.
  const formResource = useResource(async ({ force } = {}) => {
    const [projectsData, connectionsData, existing] = await Promise.all([
      readShared('projects', [], { force }),
      readShared('connections', [], { force }),
      editId ? getSchedule(editId) : Promise.resolve(null),
    ]);
    return {
      projects: projectsData.projects || projectsData || [],
      connections: connectionsData.connections || connectionsData || [],
      existing,
    };
  }, { key: ['schedule-form', editId || null], errorMessage: (e) => humanizeError(e).message || 'Errore caricamento dati' });
  const projects = formResource.data?.projects || [];
  const connections = formResource.data?.connections || [];
  const loadingData = formResource.initialLoading;
  const [cronPreset, setCronPreset] = useState('0 3 * * *');
  const [errors, setErrors] = useState({});

  const [form, setForm] = useState({
    project_id: defaultProjectId || '',
    cron_expression: '0 3 * * *',
    description: '',
    timezone: DEFAULT_TIMEZONE,
    use_cache: false,
    concurrency_max_queue: 3,
    enable_destination: false,
    connection_id: '',
    schema_name: 'public',
    table_name: '',
    write_mode: 'replace',
    match_columns: '',
    batch_size: '1000',
    verify_destination_connection: true,
    max_retries: '3',
    retry_backoff_seconds: '60, 300, 900',
    append_conflicts: 'skip',
    log_skipped_rows: true,
    // Stato attivo: in modifica si conserva quello dello schedule esistente
    // (prima il payload inviava sempre enabled:true e riattivava gli
    // schedule disattivati; audit R8.12).
    enabled: true,
  });

  const existing = formResource.data?.existing || null;
  useEffect(() => {
    if (!existing) return;
    setForm({
      project_id: existing.pipeline?.project_id || '',
      cron_expression: existing.schedule?.cron_expression || '0 3 * * *',
      description: existing.schedule?.description || '',
      // Il fuso salvato si mostra COM'E', anche se non e' in elenco (N13).
      timezone: existing.schedule?.timezone || DEFAULT_TIMEZONE,
      use_cache: existing.execution?.use_cache ?? false,
      concurrency_max_queue: existing.execution?.concurrency_max_queue ?? 3,
      enable_destination: !!existing.destination,
      connection_id: existing.destination?.connection_id || '',
      schema_name: existing.destination?.schema_name || 'public',
      table_name: existing.destination?.table_name || '',
      write_mode: existing.destination?.write_mode || 'replace',
      match_columns: (existing.destination?.match_columns || []).join(', '),
      batch_size: String(existing.destination?.batch_size ?? 1000),
      verify_destination_connection: existing.destination?.verify_destination_connection ?? true,
      max_retries: String(existing.on_error?.max_retries ?? 3),
      retry_backoff_seconds: (existing.on_error?.retry_backoff_seconds || [60,300,900]).join(', '),
      append_conflicts: existing.on_error?.append_conflicts || 'skip',
      log_skipped_rows: existing.on_error?.log_skipped_rows ?? true,
      enabled: existing.schedule?.enabled ?? true,
    });
    const isPreset = CRON_PRESETS.find(p => p.value === existing.schedule?.cron_expression);
    setCronPreset(isPreset ? existing.schedule.cron_expression : 'custom');
  }, [existing]);
  // In modifica non si puo' proseguire con il form di default: salvando si
  // sovrascriverebbe lo schedule esistente con valori inventati.
  useEffect(() => {
    if (formResource.status !== 'error') return;
    toast.error(formResource.error);
    if (editId) onClose();
  }, [formResource.status]); // eslint-disable-line react-hooks/exhaustive-deps

  // Scelte dell'utente (anche i clic sulle card, che non sono eventi
  // change/input): contano come dati inseriti per la conferma di chiusura.
  const [userEdited, setUserEdited] = useState(false);
  const updateForm = (field, value) => {
    setUserEdited(true);
    setForm(prev => ({ ...prev, [field]: value }));
    if (errors[field]) setErrors(prev => ({ ...prev, [field]: null }));
  };

  const selectedProject = projects.find(p => p.id === form.project_id);
  const selectedConnection = connections.find(c => c.id === form.connection_id);

  const getTypeIcon = (type) => ({ estrai:'📥', manipola:'⚙️', arricchisci:'🔗' }[type] || '📋');
  const getTypeColor = (type) => ({ estrai:'#34C17A', manipola:'#9B6DFF', arricchisci:'#F5A623' }[type] || '#4B8BFF');
  const getTypeDim = (type) => ({
    estrai:'rgba(52,193,122,0.12)', manipola:'rgba(155,109,255,0.12)', arricchisci:'rgba(245,166,35,0.12)'
  }[type] || 'rgba(75,139,255,0.12)');

  const fields = scheduleFields({ keepTimezone: existing?.schedule?.timezone });
  // Errori di uno step dallo schema (stessa fonte del payload).
  const stepErrors = (s) => formErrors(fields.filter(f => f.step === s), form);
  const validateStep = (s) => {
    const errs = stepErrors(s);
    setErrors(errs);
    return Object.keys(errs).length === 0;
  };

  // Regola unica del Wizard (R6.2-R6.4): ogni step dichiara la sua
  // validazione; Avanti, barra degli step e Salva passano tutti di li', un
  // solo invio alla volta, Annulla/×/Esc con dati inseriti chiedono.
  const stepValidation = (s) => () => (validateStep(s) ? null : 'Completa i campi evidenziati.');

  const handleSubmit = async () => {
    try {
      setSaving(true);
      const payload = schedulePayload(form, fields);
      if (editId) await updateSchedule(editId, payload);
      else await createSchedule(payload);
      toast.success(editId ? 'Pianificazione aggiornata' : 'Pianificazione creata');
      onCreated();
    } catch (e) {
      toastApiError(toast, e, 'Errore');
    } finally {
      setSaving(false);
    }
  };

  if (loadingData) return (
    <ModalWrapper isOpen={true} onClose={onClose} title="Pianificazione" size="lg" loading loadingText="Caricamento dati..." />
  );

  const steps = [
    {
      label: STEPS[0].label,
      validate: stepValidation(1),
      content: (
            <div className="wiz-section">
              <div className="wiz-section__header">
                <div className="wiz-section__title">Scegli il progetto</div>
                <div className="wiz-section__desc">Quale progetto verrà eseguito automaticamente?</div>
              </div>
              <div className="wiz-project-grid">
                {projects.map(p => {
                  const selected = form.project_id === p.id;
                  return (
                    <div key={p.id}
                      className={`wiz-project-card${selected ? ' wiz-project-card--selected' : ''}`}
                      {...pressable(() => updateForm('project_id', p.id))}>
                      <span className="wiz-project-card__icon" style={{ background: getTypeDim(p.project_type), color: getTypeColor(p.project_type) }}>
                        {getTypeIcon(p.project_type)}
                      </span>
                      <div className="wiz-project-card__info">
                        <div className="wiz-project-card__name">{p.name}</div>
                        <div className="wiz-project-card__type" style={{ color: getTypeColor(p.project_type) }}>{p.project_type}</div>
                      </div>
                      {selected && <span className="wiz-project-card__check">✓</span>}
                    </div>
                  );
                })}
              </div>
              {errors.project_id && <span className="wiz-error">{errors.project_id}</span>}
              {selectedProject && (
                <div className="wiz-info-box">
                  <span style={{ color: getTypeColor(selectedProject.project_type) }}>{getTypeIcon(selectedProject.project_type)}</span>
                  {' '}<strong>{selectedProject.name}</strong>
                  <span className="wiz-info-box__type"> · {selectedProject.project_type}</span>
                  {selectedProject.description && <span className="wiz-info-box__desc"> — {selectedProject.description}</span>}
                </div>
              )}
            </div>
          ),
    },
    {
      label: STEPS[1].label,
      validate: stepValidation(2),
      content: (
            <div className="wiz-section">
              <div className="wiz-section__header">
                <div className="wiz-section__title">Frequenza di esecuzione</div>
                <div className="wiz-section__desc">Quanto spesso eseguire il progetto?</div>
              </div>
              <div className="wiz-field">
                <div className="wiz-label">Preset</div>
                <div className="wiz-preset-grid">
                  {CRON_PRESETS.map(p => (
                    <button type="button" key={p.value}
                      className={`wiz-preset${cronPreset === p.value ? ' wiz-preset--active' : ''}`}
                      onClick={() => { setCronPreset(p.value); if (p.value !== 'custom') updateForm('cron_expression', p.value); }}>
                      {p.label}
                    </button>
                  ))}
                </div>
              </div>
              <div className="wiz-field">
                <div className="wiz-label">Espressione cron</div>
                <input type="text" aria-label="Espressione Cron"
                  className={`wiz-input wiz-input--mono${errors.cron_expression ? ' wiz-input--error' : ''}`}
                  value={form.cron_expression}
                  onChange={e => { updateForm('cron_expression', e.target.value); setCronPreset('custom'); }}
                  placeholder="0 3 * * *" />
                <span className="wiz-hint">minuto · ora · giorno · mese · giorno_settimana</span>
                {errors.cron_expression && <span className="wiz-error">{errors.cron_expression}</span>}
                <CronPreview expression={form.cron_expression} timeZone={schedulerTz} tzSource={tzSource} />
              </div>
              <div className="wiz-row">
                <div className="wiz-field">
                  <div className="wiz-label">Descrizione</div>
                  <input type="text" className="wiz-input"
                    value={form.description}
                    onChange={e => updateForm('description', e.target.value)}
                    placeholder="Aggiornamento notturno fabbricati" />
                </div>
                <div className="wiz-field">
                  <div className="wiz-label">Timezone</div>
                  {/* Elenco unico dei fusi (utils/timezones); un fuso salvato
                      fuori elenco si vede com'e' (N13). */}
                  <select className="wiz-select" aria-label="Timezone" value={form.timezone} onChange={e => updateForm('timezone', e.target.value)}>
                    {timezoneOptions(form.timezone).map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                  </select>
                  {errors.timezone && <span className="wiz-error">{errors.timezone}</span>}
                  {form.timezone && form.timezone !== schedulerTz && (
                    <span className="wiz-hint" role="note">
                      Il server esegue ogni pianificazione nel fuso {schedulerTz}: questo campo non sposta gli orari.
                    </span>
                  )}
                </div>
              </div>
            </div>
          ),
    },
    {
      label: STEPS[2].label,
      validate: stepValidation(3),
      content: (
            <div className="wiz-section">
              <div className="wiz-section__header">
                <div className="wiz-section__title">Opzioni di esecuzione</div>
              </div>
              <label className="wiz-toggle-row">
                <div className="wiz-toggle-row__content">
                  <div className="wiz-toggle-row__title">Usa cache risultati</div>
                  <div className="wiz-toggle-row__desc">Riusa il risultato precedente se il progetto non è cambiato</div>
                </div>
                <div className={`wiz-toggle${form.use_cache ? ' wiz-toggle--on' : ''}`}
                  {...pressable(() => updateForm('use_cache', !form.use_cache))}>
                  <div className="wiz-toggle__knob" />
                </div>
              </label>
              <div className="wiz-field">
                <div className="wiz-label">Coda concorrenza</div>
                <div className="wiz-queue-opts">
                  {[1,2,3,5].map(v => (
                    <button type="button" key={v}
                      className={`wiz-queue-opt${form.concurrency_max_queue === v ? ' wiz-queue-opt--active' : ''}`}
                      onClick={() => updateForm('concurrency_max_queue', v)}>
                      <span className="wiz-queue-opt__num">{v}</span>
                      <span className="wiz-queue-opt__desc">
                        {v === 1 ? 'salta se occupato' : v === 2 ? 'accoda 1' : v === 3 ? 'accoda 2 (✓)' : 'accoda 4'}
                      </span>
                    </button>
                  ))}
                </div>
                <span className="wiz-hint">Se il progetto è già in esecuzione, quante volte accodare prima di saltare</span>
              </div>
            </div>
          ),
    },
    {
      label: STEPS[3].label,
      validate: stepValidation(4),
      content: (
            <div className="wiz-section">
              <div className="wiz-section__header">
                <div className="wiz-section__title">Destinazione dati</div>
                <div className="wiz-section__desc">Di solito genera solo il file del risultato. Abilita per scrivere su database.</div>
              </div>
              <label className="wiz-toggle-row">
                <div className="wiz-toggle-row__content">
                  <div className="wiz-toggle-row__title">Carica su database (CARICA)</div>
                  <div className="wiz-toggle-row__desc">Dopo l'esecuzione, scrivi i risultati su una tabella</div>
                </div>
                <div className={`wiz-toggle${form.enable_destination ? ' wiz-toggle--on' : ''}`}
                  {...pressable(() => updateForm('enable_destination', !form.enable_destination))}>
                  <div className="wiz-toggle__knob" />
                </div>
              </label>
              {form.enable_destination && (
                <div className="wiz-destination-fields">
                  <div className="wiz-field">
                    <div className="wiz-label">Connessione *</div>
                    <select className={`wiz-select${errors.connection_id ? ' wiz-select--error' : ''}`}
                      value={form.connection_id} onChange={e => updateForm('connection_id', e.target.value)}>
                      <option value="">— Seleziona —</option>
                      {connections.map(c => <option key={c.id} value={c.id}>{c.name} ({c.type} - {c.database})</option>)}
                    </select>
                    {errors.connection_id && <span className="wiz-error">{errors.connection_id}</span>}
                  </div>
                  <div className="wiz-row">
                    <div className="wiz-field">
                      <div className="wiz-label">Schema</div>
                      <input className="wiz-input" value={form.schema_name}
                        onChange={e => updateForm('schema_name', e.target.value)} placeholder="public" />
                    </div>
                    <div className="wiz-field">
                      <div className="wiz-label">Tabella *</div>
                      <input className={`wiz-input${errors.table_name ? ' wiz-input--error' : ''}`}
                        value={form.table_name} onChange={e => updateForm('table_name', e.target.value)} placeholder="nome_tabella" />
                      {errors.table_name && <span className="wiz-error">{errors.table_name}</span>}
                    </div>
                  </div>
                  <div className="wiz-field">
                    <div className="wiz-label">Modalità scrittura</div>
                    <div className="wiz-write-modes">
                      {WRITE_MODES.map(m => (
                        <button type="button" key={m.value}
                          className={`wiz-write-mode${form.write_mode === m.value ? ' wiz-write-mode--active' : ''}`}
                          onClick={() => updateForm('write_mode', m.value)}>
                          <span className="wiz-write-mode__label">{m.label}</span>
                          <span className="wiz-write-mode__desc">{m.desc}</span>
                        </button>
                      ))}
                    </div>
                  </div>
                  {form.write_mode === 'upsert' && (
                    <div className="wiz-field">
                      <div className="wiz-label">Colonne di match *</div>
                      <input className={`wiz-input wiz-input--mono${errors.match_columns ? ' wiz-input--error' : ''}`}
                        value={form.match_columns} onChange={e => updateForm('match_columns', e.target.value)} placeholder="id, codice_fiscale" />
                      <span className="wiz-hint">Separare con virgola</span>
                      {errors.match_columns && <span className="wiz-error">{errors.match_columns}</span>}
                    </div>
                  )}
                  <div className="wiz-row">
                    <div className="wiz-field">
                      <div className="wiz-label">Righe per blocco</div>
                      <input type="text" inputMode="numeric" aria-label="Righe per blocco"
                        className={`wiz-input${errors.batch_size ? ' wiz-input--error' : ''}`} value={form.batch_size}
                        onChange={e => updateForm('batch_size', e.target.value)} />
                      <span className="wiz-hint">Da 100 a 50000</span>
                      {errors.batch_size && <span className="wiz-error">{errors.batch_size}</span>}
                    </div>
                    <div className="wiz-field wiz-field--check-aligned">
                      <label className="wiz-check-label">
                        <input type="checkbox" checked={form.verify_destination_connection}
                          onChange={e => updateForm('verify_destination_connection', e.target.checked)} />
                        <span>Pre-check connessione</span>
                      </label>
                    </div>
                  </div>
                </div>
              )}
            </div>
          ),
    },
    {
      label: STEPS[4].label,
      validate: stepValidation(5),
      content: (
            <div className="wiz-section">
              <div className="wiz-section__header">
                <div className="wiz-section__title">Gestione errori</div>
              </div>
              <div className="wiz-row">
                <div className="wiz-field">
                  <div className="wiz-label">Tentativi retry</div>
                  <select className="wiz-select" aria-label="Tentativi retry" value={form.max_retries} onChange={e => updateForm('max_retries', e.target.value)}>
                    {/* Un valore salvato fuori elenco si vede com'e'. */}
                    {!RETRY_OPTIONS.some(o => o.value === form.max_retries) && (
                      <option value={form.max_retries}>{form.max_retries} retry</option>
                    )}
                    {RETRY_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                  </select>
                  {errors.max_retries && <span className="wiz-error">{errors.max_retries}</span>}
                </div>
                <div className="wiz-field">
                  <div className="wiz-label">Backoff (secondi)</div>
                  <input className={`wiz-input wiz-input--mono${errors.retry_backoff_seconds ? ' wiz-input--error' : ''}`} aria-label="Backoff (secondi)" value={form.retry_backoff_seconds}
                    onChange={e => updateForm('retry_backoff_seconds', e.target.value)} placeholder="60, 300, 900" />
                  <span className="wiz-hint">Attesa tra retry, separati da virgola</span>
                  {errors.retry_backoff_seconds && <span className="wiz-error">{errors.retry_backoff_seconds}</span>}
                </div>
              </div>
              <div className="wiz-field">
                <div className="wiz-label">Politica conflitti</div>
                <select className="wiz-select" value={form.append_conflicts} onChange={e => updateForm('append_conflicts', e.target.value)}>
                  <option value="skip">Skip — salta righe duplicate</option>
                  <option value="overwrite">Overwrite — sovrascrivi duplicati</option>
                  <option value="abort">Abort — interrompi al primo conflitto</option>
                </select>
              </div>
              <label className="wiz-check-label">
                <input type="checkbox" checked={form.log_skipped_rows}
                  onChange={e => updateForm('log_skipped_rows', e.target.checked)} />
                <span>Log righe saltate nel report</span>
              </label>
            </div>
          ),
    },
    {
      label: STEPS[5].label,
      validate: stepValidation(6),
      content: (
            <div className="wiz-section">
              <div className="wiz-section__header">
                <div className="wiz-section__title">Riepilogo</div>
                <div className="wiz-section__desc">Controlla i dati prima di salvare.</div>
              </div>
              <div className="wiz-summary">
                {[
                  { title:'Progetto', icon:'⬡', rows:[
                    ['Progetto', <strong>{selectedProject?.name || '—'}</strong>],
                    ['Tipo', selectedProject?.project_type || '—'],
                  ]},
                  { title:'Frequenza', icon:'◷', rows:[
                    ['Cron', <code>{form.cron_expression}</code>],
                    ...(form.description ? [['Descrizione', form.description]] : []),
                    ['Timezone', form.timezone],
                  ]},
                  { title:'Esecuzione', icon:'◈', rows:[
                    ['Cache', form.use_cache ? 'Attiva' : 'Disattiva'],
                    ['Max coda', form.concurrency_max_queue],
                  ]},
                  { title:'Destinazione', icon:'⬢', rows: form.enable_destination ? [
                    ['Connessione', selectedConnection?.name || '—'],
                    ['Tabella', <code>{form.schema_name ? `${form.schema_name}.` : ''}{form.table_name}</code>],
                    ['Modalità', form.write_mode],
                  ] : [['Modalità', <span className="wiz-muted">Solo il file del risultato</span>]]},
                  { title:'Errori', icon:'⟳', rows:[
                    ['Retry', `${form.max_retries} (${form.retry_backoff_seconds}s)`],
                    ['Conflitti', form.append_conflicts],
                  ]},
                ].map(group => (
                  <div key={group.title} className="wiz-summary__group">
                    <div className="wiz-summary__group-title">
                      <span className="wiz-summary__group-icon">{group.icon}</span> {group.title}
                    </div>
                    {group.rows.map(([label, val], i) => (
                      <div key={i} className="wiz-summary__row">
                        <span className="wiz-summary__row-label">{label}</span>
                        <span className="wiz-summary__row-val">{val}</span>
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            </div>
          ),
    },
  ];

  return (
    <Wizard
      isOpen
      onClose={onClose}
      title={editId ? 'Modifica la pianificazione' : 'Nuova pianificazione'}
      icon={editId ? '✏️' : '⏰'}
      step={step - 1}
      onStepChange={(i) => setStep(i + 1)}
      steps={steps}
      onComplete={handleSubmit}
      completeLabel={editId ? '💾 Salva le modifiche' : '✅ Crea la pianificazione'}
      busy={saving}
      dirty={userEdited || step > 1}
    />
  );
}

export default ScheduleWizard;
