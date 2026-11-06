# Editor Arricchisci

<!-- Generato da frontend/src/utils/guides/editorGuides.js: è la stessa guida che si apre nell'app dal menu «⋯» dell'editor, dal canvas vuoto e col tasto F1. Modifica quel file e rigenera; non modificare questo a mano. -->

Arricchisci aggiunge ai dati di un progetto o di un file le risposte di una API, riga per riga.

## Sorgenti

Nella barra a sinistra: clic o Invio aggiunge la sorgente al centro della vista, il trascinamento la mette dove vuoi. Le stesse sorgenti sono anche in «＋ Passo» (gruppo «Sorgenti»).

Un file nuovo si carica con «＋ Carica un file» nella barra a sinistra, senza uscire dall'editor.

## Arricchimenti API

«＋ Passo» propone le API configurate (manuali, OpenAPI, processori di file); scegli la connessione e collega il nodo dopo la sorgente. Nel pannello associ le colonne ai parametri della chiamata.

## Output e caricamenti

Un solo Output per progetto; i caricamenti nascono collegati all'Output. Prima di avviare, la finestra stima quante chiamate verranno fatte.

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
