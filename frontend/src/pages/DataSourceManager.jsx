/**
 * DataSourceManager.jsx — Gestione Data Sources
 * Redesign Plenora design system
 * Route: /datasources
 */
import React, { useState, useEffect, useCallback } from 'react';
import { useToast } from '../hooks/useToast';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import {
  listDataSources, createDataSource, updateDataSource,
  deleteDataSource, testDataSource, listFiles,
} from '../api/datasources';
import { useHexCanvas } from '../utils/hexCanvas';
import AllowedGroupsSelector from '../components/shared/AllowedGroupsSelector';
import AppSidebar from '../components/AppSidebar';
import ThemeToggle from '../components/common/ThemeToggle';
import { handleCascade409, formatCascadeDependencies } from '../api/errors';
import useDialogFocus from '../hooks/useDialogFocus';
import { can, role } from '../utils/permissions';
import '../styles/DataSourceManager.css';
const DS_TYPES = [
  { value:'local',      label:'Cartella locale',   icon:'📂', color:'#94A3B8', rgb:'148,163,184', desc:'Disco locale o percorso UNC' },
  { value:'ftp',        label:'FTP',               icon:'📁', color:'#60A5FA', rgb:'96,165,250',  desc:'File Transfer Protocol (FTPS)' },
  { value:'sftp',       label:'SFTP',              icon:'🔐', color:'#34D399', rgb:'52,211,153',  desc:'SSH File Transfer Protocol' },
  { value:'sharepoint', label:'SharePoint Online', icon:'☁️', color:'#4B8BFF', rgb:'75,139,255',  desc:'Microsoft Graph API (OAuth2)' },
];
const TYPE_INFO = Object.fromEntries(DS_TYPES.map(t => [t.value, t]));
const DEFAULT_PORTS = { ftp:21, sftp:22 };
const EMPTY_FORM = {
  name:'', description:'', type:'local', root_path:'',
  host:'', port:'', username:'', password:'',
  tls:false, passive:true,
  private_key:'', private_key_passphrase:'', host_key_policy:'auto_add',
  tenant_id:'', client_id:'', client_secret:'', site_url:'', site_id:'', drive_id:'',
};
const formatSize = (bytes) => {
  if (!bytes) return '0 B';
  const k = 1024, s = ['B','KB','MB','GB'], i = Math.floor(Math.log(bytes)/Math.log(k));
  return `${parseFloat((bytes/Math.pow(k,i)).toFixed(1))} ${s[i]}`;
};
const formatDate = (iso) => {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('it-IT', { day:'2-digit', month:'2-digit', year:'numeric', hour:'2-digit', minute:'2-digit' });
};
export default function DataSourceManager() {
  const toast    = useToast();
  const navigate = useNavigate();
  const { user, logout } = useAuth();
  const canvasRef = React.useRef(null);
  useHexCanvas(canvasRef);

  const [datasources,   setDatasources]   = useState([]);
  const [loading,       setLoading]       = useState(true);
  const [error,         setError]         = useState(null);
  const [showModal,     setShowModal]     = useState(false);
  const [editing,       setEditing]       = useState(null);
  const [formData,      setFormData]      = useState(EMPTY_FORM);
  const [saving,        setSaving]        = useState(false);
  const [formError,     setFormError]     = useState(null);
  const [testingId,     setTestingId]     = useState(null);
  // P1 frontend-pages: anti double-submit su DELETE Data Source.
  const [deletingId,    setDeletingId]    = useState(null);
  const [testResults,   setTestResults]   = useState({});
  const [browserDs,     setBrowserDs]     = useState(null);
  const [browserFiles,  setBrowserFiles]  = useState([]);
  const [browserPath,   setBrowserPath]   = useState('');
  const [browserLoading,setBrowserLoading]= useState(false);
  const [browserError,  setBrowserError]  = useState(null);
  const [confirmDelete, setConfirmDelete] = useState(null);
  const [sharingDatasource, setSharingDatasource] = useState(null);  // ds in sharing modal (solo super)

  // Focus management per i 4 dialog inline (a11y batch 5).
  const editDialogRef    = React.useRef(null);
  const browserDialogRef = React.useRef(null);
  const confirmDialogRef = React.useRef(null);
  const shareDialogRef   = React.useRef(null);
  useDialogFocus(
    showModal,
    () => { setShowModal(false); setEditing(null); setFormError(null); },
    editDialogRef,
  );
  useDialogFocus(
    !!browserDs,
    () => { setBrowserDs(null); setBrowserFiles([]); setBrowserPath(''); },
    browserDialogRef,
  );
  useDialogFocus(!!confirmDelete,    () => setConfirmDelete(null),    confirmDialogRef);
  useDialogFocus(!!sharingDatasource, () => setSharingDatasource(null), shareDialogRef);

  const isSuperadmin   = role.isSuperadmin(user);
  const canCreateDs    = can.createDataSource(user);

  const load = useCallback(async () => {
    try { setLoading(true); setError(null); const d = await listDataSources(); setDatasources(d.datasources || []); }
    catch (e) { setError(e.message); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const openNew = () => { setEditing(null); setFormData(EMPTY_FORM); setFormError(null); setShowModal(true); };
  const openEdit = ds => {
    setEditing(ds);
    setFormData({ ...EMPTY_FORM, name:ds.name||'', description:ds.description||'', type:ds.type||'local', root_path:ds.root_path||'', host:ds.host||'', port:ds.port||'', username:ds.username||'', password:'', tls:ds.tls||false, passive:ds.passive!==false, private_key:'', private_key_passphrase:'', host_key_policy:ds.host_key_policy||'auto_add', tenant_id:ds.tenant_id||'', client_id:ds.client_id||'', client_secret:'', site_url:ds.site_url||'', site_id:ds.site_id||'', drive_id:ds.drive_id||'' });
    setFormError(null); setShowModal(true);
  };
  const closeModal = () => { setShowModal(false); setEditing(null); setFormError(null); };
  const handleTypeChange = type => setFormData(prev => ({ ...prev, type, port: DEFAULT_PORTS[type] || '' }));
  const handleField = (f, v) => setFormData(prev => ({ ...prev, [f]: v }));

  const handleSave = async () => {
    if (!formData.name.trim()) { setFormError('Il nome è obbligatorio'); return; }
    if (formData.type === 'sharepoint' && !formData.site_url && !formData.site_id) { setFormError("Inserire 'URL Sito' o 'Site ID'"); return; }
    setSaving(true); setFormError(null);
    const payload = {};
    Object.entries(formData).forEach(([k,v]) => { if (v !== '' && v !== null && v !== undefined) payload[k] = v; });
    try {
      if (editing) await updateDataSource(editing.id, payload); else await createDataSource(payload);
      closeModal(); await load();
    } catch (e) { setFormError(e.message); }
    finally { setSaving(false); }
  };

  const handleTest = async id => {
    setTestingId(id); setTestResults(prev => ({ ...prev, [id]: null }));
    try { const r = await testDataSource(id); setTestResults(prev => ({ ...prev, [id]: r })); }
    catch (e) { setTestResults(prev => ({ ...prev, [id]: { success:false, message:e.message } })); }
    finally { setTestingId(null); }
  };

  // Pre-fix (P1 frontend-pages): handleDelete async senza disabling
  // del bottone → utente puo' cliccare "Elimina" piu' volte mentre
  // l'API e' in corso, generando N DELETE concorrenti sullo stesso ID.
  // Post-fix: deletingId flag che disabilita il bottone durante l'await.
  const handleDelete = async id => {
    if (deletingId === id) return;  // guard re-entrancy
    setDeletingId(id);
    try {
      await deleteDataSource(id);
      setConfirmDelete(null);
      await load();
    } catch (e) {
      const cascade = handleCascade409(e);
      if (cascade) {
        const dsName = confirmDelete?.name || id;
        toast.error(
          `Impossibile eliminare "${dsName}": ${formatCascadeDependencies(cascade.dependencies)}. Rimuovili prima.`,
          10000
        );
        return;
      }
      toast.error(`Errore: ${e.message}`);
    } finally {
      setDeletingId(null);
    }
  };

  const openBrowser = async ds => { setBrowserDs(ds); setBrowserPath(''); setBrowserFiles([]); setBrowserError(null); await browseFiles(ds.id, ''); };
  const browseFiles = async (dsId, path) => {
    setBrowserLoading(true); setBrowserError(null); setBrowserPath(path);
    try { const files = await listFiles(dsId, { path }); setBrowserFiles(files); }
    catch (e) { setBrowserError(e.message); }
    finally { setBrowserLoading(false); }
  };
  const closeBrowser = () => { setBrowserDs(null); setBrowserFiles([]); setBrowserPath(''); };

  return (
    <div className="dsm">
      {/* SIDEBAR */}
      <AppSidebar counts={{ datasources: datasources.length }} />

      {/* MAIN */}
      <div className="dsm__main">
        <header className="dsm__topbar">
          <button type="button" className="dsm__back-btn" onClick={() => navigate(-1)}>← Indietro</button>
          <div className="dsm__page-title">Data Source</div>
          <div className="dsm__topbar-right">
            <ThemeToggle classPrefix="dsm" />
            {canCreateDs && (
              <button type="button" className="dsm__btn dsm__btn--primary" onClick={openNew}>＋ Nuovo Data Source</button>
            )}
          </div>
        </header>

        <div className="dsm__content">
          <canvas ref={canvasRef} className="dsm__hex-bg" />

          {error && (
            <div className="dsm__error-banner">⚠️ {error} <button type="button" onClick={load}>Riprova</button></div>
          )}

          {loading ? (
            <div className="dsm__loading"><div className="dsm__loading-icon">⬡</div>Caricamento Data Source...</div>
          ) : datasources.length === 0 ? (
            <div className="dsm__empty">
              <div className="dsm__empty-icon">🗄️</div>
              <div className="dsm__empty-title">Nessun Data Source configurato</div>
              <div className="dsm__empty-text">Aggiungi un Data Source per collegare Plenora a SharePoint, FTP, SFTP o cartelle di rete.</div>
              {canCreateDs && (
                <button type="button" className="dsm__btn dsm__btn--primary dsm__btn--mt-lg" onClick={openNew}>＋ Nuovo Data Source</button>
              )}
            </div>
          ) : (
            <div className="dsm__grid">
              {datasources.map(ds => {
                const ti = TYPE_INFO[ds.type] || {};
                const testResult = testResults[ds.id];
                const isTesting  = testingId === ds.id;
                const canEdit    = ds.can_edit !== false;
                return (
                  <div key={ds.id} className="dsm__card">
                    <div className="dsm__card-bar" style={{ background: ti.color }} />
                    <div className="dsm__card-inner">
                      <div className="dsm__card-header">
                        <div className="dsm__card-type" style={{ color: ti.color, background: `rgba(${ti.rgb},.10)` }}>
                          {ti.icon} {ti.label}
                        </div>
                        <div className="dsm__card-actions">
                          <button type="button" className="dsm__action-btn" onClick={() => openBrowser(ds)} title="Sfoglia" aria-label="Sfoglia file del data source">📂</button>
                          {canEdit && (
                            <button type="button" className="dsm__action-btn" onClick={() => openEdit(ds)} title="Modifica" aria-label="Modifica data source">✏️</button>
                          )}
                          {isSuperadmin && (
                            <button type="button" className="dsm__action-btn" onClick={() => setSharingDatasource(ds)} title="Gestisci condivisione cross-gruppo" aria-label="Gestisci condivisione cross-gruppo">🔗</button>
                          )}
                          {canEdit && (
                            <button type="button" className="dsm__action-btn dsm__action-btn--danger" onClick={() => setConfirmDelete(ds)} title="Elimina" aria-label="Elimina data source">🗑</button>
                          )}
                          {!canEdit && (
                            <span
                              className="dsm__lock-badge dsm__field-help-icon"
                              title="Solo un superadmin può modificare questa risorsa"
                            >
                              🔒
                            </span>
                          )}
                        </div>
                      </div>
                      <div className="dsm__card-name">{ds.name}</div>
                      {ds.description && <div className="dsm__card-desc">{ds.description}</div>}
                      <div className="dsm__card-meta">
                        {ds.host       && <span className="dsm__meta-item">🖥 {ds.host}{ds.port ? `:${ds.port}` : ''}</span>}
                        {ds.site_url   && <span className="dsm__meta-item">🌐 {ds.site_url}</span>}
                        {ds.root_path  && <span className="dsm__meta-item">📁 {ds.root_path}</span>}
                        {ds.username   && <span className="dsm__meta-item">👤 {ds.username}</span>}
                      </div>
                      <div className="dsm__card-footer">
                        <button type="button"
                          className={`dsm__test-btn${isTesting?' dsm__test-btn--loading':''}`}
                          onClick={() => handleTest(ds.id)} disabled={isTesting}>
                          {isTesting ? '⏳ Test in corso...' : '🔌 Testa connessione'}
                        </button>
                        {testResult && (
                          <div className={`dsm__test-result${testResult.success?' dsm__test-result--ok':' dsm__test-result--err'}`}>
                            {testResult.success ? '✓' : '✗'} {testResult.message}
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* MODAL CREA/MODIFICA */}
      {showModal && (
        <div className="dsm__overlay" onClick={e => e.target === e.currentTarget && closeModal()}>
          <div
            ref={editDialogRef}
            className="dsm__modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="dsm-edit-title"
            tabIndex={-1}
          >
            <div className="dsm__modal-header">
              <div className="dsm__modal-title" id="dsm-edit-title">{editing ? `Modifica — ${editing.name}` : 'Nuovo Data Source'}</div>
              <button type="button" className="dsm__modal-close" onClick={closeModal} aria-label="Chiudi">✕</button>
            </div>
            <div className="dsm__modal-body">

              {/* Tipo */}
              <div className="dsm__field-group">
                <div className="dsm__label">Tipo *</div>
                <div className="dsm__type-grid">
                  {DS_TYPES.map(t => (
                    <button type="button" key={t.value}
                      className={`dsm__type-btn${formData.type===t.value?' dsm__type-btn--active':''}`}
                      style={formData.type===t.value?{borderColor:t.color,background:`rgba(${t.rgb},.08)`}:{}}
                      onClick={() => handleTypeChange(t.value)}>
                      <span className="dsm__type-icon">{t.icon}</span>
                      <span className="dsm__type-label">{t.label}</span>
                      <span className="dsm__type-desc">{t.desc}</span>
                    </button>
                  ))}
                </div>
              </div>

              {/* Nome + Descrizione */}
              <div className="dsm__row">
                <div className="dsm__field">
                  <div className="dsm__label">Nome *</div>
                  <input className="dsm__input" value={formData.name} onChange={e=>handleField('name',e.target.value)} placeholder="es. SharePoint ICSC" />
                </div>
                <div className="dsm__field">
                  <div className="dsm__label">Descrizione</div>
                  <input className="dsm__input" value={formData.description} onChange={e=>handleField('description',e.target.value)} placeholder="Opzionale" />
                </div>
              </div>

              {/* LOCALE */}
              {formData.type === 'local' && (
                <div className="dsm__field">
                  <div className="dsm__label">Percorso cartella *</div>
                  <input className="dsm__input" value={formData.root_path} onChange={e=>handleField('root_path',e.target.value)} placeholder="C:\dati\estrazioni  oppure  \\server\share\dati" />
                  <span className="dsm__hint">Percorso assoluto locale o UNC</span>
                </div>
              )}

              {/* FTP */}
              {formData.type === 'ftp' && (<>
                <div className="dsm__row">
                  <div className="dsm__field dsm__field--grow">
                    <div className="dsm__label">Host *</div>
                    <input className="dsm__input" value={formData.host} onChange={e=>handleField('host',e.target.value)} placeholder="ftp.server.it" />
                  </div>
                  <div className="dsm__field dsm__field--sm">
                    <div className="dsm__label">Porta</div>
                    <input className="dsm__input" type="number" value={formData.port} onChange={e=>handleField('port',e.target.value)} placeholder="21" />
                  </div>
                </div>
                <div className="dsm__row">
                  <div className="dsm__field"><div className="dsm__label">Utente</div><input className="dsm__input" value={formData.username} onChange={e=>handleField('username',e.target.value)} placeholder="anonymous" /></div>
                  <div className="dsm__field"><div className="dsm__label">Password</div><input className="dsm__input" type="password" value={formData.password} onChange={e=>handleField('password',e.target.value)} placeholder={editing?'(invariata)':''} /></div>
                </div>
                <div className="dsm__field">
                  <div className="dsm__label">Cartella root</div>
                  <input className="dsm__input" value={formData.root_path} onChange={e=>handleField('root_path',e.target.value)} placeholder="/upload/estrazioni" />
                </div>
                <div className="dsm__checkboxes">
                  <label className="dsm__check-label"><input type="checkbox" checked={formData.tls} onChange={e=>handleField('tls',e.target.checked)}/><span>TLS (FTPS)</span></label>
                  <label className="dsm__check-label"><input type="checkbox" checked={formData.passive} onChange={e=>handleField('passive',e.target.checked)}/><span>Modalità passiva</span></label>
                </div>
              </>)}

              {/* SFTP */}
              {formData.type === 'sftp' && (<>
                <div className="dsm__row">
                  <div className="dsm__field dsm__field--grow"><div className="dsm__label">Host *</div><input className="dsm__input" value={formData.host} onChange={e=>handleField('host',e.target.value)} placeholder="sftp.server.it" /></div>
                  <div className="dsm__field dsm__field--sm"><div className="dsm__label">Porta</div><input className="dsm__input" type="number" value={formData.port} onChange={e=>handleField('port',e.target.value)} placeholder="22" /></div>
                </div>
                <div className="dsm__row">
                  <div className="dsm__field"><div className="dsm__label">Utente *</div><input className="dsm__input" value={formData.username} onChange={e=>handleField('username',e.target.value)} /></div>
                  <div className="dsm__field"><div className="dsm__label">Password</div><input className="dsm__input" type="password" value={formData.password} onChange={e=>handleField('password',e.target.value)} placeholder={editing?'(invariata)':'Lascia vuoto se usi chiave SSH'} /></div>
                </div>
                <div className="dsm__field">
                  <div className="dsm__label">Chiave SSH privata (PEM)</div>
                  <textarea className="dsm__textarea" value={formData.private_key} onChange={e=>handleField('private_key',e.target.value)} placeholder={editing?'(invariata — lascia vuoto per non modificare)':'-----BEGIN RSA PRIVATE KEY-----\n...'} rows={4} />
                </div>
                <div className="dsm__row">
                  <div className="dsm__field"><div className="dsm__label">Passphrase chiave</div><input className="dsm__input" type="password" value={formData.private_key_passphrase} onChange={e=>handleField('private_key_passphrase',e.target.value)} placeholder="Lascia vuoto se non cifrata" /></div>
                  <div className="dsm__field">
                    <div className="dsm__label">Policy host sconosciuti</div>
                    <select className="dsm__select" value={formData.host_key_policy} onChange={e=>handleField('host_key_policy',e.target.value)}>
                      <option value="auto_add">Accetta automaticamente</option>
                      <option value="reject">Rifiuta (più sicuro)</option>
                    </select>
                  </div>
                </div>
                <div className="dsm__field"><div className="dsm__label">Cartella root</div><input className="dsm__input" value={formData.root_path} onChange={e=>handleField('root_path',e.target.value)} placeholder="/upload/estrazioni" /></div>
              </>)}

              {/* SHAREPOINT */}
              {formData.type === 'sharepoint' && (<>
                <div className="dsm__sp-info">💡 Richiede un'app Azure AD con permessi <strong>Sites.ReadWrite.All</strong> e <strong>Files.ReadWrite.All</strong>.</div>
                <div className="dsm__field"><div className="dsm__label">Tenant ID *</div><input className="dsm__input" value={formData.tenant_id} onChange={e=>handleField('tenant_id',e.target.value)} placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" /></div>
                <div className="dsm__row">
                  <div className="dsm__field"><div className="dsm__label">Client ID *</div><input className="dsm__input" value={formData.client_id} onChange={e=>handleField('client_id',e.target.value)} placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" /></div>
                  <div className="dsm__field"><div className="dsm__label">Client Secret *</div><input className="dsm__input" type="password" value={formData.client_secret} onChange={e=>handleField('client_secret',e.target.value)} placeholder={editing?'(invariato)':''} /></div>
                </div>
                <div className="dsm__field">
                  <div className="dsm__label">URL Sito *</div>
                  <input className="dsm__input" value={formData.site_url} onChange={e=>handleField('site_url',e.target.value)} placeholder="https://tenant.sharepoint.com/sites/ICSC" />
                  <span className="dsm__hint">Usato per individuare il sito automaticamente</span>
                </div>
                <div className="dsm__row">
                  <div className="dsm__field"><div className="dsm__label">Site ID <span className="dsm__optional">(opzionale)</span></div><input className="dsm__input" value={formData.site_id} onChange={e=>handleField('site_id',e.target.value)} placeholder="Scoperto automaticamente" /></div>
                  <div className="dsm__field"><div className="dsm__label">Drive ID <span className="dsm__optional">(opzionale)</span></div><input className="dsm__input" value={formData.drive_id} onChange={e=>handleField('drive_id',e.target.value)} placeholder="Default: Document Library" /></div>
                </div>
                <div className="dsm__field"><div className="dsm__label">Percorso root</div><input className="dsm__input" value={formData.root_path} onChange={e=>handleField('root_path',e.target.value)} placeholder="/sites/ICSC/Documents/estrazioni" /></div>
              </>)}

              {formError && <div className="dsm__form-error">❌ {formError}</div>}
            </div>
            <div className="dsm__modal-footer">
              <button type="button" className="dsm__btn dsm__btn--ghost" onClick={closeModal} disabled={saving}>Annulla</button>
              <button type="button" className="dsm__btn dsm__btn--primary" onClick={handleSave} disabled={saving}>
                {saving ? '⏳ Salvataggio...' : editing ? '💾 Aggiorna' : '✅ Crea Data Source'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* MODAL BROWSER */}
      {browserDs && (
        <div className="dsm__overlay" onClick={e => e.target === e.currentTarget && closeBrowser()}>
          <div
            ref={browserDialogRef}
            className="dsm__modal dsm__modal--browser"
            role="dialog"
            aria-modal="true"
            aria-labelledby="dsm-browser-title"
            tabIndex={-1}
          >
            <div className="dsm__modal-header">
              <div className="dsm__modal-title" id="dsm-browser-title">
                📂 {browserDs.name}
                {browserPath && <span className="dsm__browser-path"> / {browserPath}</span>}
              </div>
              <div className="dsm__inline-row">
                <button type="button" className="dsm__btn dsm__btn--ghost dsm__btn--sm" onClick={() => browseFiles(browserDs.id, browserPath)} disabled={browserLoading} aria-label="Aggiorna elenco file">🔄</button>
                <button type="button" className="dsm__modal-close" onClick={closeBrowser} aria-label="Chiudi">✕</button>
              </div>
            </div>
            <div className="dsm__modal-body">
              {browserPath && (
                <div className="dsm__breadcrumb">
                  <button type="button" onClick={() => browseFiles(browserDs.id, '')}>root</button>
                  {browserPath.split('/').filter(Boolean).map((seg, i, arr) => {
                    const path = arr.slice(0, i+1).join('/');
                    return (
                      <React.Fragment key={path}>
                        <span className="dsm__breadcrumb-sep">/</span>
                        <button type="button" onClick={() => browseFiles(browserDs.id, path)}>{seg}</button>
                      </React.Fragment>
                    );
                  })}
                </div>
              )}
              {browserLoading && <div className="dsm__browser-loading">⏳ Caricamento...</div>}
              {browserError  && <div className="dsm__browser-error">❌ {browserError}</div>}
              {!browserLoading && !browserError && (
                browserFiles.length === 0 ? (
                  <div className="dsm__browser-empty">Nessun file trovato in questa cartella</div>
                ) : (
                  <table className="dsm__file-table">
                    <thead>
                      <tr>
                        <th>Nome</th><th>Tipo</th><th>Dimensione</th><th>Modificato</th>
                      </tr>
                    </thead>
                    <tbody>
                      {browserFiles.map(f => (
                        <tr key={f.path}
                          className={f.type==='folder'?'dsm__file-row--folder':''}
                          onClick={f.type==='folder'?()=>browseFiles(browserDs.id,f.path):undefined}
                          style={f.type==='folder'?{cursor:'pointer'}:{}}>
                          <td className="dsm__file-name">
                            <span className="dsm__file-icon">{f.type==='folder'?'📁':f.type==='csv'?'📊':f.type==='xlsx'?'📗':f.type==='geojson'||f.type==='gpkg'?'🗺️':'📄'}</span>
                            {f.type==='folder'?<span className="dsm__folder-link">{f.name}</span>:f.name}
                          </td>
                          <td><span className={`dsm__file-type${f.type==='folder'?' dsm__file-type--folder':''}`}>{f.type==='folder'?'Cartella':f.type.toUpperCase()}</span></td>
                          <td>{f.type==='folder'?'—':formatSize(f.size)}</td>
                          <td>{formatDate(f.modified_date)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )
              )}
            </div>
          </div>
        </div>
      )}

      {/* CONFIRM DELETE */}
      {confirmDelete && (
        <div className="dsm__overlay">
          <div
            ref={confirmDialogRef}
            className="dsm__confirm"
            role="dialog"
            aria-modal="true"
            aria-labelledby="dsm-confirm-title"
            tabIndex={-1}
          >
            <div className="dsm__confirm-title" id="dsm-confirm-title">Elimina Data Source</div>
            <div className="dsm__confirm-text">Sei sicuro di voler eliminare <strong>{confirmDelete.name}</strong>?</div>
            <div className="dsm__confirm-warn">⚠️ Verranno eliminati anche i dati di scan state associati.</div>
            <div className="dsm__confirm-actions">
              <button
                type="button"
                className="dsm__btn dsm__btn--ghost"
                onClick={() => setConfirmDelete(null)}
                disabled={deletingId === confirmDelete.id}
              >Annulla</button>
              <button
                type="button"
                className="dsm__btn dsm__btn--danger"
                onClick={() => handleDelete(confirmDelete.id)}
                disabled={deletingId === confirmDelete.id}
              >{deletingId === confirmDelete.id ? 'Eliminazione…' : 'Elimina'}</button>
            </div>
          </div>
        </div>
      )}

      {/* SHARE MODAL (solo superadmin) */}
      {sharingDatasource && (
        <div className="dsm__overlay" onClick={() => setSharingDatasource(null)}>
          <div
            ref={shareDialogRef}
            className="dsm__modal dsm__type-info"
            onClick={e => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
            aria-labelledby="dsm-share-title"
            tabIndex={-1}
          >
            <div className="dsm__modal-header">
              <div className="dsm__modal-title" id="dsm-share-title">🔗 Condividi Data Source — {sharingDatasource.name}</div>
              <button type="button" className="dsm__modal-close" onClick={() => setSharingDatasource(null)} aria-label="Chiudi">✕</button>
            </div>
            <div className="dsm__modal-body">
              <AllowedGroupsSelector
                embedded
                resourceType="datasources"
                resourceId={sharingDatasource.id}
                onClose={() => setSharingDatasource(null)}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
