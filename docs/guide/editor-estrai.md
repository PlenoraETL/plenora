# Editor Estrai

<!-- Generato da frontend/src/utils/guides/editorGuides.js: è la stessa guida che si apre nell'app dal menu «⋯» dell'editor, dal canvas vuoto e col tasto F1. Modifica quel file e rigenera; non modificare questo a mano. -->

Estrai costruisce una query SQL (o una lettura da un servizio ArcGIS) collegando tabelle con dei Join: il risultato va nell'Output, che si scarica o si carica su un database.

## Tabelle e viste

Le tabelle e le viste della connessione del progetto sono nella barra a sinistra e in «＋ Passo». La ricerca guarda il nome della tabella e i nomi delle colonne.

Nel pannello di una tabella scegli le colonne da tenere, i loro nomi in uscita, le colonne calcolate e i filtri.

La stessa tabella si può aggiungere più volte (per esempio per unire una tabella a se stessa): ogni copia riceve un alias diverso, che si vede nel titolo e nel SQL.

## Join tra tabelle

Collega una tabella a un'altra trascinando dal bordo destro al bordo sinistro, oppure seleziona la tabella e premi `C` («Collega a…»). Il Join nasce «da completare»: scegli le colonne nel pannello che si apre.

Il tipo predefinito è «Tieni tutte le righe di sinistra (Left)», lo stesso in tutti gli editor.

## Filtri con condizioni E / O

Le condizioni di un gruppo devono essere tutte vere (E). Con «＋ Aggiungi un gruppo O» si crea un altro gruppo: una riga passa se è vero almeno un gruppo.

Esempio: (stato è uguale a `aperto` E priorità è maggiore di `2`) O (stato è uguale a `urgente`). Nel SQL diventa `(stato = 'aperto' AND priorita > 2) OR (stato = 'urgente')`.

> **Attenzione:** I filtri salvati prima dei gruppi restano tutti in E, come prima.

## Output e caricamenti

L'Output si aggiunge da «＋ Passo» (gruppo «Uscite») o dal menu «⋯»: uno solo per progetto. Senza Output «Esegui» resta spento e la barra lo dice.

«Carica su database» e «Carica su ArcGIS» scrivono l'Output: servono dopo l'Output e nascono già collegati.

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
