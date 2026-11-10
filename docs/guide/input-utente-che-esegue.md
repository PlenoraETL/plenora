# Input «Utente che esegue»

<!-- Generato da frontend/src/utils/guides/utenteCheEsegue.js: è la stessa guida che si apre nell'app dal nodo «Input di progetto» (Guida completa). Modifica quel file e rigenera; non modificare questo a mano. -->

Come mostrare a ogni utente solo i suoi dati: l'input «Utente che esegue» porta nel progetto il nome utente di chi avvia l'Eseguibile.

## Cos'è

È un tipo del nodo «Input di progetto». Il suo valore è il nome utente con cui la persona ha fatto l'accesso a Plenora (lo username di login), esattamente com'è scritto nell'elenco utenti.

Si usa per filtrare i dati: lo stesso Eseguibile, aperto da utenti diversi, restituisce a ciascuno solo le righe che lo riguardano (per esempio solo il suo istituto).

Si può usare in due modi:

- Collegato a un parametro: il valore di un filtro di Estrai, di un parametro di un passo (per esempio il valore di «Filtra») o di un parametro di una chiamata API.
- Come sorgente, in Manipola: il nodo diventa una tabella di una riga con una colonna di testo (di solito `utente`) che contiene il nome utente; da lì lo si trasforma con i passi (Formula, Mappa valori) e lo si incrocia con i dati con un Join interno.

## Sicurezza: nessuno può falsificarlo

Il valore lo mette solo il server, con l'utente che ha fatto l'accesso. Nella finestra «Prima di avviare» si vede in sola lettura e non si può cambiare; se qualcuno provasse a inviarne uno diverso, la richiesta viene rifiutata.

Chi apre l'Eseguibile non può quindi farsi passare per un altro utente né vedere i dati di un altro: il filtro usa sempre il suo nome utente. L'unica eccezione la decide chi costruisce il progetto, ed è solo per gli amministratori (sezione «Amministratori che scelgono il valore»).

> **Attenzione:** La sicurezza vale per il filtro che hai costruito: se il progetto non usa davvero il valore (input non collegato, Join non interno), i dati non vengono filtrati. Prova sempre il risultato con un nome utente di prova.

## Configurazione passo passo (esempio)

Esempio reale: gli utenti hanno nomi come `E0041` e i dati hanno una colonna istituto nella forma `NNN_IS` (per esempio `004_IS`). Ogni utente deve vedere solo le righe del suo istituto.

1. Apri il progetto Manipola e aggiungi un nodo «Input di progetto».
2. In «Tipo» scegli «Utente che esegue».
3. In «Come entra nel progetto» scegli «Sorgente: tabella di una riga». Lascia il nome della colonna `utente` (o scegline un altro).
4. In «Nome utente di prova» scrivi un nome utente vero, per esempio `E0041`: serve per Anteprima, Valida ed Esegui di prova.
5. Collega il nodo a un passo Formula che crea la colonna `istituto` con la formula qui sotto: da `E0041` ottiene `004_IS`.
6. Aggiungi un Join interno (tipo «Inner (solo corrispondenze)») tra i dati principali e il risultato della Formula, sulla colonna istituto dei dati e sulla colonna `istituto` della Formula.
7. Collega il Join all'Output, salva e premi «Anteprima»: devi vedere solo le righe di `004_IS`.

```text
utente.str.slice(1, 4) + '_IS'
```

La formula prende i caratteri dal secondo al quarto del nome utente (`004` da `E0041`) e aggiunge `_IS`.

> **Attenzione:** Il nome utente, dopo i passi, deve avere ESATTAMENTE la forma dei dati: stesse cifre, stessi zeri iniziali, stesse maiuscole. Se non corrisponde, il Join interno non trova righe e il risultato è vuoto.

Al posto della sorgente puoi collegare l'input direttamente a un parametro, quando i dati contengono già il nome utente così com'è (per esempio una colonna `owner`): in «Come entra nel progetto» lascia «Valore di un parametro» e collega l'input al filtro o al passo.

## Filtrare nel database

Con la sorgente e il Join interno Plenora legge prima TUTTE le righe dal database e filtra dopo: con molti dati (centinaia di migliaia di righe) l'esecuzione dura minuti. Con la «Trasformazione» dell'input il filtro si fa nel database e si leggono solo le righe dell'utente.

