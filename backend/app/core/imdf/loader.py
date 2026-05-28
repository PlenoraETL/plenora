"""IMDF loader — estrazione zip da bytes + lettura File Geodatabase."""

from __future__ import annotations

import io
import json
import os
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.dataframes.frame_io import load_dataframe_from_file
from app.shared.contracts.payloads import JsonObject
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

    Shape RAM-first:
      - `files: dict[str, bytes]` per manifest/validation/renderers/geojson.
      - `gdb_zip_bytes` per il File Geodatabase ricomposto come sub-zip.
      - `ifc_bytes` per il file IFC opzionale.

    Gli accessor `manifest()` / `validation()` / `renderers()` leggono
    solo da `files`.
    Le properties `has_gdb` / `has_ifc` / `has_renderers` derivano da
    questa shape e sono il modo canonico per i consumer di interrogare il
    bundle.

    `tmp_dir` resta nel dataclass solo per stabilita' di cleanup verso
    caller gia' optional-aware; `extract_imdf_zip_bytes` produce sempre
    `tmp_dir=None`.

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

    tmp_dir: Optional[Path]
    # Vedi class docstring "Invariant site_id" (D3, R4
    site_id: str
    files: Dict[str, bytes] = field(default_factory=dict)
    gdb_zip_bytes: Optional[bytes] = None
    ifc_bytes: Optional[bytes] = None
    raw_filelist: List[str] = field(default_factory=list)

    def cleanup(self) -> None:
        if self.tmp_dir and self.tmp_dir.exists():
            from app.core.io.local_cleanup import rmtree_safe
            rmtree_safe(self.tmp_dir)

    @property
    def has_gdb(self) -> bool:
        """`.gdb` presente nel bundle come sub-zip RAM."""
        return self.gdb_zip_bytes is not None

    @property
    def has_ifc(self) -> bool:
        """`.ifc` presente nel bundle come bytes RAM."""
        return self.ifc_bytes is not None

    @property
    def has_renderers(self) -> bool:
        """`renderers.json` presente in RAM."""
        return "renderers.json" in self.files

    @property
    def has_manifest(self) -> bool:
        return "manifest.json" in self.files

    @property
    def has_validation(self) -> bool:
        return "validation.json" in self.files

    def manifest(self) -> JsonObject:
        raw = self.files.get("manifest.json")
        if raw is not None:
            return json.loads(raw.decode("utf-8"))
        return {}

    def validation(self) -> JsonObject:
        raw = self.files.get("validation.json")
        if raw is not None:
            return json.loads(raw.decode("utf-8"))
        return {}

    def renderers(self) -> JsonObject:
        raw = self.files.get("renderers.json")
        if raw is not None:
            return json.loads(raw.decode("utf-8"))
        return {}


_RAM_FIRST_MEMBERS: tuple[str, ...] = (
    "manifest.json",
    "validation.json",
    "renderers.json",
)


def _is_geojson_member(name: str) -> bool:
    """`{site_id}/foo.geojson` — gli IMDF GeoJSON vivono sotto la dir
    site_id. Filtriamo per estensione + presenza di `/` per evitare
    di catturare un eventuale `.geojson` al root (non standard).
    """
    return name.endswith(".geojson") and "/" in name


# Tombstone (QUERY-1 step 3): `extract_imdf_zip(path)` + helper interno
# `_extract_zip_source_to_bundle` rimossi. Quel path usava mkdtemp +
# safe_extractall su filesystem; tutti i caller production usano
# `extract_imdf_zip_bytes` (RAM-first, no tmp dir, no safe_extractall).


