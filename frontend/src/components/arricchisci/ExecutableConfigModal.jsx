import React, { useState } from 'react';

// Tipi di eseguibile supportati
const EXEC_TYPES = [
  { id: 'python', label: 'Script Python', icon: '🐍', ext: '.py', cmd: 'python' },
  { id: 'executable', label: 'Eseguibile', icon: '⚙️', ext: '.exe', cmd: '' },
  { id: 'shell', label: 'Script Shell', icon: '📜', ext: '.sh', cmd: 'bash' },
  { id: 'node', label: 'Script Node.js', icon: '🟢', ext: '.js', cmd: 'node' }
];

// Modalità di input
const INPUT_MODES = [
  { id: 'stdin', label: 'Standard Input (stdin)', desc: 'Dati passati via pipe' },
  { id: 'file', label: 'File temporaneo', desc: 'Dati scritti su file, path passato come argomento' },
  { id: 'args', label: 'Argomenti CLI', desc: 'Ogni campo come argomento separato' },
  { id: 'json_arg', label: 'JSON come argomento', desc: 'Tutti i dati come singolo JSON' }
];

// Modalità di output
const OUTPUT_MODES = [
  { id: 'stdout', label: 'Standard Output (stdout)', desc: 'Output letto da stdout' },
  { id: 'file', label: 'File di output', desc: 'Output scritto su file specificato' },
  { id: 'json_stdout', label: 'JSON da stdout', desc: 'Output JSON parsato automaticamente' }
];

