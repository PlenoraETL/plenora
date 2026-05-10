# Plenora E2E Fake REST Server

Single-process FastAPI app that emulates every external REST pattern plenora
must handle in production:

- auth schemes (api_key in query/header, Bearer, Basic, OAuth2 client
  credentials, ArcGIS `generateToken`)
- response shapes (root array, object envelope, deeply nested, nulls,
  empty, single object)
- pagination (offset, page+size, opaque cursor, GitHub-style `Link` header,
  no-meta)
- error modes (4xx/5xx, malformed JSON, wrong content-type, empty body,
  redirect loop)
- rate limiting (strict window, per-second burst)
- GeoJSON (Point, mixed types, null/missing geometry)
- file download / upload (CSV, JSON, ZIP-shapefile)
- ArcGIS-style endpoints (`/sharing/rest/...` + `/services/.../FeatureServer/...`)
- SharePoint REST + Azure AD AADSTS error paths (`/sharepoint/...`)
- slow / partial responses (delay, partial body, timeout simulator)

Static fixtures (`fixtures/customers.json`, `fixtures/cities.json`,
`fixtures/products.json`, `fixtures/cities.geojson`) match the SQL seed in
`test-e2e/sql/postgres/01_seed.sql` (base ids 1..N; edge ids are not
included).

## Running

```bash
# build & start (port 8080 on the host)
docker compose up -d --build

# stop
docker compose down

# tail logs
docker compose logs -f plenora-fake-rest

# manual smoke test
curl http://localhost:8080/health
# -> {"status":"ok"}
```

The interactive OpenAPI UI is available at <http://localhost:8080/docs>.

## Test credentials

| Scheme | Where | Value |
| --- | --- | --- |
| API key | `?api_key=` or `X-API-Key` header | `PLENORA_TEST_KEY` |
| Bearer | `Authorization: Bearer ...` | `PLENORA_TEST_BEARER` |
| Basic | `tester` / `tester_pass` | realm `fake` |
| OAuth2 | `client_id` / `client_secret` | `plenora` / `plenora_secret` |
| OAuth2 token | issued by `/auth/oauth2/token` | `OAUTH_FAKE_TOKEN` |
| ArcGIS user | `/auth/arcgis/generateToken` | `tester` / `tester_pass` |
| ArcGIS token | `?token=` or `Authorization: Bearer ...` | `ARCGIS_FAKE_TOKEN` |
| ArcGIS OAuth2 | `/sharing/rest/oauth2/token` (form `grant_type=client_credentials`) | `plenora` / `plenora_secret` |
| ArcGIS OAuth2 token | `?token=` or `Authorization: Bearer ...` | `ARCGIS_OAUTH_TOKEN` |
| SharePoint AAD client | `/sharepoint/login.microsoftonline.com/{tenant}/oauth2/v2.0/token` | `plenora_sp` / `plenora_sp_secret` |
| SharePoint token | `Authorization: Bearer ...` or `?access_token=` | `SHAREPOINT_FAKE_TOKEN` |

## Endpoint map

### `/health`
- `GET /health` – liveness probe.

### `/auth/*` (`routes/auth.py`)
- `GET  /auth/api_key_query?api_key=PLENORA_TEST_KEY`
- `GET  /auth/api_key_header` (`X-API-Key: PLENORA_TEST_KEY`)
- `GET  /auth/bearer` (`Authorization: Bearer PLENORA_TEST_BEARER`)
- `GET  /auth/basic` (Basic `tester:tester_pass`)
- `POST /auth/oauth2/token` – supports two grant types:
  - `grant_type=client_credentials&client_id=plenora&client_secret=plenora_secret`
  - `grant_type=password&username=tester&password=tester_pass&client_id=plenora&client_secret=plenora_secret`
    - bad `username`/`password` -> `401 {"error": "invalid_grant", "error_description": "Bad credentials"}`
    - missing `username` or `password` -> `400 {"error": "invalid_request"}`
  - both flows return `{"access_token": "OAUTH_FAKE_TOKEN", "token_type": "bearer", "expires_in": 3600}`
- `GET  /auth/oauth2/protected` (`Authorization: Bearer OAUTH_FAKE_TOKEN`)
- `POST /auth/arcgis/generateToken` (form: `username`, `password`, `client=referer`, `referer`)
  - bad credentials -> HTTP 200 with body `{"error": {"code": 400, "message": "Invalid credentials"}}` (ArcGIS pattern).

