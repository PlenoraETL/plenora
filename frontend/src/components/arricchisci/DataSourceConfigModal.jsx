/**
 * DataSourceConfigModal.jsx — Configurazione Data Source
 * Wizard 4 step: Tipo → Credenziali → Percorso → Test
 * Redesign Plenora design system
 */
import React, { useState } from 'react';
import Wizard from '../common/Wizard';
import './DataSourceConfigModal.css';
import { pressable } from '../../utils/pressable';

const DATASOURCE_TYPES = [
  { id:'sharepoint_365',   label:'SharePoint 365',        icon:'📊', authType:'OAuth2',  fields:['tenant_id','client_id','client_secret','site_url'] },
  { id:'sharepoint_onprem',label:'SharePoint On-Premise', icon:'🏢', authType:'NTLM',    fields:['server_url','domain','username','password'] },
  { id:'ftp',              label:'FTP',                   icon:'📂', authType:'Basic',   fields:['host','port','username','password'] },
  { id:'sftp',             label:'SFTP',                  icon:'🔐', authType:'Key',     fields:['host','port','username','password','private_key'] },
  { id:'s3',               label:'Amazon S3',             icon:'☁️', authType:'AWS',     fields:['bucket','region','access_key','secret_key'],  status:'future' },
  { id:'azure_blob',       label:'Azure Blob',            icon:'🔷', authType:'SAS',     fields:['account_name','container','sas_token'],       status:'future' },
  { id:'google_drive',     label:'Google Drive',          icon:'📁', authType:'OAuth2',  fields:['client_id','client_secret','folder_id'],      status:'future' },
];

const FIELD_LABELS = {
  tenant_id:    { label:'Tenant ID',             placeholder:'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx', type:'text' },
  client_id:    { label:'Client ID',             placeholder:'App Registration Client ID',           type:'text' },
  client_secret:{ label:'Client Secret',         placeholder:'••••••••',                             type:'password' },
  site_url:     { label:'Site URL',              placeholder:'https://contoso.sharepoint.com/sites/MySite', type:'text' },
  server_url:   { label:'Server URL',            placeholder:'https://sharepoint.azienda.local',     type:'text' },
  domain:       { label:'Dominio',               placeholder:'AZIENDA',                              type:'text' },
  username:     { label:'Username',              placeholder:'utente',                               type:'text' },
  password:     { label:'Password',              placeholder:'••••••••',                             type:'password' },
  host:         { label:'Host',                  placeholder:'ftp.esempio.com',                      type:'text' },
  port:         { label:'Porta',                 placeholder:'21',                                   type:'number' },
  private_key:  { label:'Chiave Privata (opt.)', placeholder:'Incolla la chiave SSH…',               type:'textarea' },
  bucket:       { label:'Bucket Name',           placeholder:'my-bucket',                            type:'text' },
  region:       { label:'Region',                placeholder:'eu-west-1',                            type:'text' },
  access_key:   { label:'Access Key ID',         placeholder:'AKIA…',                               type:'text' },
  secret_key:   { label:'Secret Access Key',     placeholder:'••••••••',                             type:'password' },
  account_name: { label:'Account Name',          placeholder:'mystorageaccount',                     type:'text' },
  container:    { label:'Container',             placeholder:'mycontainer',                          type:'text' },
  sas_token:    { label:'SAS Token',             placeholder:'?sv=2021-06-08&ss=…',                 type:'password' },
  folder_id:    { label:'Folder ID (opt.)',       placeholder:'1ABC…xyz',                            type:'text' },
};

// Credenziali facoltative (le altre del tipo sono obbligatorie). Per SFTP
// basta la password O la chiave privata.
const OPTIONAL_CREDENTIALS = new Set(['port', 'domain', 'private_key', 'folder_id']);
const ONE_OF_CREDENTIALS = { sftp: [['password', 'private_key']] };

