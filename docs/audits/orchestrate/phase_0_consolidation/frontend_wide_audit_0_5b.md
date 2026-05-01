# Phase 0.5b — Frontend Wide Audit (Evidence-deep)

Date: 2026-04-28
Status: **analysis only — no refactor opened, no test modified, no code change.**
Scope: 7 file frontend (LOC + coupling) + shared layer (`useCanvasStore`,
        `api/*`, `utils/permissions`, `utils/hexCanvas`).

Per ogni file:
- internal map line-ranged
- responsabilità UI principali
- state locale + derived + data fetching + validation + rendering
- coupling con API layer / canvas store / modals / node factories
- call graph / import graph essenziale
- UX / regression hotspots
- test coverage esistente (file + line per test case)
- candidati extraction small/medium/large
- decomposition in commit con rollback per-commit
- test gate + visual smoke
- raccomandazione per file

Read together with:
- `wide_audit_2026_04_28.md` (mappa allargata Phase 0)
- `hot_file_deep_dive_0_5a.md` (companion backend, evidence-deep)
- `test_coupling_roadmap_0_5d.md` (test coupling backend, evidence-deep)

---

## 0. Tabella Comparativa

| File | LOC | Componenti top | State count (visibile) | Hooks | API direct | Canvas store | Modals esterni | Test contract | Raccomandazione |
| --- | ---:| ---:| ---:| --- | --- | --- | --- | --- | --- |
| `pages/ApiConnectionManager.jsx` | 1183 | 1 page + ThemeToggle | **30+** `useState` | useToast, useNavigate, useAuth, useHexCanvas | apiConnections, datasources | no | TemplateLibrary, OpenAPIImport, DataSourceConfig, ResponseConfigBuilder, AllowedGroupsSelector | 4 test (122–340) | **medium (post-prod)** — wizard step extraction |
| `components/panels/ResponseConfigBuilder.jsx` | 978 | 2 (Builder + JsonTreeNode) + 8 utility fns | 8 useState + useMemo derived | useState/useMemo/useCallback | no (pura presentazione) | no | no (è esso stesso una sotto-UI) | **0** | **small** — utility fns extraction |
| `components/transforms/ParameterInput.jsx` | 974 | **8** sub-components + main `ParameterInput` | locale per sub | useState | no | no | no | **0** | **small** — 6 list inputs in modulo separato |
| `components/panels/ApiEnrichNode.jsx` | 944 | 2 (ApiEnrichNode + ExtraFileSlot) | ~6 useState | useStore (RF), useReactFlow, useCanvasStore, useState/useEffect/useMemo/useRef | apiConnections, files | **sì** (pesante) | RunHistoryModal | 1 boundary test (8 assertions) | **medium** — split rest_file branch |
| `pages/FileManager.jsx` | 887 | 5 (FileManager + FileCard + UploadModal + LayerModal + PreviewModal) | **30+** useState | useToast, useConfirmModal, useNavigate, useAuth, useHexCanvas | files | no | ConfirmDialog, ModalWrapper, AllowedGroupsSelector | 1 test (104) | **small** — multi-layer wizard hook extraction |
| `components/nodes/result/ResultNodeStandardView.jsx` | 880 | 1 (memo) | 7 useState | useState/useEffect | no | **sì** (`updateNodeData`) | RunHistoryModal | 13 test (86–225) | **small** — split GROUP BY/ORDER BY/HAVING sections |
| `pages/ProjectList.jsx` | 862 | 4 (ProjectList + ProjectSection + ProjectCard + EseguibileCard) + useEmptyHexCanvases | ~15 useState | useToast, useNavigate, useLocation, useAuth, useHexCanvas | projects, connections, apiConnections, datasources, features, files, schedules, eseguibili | no | PublishEseguibileModal, EseguibileExecuteWizard, ModalWrapper, AppSidebar | 2 test (96–117) | **no-op** — già ben strutturato |

LOC totale: **6708**. Test contract attivi: ~21 test su 4 file. Falsi target ricorrenti: nessuno.

---

## 1. `pages/ApiConnectionManager.jsx` (1183 LOC)

### 1.1 Internal map (line ranges)

| Block | Lines | Purpose |
| --- | --- | --- |
| Imports | 1–31 | React, hooks, modals, api/apiConnections, api/datasources, api/errors, permissions, CSS |
| Constants `CATEGORIES`, `CONNECTION_TYPES`, `STATUS_CFG` | 34–57 | UI taxonomies |
| `EMPTY_FORM` | 58–75 | shape iniziale wizard form (~17 chiavi nested: pagination/options/batch/request/response) |
| `getApiIcon`, `getApiStatus`, `formatTimeAgo` | 77–102 | pure helpers |
| `ThemeToggle` | 104–108 | sub-component (anche replicato in altri file) |
| **`ApiConnectionManager` main** | **111–1182** | ~1070 LOC |
| State (30+ useState) | 118–148 | apiConnections list, search, category, dropdown, 3 modali esterni, wizard state (showWizard, step, error, editingApi, formData, customHeadersList, testParams, testResult, testRawResponse, testing, showAdvancedConfig, quickTestInput, quickTestResult, sharingApi) |
| `useEffect` mount + outside-click handler | 151–156 | bootstrap |
| `loadApiConnections` async | 158–161 | `listApiConnections()` API call |
| `filteredApis` useMemo | 163–167 | search + category |
| `globalStats` useMemo | 169–174 | stats card |
| `openWizard(api?)` | 176–211 | normalize edit payload con **MERGE pattern (NO fallback-OR)** per pagination/options/batch/request/response (`{ ...EMPTY_FORM.x, ...api.x }`) — guard contro data loss su update parziale |
| `applyTemplate(tpl)` | 213–216 | template flow |
| `applyOpenAPIImport(importedApis)` | 218–223 | bulk import loop |
| `closeWizard` | 225 | reset |
| `handleDelete` con cascade 409 | 227–241 | usa `handleCascade409` + `formatCascadeDependencies` |
| `getCustomHeadersObject` | 243 | merge |
| **`handleSave`** (sanitize masked auth + payload) | **245–260** | rimozione `***` masked secrets prima del PUT |
| **`handleTest`** (batch vs single) | **262–299** | `testApiConfig` con batch wrapping |
| `handleQuickTest` / `runQuickTest` | 301–308 | quick test (fuori wizard) |
| param/header CRUD helpers | 310–315 | `addParameter`, `updateParameter`, ecc. |
| `toggleOutputField`, `updateOutputColumn`, `handleResponseConfigChange` | 316–318 | output mapping |
| `WIZARD_STEPS` constant | 320 | `['Tipo','Info','Parametri','Auth','Batch','Test']` |
| **JSX page layout** | 322–504 | Sidebar + topbar + dropdown + stats + toolbar + grid + cards |
| **Wizard modal** | 517–~1175 | 6 step + body sections (~660 LOC di JSX) |
| Step 0 Tipo | 539–~580 | type cards (enricher/generator) |
| Step 1 Info | ~580–660 | url, method, name, description, category, custom headers |
| Step 2 Parametri | ~660–800 | rest_file body_type/file_fields/static_fields branch, oppure parametri standard + paginazione |
| Step 3 Auth | 802–930 | 6 auth types (none, api_key, basic_auth, oauth2_password, oauth2_client_credentials, arcgis_token) — ognuno con form proprio |
| Step 4 Test/Batch | 932–~1080 | batch toggle + test panel + ResponseConfigBuilder embed |
| Step 5 Output mapping | ~1080–1170 | toggle output fields, rinomina colonne |
| Sharing modal (super) | ~1170–1180 | `<AllowedGroupsSelector />` |

### 1.2 Local / derived / fetching / validation / rendering

- **Local state**: 30+ `useState` calls (lines 118–148)
- **Derived (useMemo)**: `filteredApis` (163), `globalStats` (169) — recompute solo su deps relative (apiConnections, search, category)
- **Data fetching**: `listApiConnections` (158), `testApiConfig` (291), `testApiConnection` (306), `createApiConnection`/`updateApiConnection`/`deleteApiConnection` (218, 257, 229), `createDataSource` (513)
- **Validation**: assente lato UI (no schema validation Zod/Yup); errori solo a backend → toast.error generico
- **Rendering**: 1 grande monolite JSX 522–1175 con sotto-sezioni condizionali per step

### 1.3 Coupling

- **API layer**: `api/apiConnections` (4 funzioni), `api/datasources` (1), `api/errors` (handleCascade409)
- **Modals esterni** (black box):
  - `TemplateLibraryModal` (line 506) — onSelectTemplate
  - `OpenAPIImportModal` (507) — onImport
  - `DataSourceConfigModal` (508–516) — onSave inline create
  - `ResponseConfigBuilder` (embedded in Step 4–5) — see §2
  - `AllowedGroupsSelector` (sharing modal) — embedded
- **Hooks**: useToast, useNavigate, useAuth, useHexCanvas
- **Canvas store**: nessuno (page non legge/modifica canvas)
- **Permissions**: `can.createApiConnection(user)`, `role.isSuperadmin(user)` (line 148–149)

### 1.4 Call graph

Inbound (chi importa/route a questa pagina):
- `frontend/src/App.jsx` route (uses React Router)
- `pages/ProjectList.jsx` → navigate('/api-connections') tramite AppSidebar/stats

