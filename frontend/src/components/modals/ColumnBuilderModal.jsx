/**
 * ColumnBuilderModal.jsx — Builder per colonne calcolate (SQL expressions)
 *
 * Modal con un catalogo di funzioni SQL (geometriche, stringa, data, aggregazione)
 * per costruire espressioni visualmente o in modalità SQL manuale.
 * Usato da TableNode per aggiungere colonne derivate a una tabella sorgente.
 *
 * Dipende da: API /api/catalog/functions per la lista funzioni disponibili
 */
import { getFunctionsCatalog, translateFunction } from '../../api/transforms';
import React, { useState, useEffect, useMemo } from 'react';
import { useToast } from '../../hooks/useToast';
import { getColumnTypeIcon, getColumnTypeLabel } from '../../utils/columnUtils';
import ModalWrapper, { ModalButton } from '../common/ModalWrapper';
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
  existingColumn = null 
}) {
  const toast = useToast();
  // State
  const [catalog, setCatalog] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  
  const [columnName, setColumnName] = useState('');
  const [selectedCategory, setSelectedCategory] = useState('geometric');
  const [selectedFunction, setSelectedFunction] = useState('');
  const [params, setParams] = useState({});
  const [nestedExpression, setNestedExpression] = useState(null);
  const [useNested, setUseNested] = useState(false);
  
  const [sqlPreview, setSqlPreview] = useState('');
  const [isManualEdit, setIsManualEdit] = useState(false);
  const [manualSql, setManualSql] = useState('');

  // Carica catalogo funzioni
  useEffect(() => {
    if (isOpen) {
      loadCatalog();
    }
  }, [isOpen]);

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
        setSqlPreview('');
      }
    }
  }, [isOpen, existingColumn]);

  // Aggiorna preview SQL quando cambia l'espressione.
  //  - debounce 350ms per evitare un POST /functions/translate per
  //    ogni keystroke nel wizard
  //  - pre-validate required params: senza, ogni cambio campo del
  //    wizard mandava una call che il backend rifiutava 400
  //    ("Specificare column2 o value...") inquinando la console
  //    finche' l'utente non completava l'espressione
  useEffect(() => {
    if (isManualEdit || !selectedFunction || !columnName) return;

    const required = (currentFunctionDef?.params || []).filter(
      (p) => p.required,
    );
    const missing = required.filter((p) => {
      const v = params[p.name];
      return v === undefined || v === null || v === '';
    });
    if (missing.length > 0) {
      // espressione incompleta: niente preview, niente call. Resetto
      // il preview cosi' l'utente vede "Configura un'espressione
      // valida" se prova a salvare.
      setSqlPreview('');
      return;
    }

    const handle = setTimeout(() => { updateSqlPreview(); }, 350);
    return () => clearTimeout(handle);
  }, [selectedFunction, params, nestedExpression, columnName, dbType, isManualEdit, currentFunctionDef]);

  const loadCatalog = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getFunctionsCatalog();
      setCatalog(data);
    } catch (err) {
      setError(`Errore caricamento catalogo: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  // Funzioni disponibili per la categoria selezionata
  const availableFunctions = useMemo(() => {
    if (!catalog?.functions) return [];
    return Object.entries(catalog.functions)
      .filter(([_, func]) => func.category === selectedCategory)
      .map(([name, func]) => ({ name, ...func }))
      .sort((a, b) => a.label.localeCompare(b.label));
  }, [catalog, selectedCategory]);

  // Definizione funzione corrente
  const currentFunctionDef = useMemo(() => {
    if (!catalog?.functions || !selectedFunction) return null;
    return catalog.functions[selectedFunction];
  }, [catalog, selectedFunction]);

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

  const updateSqlPreview = async () => {
    if (!selectedFunction || !columnName) {
      setSqlPreview('');
      return;
    }

    try {
      const data = await translateFunction({
        function_name: selectedFunction,
        params: params,
        db_type: dbType
      });
      setSqlPreview(data.sql || '');
    } catch (err) {
      console.error('Errore preview SQL:', err);
    }
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
              <textarea
                value={manualSql}
                onChange={e => setManualSql(e.target.value)}
                placeholder="Scrivi l'espressione SQL manualmente..."
                className="column-builder-sql-textarea"
                rows={4}
              />
            ) : (
              <div className="column-builder-preview">
                <code className="column-builder-preview-code">
                  {sqlPreview || '-- Seleziona una funzione --'}
                </code>
              </div>
            )}

            {currentFunctionDef && (
              <div className="column-builder-result-type">
                Tipo risultato: {getColumnTypeIcon(currentFunctionDef.output_type)} {getColumnTypeLabel(currentFunctionDef.output_type)}
              </div>
            )}
          </div>
        </>
      )}
    </ModalWrapper>
  );
}

export default ColumnBuilderModal;
