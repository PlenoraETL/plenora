# Plenora E2E

Stack autocontenuto per i test end-to-end di plenora: dati di prova,
file di input multi-formato, infrastruttura docker e fake server REST.

## Stato documenti

La fonte corrente del catalogo e' `catalog/*.yaml`, renderizzata in
`test-e2e.md`. `test-e2e_LEGACY.md` e' storico pre-migrazione e non deve
essere aggiornato. `procedura.md` e' una nota operativa di bootstrap del
runner, non una checklist release.

## Layout

```
test-e2e/
├── sql/         # schema + seed + volume per i 4 dialetti supportati
├── files/       # 37 file fixture multi-formato (csv, xlsx, json, geo, ecc.)
├── fake-rest/   # FastAPI server che emula auth/data/pagination/errors/ArcGIS
└── docker/      # docker-compose master con 10 servizi (DB, cloud, file, core)
```

Ogni sottocartella ha un proprio `README.md` con il dettaglio.

Documenti operativi:

- [RUN.md](RUN.md): comandi rapidi per avviare lo stack e lanciare i test.
- [catalog/README.md](catalog/README.md): catalogo YAML e render del catalogo.
- [coverage/README.md](coverage/README.md): generazione della matrice di coverage.
- [coverage/matrix.md](coverage/matrix.md): matrice renderizzata corrente.
- [docker/README.md](docker/README.md): stack Docker dei servizi fixture.
- [fake-rest/README.md](fake-rest/README.md): server REST fake per sorgenti esterne.
- [runner/README.md](runner/README.md): harness esecutivo E2E.
- [sql/README.md](sql/README.md): schema e seed cross-dialect.
- [files/README.md](files/README.md): fixture file multi-formato.

## Avvio veloce

```fish
cd test-e2e/docker
cp .env.example .env
docker compose up -d                    # core: postgres + redis + fake-rest
docker compose --profile db up -d       # + mysql + sqlserver
docker compose --profile full up -d     # tutto, incluso Oracle (~4 GB)
docker compose ps                       # verifica (healthy)
```

## Coerenza dei dati

I tre dataset (`sql/`, `files/`, `fake-rest/fixtures/`) sono allineati
sugli stessi `id`:

- `customers` 1..12, `cities` 1..9, `products` 1..8, `orders` 1..15

Questo permette test del tipo "estrai dal DB, arricchisci via REST,
confronta con fixture file" senza disallineamenti.

I dataset SQL contengono in più: edge case (Unicode, apostrofi, geo
estremi, gerarchia profonda), righe NULL e 10000 righe di volume in
`events_log`. Vedi `sql/README.md` per la mappa completa.

## Cosa NON è (ancora) qui

- **Expected results machine-readable**: oggi `sql/expected/README.md`
  è in prosa. Servirebbero CSV/JSON deterministici per i diff
  automatici dei test.
- **Canvas templates plenora**: i test e2e devono ancora costruirsi
  i canvas a mano. Una libreria di canvas tipici (estrai SQL, join,
  arricchisci REST, carica file) sarebbe il prossimo step naturale.
- **Runner E2E**: nessuno script orchestra ancora "up + import + run
  canvas + diff + down".

## Note operative

- Le porte host sono spostate (`5433`, `3307`, `1434`, `1522`, ...) per
  non collidere coi servizi di sistema. Vedi `docker/README.md`.
- Tutte le credenziali sono fisse e checkate-in in `docker/.env.example`:
  questo stack è solo per test, non riusare le password altrove.
- I container sono `plenora-e2e-*` per non confonderli con il container
  storico `plenora-test-postgres` (che resta indipendente).