Internal: `openWizard` → setFormData/setCustomHeadersList/setWizardStep + setShowWizard.
`handleSave` → sanitizeAuth → updateApiConnection/createApiConnection → loadApiConnections.

### 1.5 UX / regression hotspots

| Hotspot | Lines | Why |
| --- | ---:| --- |
| **MERGE vs fallback-OR** in `openWizard` per pagination/options/batch/request/response | 198–202 | Pre-fix con `||` perdeva campi mancanti dal server (es. timeout) → save successivo li ometteva → data loss silenziosa. Documentato nel codice (180–186). |
| Sanitize masked auth `s.password.includes('***') → delete s.password` | 247–248 | Senza, salvataggio invia `"***"` come password → distruzione segreto |
| `connection_type==='rest_file'` → forza `method='POST'` + request/response defaults | 252–256 | Multipart richiede POST. Rimuovere = upload rotto |
| Auth `arcgis_token` con client `requestip`/`referer`/`ip` → conditional fields | 879–914 | Cambio client senza cleanup dei campi precedenti = invalid payload |
| Test in batch mode wrappa `{[inputKey]: batchItems}` | 268–284 | `inputKey`/`inputFormat` cambia formato test request → mismatch test/runtime se gli helper batch divergono |
| `applyOpenAPIImport` loop senza chunking | 218–223 | N richieste seriali — UI freeze su import grandi (>50 endpoint) |
| `outside-click` listener su dropdown global document | 152–155 | Memory leak se cleanup salta su unmount; oggi return `removeEventListener` corretto |
| `globalStats.warningApis` calcolato su ogni render | 169–174 | OK con useMemo, ma `getApiStatus` ricalcola su ogni connection — N×render se lista cresce |
| `confirm(...)` browser native (line 228) | 228 | Inconsistente vs `useConfirmModal` di FileManager — UX mista |
| `handleSave` fallback toast `Errore salvataggio` non specifico | 259 | Nessuna distinzione 4xx/5xx → utente non capisce cosa correggere |

### 1.6 Test coverage

`frontend/tests/pages/ApiConnectionManager.contract.test.jsx` (340+ LOC):

| Test | Line | Cosa protegge |
| --- | ---:| --- |
| `preserves rest_file defaults when editing a partial backend payload` | 132 | MERGE pattern di `openWizard` (linee 198–202): backend partial payload → form non perde defaults |
| `preserves template defaults and generator parameter normalization on save` | 195 | applyTemplate + handleSave normalizza `parameters[i].mode='fixed'` per generator |
| `maps datasource modal output to the backend datasource payload shape` | 257 | DataSourceConfigModal callback (line 508–516) → createDataSource payload corretto |
| `imports every OpenAPI connector and reloads the list after completion` | 300 | applyOpenAPIImport loop completo + reload |

**Tutti behavior-bound** (vi.mock per http/toast/navigate/modals + userEvent + assert su mock calls).

### 1.7 Candidati extraction

| Candidato | Tipo | Justification | LOC | Coupling reale? |
| --- | --- | --- | ---:| --- |
| **a. Wizard step body** in 6 sub-component (`StepType`, `StepInfo`, `StepParameters`, `StepAuth`, `StepBatchTest`, `StepOutput`) | medium | 660 LOC di JSX condizionale dentro un singolo return — cambio tab forza re-render dell'intero monolite. Coupling reale: ognuno legge `formData` + chiama `setFormData`. Estraibili come componenti controllati `({ formData, onChange })` | -660 LOC | **sì** — formData è prop drilling target (oggi inline in chiusura) |
| **b. `useApiConnectionForm` hook** | small | i 30+ `useState` sono in realtà 1 oggetto formData + UI flags. Estraibile come `useApiConnectionForm()` che ritorna `{ formData, customHeadersList, ..., openWizard, applyTemplate, handleSave, handleTest }` | ~250 LOC | **medium** — ridurrebbe le props da passare ai sub-component di (a) |
| **c. Auth type sub-forms** in `<AuthForm type=... auth=... onChange=... />` | small | 6 conditional blocks (815–916) ciascuno con fields propri. Pattern uniforme → componente generico mappato per `auth.type` | ~120 LOC | **sì** — già a coupling minimo |
| **d. Card list extraction** (`<ApiConnectionCard />`) | small | il card render dentro `filteredApis.map` (425–485) è ~60 LOC di JSX riutilizzabile per future viste | ~60 LOC | basso |

### 1.8 Commit decomposition

Recommendation: **medium refactor (post-prod)**. Decomposition (4 commit):

| Commit | Change | LOC | Rollback | Test gate |
| --- | --- | --- | --- | --- |
| **c1** | Estrai `<AuthForm />` sub-component (b inline) | ~120 LOC | `git revert <c1>` (sub-component nuovo, JSX inline rimpiazzato con `<AuthForm />`) | tutti 4 i test ApiConnectionManager passano; visual smoke: aprire wizard step Auth con ognuno dei 6 type, verificare campi compaiano correttamente |
| **c2** | Estrai `<ApiConnectionCard />` (d) | ~60 LOC | revert single | smoke: lista connectors render + click test/edit/share/delete |
| **c3** | Estrai `useApiConnectionForm` hook (b) — sposta logiche openWizard/applyTemplate/handleSave/handleTest, espone state+actions | ~250 LOC | revert moderate-risk: il custom hook deve preservare ordine MERGE pattern. Mitigazione: lasciare `EMPTY_FORM` in modulo `apiConnectionForm.js` esportato | tutti 4 i test passano (testano behavior, non struttura interna); aggiungere unit test su MERGE pattern (oggi solo end-to-end) |
| **c4** | Estrai 6 step body in `<WizardStepX />` (a) | ~660 LOC | revert moderate-risk: 6 sub-component nuovi | tutti 4 i test passano; visual smoke: wizard end-to-end (Tipo→Info→Parametri→Auth→Batch→Test) per enricher + generator + rest_file |

Effort: c1 2h, c2 1h, c3 4h, c4 6h. **Tot ~13h.**

### 1.9 Visual smoke richiesto

- Edit di una connection esistente (rest_file, template, openapi, manuale): verificare nessun campo si perde (pagination, options, batch, request, response, custom_headers).
- Create da template POI search → save → reload: tutti campi presenti.
- Sharing modal apre solo per superadmin.
- Cascade 409 toast con dipendenze formattate (handleCascade409 path).

### 1.10 Raccomandazione

**Medium refactor (post-prod, dopo deadline 5 maggio).**

Razionale: file più grande del frontend per LOC (1183), ma il problema reale è che il wizard è un **monolite render**: ogni keystroke nei campi step Auth fa re-render dei step Tipo/Info/Parametri/Batch/Test (anche se non visibili). Split in step component-component-controllati riduce il render scope e rende i test smoke per-step possibili.

**NON ora**: 0 source-bound test bloccanti, ma rischio UX su un area critica (creazione connectors) pre-prod. Aspettare stabilità.

---

## 2. `components/panels/ResponseConfigBuilder.jsx` (978 LOC)

### 2.1 Internal map

| Block | Lines | Purpose |
| --- | --- | --- |
| Imports + CSS | 10–11 | React (state/memo/callback) |
| `TRANSFORM_OPERATIONS` | 26–40 | catalog: substring, regex, replace, concat, ecc. |
| `PRESETS` | 42–47 | 4 preset (italiameteo_observations, geojson_features, simple_array, none) |
| **`ResponseConfigBuilder` main** | **49–615** | builder UI |
| State | 56–66 | `usePreset`, `rootPath`, `iterateOn`, `mappings`, `transforms`, `expandedPaths` (Set), `activeTab` (`structure`/`mappings`/`transforms`/`preview`), `previewData`, `previewError` |
| `jsonStructure` useMemo | 69–72 | analisi cached di sampleData |
| `generatedConfig` useMemo | 75–90 | shape backend output |
| `onChangeRef` + `useEffect` | 95–99 | **anti-loop** quando parent passa funzione inline |
| `toggleExpand`/`toggleIterate`/`toggleMapping`/`updateIterateAlias`/`updateMappingColumn` | 102–156 | tree actions |
| `addTransform`/`updateTransform`/`removeTransform` | 159–178 | transforms CRUD |
| `generatePreview` async | 181–200 | local extract (no API call) |
| `expandAll`/`collapseAll`/`autoDetectMappings` | 203–222 | bulk tree ops |
| Empty state guard | 224–233 | `if (!sampleData) return …` |
| Render: header + preset row | 235–258 | preset selector |
| Tabs UI (4 tabs) | 277–302 | structure / mappings / transforms / preview |
| Tab Structure (tree view + legend + autodetect) | 304–364 | `<JsonTreeNode />` recursive |
| Tab Mappings (iterate_on + mappings list + edit) | 366–441 | column rename UI |
| Tab Transforms (list + add) | 443–547 | transform editor |
| Tab Preview (table) | 549–599 | preview render |
| `JsonTreeNode` recursive component | 621–724 | tree row + checkbox + iterate toggle |
| **Utility functions** | 730–977 | pure functions: `analyzeJsonStructure` (733), `collectPaths` (791), `collectScalarFields` (806), `extractRowsFromConfig` (835), `recursiveIterate` (866), `extractRow` (885), `navigatePath` (905), `applyTransforms` (916), `formatSampleValue` (970) |

