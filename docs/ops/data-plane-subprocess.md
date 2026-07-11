# Isolamento memoria del data-plane

`pipeline.execute` puo' essere eseguito in un processo Python usa-e-getta.
Dramatiq continua a ricevere il messaggio da RabbitMQ e a gestire stato, ACK ed
errori, ma pandas, PyArrow, GDAL e i relativi allocator vengono caricati nel
processo figlio. Alla fine del task il processo termina e il sistema operativo
recupera tutta la sua memoria.

La funzione e' il comportamento predefinito:

```env
DATA_PLANE_EXECUTION_MODE=subprocess
DATA_PLANE_CHILD_TIMEOUT_SECONDS=3900
```

`inline` resta disponibile come rollback esplicito. Dopo una modifica della
configurazione occorre riavviare il worker:

```env
DATA_PLANE_EXECUTION_MODE=inline
```

## Piattaforme

La stessa implementazione usa il metodo Python `spawn` in tutti i casi:

| Installazione | Configurazione | Note |
|---|---|---|
| Windows senza Docker | variabili nel `backend/.env` | Il figlio e' un nuovo processo Python nativo. |
| Linux senza Docker | variabili nel file env del servizio systemd | Nessuna dipendenza da `fork` o da systemd. |
| Linux con Docker | variabili del servizio worker in Compose | Il figlio vive nello stesso container e ne rispetta il limite RAM. |
| Windows con Docker Desktop | variabili del servizio worker in Compose | Il worker gira nel container Linux; il comportamento coincide con Linux Docker. |

Non servono immagini, code RabbitMQ o comandi di avvio differenti. Il processo
Dramatiq deve restare attivo: e' l'agent che consuma la coda e avvia i processi
isolati quando arrivano i task. Worker su macchine diverse possono condividere
la stessa coda purche' usino la stessa versione applicativa, lo stesso broker,
PostgreSQL e l'object storage condiviso.

## Semantica operativa

- Il parent non conferma il messaggio finche' il child non ha restituito il
  risultato e il middleware non ha finalizzato lo stato.
- Un crash del child diventa un errore del task; il traceback completo resta
  nei log del worker e non viene esposto al client.
- La revoca cooperativa continua a usare i checkpoint PostgreSQL esistenti.
- Al timeout il parent termina il child, con fallback a kill.
- In questa prima fase solo `pipeline.execute` e' isolato. Preview, CARICA ed
  enrichment continuano a essere eseguiti inline.

## Rollout consigliato

1. Avviare un solo worker data-plane con un processo e un thread, lasciando il
   default `subprocess`.
2. Eseguire una pipeline rappresentativa e verificare risultato, revoca,
   timeout, RSS del container/host e log.
3. Aumentare le repliche dei worker per la concorrenza, mantenendo un task
   pesante per processo Dramatiq.
4. Se emerge un problema, ripristinare `inline` e riavviare il worker.

Il timeout del child deve essere almeno pari alla durata massima prevista per
la pipeline. Il limite RSS del container o della macchina deve includere sia il
piccolo processo Dramatiq sia il picco del singolo child.
