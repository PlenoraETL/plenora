# SBOM — Software Bill of Materials

T-05 Phase 3 Batch D — baseline SBOM CycloneDX per il backend
PLENORA-ETL. Inventario versioni runtime + dev deps, riproducibile da
`requirements.txt` + `requirements-dev.txt`.

---

## §1 Cos'è

Lo **SBOM** è l'inventario formale delle dipendenze software di un
prodotto, in formato standard machine-readable. PLENORA-ETL pubblica
lo SBOM in [CycloneDX 1.x JSON](https://cyclonedx.org/specification/overview/),
formato richiesto da:

- ISO 27001 A.12.6.1 (technical vulnerabilities management) — vedi
  [`compliance/soa-iso-27001.md`](soa-iso-27001.md)
- Audit di compliance per clienti istituzionali (ICSC/RFI/ANIA)
- Pre-audit ACN QI1 (vedi `VALUTAZIONE_URBY_ETL.md` Prioritaria 2)

**Scope di questo SBOM**:

- backend Python (`backend/requirements.txt` + `backend/requirements-dev.txt`)
- file output: [`sbom.json`](sbom.json) committato in repo

**Out of scope** (per scelta consapevole, da rivalutare post-launch):

- frontend npm (`frontend/package-lock.json`) — eventuale `npm sbom`
  in batch successivo se richiesto da audit
- transitive lock file Python (al momento `requirements.txt` usa `>=`,
  non hash-pinned)

---

## §2 Come rigenerare

Comando singolo:

```bash
python backend/scripts/generate_sbom.py
```

Lo script:

1. legge `backend/requirements.txt` + `backend/requirements-dev.txt`
2. invoca `pip-audit --format=cyclonedx-json --requirement <file>`
3. risolve le dipendenze in un venv temporaneo isolato (NON il
   sito-packages corrente)
4. scrive l'output in `docs/compliance/sbom.json`

Prerequisito: `pip-audit>=2.7` installato (è in `requirements-dev.txt`):

```bash
pip install -r backend/requirements-dev.txt
```

Exit code `0` = nessuna CVE; exit code `1` = CVE trovate, SBOM comunque
generato (vedi §3 per il triage). Qualunque altro exit code è un errore
hard, indaga prima di rigenerare.

### Quando rigenerare

- post-merge di un update major su `requirements.txt` o `requirements-dev.txt`
- prima di un audit esterno o invio cliente
- manualmente trimestrale come refresh routine
- ad-hoc se un advisory pubblico (es. CVE alta su una nostra dep)
  arriva tra un refresh e l'altro

---

## §3 Come triare un CVE finding

`pip-audit` usa la [Python Packaging Advisory Database](https://github.com/pypa/advisory-database)
+ OSV. Quando segnala una vulnerabilità sui package del nostro stack:

1. **Leggi la severity dell'advisory**:
   - **CRITICAL** (CVSS ≥ 9.0): bloccante. Aggiorna la dep prima del
     prossimo deploy. Se non c'è patch upstream, valuta workaround
     (config, mitigazione applicativa) o pinning a versione safe.
   - **HIGH** (7.0-8.9): pianifica fix entro la sprint corrente. Se
     l'advisory è sfruttabile solo in scenari fuori dal nostro use-case
     (vedi `attack vector` nell'advisory), documenta esplicitamente la
     decisione di non aggiornare.
   - **MEDIUM** (4.0-6.9): triage allo sprint planning successivo.
   - **LOW** (< 4.0): annota in TRIAGE come debt, fix opportunistic.
2. **Verifica applicabilità**: l'advisory descrive sempre l'attack
   vector. Se richiede privilegio admin remoto su un endpoint che noi
   non esponiamo, la severity effettiva per noi è ridotta. Documenta la
   valutazione nel commit messaggio del fix (o nella sua assenza).
3. **Aggiorna la dep**: bump minimum version in `requirements.txt`,
   rigenera SBOM, ri-esegui i test (la suite Phase 3 cattura le
   regressioni interne).
4. **Se non c'è fix upstream disponibile**: apri ticket nel TRIAGE,
   marca P1, considera workaround o downgrade temporaneo.

**Soglia di alert pre-go-live**: zero CRITICAL aperte, zero HIGH
sfruttabili nel nostro attack surface. MEDIUM/LOW accettabili con
tracking esplicito.

Riferimenti utili:

- [pip-audit docs](https://pypi.org/project/pip-audit/)
- [CycloneDX spec](https://cyclonedx.org/specification/overview/)
- [Python Packaging Advisory Database](https://github.com/pypa/advisory-database)
- [OSV.dev](https://osv.dev/) — DB unificato CVE per ecosistemi diversi

---

## §4 Cadenza e automazione

**Stato attuale (T-05 Phase 3 Batch D)**: refresh **manuale**.

- Niente workflow GitHub Actions scheduled
- Niente PR automatica con `sbom.json` aggiornato
- Niente gate CI bloccante sui PR per CVE finding

Decisione consapevole pre-go-live: lo SBOM è una baseline di
controllo, non un gate sul flusso di sviluppo. L'automazione blocking
introdurrebbe falsi positivi su CVE transient (advisory revocate,
finestre di patch upstream) che bloccherebbero merge legittimi senza
beneficio proporzionato.

**Quando promuovere ad automation**:

- post-launch, dopo aver osservato la cadenza reale di CVE su questo
  stack per ~3 mesi
- in coordinamento con `dependabot.yml` (già attivo, weekly): quando
  Dependabot apre PR di update, la CI può rigenerare SBOM nello stesso
  ciclo
- target finale: workflow scheduled weekly che apre PR di refresh +
  step CI non-blocking che warna su nuove CRITICAL/HIGH

Tracciamento: l'evoluzione verso SBOM automation è P3 nel TRIAGE
Phase 3 (vedi `orchestrate/phase_3_audit/TRIAGE.md`), promuovibile a P2
post-launch se l'audit cliente lo richiede esplicitamente.

---

## §5 Riferimenti

- [`generate_sbom.py`](../../backend/scripts/generate_sbom.py) — script di
  generazione
- [`sbom.json`](sbom.json) — baseline corrente
- [`requirements.txt`](../../backend/requirements.txt) — runtime deps
- [`requirements-dev.txt`](../../backend/requirements-dev.txt) — dev/test deps
- [`dependabot.yml`](../../backend/.github/dependabot.yml) — update PR
  weekly (tracking complementare allo SBOM)
- [`soa-iso-27001.md`](soa-iso-27001.md) — Statement of Applicability
  ISO 27001 (controllo A.12.6.1 cita SBOM come prerequisito)