def extract_imdf_zip_bytes(data: bytes) -> ImdfBundle:
    """Estrae uno zip IMDF da bytes in RAM — IMDF-6 fully-RAM.

    No tmp dir, no extract su disco. I componenti vengono letti dal
    `ZipFile(BytesIO(data))` come bytes e popolano le rispettive shape
    RAM-first:

      - `bundle.files`: dict per JSON/geojson (`manifest.json`,
        `validation.json`, `renderers.json`, `{site_id}/*.geojson`).
      - `bundle.gdb_zip_bytes`: zip-in-zip dei membri `*.gdb/...`
        ricomposto nel layout consumato da `open_gdb_in_vsimem`, cosi'
        `persist_imdf_bundle` lo uploads direttamente come
        `_gdb.zip` su storage).
      - `bundle.ifc_bytes`: bytes del file `.ifc` se presente.

    `tmp_dir` resta `None`: i consumer post-IMDF-3 (`persist_imdf_bundle`)
    + IMDF-5 (`open_gdb_in_vsimem` per validator/publish) ragionano
    su bytes/storage, non hanno bisogno di un workspace su disco.

    Args:
        data: bytes dello zip IMDF.

    Returns:
        ImdfBundle popolato (no `tmp_dir`).

    Raises:
        ValueError: se `data` e' vuoto o lo zip non e' un IMDF valido.
    """
    if not data:
        raise ValueError("IMDF bytes vuoti")

    files: Dict[str, bytes] = {}
    ifc_bytes: Optional[bytes] = None

    # `open_zip_bytes_safe` valida i cap zip-bomb PRIMA di yield: i
    # bundle IMDF possono arrivare da upload utente, quindi la guard
    # e' obbligatoria. Defense-in-depth (l'upload pipeline gia' valida
    # il MIME, ma non l'inner ZIP bomb cap).
    from app.core.io.zip_guards import open_zip_bytes_safe

    # RAM-A (2026-05-26): ricomposizione GDB sub-zip in streaming.
    # Pre: accumuavamo `gdb_members: list[tuple[str, bytes]]` (peak
    # O(sum members)) e poi ricomponevamo il sub-zip. Post: scriviamo
    # member-by-member direttamente sul sub-zip mentre leggiamo il
    # source — peak O(max single member) invece di O(sum).
    gdb_buf = io.BytesIO()
    gdb_zip_writer: Optional[zipfile.ZipFile] = None
    gdb_member_count = 0

    with open_zip_bytes_safe(data) as z:
        members = z.namelist()
        try:
            for m in members:
                try:
                    if m in _RAM_FIRST_MEMBERS or _is_geojson_member(m):
                        files[m] = z.read(m)
                    elif ".gdb/" in m:
                        # Lazy init del sub-zip writer al primo .gdb member
                        # incontrato (evita ZIP header overhead se non ci
                        # sono .gdb members).
                        if gdb_zip_writer is None:
                            gdb_zip_writer = zipfile.ZipFile(
                                gdb_buf, "w", zipfile.ZIP_DEFLATED, allowZip64=True,
                            )
                        # Scrive direttamente: peak = singolo member,
                        # non accumulazione di tutti.
                        gdb_zip_writer.writestr(m, z.read(m))
                        gdb_member_count += 1
                    elif m.endswith(".ifc") and ifc_bytes is None:
                        ifc_bytes = z.read(m)
                except KeyError:
                    # Member listato in namelist ma non leggibile (corruzione
                    # parziale): skip silenzioso, _build_bytes_bundle_index
                    # decidera' se il bundle e' valido.
                    continue
        finally:
            if gdb_zip_writer is not None:
                gdb_zip_writer.close()

    gdb_zip_bytes: Optional[bytes] = None
    if gdb_member_count > 0:
        gdb_zip_bytes = gdb_buf.getvalue()

    return _build_bytes_bundle_index(
        members=members,
        files=files,
        gdb_zip_bytes=gdb_zip_bytes,
        ifc_bytes=ifc_bytes,
    )


