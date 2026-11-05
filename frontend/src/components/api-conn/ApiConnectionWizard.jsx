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
import { parseCurlCommand } from '../../utils/curlImport';
import { requestErrorMessage } from '../../utils/errorMessages';
import { toastApiError } from '../../utils/toastApiError';

export const EMPTY_FORM = {
  name:'', description:'', api_type:'enricher', category:'custom', connection_type:'manual',
  url:'', method:'GET', auth:{ type:'none' }, custom_headers:{},
  parameters:[], static_parameters:{}, output_mapping:[], schema_fields:[],
  response_preset:'', response_config:null,
  pagination:{ type:'', page_size:100, max_rows:10000, offset_param:'offset', limit_param:'limit', cursor_param:'cursor', cursor_path:'next_cursor', link_path:'next', page_param:'page', page_size_param:'page_size', start_page:0 },
  options:{ rate_limit:5, timeout:30, retry_on_error:true, retry_max:3 },
  batch:{ enabled:false, max_size:100, input_key:'items', input_format:'array', endpoint_override:'', method_override:'' },
  async:{ enabled:false },
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
    async: { ...EMPTY_FORM.async, ...(api.async || {}) },
    request: api.connection_type === 'rest_file'
      ? { ...EMPTY_FORM.request, ...(api.request || {}) }
      : (api.request || EMPTY_FORM.request),
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
    custom_headers: tpl.config?.custom_headers || {},
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
    response_preset: tpl.config?.response_preset || '',
    response_config: tpl.config?.response_config || null,
    pagination: { ...EMPTY_FORM.pagination, ...(tpl.config?.pagination || {}) },
    options: {
      rate_limit: tpl.config?.options?.rate_limit || 5,
      timeout: tpl.config?.options?.timeout || 30,
      retry_on_error: tpl.config?.options?.retry_on_error ?? true,
      retry_max: tpl.config?.options?.retry_max || 3,
    },
    batch: { ...EMPTY_FORM.batch, ...(tpl.config?.batch || {}) },
    async: { ...EMPTY_FORM.async, ...(tpl.config?.async || {}) },
    request: tpl.config?.request || EMPTY_FORM.request,
    response: { ...EMPTY_FORM.response, ...(tpl.config?.response || {}) },
  };
}

const SELECTABLE_OUTPUT_TYPES = new Set(['string', 'integer', 'number', 'boolean', 'geojson']);
const SENSITIVE_AUTH_FIELDS = [
  'password',
  'key_value',
  'value',
  'client_secret',
  'token',
  'access_token',
  'refresh_token',
];

const isMaskedSecret = v => typeof v === 'string' && v.includes('***');
const isBlank = v => v == null || String(v).trim() === '';

function sanitizeAuth(auth) {
  if (!auth) return auth;
  const s = { ...auth };
  SENSITIVE_AUTH_FIELDS.forEach(key => {
    if (isMaskedSecret(s[key])) delete s[key];
  });
  return s;
}

function validateAuthConfig(auth) {
  const cfg = auth || { type: 'none' };
  const type = cfg.type || 'none';
  const requireField = (key, label) => (
    isBlank(cfg[key]) ? `${label} richiesto per ${type}` : null
  );

  if (type === 'none') return null;
  if (type === 'bearer') return requireField('token', 'Token');
  if (type === 'api_key') {
    return requireField('key_name', 'Nome chiave')
      || (isBlank(cfg.key_value ?? cfg.value) ? 'Valore API key richiesto per api_key' : null);
  }
  if (type === 'basic_auth') {
    return requireField('username', 'Username')
      || requireField('password', 'Password');
  }
  if (type === 'oauth2_password') {
    return requireField('token_url', 'Token URL')
      || requireField('username', 'Username')
      || requireField('password', 'Password');
  }
  if (type === 'oauth2_client_credentials') {
    return requireField('token_url', 'Token URL')
      || requireField('client_id', 'Client ID')
      || requireField('client_secret', 'Client Secret');
  }
  if (type === 'arcgis_token') {
    const baseError = requireField('token_url', 'Token URL')
      || requireField('username', 'Username')
      || requireField('password', 'Password');
    if (baseError) return baseError;
    if (cfg.client === 'referer') return requireField('referer', 'Referer URL');
    if (cfg.client === 'ip') return requireField('ip', 'IP');
  }
  return null;
}

function outputColumnFromField(field) {
  const raw = String(field?.name || field?.path || 'value')
    .replace(/\[\]|\[\*\]|\[0\]/g, '')
    .split('.')
    .pop() || 'value';
  const column = raw.replace(/[^0-9A-Za-z_]+/g, '_').replace(/^_+|_+$/g, '') || 'value';
  return /^\d/.test(column) ? `field_${column}` : column;
}

function normalizeOutputMapping(items) {
  return (Array.isArray(items) ? items : [])
    .filter(item => item?.path)
    .map(item => ({
      path: item.path,
      column: item.column || outputColumnFromField(item),
      type: item.type || 'string',
      selected: item.selected ?? true,
    }));
}

function buildSuggestedMappingFromFields(fields) {
  const seen = new Set();
  return (Array.isArray(fields) ? fields : [])
    .filter(field => field?.path && SELECTABLE_OUTPUT_TYPES.has(field.type))
    .map(field => {
      const base = outputColumnFromField(field);
      let column = base;
      let suffix = 2;
      while (seen.has(column)) {
        column = `${base}_${suffix}`;
        suffix += 1;
      }
      seen.add(column);
      return {
        path: field.path,
        column,
        type: field.type,
        selected: true,
      };
    });
}

