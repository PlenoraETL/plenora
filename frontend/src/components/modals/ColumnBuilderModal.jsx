/**
 * ColumnBuilderModal.jsx — Builder per colonne calcolate (SQL expressions)
 *
 * Modal con un catalogo di funzioni SQL (geometriche, stringa, data, aggregazione)
 * per costruire espressioni visualmente o in modalità SQL manuale.
 * Usato da TableNode per aggiungere colonne derivate a una tabella sorgente.
 *
 * Dipende da: API /api/catalog/functions per la lista funzioni disponibili
 */
import { getFunctionsCatalog, translateFunction, previewFormula } from '../../api/transforms';
import React, { useState, useEffect, useMemo } from 'react';
import { useResource } from '../../hooks/useResource';
import { useDebouncedValue } from '../../hooks/useDebouncedValue';
import '../common/ListState.css';
import { useToast } from '../../hooks/useToast';
import { getColumnTypeIcon, getColumnTypeLabel } from '../../utils/columnUtils';
import ModalWrapper, { ModalButton } from '../common/ModalWrapper';
import { humanizeError } from '../../utils/errorMessages';
import './ColumnBuilderModal.css';

/**
 * ColumnBuilderModal - Modal per costruire espressioni di colonne calcolate
 * REFACTORED: Usa ModalWrapper per struttura base
 */
