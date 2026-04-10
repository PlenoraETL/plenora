"""
E-17 — CRS assente (CSV) → warning
=====================================
Verifica il comportamento quando si carica un CSV con colonne lat/lon
ma senza informazioni CRS.

Comportamento atteso (backend):
  - Upload va a buon fine (200)
  - FileSource.crs = None (nessun CRS rilevato)
  - Nessun crash, nessun 500
  - Preview funziona normalmente
  - Il "warning CRS mancante" è responsabilità della UI, non del backend

Nota: il backend non inferisce automaticamente EPSG:4326 da colonne
chiamate "lat"/"lon" — il CRS rimane null. È comportamento corretto:
assegnare automaticamente 4326 potrebbe essere sbagliato (le coordinate
potrebbero essere in qualsiasi sistema).

Prerequisiti:
  - Backend FastAPI attivo su localhost:8000
"""

import csv
import os
import tempfile
import pytest

pytestmark = [pytest.mark.estrai]


@pytest.fixture(scope="module")
def csv_no_crs():
    """CSV con lat/lon ma nessuna indicazione CRS."""
    rows = [
        ["id", "nome", "lat", "lon", "categoria"],
        [1, "Punto A", 45.4642, 9.1900,  "parco"],
        [2, "Punto B", 41.9028, 12.4964, "museo"],
        [3, "Punto C", 40.8518, 14.2681, "chiesa"],
        [4, "Punto D", 44.4056, 8.9463,  "piazza"],
        [5, "Punto E", 43.7696, 11.2558, "fontana"],
    ]
    tmp = tempfile.NamedTemporaryFile(
        suffix=".csv", mode="w", newline="", encoding="utf-8", delete=False
    )
    writer = csv.writer(tmp)
    writer.writerows(rows)
    tmp.close()
    yield tmp.name
    os.unlink(tmp.name)


class TestE17:

    def test_upload_csv_senza_crs(self, client, csv_no_crs):
        """E-17a: upload CSV senza CRS va a buon fine (200)."""
        with open(csv_no_crs, "rb") as f:
            res = client.post(
                "/files/upload",
                files={"file": ("test_e17_no_crs.csv", f, "text/csv")},
                data={"name": "[AUTO] E-17 csv no crs"},
            )
        assert res.status_code == 200, f"Upload fallito: {res.text}"
        file_id = res.json().get("id")
        assert file_id

        client.delete(f"/files/{file_id}")

    def test_crs_null(self, client, csv_no_crs):
        """
        E-17b: FileSource.crs = None.
        Il backend non assegna automaticamente EPSG:4326 ai CSV senza .prj.
        """
        with open(csv_no_crs, "rb") as f:
            res = client.post(
                "/files/upload",
                files={"file": ("test_e17_no_crs.csv", f, "text/csv")},
                data={"name": "[AUTO] E-17 crs null"},
            )
        assert res.status_code == 200
        file_source = res.json()
        file_id = file_source["id"]

        try:
            crs = file_source.get("crs")
            assert crs is None, (
                f"CRS atteso None per CSV senza proiezione, trovato: '{crs}'"
            )
        finally:
            client.delete(f"/files/{file_id}")

    def test_preview_senza_crs_non_crasha(self, client, csv_no_crs):
        """
        E-17c: preview funziona normalmente anche senza CRS.
        Nessun 500, 5 righe restituite.
        """
        with open(csv_no_crs, "rb") as f:
            res = client.post(
                "/files/upload",
                files={"file": ("test_e17_no_crs.csv", f, "text/csv")},
                data={"name": "[AUTO] E-17 preview no crs"},
            )
        assert res.status_code == 200
        file_id = res.json()["id"]

        try:
            res_preview = client.get(f"/files/{file_id}/preview", params={"limit": 50})
            assert res_preview.status_code == 200, (
                f"Preview senza CRS ha crashato: {res_preview.text}"
            )

            data = res_preview.json()
            rows = data.get("rows", data.get("data", []))
            assert len(rows) == 5, f"Attese 5 righe, trovate {len(rows)}"

            # lat/lon presenti e valorizzate
            for row in rows:
                assert row.get("lat") is not None, f"lat null: {row}"
                assert row.get("lon") is not None, f"lon null: {row}"

        finally:
            client.delete(f"/files/{file_id}")

    def test_latlon_tipo_real_senza_crs(self, client, csv_no_crs):
        """
        E-17d: senza CRS le colonne lat/lon restano 'real', non geometry.
        Nessuna conversione automatica a geometry:point.
        """
        with open(csv_no_crs, "rb") as f:
            res = client.post(
                "/files/upload",
                files={"file": ("test_e17_no_crs.csv", f, "text/csv")},
                data={"name": "[AUTO] E-17 tipi no crs"},
            )
        assert res.status_code == 200
        file_source = res.json()
        file_id = file_source["id"]

        try:
            columns = file_source.get("columns", [])
            col_map = {
                (c["name"] if isinstance(c, dict) else c):
                (c.get("type", "") if isinstance(c, dict) else "")
                for c in columns
            }

            assert col_map.get("lat") == "real", (
                f"lat senza CRS: atteso 'real', trovato '{col_map.get('lat')}'"
            )
            assert col_map.get("lon") == "real", (
                f"lon senza CRS: atteso 'real', trovato '{col_map.get('lon')}'"
            )

            # Nessuna colonna geometry deve essere presente
            geom_cols = [k for k in col_map if "geom" in k.lower()]
            assert not geom_cols, (
                f"Colonne geometry inattese per CSV senza CRS: {geom_cols}"
            )

        finally:
            client.delete(f"/files/{file_id}")