### 2.2 Local / derived / fetching / validation / rendering

- **Local state**: 8 useState (linee 56–66). `expandedPaths` è un `Set` (immutability handled in setter).
- **Derived (useMemo)**: `jsonStructure` (caching pesante), `generatedConfig` (output al parent)
- **Data fetching**: NESSUNO — il componente è puramente di presentazione/configurazione
- **Validation**: empty-state se `!sampleData`; `generatePreview` cattura eccezioni e setta `previewError`
- **Rendering**: 4 tab + tree recursive

### 2.3 Coupling

- **API**: nessuno
- **Canvas store**: nessuno
- **Modals**: nessuno
- **Caller**: solo `pages/ApiConnectionManager.jsx:13` (embed in Step 4–5)
- **Pure functions** (730–977) → **estraibili senza coupling**

### 2.4 UX / regression hotspots

| Hotspot | Lines | Why |
| --- | ---:| --- |
| `onChangeRef` pattern per evitare loop | 95–99 | parent passa lambda inline che cambia ref ad ogni render → senza ref, useEffect su `generatedConfig` reinvocato a infinito. Documentato in commento. |
| `analyzeJsonStructure` ricorsivo senza depth limit | 733–786 | input maligno (oggetto profondamente annidato) → stack overflow. `slice(0, 10)` su array primitivi (line 759) ma niente cap su oggetti. |
| `collectPaths` con `maxDepth` cap=3 (autoDetect) | 791–806 | scelta arbitraria; autodetect non vede campi a depth>3 |
| Set state pattern `setExpandedPaths(prev => { const next = new Set(prev); ... })` | 102–112 | corretto, ma non triviale. Refactor che semplifica a array forzerebbe re-creating la struttura. |
| `extractRowsFromConfig` runs lato client per preview | 835 | logica duplicata tra FE e BE — rischio drift (BE ha `app/core/arricchisci/api_connector.py` parsing) |
| `formatSampleValue` truncate a 50 char | 970 | hardcoded senza prop |
| Tab "preview" trigger `generatePreview` su click | 298 | viene rifatto ogni click (no caching) — preview di un sampleData costoso = laggy |
| `addTransform` con `select.value` letto via `document.getElementById('add-transform-source')` | 532–540 | accesso DOM diretto in React, non controllato — rompe SSR e a11y |

### 2.5 Test coverage

**Nessun test contract dedicato** in `frontend/tests/`. Coverage indiretta tramite `ApiConnectionManager.contract.test.jsx` (su flow Step 4–5).

### 2.6 Candidati extraction

| Candidato | Tipo | LOC | Coupling reale? |
| --- | --- | ---:| --- |
| **a. Pure functions in `responseConfig.js`** (`analyzeJsonStructure`, `collectPaths`, `collectScalarFields`, `extractRowsFromConfig`, `recursiveIterate`, `extractRow`, `navigatePath`, `applyTransforms`, `formatSampleValue`) | small | ~250 LOC (730–977) | **zero** — funzioni stateless, già pure |
| **b. `<JsonTreeNode />` in modulo separato** | small | ~100 LOC (621–724) | basso — solo callback props |
| **c. Tab views in 4 sub-component** (`StructureTab`, `MappingsTab`, `TransformsTab`, `PreviewTab`) | small | ~300 LOC | medio — condividono state derivato |

### 2.7 Commit decomposition

Recommendation: **small refactor**. Decomposition (2 commit):

| Commit | Change | LOC | Rollback | Test gate |
| --- | --- | --- | --- | --- |
| **c1** | Estrai pure functions (a) in `responseConfig.js`, modulo statico testabile | ~250 LOC | revert: re-import locale | aggiungere unit test su `analyzeJsonStructure` happy + cycle protection (manca oggi); ApiConnectionManager test 1 (rest_file edit) verde |
| **c2** | Estrai `<JsonTreeNode />` (b) in modulo `JsonTreeNode.jsx` | ~100 LOC | revert single | smoke: aprire un connector con sampleData → tab Struttura → expand/collapse + checkbox |

Effort: c1 2h (incluso test), c2 1h. Tot ~3h.

### 2.8 Visual smoke richiesto

- Aprire ApiConnectionManager → Edit di un connector → Step 4 → vedere tree JSON.
- Click espandi/comprimi nodi: behavior identico.
- Auto-detect button: stessi mappings selezionati.
- Tab Preview: stesse righe estratte.

### 2.9 Raccomandazione

**Small refactor (post-prod).** Pure functions extraction è zero-risk, abilita unit test reali sul parser (oggi inesistente). JsonTreeNode è già coeso.

NON estrarre i 4 tab body — sono semplici switch su `activeTab`, non c'è coupling reale che giustifichi il churn.

---

## 3. `components/transforms/ParameterInput.jsx` (974 LOC)

### 3.1 Internal map

| Block | Lines | Purpose |
| --- | --- | --- |
| Imports + CSS | 21–24 | React + PivotMappingBuilder |
| **`ColumnSelector`** sub-component | **31–107** | dropdown con multi/single + filter (`geometry`/`numeric`/`text`) + sourceLabel |
| **`StringListInput`** | **110–149** | tag list con add via Enter |
| **`AggregationListInput`** | **152–247** | per agg: column + function (count/sum/mean/min/max/first/last/concat) + concat extras (separator/distinct/skip_null) |
| **`ColumnOrderInput`** | **250–367** | drag & drop reorder (HTML5 native) |
| **`RenameListInput`** | **368–544** | from-column → to-name list |
| **`MaskingListInput`** | **545–650** | colonna + tipo masking (full/partial/email/phone/iban) |
| **`ConditionListInput`** | **651–736** | operator + value + result (case-when) |
| **`ParameterInput`** main switch | **742–973** | cases: string, number, boolean, select, column, columns, expression, string_list, aggregation_list, rename_list, masking_list, condition_list, pivot_mapping, column_order, default(text) |

### 3.2 Local / derived / fetching / validation / rendering

- **Local state**: solo per sub-component interattivi (`StringListInput.inputValue` line 112, `ColumnOrderInput.dragIdx`/`dragOverIdx` line 251)
- **Derived**: ognuno deriva da `value` prop (controllato dal parent)
- **Data fetching**: nessuno
- **Validation**: nessuna esplicita; trust nel parent
- **Rendering**: switch su `param.type` (lines 775–948)

### 3.3 Coupling

- **API**: nessuno
- **Canvas store**: nessuno
- **Caller** (chi importa `ParameterInput`): da grep — utilizzato dal modulo manipola transforms (registry executor). Singolo entry point.
- **Internal coupling**: `ColumnSelector` riusato da 3 case (`column`, `columns`, `geometry_column`) — coesione forte
- **External**: `PivotMappingBuilder` (modulo separato)

### 3.4 UX / regression hotspots

| Hotspot | Lines | Why |
| --- | ---:| --- |
| Switch su `type` con default→text | 775–948 | tipo mancante/typo in registry → silently rendered come text input. Add-time: nessuna log warning |
| `ColumnSelector` filter `geometry` matching `colType.includes('geometry')` | 44 | substring match → falso positivo se nome colonna include "geometry" testuale |
| `StringListInput` parsing: `value.split(',').map(s => s.trim())` | 111 | dato come "a, b, c" parsato come array — overload semantico |
| `ColumnOrderInput` HTML5 drag & drop | 250 | a11y bassa; touch device rotti |
| `RenameListInput` 545+ LOC con preview/auto-suggest | 368–544 | uno dei sotto-componenti più complessi; poco coperto |
| `MaskingListInput` types hardcoded | 545–650 | aggiungere mask type richiede edit del file |
| `effectiveSourceLabel` deriva da `param.source` con `'left'` / `'right'` magic strings | 766–771 | accoppiato al backend join semantics |
| `default value`/`value ?? defaultValue ?? ''` chain | ovunque | precedenza non sempre evidente |

### 3.5 Test coverage

**Nessun test contract dedicato.** Coverage indiretta minima via `ManipolaEditor.contract.test.jsx` (probabilmente).

### 3.6 Candidati extraction

| Candidato | Tipo | LOC | Coupling reale? |
| --- | --- | ---:| --- |
| **a. 6 list inputs in modulo separato** (`StringListInput`, `AggregationListInput`, `ColumnOrderInput`, `RenameListInput`, `MaskingListInput`, `ConditionListInput`) → file `transformInputs.jsx` | small | ~600 LOC (110–736) | basso — sub-component standalone |
| **b. `ColumnSelector` in modulo separato** | small | ~80 LOC (31–107) | basso — riusabile da altri panel (es. JoinPanel) |

### 3.7 Commit decomposition

Recommendation: **small refactor**. Decomposition (2 commit):

| Commit | Change | LOC | Rollback | Test gate |
| --- | --- | --- | --- | --- |
| **c1** | Estrai `ColumnSelector` in `ColumnSelector.jsx` (b) | ~80 LOC | revert single | smoke: aprire un nodo manipola con `column` o `columns` parameter → dropdown render OK |
| **c2** | Estrai 6 list inputs (a) | ~600 LOC | revert single | smoke: rename, masking, condition, aggregation, column_order — ognuno render+edit OK |

