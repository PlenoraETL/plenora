# Tests2

Nuova suite di test del backend.

`tests2/` nasce come replacement progressivo dei test unit legacy presenti in `tests/`.

Obiettivo:

- costruire una suite piu' coerente
- ridurre il patching eccessivo
- testare contratti pubblici e invarianti reali
- evitare file tipo `coverage_part*`, `extra`, `final`

## Principi

Questa suite privilegia:

- comportamento osservabile
- contratti pubblici
- invarianti di sicurezza
- pure functions
- componenti con responsabilita' chiare

Questa suite evita:

- test scritti solo per coverage
- patch annidati su dettagli interni non stabili
- test che verificano l'implementazione invece del comportamento
- duplicazione dello stesso scenario in piu' file

## Policy lock/regression

I test di lock/regression devono bloccare il contratto desiderato, non
fotografare un bug temporaneamente presente.

Regola pratica:

- se il comportamento corrente e' corretto e va preservato, il test lo
  asserisce direttamente
- se il comportamento corrente e' sbagliato ma il fix non e' ancora
  schedulato, usare `@pytest.mark.xfail(strict=True, reason=\"bug noto\")`
  invece di scrivere un assert sul bug
- quando un bug viene corretto, il test deve essere riscritto sul
  comportamento voluto, non tenuto come testimonianza del passato

Questo mantiene il segnale della suite allineato agli invarianti del
backend e riduce il rischio di lock test che ostacolano i fix.

## Struttura

```text
tests2/
  conftest.py
  pytest.ini
  helpers/
  functional/
  integration/
  shared/
  arricchisci/
    unit/
  carica/
    unit/
  features/
    unit/
  intermediate/
    unit/
  manipola/
    unit/
  orchestrator/
    unit/
  scheduler/
    unit/
  security/
    unit/
```

## Regole di naming

- un file per componente o contratto
- nomi tipo `test_execution_manager.py`, `test_artifact_resolver.py`
- no `coverage_part1`, `part2`, `extra`, `final`

## Regole di design

Ogni test dovrebbe rispondere a una di queste domande:

- questo contratto pubblico e' corretto?
- questa invariante di sicurezza regge?
- questo edge case reale e' coperto?
- questo bug storico e' bloccato da una regression test?

## Strategia di migrazione

Ordine consigliato:

1. `scheduler/unit`
2. `arricchisci/unit`
3. `carica/unit`
4. `orchestrator/unit`
5. `intermediate/unit`
6. `features/unit`
7. `security/unit`
8. `manipola/unit`

Per ogni modulo:

1. identificare i contratti pubblici stabili
2. riscrivere i test essenziali in `tests2/`
3. mantenere temporaneamente i legacy in `tests/`
4. quando la copertura utile e' sufficiente, eliminare i vecchi unit omologhi

## Note operative

- `tests2/` e' pensata per test unit rapidi
- `tests2/functional/` e' il punto d'ingresso per i test con processi veri
- `tests2/integration/` ospita i test con DB reali e assert sui side effect persistiti
- gli integration test legacy in `tests/` possono restare dove sono finche' non c'e' un piano di migrazione separato
- a regime, `tests2/` potra' diventare la nuova `tests/`

## Stato attuale

Moduli gia' migrati in questa prima tranche:

- `scheduler/unit`
- `arricchisci/unit`
- `features/unit`
- `intermediate/unit`
- `carica/unit`
- `manipola/unit`
- `orchestrator/unit`
- `eseguibili/unit`
- `security/unit`
- `shared/`

Comando rapido:

```powershell
pytest tests2 -c tests2/pytest.ini
```