### `/data/*` (`routes/data.py`)
All require `?api_key=PLENORA_TEST_KEY` or `X-API-Key: PLENORA_TEST_KEY`.

- `GET /data/object_root` – `{"data": [...12 customers], "count": 12, "fetched_at": "2026-01-01T00:00:00Z"}`
- `GET /data/array_root` – root JSON array
- `GET /data/nested_deep` – `{"response": {"items": {"records": [...], "meta": {"total": 12}}}}`
- `GET /data/with_nulls` – 5 customers, sparse nulls
- `GET /data/empty_array` – `{"data": []}`
- `GET /data/single_object` – customer id=1 as root object
- `GET /data/by_id/{id}` – 404 if id not in 1..12

#### Write sink (`POST /data/sink/*`)

Emulates the REST write sink used by `app/core/carica/rest_write_boundary.py`
and `app/core/features/rest_sink.py`. All sink endpoints require
`X-API-Key: PLENORA_TEST_KEY`. State (records, version counters, batch
idempotency map) is in-memory **per-process**: it is shared across requests
that hit the same uvicorn worker but is lost on container restart. To clear
the state from a test, send `X-Test-Reset: 1` on any sink call (the call
itself executes normally and the response also carries `"ok": true`).

- `POST   /data/sink/records` – body `{"records": [{...}, ...]}`. Returns
  `201 {"created": N, "ids": [...]}` with sequential ids starting at
  `1000`.
- `PUT    /data/sink/by_id/{id}` – full replace. Returns
  `200 {"updated": id, "version": <n>}`. 404 if `id` is unknown.
- `PATCH  /data/sink/by_id/{id}` – partial update. Returns the merged body
  with the bumped `version`. 404 if `id` is unknown.
- `DELETE /data/sink/by_id/{id}` – `204 No Content` on success, `404` if
  the id is unknown.
- `POST   /data/sink/batch` – body
  `{"batch_records_key": "<uuid>", "records": [...]}`. Returns
  `202 {"batch_id": "<key>", "queued": N}`. Idempotent: a replay with the
  same `batch_records_key` returns the original response unchanged.

### `/page/*` (`routes/pagination.py`)
- `GET /page/offset?offset=0&limit=5`
- `GET /page/page_size?page=1&size=5`
- `GET /page/cursor?cursor=...&page_size=5` (cursor is base64 of offset; invalid -> 400)
- `GET /page/link_header?page=1&size=5` (sets `Link: <...>; rel="next", <...>; rel="last"`)
- `GET /page/no_meta?page=1&size=5` (array only; client deduces end)

### `/error/*` (`routes/errors.py`)
- `GET /error/401`, `403`, `404`, `422`, `429` (`Retry-After: 5`), `500`, `503` (`Retry-After: 10`)
- `GET /error/malformed_json` – truncated JSON, `Content-Type: application/json`
- `GET /error/wrong_content_type` – valid JSON body, `Content-Type: text/plain`
- `GET /error/empty_body` – HTTP 200 with empty body
- `GET /error/redirect_loop` – 302 -> self up to 5 hops (`?hop=N`), then 200

### `/throttle/*` (`routes/rate_limit.py`)
- `GET  /throttle/strict?limit=10` – classic rate limit; returns `X-RateLimit-{Limit,Remaining,Reset}`. 429 + `Retry-After: 1` when exceeded; window resets after 1 s.
- `GET  /throttle/burst?per_second=2` – per-second burst bucket.
- `POST /throttle/reset` – clears state. Requires header `X-Test-Reset: 1`.

State is in-process (per-IP/per-endpoint) and is lost on container restart.

### `/geo/*` (`routes/geo.py`)
- `GET /geo/cities` – FeatureCollection of 9 Point features
- `GET /geo/cities/by_region/{region_id}` – filter by region id (1, 2, 3)
- `GET /geo/empty` – empty FeatureCollection
- `GET /geo/null_geometry` – feature with `"geometry": null`
- `GET /geo/mixed` – Point + LineString + Polygon
- `GET /geo/invalid` – feature missing the `geometry` key (intentional bad data)

### `/file/*` (`routes/files.py`)
All `GET` endpoints accept `?api_key=PLENORA_TEST_KEY`.

- `GET  /file/csv/customers` – `text/csv; charset=utf-8`, `attachment; filename="customers.csv"`
- `GET  /file/csv/cities` – CSV with `latitude` / `longitude` columns
- `GET  /file/json/customers` – JSON file download
- `GET  /file/zip/shapefile` – ZIP containing a real shapefile when `pyshp` is installed (it is, in the docker image); otherwise a placeholder ZIP with a single `README.txt`
- `POST /file/upload` – multipart `file=@...`; returns `{"filename", "size", "content_type"}`