Effort: c1 1h, c2 2h. Tot ~3h.

### 3.8 Visual smoke richiesto

- Aprire un nodo manipola di tipo "rename" → Step pannello con RenameListInput → aggiungere/modificare/rimuovere righe.
- Aprire "aggregation" → AggregationListInput con concat → verificare extras (separator/distinct/skip_null).
- Aprire "column_order" → drag & drop sull'ordine.
- Aprire "case_when" (condition_list) → verificare risultati.

### 3.9 Raccomandazione

**Small refactor (post-prod).** Vantaggio: ognuno dei 6 list input diventa testabile in isolamento (oggi tutti hidden dentro un singolo file 974 LOC). Bassissimo rischio.

---

## 4. `components/panels/ApiEnrichNode.jsx` (944 LOC)

### 4.1 Internal map

| Block | Lines | Purpose |
| --- | --- | --- |
| Imports | 15–22 | uploadFile, getApiConnection, ReactFlow Handle/Position/useReactFlow/useStore, columnUtils, useCanvasStore, RunHistoryModal |
| **`ApiEnrichNode` main** | **33–841** | nodo ReactFlow custom |
| `isExecutable` flag | 40 | `data.connection_type === 'rest_file'` |
| `incomingEdgeCount` (RF store) | 43–45 | re-render trigger su connessione cambiata |
| `sourceColumnsKey` (RF store) | 47–61 | re-render trigger sulle colonne upstream (B-FE-69 fix per enricher upstream) |
| State (6 useState) | 64–67 | `fetchedApi`, `loadingApi`, `isRunHistoryOpen`, `projectId` (canvas store), `fetchedApiIdRef` |
| Derived data | 71–83 | `apiParameters`, `apiOutputMapping` (from `data` o `fetchedApi` o `schema_fields`), `apiName`, `apiEndpoint`, `staticParameters`, `paramMapping` |
| `useEffect` fetch API details | 87–92 | trigger se `data.api_id` ma manca `parameters` o `api_name` (retrocompat) |
| `loadApiDetails(apiId)` async | 94–114 | `getApiConnection(apiId)` → setFetchedApi |
| `connectedFileSource` useMemo | 117–134 | rilevamento file_source upstream + `access_denied` propagato (RB.4.C) |
| `upstreamAccessDenied` useMemo | 139–150 | qualsiasi nodo upstream con access_denied (RB.4.C defense-in-depth) |
| `handleExecParamChange(key, value)` | 153–167 | setNodes update exec_params |
| `handleOutputKindChange(value)` | 173–177 | canvas store update output_kind (rest_file → imdf tag) |
| `normalizeType(col)` / `mapApiTypeToStandard(apiType)` | 183–201 | type mapping helpers |
| `inputColumns` useMemo | 204–252 | dispatch per source type: `staticValues` (fields → columns), `api_enrich` (output_mapping merge), normal (data.columns) |
| `outputColumns` useMemo | 255–281 | inputColumns + apiOutputMapping additions |
| **`useEffect` propagate columns** | **287–303** | **`setNodes` con normalize+compare per evitare loop infinito ReactFlow ↔ Zustand** |
| **`useEffect` auto-mapping** | **306–328** | match per nome colonna ↔ parametro (skip se già mappato) |
| `handleMappingChange(paramName, columnName)` | 330–335 | canvas store updateNodeData |
| `handleDelete` | 337–339 | canvas store deleteNode |
| **`validation` useMemo** | **343–398** | dispatch: upstreamAccessDenied → file_source access_denied → no api_id → executable file_source → inputColumns required → missingParams → output_mapping warning |
| `execConfig` | 400 | response config |
| **RENDER: EXECUTABLE MODE (rest_file)** | **405–527** | header viola, output_kind selector, extra_files (ExtraFileSlot), file source link, parametri, output info, validation status |
| **RENDER: STANDARD API MODE** | **529–840** | api_method header, fixed params, mapping params (datalist autocomplete + template support `{X},{Y}`), execute_if (conditional), output preview chips, input columns chips, advanced options (continue_on_error, max_errors, skip_null_params), validation status |
| RunHistoryModal + Handles | 818–838 | history + RF I/O handles |
| **`ExtraFileSlot` sub-component** | **846–943** | upload inline file extra (per rest_file fields source='extra_input') con uploadFile API |

### 4.2 Local / derived / fetching / validation / rendering

- **Local state**: 6 useState
- **External state subscriptions**: `useReactFlow`, `useStore` (RF, 2 selettori), `useCanvasStore` (1 selector)
- **Derived (useMemo)**: 4 (`connectedFileSource`, `upstreamAccessDenied`, `inputColumns`, `outputColumns`, `validation`)
- **Data fetching**: `getApiConnection(apiId)` (lazy on mount se data incompleto), `uploadFile` (in ExtraFileSlot)
- **Validation (UI)**: `validation` useMemo computed con 7 branch (upstream access → executable mode → standard required params → output_mapping warning)
- **Rendering**: 2 modalità (executable vs standard), entrambe ~200+ LOC

### 4.3 Coupling

- **API**: `api/files.uploadFile` (line 15), `api/apiConnections.getApiConnection` (line 16) — uso diretto **importante** (defense-in-depth: passano per boundary, non chiamano http direttamente — vedi `apiEnrichNode.boundary.contract.test.js`)
- **Canvas store**: usa `useCanvasStore.getState().updateNodeData(id, ...)` ovunque (8+ siti) + `useCanvasStore.getState().deleteNode(id)` (337) + `useCanvasStore(s => s.projectId)` (67)
- **ReactFlow**: `useReactFlow().setNodes/getNodes/getEdges` (34) + `useStore` selettivi (43, 47)
- **Modal**: `RunHistoryModal`
- **Helpers**: `columnUtils.{getColumnTypeIcon, getColumnTypeLabel}`

### 4.4 Call graph

Inbound:
- `nodeFactories.js` o `pages/ArricchisciEditor.jsx` registra `api_enrich` come custom node type ReactFlow → ReactFlow renderer chiama `<ApiEnrichNode {...props} />`

Internal:
- standard mode: `useEffect propagate columns` (287) → `setNodes(...)` con compare = anti-loop
- standard mode: `useEffect auto-mapping` (306) → `useCanvasStore.updateNodeData(id, ...)`
- ExtraFileSlot: `uploadFile` → `onUploaded({file_source_id, ...})` → parent updateNodeData

### 4.5 UX / regression hotspots

| Hotspot | Lines | Why |
| --- | ---:| --- |
| **Anti-loop in propagate columns useEffect**: `normalize(JSON.stringify(...))` compare prima di setNodes | 287–303 | Pre-fix: setNodes su nuovo array ad ogni render → ReactFlow re-render → setNodes loop infinito browser. Documented. |
| **`fetchedApiIdRef` set-only-on-success** | 104 | Senza, errore di rete → re-fetch sul prossimo render (loop). Documented. |
| **B-FE-69 `sourceColumnsKey`** che include output_mapping di enricher upstream | 55–60, 233–238, 244–249 | Race condition: api_enrich upstream non ha ancora `data.columns`, ma ha output_mapping → senza il merge, il nodo a valle non vede colonne |
| **RB.4.C upstream `access_denied`** propagation | 117–134, 139–150, 348–353 | Frontend pre-flight: blocca run se file_source upstream è marked dal backend non visibile al gruppo → defense-in-depth, runtime check resta backend |
| Auto-mapping case-insensitive `c.name.toLowerCase() === param.name.toLowerCase()` | 320 | Match aggressivo; collisioni se due colonne differiscono solo per case |
| `setNodes` (RF) vs `useCanvasStore.updateNodeData` (Zustand) — usati alternati | 154 vs 174, 326, 332, 338 | Due fonti di verità sui nodi del canvas. Documented in commenti che useCanvasStore è "single source of truth per save"; ma `setNodes` viene usato per `exec_params` (154–167). Rischio drift. |
| `isExecutable` rendering 2 modalità completamente diverse | 405–527 vs 539–840 | 800 LOC di rendering condizionale; cambio struttura senza testare entrambi rompe uno |
| `ExtraFileSlot` upload inline | 846–943 | usa `uploadFile` boundary (test boundary lo blinda); errore mostra `err?.response?.data?.detail` o `err.message` |
| `data._advOpen` via canvas store updateNodeData | 737 | UI flag persisted nel canvas — ogni canvas save include flag toggle |
| Datalist autocomplete `<datalist id={`api-enrich-cols-${id}`}>` | 599 | id univoco per nodo, OK |

### 4.6 Test coverage

**1 boundary test:**

`frontend/tests/api/apiEnrichNode.boundary.contract.test.js` (~100 LOC):

| Test | Line | Cosa protegge |
| --- | ---:| --- |
| `does NOT call http.post on /files/upload directly` | 33 | il file source-scan: nessun `http.post('/files/upload')` |
| `does NOT force Content-Type: multipart/form-data on upload` | 41 | scan source per `Content-Type` manuale |
| `imports uploadFile from the api/files boundary module` | 49 | scan import statement |
| `actually invokes uploadFile in the upload code path` | 56 | scan source per `uploadFile(` invocation |
| `does NOT import http directly from api/http (C3.4)` | 64 | scan import |
| `does NOT call http.<verb>(...) anywhere (C3.4)` | 72 | scan source per http.get/post/put/delete |
| `imports getApiConnection from api/apiConnections (C3.4)` | 79 | scan import |
| `actually invokes getApiConnection in the loadApiDetails path` | 84 | scan source per `getApiConnection(` |

