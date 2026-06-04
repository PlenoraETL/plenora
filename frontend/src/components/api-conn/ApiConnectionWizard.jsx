/**
 * ApiConnectionWizard.jsx — Orchestrator del wizard ApiConnection
 *
 * Owna:
 *
 *  - lo stato wizard-owned (formData, wizardStep, customHeadersList,
 *    testParams, testResult, testRawResponse, testing, showAdvancedConfig);
 *  - il setup iniziale del seed (new / edit / template);
 *  - le callback CRUD (parameters, custom headers, output mapping);
 *  - handleSave, handleTest;
 *  - la definizione dei 6 step (label + content + validazione).
 *
 * Il CHROME (overlay/dialog/step-bar/footer/a11y) e' delegato al componente
 * condiviso `common/Wizard` (classe "wizard" del FE).
 */
import React, { useState, useEffect } from 'react';
import Wizard from '../common/Wizard';
import { useToast } from '../../hooks/useToast';
import {
  createApiConnection,
  updateApiConnection,
  testApiConfig,
  testApiConnection,
} from '../../api/apiConnections';
import Step0ChooseType from './wizard-steps/Step0ChooseType';
import Step1BasicInfo from './wizard-steps/Step1BasicInfo';
import Step2Params from './wizard-steps/Step2Params';
import Step3Auth from './wizard-steps/Step3Auth';
import Step4Batch from './wizard-steps/Step4Batch';
import Step5TestSave from './wizard-steps/Step5TestSave';

export const EMPTY_FORM = {
  name:'', description:'', api_type:'enricher', category:'custom', connection_type:'manual',
  url:'', method:'GET', auth:{ type:'none' }, custom_headers:{},
  parameters:[], static_parameters:{}, output_mapping:[], schema_fields:[],
  response_preset:'', response_config:null,
  pagination:{ type:'', page_size:100, max_rows:10000, offset_param:'offset', limit_param:'limit', cursor_param:'cursor', cursor_path:'next_cursor', link_path:'next', start_page:0 },
  options:{ rate_limit:5, timeout:30, retry_on_error:true, retry_max:3 },
  batch:{ enabled:false, max_size:100, input_key:'items', input_format:'array', endpoint_override:'', method_override:'' },
  // REST file processor fields use connection_type='rest_file'.
  // file_fields: campi multipart che ricevono un file. Per ora solo source='node_input'
  //   (file in input al nodo del canvas).
  // static_fields: campi multipart con valore fisso (es. JSON inline per styles/mapping).
  //   Se content_type e' specificato, il valore viene inviato come file in-memory
  //   con quel content type; senza, come form-data semplice.
  request:  { body_type:'multipart', file_fields:[], static_fields:[], timeout:600 },
  response: { format:'binary_file', output_extension:'.zip' },
};

// Costruisce formData dalla connessione esistente (edit). Mergia con
// EMPTY_FORM per i campi strutturati: senza il merge, un payload server
// parziale lascerebbe nell'UI campi assenti che alla save successiva
// verrebbero omessi -> data loss silenziosa. Con merge i default coprono
// i campi mancanti, quelli presenti li sovrascrivono.
function buildFormFromApi(api) {
  const eh = api.custom_headers || {};
  return {
    name: api.name || '',
    description: api.description || '',
    api_type: api.api_type || 'enricher',
    category: api.category || 'custom',
    connection_type: api.connection_type || 'manual',
    url: api.endpoint?.url || api.url || '',
    method: api.endpoint?.method || api.method || 'GET',
    auth: api.auth || { type: 'none' },
    custom_headers: eh,
    parameters: api.parameters || [],
    static_parameters: api.static_parameters || {},
    output_mapping: api.output_mapping || [],
    schema_fields: api.schema_fields || [],
    response_preset: api.response_preset || '',
    response_config: api.response_config || null,
    pagination: { ...EMPTY_FORM.pagination, ...(api.pagination || {}) },
    options: { ...EMPTY_FORM.options, ...(api.options || {}) },
    batch: { ...EMPTY_FORM.batch, ...(api.batch || {}) },
    request: { ...EMPTY_FORM.request, ...(api.request || {}) },
    response: { ...EMPTY_FORM.response, ...(api.response || {}) },
  };
}

