/**
 * ApiConnectionManager.jsx — Connettori API Esterni (page shell)
 * Redesign Plenora design system
 * Route: /external-connectors
 *
 * La page e' shell:
 *  - lista connettori + filtri/categorie + search;
 *  - dropdown "Nuovo Connettore" (Template / OpenAPI / Manuale);
 *  - delete con cascade-aware error;
 *  - quick test (input + result modali inline);
 *  - sharing modal (superadmin);
 *  - dispatch del wizard via prop `seed` (new / edit / template).
 */
import React, { useState, useEffect, useMemo, useRef } from 'react';
import { useToast } from '../hooks/useToast';
import { toastApiError } from '../utils/toastApiError';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import TemplateLibraryModal from '../components/arricchisci/TemplateLibraryModal';
import OpenAPIImportModal from '../components/arricchisci/OpenAPIImportModal';
import DataSourceConfigModal from '../components/arricchisci/DataSourceConfigModal';
import ApiConnectionWizard from '../components/api-conn/ApiConnectionWizard';
import ApiConnectionCard, { getApiStatus } from '../components/api-conn/ApiConnectionCard';
import { useHexCanvas } from '../utils/hexCanvas';
import AllowedGroupsSelector from '../components/shared/AllowedGroupsSelector';
import ConfirmDialog from '../components/common/ConfirmDialog';
import { useConfirmModal } from '../hooks/useConfirmModal';
import useDialogFocus from '../hooks/useDialogFocus';
import AppSidebar from '../components/AppSidebar';
import ThemeToggle from '../components/common/ThemeToggle';
import { handleCascade409, formatCascadeDependencies } from '../api/errors';
import {
  listApiConnections,
  createApiConnection,
  deleteApiConnection,
  testApiConnection,
} from '../api/apiConnections';
import { createDataSource } from '../api/datasources';
import { can } from '../utils/permissions';
import '../styles/ApiConnectionManager.css';
import '../components/panels/ResponseConfigBuilder.css';
import { requestErrorMessage } from '../utils/errorMessages';
const CATEGORIES = [
  { id:'all',        label:'Tutte',       icon:'🔗' },
  { id:'geocoding',  label:'Geocoding',   icon:'🌍' },
  { id:'routing',    label:'Routing',     icon:'🚗' },
  { id:'catasto',    label:'Catasto',     icon:'🏠' },
  { id:'datasource', label:'Data Source', icon:'📁' },
  { id:'custom',     label:'Custom',      icon:'⚙️' },
];

const toDataSourcePayload = (dsConfig) => {
  const datasource = dsConfig.datasource || {};
  const credentials = datasource.credentials || {};
  const rawType = datasource.type || dsConfig.source_type || '';
  const typeMap = {
    sharepoint_365: 'sharepoint',
    sharepoint_onprem: 'sharepoint',
    sharepoint: 'sharepoint',
    ftp: 'ftp',
    sftp: 'sftp',
    local: 'local',
  };
  const type = typeMap[rawType] || rawType;
  const payload = {
    name: dsConfig.name,
    description: dsConfig.description || '',
    type,
    root_path: credentials.root_path || datasource.root_path || datasource.path || datasource.path_pattern || '',
  };
  [
    'host', 'port', 'username', 'password', 'private_key',
    'private_key_passphrase', 'tenant_id', 'client_id', 'client_secret',
    'site_id', 'site_url', 'drive_id',
  ].forEach((key) => {
    const value = credentials[key] ?? datasource[key];
    if (value !== undefined && value !== '') {
      payload[key] = value;
    }
  });
  if (!payload.site_url && credentials.server_url) {
    payload.site_url = credentials.server_url;
  }
  return payload;
};

