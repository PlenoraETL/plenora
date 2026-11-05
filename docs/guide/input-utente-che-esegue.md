# Input «Utente che esegue»

<!-- Generato da frontend/src/utils/guides/utenteCheEsegue.js: è la stessa guida che si apre nell'app dal nodo «Input di progetto» (Guida completa). Modifica quel file e rigenera; non modificare questo a mano. -->

Come mostrare a ogni utente solo i suoi dati: l'input «Utente che esegue» porta nella catena il nome utente di chi avvia l'Eseguibile.

## Cos'è

È un tipo del nodo «Input di progetto». Il suo valore è il nome utente con cui la persona ha fatto l'accesso a Plenora (lo username di login), esattamente com'è scritto nell'elenco utenti.

Si usa per filtrare i dati: lo stesso Eseguibile, aperto da utenti diversi, restituisce a ciascuno solo le righe che lo riguardano (per esempio solo il suo istituto).

Si può usare in due modi:

- Collegato a un parametro: il valore di un filtro di Estrai, di un parametro di un passo (per esempio il valore di «Filtra») o di un parametro di una chiamata API.
- Come sorgente, in Manipola: il nodo diventa una tabella di una riga con una colonna di testo (di solito `utente`) che contiene il nome utente; da lì lo si trasforma con i passi (Formula, Mappa valori) e lo si incrocia con i dati con un Join interno.

## Sicurezza: nessuno può falsificarlo

Il valore lo mette solo il server, con l'utente che ha fatto l'accesso. Nella finestra «Prima di avviare» si vede in sola lettura e non si può cambiare; se qualcuno provasse a inviarne uno diverso, la richiesta viene rifiutata.

Chi apre l'Eseguibile non può quindi farsi passare per un altro utente né vedere i dati di un altro: il filtro usa sempre il suo nome utente.

> **Attenzione:** La sicurezza vale per il filtro che hai costruito: se la catena non usa davvero il valore (input non collegato, Join non interno), i dati non vengono filtrati. Prova sempre il risultato con un nome utente di prova.

## Configurazione passo passo (esempio)

Esempio reale: gli utenti hanno nomi come `E0041` e i dati hanno una colonna istituto nella forma `NNN_IS` (per esempio `004_IS`). Ogni utente deve vedere solo le righe del suo istituto.

1. Apri il progetto Manipola e aggiungi un nodo «Input di progetto».
2. In «Tipo» scegli «Utente che esegue».
3. In «Come entra nella catena» scegli «Sorgente: tabella di una riga». Lascia il nome della colonna `utente` (o scegline un altro).
4. In «Nome utente di prova» scrivi un nome utente vero, per esempio `E0041`: serve per Anteprima, Valida ed Esegui di prova.
5. Collega il nodo a un passo Formula che crea la colonna `istituto` con la formula qui sotto: da `E0041` ottiene `004_IS`.
6. Aggiungi un Join interno (tipo «Inner (solo corrispondenze)») tra i dati principali e il risultato della Formula, sulla colonna istituto dei dati e sulla colonna `istituto` della Formula.
7. Collega il Join all'Output, salva e premi «Anteprima»: devi vedere solo le righe di `004_IS`.

```text
utente.str.slice(1, 4) + '_IS'
```

La formula prende i caratteri dal secondo al quarto del nome utente (`004` da `E0041`) e aggiunge `_IS`.

> **Attenzione:** Il nome utente, dopo i passi, deve avere ESATTAMENTE la forma dei dati: stesse cifre, stessi zeri iniziali, stesse maiuscole. Se non corrisponde, il Join interno non trova righe e il risultato è vuoto.

Al posto della sorgente puoi collegare l'input direttamente a un parametro, quando i dati contengono già il nome utente così com'è (per esempio una colonna `owner`): in «Come entra nella catena» lascia «Valore di un parametro» e collega l'input al filtro o al passo.

## Provare dall'editor: Anteprima, Valida, Esegui di prova

Nell'editor non c'è un utente che esegue: al suo posto si usa il nome utente di prova scritto nell'input.

- Anteprima e Valida usano il nome utente di prova; senza, si fermano e lo chiedono.
- «Esegui» diventa un'esecuzione di prova. Può farla solo chi può modificare il progetto (non i visualizzatori, non gli esecutori).
- L'esecuzione di prova usa il nome utente di prova SALVATO nel progetto: salva prima di eseguire. La finestra «Prima di avviare» mostra con quale utente parte.
- Il risultato si scarica una volta dalla finestra di esecuzione. Non diventa l'Output del progetto: non cambia i progetti che lo usano come sorgente, non avvia Carica e non aggiorna le colonne pubblicate.
- Nella cronologia delle esecuzioni compare come «Esecuzione di prova (utente: …)» e non conta come ultima esecuzione del progetto.

## Pubblicare come Eseguibile

Quando il risultato di prova è giusto, pubblica il progetto come Eseguibile. Da quel momento ogni persona che lo apre lo esegue con il proprio nome utente: non deve scrivere nulla, la riga «Utente che esegue» in «Prima di avviare» mostra il suo nome e non si può cambiare.

Il nome utente di prova non entra nell'Eseguibile: vale solo nell'editor.

## Pianificazioni e catene

Una Pianificazione o una catena di progetti parte senza una persona che la avvia: non c'è un utente, quindi un progetto con «Utente che esegue» si ferma con un messaggio che lo spiega. Non si usa il nome utente di prova. Per questi progetti usa l'Eseguibile.

## Errori comuni

- Il nome utente ha una forma diversa dai dati (per esempio `E0041` contro `004_IS`): senza una Formula che lo trasformi il Join non trova righe. Controlla con l'Anteprima e un nome utente di prova vero.
- Maiuscole e minuscole: `e0041` e `E0041` sono valori diversi per il Join. Il nome utente arriva esattamente come è scritto nell'elenco utenti; se i dati usano un'altra forma, trasformalo (per esempio con `.str.upper()` nella Formula).
- Un utente che vede l'Eseguibile ma non ha dati corrispondenti (per esempio un utente senza istituto o un amministratore) ottiene un risultato vuoto, non un errore: il filtro funziona, ma non ci sono righe per lui. Se qualcuno deve vedere tutto, serve un Eseguibile diverso.
- Nessun nome utente di prova: Anteprima, Valida ed Esegui di prova si fermano e chiedono di indicarlo.
- Due input «Utente che esegue» con nomi utente di prova diversi: usane uno solo.
- «Esegui» dall'editor fermo con un visualizzatore: l'esecuzione di prova è riservata a chi può modificare il progetto. Apri l'Eseguibile per eseguirlo con il tuo utente.