# ─── Edge cases ───────────────────────────────────────────────────────────────

import io as _io

class TestE17Edge:

    def test_csv_completamente_vuoto(self, client):
        """
        E-17 edge: file CSV completamente vuoto (0 byte) → errore graceful, nessun crash.
        """
        res = client.post(
            "/files/upload",
            files={"file": ("empty.csv", b"", "text/csv")},
            data={"name": "[AUTO] E-17 edge csv vuoto"},
        )
        assert res.status_code in (200, 400), (
            f"CSV vuoto ha causato crash 500: {res.status_code} {res.text}"
        )
        if res.status_code == 200:
            client.delete(f"/files/{res.json()['id']}")

    def test_csv_solo_header_no_dati(self, client):
        """
        E-17 edge: CSV con solo riga header e nessun dato → row_count=0.
        """
        data = "id,lat,lon,nome\n".encode("utf-8")
        res = client.post(
            "/files/upload",
            files={"file": ("only_header.csv", data, "text/csv")},
            data={"name": "[AUTO] E-17 edge csv solo header"},
        )
        assert res.status_code == 200, (
            f"CSV solo header ha crashato: {res.status_code} {res.text}"
        )
        file_source = res.json()
        file_id = file_source["id"]

        try:
            assert file_source.get("row_count") == 0, (
                f"row_count atteso 0, trovato {file_source.get('row_count')}"
            )
            res_p = client.get(f"/files/{file_id}/preview")
            assert res_p.status_code == 200, "Preview su CSV senza dati ha crashato"
        finally:
            client.delete(f"/files/{file_id}")

    def test_csv_colonne_tutte_null(self, client):
        """
        E-17 edge: CSV con colonne presenti ma valori tutti vuoti → upload OK,
        nessun crash in preview.
        """
        buf = _io.StringIO()
        import csv as _csv
        w = _csv.writer(buf)
        w.writerow(["id", "lat", "lon", "nome"])
        for i in range(5):
            w.writerow(["", "", "", ""])

        data = buf.getvalue().encode("utf-8")
        res = client.post(
            "/files/upload",
            files={"file": ("all_null.csv", data, "text/csv")},
            data={"name": "[AUTO] E-17 edge csv valori null"},
        )
        assert res.status_code == 200, (
            f"CSV valori null ha crashato: {res.status_code} {res.text}"
        )
        file_id = res.json()["id"]

        try:
            res_p = client.get(f"/files/{file_id}/preview")
            assert res_p.status_code == 200, (
                f"Preview CSV null values ha crashato: {res_p.text}"
            )
        finally:
            client.delete(f"/files/{file_id}")
