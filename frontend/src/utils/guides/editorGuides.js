/**
 * Guide d'uso degli editor Estrai, Manipola e Arricchisci (UI-3, U30): per
 * gli utenti, non per gli sviluppatori. UNA fonte: la guida nell'app
 * (common/GuideModal, dal menu "⋯", dal canvas vuoto e col tasto F1) e i
 * file del repository docs/guide/editor-<modulo>.md, rigenerati da qui e
 * confrontati da tests/shared/userGuides.contract.test.jsx.
 *
 * Aiuto contestuale: ogni sezione dichiara i tipi di nodo che spiega
 * (`nodeTypes`); con un nodo selezionato la guida si apre su quella sezione.
 *
 * Le scorciatoie vengono dalla tabella unica dei tasti (utils/shortcuts,
 * gruppo «Editor»): la guida non le riscrive a mano.
 */
import { SHORTCUTS, shortcutParts } from '../shortcuts';

// Tasti scritti come nella guida dei tasti, dalla stessa tabella (utils/shortcuts).
const shortcutKeys = (s) => shortcutParts(s).join('` o `');
// Il tasto principale di una scorciatoia in una frase della guida.
const k = (id) => shortcutParts(id)[0];

const shortcutsSection = () => ({
  id: 'scorciatoie',
  title: 'Scorciatoie da tastiera',
  nodeTypes: [],
  blocks: [
    { p: 'Funzionano con il fuoco sul canvas (non mentre scrivi in un campo o con una finestra aperta). Le stesse azioni sono nel menu «⋯» della barra; l’elenco completo dei tasti si apre con «?».' },
    { ul: SHORTCUTS.filter(s => s.group === 'Editor').map(s => `\`${shortcutKeys(s)}\`: ${s.label}.`) },
  ],
});

const SAVE_AND_RUN = {
  id: 'salva-ed-esegui',
  title: 'Salvare, controllare ed eseguire',
  nodeTypes: ['result', 'resultNode'],
  blocks: [
    { p: 'Quello che gira (Pianificazioni, Eseguibili) è sempre l\'ultima versione SALVATA. «Valida», «Anteprima» ed «Esegui» lavorano sulla versione salvata: con modifiche non salvate il pulsante diventa «Salva e …» e salva prima di chiedere al server.' },
    { ul: [
      '«Valida» controlla il canvas e segna i nodi con un problema.',
      '«Anteprima» mostra le prime righe del risultato dell\'Output; i dati di un singolo passo si vedono in basso con «Mostra i dati».',
      '«Esegui» calcola il risultato. Se fallisce, la finestra dice il passo che ha fallito («Vai al passo»), apre il registro dell\'esecuzione e permette di segnalare il problema con progetto ed esecuzione già compilati.',
    ] },
  ],
};

const FILTERS = (sqlNames) => ({
  id: 'filtri',
  title: 'Filtri con condizioni E / O',
  nodeTypes: sqlNames ? ['tableNode'] : ['transform'],
  blocks: [
    { p: 'Le condizioni di un gruppo devono essere tutte vere (E). Con «＋ Aggiungi un gruppo O» si crea un altro gruppo: una riga passa se è vero almeno un gruppo.' },
    { p: sqlNames
      ? 'Esempio: (stato è uguale a `aperto` E priorità è maggiore di `2`) O (stato è uguale a `urgente`). Nel SQL diventa `(stato = \'aperto\' AND priorita > 2) OR (stato = \'urgente\')`.'
      : 'Esempio: (stato è uguale a `aperto` E priorità è maggiore di `2`) O (stato è uguale a `urgente`).' },
    { note: 'I filtri salvati prima dei gruppi restano tutti in E, come prima.' },
  ],
});

const SOURCES_SIDEBAR = 'Nella barra a sinistra: clic o Invio aggiunge la sorgente al centro della vista, il trascinamento la mette dove vuoi. Le stesse sorgenti sono anche in «＋ Passo» (gruppo «Sorgenti»).';

const ESTRAI_GUIDE = Object.freeze({
  slug: 'editor-estrai',
  title: 'Editor Estrai',
  intro: 'Estrai costruisce una query SQL (o una lettura da un servizio ArcGIS) collegando tabelle con dei Join: il risultato va nell\'Output, che si scarica o si carica su un database.',
  sections: [
    {
      id: 'tabelle',
      title: 'Tabelle e viste',
      nodeTypes: ['tableNode', 'featureLayerNode'],
      blocks: [
        { p: 'Le tabelle e le viste della connessione del progetto sono nella barra a sinistra e in «＋ Passo». La ricerca guarda il nome della tabella e i nomi delle colonne.' },
        { p: 'Nel pannello di una tabella scegli le colonne da tenere, i loro nomi in uscita, le colonne calcolate e i filtri.' },
        { p: 'La stessa tabella si può aggiungere più volte (per esempio per unire una tabella a se stessa): ogni copia riceve un alias diverso, che si vede nel titolo e nel SQL.' },
      ],
    },
    {
      id: 'join',
      title: 'Join tra tabelle',
      nodeTypes: [],
      blocks: [
        { p: 'Collega una tabella a un\'altra trascinando dal bordo destro al bordo sinistro, oppure seleziona la tabella e premi `C` («Collega a…»). Il Join nasce «da completare»: scegli le colonne nel pannello che si apre.' },
        { p: 'Il tipo predefinito è «Tieni tutte le righe di sinistra (Left)», lo stesso in tutti gli editor.' },
      ],
    },
    FILTERS(true),
    {
      id: 'output',
      title: 'Output e caricamenti',
      nodeTypes: ['resultNode', 'carica', 'carica_arcgis', 'caricaArcgis'],
      blocks: [
        { p: 'L\'Output si aggiunge da «＋ Passo» (gruppo «Uscite») o dal menu «⋯»: uno solo per progetto. Senza Output «Esegui» resta spento e la barra lo dice.' },
        { p: '«Carica su database» e «Carica su ArcGIS» scrivono l\'Output: servono dopo l\'Output e nascono già collegati.' },
      ],
    },
    SAVE_AND_RUN,
    shortcutsSection(),
  ],
});

