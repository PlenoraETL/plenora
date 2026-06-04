/**
 * ProjectListSkeleton.jsx — Skeleton screen della dashboard (Epic C).
 *
 * Sostituisce il vecchio "⬡ Caricamento..." a schermo vuoto durante le fetch
 * iniziali. Rispecchia la struttura reale (sidebar + topbar + stats row + due
 * sezioni di card) con placeholder shimmer, così il passaggio a contenuto
 * caricato è senza salti di layout e la latenza è percepita come minore.
 */
import React from 'react';
import AppSidebar from '../AppSidebar';

const ZERO_COUNTS = {
  projects: 0, connections: 0, apiConnections: 0,
  datasources: 0, files: 0, schedules: 0,
};

export default function ProjectListSkeleton() {
  return (
    <div className="pl" aria-busy="true" aria-label="Caricamento dashboard">
      <AppSidebar counts={ZERO_COUNTS} />
      <div className="pl__main">
        <header className="pl__topbar">
          <div className="pl__page-title">Dashboard</div>
          <div className="pl__topbar-right">
            <div className="pl__skel pl__skel--field" />
            <div className="pl__skel pl__skel--chip" />
            <div className="pl__skel pl__skel--chip" />
          </div>
        </header>

        <div className="pl__content">
          {/* Stats row: 9 box come la griglia reale */}
          <div className="pl__stats-row">
            {Array.from({ length: 9 }).map((_, i) => (
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
      </div>
    </div>
  );
}
