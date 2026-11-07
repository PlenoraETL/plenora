# UI — collaudo dal vivo dopo il deploy di 42563405 (07/11/2026)

Server 172.16.28.6, utente admin. Progetti temporanei: `TEST_collaudo_UI` (Manipola) e
`TEST_collaudo_UI_estrai` (Estrai), più il file `collaudo_ui.csv`. Si cancellano a fine collaudo.

## Esito positivo

- **Deploy**: `/ready` verde su tutti i controlli; il fuso arriva dal beat; CSP attiva; font
  precaricati.
- **Cache tra pagine**: tornando su una pagina, 0 chiamate (prima la dashboard ne faceva 11).
- **Riapertura degli editor**: Estrai da 2,1 a 0,22 s (niente catalogo tabelle da 96 kB); Manipola
  (61 nodi) da 1,9 a 0,43 s (niente `/schemas`).
- **U9**: le sorgenti mostrano il nome del progetto.
- **U10**: «Crea progetto» con Invio apre l'editor e mostra il messaggio.
- **Sorgenti**: si aggiungono dalla barra con un clic e compaiono nella palette.
- **Filtri E/O** con valori mancanti, su dati veri (immobili attivi, 8.731 righe nel campione, nessuna
  violazione). Nodo riassunto come «(A) o (B)».
- **Comandi del canvas**: «Salva e mostra i dati», Ctrl+D, Ctrl+C/V, Ctrl+Z, Ctrl+F e «C» per
  collegare funzionano.
- **Caricamento dall'editor**: il file viene caricato e aggiunto al canvas.
- **U16**: un'esecuzione fallita indica il passo, con «Vai al passo» (funziona), «Apri il registro» e
  «Segnala».
- **Guardia sui progetti a monte**: si ferma senza ricalcolare.
- **Esecuzione riuscita**: 238.044 righe, con lo scaricamento.
- **Self-join in Estrai**: alias `ac_2`; SQL `LEFT JOIN afm.ac ac_2 ON ac.company = ac_2.company`;
  colonne `_2` uguali tra la previsione e l'esecuzione.

## Difetti (L = collaudo live)

- **L1 (M)** `GET /projects` più lento: 320–380 ms contro 126–161 ms prima del deploy (connessioni
  invariate a 55 ms). Causa probabile: `list_manipola_source_refs` con `canvas::text` e poi
  `::jsonb` su colonne jsonb da decine di kB.
- **L2 (M)** La guida a canvas vuoto (U11) non compare sui progetti nuovi, né in Manipola né in Estrai.
  Probabile: `useCanvasDerived(emptyLoadedCanvas, {on:'structure'})` non si ricalcola quando
  `graphLoaded` passa a true con zero nodi.
- **L3 (M, da verificare se preesistente)** Un ramo che non arriva all'Output viene eseguito comunque
  e fa fallire l'esecuzione, mentre il pannello dice «non arriva all'Output».
- **L4 (B-M)** Messaggi tecnici dal backend, fuori dal glossario:
  - «Canvas contiene nodi orfani (nessun edge incoming): transform_…», con l'id e senza «Vai al
    passo»;
  - «Esecuzione pipeline...»;
  - «intermediate_mode (staff)» in «Prima di avviare»;
  - «Salvato 238044 righe», con il numero non formattato.
- **L5 (B-M)** Eliminare una sorgente lascia orfano il passo successivo e il salvataggio si blocca; la
  conferma non lo dice.
- **L6 (B)** Ricerca nel canvas (Ctrl+F) e scelta «Collega a»: i nodi dello stesso tipo appaiono
  identici, senza il riassunto (condizione o alias). Il titolo del selettore è in maiuscolo («AC_2»).