function ColumnBuilderModal({
  isOpen,
  onClose,
  onSave,
  columns = [],
  dbType = 'postgres',
  existingColumn = null,
  projectId = null,
  tableName = null,
  tableAlias = 't',
}) {
  const toast = useToast();
  // State
  // Fase 5 T2 (R4.4): catalogo, traduzione SQL e campione dall'hook comune.
  const catalogResource = useResource(getFunctionsCatalog, {
    key: 'functions-catalog',
    enabled: Boolean(isOpen),
    errorMessage: (err) => humanizeError(err).message,
  });
  const catalog = catalogResource.data || null;
  const loading = catalogResource.initialLoading;
  const error = catalogResource.error;
  
  const [columnName, setColumnName] = useState('');
  const [selectedCategory, setSelectedCategory] = useState('geometric');
  const [selectedFunction, setSelectedFunction] = useState('');
  const [params, setParams] = useState({});
  const [nestedExpression, setNestedExpression] = useState(null);
  const [useNested, setUseNested] = useState(false);
  
  const [isManualEdit, setIsManualEdit] = useState(false);
  const [manualSql, setManualSql] = useState('');
  const canSample = !!(projectId && tableName);

  // Reset quando si apre
  useEffect(() => {
    if (isOpen) {
      if (existingColumn) {
        setColumnName(existingColumn.name || '');
      } else {
        setColumnName('');
        setSelectedCategory('geometric');
        setSelectedFunction('');
        setParams({});
        setNestedExpression(null);
        setUseNested(false);
        setIsManualEdit(false);
        setManualSql('');
      }
    }
  }, [isOpen, existingColumn]);

  // Funzioni disponibili per la categoria selezionata
  const availableFunctions = useMemo(() => {
    if (!catalog?.functions) return [];
    return Object.entries(catalog.functions)
      .filter(([_, func]) => func.category === selectedCategory)
      .map(([name, func]) => ({ name, ...func }))
      .sort((a, b) => a.label.localeCompare(b.label));
  }, [catalog, selectedCategory]);

  // Definizione funzione corrente — DEVE precedere l'useEffect del
  // preview, che la legge per pre-validare i required params (TDZ).
  const currentFunctionDef = useMemo(() => {
    if (!catalog?.functions || !selectedFunction) return null;
    return catalog.functions[selectedFunction];
  }, [catalog, selectedFunction]);

  // Cheatsheet (#20): tutte le funzioni con esempio, raggruppate per categoria.
  // Mostrata in modalità SQL manuale come riferimento rapido.
  const cheatsheet = useMemo(() => {
    if (!catalog?.functions) return [];
    const labels = {
      geometric: '🗺 Geometriche', numeric: '🔢 Numeriche', string: '🔤 Stringa',
      date: '📅 Data', conditional: '🔀 Condizionali', cast: '🔄 Conversione',
    };
    const byCat = {};
    Object.entries(catalog.functions).forEach(([name, f]) => {
      if (!f.example) return;
      (byCat[f.category] = byCat[f.category] || []).push({ name, label: f.label, example: f.example });
    });
    return Object.keys(labels)
      .filter(cat => byCat[cat])
      .map(cat => ({
        cat,
        label: labels[cat] || cat,
        fns: byCat[cat].sort((a, b) => a.label.localeCompare(b.label)),
      }));
  }, [catalog]);

  // SQL tradotta e campione (Fase 5 T2, R4.4). La richiesta e' la chiave:
  //  - parte dopo 350 ms di quiete (niente POST /functions/translate a ogni
  //    tasto) e solo con i parametri obbligatori compilati;
  //  - vince sempre l'ultima richiesta (id di useResource): una traduzione
  //    vecchia non riscrive la SQL dei parametri attuali;
  //  - SQL mostrata e salvata = quella dei parametri ATTUALI; traduzione in
  //    corso o fallita = nessuna SQL (Salva chiede un'espressione valida),
  //    mai la SQL precedente.
  const missingRequired = (currentFunctionDef?.params || [])
    .filter((p) => p.required)
    .some((p) => params[p.name] === undefined || params[p.name] === null || params[p.name] === '');
  const translateRequest = (!isManualEdit && selectedFunction && columnName && !missingRequired)
    ? { function_name: selectedFunction, params, db_type: dbType, nested: useNested ? nestedExpression : null, column_name: columnName }
    : null;
  const debounced = useDebouncedValue(translateRequest, 350);
  const request = debounced.settled ? debounced.value : null;
  const translation = useResource(() => translateFunction({
    function_name: request.function_name,
    params: request.params,
    db_type: request.db_type,
  }), { key: ['translate', request], enabled: Boolean(request) });
  const sqlPreview = request && translation.status !== 'error' ? (translation.data?.sql || '') : '';
  const translationError = request && translation.status === 'error' ? translation.error : null;
  // #26: valuta la formula su una riga campione reale (solo con il contesto
  // progetto+tabella, es. ColumnBuilder aperto da un TableNode).
  const sampleResource = useResource(async () => previewFormula({
    project_id: projectId,
    table_name: tableName,
    table_alias: tableAlias || 't',
    db_type: request.db_type,
    column_name: request.column_name,
    ...(request.nested
      ? { expression: request.nested }
      : { function_name: request.function_name, params: request.params }),
  }), { key: ['sample', request], enabled: Boolean(request && canSample && sqlPreview), errorMessage: (err) => humanizeError(err).message });
  let sample = { status: 'idle', value: null, error: null };
  if (request && canSample && sqlPreview) {
    const res = sampleResource.data;
    if (sampleResource.status === 'error') sample = { status: 'error', value: null, error: sampleResource.error };
    else if (!res) sample = { status: 'loading', value: null, error: null };
    else if (res.success) sample = { status: res.empty ? 'empty' : 'success', value: res.value, error: null };
    else sample = { status: 'error', value: null, error: res.message || 'Errore anteprima' };
  }

  // Colonne geometry disponibili
  const geometryColumns = useMemo(() => {
    return columns.filter(c => 
      c.type?.toLowerCase().startsWith('geometry') || 
      c.type?.toLowerCase().includes('geom')
    );
  }, [columns]);

  // Colonne numeriche
  const numericColumns = useMemo(() => {
    return columns.filter(c => 
      ['int', 'integer', 'real', 'float', 'double', 'numeric', 'decimal'].includes(c.type?.toLowerCase())
    );
  }, [columns]);

  const handleCategoryChange = (category) => {
    setSelectedCategory(category);
    setSelectedFunction('');
    setParams({});
  };

  const handleFunctionChange = (funcName) => {
    setSelectedFunction(funcName);
    setParams({});
    setNestedExpression(null);
    setUseNested(false);
  };

  const handleParamChange = (paramName, value) => {
    setParams(prev => ({ ...prev, [paramName]: value }));
  };

  const handleSave = () => {
    if (!columnName.trim()) {
      toast.warning('Inserisci un nome per la colonna');
      return;
    }

    const expression = isManualEdit ? manualSql : sqlPreview;
    if (!expression) {
      toast.warning("Configura un'espressione valida");
      return;
    }

    const outputType = isManualEdit ? 'str' : (currentFunctionDef?.output_type || 'str');

    const columnDef = {
      name: columnName.trim(),
      expression: expression,
      output_type: outputType,
      function_name: isManualEdit ? null : selectedFunction,
      params: isManualEdit ? null : params
    };

    onSave(columnDef);
    onClose();
  };

  // Render param input
  const renderParamInput = (param) => {
    const value = params[param.name] || '';
    
    if (param.type === 'column_list') {
      const selectedValues = value
        ? (Array.isArray(value) ? value : value.split(',').map(v => v.trim()).filter(Boolean))
        : [];

      const toggleColSelection = (colName) => {
        const next = selectedValues.includes(colName)
          ? selectedValues.filter(v => v !== colName)
          : [...selectedValues, colName];
        handleParamChange(param.name, next.join(','));
      };

      return (
        <div className="column-builder-column-list">
          {columns.map(col => (
            <label key={col.name} className={`column-builder-column-list-item${selectedValues.includes(col.name) ? ' selected' : ''}`}>
              <input
                type="checkbox"
                checked={selectedValues.includes(col.name)}
                onChange={() => toggleColSelection(col.name)}
              />
              <span>{getColumnTypeIcon(col.type)} {col.name}</span>
            </label>
          ))}
          {selectedValues.length > 0 && (
            <div className="column-builder-column-list-preview">
              {selectedValues.join(', ')}
            </div>
          )}
        </div>
      );
    }

    if (param.type === 'column_geom') {
      return (
        <select
          value={value}
          onChange={e => handleParamChange(param.name, e.target.value)}
          className="column-builder-select"
        >
          <option value="">-- Seleziona colonna --</option>
          {geometryColumns.map(col => (
            <option key={col.name} value={col.name}>
              {getColumnTypeIcon(col.type)} {col.name}
            </option>
          ))}
        </select>
      );
    }

    if (param.type === 'column') {
      return (
        <select
          value={value}
          onChange={e => handleParamChange(param.name, e.target.value)}
          className="column-builder-select"
        >
          <option value="">-- Seleziona colonna --</option>
          {columns.map(col => (
            <option key={col.name} value={col.name}>
              {getColumnTypeIcon(col.type)} {col.name}
            </option>
          ))}
        </select>
      );
    }

    if (param.type === 'number') {
      return (
        <input
          type="number"
          value={value}
          onChange={e => handleParamChange(param.name, e.target.value)}
          placeholder={param.default?.toString() || '0'}
          className="column-builder-input column-builder-input--number"
        />
      );
    }

    if (param.type === 'srid') {
      return (
        <input
          type="number"
          value={value}
          onChange={e => handleParamChange(param.name, e.target.value)}
          placeholder="4326"
          className="column-builder-input column-builder-input--srid"
        />
      );
    }

    return (
      <input
        type="text"
        value={value}
        onChange={e => handleParamChange(param.name, e.target.value)}
        placeholder={param.placeholder || ''}
        className="column-builder-input"
      />
    );
  };

  // Footer
  const footer = (
    <>
      <ModalButton variant="secondary" onClick={onClose}>
        Annulla
      </ModalButton>
      <ModalButton 
        variant="primary" 
        onClick={handleSave}
        disabled={!columnName.trim() || (!sqlPreview && !manualSql)}
      >
        ✓ {existingColumn ? 'Salva modifiche' : 'Aggiungi colonna'}
      </ModalButton>
    </>
  );

  return (
    <ModalWrapper
      isOpen={isOpen}
      onClose={onClose}
      title={`${existingColumn ? 'Modifica' : 'Nuova'} colonna calcolata`}
      icon="✨"
      size="lg"
      variant="estrai"
      footer={footer}
      loading={loading}
      loadingText="Caricamento funzioni..."
    >
      {error ? (
        <div className="column-builder-error">❌ {error}</div>
      ) : (
        <>
          {/* Nome colonna */}
          <div className="column-builder-section">
            <label className="column-builder-label">Nome colonna risultato:</label>
            <input
              type="text"
              value={columnName}
              onChange={e => setColumnName(e.target.value)}
              placeholder="es: buffer_100m"
              className="column-builder-input"
            />
          </div>

          <div className="column-builder-divider" />

          {/* Builder */}
          <div className="column-builder-section">
            <div className="column-builder-section-title">🔧 BUILDER</div>

            {/* Categoria */}
            <div className="column-builder-field">
              <label className="column-builder-label">Categoria:</label>
              <div className="column-builder-categories">
                {catalog?.categories && Object.entries(catalog.categories).map(([key, cat]) => (
                  <button type="button"
                    key={key}
                    className={`column-builder-category-btn ${selectedCategory === key ? 'active' : ''}`}
                    style={{ 
                      '--cat-color': cat.color,
                      borderColor: selectedCategory === key ? cat.color : 'transparent'
                    }}
                    onClick={() => handleCategoryChange(key)}
                  >
                    {cat.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Funzione */}
            <div className="column-builder-field">
              <label className="column-builder-label">Funzione:</label>
              <select
                value={selectedFunction}
                onChange={e => handleFunctionChange(e.target.value)}
                className="column-builder-select"
              >
                <option value="">-- Seleziona funzione --</option>
                {availableFunctions.map(func => (
                  <option key={func.name} value={func.name}>
                    {func.label}
                  </option>
                ))}
              </select>
              {currentFunctionDef && (
                <div className="column-builder-func-desc">
                  {currentFunctionDef.description}
                </div>
              )}
              {currentFunctionDef?.example && (
                <div className="column-builder-func-example">
                  <span className="column-builder-func-example__tag">es.</span>
                  <code>{currentFunctionDef.example}</code>
                </div>
              )}
            </div>

            {/* Parametri */}
            {currentFunctionDef?.params && currentFunctionDef.params.length > 0 && (
              <div className="column-builder-params">
                <label className="column-builder-label">Parametri:</label>
                {currentFunctionDef.params.map(param => (
                  <div key={param.name} className="column-builder-param">
                    <label className="column-builder-param-label">
                      {param.label || param.name}
                      {param.required && <span className="required">*</span>}
                    </label>
                    {renderParamInput(param)}
                    {param.description && (
                      <span className="column-builder-param-hint">{param.description}</span>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="column-builder-divider" />

          {/* Preview SQL */}
          <div className="column-builder-section">
            <div className="column-builder-section-title">
              📝 ESPRESSIONE SQL
              <label className="column-builder-manual-toggle">
                <input
                  type="checkbox"
                  checked={isManualEdit}
                  onChange={e => setIsManualEdit(e.target.checked)}
                />
                Modifica manuale
              </label>
            </div>

            {isManualEdit ? (
              <>
                <textarea
                  value={manualSql}
                  onChange={e => setManualSql(e.target.value)}
                  placeholder="Scrivi l'espressione SQL manualmente..."
                  className="column-builder-sql-textarea"
                  rows={4}
                />
                {cheatsheet.length > 0 && (
                  <details className="column-builder-cheatsheet">
                    <summary>📖 Cheatsheet funzioni ed esempi</summary>
                    {cheatsheet.map(group => (
                      <div key={group.cat} className="column-builder-cheatsheet__group">
                        <div className="column-builder-cheatsheet__cat">{group.label}</div>
                        {group.fns.map(fn => (
                          <div key={fn.name} className="column-builder-cheatsheet__row">
                            <code className="column-builder-cheatsheet__ex">{fn.example}</code>
                          </div>
                        ))}
                      </div>
                    ))}
                  </details>
                )}
              </>
            ) : (
              <div className="column-builder-preview">
                <code className="column-builder-preview-code">
                  {sqlPreview || (translationError ? '-- Traduzione non riuscita --' : translateRequest && (!request || translation.loading) ? '-- Traduzione in corso… --' : '-- Seleziona una funzione --')}
                </code>
                {translationError && (
                  <div className="column-builder-error" role="alert">
                    ❌ {translationError}{' '}
                    <button type="button" className="list-state__retry" onClick={() => translation.reload()}>Riprova</button>
                  </div>
                )}
              </div>
            )}

            {currentFunctionDef && (
              <div className="column-builder-result-type">
                Tipo risultato: {getColumnTypeIcon(currentFunctionDef.output_type)} {getColumnTypeLabel(currentFunctionDef.output_type)}
              </div>
            )}

            {/* #26 — anteprima della formula su una riga campione reale */}
            {!isManualEdit && canSample && sample.status !== 'idle' && (
              <div className={`column-builder-sample column-builder-sample--${sample.status}`}>
                <span className="column-builder-sample__label">▶ Su riga campione:</span>
                {sample.status === 'loading' && <span className="column-builder-sample__val">…</span>}
                {sample.status === 'success' && (
                  <code className="column-builder-sample__val" title={sample.value ?? 'NULL'}>
                    {sample.value === null || sample.value === undefined ? 'NULL' : sample.value}
                  </code>
                )}
                {sample.status === 'empty' && <span className="column-builder-sample__val">tabella vuota</span>}
                {sample.status === 'error' && (
                  <span className="column-builder-sample__err" title={sample.error}>{sample.error}</span>
                )}
              </div>
            )}
          </div>
        </>
      )}
    </ModalWrapper>
  );
}

export default ColumnBuilderModal;
