# MANIPOLA Checkpoint Policy

Data: 2026-04-28

Scopo: definire quali nodi MANIPOLA devono produrre snapshot intermedi nel
nuovo sistema di persistenza. Questo documento integra
`intermediate_persistence_to_be.md`.

## Principio

Non salviamo le righe di tutti i nodi.

Separiamo:

1. **Execution data**: il DataFrame in memoria che serve al nodo successivo.
   Rimane sempre in RAM nel flusso pipeline, non dipende da intermediate DB.
2. **Node fingerprint**: hash/metadata per capire se un nodo e' cambiato tra
   run. Puo' essere calcolato per tutti o solo per checkpoint, in base a
   policy.
3. **Node preview rows**: prime N righe persistite per debug UI. Solo per nodi
   checkpoint.
4. **Full artifact**: opzionale e async. Solo per nodi marcati o modalita'
   debug.

Default consigliato:

```text
MANIPOLA_INTERMEDIATE_CHECKPOINTS=critical
MANIPOLA_NODE_FINGERPRINTS=checkpointed
```

Quindi:

- transform semplici non salvano righe;
- transform semplici non calcolano fingerprint completo di default;
- nodi critici salvano metadata/hash/preview;
- `result` viene sempre checkpointato.

## Policy knobs

### `MANIPOLA_INTERMEDIATE_CHECKPOINTS`

| Valore | Semantica |
|---|---|
| `none` | nessun nodo intermedio; solo result |
| `critical` | nodi critici + result |
| `marked` | solo nodi con `save_intermediate=true` + result |
| `critical_or_marked` | nodi critici oppure marcati + result |
| `all` | tutti i nodi, modalita' debug/legacy |

Default: `critical`.

### `MANIPOLA_NODE_FINGERPRINTS`

| Valore | Semantica |
|---|---|
| `result_only` | hash solo del result |
| `checkpointed` | hash solo dei nodi checkpoint + result |
| `all` | hash di tutti i transform, anche senza preview rows |

Default: `checkpointed`.

Motivo: calcolare hash su milioni di righe dopo ogni transform costa meno di
scrivere JSONB, ma resta un full scan. Non deve essere il default nascosto se
la priorita' e' performance.

## Classificazione iniziale

Regola generale:

- `critical`: cambia cardinalita', combina input, distrugge/ricostruisce
  schema, cambia geometrie in modo sostanziale, oppure e' un nodo di snodo
  che l'utente vorrebbe ispezionare se il risultato finale e' sbagliato.
- `noncritical`: trasformazione locale riga-per-riga o colonna-per-colonna,
  stesso numero righe, debugging poco utile come checkpoint persistente.
- `marked-only`: utile in alcuni workflow, ma troppo rumoroso/costoso come
  default.

### Critical default

| Transform | Motivo |
|---|---|
| `join` | combina input, schema/cardinalita' possono cambiare |
| `multi_join` | nodo speciale executor, combina N input |
| `concat` | combina input verticalmente |
| `cross_join` | prodotto cartesiano, cardinalita' esplosiva |
| `table_diff` | confronto multi-input, output semantico |
| `aggregate` | group by, cardinalita' e schema cambiano |
| `pivot` | reshape sostanziale |
| `melt` | reshape sostanziale |
| `transpose` | reshape sostanziale |
| `distinct` | riduce righe |
| `dedup_advanced` | riduce righe |
| `filter` | riduce righe |
| `sample` | riduce righe |
| `drop_columns` | rimuove schema |
| `geo_clip` | spatial cut, cardinalita'/geometria possono cambiare |
| `geo_difference` | cambia geometrie in modo sostanziale |
| `geo_intersection` | overlay, combina input |
| `geo_overlay` | overlay, combina input |
| `geo_union` | union geometrica |
| `geo_symmetric_difference` | overlay/differenza |
| `sjoin` | spatial join, combina input |
| `geo_nearest` | join spaziale nearest |
| `geo_within` | filtro spaziale da input multipli |
| `geo_count_points_in_polygons` | aggregazione spaziale da due layer |
| `geo_dissolve` | aggrega geometrie, cardinalita' cambia |
| `geo_explode` | espande multigeometrie, cardinalita' cambia |
| `geo_line_builder` | costruisce righe aggregate da punti |
| `geo_polygon_builder` | costruisce righe aggregate da punti |

### Noncritical default

| Transform | Motivo |
|---|---|
| `fill_na` | value-level |
| `replace` | value-level |
| `text_normalize` | value-level |
| `type_cast` | value/type-level |
| `rename` | schema label only |
| `reorder_columns` | schema order only |
| `sort` | ordine righe, non payload logico |
| `lookup` | value-level mapping |
| `string_pad` | value-level |
| `geo_reproject` | CRS/geometria trasformata ma cardinalita' stabile; marked se serve audit GIS |
| `geo_simplify` | geometria modificata ma spesso tecnico; marked se serve audit GIS |
| `geo_make_valid` | cleanup tecnico; marked se serve audit GIS |
| `geo_clean_topology` | cleanup tecnico; marked se serve audit GIS |
| `geo_voronoi` | da verificare: registry dice `same`, ma semanticamente puo' essere sostanziale |

### Marked-only default

Questi aggiungono colonne o derivati utili, ma salvarli tutti come checkpoint
di default rischia di reintrodurre rumore:

