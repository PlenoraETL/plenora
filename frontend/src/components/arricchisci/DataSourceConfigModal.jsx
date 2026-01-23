import React, { useState } from 'react';

// Tipi di Data Source supportati
const DATASOURCE_TYPES = [
  { 
    id: 'sharepoint_365', 
    label: 'SharePoint 365', 
    icon: '📊',
    authType: 'oauth2',
    fields: ['tenant_id', 'client_id', 'client_secret', 'site_url']
  },
  { 
    id: 'sharepoint_onprem', 
    label: 'SharePoint On-Premise', 
    icon: '🏢',
    authType: 'ntlm',
    fields: ['server_url', 'domain', 'username', 'password']
  },
  { 
    id: 'ftp', 
    label: 'FTP', 
    icon: '📂',
    authType: 'basic',
    fields: ['host', 'port', 'username', 'password']
  },
  { 
    id: 'sftp', 
    label: 'SFTP', 
    icon: '🔐',
    authType: 'key',
    fields: ['host', 'port', 'username', 'password', 'private_key']
  },
  { 
    id: 's3', 
    label: 'Amazon S3', 
    icon: '☁️',
    authType: 'aws',
    fields: ['bucket', 'region', 'access_key', 'secret_key'],
    status: 'future'
  },
  { 
    id: 'azure_blob', 
    label: 'Azure Blob', 
    icon: '🔷',
    authType: 'sas',
    fields: ['account_name', 'container', 'sas_token'],
    status: 'future'
  },
  { 
    id: 'google_drive', 
    label: 'Google Drive', 
    icon: '📁',
    authType: 'oauth2',
    fields: ['client_id', 'client_secret', 'folder_id'],
    status: 'future'
  }
];