function buildFormFromTemplate(tpl) {
  return {
    name: tpl.display?.name || tpl.name || '',
    description: tpl.display?.short_description || '',
    api_type: tpl.config?.api_type || 'enricher',
    category: tpl._meta?.category || 'custom',
    connection_type: 'template',
    url: tpl.config?.endpoint?.url || '',
    method: tpl.config?.endpoint?.method || 'GET',
    auth: tpl.config?.auth || { type: 'none' },
    custom_headers: {},
    parameters: (tpl.parameters || []).map(p => ({
      name: p.name,
      type: p.type || 'string',
      required: p.required ?? true,
      mode: p.hidden ? 'fixed' : 'mapped',
      label: p.label,
      description: p.description,
    })),
    static_parameters: {
      ...(tpl.config?.static_parameters || {}),
      ...(tpl.parameters || [])
        .filter(p => p.default != null)
        .reduce((a, p) => ({ ...a, [p.name]: p.default }), {}),
    },
    output_mapping: (tpl.output_mapping || []).map(o => ({
      path: o.path, column: o.column, type: o.type,
    })),
    schema_fields: [],
    response_preset: '',
    response_config: null,
    pagination: EMPTY_FORM.pagination,
    options: {
      rate_limit: tpl.config?.options?.rate_limit || 5,
      timeout: tpl.config?.options?.timeout || 30,
      retry_on_error: tpl.config?.options?.retry_on_error ?? true,
      retry_max: tpl.config?.options?.retry_max || 3,
    },
    batch: EMPTY_FORM.batch,
    request: EMPTY_FORM.request,
    response: EMPTY_FORM.response,
  };
}

