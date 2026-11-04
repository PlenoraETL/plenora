# Piano Blocco A: colonne, validazione e rimandati dei canvas

**Data:** 04/11/2026. **Decisione dell'utente:** perimetro completo, prima della pubblicazione finale dei canvas.
**Raccolte:**
- `docs/audits/blocco-A-raccolta-backend-2026-11-04.md` (Codex);
- `docs/audits/blocco-A-raccolta-frontend-2026-11-04.md`.

Le sigle B1.x, B2.x e B3.x rimandano alle classi della raccolta backend; F1.x e F2.x a quella frontend.

## Regole della correzione unica

1. **L'esecuzione è la verità sulle colonne.** Il motore di esecuzione non cambia comportamento: i risultati di oggi restano identici. Sono le previsioni (schemi, validatore, griglie) a doversi allineare all'esecuzione.
   - **Test di parità per costruzione.** Per **ogni** operazione del registry e per ogni tipo di nodo, una tabella di casi (configurazione e DataFrame di prova, compresi doppioni, collisioni, alias, nascoste e frame vuoti):
     1. esegue la funzione vera dell'esecuzione;
     2. confronta nomi, ordine e famiglia di tipo con l'inferenza.
   - Un'operazione nuova senza casi fa fallire il test.
   - Se l'uscita dipende dai **valori** (pivot senza mapping, split senza nomi, transpose, flatten senza lista), l'inferenza dichiara `indeterminate`, mai `certain`.
2. **Identità delle colonne.** Ogni colonna prevista ha `{id, name, type, source_name}`. L'Output riceve in esecuzione la mappa id→nome del **proprio** ingresso. Le personalizzazioni (alias, visibile, ordine) seguono l'id, non il nome.
3. **Valida esegue la stessa preparazione dell'esecuzione.** Ogni operazione espone la sua preparazione: normalizzazione dei parametri, risoluzione degli id, compilazione delle espressioni, piano colonne e vincoli.
   - La preparazione la usano sia l'esecuzione sia il validatore.
   - Il manifest degli input runtime si compila già in Valida.
   - Esiti distinti:
     - configurazione verificata;
     - verificato con gli input e i dati;
     - non garantibile (rete, database, dimensioni).
   - Test di parità: ogni caso che l'esecuzione rifiuta, Valida lo rifiuta con lo stesso motivo.
4. **Una sola fonte di colonne nel frontend.** Una mappa per editor, ricalcolata dal backend sul canvas dell'editor da un'unica sottoscrizione allo store, e un selettore `columnsOf(nodeId, lato?)`.
   - Le colonne derivate escono dai nodi e dal salvataggio. Le sorgenti tengono solo la loro configurazione (visibile, alias).
   - Test di enumerazione:
     - nessun file scrive colonne derivate;
     - ogni tipo di nodo di ogni editor ha un gestore nel backend;
     - i lettori ricevono colonne con id.
5. **Prima di avviare, una sola preparazione.** Per tutti gli editor c'è un'unica finestra «Prima di avviare»: input, stima e conferma.
   - La stima di Arricchisci usa la stessa preparazione dell'esecuzione, con gli input, ed è legata a un'impronta verificata all'avvio.
6. **Il guscio dell'editor possiede intestazione, chiusura ed eliminazione** dei pannelli. Un solo «Elimina passo», con conferma. Titolo e icona vengono dai descrittori.
7. **Titoli.** `data.title` contiene solo testo dell'utente; il titolo derivato è il segnaposto.
8. **Copia di sicurezza per scheda e salvataggio condizionato alla versione.**
   - Il PUT del canvas rifiuta un salvataggio basato su una versione superata, e l'utente decide come procedere.
   - Tabella delle transizioni, con test in due schede.
9. **Azioni dell'editor dichiarate.** Una tabella EDITOR_ACTIONS (nome, chi registra, chi consuma); un nome senza consumatore fa fallire il test. Il codice morto esce insieme ai suoi hook, modali, API e test.
10. **Glossario unico** dei nomi visibili. Quali anglicismi restano lo decide l'utente.

## Fasi

| Fase | Chi | Contenuto | Dipende da |
|---|---|---|---|
| A1 | Codex | Test di parità esecuzione/inferenza su ogni operazione Manipola. Correzione degli handler (B1.1-33, B1.39-41). `indeterminate` per i casi dipendenti dai dati. Handler per multi_join, staticValues e datasource_node. Sorgenti con alias e visibile come l'esecuzione. Mappa id dell'Output in esecuzione (B1.11-12) | — |
| A2 | Codex | Schema di Estrai dalla stessa proiezione SQL (B1.34-35). Schema di Arricchisci dal mapping effettivo (B1.36-38). Identità in output_contract (B1.40). `status` per nodo nella risposta di `/schemas` | A1 |
| A3 | Codex | Preparazione condivisa e Valida = esecuzione (B2.1-21). Manifest in Valida. Preparazione unica con input per Arricchisci (B3). Endpoint dei valori distinti per il Lookup (F2.c). PUT del canvas con versione | A1 |
| F1 | Claude | Frontend indipendente: intestazione ed eliminazione del guscio (F2.e), titoli (F2.d), EDITOR_ACTIONS e codice morto (F2.f), Join di Estrai nel pannello (F2.b) | — |
| F2 | Claude | Fonte unica delle colonne nei tre editor (F1.1-17), id fino all'Output, niente colonne derivate salvate | A1, A2 |
| F3 | Claude | Finestra «Prima di avviare» unica (F2.a), «Riempi con i valori presenti» (F2.c), copia di sicurezza per scheda e conflitti di versione (F2.h), esiti di Valida | A3 |
| G | Claude | Glossario (F2.g) | decisione dell'utente |

Ogni fase passa per correzione, verifica, **un** giro di conferma incrociato (Codex rilegge Claude e viceversa) e commit. Collaudo dal vivo e pubblicazione finale alla fine.