1. Apri il progetto Estrai con la tabella dei dati (per esempio «immobili attivi») e aggiungi alla tabella un filtro sulla colonna istituto con operatore «=» (il valore lascialo vuoto).
2. Aggiungi un nodo «Input di progetto», tipo «Utente che esegue», e lascia «Valore di un parametro».
3. In «Trasformazione» scrivi la formula qui sotto: è la stessa sintassi della Formula di Manipola, con la variabile `utente`.
4. In «Nome utente di prova» scrivi `E0041`: sotto compare l'anteprima `E0041 → 004_IS`.
5. Collega l'input alla tabella e scegli come campo il valore del filtro sulla colonna istituto.
6. Premi «Anteprima»: devi vedere solo le righe di `004_IS`. Poi pubblica come Eseguibile.

```text
utente.str.slice(1, 4) + '_IS'
```

La trasformazione la calcola il server, una volta per esecuzione, sul nome utente di chi avvia (nell'editor sul nome utente di prova). Il risultato è il valore dell'input in ogni uso: il valore del filtro, un parametro di un passo, un parametro di una chiamata API, e anche la colonna della sorgente in Manipola. Nessuno può cambiarla al momento dell'esecuzione: sta nell'Eseguibile pubblicato.

Nel filtro di Estrai il valore entra sempre come dato, mai come testo della query: un nome utente con apici, virgole o parole come `NOW()` è confrontato così com'è.

> **Attenzione:** Se la regola dà un valore, si usa anche quando non trova righe: per esempio `admin` diventa `dmi_IS` e riceve un file vuoto. Se invece la regola non si applica (errore, nessun valore, un testo vuoto o un valore che non è un testo), l'esecuzione si ferma con un messaggio che lo dice: Plenora non inventa un valore al suo posto.

## Provare dall'editor: Anteprima, Valida, Esegui di prova

Nell'editor non c'è un utente che esegue: al suo posto si usa il nome utente di prova scritto nell'input.

- Anteprima e Valida usano il nome utente di prova; senza, si fermano e lo chiedono.
- «Esegui» diventa un'esecuzione di prova. Può farla solo chi può modificare il progetto (non i visualizzatori, non gli esecutori).
- L'esecuzione di prova usa il nome utente di prova SALVATO nel progetto: salva prima di eseguire. La finestra «Prima di avviare» mostra con quale utente parte.
- Il risultato si scarica una volta dalla finestra di esecuzione. Non diventa l'Output del progetto: non cambia i progetti che lo usano come sorgente, non avvia Carica e non aggiorna le colonne pubblicate.
- La prova non ricalcola i progetti a monte: usa il loro risultato salvato. «Ricalcola tutto» non vale per la prova. Se un progetto a monte non ha un risultato o è stato modificato dopo l'ultima esecuzione, la prova si ferma con «Esegui prima il progetto a monte»: eseguilo, poi rilancia la prova. Fanno eccezione i progetti a monte con «Utente che esegue» (sezione «Il filtro negli Estrai a monte»).
- Nella cronologia delle esecuzioni compare come «Esecuzione di prova (utente: …)» e non conta come ultima esecuzione del progetto.

## Pubblicare come Eseguibile

Quando il risultato di prova è giusto, pubblica il progetto come Eseguibile. Da quel momento ogni persona che lo apre lo esegue con il proprio nome utente: non deve scrivere nulla, la riga «Utente che esegue» in «Prima di avviare» mostra il suo nome e non si può cambiare.

Il nome utente di prova non entra nell'Eseguibile: vale solo nell'editor.

## Amministratori che scelgono il valore

Un amministratore a volte deve vedere il risultato di un istituto preciso, per esempio `004_IS`, senza avere quel nome utente. Per permetterlo, nell'input spunta «Gli amministratori possono scegliere il valore» e scrivi l'etichetta del campo, per esempio «Istituto (solo amministratori)». Poi ripubblica l'Eseguibile.

- Chi ha il ruolo amministratore o superamministratore, avviando l'Eseguibile, vede in «Prima di avviare» un campo facoltativo con quell'etichetta. Il ruolo lo controlla il server, non la pagina.
- Se lo compila, il valore si usa così com'è: è già il valore finale (per esempio `004_IS`), la trasformazione non si applica. Si usano al massimo 200 caratteri; i caratteri di controllo e gli spazi all'inizio e alla fine si tolgono.
- Se lo lascia vuoto, vale la regola normale: il suo nome utente, con la trasformazione.
- Esecutori, utenti e visualizzatori non vedono il campo e non possono cambiare il valore: se provassero a inviarlo, la richiesta viene rifiutata. Lo stesso vale per tutti se l'opzione non è spuntata.
- Il valore scelto segue la stessa strada del valore del server: nel filtro di Estrai entra come dato, solo con «uguale» o «è uno di».
- La cronologia dell'esecuzione registra chi ha scelto il valore e quale valore ha usato.

Nell'editor, con l'opzione spuntata, puoi scrivere anche un «Valore di prova scelto»: Anteprima, Valida ed Esegui di prova usano quel valore al posto del nome utente di prova trasformato. Il nome utente di prova serve comunque. Il valore di prova non entra nell'Eseguibile.

> **Attenzione:** Togliere l'opzione e ripubblicare non chiede una conferma: la ripubblicazione lo elenca solo come informazione, perché da quel momento anche gli amministratori useranno il proprio nome utente.

## Il filtro negli Estrai a monte

Un Manipola che legge progetti Estrai (sorgente «Progetto») può lasciare il filtro negli Estrai: metti l'input «Utente che esegue» nell'Estrai, con la sua trasformazione, e pubblica come Eseguibile il Manipola. Il filtro si fa nel database di ogni Estrai, quindi è veloce.

- Quando qualcuno avvia l'Eseguibile, ogni Estrai a monte con «Utente che esegue» si riesegue solo per quella esecuzione, con lo stesso utente e con la trasformazione scritta nel SUO input. Anche attraverso altri Manipola (un Manipola che legge un Manipola che legge l'Estrai).
- Gli Estrai a monte senza «Utente che esegue» si leggono come sempre, dal loro risultato salvato.
- Il risultato rieseguito resta dentro quell'esecuzione: non diventa il risultato dell'Estrai, non cambia gli altri progetti che lo leggono e non avvia Carica.
- Il risultato salvato di un Estrai con «Utente che esegue» (di prima del filtro, con i dati di tutti) non si usa mai al posto di quello filtrato: Pianificazioni, progetti collegati, Carica, «Esegui» del progetto e i valori distinti si fermano con un messaggio che lo spiega.
- Nell'editor del Manipola «Esegui» diventa l'esecuzione di prova e l'Anteprima usa il nome utente di prova degli Estrai a monte: deve essere uno solo.
- Gli amministratori che possono scegliere il valore vedono in «Prima di avviare» un campo per ogni input a monte con l'opzione spuntata (con il nome del progetto a monte).
- Se dopo la pubblicazione qualcuno toglie «Utente che esegue» da un Estrai a monte, l'Eseguibile si ferma finché chi lo gestisce non lo ripubblica: senza conferma non vedrebbe i dati di tutti.
- Arricchisci non riesegue i progetti a monte per l'utente che esegue: se legge un Estrai con «Utente che esegue», si ferma. Usa un Manipola tra i due.

