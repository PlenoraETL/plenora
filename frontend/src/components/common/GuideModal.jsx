/**
 * GuideModal — una guida d'uso completa dentro l'app (utils/guides). La
 * stessa guida e' il file docs/guide/<slug>.md del repository (rigenerato
 * dallo stesso contenuto e confrontato da tests/shared/userGuides).
 *
 * Il testo e' reso come elementi React (nessun HTML grezzo); `così` diventa
 * codice.
 */
import React from 'react';
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

export default function GuideModal({ guide, isOpen, onClose }) {
  if (!isOpen || !guide) return null;
  return (
    <ModalWrapper isOpen onClose={onClose} title={`Guida: ${guide.title}`} icon="📘" size="lg">
      <article className="guide" aria-label={`Guida: ${guide.title}`}>
        {guide.intro && <p className="guide__p guide__intro"><InlineText text={guide.intro} /></p>}
        {guide.sections.map(section => (
          <section key={section.title} className="guide__section">
            <h3 className="guide__heading">{section.title}</h3>
            {section.blocks.map(block => (
              <Block key={block.p || block.note || block.code || (block.ul || block.ol || []).join('|')} block={block} />
            ))}
          </section>
        ))}
      </article>
    </ModalWrapper>
  );
}
