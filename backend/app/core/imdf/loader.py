"""
IMDF loader — estrazione zip + lettura File Geodatabase.

Funzioni pubbliche:
- `extract_imdf_zip(path)`        — estrae da un path su disco (legacy)
- `extract_imdf_zip_bytes(data)`  — estrae da bytes (no temp path nel caller)
- `load_imdf_layers(gdb_path)`    — lettura dei 5 layer dal File Geodatabase
"""

from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from app.core.dataframes.frame_io import load_dataframe_from_file
from app.shared.contracts.payloads import JsonObject
from app.utils.zip_safe import safe_extractall
from app.utils.logger import get_logger

logger = get_logger(__name__)

# I 5 layer dello standard IMDF prodotti dal servizio imdf.plenora.it
# L'ordine e' significativo: Localita > OperaPrincipale > Livello > Locale > Details
# corrisponde alla gerarchia spaziale (sito → edifici → piani → locali → dettagli
EXPECTED_LAYERS: List[str] = [
    "Localita",
    "OperaPrincipale",
    "Livello",
    "Locale",
    "Details",
]

# Alias kept for internal callers and tests that monkeypatch this module.
from app.shared.connections.esri_system_fields import ESRI_SYSTEM_FIELDS_LITERAL as ESRI_SYSTEM_FIELDS


@dataclass
class ImdfBundle:
    """Indice dei file dentro uno zip IMDF estratto.

    `tmp_dir` e' la directory radice dove l'estrazione e' stata fatta. Il
    chiamante e' responsabile di chiamare `cleanup` quando finito.

    Invariant site_id (D3, R4 — ratificato 2026-04-22):
      Il campo `site_id` e' popolato **esclusivamente** da
      `_build_bundle_index` (manifest.json preferito; fallback al primo
      candidato alfabetico tra prefissi .gdb, .ifc, top-level dir).
      Valori consentiti:
        - stringa non-vuota: site_id risolto correttamente.
        - stringa vuota: euristica fallita (bundle malformato senza
          .gdb ne' manifest ne' geojson riconoscibili).
      Il valore None NON e' consentito: il campo e' tipizzato `str`,
      inizializzato a "" nell'`__init__` interno di `_build_bundle_index`.
      Downstream reader (persist, router, validator) NON ricalcolano il
      site_id: leggono `bundle.site_id` (in-memory) oppure
      `plenora_metadata.json["site_id"]` (scritto da persist al save) come
      fonte unica. Ogni riderivazione via scansione dir e' un'euristica
      divergente proibita dall'invariant.
    """

    tmp_dir: Path
    # Vedi class docstring "Invariant site_id" (D3, R4
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

    def manifest(self) -> JsonObject:
        if self.manifest_path and self.manifest_path.exists():
            return json.loads(self.manifest_path.read_text(encoding="utf-8"))
        return {}

    def validation(self) -> JsonObject:
        if self.validation_path and self.validation_path.exists():
            return json.loads(self.validation_path.read_text(encoding="utf-8"))
        return {}

    def renderers(self) -> JsonObject:
        if self.renderers_path and self.renderers_path.exists():
            return json.loads(self.renderers_path.read_text(encoding="utf-8"))
        return {}


def _extract_zip_source_to_bundle(
    zip_source: Union[str, os.PathLike, io.BytesIO], tmp: Path,
) -> ImdfBundle:
    """Internal: estrae uno ZipFile-compatible `zip_source` dentro `tmp`
    e costruisce il bundle indicizzato.

    Il caller e' responsabile della creazione di `tmp` e del cleanup
    su exception (per le funzioni pubbliche `extract_imdf_zip*` quel
    cleanup e' fatto dal wrapper, su success la cleanup passa al
    bundle via `bundle.cleanup`).
    """
    with zipfile.ZipFile(zip_source) as z:
        members = z.namelist()
        safe_extractall(z, tmp)
    return _build_bundle_index(tmp, members)


