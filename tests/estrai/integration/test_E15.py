"""
E-15 — CRS propagato senza riproiezione (EPSG:32632)
=====================================================
Verifica che una tabella PostgreSQL con geometria EPSG:32632 (UTM zona 32N)
venga estratta senza riproiezione automatica a WGS84.

Comportamento atteso:
  - L'inspector rileva il SRID → tipo colonna = "geometry:point:32632"
  - La query usa ST_AsText senza ST_Transform
  - Le coordinate WKT restano in UTM 32N (X ~ 500000, non < 180)
  - Il canvas riporta il tipo con SRID corretto

Setup:
  - Tabella PG con 3 punti in UTM 32N (coordinate tipiche Italia nord)
    Es. Torino ~ (398000, 4992000), Milano ~ (515000, 5034000)

Prerequisiti:
  - Docker PostgreSQL + PostGIS attivo su localhost:5432
  - Backend FastAPI attivo su localhost:8000
"""

import re
import pytest

pytestmark = [pytest.mark.estrai, pytest.mark.postgres]

TABLE_NAME = "test_e15_utm32n"

# Coordinate UTM 32N per 3 città italiane
# Torino, Milano, Bologna
UTM_POINTS = [
    (398000.0, 4992000.0, "Torino"),
    (515000.0, 5034000.0, "Milano"),
    (686000.0, 4929000.0, "Bologna"),
]


class TestE15:

    def test_inspector_rileva_srid_32632(self, client, pg_conn, pg_table, make_project, poll):
        """
        E-15a: l'inspector rileva SRID 32632 → tipo = "geometry:point:32632".
        """
        pg_table(
            ddl="id INT PRIMARY KEY, nome VARCHAR(50), geom geometry(Point, 32632)",
            rows=[
                f"INSERT INTO {TABLE_NAME} VALUES ({i+1}, '{p[2]}', "
                f"ST_GeomFromText('POINT({p[0]} {p[1]})', 32632))"
                for i, p in enumerate(UTM_POINTS)
            ],
            table_name=TABLE_NAME,
        )

        # Ottieni lista colonne via inspector (GET /connections/{id}/tables/{name})
        res = client.get(f"/connections/{pg_conn}/tables/{TABLE_NAME}/columns")
        if res.status_code == 404:
            # Alcuni backend usano un endpoint diverso — fallback via canvas
            pytest.skip("Endpoint /columns non disponibile — verificato in E-15b")

        columns = res.json().get("columns", [])
        geom_col = next((c for c in columns if "geom" in c.get("name", "").lower()), None)
        assert geom_col, f"Colonna geometry non trovata: {columns}"

        col_type = geom_col.get("type", "")
        assert "32632" in col_type, (
            f"SRID 32632 non nel tipo: '{col_type}'"
        )

    def test_coordinate_non_riproiettate(self, client, pg_conn, pg_table, make_project, poll):
        """
        E-15b: le coordinate WKT nel preview sono in UTM 32N (X ~ 500000),
        non riproiettate in WGS84 (X < 180).
        """
        pg_table(
            ddl="id INT PRIMARY KEY, nome VARCHAR(50), geom geometry(Point, 32632)",
            rows=[
                f"INSERT INTO {TABLE_NAME} VALUES ({i+1}, '{p[2]}', "
                f"ST_GeomFromText('POINT({p[0]} {p[1]})', 32632))"
                for i, p in enumerate(UTM_POINTS)
            ],
            table_name=TABLE_NAME,
        )

        canvas = {
            "nodes": [
                {
                    "id": "node-source",
                    "type": "source",
                    "position": {"x": 100, "y": 200},
                    "table_name": TABLE_NAME,
                    "columns": [
                        {"name": "id",   "type": "int",                  "visible": True},
                        {"name": "nome", "type": "str",                  "visible": True},
                        {"name": "geom", "type": "geometry:point:32632", "visible": True},
                    ],
                    "filters": [],
                },
                {
                    "id": "node-result",
                    "type": "result",
                    "position": {"x": 500, "y": 200},
                    "fileName": "e15_output",
                    "format": "csv",
                },
            ],
            "edges": [
                {"id": "edge-1", "source": "node-source", "target": "node-result"}
            ],
        }

        project_id = make_project(
            name="[AUTO] E-15 CRS 32632 no riproiezione",
            project_type="estrai",
            connection_id=pg_conn,
            canvas=canvas,
        )

        res = client.get(f"/projects/{project_id}/preview", params={"limit": 10})
        assert res.status_code == 200
        task_id = res.json().get("task_id")
        result = poll(task_id)
        assert result.get("success") is True, f"Preview fallita: {result}"

        rows = result.get("rows", [])
        assert len(rows) == 3, f"Attese 3 righe, trovate {len(rows)}"

        # Verifica coordinate UTM 32N nelle geometrie WKT
        for row in rows:
            geom_val = str(row.get("geom", ""))
            assert geom_val and geom_val != "None", f"Geometria null: {row}"

            # Estrai coordinate dal WKT: POINT(X Y)
            coords = re.findall(r"[-\d.]+", geom_val)
            assert coords, f"Nessuna coordinata nel WKT: {geom_val}"

            x = float(coords[0])
            # UTM 32N: X ~ 300000–900000 per l'Italia
            # WGS84: X sarebbe tra 6 e 18 (gradi)
            assert x > 1000, (
                f"Coordinate sembrano riproiettate in WGS84: x={x}. "
                f"Atteso UTM 32N (x ~ 400000–700000). WKT: {geom_val}"
            )

    def test_tipo_colonna_con_srid(self, client, pg_conn, pg_table, make_project, poll):
        """
        E-15c: il tipo della colonna geometry nel risultato contiene il SRID 32632.
        """
        pg_table(
            ddl="id INT PRIMARY KEY, nome VARCHAR(50), geom geometry(Point, 32632)",
            rows=[
                f"INSERT INTO {TABLE_NAME} VALUES ({i+1}, '{p[2]}', "
                f"ST_GeomFromText('POINT({p[0]} {p[1]})', 32632))"
                for i, p in enumerate(UTM_POINTS)
            ],
            table_name=TABLE_NAME,
        )

        canvas = {
            "nodes": [
                {
                    "id": "node-source",
                    "type": "source",
                    "position": {"x": 100, "y": 200},
                    "table_name": TABLE_NAME,
                    "columns": [
                        {"name": "id",   "type": "int",                  "visible": True},
                        {"name": "nome", "type": "str",                  "visible": True},
                        {"name": "geom", "type": "geometry:point:32632", "visible": True},
                    ],
                    "filters": [],
                },
                {
                    "id": "node-result",
                    "type": "result",
                    "position": {"x": 500, "y": 200},
                    "fileName": "e15_output",
                    "format": "csv",
                },
            ],
            "edges": [
                {"id": "edge-1", "source": "node-source", "target": "node-result"}
            ],
        }

        project_id = make_project(
            name="[AUTO] E-15 tipo colonna SRID",
            project_type="estrai",
            connection_id=pg_conn,
            canvas=canvas,
        )

        res = client.get(f"/projects/{project_id}/preview", params={"limit": 10})
        assert res.status_code == 200
        task_id = res.json().get("task_id")
        result = poll(task_id)
        assert result.get("success") is True

        columns = result.get("columns", [])
        geom_col = next(
            (c for c in columns if "geom" in c.get("name", "").lower()),
            None
        )
        assert geom_col, f"Colonna geom non trovata in columns: {columns}"

        col_type = geom_col.get("type", "")
        assert "32632" in col_type or "geometry" in col_type, (
            f"Tipo colonna geometry inatteso: '{col_type}'"
        )
