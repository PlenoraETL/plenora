/**
 * ScheduleWizard.jsx — Wizard multi-step schedule
 * Redesign Plenora design system
 */
import React, { useState, useEffect } from 'react';
import { useToast } from '../../hooks/useToast';
import { listProjects, listConnections } from '../../api';
import { createSchedule, updateSchedule, getSchedule } from '../../api/schedules';
import ModalWrapper, { ModalButton } from '../common/ModalWrapper';
import './ScheduleWizard.css';

const STEPS = [
  { num: 1, label: 'Pipeline',     icon: '⬡' },
  { num: 2, label: 'Frequenza',    icon: '◷' },
  { num: 3, label: 'Esecuzione',   icon: '◈' },
  { num: 4, label: 'Destinazione', icon: '⬢' },
  { num: 5, label: 'Errori',       icon: '⟳' },
  { num: 6, label: 'Riepilogo',    icon: '◉' },
];

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

const WRITE_MODES = [
  { value: 'append',  label: 'Append',  desc: 'aggiungi righe' },
  { value: 'replace', label: 'Replace', desc: 'svuota e riscrivi' },
  { value: 'upsert',  label: 'Upsert',  desc: 'aggiorna o inserisci' },
];

function ScheduleWizard({ onClose, onCreated, editId = null, defaultProjectId = null }) {
  const toast = useToast();
  const [step, setStep] = useState(1);
  const [saving, setSaving] = useState(false);
  const [projects, setProjects] = useState([]);
  const [connections, setConnections] = useState([]);
  const [loadingData, setLoadingData] = useState(true);
  const [cronPreset, setCronPreset] = useState('0 3 * * *');
  const [errors, setErrors] = useState({});

  const [form, setForm] = useState({
    project_id: defaultProjectId || '',
    cron_expression: '0 3 * * *',
    description: '',
    timezone: 'Europe/Rome',
    use_cache: false,
    concurrency_max_queue: 3,
    enable_destination: false,
    connection_id: '',
    schema_name: 'public',
    table_name: '',
    write_mode: 'replace',
    match_columns: '',
    batch_size: 1000,
    verify_destination_connection: true,
    max_retries: 3,
    retry_backoff_seconds: '60, 300, 900',
    append_conflicts: 'skip',
    log_skipped_rows: true,
  });

  useEffect(() => { loadFormData(); }, []);

  const loadFormData = async () => {
    try {
      setLoadingData(true);
      const [projectsData, connectionsData] = await Promise.all([
        listProjects(),
        listConnections(),
      ]);
      setProjects(projectsData.projects || projectsData || []);
      setConnections(connectionsData.connections || connectionsData || []);
      if (editId) {
        const existing = await getSchedule(editId);
        setForm({
          project_id: existing.pipeline?.project_id || '',
          cron_expression: existing.schedule?.cron_expression || '0 3 * * *',
          description: existing.schedule?.description || '',
          timezone: existing.schedule?.timezone || 'Europe/Rome',
          use_cache: existing.execution?.use_cache ?? false,
          concurrency_max_queue: existing.execution?.concurrency_max_queue ?? 3,
          enable_destination: !!existing.destination,
          connection_id: existing.destination?.connection_id || '',
          schema_name: existing.destination?.schema_name || 'public',
          table_name: existing.destination?.table_name || '',
          write_mode: existing.destination?.write_mode || 'replace',
          match_columns: (existing.destination?.match_columns || []).join(', '),
          batch_size: existing.destination?.batch_size ?? 1000,
          verify_destination_connection: existing.destination?.verify_destination_connection ?? true,
          max_retries: existing.on_error?.max_retries ?? 3,
          retry_backoff_seconds: (existing.on_error?.retry_backoff_seconds || [60,300,900]).join(', '),
          append_conflicts: existing.on_error?.append_conflicts || 'skip',
          log_skipped_rows: existing.on_error?.log_skipped_rows ?? true,
        });
        const isPreset = CRON_PRESETS.find(p => p.value === existing.schedule?.cron_expression);
        setCronPreset(isPreset ? existing.schedule.cron_expression : 'custom');
      }
    } catch (e) {
      toast.error('Errore caricamento dati');
    } finally {
      setLoadingData(false);
    }
  };

  const updateForm = (field, value) => {
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

  const validateStep = (s) => {
    const errs = {};
    if (s === 1 && !form.project_id) errs.project_id = 'Seleziona una pipeline';
    if (s === 2 && !form.cron_expression.trim()) errs.cron_expression = 'Espressione cron richiesta';
    if (s === 4 && form.enable_destination) {
      if (!form.connection_id) errs.connection_id = 'Seleziona una connessione';
      if (!form.table_name.trim()) errs.table_name = 'Nome tabella richiesto';
      if (form.write_mode === 'upsert' && !form.match_columns.trim()) errs.match_columns = 'Colonne di match richieste per upsert';
    }
    setErrors(errs);
    return Object.keys(errs).length === 0;
  };

  const goNext = () => { if (validateStep(step)) setStep(Math.min(step + 1, 6)); };
  const goBack = () => setStep(Math.max(step - 1, 1));
  const goToStep = (t) => {
    if (t < step) setStep(t);
    else if (t === step + 1 && validateStep(step)) setStep(t);
  };

  const handleSubmit = async () => {
    if (!validateStep(6)) return;
    try {
      setSaving(true);
      const payload = {
        project_id: form.project_id,
        schedule: { cron_expression: form.cron_expression, description: form.description, timezone: form.timezone, enabled: true },
        execution: { use_cache: form.use_cache, concurrency_max_queue: form.concurrency_max_queue },
        destination: form.enable_destination ? {
          connection_id: form.connection_id,
          schema_name: form.schema_name,
          table_name: form.table_name,
          write_mode: form.write_mode,
          match_columns: form.write_mode === 'upsert' ? form.match_columns.split(',').map(c=>c.trim()).filter(Boolean) : [],
          batch_size: form.batch_size,
          verify_destination_connection: form.verify_destination_connection,
        } : null,
        on_error: {
          max_retries: form.max_retries,
          retry_backoff_seconds: form.retry_backoff_seconds.split(',').map(v=>parseInt(v.trim())).filter(Boolean),
          append_conflicts: form.append_conflicts,
          log_skipped_rows: form.log_skipped_rows,
        },
      };
      if (editId) await updateSchedule(editId, payload);
      else await createSchedule(payload);
      toast.success(editId ? 'Schedule aggiornato' : 'Schedule creato');
      onCreated();
    } catch (e) {
      toast.error('Errore: ' + (e.message || 'errore sconosciuto'));
    } finally {
      setSaving(false);
    }
  };

  if (loadingData) return (
    <ModalWrapper isOpen={true} onClose={onClose} title="Schedule" size="lg" loading loadingText="Caricamento dati..." />
  );

  return (
    <ModalWrapper
      isOpen={true}
      onClose={onClose}
      title={editId ? 'Modifica Schedule' : 'Nuovo Schedule'}
      icon={editId ? '✏️' : '⏰'}
      size="lg"
      closeOnOverlay={!saving}
      closeOnEsc={!saving}
      footer={
        <>
          {step > 1 && (
            <ModalButton variant="secondary" onClick={goBack}>← Indietro</ModalButton>
          )}
          <ModalButton variant="secondary" onClick={onClose}>Annulla</ModalButton>
          {step < 6 ? (
            <ModalButton variant="primary" onClick={goNext}>Avanti →</ModalButton>
          ) : (
            <ModalButton variant="primary" onClick={handleSubmit} loading={saving}>
              {editId ? '💾 Salva Modifiche' : '✅ Crea Schedule'}
            </ModalButton>
          )}
        </>
      }
    >
        {/* Step bar */}
        <div className="wiz-steps">
          {STEPS.map(s => (
            <button type="button" key={s.num}
              className={`wiz-step${step === s.num ? ' wiz-step--active' : ''}${step > s.num ? ' wiz-step--done' : ''}`}
              onClick={() => goToStep(s.num)}>
              <span className="wiz-step__num">{step > s.num ? '✓' : s.icon}</span>
              <span className="wiz-step__label">{s.label}</span>
            </button>
          ))}
        </div>

        {/* Body */}
        <div className="wiz-body">

          {/* STEP 1 — Pipeline */}
          {step === 1 && (
            <div className="wiz-section">
              <div className="wiz-section__header">
                <div className="wiz-section__title">Seleziona la pipeline</div>
                <div className="wiz-section__desc">Quale progetto verrà eseguito automaticamente?</div>
              </div>
              <div className="wiz-project-grid">
                {projects.map(p => {
                  const selected = form.project_id === p.id;
                  return (
                    <div key={p.id}
                      className={`wiz-project-card${selected ? ' wiz-project-card--selected' : ''}`}
                      onClick={() => updateForm('project_id', p.id)}>
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
          )}

          {/* STEP 2 — Frequenza */}
          {step === 2 && (
            <div className="wiz-section">
              <div className="wiz-section__header">
                <div className="wiz-section__title">Frequenza di esecuzione</div>
                <div className="wiz-section__desc">Quanto spesso eseguire la pipeline?</div>
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
                <div className="wiz-label">
                  Espressione Cron
                  <a href="https://crontab.guru/" target="_blank" rel="noreferrer" className="wiz-help-link">crontab.guru ↗</a>
                </div>
                <input type="text"
                  className={`wiz-input wiz-input--mono${errors.cron_expression ? ' wiz-input--error' : ''}`}
                  value={form.cron_expression}
                  onChange={e => { updateForm('cron_expression', e.target.value); setCronPreset('custom'); }}
                  placeholder="0 3 * * *" />
                <span className="wiz-hint">minuto · ora · giorno · mese · giorno_settimana</span>
                {errors.cron_expression && <span className="wiz-error">{errors.cron_expression}</span>}
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
                  <select className="wiz-select" value={form.timezone} onChange={e => updateForm('timezone', e.target.value)}>
                    <option value="Europe/Rome">Europe/Rome (CET/CEST)</option>
                    <option value="UTC">UTC</option>
                    <option value="Europe/London">Europe/London</option>
                  </select>
                </div>
              </div>
            </div>
          )}

          {/* STEP 3 — Esecuzione */}
          {step === 3 && (
            <div className="wiz-section">
              <div className="wiz-section__header">
                <div className="wiz-section__title">Opzioni di esecuzione</div>
              </div>
              <label className="wiz-toggle-row">
                <div className="wiz-toggle-row__content">
                  <div className="wiz-toggle-row__title">Usa cache risultati</div>
                  <div className="wiz-toggle-row__desc">Riutilizza output precedente se la pipeline non è cambiata</div>
                </div>
                <div className={`wiz-toggle${form.use_cache ? ' wiz-toggle--on' : ''}`}
                  onClick={() => updateForm('use_cache', !form.use_cache)}>
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
                <span className="wiz-hint">Se la pipeline è già in esecuzione, quante volte accodare prima di saltare</span>
              </div>
            </div>
          )}

          {/* STEP 4 — Destinazione */}
          {step === 4 && (
            <div className="wiz-section">
              <div className="wiz-section__header">
                <div className="wiz-section__title">Destinazione dati</div>
                <div className="wiz-section__desc">Per default genera solo file output. Abilita per scrivere su database.</div>
              </div>
              <label className="wiz-toggle-row">
                <div className="wiz-toggle-row__content">
                  <div className="wiz-toggle-row__title">Carica su database (CARICA)</div>
                  <div className="wiz-toggle-row__desc">Dopo l'esecuzione, scrivi i risultati su una tabella</div>
                </div>
                <div className={`wiz-toggle${form.enable_destination ? ' wiz-toggle--on' : ''}`}
                  onClick={() => updateForm('enable_destination', !form.enable_destination)}>
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
                      <div className="wiz-label">Batch size</div>
                      <input type="number" className="wiz-input" value={form.batch_size}
                        onChange={e => updateForm('batch_size', parseInt(e.target.value) || 1000)} min={100} max={50000} />
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
          )}

          {/* STEP 5 — Errori */}
          {step === 5 && (
            <div className="wiz-section">
              <div className="wiz-section__header">
                <div className="wiz-section__title">Gestione errori</div>
              </div>
              <div className="wiz-row">
                <div className="wiz-field">
                  <div className="wiz-label">Tentativi retry</div>
                  <select className="wiz-select" value={form.max_retries} onChange={e => updateForm('max_retries', parseInt(e.target.value))}>
                    <option value={0}>0 — nessun retry</option>
                    <option value={1}>1 retry</option>
                    <option value={2}>2 retry</option>
                    <option value={3}>3 retry (consigliato)</option>
                    <option value={5}>5 retry</option>
                  </select>
                </div>
                <div className="wiz-field">
                  <div className="wiz-label">Backoff (secondi)</div>
                  <input className="wiz-input wiz-input--mono" value={form.retry_backoff_seconds}
                    onChange={e => updateForm('retry_backoff_seconds', e.target.value)} placeholder="60, 300, 900" />
                  <span className="wiz-hint">Attesa tra retry, separati da virgola</span>
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
          )}

          {/* STEP 6 — Riepilogo */}
          {step === 6 && (
            <div className="wiz-section">
              <div className="wiz-section__header">
                <div className="wiz-section__title">Riepilogo</div>
                <div className="wiz-section__desc">Controlla i dati prima di salvare.</div>
              </div>
              <div className="wiz-summary">
                {[
                  { title:'Pipeline', icon:'⬡', rows:[
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
                  ] : [['Modalità', <span className="wiz-muted">Solo file output</span>]]},
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
          )}
        </div>
    </ModalWrapper>
  );
}

export default ScheduleWizard;
