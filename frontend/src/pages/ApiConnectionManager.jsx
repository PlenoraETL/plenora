/**
 * ApiConnectionManager.jsx — Connettori API Esterni
 * Redesign Plenora design system
 * Route: /external-connectors
 */
import React, { useState, useEffect, useMemo, useRef } from 'react';
import { useToast } from '../hooks/useToast';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import TemplateLibraryModal from '../components/arricchisci/TemplateLibraryModal';
import OpenAPIImportModal from '../components/arricchisci/OpenAPIImportModal';
import DataSourceConfigModal from '../components/arricchisci/DataSourceConfigModal';
import ResponseConfigBuilder from '../components/panels/ResponseConfigBuilder';
import { useHexCanvas } from '../utils/hexCanvas';
import AllowedGroupsSelector from '../components/shared/AllowedGroupsSelector';
import ConfirmDialog from '../components/common/ConfirmDialog';
import { useConfirmModal } from '../hooks/useConfirmModal';
import useDialogFocus from '../hooks/useDialogFocus';
import AppSidebar from '../components/AppSidebar';
import ThemeToggle from '../components/common/ThemeToggle';
import { handleCascade409, formatCascadeDependencies } from '../api/errors';
// C3.2 Phase 4: tutte le chiamate /api-connections e /datasources passano
// per il boundary api/ (ex shadow inline in cima al file).
import {
  listApiConnections,
  createApiConnection,
  updateApiConnection,
  deleteApiConnection,
  testApiConfig,
  testApiConnection,
} from '../api/apiConnections';
import { createDataSource } from '../api/datasources';
import { can, role } from '../utils/permissions';
import '../styles/ApiConnectionManager.css';
import '../components/panels/ResponseConfigBuilder.css';

// ── Constants ─────────────────────────────────────────────────────────────
const CATEGORIES = [
  { id:'all',        label:'Tutte',       icon:'🔗' },
  { id:'geocoding',  label:'Geocoding',   icon:'🌍' },
  { id:'routing',    label:'Routing',     icon:'🚗' },
  { id:'catasto',    label:'Catasto',     icon:'🏠' },
  { id:'datasource', label:'Data Source', icon:'📁' },
  { id:'custom',     label:'Custom',      icon:'⚙️' },
];

const CONNECTION_TYPES = [
  { id:'template',   label:'Da Template',     icon:'🍪', desc:'Template pre-configurato' },
  { id:'manual',     label:'Manuale',         icon:'✏️', desc:'URL e parametri manuali' },
  { id:'openapi',    label:'OpenAPI',         icon:'📋', desc:'Schema OpenAPI/Swagger' },
  { id:'rest_file',  label:'Processore File', icon:'📦', desc:'API REST con file in input e output' },
  { id:'datasource', label:'Data Source',     icon:'📁', desc:'SharePoint, FTP, S3…' },
];

// Status + quota colors
const STATUS_CFG = {
  active:   { cls:'acm__card--active',   dot:'var(--acm-ok)',   badge:'acm__status--ok'  },
  warning:  { cls:'acm__card--warning',  dot:'var(--acm-warn)', badge:'acm__status--warn'},
  critical: { cls:'acm__card--critical', dot:'var(--acm-err)',  badge:'acm__status--err' },
};

const EMPTY_FORM = {
  name:'', description:'', api_type:'enricher', category:'custom', connection_type:'manual',
  url:'', method:'GET', auth:{ type:'none' }, custom_headers:{},
  parameters:[], static_parameters:{}, output_mapping:[], schema_fields:[],
  response_preset:'', response_config:null,
  pagination:{ type:'', page_size:100, max_rows:10000, offset_param:'offset', limit_param:'limit', cursor_param:'cursor', cursor_path:'next_cursor', link_path:'next', start_page:0 },
  options:{ rate_limit:5, timeout:30, retry_on_error:true, retry_max:3 },
  batch:{ enabled:false, max_size:100, input_key:'items', input_format:'array', endpoint_override:'', method_override:'' },
  // ── REST File Processor (connection_type='rest_file') ──
  // file_fields: campi multipart che ricevono un file. Per ora solo source='node_input'
  //   (file in input al nodo del canvas).
  // static_fields: campi multipart con valore fisso (es. JSON inline per styles/mapping).
  //   Se content_type e' specificato, il valore viene inviato come file in-memory
  //   con quel content type; senza, come form-data semplice.
  request:  { body_type:'multipart', file_fields:[], static_fields:[], timeout:600 },
  response: { format:'binary_file', output_extension:'.zip' },
};

// ── Helpers ───────────────────────────────────────────────────────────────
const getApiIcon = (api) => {
  if (api.category) { const c = CATEGORIES.find(c => c.id === api.category); if (c) return c.icon; }
  const n = (api.name||'').toLowerCase(), u = (api.endpoint?.url||api.url||'').toLowerCase();
  if (n.includes('nominatim')||n.includes('geocod')||u.includes('nominatim')) return '🌍';
  if (n.includes('osrm')||n.includes('rout')) return '🚗';
  if (n.includes('catasto')||n.includes('omi')) return '🏠';
  if (n.includes('model')||n.includes('ml')) return '🧠';
  if (n.includes('sharepoint')||n.includes('ftp')) return '📁';
  return '⚙️';
};
const getApiStatus = (api) => {
  const quota = api.usage?.quota_used||0, ql = api.usage?.quota_limit||0;
  const qp = ql > 0 ? Math.round((quota/ql)*100) : 0;
  const er = api.usage?.calls_total > 0 ? ((api.usage?.calls_error||0)/api.usage.calls_total*100).toFixed(1) : 0;
  if (qp >= 90) return { status:'critical', statusLabel:'Quota esaurita', quotaPercent:qp, errorRate:er };
  if (qp >= 80 || er > 5) return { status:'warning', statusLabel:`Attenzione ${qp || er}%`, quotaPercent:qp, errorRate:er };
  return { status:'active', statusLabel:'Attiva', quotaPercent:qp, errorRate:er };
};
const formatTimeAgo = (d) => {
  if (!d) return 'Mai';
  const s = Math.floor((new Date()-new Date(d))/1000);
  if (s < 60) return 'Ora';
  if (s < 3600) return `${Math.floor(s/60)}m fa`;
  if (s < 86400) return `${Math.floor(s/3600)}h fa`;
  return `${Math.floor(s/86400)}g fa`;
};

