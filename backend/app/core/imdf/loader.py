"""
IMDF loader — estrazione zip + lettura File Geodatabase.

Funzioni pubbliche:
  extract_imdf_zip(zip_path, dest_dir=None) -> ImdfBundle
      Estrae lo zip in una directory temporanea e indicizza i contenuti
      noti (gdb, ifc, manifest, validation, renderers, geojson IMDF).

  load_imdf_layers(gdb_path, target_srid=4326, drop_z=True) -> dict[str, GeoDataFrame]
      Apre il File Geodatabase con pyogrio/OpenFileGDB e ritorna i 5 layer
      Localita / OperaPrincipale / Livello / Locale / Details, riproiettati
      a `target_srid` (default 4326), con eventuale appiattimento Z (default
      True), filtrando i campi di sistema ESRI (GLOBALID, CREATED_USER,
      CREATED_DATE, Shape_Length, Shape_Area).

Niente effetti collaterali, niente chiamate di rete, niente scrittura su
DB. Pensato per essere richiamato da `processor.py` o testato in isolamento.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

# I 5 layer dello standard IMDF prodotti dal servizio imdf.plenora.it.
# L'ordine e' significativo: Localita > OperaPrincipale > Livello > Locale > Details
# corrisponde alla gerarchia spaziale (sito → edifici → piani → locali → dettagli).
EXPECTED_LAYERS: List[str] = [
    "Localita",
    "OperaPrincipale",
    "Livello",
    "Locale",
    "Details",
]

# Campi gestiti automaticamente da ArcGIS (OBJECTID, GLOBALID, ecc.) e campi
# calcolati da Esri (Shape_Length, Shape_Area). Vanno esclusi prima di creare
# uno schema custom o di inviare features in addFeatures, altrimenti l'API
# ArcGIS lancia errori.
ESRI_SYSTEM_FIELDS = {
    "OBJECTID",
    "objectid",
    "GLOBALID",
    "globalid",
    "GlobalID",
    "Shape_Length",
    "Shape_Area",
    "SHAPE_Length",
    "SHAPE_Area",
    "CREATED_USER",
    "CREATED_DATE",
    "LAST_EDITED_USER",
    "LAST_EDITED_DATE",
}


@dataclass
class ImdfBundle:
    """Indice dei file dentro uno zip IMDF estratto.

    `tmp_dir` e' la directory radice dove l'estrazione e' stata fatta. Il
    chiamante e' responsabile di chiamare `cleanup()` quando finito.
    """

    tmp_dir: Path
    site_id: str
    gdb_path: Optional[Path] = None
    ifc_path: Optional[Path] = None
    manifest_path: Optional[Path] = None
    validation_path: Optional[Path] = None
    renderers_path: Optional[Path] = None
    imdf_dir: Optional[Path] = None
    imdf_geojson_files: List[Path] = field(default_factory=list)
    raw_filelist: List[str] = field(default_factory=list)

    def cleanup(self) -> None:
        if self.tmp_dir and self.tmp_dir.exists():
            shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def manifest(self) -> Dict:
        if self.manifest_path and self.manifest_path.exists():
            return json.loads(self.manifest_path.read_text(encoding="utf-8"))
        return {}

    def validation(self) -> Dict:
        if self.validation_path and self.validation_path.exists():
            return json.loads(self.validation_path.read_text(encoding="utf-8"))
        return {}

    def renderers(self) -> Dict:
        if self.renderers_path and self.renderers_path.exists():
            return json.loads(self.renderers_path.read_text(encoding="utf-8"))
        return {}


def extract_imdf_zip(zip_path: str | os.PathLike, dest_dir: Optional[str | os.PathLike] = None) -> ImdfBundle:
    """Estrae uno zip IMDF e indicizza i contenuti riconosciuti.

    Args:
        zip_path: percorso allo zip IMDF (output di imdf.plenora.it/api/process)
        dest_dir: se None, crea una tempdir nuova; altrimenti usa quella

    Returns:
        ImdfBundle popolato.

    Raises:
        FileNotFoundError: se zip_path non esiste
        ValueError: se lo zip non sembra un IMDF valido (manca .gdb e manifest)
    """
    src = Path(zip_path)
    if not src.exists():
        raise FileNotFoundError(f"Zip IMDF non trovato: {src}")

    # Distinguiamo "abbiamo creato noi la tmp dir" da "il caller l'ha
    # passata": solo nel primo caso siamo noi responsabili del cleanup
    # in caso di errore. Sul path success la responsabilita' del cleanup
    # passa al caller via `bundle.cleanup()`. Senza il try/except, una
    # eccezione (zipfile corrotto, ValueError IMDF non valido) dopo
    # `mkdtemp` lascerebbe la tmp dir su disco.
    _we_own_tmp = dest_dir is None
    tmp = Path(dest_dir) if dest_dir else Path(tempfile.mkdtemp(prefix="imdf_"))
    tmp.mkdir(parents=True, exist_ok=True)

    try:
        from app.utils.zip_safe import safe_extractall
        with zipfile.ZipFile(src) as z:
            members = z.namelist()
            safe_extractall(z, tmp)
        bundle = _build_bundle_index(tmp, members)
    except Exception:
        if _we_own_tmp:
            shutil.rmtree(tmp, ignore_errors=True)
        raise

    return bundle


def _build_bundle_index(tmp: Path, members: List[str]) -> "ImdfBundle":
    """Indicizza i contenuti noti di un bundle IMDF gia' estratto in `tmp`.

    Separato da `extract_imdf_zip` per wrappare l'intero ciclo
    estrazione + indicizzazione in un singolo try/cleanup. Anche la
    validazione finale `raise ValueError("non sembra un IMDF")` deve
    avvenire sotto il guard del cleanup, altrimenti la tmp dir
    resterebbe su disco.
    """
    bundle = ImdfBundle(tmp_dir=tmp, site_id="", raw_filelist=members)

    # Indicizza i contenuti noti.
    # Convenzione del servizio:
    #   {SITE_ID}.gdb/             cartella File Geodatabase
    #   {SITE_ID}.ifc              file IFC al root
    #   {SITE_ID}/                 cartella IMDF (geojson + manifest IMDF)
    #   manifest.json              manifest del processing (root)
    #   validation.json            (root)
    #   renderers.json             (root)
    site_id_candidates = set()

    for m in members:
        p = tmp / m
        # Il GDB e' una "cartella" dentro lo zip: i membri sono path tipo
        # "LO2244.gdb/a00000001.gdbtable". Estraggo il prefisso fino a `.gdb/`
        # come cartella GDB.
        if ".gdb/" in m:
            if bundle.gdb_path is None:
                top = m.split(".gdb/", 1)[0] + ".gdb"
                bundle.gdb_path = tmp / top
                site_id_candidates.add(top.split("/")[-1].removesuffix(".gdb"))
        elif m.endswith(".ifc"):
            bundle.ifc_path = p
            site_id_candidates.add(Path(m).stem)
        elif m == "manifest.json":
            bundle.manifest_path = p
        elif m == "validation.json":
            bundle.validation_path = p
        elif m == "renderers.json":
            bundle.renderers_path = p
        elif m.endswith(".geojson") and "/" in m:
            bundle.imdf_geojson_files.append(p)
            top = m.split("/", 1)[0]
            site_id_candidates.add(top)
            if bundle.imdf_dir is None:
                bundle.imdf_dir = tmp / top

    # Site ID: prendi dal manifest se presente, altrimenti dal primo candidato
    if bundle.manifest_path and bundle.manifest_path.exists():
        try:
            mf = json.loads(bundle.manifest_path.read_text(encoding="utf-8"))
            sites = mf.get("sites") or {}
            if isinstance(sites, dict) and sites:
                bundle.site_id = next(iter(sites.keys()))
        except Exception:
            pass

    if not bundle.site_id and site_id_candidates:
        bundle.site_id = sorted(site_id_candidates)[0]

    if not bundle.gdb_path and not bundle.manifest_path:
        raise ValueError(
            "Lo zip non sembra un IMDF: manca sia .gdb sia manifest.json"
        )

    return bundle


def load_imdf_layers(
    gdb_path: str | os.PathLike,
    target_srid: int = 4326,
    drop_z: bool = True,
):
    """Apre un File Geodatabase IMDF e ritorna i 5 layer come GeoDataFrames.

    Args:
        gdb_path: percorso alla cartella .gdb
        target_srid: EPSG di destinazione (default 4326 — WGS84, ottimale per
            ArcGIS Online). Se diverso dal CRS originale (di norma 6875 per
            i siti italiani RFI), i layer vengono riproiettati.
        drop_z: se True (default) appiattisce le geometrie 3D Z in 2D XY.
            ArcGIS FeatureService web 2D di default non gestisce Z se
            `hasZ` non e' attivo, quindi conviene rimuoverla per il primo
            upload.

    Returns:
        dict {layer_name: GeoDataFrame} per i 5 layer attesi. Eventuali
        layer mancanti vengono semplicemente omessi (warning).

    Raises:
        FileNotFoundError: se gdb_path non esiste
    """
    import geopandas as gpd
    import pyogrio
    from shapely.ops import transform as shapely_transform

    p = Path(gdb_path)
    if not p.exists():
        raise FileNotFoundError(f"GDB non trovato: {p}")

    available = {name: gtype for name, gtype in pyogrio.list_layers(str(p))}

    result: Dict[str, "gpd.GeoDataFrame"] = {}
    for layer_name in EXPECTED_LAYERS:
        if layer_name not in available:
            continue
        gdf = gpd.read_file(str(p), layer=layer_name)

        # Riproiezione
        if gdf.crs and target_srid:
            try:
                current = gdf.crs.to_epsg()
                if current and current != target_srid:
                    gdf = gdf.to_crs(epsg=target_srid)
            except Exception:
                # CRS incompatibile o riproiezione fallita: lascia originale,
                # il chiamante gestira'.
                pass

        # Drop Z (geometrie 3D → 2D)
        if drop_z and len(gdf) > 0:
            def _drop_z(geom):
                if geom is None or not geom.has_z:
                    return geom
                return shapely_transform(lambda x, y, z=None: (x, y), geom)

            try:
                gdf = gdf.set_geometry(gdf.geometry.apply(_drop_z))
            except Exception:
                pass

        # Filtra colonne di sistema ESRI: non vanno mai inviate al create_service
        sys_cols = [c for c in gdf.columns if c in ESRI_SYSTEM_FIELDS]
        if sys_cols:
            gdf = gdf.drop(columns=sys_cols)

        result[layer_name] = gdf

    return result


def summarize_layers(layers: Dict) -> List[Dict]:
    """Ritorna un riassunto leggibile dei layer IMDF caricati.

    Utile per logging/preview lato UI senza dover serializzare interi
    GeoDataFrame.
    """
    out = []
    for name, gdf in layers.items():
        try:
            geom_type = gdf.geometry.geom_type.iloc[0] if len(gdf) > 0 else None
        except Exception:
            geom_type = None
        try:
            crs = str(gdf.crs) if gdf.crs else None
        except Exception:
            crs = None
        out.append({
            "name": name,
            "rows": len(gdf),
            "geom_type": geom_type,
            "crs": crs,
            "fields": [c for c in gdf.columns if c != "geometry"],
        })
    return out
