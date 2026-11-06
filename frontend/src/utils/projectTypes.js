/**
 * projectTypes — UNICA tabella dei tipi di progetto: nome, icona e
 * descrizione (UI-4, U24 e U32). La leggono la dashboard (sezioni e card),
 * la creazione del progetto, la sidebar dei progetti negli editor, gli
 * Eseguibili e il nodo «Progetto sorgente». Prima un progetto Estrai era
 * «SQL» in un punto, «ESTRAI» in un altro, con tre icone diverse.
 */
import { ICON } from './icons';

export const PROJECT_TYPES = Object.freeze({
  estrai: {
    label: 'Estrai',
    icon: ICON.estrai,
    description: 'Legge tabelle e viste di un database o di un servizio ArcGIS, con filtri, Join e colonne calcolate.',
  },
  manipola: {
    label: 'Manipola',
    icon: ICON.manipola,
    description: 'Lavora su file, Data Source e risultati di altri progetti con una sequenza di passi.',
  },
  arricchisci: {
    label: 'Arricchisci',
    icon: ICON.arricchisci,
    description: 'Aggiunge ai dati le risposte di API esterne, riga per riga.',
  },
});

/** Tipo di un progetto (assente o sconosciuto = Estrai, come il backend e le rotte). */
const projectTypeKey = (type) => (PROJECT_TYPES[type] ? type : 'estrai');

/** Voce della tabella per un tipo (con la regola di riserva). */
export const projectType = (type) => PROJECT_TYPES[projectTypeKey(type)];

/** Etichetta con icona: «↓ Estrai». */
export const projectTypeChip = (type) => `${projectType(type).icon} ${projectType(type).label}`;