def _build_bytes_bundle_index(
    *,
    members: List[str],
    files: Dict[str, bytes],
    gdb_zip_bytes: Optional[bytes],
    ifc_bytes: Optional[bytes],
) -> "ImdfBundle":
    """Indicizza un bundle IMDF da bytes (IMDF-6 fully-RAM).

    I consumer leggono via `bundle.files` (gia' popolato
    dall'estrazione) o via bytes campi (`gdb_zip_bytes`, `ifc_bytes`).

    Site_id derivato:
      1. da `manifest.json["sites"]` (primo key) se presente;
      2. fallback: prefisso della prima `.gdb/` o primo dir top-level
         con geojson, alphabetico.

    Raises:
        ValueError: se ne' .gdb ne' manifest sono presenti (formato
        non-IMDF).
    """
    bundle = ImdfBundle(
        tmp_dir=None,
        site_id="",
        files=files,
        gdb_zip_bytes=gdb_zip_bytes,
        ifc_bytes=ifc_bytes,
        raw_filelist=members,
    )

    site_id_candidates: set[str] = set()
    for m in members:
        if ".gdb/" in m:
            site_id_candidates.add(m.split(".gdb/", 1)[0].split("/")[-1])
        elif m.endswith(".ifc"):
            site_id_candidates.add(Path(m).stem)
        elif m.endswith(".geojson") and "/" in m:
            site_id_candidates.add(m.split("/", 1)[0])

    # Preferenza al manifest.
    manifest_raw = files.get("manifest.json")
    if manifest_raw:
        try:
            mf = json.loads(manifest_raw.decode("utf-8"))
            sites = mf.get("sites") or {}
            if isinstance(sites, dict) and sites:
                bundle.site_id = next(iter(sites.keys()))
        except Exception:
            pass

    if not bundle.site_id and site_id_candidates:
        bundle.site_id = sorted(site_id_candidates)[0]

    # Validity check minimo: deve avere o .gdb o manifest.
    if gdb_zip_bytes is None and "manifest.json" not in files:
        raise ValueError(
            "Lo zip non sembra un IMDF: manca sia .gdb sia manifest.json"
        )

    return bundle


# Tombstone (QUERY-1 step 3): `_build_bundle_index(tmp, members)` rimosso.
# Helper interno path-mode: derivava `*_path` su disco scorrendo i
# membri estratti via `safe_extractall`. Sostituito dalla variante
# bytes-mode `_build_bytes_bundle_index` (sopra) che ragiona su
# `bundle.files` + `gdb_zip_bytes` + `ifc_bytes` senza filesystem.


def load_imdf_layers(
    gdb_path: str | os.PathLike,
    target_srid: int = 4326,
    drop_z: bool = True,
):
    """Apre un File Geodatabase IMDF e ritorna i 5 layer come GeoDataFrames.

    Args:
        gdb_path: percorso alla cartella .gdb. Accetta sia path filesystem
            (es. ``/tmp/foo/SITE.gdb``) sia path GDAL VSI (es.
            ``/vsizip//vsimem/.../_gdb.zip/SITE.gdb``, IMDF-5
            `open_gdb_in_vsimem`).
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
        FileNotFoundError: se gdb_path non esiste (solo per path filesystem;
            per path VSI lascia che pyogrio sollevi DataSourceError).
    """
    import pyogrio
    from shapely.ops import transform as shapely_transform

    # IMDF-5: gestiamo sia path filesystem sia GDAL VSI string. I path
    # VSI (e.g. /vsizip//vsimem/... per .gdb in RAM) non supportano
    # `Path.exists()`: pyogrio risolve via GDAL VSI handler.
    gdb_path_str = str(gdb_path)
    is_vsi = gdb_path_str.startswith("/vsi")
    if is_vsi:
        p_for_pyogrio: object = gdb_path_str
    else:
        p = Path(gdb_path)
        if not p.exists():
            raise FileNotFoundError(f"GDB non trovato: {p}")
        p_for_pyogrio = p

    available = {
        name: gtype
        for name, gtype in pyogrio.list_layers(
            p_for_pyogrio if is_vsi else str(p_for_pyogrio)
        )
    }

    result: Dict[str, Any] = {}
    for layer_name in EXPECTED_LAYERS:
        if layer_name not in available:
            continue
        if is_vsi:
            # Bypass `load_dataframe_from_file`: usa pyogrio diretto.
            # `gpd.read_file` accetta VSI string, ma il wrapper canonico
            # ha guard `file_path.exists()` che fallisce su /vsi.
            gdf = pyogrio.read_dataframe(p_for_pyogrio, layer=layer_name)
        else:
            gdf = load_dataframe_from_file(p_for_pyogrio, layer=layer_name)

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