### ArcGIS routes (`routes/arcgis.py`)
All require `?token=ARCGIS_FAKE_TOKEN` or `Authorization: Bearer ARCGIS_FAKE_TOKEN`.
Missing/invalid token returns HTTP 200 with `{"error": {"code": 498, "message": "Invalid token"}}` (ArcGIS pattern).

- `GET  /sharing/rest/portals/self`
- `GET  /sharing/rest/content/users/tester`
- `GET  /sharing/rest/content/users/tester/items/{item_id}`
- `POST /sharing/rest/content/users/tester/createService`
- `GET  /services/{service_name}/FeatureServer`
- `GET  /services/{service_name}/FeatureServer/0`
- `GET  /services/{service_name}/FeatureServer/0/query?where=1=1&outFields=*&f=json&returnCountOnly=false`
- `POST /services/{service_name}/FeatureServer/0/applyEdits` (form: `adds`, `updates`, `deletes` as JSON strings)

`applyEdits` mutates per-service in-memory state. Send `X-Test-Reset: 1` on
any ArcGIS call to clear it (e.g. between test cases).

#### ArcGIS extra (OAuth2, folder, name check, catalog)

The endpoints below complete the slice consumed by plenora's ArcGIS
publisher. They accept either `?token=ARCGIS_FAKE_TOKEN`,
`?token=ARCGIS_OAUTH_TOKEN`, or `Authorization: Bearer <one of the above>`.
Missing / invalid token follows the ArcGIS pattern: HTTP 200 with
`{"error": {"code": 498, "message": "Invalid token"}}`.

- `POST /sharing/rest/oauth2/token` – ArcGIS OAuth2 `client_credentials`.
  Body form `grant_type=client_credentials&client_id=plenora&client_secret=plenora_secret`.
  Success: `{"access_token": "ARCGIS_OAUTH_TOKEN", "expires_in": 3600, "token_type": "Bearer"}`.
  Wrong credentials: HTTP 200 with
  `{"error": {"code": 400, "message": "Invalid client credentials"}}`.
- `POST /sharing/rest/content/users/tester/createFolder?token=...` –
  body form-encoded or JSON `{"title": "<name>", "f": "json"}`. Success:
  `{"success": true, "folder": {"username": "tester", "id": "<deterministic_id>", "title": "<name>"}}`.
  Missing title: HTTP 200 with
  `{"error": {"code": 400, "message": "Missing title"}}`.
- `GET  /sharing/rest/portals/self/isServiceNameAvailable?name=...&type=FeatureServer&token=...` –
  Returns `{"available": false, "name": "occupied"}` for `name=occupied`,
  otherwise `{"available": true, "name": "<name>"}`.
- `GET  /rest/services?token=...` – Catalog root:
  `{"currentVersion": 11.2, "folders": ["test_folder"], "services": [{"name": "test_folder/Test_FS", "type": "FeatureServer"}]}`.

### SharePoint routes (`routes/sharepoint.py`)

Emulates the slice of SharePoint Online + Azure AD that plenora's SharePoint
connector (`app/core/datasources/sharepoint.py`, built on
`office365-rest-python-client` + `msal`) consumes.

All "normal" SharePoint endpoints require either
`Authorization: Bearer SHAREPOINT_FAKE_TOKEN` or `?access_token=SHAREPOINT_FAKE_TOKEN`.
Missing / wrong token returns `401 {"error": "invalid_token"}`.

Token endpoints (AAD-style):

- `POST /sharepoint/login.microsoftonline.com/{tenant}/oauth2/v2.0/token` –
  `grant_type=client_credentials&client_id=plenora_sp&client_secret=plenora_sp_secret&scope=https://fake.sharepoint.com/.default`.
  Success: `{"access_token": "SHAREPOINT_FAKE_TOKEN", "expires_in": 3600, "token_type": "Bearer"}`.
  Wrong client credentials: `401` with an AADSTS-shaped body (code `900023`).