**Tipo: source-bound (S, frontend)** — boundary architectural guard. Tutti basati su scan del source code letto da disco. **Da preservare** ma non blocca refactor (scan pattern resta valido se le boundaries sono mantenute).

### 4.7 Candidati extraction

| Candidato | Tipo | LOC | Coupling reale? |
| --- | --- | ---:| --- |
| **a. Split rendering executable vs standard mode** in 2 sub-component (`<ApiEnrichNodeExecutable />` e `<ApiEnrichNodeStandard />`) | medium | ~120 + ~300 LOC (405–527 + 539–840) | **sì** — 2 modalità totalmente disgiunte, 800 LOC di JSX condizionale; il file è in pratica **2 componenti incollati** |
| **b. Estrai useEffects e useMemos in custom hook** `useApiEnrichNodeData(id, data)` | medium | ~200 LOC (43–328) | **sì** — il nodo è memoria + render; separarli abilita test del hook senza ReactFlow |
| **c. `ExtraFileSlot` in modulo separato** | small | ~100 LOC (846–943) | basso — già coeso |
| **d. `<ValidationStatus validation={..} />`** sub-component | small | ~10 LOC ripetuto in entrambe le modalità | basso |

### 4.8 Commit decomposition

Recommendation: **medium refactor**. Decomposition (3 commit):

| Commit | Change | LOC | Rollback | Test gate |
| --- | --- | --- | --- | --- |
| **c1** | Estrai `ExtraFileSlot` (c) + `<ValidationStatus />` (d) | ~110 LOC | revert single | boundary test passa invariato (scan importa stessi simboli); smoke: rest_file con extra_files upload+remove; standard mode con missing params |
| **c2** | Estrai custom hook `useApiEnrichNodeData(id, data, isExecutable)` (b) — sposta i 4 useMemo + 2 useEffect + state | ~200 LOC | revert moderate-risk: l'anti-loop pattern (287–303) deve restare; `fetchedApiIdRef` deve restare set-on-success | boundary test passa; smoke: connetti file_source → api_enrich → verifica colonne propagate, auto-mapping fa match |
| **c3** | Split rendering in `<ApiEnrichNodeExecutable />` + `<ApiEnrichNodeStandard />` (a) | ~420 LOC | revert moderate-risk: Handle position/id devono restare uguali (RF I/O contract); validation logic deve restare nel parent o passata down | boundary test passa; smoke: entrambe le modalità render OK + validation OK |

Effort: c1 2h, c2 4h, c3 4h. Tot ~10h.

### 4.9 Visual smoke richiesto

- **Standard mode**: connetti project_source → api_enrich (geocoding) → mappa parametri → vedi output preview chips.
- **Standard mode con enricher upstream** (B-FE-69): api_enrich → api_enrich → secondo nodo vede output_mapping del primo come input.
- **Executable mode (rest_file)**: connetti file_source → api_enrich (rest_file IMDF) → seleziona output_kind="imdf" → upload extra file → verifica access_denied propagation se applicabile.
- **Anti-loop**: aprire console, verificare nessun loop di re-render quando il canvas è caricato.
- **Auto-mapping**: lasciare `param_mapping` vuoto → al collegamento, mappatura per nome match.

### 4.10 Raccomandazione

**Medium refactor (post-prod).** Razionale: il file è in pratica due componenti diversi (executable vs standard) accoppiati da un'unica entry. Il custom hook (c2) è il vero unlock: rende testabili gli invariants di **anti-loop** (287–303) e **auto-mapping** (306–328) senza dover montare ReactFlow.

**Boundary contract test** (apiEnrichNode.boundary) **non blocca** il refactor — verifica gli import via scan source, sopravvive a MOVE/RENAME purché gli import statement restino sul nuovo entry file.

---

## 5. `pages/FileManager.jsx` (887 LOC)

### 5.1 Internal map

| Block | Lines | Purpose |
| --- | --- | --- |
| Imports | 6–26 | useToast, useConfirmModal, useNavigate, useAuth, useHexCanvas, ConfirmDialog, AllowedGroupsSelector, AppSidebar, ModalWrapper, permissions |
| `FORMAT_CONFIG` (csv/xlsx/json/geojson/gpkg/shp/gdb/zip/dxf) | 29–38 | UI taxonomy |
| `getFormatConfig`, `formatFileSize`, `FILTER_TABS`, `SPATIAL_FORMATS` | 39–55 | helpers |
| `ThemeToggle` | 58–76 | local theme |
| **`FileManager` main** | **78–579** | page |
| State (~30 useState) | 87–133 | files, loading, error, search, activeTab, sharingFile + upload modal state (file, name, desc, layer, sheet, uploading, uploadError, dragActive, uploadStep) + columns step state (availableColumns, selectedColumns, loadingColumns, detectedSrid, userSrid, hasGeometryColumn) + layer modal state + multi-layer wizard state (isMultiLayerFile, checkedLayers, layerColumns, layerSelectedCols, activeLayerTab, loadingLayerCols) + preview modal state |
| `loadFiles` useCallback async | 136–147 | `getFiles()` + setFiles |
| `useEffect` mount | 149 | bootstrap |
| Drag & drop handlers | 152–168 | handleDrag, handleDrop, handleFileSelect |
| `_loadColumnsForLayer` async | 171–185 | per multi-layer: `previewFileTemp(file, layer, sheet)` |
| `handleLayerTabChange`, `handleToggleLayerCol`, `handleToggleAllLayerCols` | 187–209 | layer tabs UX |
| **`handleMultiLayerUpload`** async | **212–227** | `uploadMultiLayer(...)` |
| **`handleNextStep`** async | **230–284** | dispatch: multi-layer/sheet detection → preview → columns step OR binary upload diretto |
| `handlePrevStep` | 286–289 | reset columns step |
| `handleToggleColumn`, `handleSelectAllColumns` | 291–292 | columns selection |
| **`handleUpload`** async | **295–314** | `uploadFile(...)` → se gpkg/gdb/xlsx con multi-layer → mostra LayerModal |
| `handleConfirmLayer` | 316 | layer modal confirm |
| **`handlePreview` async** con 404 race-cleanup (RB.5) | **319–336** | `getFilePreview(file.id, 50)` + 404 → reload + toast |
| `handleDelete` con confirmModal | 339–344 | `deleteFile` |
| `resetUploadModal`, `resetLayerModal` | 347–353 | state cleanup |
| `filteredFiles` filter | 356–363 | search + tab |
| Page render: sidebar + topbar + toolbar + drop-zone + grid + modals | 374–578 | UI |
| **`FileCard`** sub-component | **581–640** | card render con format icon, columns, share/delete |
| **`UploadModal`** sub-component | **643–820** | 3-step wizard (file → columns → confirm) con multi-layer support |
| **`LayerModal`** sub-component | **821–854** | post-upload layer selection (gpkg/xlsx) |
| **`PreviewModal`** sub-component | **856–886** | tabular preview |

### 5.2 Local / derived / fetching / validation / rendering

- **Local state**: ~30 useState (87–133)
- **Derived (no useMemo)**: `filteredFiles` (computed every render) — OK perché `files` cambia raramente
- **Data fetching**: `getFiles`, `previewFileTemp`, `uploadFile`, `uploadMultiLayer`, `getFileLayers`, `getFilePreview`, `deleteFile`
- **Validation**: client-side: `!uploadFile_ || !uploadName.trim()` (231); `selectedColumns.length === 0` (217)
- **Rendering**: page + 4 sub-component modali

### 5.3 Coupling

- **API**: `api/files` (7 funzioni)
- **Modals**: `ConfirmDialog`, `ModalWrapper`, `AllowedGroupsSelector` (sharing, super-only)
- **Hooks**: useToast, useConfirmModal, useNavigate, useAuth, useHexCanvas
- **Canvas store**: nessuno
- **Permissions**: `can.uploadFile(user)`, `role.isSuperadmin(user)`

### 5.4 UX / regression hotspots

| Hotspot | Lines | Why |
| --- | ---:| --- |
| **RB.5 404 cleanup**: `handlePreview` su file deleted-during-list → toast + auto-reload | 329–333 | Race condition fix per altre tab/scheduler/share revoke |
| `handleNextStep` dispatch multi-layer (gpkg/gdb) vs multi-sheet (xlsx) vs single | 235–252 | Tre branch separati; ognuno setta state diverso. Mancanza di un step intermedio = state machine complicata |
| `handleNextStep` early upload binary (zip/gz/tar/7z/rar/dxf) | 270–280 | salta lo step columns; setta `uploadStep` prima del upload finally |
| `handleUpload` post-upload re-detect multi-layer | 303–309 | se gpkg/gdb/xlsx → fetch `getFileLayers` → mostra LayerModal — secondo round dopo upload |
| `handleMultiLayerUpload` accetta sia `layer` che `sheet` per same flow | 213–216 | xls/xlsx → sheet; gpkg/gdb → layer |
| Multi-layer wizard state (5 useState) | 122–128 | molto state con dipendenze: `checkedLayers`, `layerColumns`, `layerSelectedCols`, `activeLayerTab` — sync su tab change |
| Drop-zone `onClick` apre modal anche se file già presente | 432 | UX: doppio path (drop + click) |
| `useConfirmModal()` per delete (FileManager) vs `confirm()` browser (ApiConnectionManager:228) | 339, 228 | inconsistent UX cross-page |
| Non-superadmin sharing button hidden | 624–631 | Ok, ma il check è duplicato (FileCard riceve `isSuperadmin` prop e check) |