// Status/icon/timeAgo helpers + STATUS_CFG vivono in
// `components/api-conn/ApiConnectionCard.jsx` (presentational scope).
// Qui importiamo solo `getApiStatus` per il counter `globalStats.warningApis`.
function ApiConnectionManager() {
  const toast = useToast();
  const confirmModal = useConfirmModal();
  const navigate = useNavigate();
  const { user } = useAuth();
  const canvasRef = React.useRef(null);
  useHexCanvas(canvasRef);

  const [apiConnections, setApiConnections] = useState([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [activeCategory, setActiveCategory] = useState('all');
  const [showNewApiDropdown, setShowNewApiDropdown] = useState(false);

  // External modals (black box)
  const [showTemplateLibrary,  setShowTemplateLibrary]  = useState(false);
  const [showOpenAPIImport,    setShowOpenAPIImport]    = useState(false);
  const [showDataSourceConfig, setShowDataSourceConfig] = useState(false);

  // Wizard keeps its own form state; the page only chooses the seed.
  const [wizardOpen, setWizardOpen] = useState(false);
  const [wizardSeed, setWizardSeed] = useState(null); // null | { kind:'new' } | { kind:'edit', api } | { kind:'template', template }

  // Quick test
  const [quickTestInput,  setQuickTestInput]  = useState(null);
  const [quickTestResult, setQuickTestResult] = useState(null);

  // Sharing (solo superadmin)
  const [sharingApi, setSharingApi] = useState(null);
  const canCreateApi    = can.createApiConnection(user);

  // Focus management per i 3 dialog inline residui in page (a11y).
  // Il wizard gestisce internamente il proprio focus.
  const quickTestInputDialogRef  = useRef(null);
  const quickTestResultDialogRef = useRef(null);
  const shareDialogRef           = useRef(null);
  useDialogFocus(!!quickTestInput,  () => setQuickTestInput(null),  quickTestInputDialogRef);
  useDialogFocus(!!quickTestResult, () => setQuickTestResult(null), quickTestResultDialogRef);
  useDialogFocus(!!sharingApi,      () => setSharingApi(null),      shareDialogRef);

  useEffect(() => { loadApiConnections(); }, []);
  useEffect(() => {
    const h = e => { if (showNewApiDropdown && !e.target.closest('.acm__new-wrapper')) setShowNewApiDropdown(false); };
    document.addEventListener('click', h);
    return () => document.removeEventListener('click', h);
  }, [showNewApiDropdown]);

  const loadApiConnections = async () => {
    // Pre-fix (P1 frontend-pages): catch ignorava l'errore con solo
    // console.error → pagina vuota senza spiegazione per l'utente
    // se il fetch falliva per auth/network.
    try {
      setLoading(true);
      const d = await listApiConnections();
      setApiConnections(d.api_connections || []);
    } catch (e) {
      console.error('[ApiConnectionManager] loadApiConnections fallito:', e);
      const msg = requestErrorMessage(e, 'Errore caricamento API connections');
      toast.error(msg);
    } finally {
      setLoading(false);
    }
  };

  const filteredApis = useMemo(() => apiConnections.filter(api => {
    const ms = !searchQuery || api.name?.toLowerCase().includes(searchQuery.toLowerCase()) || api.description?.toLowerCase().includes(searchQuery.toLowerCase());
    const mc = activeCategory === 'all' || api.category === activeCategory;
    return ms && mc;
  }), [apiConnections, searchQuery, activeCategory]);

  const globalStats = useMemo(() => ({
    total: apiConnections.length,
    totalCalls: apiConnections.reduce((s,a) => s+(a.usage?.calls_total||0), 0),
    totalErrors: apiConnections.reduce((s,a) => s+(a.usage?.calls_error||0), 0),
    warningApis: apiConnections.filter(a => { const {status}=getApiStatus(a); return status!=='active'; }).length,
  }), [apiConnections]);
const openWizardForNew = () => {
    setWizardSeed({ kind: 'new' });
    setWizardOpen(true);
    setShowNewApiDropdown(false);
  };
  const openWizardForEdit = (api) => {
    setWizardSeed({ kind: 'edit', api });
    setWizardOpen(true);
    setShowNewApiDropdown(false);
  };
  const openWizardForTemplate = (template) => {
    setWizardSeed({ kind: 'template', template });
    setWizardOpen(true);
    setShowTemplateLibrary(false);
  };
  const closeWizard = () => {
    setWizardOpen(false);
    setWizardSeed(null);
  };
  const handleWizardSaved = () => {
    loadApiConnections();
  };

  const applyOpenAPIImport = async (importedApis) => {
    let ok=0,fail=0;
    for (const a of importedApis) { try { await createApiConnection(a); ok++; } catch(e){ fail++; } }
    setShowOpenAPIImport(false); await loadApiConnections();
    if (fail===0) toast.success(`✅ ${ok} endpoint importati`); else toast.warning(`⚠ ${ok} importati, ${fail} falliti`);
  };

  const handleDelete = async (apiId, apiName) => {
    const ok = await confirmModal.confirm({
      title: 'Elimina connessione API',
      message: `Eliminare "${apiName}"?`,
      confirmText: 'Elimina',
      variant: 'danger',
    });
    if (!ok) return;
    try { await deleteApiConnection(apiId); loadApiConnections(); }
    catch (e) {
      const cascade = handleCascade409(e);
      if (cascade) {
        toast.error(
          `Impossibile eliminare "${apiName}": ${formatCascadeDependencies(cascade.dependencies)}. Rimuovili prima.`,
          10000
        );
        return;
      }
      // Pre-fix (P2 frontend-pages): messaggio hardcoded 'Errore
      // eliminazione' senza propagare il dettaglio del server →
      // l'utente non distingue 403 (permessi) da 500 (DB down) da
      // 502 (gateway). Allineato al pattern di ConnectionManager.
      toastApiError(toast, e, 'Errore eliminazione connessione API');
    }
  };

  const handleQuickTest = (api) => {
    const mappable = (api.parameters||[]).filter(p=>p.mode!=='fixed');
    if (mappable.length===0) runQuickTest(api, {}); else setQuickTestInput({api, params:{...(api.test_data||{})}});
  };
  const runQuickTest = async (api, extraParams) => {
    try { const r=await testApiConnection(api.id,{test_params:{...(api.static_parameters||{}),...extraParams}}); setQuickTestInput(null); setQuickTestResult({api,result:r}); }
    catch(e) { setQuickTestInput(null); setQuickTestResult({api,result:{success:false,error:requestErrorMessage(e)}}); }
  };

  return (
    <div className="acm">
      {/* SIDEBAR */}
      <AppSidebar counts={{ apiConnections: apiConnections.length }} />

      {/* MAIN */}
      <div className="acm__main">
        <header className="acm__topbar">
          <button type="button" className="acm__back-btn" onClick={()=>navigate(-1)}>← Indietro</button>
          <div className="acm__page-title">Connettori Esterni</div>
          <div className="acm__topbar-right">
            <ThemeToggle classPrefix="acm" />
            {canCreateApi && (
              <div className="acm__new-wrapper">
                <button type="button" className="acm__btn acm__btn--primary" onClick={()=>setShowNewApiDropdown(!showNewApiDropdown)}>
                  ＋ Nuovo Connettore ▾
                </button>
                {showNewApiDropdown && (
                  <div className="acm__dropdown">
                    <div className="acm__dropdown-item" onClick={()=>{setShowTemplateLibrary(true);setShowNewApiDropdown(false);}}>
                      <span className="acm__dropdown-icon">📦</span>
                      <div><div className="acm__dropdown-label">Da Template</div><div className="acm__dropdown-sub">Nominatim, OSRM, Catasto…</div></div>
                    </div>
                    <div className="acm__dropdown-item" onClick={()=>{setShowOpenAPIImport(true);setShowNewApiDropdown(false);}}>
                      <span className="acm__dropdown-icon">📋</span>
                      <div><div className="acm__dropdown-label">Importa OpenAPI</div><div className="acm__dropdown-sub">Da URL o file JSON</div></div>
                    </div>
                    <div className="acm__dropdown-item" onClick={openWizardForNew}>
                      <span className="acm__dropdown-icon">✏️</span>
                      <div><div className="acm__dropdown-label">Configurazione Manuale</div><div className="acm__dropdown-sub">Wizard passo-passo</div></div>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </header>

        <div className="acm__content">
          <canvas ref={canvasRef} className="acm__hex-bg"/>

          {/* Stats */}
          <div className="acm__stats acm__layer-1">
            <div className="acm__stat acm__stat--total">
              <div className="acm__stat-val">{globalStats.total}</div>
              <div className="acm__stat-label">Connettori</div>
            </div>
            <div className="acm__stat acm__stat--calls">
              <div className="acm__stat-val">{globalStats.totalCalls.toLocaleString()}</div>
              <div className="acm__stat-label">Chiamate totali</div>
            </div>
            <div className="acm__stat acm__stat--err">
              <div className="acm__stat-val">{globalStats.totalErrors}</div>
              <div className="acm__stat-label">Errori</div>
            </div>
            {globalStats.warningApis > 0 && (
              <div className="acm__stat acm__stat--warn">
                <div className="acm__stat-val">⚠ {globalStats.warningApis}</div>
                <div className="acm__stat-label">Richiede attenzione</div>
              </div>
            )}
          </div>

          {/* Toolbar */}
          <div className="acm__toolbar acm__layer-1">
            <div className="acm__search">
              <span className="acm__search-icon">🔍</span>
              <input type="text" className="acm__search-input" placeholder="Cerca connettore…" aria-label="Cerca connettore" value={searchQuery} onChange={e=>setSearchQuery(e.target.value)} />
              {searchQuery && <button type="button" className="acm__search-clear" onClick={()=>setSearchQuery('')} aria-label="Pulisci ricerca">×</button>}
            </div>
            <div className="acm__cats">
              {CATEGORIES.map(cat=>(
                <button type="button" key={cat.id}
                  className={`acm__cat-btn${activeCategory===cat.id?' acm__cat-btn--active':''}`}
                  onClick={()=>setActiveCategory(cat.id)}>
                  {cat.icon} <span className="acm__cat-label">{cat.label}</span>
                </button>
              ))}
            </div>
          </div>

          {/* Grid */}
          <div className="acm__grid acm__layer-1">
            {loading ? (
              <div className="acm__loading">⬡ Caricamento connettori…</div>
            ) : filteredApis.length === 0 ? (
              <div className="acm__empty">
                <div className="acm__empty-icon">🔗</div>
                <div className="acm__empty-title">{apiConnections.length===0?'Nessun connettore configurato':'Nessun risultato'}</div>
                <div className="acm__empty-text">{apiConnections.length===0?'Configura il tuo primo connettore API':'Modifica i filtri di ricerca'}</div>
                {apiConnections.length===0 && canCreateApi && (
                  <div className="acm__empty-actions">
                    <button type="button" className="acm__btn acm__btn--ghost" onClick={()=>setShowTemplateLibrary(true)}>📦 Da Template</button>
                    <button type="button" className="acm__btn acm__btn--primary" onClick={openWizardForNew}>✏️ Manuale</button>
                  </div>
                )}
              </div>
            ) : (
              <>
                {filteredApis.map(api => (
                  <ApiConnectionCard
                    key={api.id}
                    connection={api}
                    canEdit={can.editApiConnection(user, api)}
                    canShare={can.shareResources(user)}
                    onEdit={openWizardForEdit}
                    onDelete={(a) => handleDelete(a.id, a.name)}
                    onQuickTest={handleQuickTest}
                    onShare={setSharingApi}
                  />
                ))}

                {/* Add card */}
                {canCreateApi && (
                <div className="acm__card acm__card--add">
                  <div className="acm__add-title">Aggiungi Connettore</div>
                  <div className="acm__add-btns">
                    <button type="button" className="acm__add-btn" onClick={()=>setShowTemplateLibrary(true)}>📦 Template</button>
                    <button type="button" className="acm__add-btn" onClick={()=>setShowOpenAPIImport(true)}>📋 OpenAPI</button>
                    <button type="button" className="acm__add-btn" onClick={openWizardForNew}>✏️ Manuale</button>
                    {can.createDataSource(user) && <button type="button" className="acm__add-btn" onClick={()=>setShowDataSourceConfig(true)}>📁 DataSource</button>}
                  </div>
                </div>
                )}
              </>
            )}
          </div>
        </div>
      </div>
{showTemplateLibrary && <TemplateLibraryModal onClose={()=>setShowTemplateLibrary(false)} onSelectTemplate={openWizardForTemplate} />}
      {showOpenAPIImport   && <OpenAPIImportModal   onClose={()=>setShowOpenAPIImport(false)}   onImport={applyOpenAPIImport} />}
      {showDataSourceConfig && (
        <DataSourceConfigModal
          onClose={()=>setShowDataSourceConfig(false)}
          onSave={async(dsConfig)=>{
            try { await createDataSource(toDataSourcePayload(dsConfig)); setShowDataSourceConfig(false); }
            catch(e){console.error(e);}
          }}
        />
      )}

      <ApiConnectionWizard
        isOpen={wizardOpen}
        seed={wizardSeed}
        onClose={closeWizard}
        onSaved={handleWizardSaved}
      />
{quickTestInput && (
        <div className="acm__overlay" onClick={()=>setQuickTestInput(null)}>
          <div
            ref={quickTestInputDialogRef}
            className="acm__modal acm__modal--sm"
            onClick={e=>e.stopPropagation()}
            role="dialog"
            aria-modal="true"
            aria-labelledby="acm-quicktest-input-title"
            tabIndex={-1}
          >
            <div className="acm__wizard-header">
              <div className="acm__wizard-title" id="acm-quicktest-input-title">🚀 Test — {quickTestInput.api.name}</div>
              <button type="button" className="acm__modal-close" onClick={()=>setQuickTestInput(null)} aria-label="Chiudi">✕</button>
            </div>
            <div className="acm__wizard-body">
              <div className="acm__qt-help">Inserisci valori di test per i parametri mappabili:</div>
              {(quickTestInput.api.parameters||[]).filter(p=>p.mode!=='fixed').map(p=>(
                <div key={p.name} className="acm__field-group">
                  <div className="acm__label">{p.name}{p.required?' *':''}{p.description&&<span className="acm__hint-meta">— {p.description}</span>}</div>
                  <input className="acm__input" autoComplete="off" value={quickTestInput.params[p.name]||''} onChange={e=>setQuickTestInput(prev=>({...prev,params:{...prev.params,[p.name]:e.target.value}}))} placeholder={p.example||`Valore per ${p.name}`}/>
                </div>
              ))}
            </div>
            <div className="acm__wizard-footer">
              <button type="button" className="acm__btn acm__btn--ghost" onClick={()=>setQuickTestInput(null)}>Annulla</button>
              <button type="button" className="acm__btn acm__btn--primary" onClick={()=>runQuickTest(quickTestInput.api,quickTestInput.params)}>🚀 Esegui</button>
            </div>
          </div>
        </div>
      )}
{quickTestResult && (
        <div className="acm__overlay" onClick={()=>setQuickTestResult(null)}>
          <div
            ref={quickTestResultDialogRef}
            className="acm__modal"
            onClick={e=>e.stopPropagation()}
            role="dialog"
            aria-modal="true"
            aria-labelledby="acm-quicktest-result-title"
            tabIndex={-1}
          >
            <div className="acm__wizard-header">
              <div className="acm__wizard-title" id="acm-quicktest-result-title">{quickTestResult.result.success?'✅':'❌'} Test — {quickTestResult.api.name}</div>
              <button type="button" className="acm__modal-close" onClick={()=>setQuickTestResult(null)} aria-label="Chiudi">✕</button>
            </div>
            <div className="acm__wizard-body">
              {quickTestResult.result.success ? (<>
                <div className="acm__info-box acm__info-box--ok">✅ API raggiungibile — HTTP {quickTestResult.result.status_code}</div>
                {quickTestResult.result.available_fields?.length > 0 && (<>
                  <div className="acm__wlabel acm__wlabel--mt-md">Campi estratti ({quickTestResult.result.available_fields.length})</div>
                  <div className="acm__fields-grid">
                    {quickTestResult.result.available_fields.map((f,i)=>(
                      <div key={f.name || `field-${i}`} className="acm__field-item">
                        <div className="acm__field-info">
                          <span className="acm__field-name">{f.name}</span>
                          <span className={`acm__field-type acm__field-type--${f.type}`}>{f.type}</span>
                          {f.sample_value!==undefined && <span className="acm__field-sample">{String(f.sample_value).substring(0,60)}</span>}
                        </div>
                      </div>
                    ))}
                  </div>
                </>)}
                {quickTestResult.result.response_preview && (<>
                  <div className="acm__wlabel acm__wlabel--mt-md">Anteprima risposta</div>
                  <pre className="acm__json-preview">{typeof quickTestResult.result.raw_response==='object'?JSON.stringify(quickTestResult.result.raw_response,null,2).substring(0,1500):quickTestResult.result.response_preview}</pre>
                </>)}
              </>) : (
                <div className="acm__info-box acm__info-box--err">❌ {quickTestResult.result.error||'Test fallito'}{quickTestResult.result.status_code&&<div className="acm__http-code">HTTP {quickTestResult.result.status_code}</div>}</div>
              )}
            </div>
            <div className="acm__wizard-footer">
              <button type="button" className="acm__btn acm__btn--ghost" onClick={()=>setQuickTestResult(null)}>Chiudi</button>
              <button type="button" className="acm__btn acm__btn--primary" onClick={()=>{const api=quickTestResult.api;setQuickTestResult(null);openWizardForEdit(api);}}>⚙️ Apri configurazione</button>
            </div>
          </div>
        </div>
      )}
{sharingApi && (
        <div className="acm__overlay" onClick={()=>setSharingApi(null)}>
          <div
            ref={shareDialogRef}
            className="acm__wizard acm__share-modal"
            onClick={e=>e.stopPropagation()}
            role="dialog"
            aria-modal="true"
            aria-labelledby="acm-share-title"
            tabIndex={-1}
          >
            <div className="acm__wizard-header">
              <div className="acm__wizard-title" id="acm-share-title">🔗 Condividi Connettore — {sharingApi.name}</div>
              <button type="button" className="acm__modal-close" onClick={()=>setSharingApi(null)} aria-label="Chiudi">✕</button>
            </div>
            <div className="acm__wizard-body acm__share-modal-body">
              <AllowedGroupsSelector
                embedded
                resourceType="api_connections"
                resourceId={sharingApi.id}
                onClose={()=>setSharingApi(null)}
              />
            </div>
          </div>
        </div>
      )}
      <ConfirmDialog {...confirmModal} />
    </div>
  );
}

export default ApiConnectionManager;
