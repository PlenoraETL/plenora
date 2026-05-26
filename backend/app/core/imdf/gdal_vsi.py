"""
GDAL VSI adapter via ctypes (IMDF-5).

Espone `vsi_register_bytes` per registrare un buffer Python in
`/vsimem/{path}` SENZA passare dalle Python bindings osgeo (non
installabili nel container backend: `pip install gdal==3.13.0`
fallisce per mancanza di toolchain di compilazione).

WHY ctypes:
  - pyogrio bundle GDAL in `/opt/gdal/lib/libgdal.so` (3.13.0).
  - pyogrio espone `vsi_listtree` / `vsi_unlink` / `vsi_rmtree` ma NON
    una "write bytes to /vsimem" API. La sua `read_dataframe(BytesIO)`
    registra internamente a /vsimem ma non espone il path per
    sub-resource access (e.g. `/vsizip//vsimem/{auto_uuid}.zip/{gdb}`).
  - GDAL VSI C API e' parte dell'ABI pubblica e stabile.
    `VSIFileFromMemBuffer` registra bytes in `/vsimem`; `VSIUnlink`
    de-registra. Interop ctypes e' minimale e robusto.

USAGE:
    from app.core.imdf.gdal_vsi import vsi_register_bytes

    with vsi_register_bytes("/vsimem/plenora_imdf/abc/_gdb.zip", zip_bytes):
        gdf = pyogrio.read_dataframe(
            f"/vsizip//vsimem/plenora_imdf/abc/_gdb.zip/SITE_X.gdb",
            layer="Locale",
        )
    # vsi cleanup automatico (VSIUnlink) all'exit del context.

INVARIANTI:
  - Il `data: bytes` passato a `vsi_register_bytes` viene COPIATO in
    una ctypes buffer (`from_buffer_copy`). Il buffer ctypes resta
    vivo nello scope del context manager (riferimento esplicito).
  - GDAL viene chiamato con `bTakeOwnership=0`: NON possiede il
    buffer, quindi non tenta di liberare memoria Python-managed.
  - `VSIUnlink` viene chiamato sempre nel `finally` (anche su
    exception del consumer).
"""

from __future__ import annotations

import ctypes
import threading
from contextlib import contextmanager
from typing import Iterator, Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)

_LIBGDAL_LOCK = threading.Lock()
_libgdal: Optional[ctypes.CDLL] = None


# Path candidati di libgdal.so/dylib. L'ordine privilegia il bundle
# pyogrio (consistente con la versione GDAL che pyogrio usa per leggere).
_LIBGDAL_CANDIDATES: tuple[str, ...] = (
    "/opt/gdal/lib/libgdal.so",   # plenora backend container layout
    "libgdal.so",                 # system Linux fallback
    "libgdal.dylib",              # macOS
    "libgdal.so.39",              # major-version fallback (GDAL 3.x)
)


class VsiAdapterError(RuntimeError):
    """Errore dell'adapter ctypes GDAL VSI (libload o API call)."""


def _resolve_libgdal() -> ctypes.CDLL:
    """Carica libgdal + binda le signatures. Lazy + thread-safe.

    Raises:
        VsiAdapterError: se nessun candidato libgdal e' caricabile.
    """
    global _libgdal
    if _libgdal is not None:
        return _libgdal
    with _LIBGDAL_LOCK:
        if _libgdal is not None:
            return _libgdal
        last_err: Optional[Exception] = None
        lib: Optional[ctypes.CDLL] = None
        for cand in _LIBGDAL_CANDIDATES:
            try:
                lib = ctypes.CDLL(cand)
                break
            except OSError as exc:
                last_err = exc
        if lib is None:
            raise VsiAdapterError(
                f"libgdal non trovata. Tentativi: {list(_LIBGDAL_CANDIDATES)}. "
                f"Ultimo errore: {last_err!r}"
            )
        # VSILFILE *VSIFileFromMemBuffer(const char *path, GByte *data,
        #                                vsi_l_offset nDataLength, int bTakeOwnership)
        lib.VSIFileFromMemBuffer.restype = ctypes.c_void_p
        lib.VSIFileFromMemBuffer.argtypes = [
            ctypes.c_char_p,
            ctypes.c_void_p,
            ctypes.c_uint64,
            ctypes.c_int,
        ]
        # int VSIFCloseL(VSILFILE *fp)
        lib.VSIFCloseL.restype = ctypes.c_int
        lib.VSIFCloseL.argtypes = [ctypes.c_void_p]
        # int VSIUnlink(const char *path)
        lib.VSIUnlink.restype = ctypes.c_int
        lib.VSIUnlink.argtypes = [ctypes.c_char_p]
        _libgdal = lib
        return lib


@contextmanager
def vsi_register_bytes(vsi_path: str, data: bytes) -> Iterator[str]:
    """Registra `data` in `/vsimem/{vsi_path}` per la durata del ctx.

    Il buffer ctypes derivato da `data` (copia, `from_buffer_copy`)
    resta vivo durante il `yield`; al cleanup viene chiamato
    `VSIUnlink` e il buffer rilasciato dal GC Python.

    Args:
        vsi_path: path GDAL VSI, DEVE iniziare con `/vsimem/`.
        data: bytes da registrare. Tipicamente uno zip
            (.gdb.zip per IMDF-5).

    Yields:
        Il path VSI registrato (uguale a `vsi_path`), pronto per
        consumer pyogrio / GDAL.

    Raises:
        ValueError: se `vsi_path` non e' `/vsimem/`-prefixed.
        VsiAdapterError: se `VSIFileFromMemBuffer` ritorna NULL.
    """
    if not vsi_path.startswith("/vsimem/"):
        raise ValueError(
            f"vsi_register_bytes: vsi_path deve iniziare con /vsimem/, "
            f"dato: {vsi_path!r}"
        )

    lib = _resolve_libgdal()

    # Copia esplicita in ctypes buffer (bytes Python sono immutabili
    # e from_buffer fallirebbe). Il buffer e' la zona di memoria che
    # GDAL referenzia via puntatore: deve restare allocata.
    buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)

    handle = lib.VSIFileFromMemBuffer(
        vsi_path.encode("utf-8"),
        ctypes.cast(buf, ctypes.c_void_p),
        len(data),
        0,  # bTakeOwnership=0: NON cedere ownership a GDAL.
    )
    if not handle:
        raise VsiAdapterError(
            f"VSIFileFromMemBuffer fallita per {vsi_path!r} ({len(data)} bytes)"
        )
    # Chiudiamo il file handle (la registrazione del path persiste).
    lib.VSIFCloseL(handle)

    try:
        # Reference esplicita a `buf` per evitare GC durante il yield.
        # Il consumer (pyogrio) leggera' via il path; `buf` deve esistere.
        _ = buf  # noqa: F841 — keep-alive guard
        yield vsi_path
    finally:
        try:
            rc = lib.VSIUnlink(vsi_path.encode("utf-8"))
            if rc != 0:
                logger.debug(f"VSIUnlink({vsi_path}) rc={rc}")
        except Exception as exc:
            logger.warning(f"VSIUnlink({vsi_path}) raised: {exc}")
        # `buf` esce di scope qui -> GC Python libera la memoria.


__all__ = ["VsiAdapterError", "vsi_register_bytes"]
