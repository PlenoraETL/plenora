import React, { useState, useEffect, useMemo } from 'react';

// Categorie con icone
const CATEGORIES = {
  all: { label: 'Tutte', icon: '📋' },
  geocoding: { label: 'Geocoding', icon: '🌍' },
  routing: { label: 'Routing', icon: '🚗' },
  catasto: { label: 'Catasto', icon: '🏠' },
  ml_model: { label: 'Modelli ML', icon: '🧠' },
  custom: { label: 'Altri', icon: '📄' }
};

function TemplateLibraryModal({ onClose, onSelectTemplate }) {
  const [templates, setTemplates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedCategory, setSelectedCategory] = useState('all');
  const [expandedTemplate, setExpandedTemplate] = useState(null);

  // Carica template dal backend
  useEffect(() => {
    const loadTemplates = async () => {
      try {
        setLoading(true);
        const response = await fetch('http://localhost:8000/api/templates');
        
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        
        const data = await response.json();
        
        if (data.success) {
          setTemplates(data.templates || []);
        } else {
          throw new Error(data.error || 'Errore caricamento template');
        }
      } catch (err) {
        console.error('Errore caricamento template:', err);
        setError(err.message);
      } finally {
        setLoading(false);
      }
    };

    loadTemplates();
  }, []);

  // Filtra template
  const filteredTemplates = useMemo(() => {
    return templates.filter(t => {
      const matchesSearch = searchQuery === '' || 
        t.display?.name?.toLowerCase().includes(searchQuery.toLowerCase()) ||
        t.display?.short_description?.toLowerCase().includes(searchQuery.toLowerCase());
      
      const matchesCategory = selectedCategory === 'all' || 
        t._meta?.category === selectedCategory;
      
      return matchesSearch && matchesCategory;
    });
  }, [templates, searchQuery, selectedCategory]);

  // Raggruppa per categoria
  const groupedTemplates = useMemo(() => {
    const groups = {};
    
    filteredTemplates.forEach(t => {
      const cat = t._meta?.category || 'custom';
      if (!groups[cat]) {
        groups[cat] = [];
      }
      groups[cat].push(t);
    });
    
    return groups;
  }, [filteredTemplates]);

  // Seleziona template
  const handleSelectTemplate = (template) => {
    onSelectTemplate(template);
    onClose();
  };

  return (
    <div className="template-modal__overlay" onClick={onClose}>
      <div className="template-modal" onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div className="template-modal__header">
          <h2 className="template-modal__title">🌐 Libreria Template API</h2>
          <button onClick={onClose} className="template-modal__close">×</button>
        </div>

        {/* Toolbar */}
        <div className="template-modal__toolbar">
          <div className="template-modal__search">
            <span className="template-modal__search-icon">🔍</span>
            <input
              type="text"
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              placeholder="Cerca template..."
              className="template-modal__search-input"
            />
          </div>
          <div className="template-modal__filters">
            {Object.entries(CATEGORIES).map(([catId, catInfo]) => (
              <button
                key={catId}
                onClick={() => setSelectedCategory(catId)}
                className={`template-modal__filter-btn ${selectedCategory === catId ? 'template-modal__filter-btn--active' : ''}`}
                title={catInfo.label}
              >
                {catInfo.icon}
              </button>
            ))}
          </div>
        </div>

        {/* Content */}
        <div className="template-modal__content">
          {loading ? (
            <div className="template-modal__loading">
              <span>⏳</span> Caricamento template...
            </div>
          ) : error ? (
            <div className="template-modal__error">
              <span>❌</span> Errore: {error}
              <button onClick={() => window.location.reload()} className="template-modal__retry-btn">
                🔄 Riprova
              </button>
            </div>
          ) : filteredTemplates.length === 0 ? (
            <div className="template-modal__empty">
              <span>📭</span> Nessun template trovato
            </div>
          ) : (
            Object.entries(groupedTemplates).map(([category, categoryTemplates]) => (
              <div key={category} className="template-modal__category">
                <h3 className="template-modal__category-title">
                  {CATEGORIES[category]?.icon} {CATEGORIES[category]?.label || category}
                </h3>
                <div className="template-modal__grid">
                  {categoryTemplates.map(template => (
                    <div 
                      key={template._meta?.id}
                      className={`template-card ${expandedTemplate === template._meta?.id ? 'template-card--expanded' : ''}`}
                    >
                      {/* Card Header */}
                      <div 
                        className="template-card__header"
                        onClick={() => setExpandedTemplate(
                          expandedTemplate === template._meta?.id ? null : template._meta?.id
                        )}
                      >
                        <span className="template-card__icon">{template.display?.icon || '📄'}</span>
                        <div className="template-card__info">
                          <h4 className="template-card__name">{template.display?.name}</h4>
                          <p className="template-card__desc">{template.display?.short_description}</p>
                        </div>
                        <span className="template-card__expand">
                          {expandedTemplate === template._meta?.id ? '▼' : '▶'}
                        </span>
                      </div>

                      {/* Card Details (expanded) */}
                      {expandedTemplate === template._meta?.id && (
                        <div className="template-card__details">
                          {/* Meta info */}
                          <div className="template-card__meta">
                            <span className="template-card__meta-item">
                              <span className="template-card__label">Tipo:</span>
                              <span className={`template-card__badge ${template.config?.api_type === 'generator' ? 'template-card__badge--generator' : ''}`}>
                                {template.config?.api_type === 'generator' ? 'Generatore' : 'Arricchitore'}
                              </span>
                            </span>
                            {template.config?.auth?.type !== 'none' && (
                              <span className="template-card__meta-item">
                                <span className="template-card__label">Auth:</span>
                                <span className="template-card__badge">🔐 {template.config?.auth?.type}</span>
                              </span>
                            )}
                            {template.config?.options?.rate_limit && (
                              <span className="template-card__meta-item">
                                <span className="template-card__label">Rate:</span>
                                {template.config.options.rate_limit} req/s
                              </span>
                            )}
                          </div>

                          {/* Parametri */}
                          <div className="template-card__section">
                            <span className="template-card__label">Parametri input:</span>
                            <div className="template-card__params">
                              {template.parameters?.filter(p => !p.hidden).map(p => (
                                <span key={p.name} className="template-card__param">
                                  {p.name}{p.required ? '*' : ''}
                                </span>
                              ))}
                            </div>
                          </div>

                          {/* Output */}
                          <div className="template-card__section">
                            <span className="template-card__label">Campi output:</span>
                            <div className="template-card__params">
                              {template.output_mapping?.map(o => (
                                <span key={o.column} className="template-card__param template-card__param--output">
                                  {o.column}
                                </span>
                              ))}
                            </div>
                          </div>

                          {/* Limitations */}
                          {template.display?.limitations?.length > 0 && (
                            <div className="template-card__limitations">
                              <span className="template-card__label">⚠️ Limitazioni:</span>
                              <ul>
                                {template.display.limitations.map((l, i) => (
                                  <li key={i}>{l}</li>
                                ))}
                              </ul>
                            </div>
                          )}

                          {/* Pricing */}
                          {template.display?.pricing && (
                            <div className="template-card__pricing">
                              <span className="template-card__label">💰</span>
                              {template.display.pricing}
                            </div>
                          )}
                        </div>
                      )}

                      {/* Use Button */}
                      <div className="template-card__footer">
                        <button 
                          onClick={() => handleSelectTemplate(template)}
                          className="template-card__use-btn"
                        >
                          + Usa questo template
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ))
          )}
        </div>

        {/* Footer */}
        <div className="template-modal__footer">
          <p className="template-modal__hint">
            💡 I template preconfigurano URL, parametri e mapping. Potrai personalizzarli nel wizard.
          </p>
        </div>
      </div>
    </div>
  );
}

export default TemplateLibraryModal;