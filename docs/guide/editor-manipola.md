# Editor Manipola

<!-- Generato da frontend/src/utils/guides/editorGuides.js: è la stessa guida che si apre nell'app dal menu «⋯» dell'editor, dal canvas vuoto e col tasto F1. Modifica quel file e rigenera; non modificare questo a mano. -->

Manipola trasforma i dati di altri progetti e di file caricati con una sequenza di passi (filtri, formule, Join, raggruppamenti, operazioni geografiche) fino all'Output.

## Sorgenti: progetti e file

Nella barra a sinistra: clic o Invio aggiunge la sorgente al centro della vista, il trascinamento la mette dove vuoi. Le stesse sorgenti sono anche in «＋ Passo» (gruppo «Sorgenti»).

Un file nuovo si carica senza uscire dall'editor: «＋ Carica un file» nella barra a sinistra. A caricamento finito il file entra nel canvas.

Un progetto sorgente usa il suo ultimo risultato salvato: se è stato modificato dopo, il nodo lo dice e l'esecuzione chiede di rieseguirlo o di ricalcolare tutto.

## Aggiungere e collegare i passi

«＋ Passo» (o `/`) apre «Cosa vuoi fare?»: cerca un'operazione per nome o per scopo. Con un passo selezionato il nuovo passo nasce dopo di lui e, se dopo c’è già un altro passo, entra in mezzo.

Le operazioni preferite (★) e quelle usate di recente compaiono in cima.

Per collegare due passi trascina dal bordo destro al bordo sinistro, oppure seleziona il passo e premi `C`. «Duplica passo» (`Ctrl+D`) crea una copia con gli stessi ingressi; copia e incolla (`Ctrl+C`, `Ctrl+V`) copiano anche più passi con i loro collegamenti.

## Filtri con condizioni E / O

Le condizioni di un gruppo devono essere tutte vere (E). Con «＋ Aggiungi un gruppo O» si crea un altro gruppo: una riga passa se è vero almeno un gruppo.

Esempio: (stato è uguale a `aperto` E priorità è maggiore di `2`) O (stato è uguale a `urgente`).

> **Attenzione:** I filtri salvati prima dei gruppi restano tutti in E, come prima.

## Output e caricamenti

Un solo Output per progetto; «Carica su database» e «Carica su ArcGIS» lo scrivono e nascono collegati. Le voci che non si possono ancora usare sono spente nel menu con il motivo scritto sotto.

«Usa i risultati salvati delle sorgenti» (predefinito) è veloce; la finestra dice di quando sono i risultati di ogni progetto sorgente. «Ricalcola tutto» riesegue anche i progetti a monte.

## Salvare, controllare ed eseguire

Quello che gira (Pianificazioni, Eseguibili) è sempre l'ultima versione SALVATA. «Valida», «Anteprima» ed «Esegui» lavorano sulla versione salvata: con modifiche non salvate il pulsante diventa «Salva e …» e salva prima di chiedere al server.

- «Valida» controlla il canvas e segna i nodi con un problema.
- «Anteprima» mostra le prime righe del risultato dell'Output; i dati di un singolo passo si vedono in basso con «Mostra i dati».
- «Esegui» calcola il risultato. Se fallisce, la finestra dice il passo che ha fallito («Vai al passo»), apre il registro dell'esecuzione e permette di segnalare il problema con progetto ed esecuzione già compilati.

## Scorciatoie da tastiera

Funzionano con il fuoco sul canvas (non mentre scrivi in un campo o con una finestra aperta). Le stesse azioni sono nel menu «⋯» della barra; l’elenco completo dei tasti si apre con «?».

- `Ctrl+S`: Salva il progetto.
- `/` o `A`: Aggiungi un passo (con il fuoco sul canvas).
- `Ctrl+Z`: Annulla l’ultima modifica.
- `Ctrl+⇧+Z` o `Ctrl+Y`: Ripristina la modifica annullata.
- `Canc` o `⌫`: Elimina il passo o il collegamento selezionato (chiede conferma).
- `Ctrl+A`: Seleziona tutti i nodi del canvas.
- `Ctrl+C`: Copia i nodi selezionati (con i loro collegamenti).
- `Ctrl+V`: Incolla i nodi copiati.
- `Ctrl+D`: Duplica il passo selezionato con gli stessi ingressi.
- `Ctrl+F`: Cerca un nodo nel canvas e vai lì.
- `C`: Collega il nodo selezionato a un altro (da tastiera).
- `F1`: Guida dell’editor, sulla parte del nodo selezionato.