### 5.5 Test coverage

`frontend/tests/pages/FileManager.contract.test.jsx` (~120 LOC):

| Test | Line | Cosa protegge |
| --- | ---:| --- |
| `loads file cards and opens the preview flow with the backend payload` | 104 | end-to-end: getFiles → render cards → click preview → getFilePreview → preview modal |

**1 test contract**, behavior-bound (vi.mock + userEvent + waitFor). Coverage molto bassa per un page critico (upload multi-layer non testato).

### 5.6 Candidati extraction

| Candidato | Tipo | LOC | Coupling reale? |
| --- | --- | ---:| --- |
| **a. Custom hook `useFileUploadWizard()`** che incapsula upload step state + handlers | medium | ~150 LOC | **sì** — multi-layer wizard state ha 5 useState + 5 handler interconnessi; hook riusabile |
| **b. `<UploadModal />` già estratto** (643–820) → ulteriore split in `<UploadStep1File />`, `<UploadStep2Columns />`, `<UploadStep2MultiLayer />`, `<UploadStep3Confirm />` | medium | ~180 LOC | **sì** — è un mini-wizard con 3 step + multi-layer variant |
| **c. `<FileCard />` in modulo separato** | small | ~60 LOC | basso — già coeso |
| **d. `useFileFiltering(files, search, activeTab)` hook** | small | ~30 LOC | basso |

### 5.7 Commit decomposition

Recommendation: **small refactor** (a + c sono i vincenti).

| Commit | Change | LOC | Rollback | Test gate |
| --- | --- | --- | --- | --- |
| **c1** | Estrai `<FileCard />` (c) in modulo separato | ~60 LOC | revert single | FileManager test passa invariato; smoke: card render con/senza columns, sharing button visible per super |
| **c2** | Estrai `useFileUploadWizard()` (a) — incapsula upload modal state + handlers | ~150 LOC | revert moderate-risk: lo state è disteso fra page e wizard; il hook deve esporre `state + actions` | smoke: upload single-file CSV, upload multi-layer GPKG, upload multi-sheet XLSX, upload binary ZIP |

Effort: c1 1h, c2 3h. **Tot ~4h.**

NON fare (b) ora: è ulteriore decomposizione interna del UploadModal — payoff marginale rispetto al churn.

### 5.8 Visual smoke richiesto

- Upload CSV: nome → step columns → upload OK.
- Upload GPKG multi-layer: detect → checkedLayers tab → toggle columns per layer → upload multi-layer.
- Upload XLSX multi-sheet: idem.
- Upload ZIP binary: skip columns, upload diretto.
- Drag & drop: drop fuori modal apre modal con file pre-caricato.
- 404 race: aprire 2 tab, eliminare file in tab A, click preview tab B → toast + reload.
- Delete con `useConfirmModal` (non browser confirm).

### 5.9 Raccomandazione

**Small refactor (post-prod).** Hot extraction `useFileUploadWizard` (c2) sblocca testabilità unit dei branch upload (oggi solo 1 test e2e). Non aprire (b) — il UploadModal è già un sub-component sufficiente.

---

## 6. `components/nodes/result/ResultNodeStandardView.jsx` (880 LOC)

### 6.1 Internal map

| Block | Lines | Purpose |
| --- | --- | --- |
| Imports | 37–45 | useCanvasStore, columnUtils, RunHistoryModal |
| `COLUMNS_PREVIEW = 5` constant | 47 | UI cap |
| **`ResultNodeStandardView` memo** | **49–877** | nodo result custom |
| Props destructure | 49–58 | `data`, `id`, `upstreamColumns`, `hasOnPreview`, `hasOnExecute`, `onValidate`, `onExecute`, `onPreview` |
| `updateNodeData` selector | 59 | canvas store action |
| State (7 useState) | 61–67 | `isEditingFileName`, `isRunHistoryOpen`, `tempFileName`, `executionMode` (cache/recalculate), `showGroupBySection`, `showOrderBySection`, `columnsExpanded` |
| Project type flags | 70–73 | `isManipulate`, `isArricchisci`, `isEstrai`, `hasExecutionOptions` |
| Data extraction da `data.*` | 75–92 | fileName, format, limit, distinct, geomColumn, group_by_*, order_by_*, having_* |
| `geomSourceColumns` derived | 98–109 | dispatch: manipulate/arricchisci → upstreamColumns; estrai → merge cols+upstream |
| `availableGeomColumns` derived | 111–116 | filter geometry types, format display |
| `hasGeometryColumns` | 118 | flag |
| **Auto-reset effect**: format CSV se geo perde columns | 121–125 | side-effect su `updateNodeData` |
| `availableColumnsRaw` + dedup `availableColumns` | 128–133 | per GROUP BY (estrai) |
| `manipolaColumnsRaw` + dedup `manipolaColumns` | 136–142 | per GROUP BY (manipola) |
| `groupByAvailableColumns` | 144 | dispatch |
| `nodeVariant`/`config`/`executeLabel` | 147–153 | UI strings |
| `formats` array (csv/json/xlsx/geojson/gpkg/shp) | 155–162 | format list |
| `aggregateFunctions` | 166–172 | COUNT/SUM/AVG/MIN/MAX |
| **Filename handlers** | 175–185 | edit/save/format change/distinct/limit |
| **GROUP BY handlers** | 187–242 | toggle, add, remove, addAggregation, updateAggregation, removeAggregation |
| **ORDER BY handlers** | 243–268 | toggle, add, remove, toggleDirection |
| **HAVING handlers** | 270–293 | toggle, add, remove, update |
| **Render: filename section** | 297–329 | edit-in-place |
| **Render: format section** | 331–388 | standard formats + geo formats + geom column selector |
| **Render: columns output (Estrai)** | 390–435 | expandable list di col types |
| **Render: query options (Estrai)** | 437–467 | DISTINCT + LIMIT |
| **Render: GROUP BY (Estrai+Manipola)** | 469–610 | collapsible: enable + columns selector + aggregations + preview |
| **Render: ORDER BY (Estrai+Manipola)** | 612–686 | collapsible: enable + columns + ASC/DESC toggle |
| **Render: HAVING (when GROUP BY active)** | 688–768 | enable + condition list |
| **Render: execution mode (Manipola+Arricchisci)** | 770–819 | radio: cache vs recalculate |
| **Render: action buttons** | 821–851 | Valida + Preview + Execute |
| **Render: footer + history modal** | 853–873 | cronologia run |

### 6.2 Local / derived / fetching / validation / rendering

- **Local state**: 7 useState (linee 61–67)
- **Derived**: 6 (`geomSourceColumns`, `availableGeomColumns`, `hasGeometryColumns`, `availableColumns`, `manipolaColumns`, `groupByAvailableColumns`) — **NON** useMemo, ricalcolate ad ogni render
- **Data fetching**: nessuno (controllato dal parent + canvas store)
- **Validation**: nessuna esplicita; `onValidate` è prop callback (parent fa la validation)
- **Rendering**: 8 sezioni condizionali con collapsible (showGroupBySection / showOrderBySection)

### 6.3 Coupling

- **API**: nessuno
- **Canvas store**: `useCanvasStore(s => s.updateNodeData)` (1 selector) + chiamate sparse (oltre 20 invocazioni di `updateNodeData(id, {...})`)
- **Modal**: `RunHistoryModal`
- **Helpers**: `columnUtils.{getColumnTypeIcon, getColumnTypeLabel, getColumnSRID, isGeometryType}`
- **Caller**: probabilmente `nodes/result/ResultNode.jsx` (composition con `ResultNodeImdfView`)

### 6.4 UX / regression hotspots

