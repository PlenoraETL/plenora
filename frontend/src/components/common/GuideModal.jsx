/**
 * GuideModal — una guida d'uso completa dentro l'app (utils/guides). La
 * stessa guida e' il file docs/guide/<slug>.md del repository (rigenerato
 * dallo stesso contenuto e confrontato da tests/shared/userGuides).
 *
 * Il testo e' reso come elementi React (nessun HTML grezzo); `così` diventa
 * codice.
 */
import React, { useEffect, useRef } from 'react';
import ModalWrapper from './ModalWrapper';
import './GuideModal.css';

/** Testo con `codice`: pezzi alterni testo / codice. */
function InlineText({ text }) {
  const parts = String(text).split('`');
  return parts.map((part, i) => (i % 2 === 1
    // eslint-disable-next-line react/no-array-index-key -- pezzi fissi di un testo statico
    ? <code key={i} className="guide__code-inline">{part}</code>
    // eslint-disable-next-line react/no-array-index-key -- pezzi fissi di un testo statico
    : <React.Fragment key={i}>{part}</React.Fragment>));
}

function Block({ block }) {
  if (block.p) return <p className="guide__p"><InlineText text={block.p} /></p>;
  if (block.note) return <p className="guide__note" role="note"><InlineText text={block.note} /></p>;
  if (block.code) return <pre className="guide__code"><code>{block.code}</code></pre>;
  const items = block.ul || block.ol || [];
  const List = block.ol ? 'ol' : 'ul';
  return (
    <List className="guide__list">
      {items.map(item => <li key={item}><InlineText text={item} /></li>)}
    </List>
  );
}

// `section` (UI-3, aiuto contestuale): id della sezione su cui aprire la
// guida (es. quella del nodo selezionato); evidenziata e portata in vista.
export default function GuideModal({ guide, isOpen, onClose, section = null }) {
  const articleRef = useRef(null);
  useEffect(() => {
    if (!isOpen || !section) return;
    articleRef.current?.querySelector(`[data-guide-section="${section}"]`)?.scrollIntoView?.({ block: 'start' });
  }, [isOpen, section]);
  if (!isOpen || !guide) return null;
  return (
    <ModalWrapper isOpen onClose={onClose} title={`Guida: ${guide.title}`} icon="📘" size="lg">
      <article ref={articleRef} className="guide" aria-label={`Guida: ${guide.title}`}>
        {guide.intro && <p className="guide__p guide__intro"><InlineText text={guide.intro} /></p>}
        {guide.sections.map(s => (
          <section key={s.title} data-guide-section={s.id || undefined}
            className={`guide__section${section && s.id === section ? ' guide__section--focus' : ''}`}>
            {/* h4: il titolo della finestra (ModalWrapper) e' h3. */}
            <h4 className="guide__heading">{s.title}</h4>
            {s.blocks.map(block => (
              <Block key={block.p || block.note || block.code || (block.ul || block.ol || []).join('|')} block={block} />
            ))}
          </section>
        ))}
      </article>
    </ModalWrapper>
  );
}
