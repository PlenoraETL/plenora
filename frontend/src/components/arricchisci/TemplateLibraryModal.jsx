/**
 * TemplateLibraryModal.jsx — Libreria template API predefiniti
 * Redesign Plenora design system — 4-column grid gallery
 */
import { getTemplates } from '../../api/templates';
import React, { useState, useEffect, useMemo } from 'react';
import ModalWrapper from '../common/ModalWrapper';
import './TemplateLibraryModal.css';

const CATEGORIES = {
  all:       { label: 'Tutti',      icon: '⊞' },
  geocoding: { label: 'Geocoding',  icon: '🌍' },
  routing:   { label: 'Routing',    icon: '🚗' },
  catasto:   { label: 'Catasto',    icon: '🏠' },
  ml_model:  { label: 'Modelli ML', icon: '🧠' },
  custom:    { label: 'Altri',      icon: '📄' },
};

// Dark-mode friendly palette — accent bar + icon bg tint
const CATEGORY_COLORS = {
  geocoding: { accent: '#38BDF8', tint: 'rgba(56,189,248,0.08)' },
  routing:   { accent: '#FBBF24', tint: 'rgba(251,191,36,0.08)'  },
  catasto:   { accent: '#34D399', tint: 'rgba(52,211,153,0.08)'  },
  ml_model:  { accent: '#A78BFA', tint: 'rgba(167,139,250,0.08)' },
  custom:    { accent: '#94A3B8', tint: 'rgba(148,163,184,0.08)' },
};

function TemplateCard({ template, onSelect }) {
  const cat = template._meta?.category || 'custom';
  const colors = CATEGORY_COLORS[cat] || CATEGORY_COLORS.custom;
  const isGenerator = template.config?.api_type === 'generator';
  const params = (template.parameters || []).filter(p => !p.hidden);
  const outputs = template.output_mapping || [];
  const hasAuth = template.config?.auth?.type && template.config.auth.type !== 'none';

  return (
    <div className="tlc" onClick={() => onSelect(template)}>
      {/* Accent bar */}
      <div className="tlc__bar" style={{ background: colors.accent }} />

      {/* Icon */}
      <div className="tlc__icon-wrap" style={{ background: colors.tint }}>
        <span className="tlc__icon">{template.display?.icon || '📄'}</span>
      </div>

      {/* Text */}
      <div className="tlc__body">
        <div className="tlc__name">{template.display?.name}</div>
        <div className="tlc__desc">{template.display?.short_description}</div>
      </div>

      {/* Footer */}
      <div className="tlc__foot">
        <span className={`tlc__type ${isGenerator ? 'tlc__type--gen' : 'tlc__type--enr'}`}>
          {isGenerator ? '⚡ Gen' : '🔗 Enr'}
        </span>
        {hasAuth && <span className="tlc__auth">🔐</span>}
      </div>

      {/* Hover overlay */}
      <div className="tlc__overlay">
        <div className="tlc__ol-inner">
          <div className="tlc__ol-name">{template.display?.name}</div>

          {params.length > 0 && (
            <div className="tlc__ol-section">
              <div className="tlc__ol-label">📥 Input</div>
              <div className="tlc__ol-chips">
                {params.slice(0, 4).map(p => (
                  <span key={p.name} className="tlc__chip tlc__chip--in">{p.name}{p.required ? '*' : ''}</span>
                ))}
                {params.length > 4 && <span className="tlc__chip tlc__chip--more">+{params.length - 4}</span>}
              </div>
            </div>
          )}

          {outputs.length > 0 && (
            <div className="tlc__ol-section">
              <div className="tlc__ol-label">📤 Output</div>
              <div className="tlc__ol-chips">
                {outputs.slice(0, 4).map(o => (
                  <span key={o.column} className="tlc__chip tlc__chip--out">{o.column}</span>
                ))}
                {outputs.length > 4 && <span className="tlc__chip tlc__chip--more">+{outputs.length - 4}</span>}
              </div>
            </div>
          )}

          {template.display?.pricing && (
            <div className="tlc__ol-pricing">💰 {template.display.pricing}</div>
          )}
          {template.display?.limitations?.length > 0 && (
            <div className="tlc__ol-limits">⚠️ {template.display.limitations[0]}</div>
          )}

          <button type="button" className="tlc__ol-btn"
            onClick={e => { e.stopPropagation(); onSelect(template); }}>
            Usa questo template →
          </button>
        </div>
      </div>
    </div>
  );
}

function TemplateLibraryModal({ onClose, onSelectTemplate }) {
  const [templates, setTemplates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedCategory, setSelectedCategory] = useState('all');

  useEffect(() => {
    const load = async () => {
      try {
        setLoading(true);
        const data = await getTemplates();
        if (data.success) setTemplates(data.templates || []);
        else throw new Error(data.error || 'Errore caricamento template');
      } catch (err) { setError(err.message); }
      finally { setLoading(false); }
    };
    load();
  }, []);

  const filtered = useMemo(() => templates.filter(t => {
    const q = searchQuery.toLowerCase();
    const matchSearch = !q ||
      t.display?.name?.toLowerCase().includes(q) ||
      t.display?.short_description?.toLowerCase().includes(q);
    const matchCat = selectedCategory === 'all' || t._meta?.category === selectedCategory;
    return matchSearch && matchCat;
  }), [templates, searchQuery, selectedCategory]);

  return (
    <ModalWrapper
      isOpen={true}
      onClose={onClose}
      title="Libreria Template"
      subtitle="Template API predefiniti pronti all'uso"
      icon="🌐"
      size="lg"
    >
        {/* Toolbar */}
        <div className="tlm__toolbar">
          <div className="tlm__search">
            <span className="tlm__search-icon">🔍</span>
            <input type="text" value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              placeholder="Cerca template…"
              className="tlm__search-input" autoComplete="off" />
            {searchQuery && (
              <button type="button" className="tlm__search-clear" onClick={() => setSearchQuery('')}>✕</button>
            )}
          </div>
          <div className="tlm__cats">
            {Object.entries(CATEGORIES).map(([id, cat]) => {
              const count = id === 'all' ? templates.length : templates.filter(t => t._meta?.category === id).length;
              return (
                <button key={id} type="button"
                  className={`tlm__cat${selectedCategory === id ? ' tlm__cat--active' : ''}`}
                  onClick={() => setSelectedCategory(id)}>
                  <span>{cat.icon}</span>
                  <span>{cat.label}</span>
                  <span className="tlm__cat-count">{count}</span>
                </button>
              );
            })}
          </div>
        </div>

        {/* Content */}
        <div className="tlm__content">
          {loading ? (
            <div className="tlm__empty">
              <div className="tlm__spinner" />
              <div>Caricamento template…</div>
            </div>
          ) : error ? (
            <div className="tlm__empty">
              <div className="tlm__empty-icon">❌</div>
              <div>Errore: {error}</div>
            </div>
          ) : filtered.length === 0 ? (
            <div className="tlm__empty">
              <div className="tlm__empty-icon">🔭</div>
              <div>Nessun template trovato</div>
            </div>
          ) : (
            <div className="tlm__grid">
              {filtered.map(t => (
                <TemplateCard key={t._meta?.id} template={t}
                  onSelect={t => { onSelectTemplate(t); onClose(); }} />
              ))}
            </div>
          )}
        </div>

        {/* Hint bar */}
        <div className="tlm__hint">
          💡 Passa il mouse su una card per i dettagli · Click per selezionare
        </div>
    </ModalWrapper>
  );
}

export default TemplateLibraryModal;
