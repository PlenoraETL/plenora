import React, { useState } from 'react';

/**
 * Tag di un progetto (#10): chip rimovibili e aggiunta inline (solo con
 * `onSetTags`). Uno solo per la scheda dell'elenco (ProjectCard) e il
 * pannello della home per catene (ChainsView).
 */
export default function ProjectTags({ tags: rawTags, onSetTags = null }) {
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState('');
  const tags = Array.isArray(rawTags) ? rawTags : [];
  if (!tags.length && !onSetTags) return null;

  const addTag = () => {
    const t = draft.trim();
    setDraft('');
    setAdding(false);
    if (t && !tags.some(x => x.toLowerCase() === t.toLowerCase())) onSetTags([...tags, t]);
  };
  const removeTag = (t) => onSetTags(tags.filter(x => x !== t));

  return (
    <div className="pl__card-tags" onClick={e => e.stopPropagation()}>
      {tags.map(t => (
        <span key={t} className="pl__tag">
          {t}
          {onSetTags && (
            <button type="button" className="pl__tag-x" title="Rimuovi tag" aria-label={`Rimuovi il tag ${t}`}
              onClick={() => removeTag(t)}>×</button>
          )}
        </span>
      ))}
      {onSetTags && (adding ? (
        <input
          className="pl__tag-input"
          value={draft}
          autoFocus
          aria-label="Nuovo tag"
          placeholder="tag…"
          onChange={e => setDraft(e.target.value)}
          onBlur={addTag}
          onKeyDown={e => {
            if (e.key === 'Enter') addTag();
            else if (e.key === 'Escape') { setDraft(''); setAdding(false); }
          }}
        />
      ) : (
        <button type="button" className="pl__tag-add" title="Aggiungi tag"
          onClick={() => setAdding(true)}>＋ tag</button>
      ))}
    </div>
  );
}