function suggestedMappingFromTestResult(result) {
  const suggested = normalizeOutputMapping(result?.suggested_output_mapping);
  if (suggested.length > 0) return suggested;
  return buildSuggestedMappingFromFields(result?.available_fields);
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
            path: f.path,
            name: f.name || f.column || f.path,
            type: f.type,
            sample_value: f.sample_value,
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
      setCustomHeadersList(Object.entries(
        seed.template.config?.custom_headers || {}
      ).map(([key, value]) => ({ key, value })));
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

  const buildConnectionPayload = () => {
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
    if (formData.async?.enabled) {
      data.async = formData.async;
    }
    if (formData.request?.body_type && formData.request.body_type !== 'multipart') {
      data.request = formData.request;
    }
    if (formData.connection_type === 'rest_file') {
      data.method = 'POST';
      data.request = formData.request || { body_type:'multipart', file_fields:[], static_fields:[] };
      data.response = formData.response || { format:'binary_file', output_extension:'.zip' };
    }
    return data;
  };

  const handleSave = async () => {
    try {
      const authError = validateAuthConfig(formData.auth);
      if (authError) {
        toast.error(authError);
        return;
      }
      const data = buildConnectionPayload();
      if (editingApi) await updateApiConnection(editingApi.id, data);
      else await createApiConnection(data);
      onSaved?.();
      onClose?.();
    } catch (e) {
      // N5: il motivo del backend (nome duplicato, auth non valida, 422).
      toastApiError(toast, e, 'Salvataggio non riuscito');
    }
  };

  const handleTest = async () => {
    setTesting(true); setTestResult(null); setTestRawResponse(null);
    try {
      const authError = validateAuthConfig(formData.auth);
      if (authError) {
        setTestResult({ success: false, error: authError });
        return;
      }
      const applyTestSampling = result => {
        if (!result?.success) return;
        const hasFields = Array.isArray(result.available_fields);
        const fields = hasFields ? result.available_fields : [];
        const suggested = suggestedMappingFromTestResult(result);
        setFormData(prev => ({
          ...prev,
          ...(hasFields ? { schema_fields: fields } : {}),
          ...(
            prev.output_mapping?.length > 0 || suggested.length === 0
              ? {}
              : { output_mapping: suggested }
          ),
        }));
      };

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
          config_overrides: buildConnectionPayload(),
        });
        setTestResult(result);
        if (result.raw_response) setTestRawResponse(result.raw_response);
        applyTestSampling(result);
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
          options: formData.options,
          request: formData.request?.body_type !== 'multipart' ? formData.request : undefined,
          async: formData.async?.enabled ? formData.async : undefined,
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
          options: formData.options,
          request: formData.request?.body_type !== 'multipart' ? formData.request : undefined,
          async: formData.async?.enabled ? formData.async : undefined,
          test_params: { ...formData.static_parameters, ...testParams },
          include_raw: true,
        };
      }
      const result = await testApiConfig(testPayload);
      setTestResult(result);
      if (result.raw_response) setTestRawResponse(result.raw_response);
      applyTestSampling(result);
    } catch (e) {
      setTestResult({ success: false, error: requestErrorMessage(e) });
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
  const handleImportCurl = (curlCommand) => {
    const parsed = parseCurlCommand(curlCommand);
    setFormData(prev => ({
      ...prev,
      name: prev.name || parsed.name || '',
      url: parsed.url,
      method: parsed.method,
      auth: parsed.auth || { type: 'none' },
      parameters: parsed.parameters,
      static_parameters: parsed.static_parameters,
      output_mapping: [],
      schema_fields: [],
      response_preset: '',
      response_config: null,
      request: Object.keys(parsed.request || {}).length > 0 ? parsed.request : {},
    }));
    setCustomHeadersList(
      Object.entries(parsed.custom_headers || {})
        .map(([key, value]) => ({ key, value })),
    );
    setTestResult(null);
    setTestRawResponse(null);
    setShowAdvancedConfig(false);
    return parsed;
  };
  const toggleOutputField  = (field) => setFormData(prev => {
    const ex = prev.output_mapping.find(m => m.path === field.path);
    return ex
      ? { ...prev, output_mapping: prev.output_mapping.filter(m => m.path !== field.path) }
      : { ...prev, output_mapping: [...prev.output_mapping, { path: field.path, column: outputColumnFromField(field), type: field.type, selected: true }] };
  });
  const applySuggestedOutputMapping = () => {
    const suggested = suggestedMappingFromTestResult(testResult);
    if (suggested.length === 0) return;
    setFormData(prev => ({ ...prev, output_mapping: suggested }));
  };
  const clearOutputMapping = () => setFormData(prev => ({ ...prev, output_mapping: [] }));
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
      content: (
        <Step1BasicInfo
          formData={formData}
          setFormData={setFormData}
          onImportCurl={handleImportCurl}
        />
      ),
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
      // R6.2: anche il salto dalla barra degli step e "Salva" passano di qui.
      validate: () => validateAuthConfig(formData.auth),
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
          applySuggestedOutputMapping={applySuggestedOutputMapping}
          clearOutputMapping={clearOutputMapping}
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