/** Messaggio della prima credenziale mancante, o null (R6.2). */
function missingCredentials(source, credentials = {}) {
  if (!source) return 'Scegli il tipo di sorgente.';
  const filled = (k) => String(credentials[k] ?? '').trim() !== '';
  const groups = ONE_OF_CREDENTIALS[source.id] || [];
  const inGroup = new Set(groups.flat());
  const missing = source.fields.find(f => !OPTIONAL_CREDENTIALS.has(f) && !inGroup.has(f) && !filled(f));
  if (missing) return `Compila «${(FIELD_LABELS[missing] || { label: missing }).label}».`;
  const emptyGroup = groups.find(g => !g.some(filled));
  if (emptyGroup) return `Compila «${emptyGroup.map(f => FIELD_LABELS[f]?.label || f).join('» o «')}».`;
  return null;
}

const STEPS = ['Tipo', 'Credenziali', 'Percorso', 'Test'];
const FILE_EXTS = ['.csv','.xlsx','.xls','.json','.xml','.txt'];

function DataSourceConfigModal({ onClose, onSave }) {
  const [step, setStep] = useState(0);
  const [formData, setFormData] = useState({
    name: '', description: '', source_type: '',
    credentials: {}, path_pattern: '',
    file_types: ['.csv','.xlsx'], recursive: false, schedule: null,
  });

  const upd = (k, v) => setFormData(prev => ({...prev, [k]:v}));
  const updCred = (k, v) => setFormData(prev => ({...prev, credentials:{...prev.credentials,[k]:v}}));
  const selectedSource = DATASOURCE_TYPES.find(s => s.id === formData.source_type);

  const handleSave = () => onSave({
    name: formData.name, description: formData.description,
    connection_type: 'datasource', api_type: 'generator',
    datasource: { type:formData.source_type, credentials:formData.credentials, path_pattern:formData.path_pattern, file_types:formData.file_types, recursive:formData.recursive, schedule:formData.schedule },
  });

  const steps = [
    {
      label: STEPS[0],
      canAdvance: !!(formData.name && formData.source_type),
      content: (
            <div className="dsm__section">
              <div className="dsm__field-group">
                <div className="dsm__label">Nome connessione *</div>
                <input className="dsm__input" value={formData.name} onChange={e=>upd('name',e.target.value)} placeholder="Es: SharePoint Aziendale, FTP Report"/>
              </div>
              <div className="dsm__field-group">
                <div className="dsm__label">Descrizione</div>
                <textarea className="dsm__textarea" value={formData.description} onChange={e=>upd('description',e.target.value)} placeholder="Descrizione opzionale…" rows={2}/>
              </div>
              <div className="dsm__label">Tipo di sorgente</div>
              <div className="dsm__types-grid">
                {DATASOURCE_TYPES.map(src => (
                  <div key={src.id}
                    className={`dsm__type-tile${formData.source_type===src.id?' dsm__type-tile--on':''}${src.status==='future'?' dsm__type-tile--future':''}`}
                    {...pressable(() => setFormData(prev => ((src.status || prev.source_type === src.id) ? prev : { ...prev, source_type: src.id, credentials: {} })))}>
                    <span className="dsm__type-icon">{src.icon}</span>
                    <span className="dsm__type-label">{src.label}</span>
                    <span className="dsm__type-auth">{src.authType}</span>
                    {src.status === 'future' && <span className="dsm__type-soon">Presto</span>}
                  </div>
                ))}
              </div>
            </div>
          ),
    },
    {
      label: STEPS[1],
      // R6.2: tutte le credenziali obbligatorie del tipo (non "una qualsiasi").
      validate: () => missingCredentials(selectedSource, formData.credentials),
      content: selectedSource ? (
            <div className="dsm__section">
              <div className="dsm__cred-header">
                <span className="dsm__cred-icon">{selectedSource.icon}</span>
                <div>
                  <div className="dsm__cred-title">{selectedSource.label}</div>
                  <div className="dsm__cred-auth">Autenticazione: <span>{selectedSource.authType}</span></div>
                </div>
              </div>
              {selectedSource.fields.map(field => {
                const fi = FIELD_LABELS[field] || {label:field, type:'text', placeholder:''};
                return (
                  <div key={field} className="dsm__field-group">
                    <div className="dsm__label">{fi.label}</div>
                    {fi.type === 'textarea' ? (
                      <textarea className="dsm__textarea dsm__textarea--mono" value={formData.credentials[field]||''} onChange={e=>updCred(field,e.target.value)} placeholder={fi.placeholder} rows={4}/>
                    ) : (
                      <input className={`dsm__input${fi.type==='password'?' dsm__input--pw':''}${field==='host'||field==='site_url'||field==='server_url'?' dsm__input--mono':''}`} type={fi.type} value={formData.credentials[field]||''} onChange={e=>updCred(field,e.target.value)} placeholder={fi.placeholder}/>
                    )}
                  </div>
                );
              })}
            </div>
          ) : null,
    },
    {
      label: STEPS[2],
      canAdvance: Boolean(formData.path_pattern && formData.path_pattern.trim()),
      content: (
            <div className="dsm__section">
              <div className="dsm__field-group">
                <div className="dsm__label">Percorso o Pattern *</div>
                <input className="dsm__input dsm__input--mono" value={formData.path_pattern} onChange={e=>upd('path_pattern',e.target.value)} placeholder="/Documenti/Reports/*.csv"/>
                <div className="dsm__hint">💡 Usa * come wildcard: /dati/*.xlsx · /reports/2024-*.csv</div>
              </div>

              <div className="dsm__field-group">
                <div className="dsm__label">Tipi di file accettati</div>
                <div className="dsm__ext-grid">
                  {FILE_EXTS.map(ext => {
                    const on = formData.file_types.includes(ext);
                    return (
                      <label key={ext} className={`dsm__ext-pill${on?' dsm__ext-pill--on':''}`}>
                        <input type="checkbox" checked={on} onChange={e => {
                          if (e.target.checked) upd('file_types',[...formData.file_types,ext]);
                          else upd('file_types', formData.file_types.filter(t=>t!==ext));
                        }}/>
                        {ext}
                      </label>
                    );
                  })}
                </div>
              </div>

              <label className="dsm__checkbox">
                <input type="checkbox" checked={formData.recursive} onChange={e=>upd('recursive',e.target.checked)}/>
                <span>Cerca nelle sottocartelle (ricorsivo)</span>
              </label>
            </div>
          ),
    },
    {
      label: STEPS[3],
      content: (
            <div className="dsm__section">
              <div className="dsm__summary">
                {[
                  ['Nome',      formData.name],
                  ['Tipo',      `${selectedSource?.icon} ${selectedSource?.label}`],
                  ['Auth',      selectedSource?.authType],
                  ['Percorso',  formData.path_pattern],
                  ['Tipi file', formData.file_types.join(', ')],
                  ['Ricorsivo', formData.recursive ? 'Sì' : 'No'],
                ].map(([k,v]) => (
                  <div key={k} className="dsm__summary-row">
                    <span className="dsm__summary-key">{k}</span>
                    <span className="dsm__summary-val">{v}</span>
                  </div>
                ))}
              </div>

              {/* Il backend testa solo Data Source gia' salvati
                  (POST /datasources/{id}/test). Prima qui c'era un test
                  simulato che rispondeva sempre "riuscita" (audit P2). */}
              <div className="dsm__info-box">
                🔍 Dopo il salvataggio puoi verificare la connessione con <strong>Test</strong> nella pagina <strong>Data Source</strong>.
              </div>

              <div className="dsm__info-box">
                💡 <strong>Nota:</strong> I Data Source funzionano come <em>Generatori</em>. Quando eseguiti, scaricano i file dal percorso specificato e li rendono disponibili come tabelle.
              </div>
            </div>
          ),
    },
  ];

  return (
    <Wizard
      isOpen
      onClose={onClose}
      icon="📁"
      title="Configura Data Source"
      subtitle="Collega sorgente file remota — SharePoint · FTP · SFTP · S3"
      step={step}
      onStepChange={setStep}
      steps={steps}
      onComplete={handleSave}
      completeLabel="💾 Salva Data Source"
      completeDisabled={!formData.name || !formData.source_type || !formData.path_pattern}
    />
  );
}

export default DataSourceConfigModal;
