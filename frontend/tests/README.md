# Frontend Tests

Suite frontend 1.0 basata su Vitest e Testing Library.

## Esecuzione

```powershell
cd frontend
npm test
```

File singolo:

```powershell
npx vitest run tests/api/openapiContract.contract.test.js
```

## Struttura

```text
tests/
  api/         contratti del layer src/api
  components/ contratti componenti e modali
  hooks/      hook con logica condivisa
  pages/      pagine e workflow principali
  shared/     helper e guard della suite
  setup.js    setup Vitest/Testing Library
```

## Scopo

I test frontend bloccano:

- drift fra frontend e backend;
- payload persi in create/edit/reload;
- trasformazioni errate negli adapter page-level;
- regressioni nei moduli `src/api/*`;
- stati UI critici dei workflow principali.

## Regole

- Preferire contract test stretti sui payload.
- Usare mock HTTP espliciti e leggibili.
- Non rendere permissivi gli assert per far passare una regressione.
- Quando cambia un endpoint backend, aggiornare prima il manifest/contratto e poi il test frontend.
