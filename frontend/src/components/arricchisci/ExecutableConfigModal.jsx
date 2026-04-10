/**
 * ExecutableConfigModal.jsx — Configurazione eseguibile locale
 * Redesign Plenora design system
 */
import React, { useState } from 'react';
import { testExecutable } from '../../api/templates';
import { useAuth } from '../../context/AuthContext';
import { role } from '../../utils/permissions';
import './ExecutableConfigModal.css';

const EXEC_TYPES = [
  { id:'python',     label:'Script Python', icon:'🐍', ext:'.py',  cmd:'python'  },
  { id:'r',          label:'Script R',       icon:'📊', ext:'.R',   cmd:'Rscript' },
  { id:'executable', label:'Eseguibile',     icon:'⚙️', ext:'.exe', cmd:''        },
  { id:'shell',      label:'Script Shell',   icon:'📜', ext:'.sh',  cmd:'bash'    },
  { id:'node',       label:'Node.js',        icon:'🟢', ext:'.js',  cmd:'node'    },
];
const INPUT_MODES = [
  { id:'stdin',    label:'Standard Input (stdin)', desc:'Dati passati via pipe' },
  { id:'file',     label:'File temporaneo',        desc:'Path passato come argomento' },
  { id:'args',     label:'Argomenti CLI',           desc:'Ogni campo come argomento' },
  { id:'json_arg', label:'JSON come argomento',     desc:'Tutti i dati come singolo JSON' },
];
const OUTPUT_MODES = [
  { id:'stdout',      label:'Standard Output (stdout)', desc:'Output letto da stdout' },
  { id:'file',        label:'File di output',            desc:'Output scritto su file' },
  { id:'json_stdout', label:'JSON da stdout',            desc:'Output JSON parsato automaticamente' },
];

const STEPS = ['Tipo','File','I/O','Parametri','Test'];