- `POST /sharepoint/login.microsoftonline.com/error/{aadsts_code}` – force-return
  a `401` AADSTS response. Body shape:
  ```json
  {
    "error": "invalid_grant",
    "error_description": "AADSTS{code}: <message> Trace ID: ... Correlation ID: ... Timestamp: ...",
    "error_codes": [<code>],
    "timestamp": "...",
    "trace_id": "...",
    "correlation_id": "..."
  }
  ```
  Supported codes: `50034` (user not found), `50053` (account locked),
  `900023` (invalid client / tenant), `50126` (invalid credentials),
  `700016` (unauthorized client / app not in directory). Unlisted codes get
  a generic `invalid_grant` payload.

SharePoint REST descriptor + file endpoints:

- `GET  /sharepoint/_api/web` – descriptor: `{"d": {"Title": "Plenora Test Site", "Url": "http://fake/sharepoint"}}`.
- `GET  /sharepoint/_api/web/lists/getbytitle('{title}')/items` – list items
  envelope `{"d": {"results": [{"Id", "FileLeafRef", "Length", "TimeLastModified"}, ...]}}`.
- `GET  /sharepoint/_api/web/getfolderbyserverrelativeurl('{path}')/files` –
  file listing under a folder.
- `GET  /sharepoint/_api/web/getfilebyserverrelativeurl('{path}')/$value` –
  binary download. Routes by extension: `.json` -> customers JSON,
  `.geojson` -> cities GeoJSON, anything else -> CSV built from the
  customers fixture.

Resilience helpers:

- `GET  /sharepoint/throttle` – `429 {"error": "throttled"}` with
  `Retry-After: 5`.
- `GET  /sharepoint/transient_5xx` – per-process counter: 1st call 503, 2nd
  call 502, 3rd+ call `200 {"ok": true}`. Reset with `X-Test-Reset: 1`.

### `/slow/*` (`routes/slow.py`)
- `GET /slow/delay/{seconds}` – sleeps N seconds (max 30) then returns `{"slept": N}`
- `GET /slow/partial_body` – streams half a JSON body, then aborts mid-stream
- `GET /slow/timeout_simulator?ms=3000` – sleeps `ms` milliseconds (max 30000)

## Curl quick reference