// ── Main ──────────────────────────────────────────────────────────────────
function ApiConnectionManager() {
  const toast = useToast();
  const confirmModal = useConfirmModal();
  const navigate = useNavigate();
  const { user, logout } = useAuth();
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

  // Wizard
  const [showWizard, setShowWizard] = useState(false);
  const [wizardStep, setWizardStep] = useState(0);
  const [wizardStepError, setWizardStepError] = useState(null);
  const [editingApi, setEditingApi] = useState(null);
  const [formData,   setFormData]   = useState(EMPTY_FORM);
  const [customHeadersList, setCustomHeadersList] = useState([]);
  const [testParams, setTestParams] = useState({});
  const [testResult, setTestResult] = useState(null);
  const [testRawResponse, setTestRawResponse] = useState(null);
  const [testing,  setTesting]  = useState(false);
  const [showAdvancedConfig, setShowAdvancedConfig] = useState(false);

  // Quick test
  const [quickTestInput,  setQuickTestInput]  = useState(null);
  const [quickTestResult, setQuickTestResult] = useState(null);

  // Sharing (solo superadmin)
  const [sharingApi, setSharingApi] = useState(null);
  const isSuperadmin    = role.isSuperadmin(user);
  const canCreateApi    = can.createApiConnection(user);

  // Focus management per i 4 dialog inline (a11y batch 5).
  // Le close-callback usano inline arrow per evitare TDZ con
  // `closeWizard` definito piu' avanti nel componente.
  const wizardDialogRef          = useRef(null);
  const quickTestInputDialogRef  = useRef(null);
  const quickTestResultDialogRef = useRef(null);
  const shareDialogRef           = useRef(null);
  useDialogFocus(
    showWizard,
    () => { setShowWizard(false); setEditingApi(null); setWizardStep(0); setTestResult(null); setCustomHeadersList([]); },
    wizardDialogRef,
  );
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
    try { setLoading(true); const d = await listApiConnections(); setApiConnections(d.api_connections||[]); }
    catch(e) { console.error(e); } finally { setLoading(false); }
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

  const openWizard = (api=null) => {
    if (api) {
      setEditingApi(api);
      const eh = api.custom_headers||{};
      // IMPORTANTE: tutti gli oggetti complessi usano MERGE coi default
      // di EMPTY_FORM (spread-merge), non fallback-OR. Con fallback-OR,
      // se il server ritorna un oggetto parziale (es. options senza
      // timeout), i campi mancanti non esistono nell'UI state e al save
      // successivo vengono omessi dal payload → data loss silenziosa.
      // Con merge: i default coprono i campi mancanti, quelli presenti
      // nel server sovrascrivono.
      setFormData({
        name:api.name||'', description:api.description||'',
        api_type:api.api_type||'enricher', category:api.category||'custom',
        connection_type:api.connection_type||'manual',
        url:api.endpoint?.url||api.url||'', method:api.endpoint?.method||api.method||'GET',
        auth:api.auth||{type:'none'}, custom_headers:eh,
        parameters:api.parameters||[], static_parameters:api.static_parameters||{},
        output_mapping:api.output_mapping||[], schema_fields:api.schema_fields||[],
        response_preset:api.response_preset||'',
        response_config:api.response_config||null,
        // Merge per tutti gli oggetti strutturati
        pagination:{ ...EMPTY_FORM.pagination, ...(api.pagination||{}) },
        options:{ ...EMPTY_FORM.options, ...(api.options||{}) },
        batch:{ ...EMPTY_FORM.batch, ...(api.batch||{}) },
        request:{ ...EMPTY_FORM.request, ...(api.request||{}) },
        response:{ ...EMPTY_FORM.response, ...(api.response||{}) },
      });
      if (api.schema_fields?.length > 0) setTestResult({ success:true, available_fields:api.schema_fields.map(f=>({path:f.path,name:f.name,type:f.type})) });
      setCustomHeadersList(Object.entries(eh).map(([k,v])=>({key:k,value:v})));
      setWizardStep(api.parameters?.length > 0 || Object.keys(api.static_parameters||{}).length > 0 ? 2 : 1);
    } else {
      setEditingApi(null); setFormData(EMPTY_FORM); setCustomHeadersList([]); setWizardStep(0);
    }
    setTestResult(null); setTestParams({}); setShowWizard(true); setShowNewApiDropdown(false);
  };

  const applyTemplate = (tpl) => {
    setFormData({ name:tpl.display?.name||tpl.name||'', description:tpl.display?.short_description||'', api_type:tpl.config?.api_type||'enricher', category:tpl._meta?.category||'custom', connection_type:'template', url:tpl.config?.endpoint?.url||'', method:tpl.config?.endpoint?.method||'GET', auth:tpl.config?.auth||{type:'none'}, custom_headers:{}, parameters:(tpl.parameters||[]).map(p=>({name:p.name,type:p.type||'string',required:p.required??true,mode:p.hidden?'fixed':'mapped',label:p.label,description:p.description})), static_parameters:{...(tpl.config?.static_parameters||{}), ...(tpl.parameters||[]).filter(p=>p.default!=null).reduce((a,p)=>({...a,[p.name]:p.default}),{})}, output_mapping:(tpl.output_mapping||[]).map(o=>({path:o.path,column:o.column,type:o.type})), schema_fields:[], response_preset:'', response_config:null, pagination:EMPTY_FORM.pagination, options:{ rate_limit:tpl.config?.options?.rate_limit||5, timeout:tpl.config?.options?.timeout||30, retry_on_error:tpl.config?.options?.retry_on_error??true, retry_max:tpl.config?.options?.retry_max||3 } });
    setCustomHeadersList([]); setShowTemplateLibrary(false); setWizardStep(1); setShowWizard(true);
  };

  const applyOpenAPIImport = async (importedApis) => {
    let ok=0,fail=0;
    for (const a of importedApis) { try { await createApiConnection(a); ok++; } catch(e){ fail++; } }
    setShowOpenAPIImport(false); await loadApiConnections();
    if (fail===0) toast.success(`✅ ${ok} endpoint importati`); else toast.warning(`⚠ ${ok} importati, ${fail} falliti`);
  };

  const closeWizard = () => { setShowWizard(false); setEditingApi(null); setWizardStep(0); setTestResult(null); setCustomHeadersList([]); };

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
      toast.error('Errore eliminazione');
    }
  };

  const getCustomHeadersObject = () => { const h={}; customHeadersList.forEach(x=>{if(x.key?.trim())h[x.key.trim()]=x.value||'';}); return h; };

  const handleSave = async () => {
    try {
      const isMasked = v => typeof v==='string'&&v.includes('***');
      const sanitizeAuth = auth => { if(!auth)return auth; const s={...auth}; if(isMasked(s.password))delete s.password; if(isMasked(s.key_value))delete s.key_value; if(isMasked(s.client_secret))delete s.client_secret; return s; };
      const data = { name:formData.name, description:formData.description, api_type:formData.api_type, category:formData.category, connection_type:formData.connection_type||'manual', url:formData.url, method:formData.method, auth:sanitizeAuth(formData.auth), custom_headers:getCustomHeadersObject(), parameters:formData.parameters.map(p=>formData.api_type==='generator'?{...p,mode:'fixed',required:false}:p), static_parameters:formData.static_parameters, output_mapping:formData.output_mapping, schema_fields:formData.schema_fields||[], response_preset:formData.response_preset||undefined, response_config:formData.response_config||undefined, pagination:(formData.api_type==='generator'&&formData.pagination?.type)?formData.pagination:undefined, options:{...formData.options, parallel_workers:formData.options?.parallel_workers||1}, batch:formData.batch||{enabled:false} };
      // Per i Processori File: includi anche request/response nel payload.
      // Forziamo method=POST perche' multipart richiede POST.
      if (formData.connection_type==='rest_file') {
        data.method = 'POST';
        data.request = formData.request || { body_type:'multipart', file_fields:[], static_fields:[] };
        data.response = formData.response || { format:'binary_file', output_extension:'.zip' };
      }
      if (editingApi) await updateApiConnection(editingApi.id, data); else await createApiConnection(data);
      closeWizard(); loadApiConnections();
    } catch(e) { toast.error('Errore salvataggio'); }
  };

  const handleTest = async () => {
    setTesting(true); setTestResult(null); setTestRawResponse(null);
    try {
      const batch = formData.batch;
      let testPayload;
      if (batch?.enabled) {
        // Batch mode: wrappa i parametri nel formato batch per testare l'endpoint reale
        const inputKey = batch.input_key || 'items';
        const inputFormat = batch.input_format || 'array';
        const singleParams = {...formData.static_parameters, ...testParams};
        const batchItems = inputFormat === 'flat_array'
          ? Object.values(singleParams).filter(v => v != null)
          : [singleParams];
        testPayload = {
          url: batch.endpoint_override || formData.url,
          method: batch.method_override || 'POST',
          auth: formData.auth,
          custom_headers: getCustomHeadersObject(),
          test_params: { [inputKey]: batchItems },
          force_body: true,
          include_raw: true,
          batch_response: true,
        };
      } else {
        testPayload = { url:formData.url, method:formData.method, auth:formData.auth, custom_headers:getCustomHeadersObject(), test_params:{...formData.static_parameters,...testParams}, include_raw:true };
      }
      // C3.2: testApiConfig (no id) e' il draft test pre-save; non
      // confondere con testApiConnection(id, payload) che testa una
      // connessione gia' persistita.
      const result = await testApiConfig(testPayload);
      setTestResult(result);
      if (result.raw_response) setTestRawResponse(result.raw_response);
      if (result.success && result.available_fields) {
        setFormData(prev => { if (prev.output_mapping?.length>0) return prev; return { ...prev, output_mapping:result.available_fields.filter(f=>f.type==='geojson'||f.name==='id').map(f=>({path:f.path,column:f.name,type:f.type,selected:true})) }; });
      }
    } catch(e) { setTestResult({success:false,error:e.message}); }
    finally { setTesting(false); }
  };

  const handleQuickTest = (api) => {
    const mappable = (api.parameters||[]).filter(p=>p.mode!=='fixed');
    if (mappable.length===0) runQuickTest(api, {}); else setQuickTestInput({api, params:{...(api.test_data||{})}});
  };
  const runQuickTest = async (api, extraParams) => {
    try { const r=await testApiConnection(api.id,{test_params:{...(api.static_parameters||{}),...extraParams}}); setQuickTestInput(null); setQuickTestResult({api,result:r}); }
    catch(e) { setQuickTestInput(null); setQuickTestResult({api,result:{success:false,error:e.message}}); }
  };

  const addParameter    = () => setFormData(prev=>({...prev,parameters:[...prev.parameters,{name:'',type:'string',required:true,mode:'mapped'}]}));
  const updateParameter = (i,f,v) => setFormData(prev=>({...prev,parameters:prev.parameters.map((p,j)=>j===i?{...p,[f]:v}:p)}));
  const removeParameter = (i) => setFormData(prev=>({...prev,parameters:prev.parameters.filter((_,j)=>j!==i)}));
  const addCustomHeader    = () => setCustomHeadersList(prev=>[...prev,{key:'',value:''}]);
  const updateCustomHeader = (i,f,v) => setCustomHeadersList(prev=>prev.map((h,j)=>j===i?{...h,[f]:v}:h));
  const removeCustomHeader = (i) => setCustomHeadersList(prev=>prev.filter((_,j)=>j!==i));
  const toggleOutputField  = (field) => setFormData(prev=>{ const ex=prev.output_mapping.find(m=>m.path===field.path); return ex ? {...prev,output_mapping:prev.output_mapping.filter(m=>m.path!==field.path)} : {...prev,output_mapping:[...prev.output_mapping,{path:field.path,column:field.name,type:field.type,selected:true}]}; });
  const updateOutputColumn = (path,col) => setFormData(prev=>({...prev,output_mapping:prev.output_mapping.map(m=>m.path===path?{...m,column:col}:m)}));
  const handleResponseConfigChange = (config) => { if(config.response_preset) setFormData(prev=>({...prev,response_preset:config.response_preset,response_config:null})); else if(config.response_config) setFormData(prev=>({...prev,response_preset:'',response_config:config.response_config,output_mapping:config.response_config.mappings?.map(m=>({path:m.path,column:m.column,type:m.type||'string',selected:true}))||[]})); };

  const WIZARD_STEPS = ['Tipo','Info','Parametri','Auth','Batch','Test'];

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
                    <div className="acm__dropdown-item" onClick={()=>openWizard()}>
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
          <div className="acm__stats" style={{position:'relative',zIndex:1}}>
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
          <div className="acm__toolbar" style={{position:'relative',zIndex:1}}>
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
          <div className="acm__grid" style={{position:'relative',zIndex:1}}>
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
                    <button type="button" className="acm__btn acm__btn--primary" onClick={()=>openWizard()}>✏️ Manuale</button>
                  </div>
                )}
              </div>
            ) : (
              <>
                {filteredApis.map(api => {
                  const {status,statusLabel,quotaPercent} = getApiStatus(api);
                  const sc = STATUS_CFG[status];
                  const canEdit = api.can_edit !== false;
                  return (
                    <div key={api.id} className={`acm__card ${sc.cls}`}>
                      <div className="acm__card-bar"/>
                      <div className="acm__card-inner">
                        <div className="acm__card-header">
                          <div className="acm__card-icon">{getApiIcon(api)}</div>
                          <div className="acm__card-header-info">
                            <div className="acm__card-name">
                              {api.name}
                              {!canEdit && (
                                <span
                                  title="Solo un superadmin può modificare questa risorsa"
                                  style={{ marginLeft: 6, opacity: 0.7, cursor: 'help' }}
                                >
                                  🔒
                                </span>
                              )}
                            </div>
                            <div className="acm__card-badges">
                              <span className={`acm__badge acm__badge--type${api.api_type==='generator'?' acm__badge--gen':''}`}>
                                {api.api_type==='generator'?'Generatore':'Arricchitore'}
                              </span>
                              {api.connection_type && api.connection_type!=='manual' && (
                                <span className="acm__badge acm__badge--conn">
                                  {api.connection_type==='openapi'?'📋 OpenAPI':api.connection_type==='template'?'🍪 Template':api.connection_type==='datasource'?'📁 DataSource':null}
                                </span>
                              )}
                              <span className={`acm__badge ${sc.badge}`}>
                                <span className="acm__status-dot" style={{background:sc.dot}}/>
                                {statusLabel}
                              </span>
                            </div>
                          </div>
                        </div>
                        <div className="acm__card-desc">{api.description||'Nessuna descrizione'}</div>
                        <div className="acm__card-stats">
                          <div className="acm__card-stat"><span className="acm__card-stat-val">{(api.usage?.calls_total||0).toLocaleString()}</span><span className="acm__card-stat-label">chiamate</span></div>
                          {api.usage?.quota_limit > 0 && <div className="acm__card-stat"><span className="acm__card-stat-val">{quotaPercent}%</span><span className="acm__card-stat-label">quota</span></div>}
                          <div className="acm__card-stat"><span className="acm__card-stat-val">{formatTimeAgo(api.usage?.last_call)}</span><span className="acm__card-stat-label">ultimo</span></div>
                        </div>
                        {api.usage?.quota_limit > 0 && (
                          <div className="acm__quota-bar"><div className={`acm__quota-fill acm__quota-fill--${status}`} style={{width:`${Math.min(quotaPercent,100)}%`}}/></div>
                        )}
                        <div className="acm__card-footer">
                          <button type="button" className="acm__btn acm__btn--ghost acm__btn--sm" onClick={()=>handleQuickTest(api)}>🧪 Test</button>
                          <div style={{marginLeft:'auto',display:'flex',gap:4}}>
                            {canEdit && (
                              <button type="button" className="acm__icon-btn" title="Modifica" aria-label="Modifica connettore" onClick={()=>openWizard(api)}>⚙️</button>
                            )}
                            {isSuperadmin && (
                              <button type="button" className="acm__icon-btn" title="Gestisci condivisione cross-gruppo" aria-label="Gestisci condivisione cross-gruppo" onClick={()=>setSharingApi(api)}>🔗</button>
                            )}
                            {canEdit && (
                              <button type="button" className="acm__icon-btn acm__icon-btn--danger" title="Elimina" aria-label="Elimina connettore" onClick={()=>handleDelete(api.id,api.name)}>🗑</button>
                            )}
                          </div>
                        </div>
                      </div>
                    </div>
                  );
                })}

                {/* Add card */}
                {canCreateApi && (
                <div className="acm__card acm__card--add">
                  <div className="acm__add-title">Aggiungi Connettore</div>
                  <div className="acm__add-btns">
                    <button type="button" className="acm__add-btn" onClick={()=>setShowTemplateLibrary(true)}>📦 Template</button>
                    <button type="button" className="acm__add-btn" onClick={()=>setShowOpenAPIImport(true)}>📋 OpenAPI</button>
                    <button type="button" className="acm__add-btn" onClick={()=>openWizard()}>✏️ Manuale</button>
                    <button type="button" className="acm__add-btn" onClick={()=>setShowDataSourceConfig(true)}>📁 DataSource</button>
                  </div>
                </div>
                )}
              </>
            )}
          </div>
        </div>
      </div>

      {/* ── External modals (black box, unchanged) ─────────────────────── */}
      {showTemplateLibrary && <TemplateLibraryModal onClose={()=>setShowTemplateLibrary(false)} onSelectTemplate={applyTemplate} />}
      {showOpenAPIImport   && <OpenAPIImportModal   onClose={()=>setShowOpenAPIImport(false)}   onImport={applyOpenAPIImport} />}
      {showDataSourceConfig && (
        <DataSourceConfigModal
          onClose={()=>setShowDataSourceConfig(false)}
          onSave={async(dsConfig)=>{
            try { await createDataSource({name:dsConfig.name,description:dsConfig.description||'',ds_type:dsConfig.datasource?.type||dsConfig.source_type||'',config:JSON.stringify(dsConfig.datasource||dsConfig)}); setShowDataSourceConfig(false); }
            catch(e){console.error(e);}
          }}
        />
      )}
      {/* ── Wizard ─────────────────────────────────────────────────────── */}
      {showWizard && (
        <div className="acm__overlay" onClick={closeWizard}>
          <div
            ref={wizardDialogRef}
            className="acm__wizard"
            onClick={e=>e.stopPropagation()}
            role="dialog"
            aria-modal="true"
            aria-labelledby="acm-wizard-title"
            tabIndex={-1}
          >
            <div className="acm__wizard-header">
              <div className="acm__wizard-title" id="acm-wizard-title">{editingApi?`Modifica — ${editingApi.name}`:'Nuovo Connettore'}</div>
              <button type="button" className="acm__modal-close" onClick={closeWizard} aria-label="Chiudi">✕</button>
            </div>

            {/* Step bar */}
            <div className="acm__wizard-steps">
              {WIZARD_STEPS.map((s,i)=>(
                <div key={i} className={`acm__wstep${wizardStep===i?' acm__wstep--active':''}${wizardStep>i?' acm__wstep--done':''}`} onClick={()=>setWizardStep(i)}>
                  <div className="acm__wstep-num">{wizardStep>i?'✓':i+1}</div>
                  <div className="acm__wstep-label">{s}</div>
                </div>
              ))}
            </div>

            <div className="acm__wizard-body">

              {/* Step 0: Tipo */}
              {wizardStep===0 && (
                <div className="acm__wstep-content">
                  <div className="acm__wlabel">Tipo API</div>
                  <div className="acm__type-cards">
                    {[
                      {v:'enricher',icon:'🔄',label:'Arricchitore',desc:'Chiamata per ogni riga del dataset',ex:'Geocoding, lookup, validazione'},
                      {v:'generator',icon:'📥',label:'Generatore',desc:'Chiamata singola, genera nuovo dataset',ex:'POI, meteo, cataloghi'},
                    ].map(t=>(
                      <div key={t.v} className={`acm__type-card${formData.api_type===t.v?' acm__type-card--on':''}`} onClick={()=>setFormData({...formData,api_type:t.v})}>
                        <div className="acm__type-card-icon">{t.icon}</div>
                        <div className="acm__type-card-label">{t.label}</div>
                        <div className="acm__type-card-desc">{t.desc}</div>
                        <div className="acm__type-card-ex">{t.ex}</div>
                      </div>
                    ))}
                  </div>
                  <div className="acm__wlabel" style={{marginTop:16}}>Categoria</div>
                  <div className="acm__pill-group">
                    {CATEGORIES.filter(c=>c.id!=='all').map(c=>(
                      <button type="button" key={c.id}
                        className={`acm__pill${formData.category===c.id?' acm__pill--on':''}`}
                        onClick={()=>setFormData({...formData,category:c.id})}>
                        {c.icon} {c.label}
                      </button>
                    ))}
                  </div>
                  <div className="acm__wlabel" style={{marginTop:16}}>Tipo connessione</div>
                  <div className="acm__conn-type-list">
                    {CONNECTION_TYPES.map(ct=>(
                      <div key={ct.id} className={`acm__conn-type${formData.connection_type===ct.id?' acm__conn-type--on':''}`} onClick={()=>setFormData({...formData,connection_type:ct.id})}>
                        <span className="acm__conn-type-icon">{ct.icon}</span>
                        <div><div className="acm__conn-type-label">{ct.label}</div><div className="acm__conn-type-desc">{ct.desc}</div></div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Step 1: Info */}
              {wizardStep===1 && (
                <div className="acm__wstep-content">
                  <div className="acm__field-group">
                    <div className="acm__label">Nome API *</div>
                    <input className="acm__input" autoComplete="off" value={formData.name} onChange={e=>setFormData({...formData,name:e.target.value})} placeholder="Es: Nominatim Geocoding" />
                  </div>
                  <div className="acm__field-group">
                    <div className="acm__label">Descrizione</div>
                    <textarea className="acm__textarea" value={formData.description} onChange={e=>setFormData({...formData,description:e.target.value})} placeholder="Descrizione opzionale…" rows={2}/>
                  </div>
                  <div className="acm__form-row">
                    <div className="acm__field-group" style={{flex:3}}>
                      <div className="acm__label">URL Endpoint *</div>
                      <input className="acm__input acm__input--mono" autoComplete="off" value={formData.url} onChange={e=>setFormData({...formData,url:e.target.value})} placeholder="https://api.example.com/endpoint"/>
                    </div>
                    <div className="acm__field-group" style={{flex:1}}>
                      <div className="acm__label">Metodo</div>
                      <select className="acm__select" value={formData.method} onChange={e=>setFormData({...formData,method:e.target.value})}>
                        <option value="GET">GET</option>
                        <option value="POST">POST</option>
                      </select>
                    </div>
                  </div>
                  <details className="acm__details">
                    <summary>⚙️ Opzioni avanzate</summary>
                    <div className="acm__form-row" style={{marginTop:12,gap:12}}>
                      {[['Rate Limit (req/s)','rate_limit',1,100],['Timeout (sec)','timeout',5,300],['Max Retry','retry_max',0,10]].map(([label,key,min,max])=>(
                        <div key={key} className="acm__field-group">
                          <div className="acm__label">{label}</div>
                          <input className="acm__input" type="number" min={min} max={max} value={formData.options[key]} onChange={e=>setFormData({...formData,options:{...formData.options,[key]:parseInt(e.target.value)||min}})}/>
                        </div>
                      ))}
                    </div>
                  {formData.api_type==='enricher' && (
                    <div className="acm__field-row" style={{marginTop:8}}>
                      <label className="acm__label">🔀 Worker paralleli
                        <span style={{fontWeight:400,marginLeft:6,opacity:.65,fontSize:12}}>— richieste simultanée per riga</span>
                      </label>
                      <input type="number" className="acm__input" min={1} max={16}
                        value={formData.options?.parallel_workers||1}
                        onChange={e=>setFormData(f=>({...f,options:{...f.options,parallel_workers:Math.max(1,Math.min(16,parseInt(e.target.value)||1))}}))}/>
                      <div className="acm__field-hint">1 = sequenziale · 2-4 consigliato · max 16. Rispetta sempre il Rate Limit.</div>
                    </div>
                  )}
                  </details>
                </div>
              )}

              {/* Step 2: Parametri */}
              {wizardStep===2 && (
                <div className="acm__wstep-content">
                  {wizardStepError && (
                    <div className="acm__validation-error" style={{background:'#fff0f0',border:'1px solid #f5a0a0',borderRadius:6,padding:'10px 14px',marginBottom:10,color:'#c0392b',fontSize:13,display:'flex',alignItems:'center',gap:8}}>
                      <span>⚠️</span><span>{wizardStepError}</span>
                    </div>
                  )}

                  {/* ── REST FILE PROCESSOR: campi multipart + risposta binaria ── */}
                  {formData.connection_type==='rest_file' ? (
                    <>
                      <div className="acm__info-box">📦 Configura i campi multipart che invierai al servizio REST e il formato del file di output.</div>

                      <div className="acm__field-group">
                        <div className="acm__label">Estensione file di output *</div>
                        <input className="acm__input acm__input--mono" style={{maxWidth:160}}
                          value={formData.response?.output_extension||'.zip'}
                          onChange={e=>setFormData({...formData,response:{...(formData.response||{format:'binary_file'}),output_extension:e.target.value}})}
                          placeholder=".zip"/>
                        <div className="acm__field-hint">Estensione del file restituito dal servizio (es. .zip, .json, .pdf)</div>
                      </div>

                      <div className="acm__field-group" style={{marginTop:18}}>
                        <div className="acm__label">📎 File da inviare (multipart)</div>
                        <div className="acm__field-hint" style={{marginBottom:8}}>
                          <strong>Sorgente nodo</strong>: il file arriva dal file_source collegato via edge (il "file principale", uno solo per nodo).
                          <br/><strong>Extra (upload inline)</strong>: l'utente carica il file direttamente nel nodo canvas (es. Excel opzionale per il servizio IMDF).
                        </div>
                        <div className="acm__params-list">
                          {(formData.request?.file_fields||[]).map((ff,i)=>(
                            <div key={i} style={{display:'flex',flexDirection:'column',gap:6,padding:'10px 12px',border:'1px solid var(--acm-border)',borderRadius:8,marginBottom:8}}>
                              <div className="acm__param-row" style={{margin:0}}>
                                <input className="acm__input" value={ff.name||''}
                                  onChange={e=>{const arr=[...(formData.request?.file_fields||[])]; arr[i]={...arr[i],name:e.target.value}; setFormData({...formData,request:{...(formData.request||{}),file_fields:arr}});}}
                                  placeholder="Nome campo (es: file, excel)"/>
                                <select className="acm__select" style={{maxWidth:180}}
                                  value={ff.source||'node_input'}
                                  onChange={e=>{const arr=[...(formData.request?.file_fields||[])]; arr[i]={...arr[i],source:e.target.value}; setFormData({...formData,request:{...(formData.request||{}),file_fields:arr}});}}>
                                  <option value="node_input">Sorgente nodo (edge)</option>
                                  <option value="extra_input">Extra (upload inline)</option>
                                </select>
                                <label style={{display:'flex',alignItems:'center',gap:6,fontSize:13,whiteSpace:'nowrap'}}>
                                  <input type="checkbox" checked={!!ff.required}
                                    onChange={e=>{const arr=[...(formData.request?.file_fields||[])]; arr[i]={...arr[i],required:e.target.checked}; setFormData({...formData,request:{...(formData.request||{}),file_fields:arr}});}}/>
                                  Obbligatorio
                                </label>
                                <button type="button" className="acm__del-btn"
                                  onClick={()=>{const arr=[...(formData.request?.file_fields||[])]; arr.splice(i,1); setFormData({...formData,request:{...(formData.request||{}),file_fields:arr}});}}>🗑</button>
                              </div>
                              {ff.source === 'extra_input' && (
                                <div className="acm__param-row" style={{margin:0}}>
                                  <input className="acm__input acm__input--mono"
                                    value={ff.accept||''}
                                    onChange={e=>{const arr=[...(formData.request?.file_fields||[])]; arr[i]={...arr[i],accept:e.target.value}; setFormData({...formData,request:{...(formData.request||{}),file_fields:arr}});}}
                                    placeholder="Accept (es: .xlsx,.xls) — opzionale"/>
                                  <input className="acm__input"
                                    value={ff.label||''}
                                    onChange={e=>{const arr=[...(formData.request?.file_fields||[])]; arr[i]={...arr[i],label:e.target.value}; setFormData({...formData,request:{...(formData.request||{}),file_fields:arr}});}}
                                    placeholder="Label UI (es: Excel arricchimento)"/>
                                </div>
                              )}
                            </div>
                          ))}
                          <button type="button" className="acm__add-param-btn"
                            onClick={()=>setFormData({...formData,request:{...(formData.request||{body_type:'multipart'}),file_fields:[...(formData.request?.file_fields||[]),{name:'',source:'node_input',required:false}]}})}>＋ Aggiungi campo file</button>
                        </div>
                      </div>

                      <div className="acm__field-group" style={{marginTop:18}}>
                        <div className="acm__label">📝 Campi statici (multipart)</div>
                        <div className="acm__field-hint" style={{marginBottom:8}}>Valori fissi inviati a ogni chiamata. Se imposti un Content-Type il valore viene inviato come file in-memory (utile per JSON inline tipo <code>styles</code> e <code>mapping</code>).</div>
                        <div className="acm__params-list">
                          {(formData.request?.static_fields||[]).map((sf,i)=>(
                            <div key={i} style={{display:'flex',flexDirection:'column',gap:6,padding:'10px 12px',border:'1px solid var(--acm-border)',borderRadius:8,marginBottom:8}}>
                              <div className="acm__param-row" style={{margin:0}}>
                                <input className="acm__input" value={sf.name||''}
                                  onChange={e=>{const arr=[...(formData.request?.static_fields||[])]; arr[i]={...arr[i],name:e.target.value}; setFormData({...formData,request:{...(formData.request||{}),static_fields:arr}});}}
                                  placeholder="Nome campo (es: styles)"/>
                                <input className="acm__input acm__input--mono" style={{maxWidth:200}} value={sf.content_type||''}
                                  onChange={e=>{const arr=[...(formData.request?.static_fields||[])]; arr[i]={...arr[i],content_type:e.target.value}; setFormData({...formData,request:{...(formData.request||{}),static_fields:arr}});}}
                                  placeholder="Content-Type (opz.)"/>
                                <button type="button" className="acm__del-btn"
                                  onClick={()=>{const arr=[...(formData.request?.static_fields||[])]; arr.splice(i,1); setFormData({...formData,request:{...(formData.request||{}),static_fields:arr}});}}>🗑</button>
                              </div>
                              <textarea className="acm__textarea acm__input--mono" rows={4}
                                value={sf.value||''}
                                onChange={e=>{const arr=[...(formData.request?.static_fields||[])]; arr[i]={...arr[i],value:e.target.value}; setFormData({...formData,request:{...(formData.request||{}),static_fields:arr}});}}
                                placeholder='Valore (es. {"layer":"foo","color":[255,0,0]})'/>
                            </div>
                          ))}
                          <button type="button" className="acm__add-param-btn"
                            onClick={()=>setFormData({...formData,request:{...(formData.request||{body_type:'multipart'}),static_fields:[...(formData.request?.static_fields||[]),{name:'',value:'',content_type:''}]}})}>＋ Aggiungi campo statico</button>
                        </div>
                      </div>

                      <div className="acm__field-group" style={{marginTop:18}}>
                        <div className="acm__label">⏱ Timeout (secondi)</div>
                        <input type="number" className="acm__input" style={{maxWidth:160}} min={10} max={3600}
                          value={formData.request?.timeout||600}
                          onChange={e=>setFormData({...formData,request:{...(formData.request||{}),timeout:Math.max(10,Math.min(3600,parseInt(e.target.value)||600))}})}/>
                        <div className="acm__field-hint">Tempo massimo di attesa della risposta. Default 600s (10 min).</div>
                      </div>
                    </>
                  ) : (
                  <>
                  <div className="acm__info-box">{formData.api_type==='enricher'?<>📥 <strong>Mappato</strong> = da colonna &nbsp;|&nbsp; 📌 <strong>Fisso</strong> = costante</>:<>ℹ️ Parametri fissi per la chiamata API</>}</div>
                  <div className="acm__params-list">
                    {formData.parameters.map((p,i)=>(
                      <div key={i} className="acm__param-row">
                        <input className="acm__input" value={p.name} onChange={e=>updateParameter(i,'name',e.target.value)} placeholder="Nome"/>
                        <select className="acm__select" value={p.type} onChange={e=>updateParameter(i,'type',e.target.value)}>
                          <option value="string">Testo</option><option value="number">Numero</option><option value="boolean">Bool</option>
                        </select>
                        {formData.api_type==='enricher' && (
                          <select className={`acm__select acm__select--${p.mode||'mapped'}`} value={p.mode||'mapped'} onChange={e=>updateParameter(i,'mode',e.target.value)}>
                            <option value="mapped">📥 Mappato</option><option value="fixed">📌 Fisso</option>
                          </select>
                        )}
                        {(p.mode==='fixed'||formData.api_type==='generator') && (
                          <input className="acm__input" value={formData.static_parameters[p.name]||''} onChange={e=>setFormData({...formData,static_parameters:{...formData.static_parameters,[p.name]:e.target.value}})} placeholder="Valore fisso"/>
                        )}
                        <button type="button" className="acm__del-btn" onClick={()=>removeParameter(i)} aria-label="Rimuovi parametro">🗑</button>
                      </div>
                    ))}
                    <button type="button" className="acm__add-param-btn" onClick={addParameter}>＋ Aggiungi parametro</button>
                  </div>

                  {formData.api_type==='generator' && (
                    <div style={{marginTop:20}}>
                      <div className="acm__wlabel">📄 Paginazione</div>
                      <div className="acm__param-row">
                        <span className="acm__param-label">Tipo</span>
                        <select className="acm__select" value={formData.pagination?.type||''} onChange={e=>setFormData({...formData,pagination:{...formData.pagination,type:e.target.value}})}>
                          <option value="">Nessuna</option><option value="offset">Offset/Limit</option><option value="cursor">Cursor/Token</option><option value="link">Link (next URL)</option>
                        </select>
                      </div>
                      {formData.pagination?.type && (
                        <div className="acm__pagination-fields">
                          <div className="acm__param-row">
                            <span className="acm__param-label">Righe/pagina</span>
                            <input className="acm__input acm__input--sm" type="number" min="1" max="10000" value={formData.pagination?.page_size??100} onChange={e=>setFormData({...formData,pagination:{...formData.pagination,page_size:parseInt(e.target.value)||100}})}/>
                            <span className="acm__param-label">Max righe</span>
                            <input className="acm__input acm__input--sm" type="number" min="1" value={formData.pagination?.max_rows??10000} onChange={e=>setFormData({...formData,pagination:{...formData.pagination,max_rows:parseInt(e.target.value)||10000}})}/>
                          </div>
                          {formData.pagination?.type==='offset' && (<>
                            <div className="acm__param-row">
                              <span className="acm__param-label">Param offset</span>
                              <input className="acm__input" value={formData.pagination?.offset_param??'offset'} onChange={e=>setFormData({...formData,pagination:{...formData.pagination,offset_param:e.target.value}})} placeholder="offset"/>
                              <span className="acm__param-label">Param limit</span>
                              <input className="acm__input" value={formData.pagination?.limit_param??'limit'} onChange={e=>setFormData({...formData,pagination:{...formData.pagination,limit_param:e.target.value}})} placeholder="limit"/>
                            </div>
                          </>)}
                          {formData.pagination?.type==='cursor' && (
                            <div className="acm__param-row">
                              <span className="acm__param-label">Param cursor</span>
                              <input className="acm__input" value={formData.pagination?.cursor_param??'cursor'} onChange={e=>setFormData({...formData,pagination:{...formData.pagination,cursor_param:e.target.value}})} placeholder="cursor"/>
                              <span className="acm__param-label">Path risposta</span>
                              <input className="acm__input" value={formData.pagination?.cursor_path??'next_cursor'} onChange={e=>setFormData({...formData,pagination:{...formData.pagination,cursor_path:e.target.value}})} placeholder="next_cursor"/>
                            </div>
                          )}
                          {formData.pagination?.type==='link' && (
                            <div className="acm__param-row">
                              <span className="acm__param-label">Path URL next</span>
                              <input className="acm__input" value={formData.pagination?.link_path??'next'} onChange={e=>setFormData({...formData,pagination:{...formData.pagination,link_path:e.target.value}})} placeholder="next"/>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                  </>
                  )}
                </div>
              )}

              {/* Step 3: Auth */}
              {wizardStep===3 && (
                <div className="acm__wstep-content">
                  <div className="acm__wlabel">🔐 Autenticazione</div>
                  <div className="acm__auth-opts">
                    {[{value:'none',label:'Nessuna',icon:'🔓'},{value:'api_key',label:'API Key',icon:'🔑'},{value:'basic_auth',label:'Basic Auth',icon:'👤'},{value:'oauth2_password',label:'OAuth2 Password',icon:'🔐'},{value:'oauth2_client_credentials',label:'OAuth2 Client Creds',icon:'🏢'},{value:'arcgis_token',label:'ArcGIS Token',icon:'🌐'}].map(opt=>(
                      <label key={opt.value} className={`acm__auth-opt${formData.auth.type===opt.value?' acm__auth-opt--on':''}`}>
                        <input type="radio" name="authType" value={opt.value} checked={formData.auth.type===opt.value} onChange={e=>setFormData({...formData,auth:{type:e.target.value}})}/>
                        <span>{opt.icon}</span> {opt.label}
                      </label>
                    ))}
                  </div>

                  {formData.auth.type==='api_key' && (
                    <div className="acm__auth-config">
                      <div className="acm__form-row">
                        <div className="acm__field-group"><div className="acm__label">Nome chiave *</div><input className="acm__input" value={formData.auth.key_name||''} onChange={e=>setFormData({...formData,auth:{...formData.auth,key_name:e.target.value}})} placeholder="X-API-Key"/></div>
                        <div className="acm__field-group"><div className="acm__label">Valore *</div><input type="password" className="acm__input" value={formData.auth.key_value||''} onChange={e=>setFormData({...formData,auth:{...formData.auth,key_value:e.target.value}})} placeholder="La tua API key"/></div>
                      </div>
                      <div className="acm__auth-pos">
                        {['header','query'].map(loc=>(
                          <label key={loc} className={`acm__auth-opt${(formData.auth.location||'header')===loc?' acm__auth-opt--on':''}`}>
                            <input type="radio" name="keyLoc" value={loc} checked={(formData.auth.location||'header')===loc} onChange={e=>setFormData({...formData,auth:{...formData.auth,location:e.target.value}})}/> {loc==='header'?'Header':'Query param'}
                          </label>
                        ))}
                      </div>
                    </div>
                  )}
                  {(formData.auth.type==='oauth2_password'||formData.auth.type==='basic_auth') && (
                    <div className="acm__auth-config">
                      {formData.auth.type==='oauth2_password' && <div className="acm__field-group"><div className="acm__label">Token URL *</div><input className="acm__input acm__input--mono" value={formData.auth.token_url||''} onChange={e=>setFormData({...formData,auth:{...formData.auth,token_url:e.target.value}})} placeholder="https://…/token"/></div>}
                      <div className="acm__form-row">
                        <div className="acm__field-group"><div className="acm__label">Username *</div><input className="acm__input" value={formData.auth.username||''} onChange={e=>setFormData({...formData,auth:{...formData.auth,username:e.target.value}})} placeholder="Username"/></div>
                        <div className="acm__field-group"><div className="acm__label">Password *</div><input type="password" className="acm__input" value={formData.auth.password||''} onChange={e=>setFormData({...formData,auth:{...formData.auth,password:e.target.value}})} placeholder="••••••••"/></div>
                      </div>
                    </div>
                  )}
                  {formData.auth.type==='oauth2_client_credentials' && (
                    <div className="acm__auth-config">
                      <div className="acm__field-group"><div className="acm__label">Token URL *</div><input className="acm__input acm__input--mono" value={formData.auth.token_url||''} onChange={e=>setFormData({...formData,auth:{...formData.auth,token_url:e.target.value}})} placeholder="https://auth.example.com/oauth/token"/></div>
                      <div className="acm__form-row">
                        <div className="acm__field-group"><div className="acm__label">Client ID *</div><input className="acm__input" value={formData.auth.client_id||''} onChange={e=>setFormData({...formData,auth:{...formData.auth,client_id:e.target.value}})} placeholder="client_id"/></div>
                        <div className="acm__field-group"><div className="acm__label">Client Secret *</div><input type="password" className="acm__input" value={formData.auth.client_secret||''} onChange={e=>setFormData({...formData,auth:{...formData.auth,client_secret:e.target.value}})} placeholder="••••••••"/></div>
                      </div>
                      <div className="acm__field-group"><div className="acm__label">Scope (opzionale)</div><input className="acm__input" value={formData.auth.scope||''} onChange={e=>setFormData({...formData,auth:{...formData.auth,scope:e.target.value}})} placeholder="read write"/></div>
                    </div>
                  )}
                  {formData.auth.type==='arcgis_token' && (
                    <div className="acm__auth-config">
                      <div className="acm__info-box" style={{marginBottom:12}}>
                        ℹ️ ArcGIS Token Service (ESRI). Il token viene generato chiamando <code>generateToken</code>,
                        cachato in memoria, e iniettato come query param <code>?token=...</code> nelle chiamate API.
                        Auto-renewal alla scadenza.
                      </div>
                      <div className="acm__field-group">
                        <div className="acm__label">Token URL *</div>
                        <input className="acm__input acm__input--mono"
                          value={formData.auth.token_url||''}
                          onChange={e=>setFormData({...formData,auth:{...formData.auth,token_url:e.target.value}})}
                          placeholder="https://server.tld/arcgis/admin/generateToken"/>
                      </div>
                      <div className="acm__form-row">
                        <div className="acm__field-group">
                          <div className="acm__label">Username *</div>
                          <input className="acm__input"
                            value={formData.auth.username||''}
                            onChange={e=>setFormData({...formData,auth:{...formData.auth,username:e.target.value}})}
                            placeholder="utente"/>
                        </div>
                        <div className="acm__field-group">
                          <div className="acm__label">Password *</div>
                          <input type="password" className="acm__input"
                            value={formData.auth.password||''}
                            onChange={e=>setFormData({...formData,auth:{...formData.auth,password:e.target.value}})}
                            placeholder="••••••••"/>
                        </div>
                      </div>
                      <div className="acm__form-row">
                        <div className="acm__field-group">
                          <div className="acm__label">Client</div>
                          <select className="acm__input"
                            value={formData.auth.client||'requestip'}
                            onChange={e=>setFormData({...formData,auth:{...formData.auth,client:e.target.value}})}>
                            <option value="requestip">requestip (IP del server URBY)</option>
                            <option value="referer">referer (URL specifico)</option>
                            <option value="ip">ip (IP custom)</option>
                          </select>
                        </div>
                        <div className="acm__field-group">
                          <div className="acm__label">Scadenza (minuti)</div>
                          <input type="number" min={1} max={1440} className="acm__input"
                            value={formData.auth.expiration||60}
                            onChange={e=>setFormData({...formData,auth:{...formData.auth,expiration:parseInt(e.target.value)||60}})}/>
                        </div>
                      </div>
                      {formData.auth.client==='referer' && (
                        <div className="acm__field-group">
                          <div className="acm__label">Referer URL *</div>
                          <input className="acm__input acm__input--mono"
                            value={formData.auth.referer||''}
                            onChange={e=>setFormData({...formData,auth:{...formData.auth,referer:e.target.value}})}
                            placeholder="https://myapp.example.com"/>
                        </div>
                      )}
                      {formData.auth.client==='ip' && (
                        <div className="acm__field-group">
                          <div className="acm__label">IP *</div>
                          <input className="acm__input acm__input--mono"
                            value={formData.auth.ip||''}
                            onChange={e=>setFormData({...formData,auth:{...formData.auth,ip:e.target.value}})}
                            placeholder="123.45.67.89"/>
                        </div>
                      )}
                    </div>
                  )}

                  <div className="acm__wlabel" style={{marginTop:20}}>📋 Headers personalizzati</div>
                  <div className="acm__params-list">
                    {customHeadersList.map((h,i)=>(
                      <div key={i} className="acm__param-row">
                        <input className="acm__input" value={h.key} onChange={e=>updateCustomHeader(i,'key',e.target.value)} placeholder="Nome header"/>
                        <input className="acm__input" value={h.value} onChange={e=>updateCustomHeader(i,'value',e.target.value)} placeholder="Valore"/>
                        <button type="button" className="acm__del-btn" onClick={()=>removeCustomHeader(i)} aria-label="Rimuovi header">🗑</button>
                      </div>
                    ))}
                    <button type="button" className="acm__add-param-btn" onClick={addCustomHeader}>＋ Aggiungi header</button>
                  </div>
                </div>
              )}

              {/* Step 4: Test */}
              {wizardStep===4 && (
                <div className="acm__wstep-content">
                  <div className="acm__wlabel">⚡ Configurazione Batch</div>
                  <div className="acm__info-box" style={{marginBottom:12}}>
                    ℹ️ Abilita solo se l'API accetta un array di input in una singola chiamata (es. <code>{`{"items":[...]}`}</code>).
                    Per API standard (una chiamata per riga) lascia disabilitato.
                  </div>
                  <label className="acm__toggle-label" style={{marginBottom:16}}>
                    <input type="checkbox" checked={formData.batch?.enabled||false}
                      onChange={e=>setFormData(f=>({...f, batch:{...f.batch, enabled:e.target.checked}}))}/>
                    <span>Batch mode abilitato</span>
                  </label>
                  {formData.batch?.enabled && (<>
                    <div className="acm__field-row">
                      <label className="acm__label">Max righe per chiamata</label>
                      <input type="number" className="acm__input" min={1} max={10000}
                        value={formData.batch?.max_size||100}
                        onChange={e=>setFormData(f=>({...f, batch:{...f.batch, max_size:parseInt(e.target.value)||100}}))}/>
                    </div>
                    <div className="acm__field-row">
                      <label className="acm__label">Chiave array nel payload</label>
                      <input type="text" className="acm__input" placeholder="items"
                        value={formData.batch?.input_key||'items'}
                        onChange={e=>setFormData(f=>({...f, batch:{...f.batch, input_key:e.target.value}}))}/>
                      <div className="acm__field-hint">Es: <code>items</code> → <code>{`{"items":[...]}`}</code></div>
                    </div>
                    <div className="acm__field-row">
                      <label className="acm__label">Formato input</label>
                      <select className="acm__input"
                        value={formData.batch?.input_format||'array'}
                        onChange={e=>setFormData(f=>({...f, batch:{...f.batch, input_format:e.target.value}}))}>
                        <option value="array">Array di oggetti — {`[{"city":"Rome"}, ...]`}</option>
                        <option value="flat_array">Array piatto — {`["Rome", "Milan", ...]`}</option>
                      </select>
                    </div>
                    <div className="acm__field-row">
                      <label className="acm__label">Endpoint override (opzionale)</label>
                      <input type="text" className="acm__input" placeholder="Lascia vuoto per usare URL principale"
                        value={formData.batch?.endpoint_override||''}
                        onChange={e=>setFormData(f=>({...f, batch:{...f.batch, endpoint_override:e.target.value}}))}/>
                    </div>
                    <div className="acm__field-row">
                      <label className="acm__label">Metodo HTTP override (opzionale)</label>
                      <select className="acm__input"
                        value={formData.batch?.method_override||''}
                        onChange={e=>setFormData(f=>({...f, batch:{...f.batch, method_override:e.target.value}}))}>
                        <option value="">Usa metodo principale ({formData.method})</option>
                        <option value="POST">POST</option>
                        <option value="GET">GET</option>
                        <option value="PUT">PUT</option>
                      </select>
                    </div>
                  </>)}
                </div>
              )}
              {wizardStep===5 && (
                <div className="acm__wstep-content">
                  <div className="acm__wlabel">📝 Dati di test</div>
                  {formData.parameters.length===0 ? (
                    <div className="acm__info-box">ℹ️ Nessun parametro — l'endpoint verrà chiamato direttamente.</div>
                  ) : formData.parameters.map((p,i)=>{
                    const isFixed = p.mode==='fixed'||formData.api_type==='generator';
                    return (
                      <div key={i} className="acm__test-param">
                        <label className="acm__test-param-label">{p.name} <span className={`acm__mini-badge${isFixed?' acm__mini-badge--fixed':''}`}>{isFixed?'📌 fisso':'📥 mappato'}</span></label>
                        <input type={p.type==='number'?'number':'text'} className={`acm__input${isFixed?' acm__input--dim':''}`} value={isFixed?(formData.static_parameters[p.name]||''):(testParams[p.name]||'')} onChange={e=>!isFixed&&setTestParams({...testParams,[p.name]:e.target.value})} disabled={isFixed} placeholder={isFixed?'':`Valore test per ${p.name}`}/>
                      </div>
                    );
                  })}
                  <button type="button" className="acm__test-run-btn" onClick={handleTest} disabled={testing}>
                    {testing?'⏳ Test in corso…':'🚀 Esegui Test'}
                  </button>

                  {testResult && (
                    <div className={`acm__test-result${testResult.success?' acm__test-result--ok':' acm__test-result--err'}`}>
                      <div className="acm__test-result-title">{testResult.success?'✅ Test riuscito!':'❌ Test fallito'}</div>
                      {testResult.success && (<>
                        <label className="acm__toggle-label">
                          <input type="checkbox" checked={showAdvancedConfig} onChange={e=>setShowAdvancedConfig(e.target.checked)}/>
                          <span>🔧 Struttura dati complessa (array annidati)</span>
                        </label>
                        {showAdvancedConfig && testRawResponse && (
                          <div className="acm__response-builder">
                            <ResponseConfigBuilder sampleData={testRawResponse} initialConfig={{preset:formData.response_preset,root_path:formData.response_config?.root_path||'',iterate_on:formData.response_config?.iterate_on||[],mappings:formData.response_config?.mappings||[],transforms:formData.response_config?.transforms||[]}} onChange={handleResponseConfigChange}/>
                          </div>
                        )}
                        {!showAdvancedConfig && testResult.available_fields?.length > 0 && (<>
                          <div style={{fontSize:12,color:'var(--acm-text-sub)',marginBottom:8}}>Seleziona i campi da importare:</div>
                          <div className="acm__fields-grid">
                            {testResult.available_fields.map((f,i)=>{
                              const sel = formData.output_mapping.find(m=>m.path===f.path);
                              return (
                                <div key={i} className={`acm__field-item${sel?' acm__field-item--on':''}`} role="button" tabIndex={0} onClick={()=>toggleOutputField(f)} onKeyDown={e=>(e.key==='Enter'||e.key===' ')&&toggleOutputField(f)}>
                                  <input type="checkbox" checked={!!sel} onChange={()=>{}} />
                                  <div className="acm__field-info">
                                    <span className="acm__field-name">{f.name}</span>
                                    <span className={`acm__field-type acm__field-type--${f.type}`}>{f.type}</span>
                                    {f.sample_value && <span className="acm__field-sample">{String(f.sample_value).substring(0,50)}</span>}
                                  </div>
                                </div>
                              );
                            })}
                          </div>
                          {formData.output_mapping.length > 0 && (
                            <div className="acm__selected-fields">
                              <div className="acm__wlabel">Campi selezionati ({formData.output_mapping.length})</div>
                              {formData.output_mapping.map((m,i)=>(
                                <div key={i} className="acm__selected-row">
                                  <span className="acm__selected-path">{m.path}</span>
                                  <span className="acm__selected-arrow">→</span>
                                  <input className="acm__input" value={m.column} onChange={e=>updateOutputColumn(m.path,e.target.value)}/>
                                </div>
                              ))}
                            </div>
                          )}
                        </>)}
                      </>)}
                      {!testResult.success && <div className="acm__test-err-msg">{testResult.error}</div>}
                    </div>
                  )}
                </div>
              )}
            </div>

            <div className="acm__wizard-footer">
              <button type="button" className="acm__btn acm__btn--ghost" onClick={wizardStep===0?closeWizard:()=>{setWizardStepError(null);setWizardStep(s=>s-1);}}>
                {wizardStep===0?'Annulla':'← Indietro'}
              </button>
              {wizardStep < 5 ? (
                <button type="button" className="acm__btn acm__btn--primary" onClick={()=>{
                  // Validazione step Parametri:
                  // - rest_file: serve almeno un campo file
                  // - enricher "tradizionale": serve almeno un parametro mappato a colonna
                  if (wizardStep===2 && formData.connection_type==='rest_file') {
                    const hasFile = (formData.request?.file_fields||[]).some(ff=>ff.name && ff.name.trim());
                    if (!hasFile) {
                      setWizardStepError('Aggiungi almeno un campo file per il Processore File (es. "file" per il servizio IMDF).');
                      return;
                    }
                  } else if (wizardStep===2 && formData.api_type==='enricher') {
                    const hasMapped = formData.parameters.some(p=>p.mode==='mapped');
                    if (!hasMapped) {
                      setWizardStepError('Un arricchitore richiede almeno un parametro mappato a una colonna. Se non hai parametri dinamici, torna al passo 1 e scegli \"Generatore\".');
                      return;
                    }
                  }
                  setWizardStepError(null);
                  setWizardStep(s=>s+1);
                }} disabled={wizardStep===1&&(!formData.name||!formData.url)}>
                  Avanti →
                </button>
              ) : (
                <button type="button" className="acm__btn acm__btn--ok" onClick={handleSave} disabled={!formData.name||!formData.url}>
                  💾 Salva API
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ── Quick Test Input ───────────────────────────────────────────── */}
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
              <div style={{fontSize:12,color:'var(--acm-text-sub)',marginBottom:16}}>Inserisci valori di test per i parametri mappabili:</div>
              {(quickTestInput.api.parameters||[]).filter(p=>p.mode!=='fixed').map(p=>(
                <div key={p.name} className="acm__field-group">
                  <div className="acm__label">{p.name}{p.required?' *':''}{p.description&&<span style={{fontWeight:400,marginLeft:6,opacity:.6}}>— {p.description}</span>}</div>
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

      {/* ── Quick Test Result ──────────────────────────────────────────── */}
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
                  <div className="acm__wlabel" style={{marginTop:16}}>Campi estratti ({quickTestResult.result.available_fields.length})</div>
                  <div className="acm__fields-grid">
                    {quickTestResult.result.available_fields.map((f,i)=>(
                      <div key={i} className="acm__field-item">
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
                  <div className="acm__wlabel" style={{marginTop:16}}>Anteprima risposta</div>
                  <pre className="acm__json-preview">{typeof quickTestResult.result.raw_response==='object'?JSON.stringify(quickTestResult.result.raw_response,null,2).substring(0,1500):quickTestResult.result.response_preview}</pre>
                </>)}
              </>) : (
                <div className="acm__info-box acm__info-box--err">❌ {quickTestResult.result.error||'Test fallito'}{quickTestResult.result.status_code&&<div style={{marginTop:8,fontSize:12}}>HTTP {quickTestResult.result.status_code}</div>}</div>
              )}
            </div>
            <div className="acm__wizard-footer">
              <button type="button" className="acm__btn acm__btn--ghost" onClick={()=>setQuickTestResult(null)}>Chiudi</button>
              <button type="button" className="acm__btn acm__btn--primary" onClick={()=>{setQuickTestResult(null);openWizard(quickTestResult.api);}}>⚙️ Apri configurazione</button>
            </div>
          </div>
        </div>
      )}

      {/* ── SHARE MODAL (solo superadmin) ── */}
      {sharingApi && (
        <div className="acm__overlay" onClick={()=>setSharingApi(null)}>
          <div
            ref={shareDialogRef}
            className="acm__wizard"
            onClick={e=>e.stopPropagation()}
            style={{ maxWidth: 540 }}
            role="dialog"
            aria-modal="true"
            aria-labelledby="acm-share-title"
            tabIndex={-1}
          >
            <div className="acm__wizard-header">
              <div className="acm__wizard-title" id="acm-share-title">🔗 Condividi Connettore — {sharingApi.name}</div>
              <button type="button" className="acm__modal-close" onClick={()=>setSharingApi(null)} aria-label="Chiudi">✕</button>
            </div>
            <div className="acm__wizard-body" style={{ padding: '18px 20px' }}>
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
