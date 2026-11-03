/**
 * AnnotationNode - Gruppo container per annotare aree del canvas
 *
 * Design: bordo colorato semitrasparente, sfondo tintato, z-index -1
 * così i nodi ci vivono dentro. Label uppercase in topbar, nota in basso.
 * Ridimensionabile, titolo e nota editabili con doppio click.
 */
import { memo, useState, useCallback } from 'react';
import { NodeResizer } from '@xyflow/react';
import useCanvasStore from '../../store/useCanvasStore';
import './AnnotationNode.css';

const COLORS = [
  { value: '#FBBF24', label: 'Giallo' },
  { value: '#60A5FA', label: 'Blu'    },
  { value: '#34D399', label: 'Verde'  },
  { value: '#F87171', label: 'Rosso'  },
  { value: '#C084FC', label: 'Viola'  },
  { value: '#FB923C', label: 'Arancio'},
];

// alpha per bordo e sfondo a partire dal colore hex
const toRgba = (hex, alpha) => {
  const r = parseInt(hex.slice(1,3),16);
  const g = parseInt(hex.slice(3,5),16);
  const b = parseInt(hex.slice(5,7),16);
  return `rgba(${r},${g},${b},${alpha})`;
};

function AnnotationNode({ id, data, selected }) {
  const {
    label = '',
    title = '',
    color = '#FBBF24',
  } = data;

  const updateNodeData = useCanvasStore(s => s.updateNodeData);
  const deleteNode     = useCanvasStore(s => s.deleteNode);

  const [editingTitle, setEditingTitle] = useState(!title);
  const [editingNote,  setEditingNote]  = useState(false);
  const [showColors,   setShowColors]   = useState(false);

  const handleChange = useCallback((field, value) => {
    updateNodeData(id, { [field]: value });
  }, [id, updateNodeData]);

  const borderColor = toRgba(color, 0.4);
  const bgColor     = toRgba(color, 0.04);
  const topbarColor = toRgba(color, 0.12);

  return (
    <>
      <NodeResizer
        color={color}
        isVisible={selected}
        minWidth={220}
        minHeight={120}
        lineStyle={{ borderColor }}
        handleStyle={{ borderColor, background: 'var(--plen-surface,#0C0F18)' }}
      />

      <div
        className={`ann-group ${selected ? 'ann-group--selected' : ''}`}
        style={{
          '--ag-color':        color,
          '--ag-border':       borderColor,
          '--ag-bg':           bgColor,
          '--ag-topbar':       topbarColor,
        }}
      >
<div className="ann-group__topbar">
          {editingTitle ? (
            <input
              className="ann-group__title-input"
              value={title}
              onChange={e => handleChange('title', e.target.value)}
              onBlur={() => setEditingTitle(false)}
              onKeyDown={e => {
                if (e.key === 'Enter' || e.key === 'Escape') setEditingTitle(false);
              }}
              placeholder="Nome gruppo..."
              autoFocus
              onMouseDown={e => e.stopPropagation()}
            />
          ) : (
            <span
              className="ann-group__title"
              onDoubleClick={e => { e.stopPropagation(); setEditingTitle(true); }}
              title="Doppio click per modificare"
            >
              {title || 'Gruppo'}
            </span>
          )}

          <div className="ann-group__actions">
            {/* Color picker toggle */}
            <button
              type="button"
              className="ann-group__btn"
              onClick={e => { e.stopPropagation(); setShowColors(v => !v); }}
              onMouseDown={e => e.stopPropagation()}
              title="Cambia colore"
            >
              <svg width="11" height="11" viewBox="0 0 16 16" fill="none">
                <circle cx="8" cy="8" r="6" fill={color} stroke="rgba(0,0,0,0.3)" strokeWidth="1.5"/>
              </svg>
            </button>
            {/* Delete */}
            <button
              type="button"
              className="ann-group__btn ann-group__btn--del"
              onClick={e => { e.stopPropagation(); deleteNode(id); }}
              onMouseDown={e => e.stopPropagation()}
              title="Elimina gruppo"
            >✕</button>
          </div>
        </div>
{showColors && (
          <div className="ann-group__colors">
            {COLORS.map(c => (
              <button
                type="button"
                key={c.value}
                className={`ann-group__dot ${c.value === color ? 'ann-group__dot--active' : ''}`}
                style={{ background: c.value }}
                onClick={e => { e.stopPropagation(); handleChange('color', c.value); setShowColors(false); }}
                onMouseDown={e => e.stopPropagation()}
                title={c.label}
              />
            ))}
          </div>
        )}
<div className="ann-group__body" />
<div className="ann-group__note-area">
          {editingNote ? (
            <textarea
              className="ann-group__textarea"
              value={label}
              onChange={e => handleChange('label', e.target.value)}
              onBlur={() => setEditingNote(false)}
              onKeyDown={e => { if (e.key === 'Escape') setEditingNote(false); }}
              placeholder="Descrivi questo gruppo..."
              autoFocus
              onMouseDown={e => e.stopPropagation()}
            />
          ) : (
            <div
              className={`ann-group__note ${!label ? 'ann-group__note--empty' : ''}`}
              onDoubleClick={e => { e.stopPropagation(); setEditingNote(true); }}
              title="Doppio click per aggiungere una nota"
            >
              {label || 'Doppio click per aggiungere una nota...'}
            </div>
          )}
        </div>
      </div>
    </>
  );
}

export default memo(AnnotationNode);