| Transform | Motivo |
|---|---|
| `formula` | nuova colonna calcolata |
| `concat_columns` | nuova colonna |
| `split_column` | nuove colonne |
| `flatten_json` | espansione schema |
| `conditional` | nuova colonna |
| `bin` | nuova colonna |
| `date_extract` | nuove colonne |
| `statistics` | nuove colonne/statistiche |
| `window_function` | nuova colonna dipendente da ordine/gruppo |
| `add_row_number` | nuova colonna |
| `uuid_generator` | nuova colonna non deterministica se non controllata |
| `md5_hash` | nuova colonna |
| `mask_data` | privacy transform |
| `string_extract` | nuova colonna |
| `string_length` | nuova colonna |
| `geo_from_coords` | nuova geometria |
| `geo_buffer` | nuova geometria |
| `geo_centroid` | nuova geometria |
| `geo_area` | nuova colonna |
| `geo_length` | nuova colonna |
| `geo_perimeter` | nuova colonna |
| `geo_boundary` | nuova geometria |
| `geo_envelope` | nuova geometria |
| `geo_convex_hull` | nuova geometria |
| `geo_point_on_surface` | nuova geometria |
| `geo_to_wkt` | nuova colonna |
| `geo_bounds_extractor` | nuove colonne |
| `geo_vertex_count` | nuova colonna |
| `geo_distance` | nuova colonna da input multipli, ma spesso derivata; marked salvo workflow specifico |

## Eccezioni e override

Ogni nodo puo' avere flag opzionale:

```json
{
  "save_intermediate": true,
  "save_intermediate_mode": "preview_only"
}
```

Questo serve per:

- audit su un transform normalmente noncritical;
- debug temporaneo;
- workflow dove una formula o una normalizzazione e' business-critical.

Nel primo batch il flag puo' essere solo backend-compatible, senza UI dedicata.
La UI puo' arrivare dopo.

## Evoluzione futura: controllo per singolo nodo

La direzione prodotto finale deve permettere all'utente/admin di decidere,
per ogni nodo, se salvare o meno i dati intermedi.

Livelli possibili:

1. Policy globale di sistema:
   - default attuale del deployment;
   - esempio: `critical`.
2. Policy per progetto/canvas:
   - override opzionale per workflow specifici;
   - esempio: un progetto audit usa `critical_or_marked`, uno operativo usa
     `none` o `critical`.
3. Policy per nodo:
   - checkbox/toggle nel pannello nodo;
   - campi canvas:

     ```json
     {
       "save_intermediate": true,
       "intermediate_mode": "preview_only"
     }
     ```

   - valori possibili: `inherit`, `off`, `preview_only`, `full_artifact`;
   - `inherit` segue la policy globale/progetto;
   - `off` forza niente snapshot anche se il nodo sarebbe critical;
   - `preview_only` forza preview;
   - `full_artifact` forza salvataggio completo async.

Regola di sicurezza/performance:

- il default resta governato dal sistema quando il nodo non specifica nulla;
- se il nodo specifica `save_intermediate` / `intermediate_mode`, la scelta
  del nodo vince sulla policy globale/progetto;
- il backend non decide al posto del frontend: valida solo che il mode sia
  ammesso dalla configurazione di sistema e che l'utente abbia permessi per
  richiedere eventuali mode costosi;
- full artifact per nodo deve essere opt-in esplicito, mai implicito.

Ordine di precedenza:

```text
node.intermediate_mode / node.save_intermediate
  > project/canvas policy futura
  > global system policy
  > default built-in
```

Questa evoluzione e' fuori dal primo batch per evitare scope creep UI/schema,
ma il design backend deve gia' lasciare spazio ai campi `save_intermediate` e
`intermediate_mode` nel canvas.

## Implementazione proposta

Nuovo helper puro:

```python
def classify_manipola_checkpoint(node, transform_registry) -> dict:
    return {
        "checkpoint": bool,
        "reason": "...",
        "tier": "critical|marked|noncritical|result",
    }
```

Input:

- `node.type`
- `node.transform_op`
- `node.data.config`
- registry transform config (`inputs`, `output`, `category`)
- flag `save_intermediate`

Regole:

1. `node.type == "result"` -> checkpoint sempre.
2. `node.type == "multi_join"` -> critical.
3. `save_intermediate == true` -> marked.
4. `transform_op` in set critical statico -> critical.
5. policy `all` -> checkpoint.
6. altrimenti noncritical.

Non basarsi solo su `output.type == "modified"`: nel registry alcuni transform
con `output.type="same"` sono in realta' cardinality-changing (`distinct`,
`filter`, `geo_explode`). Serve una allowlist esplicita.

## Test contract minimi

1. `critical`: `join`, `aggregate`, `filter`, `geo_explode`, `sjoin` -> checkpoint.
2. `noncritical`: `text_normalize`, `fill_na`, `rename`, `type_cast` -> no checkpoint.
3. `marked`: `formula` con `save_intermediate=true` -> checkpoint.
4. `all`: ogni transform -> checkpoint.
5. `none`: nessun transform, ma `result` resta checkpoint.
6. `multi_join`: checkpoint anche senza `transform_op`.
7. Registry drift: ogni transform id del registry e' classificato in una delle
   tre liste (`critical`, `noncritical`, `marked-only`) o il test fallisce.

L'ultimo test e' importante: quando aggiungiamo una nuova transform in futuro,
deve essere classificata esplicitamente. Cosi' non ci torniamo sopra ogni volta
per bug di performance.

## Decisione aperta

`geo_voronoi` e alcuni transform GIS tecnici sono borderline. Per il primo
batch li mettiamo `marked-only` o `noncritical` se il registry dice
cardinalita' stabile. Se emergono workflow GIS dove sono checkpoint importanti,
si spostano in `critical` con test.