export default function ApiConnectionWizard({ isOpen, seed, onClose, onSaved }) {
  const toast = useToast();
const [editingApi, setEditingApi] = useState(null);
  const [wizardStep, setWizardStep] = useState(0);
  const [formData, setFormData] = useState(EMPTY_FORM);
  const [customHeadersList, setCustomHeadersList] = useState([]);
  const [testParams, setTestParams] = useState({});
  const [testResult, setTestResult] = useState(null);
  const [testRawResponse, setTestRawResponse] = useState(null);
  const [testing, setTesting] = useState(false);
  const [showAdvancedConfig, setShowAdvancedConfig] = useState(false);

  useEffect(() => {
    if (!isOpen) return;
    if (!seed || seed.kind === 'new') {
      setEditingApi(null);
      setFormData(EMPTY_FORM);
      setCustomHeadersList([]);
      setWizardStep(0);
      setTestResult(null);
      setTestParams({});
      return;
    }
    if (seed.kind === 'edit') {
      const api = seed.api;
      setEditingApi(api);
      const eh = api.custom_headers || {};
      setFormData(buildFormFromApi(api));
      setCustomHeadersList(Object.entries(eh).map(([k, v]) => ({ key: k, value: v })));
      if (api.schema_fields?.length > 0) {
        setTestResult({
          success: true,
          available_fields: api.schema_fields.map(f => ({
            path: f.path, name: f.name, type: f.type,
          })),
        });
      } else {
        setTestResult(null);
      }
      setWizardStep(
        api.parameters?.length > 0 || Object.keys(api.static_parameters || {}).length > 0
          ? 2
          : 1
      );
      setTestParams({});
      return;
    }
    if (seed.kind === 'template') {
      setEditingApi(null);
      setFormData(buildFormFromTemplate(seed.template));
      setCustomHeadersList([]);
      setWizardStep(1);
      setTestResult(null);
      setTestParams({});
      return;
    }
  }, [isOpen, seed]);

  if (!isOpen) return null;
const getCustomHeadersObject = () => {
    const h = {};
    customHeadersList.forEach(x => { if (x.key?.trim()) h[x.key.trim()] = x.value || ''; });
    return h;
  };

  const handleSave = async () => {
    try {
      const isMasked = v => typeof v === 'string' && v.includes('***');
      const sanitizeAuth = auth => {
        if (!auth) return auth;
        const s = { ...auth };
        if (isMasked(s.password)) delete s.password;
        if (isMasked(s.key_value)) delete s.key_value;
        if (isMasked(s.client_secret)) delete s.client_secret;
        return s;
      };
      const data = {
        name: formData.name,
        description: formData.description,
        api_type: formData.api_type,
        category: formData.category,
        connection_type: formData.connection_type || 'manual',
        url: formData.url,
        method: formData.method,
        auth: sanitizeAuth(formData.auth),
        custom_headers: getCustomHeadersObject(),
        parameters: formData.parameters.map(p =>
          formData.api_type === 'generator'
            ? { ...p, mode: 'fixed', required: false }
            : p
        ),
        static_parameters: formData.static_parameters,
        output_mapping: formData.output_mapping,
        schema_fields: formData.schema_fields || [],
        response_preset: formData.response_preset || undefined,
        response_config: formData.response_config || undefined,
        pagination: (formData.api_type === 'generator' && formData.pagination?.type)
          ? formData.pagination
          : undefined,
        options: { ...formData.options, parallel_workers: formData.options?.parallel_workers || 1 },
        batch: formData.batch || { enabled: false },
      };
      if (formData.connection_type === 'rest_file') {
        data.method = 'POST';
        data.request = formData.request || { body_type:'multipart', file_fields:[], static_fields:[] };
        data.response = formData.response || { format:'binary_file', output_extension:'.zip' };
      }
      if (editingApi) await updateApiConnection(editingApi.id, data);
      else await createApiConnection(data);
      onSaved?.();
      onClose?.();
    } catch (e) {
      toast.error('Errore salvataggio');
    }
  };

  const handleTest = async () => {
    setTesting(true); setTestResult(null); setTestRawResponse(null);
    try {
      // In edit mode: se l'auth ha credenziali sensibili mascherate
      // ("***") o vuote, il payload del form non puo' fare /test
      // (il backend useremmo l'auth del payload, e fallirebbe 401).
      // Usa l'endpoint by-id che decifra dal DB. Skippa se l'utente
      // ha esplicitamente cambiato la chiave nel form (valore non
      // mascherato e non vuoto).
      const auth = formData.auth || {};
      const sensitive = auth.key_value ?? auth.password ?? auth.client_secret ?? auth.token;
      const sensitiveIsPlaceholder = sensitive === '***' || sensitive === '' || sensitive == null;
      if (editingApi?.id && auth.type && auth.type !== 'none' && sensitiveIsPlaceholder) {
        const result = await testApiConnection(editingApi.id, {
          test_params: { ...formData.static_parameters, ...testParams },
          include_raw: true,
        });
        setTestResult(result);
        if (result.raw_response) setTestRawResponse(result.raw_response);
        return;
      }
      const batch = formData.batch;
      let testPayload;
      if (batch?.enabled) {
        const inputKey = batch.input_key || 'items';
        const inputFormat = batch.input_format || 'array';
        const singleParams = { ...formData.static_parameters, ...testParams };
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
        testPayload = {
          url: formData.url,
          method: formData.method,
          auth: formData.auth,
          custom_headers: getCustomHeadersObject(),
          test_params: { ...formData.static_parameters, ...testParams },
          include_raw: true,
        };
      }
      const result = await testApiConfig(testPayload);
      setTestResult(result);
      if (result.raw_response) setTestRawResponse(result.raw_response);
      if (result.success && result.available_fields) {
        setFormData(prev => {
          if (prev.output_mapping?.length > 0) return prev;
          return {
            ...prev,
            output_mapping: result.available_fields
              .filter(f => f.type === 'geojson' || f.name === 'id')
              .map(f => ({ path: f.path, column: f.name, type: f.type, selected: true })),
          };
        });
      }
    } catch (e) {
      setTestResult({ success: false, error: e.message });
    } finally {
      setTesting(false);
    }
  };

  const addParameter    = () => setFormData(prev => ({ ...prev, parameters: [...prev.parameters, { name:'', type:'string', required:true, mode:'mapped' }] }));
  const updateParameter = (i, f, v) => setFormData(prev => ({ ...prev, parameters: prev.parameters.map((p, j) => j === i ? { ...p, [f]: v } : p) }));
  const removeParameter = (i) => setFormData(prev => ({ ...prev, parameters: prev.parameters.filter((_, j) => j !== i) }));
  const addCustomHeader    = () => setCustomHeadersList(prev => [...prev, { key:'', value:'' }]);
  const updateCustomHeader = (i, f, v) => setCustomHeadersList(prev => prev.map((h, j) => j === i ? { ...h, [f]: v } : h));
  const removeCustomHeader = (i) => setCustomHeadersList(prev => prev.filter((_, j) => j !== i));
  const toggleOutputField  = (field) => setFormData(prev => {
    const ex = prev.output_mapping.find(m => m.path === field.path);
    return ex
      ? { ...prev, output_mapping: prev.output_mapping.filter(m => m.path !== field.path) }
      : { ...prev, output_mapping: [...prev.output_mapping, { path: field.path, column: field.name, type: field.type, selected: true }] };
  });
  const updateOutputColumn = (path, col) => setFormData(prev => ({
    ...prev,
    output_mapping: prev.output_mapping.map(m => m.path === path ? { ...m, column: col } : m),
  }));
  const handleResponseConfigChange = (config) => {
    if (config.response_preset) {
      setFormData(prev => ({ ...prev, response_preset: config.response_preset, response_config: null }));
    } else if (config.response_config) {
      setFormData(prev => ({
        ...prev,
        response_preset: '',
        response_config: config.response_config,
        output_mapping: config.response_config.mappings?.map(m => ({
          path: m.path, column: m.column, type: m.type || 'string', selected: true,
        })) || [],
      }));
    }
  };
  const validateParamsStep = () => {
    // - rest_file: serve almeno un campo file
    // - enricher "tradizionale": serve almeno un parametro mappato a colonna
    if (formData.connection_type === 'rest_file') {
      const hasFile = (formData.request?.file_fields || []).some(ff => ff.name && ff.name.trim());
      if (!hasFile) return 'Aggiungi almeno un campo file per il Processore File (es. "file" per il servizio IMDF).';
    } else if (formData.api_type === 'enricher') {
      const hasMapped = formData.parameters.some(p => p.mode === 'mapped');
      if (!hasMapped) return 'Un arricchitore richiede almeno un parametro mappato a una colonna. Se non hai parametri dinamici, torna al passo 1 e scegli "Generatore".';
    }
    return null;
  };

  const steps = [
    { label: 'Tipo', content: <Step0ChooseType formData={formData} setFormData={setFormData} /> },
    {
      label: 'Info',
      canAdvance: !!formData.name && !!formData.url,
      content: <Step1BasicInfo formData={formData} setFormData={setFormData} />,
    },
    {
      label: 'Parametri',
      validate: validateParamsStep,
      content: (
        <Step2Params
          formData={formData}
          setFormData={setFormData}
          addParameter={addParameter}
          updateParameter={updateParameter}
          removeParameter={removeParameter}
        />
      ),
    },
    {
      label: 'Auth',
      content: (
        <Step3Auth
          formData={formData}
          setFormData={setFormData}
          customHeadersList={customHeadersList}
          addCustomHeader={addCustomHeader}
          updateCustomHeader={updateCustomHeader}
          removeCustomHeader={removeCustomHeader}
        />
      ),
    },
    { label: 'Batch', content: <Step4Batch formData={formData} setFormData={setFormData} /> },
    {
      label: 'Test',
      content: (
        <Step5TestSave
          formData={formData}
          setFormData={setFormData}
          testParams={testParams}
          setTestParams={setTestParams}
          testResult={testResult}
          testRawResponse={testRawResponse}
          testing={testing}
          showAdvancedConfig={showAdvancedConfig}
          setShowAdvancedConfig={setShowAdvancedConfig}
          handleTest={handleTest}
          toggleOutputField={toggleOutputField}
          updateOutputColumn={updateOutputColumn}
          handleResponseConfigChange={handleResponseConfigChange}
        />
      ),
    },
  ];

  return (
    <Wizard
      isOpen={isOpen}
      onClose={onClose}
      title={editingApi ? `Modifica — ${editingApi.name}` : 'Nuovo Connettore'}
      step={wizardStep}
      onStepChange={setWizardStep}
      steps={steps}
      onComplete={handleSave}
      completeLabel="💾 Salva API"
      completeDisabled={!formData.name || !formData.url}
    />
  );
}