def extract_imdf_zip(zip_path: str | os.PathLike, dest_dir: Optional[str | os.PathLike] = None) -> ImdfBundle:
    """Estrae uno zip IMDF da un path su disco e indicizza i contenuti.

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
    # passa al caller via `bundle.cleanup`. Senza il try/except, una
    # eccezione (zipfile corrotto, ValueError IMDF non valido) dopo
    # `mkdtemp` lascerebbe la tmp dir su disco
    _we_own_tmp = dest_dir is None
    tmp = Path(dest_dir) if dest_dir else Path(tempfile.mkdtemp(prefix="imdf_"))
    tmp.mkdir(parents=True, exist_ok=True)

    try:
        return _extract_zip_source_to_bundle(src, tmp)
    except Exception:
        if _we_own_tmp:
            shutil.rmtree(tmp, ignore_errors=True)
        raise


def extract_imdf_zip_bytes(data: bytes) -> ImdfBundle:
    """Estrae uno zip IMDF da bytes in RAM e indicizza i contenuti.

    Variante bytes-first di `extract_imdf_zip`: chiude l'escape hatch
    `temporary_path_from_bytes` lato caller. Lo zip stesso e' aperto
    via `ZipFile(BytesIO(data))`, senza materializzare il file zip su
    disco.

    Internamente la tmp dir resta necessaria SOLO per le componenti
    estratte che richiedono filesystem (il File Geodatabase `.gdb`
    e' una directory letta da pyogrio via path). IMDF-2 spostera'
    anche manifest/validation/renderers/geojson in-memory e
    introdurra' una nuova shape `ImdfBundle.files: dict[str, bytes]`;
    IMDF-1 chiude solo il bounce nel caller.

    Args:
        data: bytes dello zip IMDF.

    Returns:
        ImdfBundle popolato (tmp_dir-based come `extract_imdf_zip`).

    Raises:
        ValueError: se `data` e' vuoto o lo zip non e' un IMDF valido.
    """
    if not data:
        raise ValueError("IMDF bytes vuoti")

    tmp = Path(tempfile.mkdtemp(prefix="imdf_"))
    tmp.mkdir(parents=True, exist_ok=True)

    try:
        return _extract_zip_source_to_bundle(io.BytesIO(data), tmp)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def _build_bundle_index(tmp: Path, members: List[str]) -> "ImdfBundle":
    """Indicizza i contenuti noti di un bundle IMDF gia' estratto in `tmp`.

    Separato da `extract_imdf_zip` per wrappare l'intero ciclo
    estrazione + indicizzazione in un singolo try/cleanup. Anche la
    validazione finale `raise ValueError("non sembra un IMDF")` deve
    avvenire sotto il guard del cleanup, altrimenti la tmp dir
    resterebbe su disco.
    """
    bundle = ImdfBundle(tmp_dir=tmp, site_id="", raw_filelist=members)

    # Indicizza i contenuti noti
    # Convenzione del servizio
    # {SITE_ID}.gdb/ cartella File Geodatabase
    # {SITE_ID}.ifc file IFC al root
    # {SITE_ID}/ cartella IMDF (geojson + manifest IMDF
    # manifest.json manifest del processing (root
    # validation.json (root
    # renderers.json (root
    site_id_candidates = set()

    for m in members:
        p = tmp / m
        # Il GDB e' una "cartella" dentro lo zip: i membri sono path tipo
        # "LO2244.gdb/a00000001.gdbtable". Estraggo il prefisso fino a `.gdb/`
        # come cartella GDB
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
    import pyogrio
    from shapely.ops import transform as shapely_transform

    p = Path(gdb_path)
    if not p.exists():
        raise FileNotFoundError(f"GDB non trovato: {p}")

    available = {name: gtype for name, gtype in pyogrio.list_layers(str(p))}

    result: Dict[str, Any] = {}
    for layer_name in EXPECTED_LAYERS:
        if layer_name not in available:
            continue
        gdf = load_dataframe_from_file(p, layer=layer_name)

        # Riproiezione
        if gdf.crs and target_srid:
            try:
                current = gdf.crs.to_epsg()
                if current and current != target_srid:
                    gdf = gdf.to_crs(epsg=target_srid)
            except Exception:
                # CRS incompatibile o riproiezione fallita: lascia originale
                # il chiamante gestira'
                pass

        # Drop Z (geometrie 3D → 2D
        if drop_z and len(gdf) > 0:
            def _drop_z(geom):
                if geom is None or not geom.has_z:
                    return geom
                return shapely_transform(lambda x, y, z=None: (x, y), geom)

            try:
                gdf = gdf.set_geometry(gdf.geometry.apply(_drop_z))
            except Exception as _ze:
                # Geometria malformata o shapely senza has_z: lascia GDF
                # invariato (2D o mix). Non-fatal per il caller
                logger.debug(
                    f"drop_z skipped for layer '{layer_name}': {type(_ze).__name__}: {_ze}"
                )

        # Filtra colonne di sistema ESRI: non vanno mai inviate al create_service
        sys_cols = [c for c in gdf.columns if c in ESRI_SYSTEM_FIELDS]
        if sys_cols:
            gdf = gdf.drop(columns=sys_cols)

        result[layer_name] = gdf

    return result