| Hotspot | Lines | Why |
| --- | ---:| --- |
| Auto-reset format=CSV se geo perde columns | 121–125 | Side-effect in useEffect → ogni render con `hasGeometryColumns=false` AND `isGeoFormat=true` triggera updateNodeData. Anti-loop via deps [hasGeometryColumns, isGeoFormat, id] (no [updateNodeData] |
| Derived NON-memoized (`geomSourceColumns`, `availableColumns`, `manipolaColumns`) | 98–144 | Ricalcolate ogni render. OK perché input piccoli, ma dataset >100 col diventa costoso |
| `executionMode` local state (NON sync con data) | 64 | Cache/recalculate mode è ephemeral — non persisted nel canvas. Cambia tra render parent/child se mount/unmount |
| `handleAddOrderByColumn` early-return su duplicate | 250 | OK ma silente |
| `aggregations[i].alias` re-derivato su ogni `updateAggregation` con field='column'/'function' | 224–227 | Sovrascrive alias custom dell'utente — bug UX latente |
| Section collapse state (showGroupBySection/showOrderBySection) NON persisted | 65–66 | Ogni mount li resetta a false |
| Geo format reset implicito su perdere colonne geometriche | 121 | UX: l'utente seleziona shapefile, poi disconnette nodo geo → format auto-cambia a CSV senza notifica |
| `aggregateFunctions` vs aggregateFunctions usato in HAVING — duplicato semantico | 166, 711 | Stesso array, accoppiamento cross-section |
| `having_conditions` con `function='COUNT'`, `column='*'` defaults | 280 | OK ma assume sempre COUNT(*) come start |
| `data.canvas_id` || `data.project_id` | 870 | Backward compat fallback per RunHistoryModal |

### 6.5 Test coverage

`frontend/tests/components/ResultNodeStandardView.contract.test.jsx` (220 LOC):

| Test | Line | Cosa protegge |
| --- | ---:| --- |
| `render base ESTRAI: mostra sezione formato + filename + bottone Valida` | 86 | smoke render base |
| `format button click → updateNodeData({format})` | 93 | format change handler |
| `filename editor: click display → input → blur salva con updateNodeData` | 102 | edit-in-place |
| `DISTINCT toggle (ESTRAI) → updateNodeData({distinct})` | 118 | distinct |
| `hasOnPreview=false: bottone Preview NON renderizzato` | 126 | conditional render |
| `hasOnPreview=true: click Preview → onPreview invocato` | 131 | callback |
| `hasOnExecute=false: bottone Esegui NON renderizzato` | 138 | conditional render |
| `hasOnExecute=true ESTRAI: click Esegui → onExecute({force_recalculate:false}) default` | 143 | execution mode default |
| `MANIPOLA con radio "recalculate" → onExecute({force_recalculate:true})` | 151 | execution mode toggle |
| `click Valida → onValidate invocato` | 164 | callback |
| `executeLabel deriva da variante: ESTRAI→Esegui Query, MANIPOLA→Esegui Pipeline, ARRICCHISCI→Esegui Arricchimento` | 171 | label dispatch |
| `variante CSS manipola: bottone execute ha classe contenente "manipola"` | 204 | CSS variant |
| `cronologia run: click apre RunHistoryModal` | 212 | modal trigger |

**13 test, tutti behavior-bound.** Mock pattern `useCanvasStoreMock` con `storeRef.current = { updateNodeData }`. Coverage solida sul base flow, **manca**: GROUP BY, ORDER BY, HAVING, geo format reset, columns expand.

### 6.6 Candidati extraction

| Candidato | Tipo | LOC | Coupling reale? |
| --- | --- | ---:| --- |
| **a. Estrai `<GroupBySection />`** (469–610) | small | ~140 LOC | basso — input controllato (groupByAvailableColumns, groupByEnabled, groupByColumns, aggregations, handlers) |
| **b. Estrai `<OrderBySection />`** (612–686) | small | ~75 LOC | basso |
| **c. Estrai `<HavingSection />`** (688–768) | small | ~80 LOC | basso (richiede groupBy attivo) |
| **d. Estrai `<FormatSection />`** (331–388) | small | ~60 LOC | basso |
| **e. Custom hook `useResultNodeData(data, id)`** che incapsula derived flags | small | ~80 LOC | basso |

### 6.7 Commit decomposition

Recommendation: **small refactor**. Decomposition (3 commit):

| Commit | Change | LOC | Rollback | Test gate |
| --- | --- | --- | --- | --- |
| **c1** | Estrai `<GroupBySection />` (a) + `<OrderBySection />` (b) + `<HavingSection />` (c) | ~295 LOC | revert single | tutti 13 i test passano (testano il top-level component, non sezioni interne); aggiungere smoke su GROUP BY enable/add/remove |
| **c2** | Estrai `<FormatSection />` (d) | ~60 LOC | revert single | test 2 (format button click) e geo-warning passa; smoke: shapefile → seleziona geom column |
| **c3** | (Opzionale) Estrai `useResultNodeData(data, id)` (e) | ~80 LOC | revert moderate-risk: derived flags devono restare consistenti | tutti 13 i test passano |

Effort: c1 3h, c2 1h, c3 (opzionale) 2h. **Tot ~4–6h.**

### 6.8 Visual smoke richiesto

- Estrai project: edit filename, change format, toggle DISTINCT, set LIMIT, GROUP BY con aggregations, ORDER BY ASC/DESC, HAVING.
- Manipola project: toggle execution mode cache/recalculate, GROUP BY, ORDER BY.
- Arricchisci project: execution mode buttons.
- Geo format → seleziona shp → vedi geom column selector → disconnetti upstream → format auto-reset CSV.
- Cronologia run modal apertura.

### 6.9 Raccomandazione

**Small refactor (post-prod).** Estrazione di GROUP BY/ORDER BY/HAVING/FORMAT in 4 sub-component aumenta testabilità e riduce il monolite render. Bassissimo rischio (test esistenti sono già behavior-bound, sopravvivono).

---

## 7. `pages/ProjectList.jsx` (862 LOC)

### 7.1 Internal map

| Block | Lines | Purpose |
| --- | --- | --- |
| Imports | 6–24 | hooks, api/{projects,features,files,schedules,datasources,errors}, hexCanvas, modals, AppSidebar, ModalWrapper, permissions |
| **`ProjectList` main** | **27–692** | dashboard page |
| State | 33–61 | projects, connections (db+arcgis), apiConnections, files, schedules, dataSources, eseguibili, loading, creating, modal flags (showModal, publishProject, executeEseguibileId, showEsegSelector), new project form, projectSources/projectFileSources maps, search, theme |
| `toggleTheme` | 67–72 | theme local |
| `useEffect` mount + hash scroll | 75–91 | bootstrap + scroll-to-section |
| **`loadData` async parallelo** | **93–134** | `Promise.all` su 8 endpoint (listProjects, listConnections, listApiConnections, getFiles, listSchedules, listFeatureConnections, listEseguibili, listDataSources) |
| `handleCreateProject` async | 136–151 | createProject + reload |
| **`handleDeleteProject` con cascade 409 + project_cleanup_partial** | **153–189** | handleCascade409 + status 500 detail.error="project_cleanup_partial" + fallback err.message |
| `handleDeleteEseguibile` | 194–203 | deleteEseguibile + reload |
| `openEsegSelector` | 205–208 | trigger eseg modal |
| `handleOpenProject(p)` | 210–214 | dispatch route per project_type (manipola → /editor/manipola/:id, arricchisci → /arricchisci/:id, estrai → /project/:id) |
| `openModalForType(type)` | 216–220 | new project modal |
| Helpers: `getConn`, `getProjectById`, `getSourceProjects`, `getScheduleCount`, `activeSchedules`, `formatDate` | 222–242 | derived utilities |
| `estraiProjects`/`manipolaProjects`/`arricchisciProjects` useMemo | 244–246 | filter by type |
| `filterBySearch(list)` | 248–251 | search filter |
| Page render: sidebar + topbar (search + theme + new) + stats row + 4 sezioni (Estrai/Manipola/Arricchisci/Eseguibili) + new project modal + eseg selector modal + delete confirm | 264–691 | UI |
| **`ProjectSection`** sub-component | **695–720** | section header + cards grid + empty state |
| **`ProjectCard`** sub-component | **723–774** | card render con status, schedule count, actions |
| **`EseguibileCard`** sub-component | **777–815** | eseg card |
| **`useEmptyHexCanvases(theme)`** hook | **818–853** | canvas hex art (theme-aware) |
| `EmptyHexDrawer` wrapper | 857–860 | hook invoker |

### 7.2 Local / derived / fetching / validation / rendering

- **Local state**: ~15 useState
- **Derived (useMemo)**: `estraiProjects`, `manipolaProjects`, `arricchisciProjects`, `activeSchedules`
- **Derived (no useMemo, useCallback)**: `getScheduleCount`
- **Data fetching**: `loadData` con Promise.all su 8 endpoints (con `.catch(() => default)` fallback per non-bloccare il page intero)
- **Validation**: `!newProjectName.trim()` (138); `newProjectType === 'estrai' && !newProjectConnId` (139)
- **Rendering**: 4 ProjectSection + Eseguibili section + 3 modal

### 7.3 Coupling

- **API**: 8 boundary modules (`api/index.js`, features, files, schedules, datasources, errors)
- **Modals**: `PublishEseguibileModal`, `EseguibileExecuteWizard`, `ModalWrapper`, `AppSidebar`
- **Hooks**: useToast, useNavigate, useLocation, useAuth, useHexCanvas
- **Canvas store**: nessuno
- **Permissions**: `role.isAdminOrAbove(user)`, `can.createProject(user)`

### 7.4 UX / regression hotspots

| Hotspot | Lines | Why |
| --- | ---:| --- |
| `loadData` parallelo con `.catch(() => default)` per ogni endpoint | 95–106 | Defensive: 1 endpoint down NON rompe l'intera dashboard. **Best practice del file**. |
| `handleDeleteProject` cascade 409 + 500 project_cleanup_partial | 161–183 | 2 path di errore distinti: cascade dependencies vs cleanup partial post-delete attempt (`project_preserved:true`). Documented in commenti. |
| `handleOpenProject` dispatch route per project_type | 210–214 | 3 route diverse (`/editor/manipola/:id`, `/arricchisci/:id`, `/project/:id`); aggiungere un nuovo project type richiede edit qui |
| `useEffect` hash scroll con setTimeout 50ms | 81–91 | Race condition per assicurare DOM sezioni renderizzate. Brittle |
| 8 endpoint paralleli su mount | 93–106 | Latenza rete = 8× round-trip; OK con Promise.all ma il page si blocca a "Caricamento..." finché tutti completano (anche se il più lento è degradabile) |
| `confirm(\`Eliminare "${name}"?\`)` browser native | 154 | Inconsistente con FileManager che usa useConfirmModal |
| Manipola sources lookup `getSourceProjects` su ogni render | 227, 388–392 | OK perché map è piccolo, ma potrebbe usare useMemo |
| `useEmptyHexCanvases` invocato come effect senza cleanup chiaro | 818–853 | Disegna su tutti `.pl__empty-hex` ad ogni render — re-disegna canvas vuoti a infinito? Verificare. |
| theme local + html data-theme attribute | 56–61, 67–72 | doppia fonte di verità (state + DOM attribute); potenziale drift |

### 7.5 Test coverage

`frontend/tests/pages/ProjectList.contract.test.jsx` (~140 LOC):

| Test | Line | Cosa protegge |
| --- | ---:| --- |
| `creates an estrai project with the selected connection` | 96 | createProject form flow + reload |
| `routes project cards to the expected editor pages and project schedule filter` | 117 | dispatch route per project_type + schedule filter |

**2 test, behavior-bound.** Coverage minima ma sui due flow critici (create + open routing).

### 7.6 Candidati extraction

| Candidato | Tipo | LOC | Coupling reale? |
| --- | --- | ---:| --- |
| **a. `useDashboardData()` hook** che incapsula loadData + state | small | ~50 LOC | basso — già concentrato in 1 funzione |
| **b. `<NewProjectModal />` in modulo separato** | small | ~80 LOC | basso |
| **c. Sub-components `<ProjectSection />`, `<ProjectCard />`, `<EseguibileCard />` già estratti** | n.a. | — | — |

### 7.7 Commit decomposition

Recommendation: **no-op.**

Razionale concreto:
- Il file è già **ben strutturato**: 4 sub-component (ProjectSection, ProjectCard, EseguibileCard, useEmptyHexCanvases) sono già estratti.
- Defensive `loadData` con `.catch(default)` per ogni endpoint è un pattern positivo.
- 2 test contract behavior-bound coprono i due flow critici.
- Nessun coupling reale che giustifichi extraction ulteriore.

Le 2 estrazioni teoricamente possibili (`useDashboardData`, `<NewProjectModal />`) sono **pure cosmetic**: il file è 862 LOC ma ha LOC ben distribuita (main 462 LOC, sub-components 400 LOC).

**Eccezione minor**: sostituire `confirm(...)` (154) con `useConfirmModal` per consistency con FileManager — è UX fix, non refactor strutturale, **1h**, separato dal refactor di Phase 1.

### 7.8 Visual smoke richiesto

- Mount: 4 sezioni renderizzate, stats row corretto.
- Search: filtra projects by name.
- Theme toggle: salva localStorage + cambia data-theme.
- New project modal: estrai con connection required, manipola/arricchisci senza.
- Open project: dispatch route corretto per type.
- Delete con cascade 409: toast con dipendenze formattate.
- Delete con 500 project_cleanup_partial: toast con messaggio corretto.
- Hash navigation: `/#estrai` scrolla alla sezione.
- Eseguibili section: lista + execute + delete.

### 7.9 Raccomandazione

**No-op.** Il file è grande ma già ben strutturato. Prerequisito Phase 0 wide audit: NON splittare per dimensione senza coupling reale.

Eccezione: 1h fix UX per `confirm()` → `useConfirmModal` (consistency cross-page).

---

## 8. Shared Layer collegato

### 8.1 `store/useCanvasStore.js` (429 LOC)

Zustand store con devtools middleware. State: nodes, edges, projectId, mode, lastTaskResult, isReadOnly, isDirty, connections.

Action principali (line range):
- `setNodes` (208), `setEdges` (224), `updateNodeData(nodeId, updates)` (246), `deleteNode(nodeId)` (264), `handleSaveQuiet` (397), `markClean` (413).

Coupling: usato direttamente da `ApiEnrichNode`, `ResultNodeStandardView`, e da hooks `useCanvasCore`, `useNodeHandlers`, `useEdgeHandlers`.

Test: `frontend/tests/shared/useCanvasStore.contract.test.js`.

**Raccomandazione: no-op.** Il store è ben fattorizzato; rinominarlo o splittarlo senza un trigger è churn.

### 8.2 `api/*` boundary modules

7 file rilevanti totalmente: `apiConnections.js` (115), `files.js` (183), `projects.js` (204) + `client.js`, `http.js`, `errors.js`.

**Pattern boundary architectural**: ogni modulo espone funzioni named (`listFiles`, `uploadFile`, ...). I componenti **non importano `http` direttamente** — verificato da `apiEnrichNode.boundary.contract.test.js` e `connections.boundary.contract.test.js`.

**Raccomandazione: no-op.** Pattern già al livello desiderato.

### 8.3 `utils/permissions.js` (107 LOC)

Policy table `can.*` e `role.*`. Pure functions stateless.

**Raccomandazione: no-op.**

### 8.4 `utils/hexCanvas.js` (100 LOC)

Hook `useHexCanvas(canvasRef)` per disegno hex art di sfondo. Usato in 4 page (ApiConnectionManager, FileManager, ProjectList, ApiEnrichNode no).

**Raccomandazione: no-op.**

---

## 9. Ranking Finale

| Rank | File | Refactor | Effort | Rischio | Priorità |
| ---:| --- | --- | ---:| --- | --- |
| 1 | `ResultNodeStandardView.jsx` | Split sezioni GROUP BY/ORDER BY/HAVING/FORMAT | ~4–6h | basso | post-prod (test esistenti già behavior, 13 test) |
| 2 | `ResponseConfigBuilder.jsx` | Pure functions extraction + JsonTreeNode | ~3h | basso | post-prod (sblocca unit test parsing) |
| 3 | `ParameterInput.jsx` | 6 list inputs + ColumnSelector | ~3h | basso | post-prod |
| 4 | `FileManager.jsx` | useFileUploadWizard + FileCard | ~4h | medio | post-prod (test coverage bassa) |
| 5 | `ApiEnrichNode.jsx` | useApiEnrichNodeData hook + split executable/standard | ~10h | medio | post-prod (canvas store + ReactFlow coupling) |
| 6 | `ApiConnectionManager.jsx` | Wizard step extraction | ~13h | medio | post-prod (file più grande, payoff alto ma rischio UX su area critica) |
| 7 | `ProjectList.jsx` | nessuno | — | — | no-op |

### 9.1 Cosa NON toccare ora (pre-prod 5 maggio)

- **Nessun file frontend.** Tutti i candidati hanno effort ≥3h con visual smoke richiesto. Pre-prod il rischio UX è il blocker, non il rischio test.
- **Eccezione 1h**: ProjectList `confirm()` → `useConfirmModal` per consistency cross-page (UX fix, non refactor strutturale).

### 9.2 Cosa serve PRIMA dei refactor frontend

- **Visual smoke checklist** per ognuno dei 7 file (oggi assente, descritta sopra per file).
- **Increase test coverage** su FileManager (1 test → ~5), ApiConnectionManager (4 test → ~10), ApiEnrichNode (boundary only → behavior).
- **Decisione UX**: unificare `confirm()` browser vs `useConfirmModal` cross-page.

### 9.3 Differenze rispetto al backend (per contestualizzare)

- Backend: 5 hot file con 49 + 41 + 50 + 40 + 10 test totali.
- Frontend: 7 hot file con ~21 test totali. **Coverage ratio frontend ~10× più bassa.**
- Implicazione: refactor frontend pre-prod ha rischio asymmetric (meno safety net), refactor post-prod richiede prima un investment su test coverage.

---

## 10. Stato Phase 0.5b

**Status: analysis only. No refactor opened. No test modified.**

Output:
- internal map line-ranged per i 7 file (+ shared layer)
- responsabilità UI principali per ogni file
- enumerazione local/derived/fetching/validation/rendering
- coupling esplicito (API, store, modals, hooks, permissions)
- call graph per file
- UX/regression hotspots con rationale
- per-test classification dei 21+ test contract con file/line
- candidati extraction con coupling justification (NON solo "file lungo")
- commit decomposition con rollback per-commit
- visual smoke richiesto per file
- ranking finale + cosa NON toccare ora

Pending Phase 0:
- 0.5c — Q1–Q4 SQL queries (`compat_residue_evidence_0_5c.md`)
- 0.5f — perf baseline
- 0.5g — UX / product readiness

Sintesi: ranking ordinato per ratio payoff/risk, 6 candidati small/medium + 1 no-op (ProjectList già ben strutturato). Tutti i refactor sono **post-prod**: pre-prod il rischio UX e la coverage frontend bassa sconsigliano qualsiasi modifica.

---

*End of Phase 0.5b — Frontend Wide Audit (Evidence-deep).*
