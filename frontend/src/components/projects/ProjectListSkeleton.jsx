/**
 * ProjectListSkeleton.jsx — Skeleton screen della dashboard (Epic C).
 *
 * Sostituisce il vecchio "⬡ Caricamento..." a schermo vuoto durante le fetch
 * iniziali. Stesso layout della pagina (PageLayout, G1) con placeholder
 * shimmer, così il passaggio a contenuto caricato è senza salti di layout.
 */
import React from 'react';
import PageLayout from '../common/PageLayout';

export default function ProjectListSkeleton() {
  return (
    <PageLayout
      className="pl"
      bare
      aria-busy="true"
      aria-label="Caricamento dashboard"
      title="Dashboard"
      actions={(
        <>
          <div className="pl__skel pl__skel--field" />
          <div className="pl__skel pl__skel--chip" />
          <div className="pl__skel pl__skel--chip" />
        </>
      )}
    >
        <div className="pl__content">
          <div className="pl__stats-row">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="pl__stat pl__skel pl__skel--stat" />
            ))}
          </div>

          {/* Due sezioni con header + griglia di card placeholder */}
          {Array.from({ length: 2 }).map((_, s) => (
            <section key={s} className="pl__section">
              <div className="pl__section-header">
                <div className="pl__skel pl__skel--title" />
              </div>
              <div className="pl__cards-grid">
                {Array.from({ length: 3 }).map((_, c) => (
                  <div key={c} className="pl__card pl__skel-card">
                    <div className="pl__skel pl__skel--line pl__skel--line-lg" />
                    <div className="pl__skel pl__skel--line pl__skel--line-md" />
                    <div className="pl__skel pl__skel--line pl__skel--line-sm" />
                  </div>
                ))}
              </div>
            </section>
          ))}
        </div>
    </PageLayout>
  );
}