function ExecutableConfigModal({ onClose, onSave }) {
  const [step, setStep] = useState(0);
  const [formData, setFormData] = useState({
    name: '',
    description: '',
    exec_type: 'enricher', // enricher o generator
    executable_type: 'python',
    executable_path: '',
    working_dir: '',
    input_mode: 'json_arg',
    output_mode: 'json_stdout',
    timeout: 60,
    parameters: [],
    output_mapping: [],
    env_vars: []
  });
  
  const [uploadedFile, setUploadedFile] = useState(null);
  const [testResult, setTestResult] = useState(null);
  const [testing, setTesting] = useState(false);
  const [testInput, setTestInput] = useState('{}');

  // Handler per upload file
  const handleFileUpload = (e) => {
    const file = e.target.files[0];
    if (file) {
      setUploadedFile(file);
      setFormData(prev => ({
        ...prev,
        name: prev.name || file.name.replace(/\.[^/.]+$/, ''),
        executable_path: file.name
      }));
    }
  };

  // Aggiungi parametro
  const addParameter = () => {
    setFormData(prev => ({
      ...prev,
      parameters: [...prev.parameters, { name: '', type: 'string', required: true, description: '' }]
    }));
  };

  // Aggiorna parametro
  const updateParameter = (index, field, value) => {
    setFormData(prev => ({
      ...prev,
      parameters: prev.parameters.map((p, i) => i === index ? { ...p, [field]: value } : p)
    }));
  };

  // Rimuovi parametro
  const removeParameter = (index) => {
    setFormData(prev => ({
      ...prev,
      parameters: prev.parameters.filter((_, i) => i !== index)
    }));
  };

  // Aggiungi output field
  const addOutputField = () => {
    setFormData(prev => ({
      ...prev,
      output_mapping: [...prev.output_mapping, { path: '', column: '', type: 'string' }]
    }));
  };

  // Aggiorna output field
  const updateOutputField = (index, field, value) => {
    setFormData(prev => ({
      ...prev,
      output_mapping: prev.output_mapping.map((o, i) => i === index ? { ...o, [field]: value } : o)
    }));
  };

  // Rimuovi output field
  const removeOutputField = (index) => {
    setFormData(prev => ({
      ...prev,
      output_mapping: prev.output_mapping.filter((_, i) => i !== index)
    }));
  };

  // Aggiungi env var
  const addEnvVar = () => {
    setFormData(prev => ({
      ...prev,
      env_vars: [...prev.env_vars, { key: '', value: '' }]
    }));
  };

  // Test esecuzione
  const handleTest = async () => {
    setTesting(true);
    setTestResult(null);
    
    try {
      // Simula test - in produzione chiamerebbe il backend
      await new Promise(resolve => setTimeout(resolve, 1500));
      
      // Mock result
      setTestResult({
        success: true,
        output: '{"result": 123, "confidence": 0.95}',
        execution_time: 0.45,
        available_fields: [
          { path: 'result', name: 'result', type: 'number' },
          { path: 'confidence', name: 'confidence', type: 'number' }
        ]
      });
      
      // Auto-popola output mapping
      if (formData.output_mapping.length === 0) {
        setFormData(prev => ({
          ...prev,
          output_mapping: [
            { path: 'result', column: 'result', type: 'number' },
            { path: 'confidence', column: 'confidence', type: 'number' }
          ]
        }));
      }
    } catch (error) {
      setTestResult({
        success: false,
        error: error.message
      });
    } finally {
      setTesting(false);
    }
  };

  // Salva configurazione
  const handleSave = () => {
    onSave({
      name: formData.name,
      description: formData.description,
      exec_type: formData.exec_type,
      connection_type: 'executable',
      executable: {
        type: formData.executable_type,
        path: formData.executable_path,
        working_dir: formData.working_dir,
        input_mode: formData.input_mode,
        output_mode: formData.output_mode,
        timeout: formData.timeout,
        env_vars: formData.env_vars.reduce((acc, ev) => {
          if (ev.key) acc[ev.key] = ev.value;
          return acc;
        }, {})
      },
      parameters: formData.parameters,
      output_mapping: formData.output_mapping
    });
  };

  const execTypeInfo = EXEC_TYPES.find(t => t.id === formData.executable_type);

  return (
    <div className="exec-modal__overlay" onClick={onClose}>
      <div className="exec-modal" onClick={e => e.stopPropagation()}>
        <div className="exec-modal__header">
          <h2 className="exec-modal__title">🖥️ Configura Eseguibile Locale</h2>
          <button onClick={onClose} className="exec-modal__close">×</button>
        </div>

        <div className="exec-modal__progress">
          {['Tipo', 'File', 'I/O', 'Parametri', 'Test'].map((s, idx) => (
            <div 
              key={idx}
              className={`exec-modal__step ${step === idx ? 'exec-modal__step--active' : ''} ${step > idx ? 'exec-modal__step--done' : ''}`}
              onClick={() => setStep(idx)}
            >
              <span className="exec-modal__step-num">{idx + 1}</span>
              <span>{s}</span>
            </div>
          ))}
        </div>

        <div className="exec-modal__content">
          {/* Step 0: Tipo */}
          {step === 0 && (
            <div className="exec-modal__section">
              <h3 className="exec-modal__section-title">Che tipo di elaborazione vuoi configurare?</h3>
              
              <div className="exec-modal__type-cards">
                <div 
                  className={`exec-modal__type-card ${formData.exec_type === 'enricher' ? 'exec-modal__type-card--selected' : ''}`}
                  onClick={() => setFormData(prev => ({ ...prev, exec_type: 'enricher' }))}
                >
                  <span className="exec-modal__type-icon">🔄</span>
                  <h4>Arricchitore</h4>
                  <p>Esegue per ogni riga del dataset e aggiunge colonne con i risultati</p>
                  <small>Es: Predizione ML, calcolo score, validazione</small>
                </div>
                <div 
                  className={`exec-modal__type-card ${formData.exec_type === 'generator' ? 'exec-modal__type-card--selected' : ''}`}
                  onClick={() => setFormData(prev => ({ ...prev, exec_type: 'generator' }))}
                >
                  <span className="exec-modal__type-icon">📥</span>
                  <h4>Generatore</h4>
                  <p>Esegue una volta e genera un nuovo dataset dalla risposta</p>
                  <small>Es: Scraping, estrazione dati, batch processing</small>
                </div>
              </div>

              <div className="exec-modal__exec-types">
                <label className="exec-modal__label">Tipo di eseguibile</label>
                <div className="exec-modal__exec-grid">
                  {EXEC_TYPES.map(et => (
                    <div
                      key={et.id}
                      className={`exec-modal__exec-option ${formData.executable_type === et.id ? 'exec-modal__exec-option--selected' : ''}`}
                      onClick={() => setFormData(prev => ({ ...prev, executable_type: et.id }))}
                    >
                      <span className="exec-modal__exec-icon">{et.icon}</span>
                      <span>{et.label}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* Step 1: File */}
          {step === 1 && (
            <div className="exec-modal__section">
              <h3 className="exec-modal__section-title">Configura l'eseguibile {execTypeInfo?.icon}</h3>
              
              <div className="exec-modal__form-group">
                <label className="exec-modal__label">Nome *</label>
                <input
                  type="text"
                  value={formData.name}
                  onChange={e => setFormData(prev => ({ ...prev, name: e.target.value }))}
                  placeholder="Es: Modello Stima Valore UniGe"
                  className="exec-modal__input"
                />
              </div>

              <div className="exec-modal__form-group">
                <label className="exec-modal__label">Descrizione</label>
                <textarea
                  value={formData.description}
                  onChange={e => setFormData(prev => ({ ...prev, description: e.target.value }))}
                  placeholder="Descrizione opzionale..."
                  className="exec-modal__textarea"
                  rows={2}
                />
              </div>

              <div className="exec-modal__form-group">
                <label className="exec-modal__label">File eseguibile *</label>
                <div className="exec-modal__file-upload">
                  <input
                    type="file"
                    accept={execTypeInfo?.ext || '*'}
                    onChange={handleFileUpload}
                    className="exec-modal__file-input"
                    id="exec-file"
                  />
                  <label htmlFor="exec-file" className="exec-modal__file-label">
                    {uploadedFile ? (
                      <>📄 {uploadedFile.name}</>
                    ) : (
                      <>📁 Clicca per caricare o trascina qui</>
                    )}
                  </label>
                </div>
                <p className="exec-modal__hint">Oppure specifica il path completo:</p>
                <input
                  type="text"
                  value={formData.executable_path}
                  onChange={e => setFormData(prev => ({ ...prev, executable_path: e.target.value }))}
                  placeholder={`Es: /opt/models/predict${execTypeInfo?.ext || ''}`}
                  className="exec-modal__input exec-modal__input--mono"
                />
              </div>

              <div className="exec-modal__form-group">
                <label className="exec-modal__label">Working Directory (opzionale)</label>
                <input
                  type="text"
                  value={formData.working_dir}
                  onChange={e => setFormData(prev => ({ ...prev, working_dir: e.target.value }))}
                  placeholder="Es: /opt/models/"
                  className="exec-modal__input exec-modal__input--mono"
                />
              </div>

              <div className="exec-modal__form-group">
                <label className="exec-modal__label">Timeout (secondi)</label>
                <input
                  type="number"
                  value={formData.timeout}
                  onChange={e => setFormData(prev => ({ ...prev, timeout: parseInt(e.target.value) || 60 }))}
                  min={5}
                  max={3600}
                  className="exec-modal__input exec-modal__input--small"
                />
              </div>
            </div>
          )}

          {/* Step 2: I/O */}
          {step === 2 && (
            <div className="exec-modal__section">
              <h3 className="exec-modal__section-title">Configura Input/Output</h3>
              
              <div className="exec-modal__io-section">
                <h4 className="exec-modal__subsection-title">📥 Come passare i dati in input?</h4>
                <div className="exec-modal__io-options">
                  {INPUT_MODES.map(mode => (
                    <div
                      key={mode.id}
                      className={`exec-modal__io-option ${formData.input_mode === mode.id ? 'exec-modal__io-option--selected' : ''}`}
                      onClick={() => setFormData(prev => ({ ...prev, input_mode: mode.id }))}
                    >
                      <strong>{mode.label}</strong>
                      <small>{mode.desc}</small>
                    </div>
                  ))}
                </div>
              </div>

              <div className="exec-modal__io-section">
                <h4 className="exec-modal__subsection-title">📤 Come leggere l'output?</h4>
                <div className="exec-modal__io-options">
                  {OUTPUT_MODES.map(mode => (
                    <div
                      key={mode.id}
                      className={`exec-modal__io-option ${formData.output_mode === mode.id ? 'exec-modal__io-option--selected' : ''}`}
                      onClick={() => setFormData(prev => ({ ...prev, output_mode: mode.id }))}
                    >
                      <strong>{mode.label}</strong>
                      <small>{mode.desc}</small>
                    </div>
                  ))}
                </div>
              </div>

              <div className="exec-modal__env-section">
                <h4 className="exec-modal__subsection-title">🔧 Variabili d'ambiente (opzionale)</h4>
                <div className="exec-modal__env-list">
                  {formData.env_vars.map((ev, idx) => (
                    <div key={idx} className="exec-modal__env-row">
                      <input
                        type="text"
                        value={ev.key}
                        onChange={e => {
                          const newVars = [...formData.env_vars];
                          newVars[idx].key = e.target.value;
                          setFormData(prev => ({ ...prev, env_vars: newVars }));
                        }}
                        placeholder="NOME"
                        className="exec-modal__input exec-modal__input--small"
                      />
                      <span>=</span>
                      <input
                        type="text"
                        value={ev.value}
                        onChange={e => {
                          const newVars = [...formData.env_vars];
                          newVars[idx].value = e.target.value;
                          setFormData(prev => ({ ...prev, env_vars: newVars }));
                        }}
                        placeholder="valore"
                        className="exec-modal__input"
                      />
                      <button onClick={() => setFormData(prev => ({ 
                        ...prev, 
                        env_vars: prev.env_vars.filter((_, i) => i !== idx) 
                      }))} className="exec-modal__remove-btn">🗑️</button>
                    </div>
                  ))}
                  <button onClick={addEnvVar} className="exec-modal__add-btn">+ Aggiungi variabile</button>
                </div>
              </div>
            </div>
          )}

          {/* Step 3: Parametri */}
          {step === 3 && (
            <div className="exec-modal__section">
              <h3 className="exec-modal__section-title">Definisci i parametri</h3>
              
              <div className="exec-modal__params-section">
                <h4 className="exec-modal__subsection-title">📥 Parametri di input</h4>
                <p className="exec-modal__hint">
                  {formData.exec_type === 'enricher' 
                    ? 'Questi campi verranno presi dalle colonne del dataset per ogni riga'
                    : 'Questi sono i parametri fissi per l\'esecuzione'}
                </p>
                
                <div className="exec-modal__params-list">
                  {formData.parameters.map((param, idx) => (
                    <div key={idx} className="exec-modal__param-row">
                      <input
                        type="text"
                        value={param.name}
                        onChange={e => updateParameter(idx, 'name', e.target.value)}
                        placeholder="nome_parametro"
                        className="exec-modal__input"
                      />
                      <select
                        value={param.type}
                        onChange={e => updateParameter(idx, 'type', e.target.value)}
                        className="exec-modal__select"
                      >
                        <option value="string">Testo</option>
                        <option value="number">Numero</option>
                        <option value="boolean">Booleano</option>
                        <option value="json">JSON</option>
                      </select>
                      <label className="exec-modal__checkbox">
                        <input
                          type="checkbox"
                          checked={param.required}
                          onChange={e => updateParameter(idx, 'required', e.target.checked)}
                        />
                        Obbligatorio
                      </label>
                      <button onClick={() => removeParameter(idx)} className="exec-modal__remove-btn">🗑️</button>
                    </div>
                  ))}
                  <button onClick={addParameter} className="exec-modal__add-btn">+ Aggiungi parametro</button>
                </div>
              </div>

              <div className="exec-modal__output-section">
                <h4 className="exec-modal__subsection-title">📤 Campi di output</h4>
                <p className="exec-modal__hint">Definisci come mappare l'output in colonne del dataset</p>
                
                <div className="exec-modal__params-list">
                  {formData.output_mapping.map((out, idx) => (
                    <div key={idx} className="exec-modal__param-row">
                      <input
                        type="text"
                        value={out.path}
                        onChange={e => updateOutputField(idx, 'path', e.target.value)}
                        placeholder="path.nel.json"
                        className="exec-modal__input"
                      />
                      <span className="exec-modal__arrow">→</span>
                      <input
                        type="text"
                        value={out.column}
                        onChange={e => updateOutputField(idx, 'column', e.target.value)}
                        placeholder="nome_colonna"
                        className="exec-modal__input"
                      />
                      <select
                        value={out.type}
                        onChange={e => updateOutputField(idx, 'type', e.target.value)}
                        className="exec-modal__select"
                      >
                        <option value="string">Testo</option>
                        <option value="number">Numero</option>
                        <option value="boolean">Booleano</option>
                        <option value="json">JSON</option>
                        <option value="geometry">Geometria</option>
                      </select>
                      <button onClick={() => removeOutputField(idx)} className="exec-modal__remove-btn">🗑️</button>
                    </div>
                  ))}
                  <button onClick={addOutputField} className="exec-modal__add-btn">+ Aggiungi campo output</button>
                </div>
              </div>
            </div>
          )}

          {/* Step 4: Test */}
          {step === 4 && (
            <div className="exec-modal__section">
              <h3 className="exec-modal__section-title">Testa l'eseguibile</h3>
              
              <div className="exec-modal__test-section">
                <h4 className="exec-modal__subsection-title">📝 Dati di test (JSON)</h4>
                <textarea
                  value={testInput}
                  onChange={e => setTestInput(e.target.value)}
                  placeholder='{"parametro1": "valore1", "parametro2": 123}'
                  className="exec-modal__textarea exec-modal__textarea--mono"
                  rows={4}
                />
                
                <button 
                  onClick={handleTest} 
                  disabled={testing || !formData.executable_path}
                  className="exec-modal__test-btn"
                >
                  {testing ? '⏳ Esecuzione in corso...' : '🚀 Esegui Test'}
                </button>
              </div>

              {testResult && (
                <div className={`exec-modal__result ${testResult.success ? 'exec-modal__result--success' : 'exec-modal__result--error'}`}>
                  <h4>{testResult.success ? '✅ Test riuscito!' : '❌ Test fallito'}</h4>
                  {testResult.success ? (
                    <>
                      <p><strong>Tempo esecuzione:</strong> {testResult.execution_time}s</p>
                      <div className="exec-modal__result-output">
                        <strong>Output:</strong>
                        <pre>{testResult.output}</pre>
                      </div>
                      {testResult.available_fields?.length > 0 && (
                        <div className="exec-modal__result-fields">
                          <strong>Campi rilevati:</strong>
                          <div className="exec-modal__fields-list">
                            {testResult.available_fields.map((f, i) => (
                              <span key={i} className="exec-modal__field-tag">{f.name} ({f.type})</span>
                            ))}
                          </div>
                        </div>
                      )}
                    </>
                  ) : (
                    <p className="exec-modal__error-msg">{testResult.error}</p>
                  )}
                </div>
              )}

              <div className="exec-modal__summary">
                <h4 className="exec-modal__subsection-title">📋 Riepilogo configurazione</h4>
                <div className="exec-modal__summary-grid">
                  <div><strong>Nome:</strong> {formData.name || '-'}</div>
                  <div><strong>Tipo:</strong> {formData.exec_type === 'enricher' ? 'Arricchitore' : 'Generatore'}</div>
                  <div><strong>Eseguibile:</strong> {execTypeInfo?.label}</div>
                  <div><strong>Path:</strong> {formData.executable_path || '-'}</div>
                  <div><strong>Input:</strong> {INPUT_MODES.find(m => m.id === formData.input_mode)?.label}</div>
                  <div><strong>Output:</strong> {OUTPUT_MODES.find(m => m.id === formData.output_mode)?.label}</div>
                  <div><strong>Parametri:</strong> {formData.parameters.length}</div>
                  <div><strong>Campi output:</strong> {formData.output_mapping.length}</div>
                </div>
              </div>
            </div>
          )}
        </div>

        <div className="exec-modal__footer">
          <button 
            onClick={step === 0 ? onClose : () => setStep(s => s - 1)} 
            className="exec-modal__btn exec-modal__btn--secondary"
          >
            {step === 0 ? 'Annulla' : '← Indietro'}
          </button>
          
          {step < 4 ? (
            <button 
              onClick={() => setStep(s => s + 1)}
              disabled={step === 1 && !formData.name}
              className="exec-modal__btn exec-modal__btn--primary"
            >
              Avanti →
            </button>
          ) : (
            <button 
              onClick={handleSave}
              disabled={!formData.name || !formData.executable_path}
              className="exec-modal__btn exec-modal__btn--success"
            >
              💾 Salva Eseguibile
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export default ExecutableConfigModal;