> **Attenzione:** Un Estrai nuovo con «Utente che esegue» deve aver avuto almeno un'esecuzione normale prima di aggiungere l'input: il Manipola legge da lì le sue colonne.

## Pianificazioni e progetti collegati

Una Pianificazione o un progetto avviato da un altro progetto parte senza una persona che la avvia: non c'è un utente, quindi un progetto con «Utente che esegue» si ferma con un messaggio che lo spiega. Non si usa il nome utente di prova. Per questi progetti usa l'Eseguibile.

## Errori comuni

- Il nome utente ha una forma diversa dai dati (per esempio `E0041` contro `004_IS`): senza una Formula che lo trasformi il Join non trova righe. Controlla con l'Anteprima e un nome utente di prova vero.
- Maiuscole e minuscole: `e0041` e `E0041` sono valori diversi per il Join. Il nome utente arriva esattamente come è scritto nell'elenco utenti; se i dati usano un'altra forma, trasformalo (per esempio con `.str.upper()` nella Formula).
- Un utente che vede l'Eseguibile ma non ha dati corrispondenti (per esempio un utente senza istituto o un amministratore) ottiene un risultato vuoto, non un errore: il filtro funziona, ma non ci sono righe per lui. Se qualcuno deve vedere tutto, serve un Eseguibile diverso.
- Nessun nome utente di prova: Anteprima, Valida ed Esegui di prova si fermano e chiedono di indicarlo.
- Trasformazione non valida (sintassi, nomi diversi da `utente`, o una formula che non usa `utente`): Valida e la pubblicazione la rifiutano e dicono perché.
- Due input «Utente che esegue» con nomi utente di prova diversi: usane uno solo.
- «Esegui» dall'editor fermo con un visualizzatore: l'esecuzione di prova è riservata a chi può modificare il progetto. Apri l'Eseguibile per eseguirlo con il tuo utente.