```bash
# auth: api_key in query
curl 'http://localhost:8080/auth/api_key_query?api_key=PLENORA_TEST_KEY'

# auth: api_key in header
curl -H 'X-API-Key: PLENORA_TEST_KEY' http://localhost:8080/auth/api_key_header

# auth: bearer
curl -H 'Authorization: Bearer PLENORA_TEST_BEARER' http://localhost:8080/auth/bearer

# auth: basic
curl -u tester:tester_pass http://localhost:8080/auth/basic

# auth: oauth2 token (client_credentials)
curl -X POST http://localhost:8080/auth/oauth2/token \
     -d grant_type=client_credentials \
     -d client_id=plenora \
     -d client_secret=plenora_secret

# auth: oauth2 token (password grant)
curl -X POST http://localhost:8080/auth/oauth2/token \
     -d grant_type=password \
     -d username=tester \
     -d password=tester_pass \
     -d client_id=plenora \
     -d client_secret=plenora_secret

# auth: arcgis token
curl -X POST http://localhost:8080/auth/arcgis/generateToken \
     -d username=tester \
     -d password=tester_pass \
     -d client=referer \
     -d referer=http://localhost

# data
curl -H 'X-API-Key: PLENORA_TEST_KEY' http://localhost:8080/data/object_root

# write sink: bulk create
curl -X POST -H 'X-API-Key: PLENORA_TEST_KEY' -H 'Content-Type: application/json' \
     -d '{"records":[{"name":"alice"},{"name":"bob"}]}' \
     http://localhost:8080/data/sink/records

# write sink: replace
curl -X PUT  -H 'X-API-Key: PLENORA_TEST_KEY' -H 'Content-Type: application/json' \
     -d '{"name":"alice2"}' \
     http://localhost:8080/data/sink/by_id/1000

# write sink: partial update
curl -X PATCH -H 'X-API-Key: PLENORA_TEST_KEY' -H 'Content-Type: application/json' \
     -d '{"age":42}' \
     http://localhost:8080/data/sink/by_id/1000

# write sink: delete (204) -- replay -> 404
curl -X DELETE -H 'X-API-Key: PLENORA_TEST_KEY' \
     http://localhost:8080/data/sink/by_id/1000

# write sink: idempotent batch
curl -X POST -H 'X-API-Key: PLENORA_TEST_KEY' -H 'Content-Type: application/json' \
     -d '{"batch_records_key":"abc-123","records":[{"x":1},{"x":2}]}' \
     http://localhost:8080/data/sink/batch

# write sink: clear in-memory state on any sink call
curl -X POST -H 'X-API-Key: PLENORA_TEST_KEY' -H 'X-Test-Reset: 1' \
     -H 'Content-Type: application/json' -d '{"records":[]}' \
     http://localhost:8080/data/sink/records

# pagination
curl 'http://localhost:8080/page/offset?offset=0&limit=5'

# errors
curl -i http://localhost:8080/error/429

# geo
curl http://localhost:8080/geo/cities

# file
curl -OJ 'http://localhost:8080/file/csv/customers?api_key=PLENORA_TEST_KEY'

# arcgis
curl 'http://localhost:8080/sharing/rest/portals/self?token=ARCGIS_FAKE_TOKEN'
curl 'http://localhost:8080/services/Test_FS/FeatureServer/0/query?where=1=1&outFields=*&f=json&token=ARCGIS_FAKE_TOKEN'

# arcgis: oauth2 token
curl -X POST http://localhost:8080/sharing/rest/oauth2/token \
     -d grant_type=client_credentials \
     -d client_id=plenora \
     -d client_secret=plenora_secret

# arcgis: createFolder
curl -X POST 'http://localhost:8080/sharing/rest/content/users/tester/createFolder?token=ARCGIS_FAKE_TOKEN' \
     -d 'title=Plenora_Folder&f=json'

# arcgis: isServiceNameAvailable
curl 'http://localhost:8080/sharing/rest/portals/self/isServiceNameAvailable?name=My_FS&type=FeatureServer&token=ARCGIS_FAKE_TOKEN'

# arcgis: services catalog root
curl 'http://localhost:8080/rest/services?token=ARCGIS_FAKE_TOKEN'

# slow
curl http://localhost:8080/slow/delay/2

# sharepoint: AAD-style client_credentials token
curl -X POST 'http://localhost:8080/sharepoint/login.microsoftonline.com/contoso/oauth2/v2.0/token' \
     -d grant_type=client_credentials \
     -d client_id=plenora_sp \
     -d client_secret=plenora_sp_secret \
     -d 'scope=https://fake.sharepoint.com/.default'

# sharepoint: AADSTS error path (e.g. 50053 = account locked)
curl -i -X POST 'http://localhost:8080/sharepoint/login.microsoftonline.com/error/50053' \
     -d ''

# sharepoint: web descriptor
curl -H 'Authorization: Bearer SHAREPOINT_FAKE_TOKEN' \
     http://localhost:8080/sharepoint/_api/web

# sharepoint: list items
curl -H 'Authorization: Bearer SHAREPOINT_FAKE_TOKEN' \
     "http://localhost:8080/sharepoint/_api/web/lists/getbytitle('Documents')/items"

# sharepoint: download file (CSV by default)
curl -OJ -H 'Authorization: Bearer SHAREPOINT_FAKE_TOKEN' \
     "http://localhost:8080/sharepoint/_api/web/getfilebyserverrelativeurl('/sites/Plenora/Shared%20Documents/customers.csv')/\$value"

# sharepoint: throttle (429 + Retry-After: 5)
curl -i -H 'Authorization: Bearer SHAREPOINT_FAKE_TOKEN' \
     http://localhost:8080/sharepoint/throttle

# sharepoint: transient 5xx (503 -> 502 -> 200; reset with X-Test-Reset: 1)
curl -i -H 'Authorization: Bearer SHAREPOINT_FAKE_TOKEN' \
     -H 'X-Test-Reset: 1' \
     http://localhost:8080/sharepoint/transient_5xx
```

## Notes

- Fixtures intentionally cover only the **base** ids from
  `test-e2e/sql/postgres/01_seed.sql` (1..12 customers, 1..9 cities, 1..8
  products). Edge-case ids defined in the seed (13.., 16.., 21..) are not
  re-exposed here; tests that need them should hit the database directly.
- Rate-limit, ArcGIS edit, REST write-sink and SharePoint transient-5xx
  state are all in-process (per-uvicorn-worker). Restart the container or
  call `POST /throttle/reset`, send `X-Test-Reset: 1` on any ArcGIS call,
  on any `/data/sink/*` call, or on `/sharepoint/transient_5xx` to reset
  between test runs. Multi-worker deployments are not supported by the
  in-memory state.
- The shapefile endpoint produces a real `.shp/.shx/.dbf` ZIP when `pyshp`
  is installed (the docker image installs it from `requirements.txt`); the
  code falls back to a placeholder ZIP otherwise.
