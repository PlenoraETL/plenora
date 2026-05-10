# Plenora E2E expected queries — INDEX

Risultati attesi machine-readable derivati da `expected/README.md`.
Per ogni query canonica una coppia `<name>.sql` + `<name>.json`.

Convenzioni JSON:
- numeri interi → `int` JSON;
- `DECIMAL` → string per preservare precisione (es. `"123.45"`);
- date → `"YYYY-MM-DD"`;
- timestamp → `"YYYY-MM-DDTHH:MM:SS"` (naive, no tz);
- NULL SQL → `null` JSON;
- geometrie → WKT (`"POINT(<lon> <lat>)"`);
- stringhe Unicode preservate as-is.

L'ordine delle righe in ciascun JSON segue l'`ORDER BY` documentato
nel relativo `.sql`.

## Tabella delle query

| Query | File SQL | File JSON | Tabelle coinvolte | Edge case testato |
|-------|----------|-----------|-------------------|-------------------|
| count_per_table | count_per_table.sql | count_per_table.json | tutte | conteggi base+edge+NULL+volume |
| regions_left_join_cities | regions_left_join_cities.sql | regions_left_join_cities.json | regions, cities | LEFT JOIN preserva VEN (regione senza cities) |
| customers_per_region | customers_per_region.sql | customers_per_region.json | regions, cities, customers | INNER JOIN scarta customer con city_id NULL (id 12, 20) |
| top_n_customers_by_orders | top_n_customers_by_orders.sql | top_n_customers_by_orders.json | customers, orders | tie-break deterministico su customer_id; customer 1 ha 7 ordini (5 edge) |
| orders_total_per_year | orders_total_per_year.sql | orders_total_per_year.json | orders | EXTRACT(YEAR …) su date 1999/2023/2024/2025; SUM ignora NULL (order 21) |
| order_line_total_integrity | order_line_total_integrity.sql | order_line_total_integrity.json | order_items | riga edge id=33 con line_total ≠ qty*unit_price |
| cities_within_lombardia_boundary | cities_within_lombardia_boundary.sql | cities_within_lombardia_boundary.json | cities, regions | ST_Within su MultiPolygon LOM, include cities edge 10/11 |
| cities_within_campania_boundary | cities_within_campania_boundary.sql | cities_within_campania_boundary.json | cities, regions | ST_Within su MultiPolygon a 2 componenti (main + Capri) |
| regions_with_null_boundary | regions_with_null_boundary.sql | regions_with_null_boundary.json | regions | boundary IS NULL (VEN id=4) |
| routes_longest | routes_longest.sql | routes_longest.json | routes | edge cross-meridian (id=5, 20015 km) |
| employees_5_levels_deep | employees_5_levels_deep.sql | employees_5_levels_deep.json | employees | CTE ricorsiva, gerarchia 5 livelli (CEO→senior→junior→helper→intern) |
| products_per_category | products_per_category.sql | products_per_category.json | products | GROUP BY (con BOOKS solitario) |
| products_categories_having_more_than_one | products_categories_having_more_than_one.sql | products_categories_having_more_than_one.json | products | HAVING COUNT(*) > 1 esclude BOOKS |
| customers_with_null_age | customers_with_null_age.sql | customers_with_null_age.json | customers | WHERE age IS NULL (id=21) |
| customers_without_orders | customers_without_orders.sql | customers_without_orders.json | customers, orders | LEFT JOIN + IS NULL: 12 customer senza ordini (incl. Unicode/emoji/empty status) |
| orders_per_status | orders_per_status.sql | orders_per_status.json | orders | GROUP BY status su 21 ordini (paid 13 / pending 6 / cancelled 2) |
| events_log_count_per_event_type | events_log_count_per_event_type.sql | events_log_count_per_event_type.json | events_log | GROUP BY su 10000 righe di volume; distribuzione equiprobabile (2500 ciascuno) |
| events_log_null_count | events_log_null_count.sql | events_log_null_count.json | events_log | COUNT(*) vs COUNT(value): 10000 / 8000 / 2000 NULL |