function ExecutableConfigModal({ onClose, onSave, initialData = null }) {
  const { user } = useAuth();
  const isAdmin = role.isAdminOrAbove(user);
  const [step, setStep] = useState(0);
  const exec = initialData?.executable || {};
  const [formData, setFormData] = useState({
    name: initialData?.name || '',
    description: initialData?.description || '',
    exec_type: initialData?.api_type || 'enricher',
    executable_type: exec.type || 'python',
    executable_path: exec.path || '',
    working_dir: exec.working_dir || '',
    input_mode: exec.input_mode || 'json_arg',
    output_mode: exec.output_mode || 'json_stdout',
    output_extension: exec.output_extension || '',
    timeout: exec.timeout || 60,
    parameters: initialData?.parameters || [],
    output_mapping: initialData?.output_mapping || [],
    env_vars: Object.entries(exec.env_vars || {}).map(([key,value]) => ({key,value})),
  });
  const [uploadedFile, setUploadedFile] = useState(null);
  const [testResult, setTestResult] = useState(null);
  const [testing, setTesting] = useState(false);
  const [testInput, setTestInput] = useState('{}');

  const upd = (field, value) => setFormData(prev => ({...prev, [field]:value}));

  const handleFileUpload = (e) => {
    const file = e.target.files[0]; if (!file) return;
    setUploadedFile(file);
    upd('name', formData.name || file.name.replace(/\.[^/.]+$/, ''));
    upd('executable_path', file.name);
  };

  const addParameter    = () => upd('parameters', [...formData.parameters, {name:'',type:'string',required:true,description:''}]);
  const updateParameter = (i,f,v) => upd('parameters', formData.parameters.map((p,j) => j===i?{...p,[f]:v}:p));
  const removeParameter = (i) => upd('parameters', formData.parameters.filter((_,j) => j!==i));
  const addOutputMapping    = () => upd('output_mapping', [...formData.output_mapping, {path:'',column:'',type:'string'}]);
  const updateOutputMapping = (i,f,v) => upd('output_mapping', formData.output_mapping.map((o,j) => j===i?{...o,[f]:v}:o));
  const removeOutputMapping = (i) => upd('output_mapping', formData.output_mapping.filter((_,j) => j!==i));
  const addEnvVar = () => upd('env_vars', [...formData.env_vars, {key:'',value:''}]);
  const updateEnvVar = (i,f,v) => upd('env_vars', formData.env_vars.map((e,j) => j===i?{...e,[f]:v}:e));
  const removeEnvVar = (i) => upd('env_vars', formData.env_vars.filter((_,j) => j!==i));

  const handleTest = async () => {
    setTesting(true); setTestResult(null);
    try {
      const data = await testExecutable({
        executable: { type:formData.executable_type, path:formData.executable_path, working_dir:formData.working_dir, input_mode:formData.input_mode, output_mode:formData.output_mode, timeout:Math.min(formData.timeout,30), env_vars:formData.env_vars.reduce((a,e)=>{if(e.key)a[e.key]=e.value;return a;},{}) },
        test_input: testInput
      });
      setTestResult({ success:data.success, output:data.output||'', elapsed:data.elapsed||'', error:data.error||'' });
    } catch(e) {
      // Errori HTTP del backend (auth, sandbox, ecc.) — estrai il detail per mostrarlo all'utente
      const status = e?.response?.status;
      const detail = e?.response?.data?.detail;
      let errMsg;
      if (status === 403) {
        errMsg = typeof detail === 'string' && detail
          ? detail
          : "Permesso negato. Solo gli amministratori possono testare eseguibili, e il percorso deve essere all'interno della directory dati.";
      } else if (status === 400) {
        errMsg = typeof detail === 'string' ? detail : 'Configurazione non valida';
      } else {
        errMsg = e?.message || 'Errore sconosciuto';
      }
      setTestResult({ success:false, error:errMsg });
    }
    finally { setTesting(false); }
  };

  const handleSave = () => onSave({
    name: formData.name, description: formData.description, exec_type: formData.exec_type, connection_type: 'executable',
    executable: { type:formData.executable_type, path:formData.executable_path, working_dir:formData.working_dir, input_mode:formData.input_mode, output_mode:formData.output_mode, output_extension:formData.output_extension||'', timeout:formData.timeout, env_vars:formData.env_vars.reduce((a,e)=>{if(e.key)a[e.key]=e.value;return a;},{}) },
    parameters: formData.parameters, output_mapping: formData.output_mapping
  });

  const execTypeInfo = EXEC_TYPES.find(t => t.id === formData.executable_type);

  return (
    <div className="exc__overlay" onClick={onClose}>
      <div className="exc__modal" onClick={e => e.stopPropagation()}>

        {/* Header */}
        <div className="exc__header">
          <div className="exc__header-left">
            <span className="exc__header-icon">🖥️</span>
            <div>
              <div className="exc__title">Configura Eseguibile Locale</div>
              <div className="exc__subtitle">Script Python / Shell / R / Node.js come connettore Arricchisci</div>
            </div>
          </div>
          <button type="button" className="exc__close" onClick={onClose}>✕</button>
        </div>

        {/* Step bar */}
        <div className="exc__steps">
          {STEPS.map((s,i) => (
            <div key={i} className={`exc__step${step===i?' exc__step--active':''}${step>i?' exc__step--done':''}`} onClick={()=>setStep(i)}>
              <div className="exc__step-num">{step>i?'✓':i+1}</div>
              <div className="exc__step-label">{s}</div>
            </div>
          ))}
        </div>

        {/* Body */}
        <div className="exc__body">

          {/* ── Step 0: Tipo ── */}
          {step === 0 && (
            <div className="exc__section">
              <div className="exc__label">Tipo di elaborazione</div>
              <div className="exc__type-cards">
                {[
                  {v:'enricher',icon:'🔄',label:'Arricchitore',desc:'Esegue per ogni riga del dataset, aggiunge colonne',ex:'Predizione ML, scoring, validazione'},
                  {v:'generator',icon:'📥',label:'Generatore',desc:'Esegue una volta, genera un nuovo dataset',ex:'Scraping, estrazione batch, esportazione'},
                ].map(t => (
                  <div key={t.v} className={`exc__type-card${formData.exec_type===t.v?' exc__type-card--on':''}`} onClick={()=>upd('exec_type',t.v)}>
                    <div className="exc__type-card-icon">{t.icon}</div>
                    <div className="exc__type-card-label">{t.label}</div>
                    <div className="exc__type-card-desc">{t.desc}</div>
                    <div className="exc__type-card-ex">{t.ex}</div>
                  </div>
                ))}
              </div>

              <div className="exc__label" style={{marginTop:8}}>Tipo di eseguibile</div>
              <div className="exc__exec-grid">
                {EXEC_TYPES.map(t => (
                  <div key={t.id} className={`exc__exec-tile${formData.executable_type===t.id?' exc__exec-tile--on':''}`} onClick={()=>upd('executable_type',t.id)}>
                    <span className="exc__exec-tile-icon">{t.icon}</span>
                    <span className="exc__exec-tile-label">{t.label}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* ── Step 1: File ── */}
          {step === 1 && (
            <div className="exc__section">
              <div className="exc__field-group">
                <div className="exc__label">Nome configurazione *</div>
                <input className="exc__input" value={formData.name} onChange={e=>upd('name',e.target.value)} placeholder="Es: Modello ML Prezzi, Script Validazione"/>
              </div>
              <div className="exc__field-group">
                <div className="exc__label">Descrizione</div>
                <textarea className="exc__textarea" value={formData.description} onChange={e=>upd('description',e.target.value)} placeholder="Descrizione opzionale…" rows={2}/>
              </div>
              <div className="exc__field-group">
                <div className="exc__label">{execTypeInfo?.icon} Carica {execTypeInfo?.label}</div>
                <label className="exc__file-drop">
                  <input type="file" accept={execTypeInfo?.ext} onChange={handleFileUpload} style={{display:'none'}}/>
                  <span className="exc__file-icon">{uploadedFile ? '✅' : '📂'}</span>
                  <span>{uploadedFile ? uploadedFile.name : `Clicca o trascina ${execTypeInfo?.ext}`}</span>
                  <span className="exc__file-hint">{execTypeInfo?.ext} — oppure specifica il percorso sotto</span>
                </label>
              </div>
              <div className="exc__field-group">
                <div className="exc__label">Percorso sul server</div>
                <input className="exc__input exc__input--mono" value={formData.executable_path} onChange={e=>upd('executable_path',e.target.value)} placeholder={`/path/to/script${execTypeInfo?.ext}`}/>
              </div>
              <div className="exc__field-group">
                <div className="exc__label">Working Directory (opzionale)</div>
                <input className="exc__input exc__input--mono" value={formData.working_dir} onChange={e=>upd('working_dir',e.target.value)} placeholder="/path/to/workdir"/>
              </div>
            </div>
          )}

          {/* ── Step 2: I/O ── */}
          {step === 2 && (
            <div className="exc__section">
              <div className="exc__io-group">
                <div className="exc__label">📥 Modalità Input</div>
                <div className="exc__io-opts">
                  {INPUT_MODES.map(m => (
                    <label key={m.id} className={`exc__io-opt${formData.input_mode===m.id?' exc__io-opt--on':''}`}>
                      <input type="radio" name="input_mode" checked={formData.input_mode===m.id} onChange={()=>upd('input_mode',m.id)}/>
                      <div><div className="exc__io-opt-label">{m.label}</div><div className="exc__io-opt-desc">{m.desc}</div></div>
                    </label>
                  ))}
                </div>
              </div>

              <div className="exc__io-group">
                <div className="exc__label">📤 Modalità Output</div>
                <div className="exc__io-opts">
                  {OUTPUT_MODES.map(m => (
                    <label key={m.id} className={`exc__io-opt${formData.output_mode===m.id?' exc__io-opt--on':''}`}>
                      <input type="radio" name="output_mode" checked={formData.output_mode===m.id} onChange={()=>upd('output_mode',m.id)}/>
                      <div><div className="exc__io-opt-label">{m.label}</div><div className="exc__io-opt-desc">{m.desc}</div></div>
                    </label>
                  ))}
                </div>
              </div>

              <div className="exc__form-row">
                <div className="exc__field-group">
                  <div className="exc__label">Timeout (sec)</div>
                  <input className="exc__input exc__input--sm" type="number" min="1" max="3600" value={formData.timeout} onChange={e=>upd('timeout',parseInt(e.target.value)||60)}/>
                </div>
                {formData.output_mode === 'file' && (
                  <div className="exc__field-group">
                    <div className="exc__label">Estensione file output</div>
                    <input className="exc__input exc__input--sm exc__input--mono" value={formData.output_extension||''} onChange={e=>upd('output_extension',e.target.value)} placeholder=".csv"/>
                  </div>
                )}
              </div>

              <div className="exc__label">🔐 Variabili d'ambiente</div>
              <div className="exc__params-list">
                {formData.env_vars.map((ev,i) => (
                  <div key={i} className="exc__param-row">
                    <input className="exc__input exc__input--mono" value={ev.key} onChange={e=>updateEnvVar(i,'key',e.target.value)} placeholder="NOME_VAR"/>
                    <span className="exc__eq">=</span>
                    <input className="exc__input" value={ev.value} onChange={e=>updateEnvVar(i,'value',e.target.value)} placeholder="valore"/>
                    <button type="button" className="exc__del-btn" onClick={()=>removeEnvVar(i)}>🗑</button>
                  </div>
                ))}
                <button type="button" className="exc__add-btn" onClick={addEnvVar}>＋ Aggiungi variabile</button>
              </div>
            </div>
          )}

          {/* ── Step 3: Parametri ── */}
          {step === 3 && (
            <div className="exc__section">
              <div className="exc__label">📥 Parametri Input</div>
              <div className="exc__hint">Campi che verranno passati all'eseguibile</div>
              <div className="exc__params-list">
                {formData.parameters.map((p,i) => (
                  <div key={i} className="exc__param-row">
                    <input className="exc__input exc__input--mono" value={p.name} onChange={e=>updateParameter(i,'name',e.target.value)} placeholder="nome_campo"/>
                    <input className="exc__input" value={p.default_value||''} onChange={e=>updateParameter(i,'default_value',e.target.value)} placeholder="default"/>
                    <select className="exc__select" value={p.type} onChange={e=>updateParameter(i,'type',e.target.value)}>
                      <option value="string">Testo</option><option value="number">Numero</option><option value="boolean">Bool</option>
                    </select>
                    <button type="button" className="exc__del-btn" onClick={()=>removeParameter(i)}>🗑</button>
                  </div>
                ))}
                <button type="button" className="exc__add-btn" onClick={addParameter}>＋ Aggiungi parametro</button>
              </div>

              <div className="exc__label" style={{marginTop:16}}>📤 Mapping Output</div>
              <div className="exc__hint">Come estrarre i risultati dall'output</div>
              <div className="exc__params-list">
                {formData.output_mapping.map((o,i) => (
                  <div key={i} className="exc__param-row">
                    <input className="exc__input exc__input--mono" value={o.path} onChange={e=>updateOutputMapping(i,'path',e.target.value)} placeholder="risultato.valore"/>
                    <span className="exc__arr">→</span>
                    <input className="exc__input" value={o.column} onChange={e=>updateOutputMapping(i,'column',e.target.value)} placeholder="nome_colonna"/>
                    <select className="exc__select" value={o.type} onChange={e=>updateOutputMapping(i,'type',e.target.value)}>
                      <option value="string">Testo</option><option value="number">Numero</option><option value="boolean">Bool</option>
                    </select>
                    <button type="button" className="exc__del-btn" onClick={()=>removeOutputMapping(i)}>🗑</button>
                  </div>
                ))}
                <button type="button" className="exc__add-btn" onClick={addOutputMapping}>＋ Aggiungi output</button>
              </div>
            </div>
          )}

          {/* ── Step 4: Test ── */}
          {step === 4 && (
            <div className="exc__section">
              <div className="exc__summary">
                {[
                  ['Nome', formData.name],
                  ['Tipo', formData.exec_type==='enricher'?'🔄 Arricchitore':'📥 Generatore'],
                  ['Eseguibile', `${execTypeInfo?.icon} ${formData.executable_path}`],
                  ['Input', INPUT_MODES.find(m=>m.id===formData.input_mode)?.label],
                  ['Output', OUTPUT_MODES.find(m=>m.id===formData.output_mode)?.label],
                  ['Parametri', String(formData.parameters.length)],
                  ['Campi output', String(formData.output_mapping.length)],
                ].map(([k,v]) => (
                  <div key={k} className="exc__summary-row">
                    <span className="exc__summary-key">{k}</span>
                    <span className="exc__summary-val">{v}</span>
                  </div>
                ))}
              </div>

              <div className="exc__label">Input di test (JSON)</div>
              <textarea className="exc__textarea exc__textarea--mono" value={testInput} onChange={e=>setTestInput(e.target.value)} placeholder='{"campo1": "valore1"}' rows={4}/>

              {!isAdmin && (
                <div className="exc__test-result exc__test-result--err" style={{marginTop:8}}>
                  <div className="exc__test-result-title">
                    ⚠️ Solo gli amministratori possono eseguire il test. L'eseguibile può essere comunque salvato e usato in produzione.
                  </div>
                </div>
              )}

              <button
                type="button"
                className="exc__test-btn"
                onClick={handleTest}
                disabled={testing || !isAdmin}
                title={!isAdmin ? 'Funzione riservata agli amministratori' : ''}
              >
                {testing ? '⏳ Esecuzione in corso…' : '▶️ Esegui Test'}
              </button>

              {testResult && (
                <div className={`exc__test-result${testResult.success?' exc__test-result--ok':' exc__test-result--err'}`}>
                  {testResult.success ? (
                    <>
                      <div className="exc__test-result-title">✅ Esecuzione riuscita{testResult.elapsed&&` (${testResult.elapsed})`}</div>
                      <pre className="exc__test-output">{testResult.output}</pre>
                    </>
                  ) : (
                    <div className="exc__test-result-title">❌ Errore: {testResult.error}</div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="exc__footer">
          <button type="button" className="exc__btn exc__btn--ghost" onClick={step===0?onClose:()=>setStep(s=>s-1)}>
            {step===0?'Annulla':'← Indietro'}
          </button>
          {step < 4 ? (
            <button type="button" className="exc__btn exc__btn--primary" onClick={()=>setStep(s=>s+1)} disabled={step===1&&!formData.name}>
              Avanti →
            </button>
          ) : (
            <button type="button" className="exc__btn exc__btn--ok" onClick={handleSave} disabled={!formData.name||!formData.executable_path}>
              💾 Salva Eseguibile
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export default ExecutableConfigModal;