const MANIPOLA_GUIDE = Object.freeze({
  slug: 'editor-manipola',
  title: 'Editor Manipola',
  intro: 'Manipola trasforma i dati di altri progetti e di file caricati con una sequenza di passi (filtri, formule, Join, raggruppamenti, operazioni geografiche) fino all\'Output.',
  sections: [
    {
      id: 'sorgenti',
      title: 'Sorgenti: progetti e file',
      nodeTypes: ['project_source', 'file_source', 'datasourceNode'],
      blocks: [
        { p: SOURCES_SIDEBAR },
        { p: 'Un file nuovo si carica senza uscire dall\'editor: «＋ Carica un file» nella barra a sinistra. A caricamento finito il file entra nel canvas.' },
        { p: 'Un progetto sorgente usa il suo ultimo risultato salvato: se è stato modificato dopo, il nodo lo dice e l\'esecuzione chiede di rieseguirlo o di ricalcolare tutto.' },
      ],
    },
    {
      id: 'passi',
      title: 'Aggiungere e collegare i passi',
      nodeTypes: ['transform', 'multi_join'],
      blocks: [
        { p: `«＋ Passo» (o \`${k('add_step')}\`) apre «Cosa vuoi fare?»: cerca un\'operazione per nome o per scopo. Con un passo selezionato il nuovo passo nasce dopo di lui e, se dopo c’è già un altro passo, entra in mezzo.` },
        { p: 'Le operazioni preferite (★) e quelle usate di recente compaiono in cima.' },
        { p: `Per collegare due passi trascina dal bordo destro al bordo sinistro, oppure seleziona il passo e premi \`${k('connect')}\`. «Duplica passo» (\`${k('duplicate')}\`) crea una copia con gli stessi ingressi; copia e incolla (\`${k('copy')}\`, \`${k('paste')}\`) copiano anche più passi con i loro collegamenti.` },
      ],
    },
    FILTERS(false),
    {
      id: 'output',
      title: 'Output e caricamenti',
      nodeTypes: ['result', 'carica', 'carica_arcgis', 'caricaArcgis'],
      blocks: [
        { p: 'Un solo Output per progetto; «Carica su database» e «Carica su ArcGIS» lo scrivono e nascono collegati. Le voci che non si possono ancora usare sono spente nel menu con il motivo scritto sotto.' },
        { p: '«Usa i risultati salvati delle sorgenti» (predefinito) è veloce; la finestra dice di quando sono i risultati di ogni progetto sorgente. «Ricalcola tutto» riesegue anche i progetti a monte.' },
      ],
    },
    SAVE_AND_RUN,
    shortcutsSection(),
  ],
});

const ARRICCHISCI_GUIDE = Object.freeze({
  slug: 'editor-arricchisci',
  title: 'Editor Arricchisci',
  intro: 'Arricchisci aggiunge ai dati di un progetto o di un file le risposte di una API, riga per riga.',
  sections: [
    {
      id: 'sorgenti',
      title: 'Sorgenti',
      nodeTypes: ['project_source', 'file_source'],
      blocks: [
        { p: SOURCES_SIDEBAR },
        { p: 'Un file nuovo si carica con «＋ Carica un file» nella barra a sinistra, senza uscire dall\'editor.' },
      ],
    },
    {
      id: 'arricchimenti',
      title: 'Arricchimenti API',
      nodeTypes: ['api_enrich'],
      blocks: [
        { p: '«＋ Passo» propone le API configurate (manuali, OpenAPI, processori di file); scegli la connessione e collega il nodo dopo la sorgente. Nel pannello associ le colonne ai parametri della chiamata.' },
      ],
    },
    {
      id: 'output',
      title: 'Output e caricamenti',
      nodeTypes: ['result', 'carica', 'carica_arcgis', 'caricaArcgis'],
      blocks: [
        { p: 'Un solo Output per progetto; i caricamenti nascono collegati all\'Output. Prima di avviare, la finestra stima quante chiamate verranno fatte.' },
      ],
    },
    SAVE_AND_RUN,
    shortcutsSection(),
  ],
});

export const EDITOR_GUIDES = Object.freeze({
  estrai: ESTRAI_GUIDE,
  manipola: MANIPOLA_GUIDE,
  arricchisci: ARRICCHISCI_GUIDE,
});

/** Sezione della guida che spiega un tipo di nodo (null = inizio della guida). */
export function guideSectionFor(guide, nodeType) {
  if (!guide || !nodeType) return null;
  return guide.sections.find(s => (s.nodeTypes || []).includes(nodeType))?.id || null;
}

/** Testo del canvas vuoto per editor: cosa fare per primo. */
export const EMPTY_CANVAS_HINTS = Object.freeze({
  estrai: 'Comincia da una tabella: cliccala o trascinala dalla barra a sinistra, oppure premi «＋ Aggiungi» e cercala per nome o per colonna.',
  manipola: 'Comincia da una sorgente: un progetto o un file dalla barra a sinistra (clic o trascinamento), oppure «＋ Aggiungi». Poi aggiungi i passi e infine l\'Output.',
  arricchisci: 'Comincia da una sorgente: un progetto o un file dalla barra a sinistra, poi aggiungi un arricchimento API e l\'Output.',
});