// Labels per i campi
const FIELD_LABELS = {
  tenant_id: { label: 'Tenant ID', placeholder: 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx', type: 'text' },
  client_id: { label: 'Client ID', placeholder: 'App Registration Client ID', type: 'text' },
  client_secret: { label: 'Client Secret', placeholder: '••••••••', type: 'password' },
  site_url: { label: 'Site URL', placeholder: 'https://contoso.sharepoint.com/sites/MySite', type: 'text' },
  server_url: { label: 'Server URL', placeholder: 'https://sharepoint.azienda.local', type: 'text' },
  domain: { label: 'Dominio', placeholder: 'AZIENDA', type: 'text' },
  username: { label: 'Username', placeholder: 'utente', type: 'text' },
  password: { label: 'Password', placeholder: '••••••••', type: 'password' },
  host: { label: 'Host', placeholder: 'ftp.esempio.com', type: 'text' },
  port: { label: 'Porta', placeholder: '21', type: 'number' },
  private_key: { label: 'Chiave Privata (opzionale)', placeholder: 'Incolla la chiave SSH...', type: 'textarea' },
  bucket: { label: 'Bucket Name', placeholder: 'my-bucket', type: 'text' },
  region: { label: 'Region', placeholder: 'eu-west-1', type: 'text' },
  access_key: { label: 'Access Key ID', placeholder: 'AKIA...', type: 'text' },
  secret_key: { label: 'Secret Access Key', placeholder: '••••••••', type: 'password' },
  account_name: { label: 'Account Name', placeholder: 'mystorageaccount', type: 'text' },
  container: { label: 'Container', placeholder: 'mycontainer', type: 'text' },
  sas_token: { label: 'SAS Token', placeholder: '?sv=2021-06-08&ss=...', type: 'password' },
  folder_id: { label: 'Folder ID (opzionale)', placeholder: '1ABC...xyz', type: 'text' }
};

function DataSourceConfigModal({ onClose, onSave }) {
  const [step, setStep] = useState(0);
  const [formData, setFormData] = useState({
    name: '',
    description: '',
    source_type: '',
    credentials: {},
    path_pattern: '',
    file_filter: '*.*',
    recursive: false,
    schedule: null
  });
  
  const [testResult, setTestResult] = useState(null);
  const [testing, setTesting] = useState(false);
  const [browsing, setBrowsing] = useState(false);
  const [fileList, setFileList] = useState([]);
  const [selectedPath, setSelectedPath] = useState('');

  const selectedSource = DATASOURCE_TYPES.find(t => t.id === formData.source_type);

  // Aggiorna campo credenziali
  const updateCredential = (field, value) => {
    setFormData(prev => ({
      ...prev,
      credentials: { ...prev.credentials, [field]: value }
    }));
  };

  // Test connessione
  const handleTestConnection = async () => {
    setTesting(true);
    setTestResult(null);
    
    try {
      // Simula test - in produzione chiamerebbe il backend
      await new Promise(resolve => setTimeout(resolve, 1500));
      
      setTestResult({
        success: true,
        message: 'Connessione riuscita!',
        details: {
          server_version: formData.source_type.includes('sharepoint') ? 'SharePoint Online' : 'Server v2.1',
          permissions: ['read', 'write', 'list']
        }
      });
    } catch (error) {
      setTestResult({
        success: false,
        message: error.message || 'Connessione fallita'
      });
    } finally {
      setTesting(false);
    }
  };

  // Sfoglia file remoti
  const handleBrowse = async () => {
    setBrowsing(true);
    setFileList([]);
    
    try {
      await new Promise(resolve => setTimeout(resolve, 1000));
      
      // Mock file list
      setFileList([
        { type: 'folder', name: 'Documenti', path: '/Documenti', size: null },
        { type: 'folder', name: 'Report 2025', path: '/Report 2025', size: null },
        { type: 'file', name: 'dataset_immobili.xlsx', path: '/dataset_immobili.xlsx', size: '2.4 MB' },
        { type: 'file', name: 'export_catasto.csv', path: '/export_catasto.csv', size: '15.8 MB' },
        { type: 'file', name: 'boundaries.geojson', path: '/boundaries.geojson', size: '8.1 MB' }
      ]);
    } catch (error) {
      console.error('Errore browsing:', error);
    } finally {
      setBrowsing(false);
    }
  };

  // Seleziona path
  const selectPath = (path) => {
    setSelectedPath(path);
    setFormData(prev => ({ ...prev, path_pattern: path }));
  };

  // Salva configurazione
  const handleSave = () => {
    onSave({
      name: formData.name,
      description: formData.description,
      connection_type: 'datasource',
      source_type: formData.source_type,
      category: 'datasource',
      api_type: 'generator', // Data sources sono sempre generator
      credentials: formData.credentials,
      config: {
        path_pattern: formData.path_pattern,
        file_filter: formData.file_filter,
        recursive: formData.recursive,
        schedule: formData.schedule
      }
    });
  };

  return (
    <div className="datasource-modal__overlay" onClick={onClose}>
      <div className="datasource-modal" onClick={e => e.stopPropagation()}>
        <div className="datasource-modal__header">
          <h2 className="datasource-modal__title">📁 Configura Data Source</h2>
          <button onClick={onClose} className="datasource-modal__close">×</button>
        </div>

        <div className="datasource-modal__progress">
          {['Tipo', 'Credenziali', 'Percorso', 'Test'].map((s, idx) => (
            <div 
              key={idx}
              className={`datasource-modal__step ${step === idx ? 'datasource-modal__step--active' : ''} ${step > idx ? 'datasource-modal__step--done' : ''}`}
              onClick={() => step > idx && setStep(idx)}
            >
              <span className="datasource-modal__step-num">{idx + 1}</span>
              <span>{s}</span>
            </div>
          ))}
        </div>

        <div className="datasource-modal__content">
          {/* Step 0: Tipo */}
          {step === 0 && (
            <div className="datasource-modal__section">
              <h3 className="datasource-modal__section-title">Seleziona il tipo di origine dati</h3>
              
              <div className="datasource-modal__form-group">
                <label className="datasource-modal__label">Nome connessione *</label>
                <input
                  type="text"
                  value={formData.name}
                  onChange={e => setFormData(prev => ({ ...prev, name: e.target.value }))}
                  placeholder="Es: SharePoint Aziendale, FTP Report"
                  className="datasource-modal__input"
                />
              </div>

              <div className="datasource-modal__form-group">
                <label className="datasource-modal__label">Descrizione</label>
                <textarea
                  value={formData.description}
                  onChange={e => setFormData(prev => ({ ...prev, description: e.target.value }))}
                  placeholder="Descrizione opzionale..."
                  className="datasource-modal__textarea"
                  rows={2}
                />
              </div>

              <div className="datasource-modal__types-grid">
                {DATASOURCE_TYPES.map(source => (
                  <div
                    key={source.id}
                    className={`datasource-modal__type-card ${formData.source_type === source.id ? 'datasource-modal__type-card--selected' : ''} ${source.status === 'future' ? 'datasource-modal__type-card--disabled' : ''}`}
                    onClick={() => !source.status && setFormData(prev => ({ ...prev, source_type: source.id, credentials: {} }))}
                  >
                    <span className="datasource-modal__type-icon">{source.icon}</span>
                    <span className="datasource-modal__type-label">{source.label}</span>
                    {source.status === 'future' && (
                      <span className="datasource-modal__type-badge">Prossimamente</span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Step 1: Credenziali */}
          {step === 1 && selectedSource && (
            <div className="datasource-modal__section">
              <h3 className="datasource-modal__section-title">
                {selectedSource.icon} Credenziali {selectedSource.label}
              </h3>
              
              <div className="datasource-modal__auth-info">
                <span className="datasource-modal__auth-badge">
                  🔐 {selectedSource.authType === 'oauth2' ? 'OAuth 2.0' : 
                      selectedSource.authType === 'ntlm' ? 'NTLM/Kerberos' :
                      selectedSource.authType === 'basic' ? 'Username/Password' :
                      selectedSource.authType === 'key' ? 'SSH Key' :
                      selectedSource.authType === 'aws' ? 'AWS Credentials' :
                      selectedSource.authType === 'sas' ? 'SAS Token' : 'OAuth 2.0'}
                </span>
              </div>

              <div className="datasource-modal__credentials-form">
                {selectedSource.fields.map(field => {
                  const fieldInfo = FIELD_LABELS[field] || { label: field, placeholder: '', type: 'text' };
                  
                  return (
                    <div key={field} className="datasource-modal__form-group">
                      <label className="datasource-modal__label">{fieldInfo.label}</label>
                      {fieldInfo.type === 'textarea' ? (
                        <textarea
                          value={formData.credentials[field] || ''}
                          onChange={e => updateCredential(field, e.target.value)}
                          placeholder={fieldInfo.placeholder}
                          className="datasource-modal__textarea datasource-modal__textarea--mono"
                          rows={4}
                        />
                      ) : (
                        <input
                          type={fieldInfo.type}
                          value={formData.credentials[field] || ''}
                          onChange={e => updateCredential(field, e.target.value)}
                          placeholder={fieldInfo.placeholder}
                          className={`datasource-modal__input ${fieldInfo.type === 'password' ? 'datasource-modal__input--password' : ''}`}
                        />
                      )}
                    </div>
                  );
                })}
              </div>

              {selectedSource.authType === 'oauth2' && (
                <div className="datasource-modal__oauth-help">
                  <h4>📖 Come ottenere le credenziali OAuth2</h4>
                  <ol>
                    <li>Vai su Azure Portal → App Registrations</li>
                    <li>Crea una nuova app o selezionane una esistente</li>
                    <li>Copia Application (client) ID e Directory (tenant) ID</li>
                    <li>In "Certificates & secrets" crea un nuovo client secret</li>
                    <li>Assegna i permessi: Sites.Read.All, Files.Read.All</li>
                  </ol>
                </div>
              )}
            </div>
          )}

          {/* Step 2: Percorso */}
          {step === 2 && (
            <div className="datasource-modal__section">
              <h3 className="datasource-modal__section-title">Configura percorso e filtri</h3>
              
              <div className="datasource-modal__path-section">
                <div className="datasource-modal__form-group">
                  <label className="datasource-modal__label">Percorso o Pattern *</label>
                  <div className="datasource-modal__path-input">
                    <input
                      type="text"
                      value={formData.path_pattern}
                      onChange={e => setFormData(prev => ({ ...prev, path_pattern: e.target.value }))}
                      placeholder="/Documenti/Report/*.xlsx"
                      className="datasource-modal__input datasource-modal__input--mono"
                    />
                    <button 
                      onClick={handleBrowse} 
                      disabled={browsing}
                      className="datasource-modal__browse-btn"
                    >
                      {browsing ? '⏳' : '📂'} Sfoglia
                    </button>
                  </div>
                  <p className="datasource-modal__hint">
                    Usa * per wildcard. Es: /Report/2025/*.csv
                  </p>
                </div>

                {/* File Browser */}
                {fileList.length > 0 && (
                  <div className="datasource-modal__file-browser">
                    <h4>📁 Contenuto remoto</h4>
                    <div className="datasource-modal__file-list">
                      {fileList.map((item, idx) => (
                        <div 
                          key={idx}
                          className={`datasource-modal__file-item ${selectedPath === item.path ? 'datasource-modal__file-item--selected' : ''}`}
                          onClick={() => selectPath(item.path)}
                        >
                          <span className="datasource-modal__file-icon">
                            {item.type === 'folder' ? '📁' : '📄'}
                          </span>
                          <span className="datasource-modal__file-name">{item.name}</span>
                          {item.size && (
                            <span className="datasource-modal__file-size">{item.size}</span>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                <div className="datasource-modal__form-group">
                  <label className="datasource-modal__label">Filtro file</label>
                  <input
                    type="text"
                    value={formData.file_filter}
                    onChange={e => setFormData(prev => ({ ...prev, file_filter: e.target.value }))}
                    placeholder="*.xlsx, *.csv"
                    className="datasource-modal__input"
                  />
                </div>

                <div className="datasource-modal__checkbox-group">
                  <label className="datasource-modal__checkbox">
                    <input
                      type="checkbox"
                      checked={formData.recursive}
                      onChange={e => setFormData(prev => ({ ...prev, recursive: e.target.checked }))}
                    />
                    <span>Includi sottocartelle (ricorsivo)</span>
                  </label>
                </div>
              </div>
            </div>
          )}

          {/* Step 3: Test */}
          {step === 3 && (
            <div className="datasource-modal__section">
              <h3 className="datasource-modal__section-title">Testa la connessione</h3>
              
              <div className="datasource-modal__summary">
                <h4>📋 Riepilogo configurazione</h4>
                <div className="datasource-modal__summary-grid">
                  <div><strong>Nome:</strong> {formData.name || '-'}</div>
                  <div><strong>Tipo:</strong> {selectedSource?.label || '-'}</div>
                  <div><strong>Percorso:</strong> {formData.path_pattern || '-'}</div>
                  <div><strong>Filtro:</strong> {formData.file_filter}</div>
                  <div><strong>Ricorsivo:</strong> {formData.recursive ? 'Sì' : 'No'}</div>
                </div>
              </div>

              <button 
                onClick={handleTestConnection} 
                disabled={testing}
                className="datasource-modal__test-btn"
              >
                {testing ? '⏳ Test in corso...' : '🔌 Testa Connessione'}
              </button>

              {testResult && (
                <div className={`datasource-modal__result ${testResult.success ? 'datasource-modal__result--success' : 'datasource-modal__result--error'}`}>
                  <h4>{testResult.success ? '✅ ' : '❌ '}{testResult.message}</h4>
                  {testResult.success && testResult.details && (
                    <div className="datasource-modal__result-details">
                      <p><strong>Versione:</strong> {testResult.details.server_version}</p>
                      <p><strong>Permessi:</strong> {testResult.details.permissions.join(', ')}</p>
                    </div>
                  )}
                </div>
              )}

              <div className="datasource-modal__note">
                <p>💡 <strong>Nota:</strong> I Data Source funzionano come <em>Generatori</em>. 
                Quando eseguiti, scaricano i file dal percorso specificato e li rendono disponibili come dataset.</p>
              </div>
            </div>
          )}
        </div>

        <div className="datasource-modal__footer">
          <button 
            onClick={step === 0 ? onClose : () => setStep(s => s - 1)} 
            className="datasource-modal__btn datasource-modal__btn--secondary"
          >
            {step === 0 ? 'Annulla' : '← Indietro'}
          </button>
          
          {step < 3 ? (
            <button 
              onClick={() => setStep(s => s + 1)}
              disabled={(step === 0 && (!formData.name || !formData.source_type)) || 
                        (step === 1 && Object.keys(formData.credentials).length === 0)}
              className="datasource-modal__btn datasource-modal__btn--primary"
            >
              Avanti →
            </button>
          ) : (
            <button 
              onClick={handleSave}
              disabled={!formData.name || !formData.source_type || !formData.path_pattern}
              className="datasource-modal__btn datasource-modal__btn--success"
            >
              💾 Salva Data Source
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export default DataSourceConfigModal;
