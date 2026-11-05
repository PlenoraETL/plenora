/**
 * PageLayout — l'unico layout delle pagine di sistema e della dashboard
 * (Fase 5 T7, linea guida G1, voci S1, S2, S6, S8, L12).
 *
 *   <PageLayout title="Data Source" subtitle="…" actions={<button…/>}>
 *     …contenuto…
 *   </PageLayout>
 *
 * Regole:
 *  - sidebar comune a sinistra, intestazione con titolo (h1) e sottotitolo,
 *    azioni a destra, cambio tema sempre nello stesso punto;
 *  - nessun «← Indietro» / «← Dashboard»: le pagine di primo livello si
 *    raggiungono dalla sidebar (tornare nella storia poteva uscire dall'app). Una
 *    pagina di dettaglio passa `parent` (link alla sua lista, non la storia);
 *  - il tema della pagina è quello dei token `--plen-*` (chiaro e scuro);
 *  - il contenuto scorre in verticale dentro `body`, mai in orizzontale la
 *    pagina intera (la tabella larga scorre nel suo contenitore); `bare`:
 *    la pagina ha già il suo contenitore che scorre (es. `dsm__content`);
 *  - la sidebar non riceve contatori dalla pagina: gli stessi badge su ogni
 *    pagina (L12).
 *
 * Il test tests/shared/pageLayout.contract.test.jsx enumera le rotte.
 */
import React from 'react';
import { Link } from 'react-router-dom';
import AppSidebar from '../AppSidebar';
import ThemeToggle from './ThemeToggle';
import './PageLayout.css';

function PageHeader({ title, subtitle, badge, actions, parent }) {
  return (
    <header className="plen-page__header">
      <div className="plen-page__heading">
        {parent && (
          <Link className="plen-page__parent" to={parent.to}>{parent.label}</Link>
        )}
        <h1 className="plen-page__title">
          {title}
          {badge}
        </h1>
        {subtitle && <div className="plen-page__subtitle">{subtitle}</div>}
      </div>
      <div className="plen-page__actions">
        {actions}
        <ThemeToggle classPrefix="plen-page" />
      </div>
    </header>
  );
}

export default function PageLayout({
  className = '',
  bare = false,
  title,
  subtitle,
  badge,
  actions,
  parent,
  children,
  ...rest
}) {
  return (
    <div className={`plen-page${className ? ` ${className}` : ''}`} {...rest}>
      <AppSidebar />
      <main className="plen-page__main">
        <PageHeader title={title} subtitle={subtitle} badge={badge} actions={actions} parent={parent} />
        <div className={`plen-page__body${bare ? ' plen-page__body--bare' : ''}`}>
          {children}
        </div>
      </main>
    </div>
  );
}
