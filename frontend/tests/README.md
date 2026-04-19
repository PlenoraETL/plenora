# Frontend Tests

Suite frontend basata su `vitest` + `@testing-library`.

## Run

Esegui tutta la suite frontend con:

```bash
npm run test:frontend
```

Per lanciare un file singolo:

```bash
npx vitest run tests/pages/ApiConnectionManager.contract.test.jsx
```

## Struttura

- `tests/pages/`
  Contract test delle pagine ad alto ROI.
- `tests/components/`
  Contract test dei componenti/modali con logica di payload.
- `tests/api/`
  Contract test del layer `src/api/*`.
- `tests/setup.js`
  Setup condiviso `vitest` / `testing-library`.
- `tests/helpers/`
  Helper condivisi per mock HTTP, contesto e `reactflow`.
- `tests/shared/`
  Meta-test della suite frontend.

## Cosa copre oggi

- `ApiConnectionManager`
  - `rest_file`
  - `template`
  - `datasource`
  - `OpenAPI import`
  - `executable`
- `ArricchisciEditor`
- `ManipolaEditor`
- `EstraiEditor`
- `ScheduleList`
- `ProjectList`
- `FileManager`
- `ConnectionManager`
- `UserManager`
- `ScheduleWizard`
- `ExecutableConfigModal`
- `EseguibileExecuteWizard`
  - happy path completo
  - loader error
  - upload/analyze error
- `ImdfBundlesManagerModal`
- `ResultNode`
- `ImdfPublisherNode`
- moduli API:
  - `projects`
  - `schedules`
  - `imdf`
  - `tasks`
  - `files`
  - `features`
  - `enrichment`
  - `datasources`
  - `users`

## Gap attuali / prossimi target

- canvas editors:
  - `ArricchisciEditor` (base contract solo su route guard + save wiring)
  - `ManipolaEditor` (base contract solo su route guard + save wiring)
  - `EstraiEditor` (base contract solo su route guard + save wiring)
- pagine manager:
  - `FileManager` (manca upload/edit flow)
  - `ConnectionManager` (manca ArcGIS branch)
  - `UserManager` (manca groups/delete/reset flow)
- componenti IMDF:
  - `ResultNode` (coperto soprattutto sul path IMDF; restano branch non-IMDF più fini)
  - `ImdfPublisherNode` (coperto su validate/publish async; restano branch error/busy)
  - `ImdfBundlesManagerModal` (coperto; resta solo eventuale branch busy/error)
- moduli API ancora non coperti o coperti solo parzialmente:
  - `connections`
  - `carica`
  - `apiConnections`
  - `transforms`
  - `intermediate` (se/quando presente nel frontend)

## Obiettivo

Questi test non sono smoke generici. Servono a bloccare:

- drift frontend-backend dei payload
- campi persi nei flow `create/edit/reload`
- adapter page-level che trasformano male i dati dei modal
- regressioni sui moduli `src/api/*`

## Regole pratiche

- preferire contract test stretti sui payload e sui branch ad alto ROI
- non rendere i test permissivi per “farli passare”
- se emerge un mismatch reale, correggere il codice prima del test
