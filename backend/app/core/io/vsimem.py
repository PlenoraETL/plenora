"""
GDAL `/vsimem` adapter via ctypes — boundary I/O orizzontale.

Primitive boundary per flussi multi-file RAM-only come Shapefile sidecar,
GDB read e formati path-only.

Espone API minimali contro `libgdal.so` (bundled da pyogrio) senza
passare per le Python bindings `osgeo` (non installabili nel container
backend: `pip install gdal==3.13.0` fallisce per mancanza di toolchain).

API:
  - `vsi_register_bytes(vsi_path, data)` — context manager: registra
    `data` come file VSI in `/vsimem/...` e cleanup al __exit__.
  - `vsi_temp_dir(prefix)` — context manager: crea una `/vsimem/{prefix}_{uuid}/`
    dir + cleanup ricorsivo. Per writer multi-file (Shapefile sidecar).
  - `vsi_read_bytes(vsi_path)` — legge bytes da un path VSI (post-write).
  - `vsi_listdir(vsi_path)` — lista entry di una /vsimem/ dir.
  - `vsi_mkdir(vsi_path)` — crea una /vsimem/ dir (no parents).
  - `vsi_rmdir_recursive(vsi_path)` — rmtree ricorsivo VSI.

WHY ctypes:
  - pyogrio bundle GDAL in `/opt/gdal/lib/libgdal.so` (3.13.0).
  - pyogrio espone `vsi_listtree` / `vsi_unlink` / `vsi_rmtree` ma NON
    `mkdir` / `write bytes` / `read bytes` su /vsimem; non bastano
    per scrivere Shapefile sidecar in RAM.
  - GDAL VSI C API e' parte dell'ABI pubblica e stabile.

INVARIANTI:
  - `vsi_register_bytes` usa `bTakeOwnership=0`: il buffer ctypes
    (`from_buffer_copy`) resta vivo nello scope, GDAL non possiede
    memoria Python-managed.
  - Cleanup garantito da `finally` in ogni context manager.
"""

from __future__ import annotations

import ctypes
import threading
import uuid
from contextlib import contextmanager
from ctypes.util import find_library
from typing import Iterator, List, Optional

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
        discovered = find_library("gdal")
        candidates = _LIBGDAL_CANDIDATES + ((discovered,) if discovered else ())
        for cand in dict.fromkeys(candidates):
            try:
                lib = ctypes.CDLL(cand)
                break
            except OSError as exc:
                last_err = exc
        if lib is None:
            raise VsiAdapterError(
                f"libgdal non trovata. Tentativi: {list(dict.fromkeys(candidates))}. "
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
        # VSILFILE *VSIFOpenL(const char *path, const char *mode)
        lib.VSIFOpenL.restype = ctypes.c_void_p
        lib.VSIFOpenL.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        # size_t VSIFReadL(void *pBuffer, size_t nSize, size_t nCount, VSILFILE *fp)
        lib.VSIFReadL.restype = ctypes.c_size_t
        lib.VSIFReadL.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_void_p,
        ]
        # int VSIFCloseL(VSILFILE *fp)
        lib.VSIFCloseL.restype = ctypes.c_int
        lib.VSIFCloseL.argtypes = [ctypes.c_void_p]
        # int VSIStatL(const char *path, VSIStatBufL *psStatBuf)
        # Usiamo VSIStatExL con flag VSI_STAT_EXISTS_FLAG|SIZE_FLAG.
        # Per semplicita': solo size via seek-to-end pattern.
        # vsi_l_offset VSIFTellL(VSILFILE *fp)
        lib.VSIFTellL.restype = ctypes.c_uint64
        lib.VSIFTellL.argtypes = [ctypes.c_void_p]
        # int VSIFSeekL(VSILFILE *fp, vsi_l_offset nOffset, int nWhence)
        lib.VSIFSeekL.restype = ctypes.c_int
        lib.VSIFSeekL.argtypes = [ctypes.c_void_p, ctypes.c_uint64, ctypes.c_int]
        # int VSIUnlink(const char *path)
        lib.VSIUnlink.restype = ctypes.c_int
        lib.VSIUnlink.argtypes = [ctypes.c_char_p]
        # int VSIMkdir(const char *path, long mode)
        lib.VSIMkdir.restype = ctypes.c_int
        lib.VSIMkdir.argtypes = [ctypes.c_char_p, ctypes.c_long]
        # int VSIRmdirRecursive(const char *path)
        lib.VSIRmdirRecursive.restype = ctypes.c_int
        lib.VSIRmdirRecursive.argtypes = [ctypes.c_char_p]
        # char **VSIReadDir(const char *path) — NULL-terminated array of strings.
        lib.VSIReadDir.restype = ctypes.POINTER(ctypes.c_char_p)
        lib.VSIReadDir.argtypes = [ctypes.c_char_p]
        # void CSLDestroy(char **strlist)
        lib.CSLDestroy.restype = None
        lib.CSLDestroy.argtypes = [ctypes.POINTER(ctypes.c_char_p)]
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


def vsi_mkdir(vsi_path: str) -> None:
    """Crea una directory `/vsimem/...`. Non-recursive.

    Raises VsiAdapterError se `VSIMkdir` ritorna non-zero.
    """
    if not vsi_path.startswith("/vsimem/"):
        raise ValueError(
            f"vsi_mkdir: vsi_path deve iniziare con /vsimem/, dato: {vsi_path!r}"
        )
    lib = _resolve_libgdal()
    rc = lib.VSIMkdir(vsi_path.encode("utf-8"), 0o755)
    if rc != 0:
        raise VsiAdapterError(f"VSIMkdir({vsi_path}) rc={rc}")


def vsi_rmdir_recursive(vsi_path: str) -> None:
    """Cancella ricorsivamente una directory `/vsimem/...`. Idempotente:
    no-op se il path non esiste (rc != 0 viene loggato a debug)."""
    if not vsi_path.startswith("/vsimem/"):
        raise ValueError(
            f"vsi_rmdir_recursive: vsi_path deve iniziare con /vsimem/, "
            f"dato: {vsi_path!r}"
        )
    lib = _resolve_libgdal()
    rc = lib.VSIRmdirRecursive(vsi_path.encode("utf-8"))
    if rc != 0:
        logger.debug(f"VSIRmdirRecursive({vsi_path}) rc={rc}")


def vsi_listdir(vsi_path: str) -> List[str]:
    """Lista i nomi delle entry in `/vsimem/{dir}`. Ritorna sempre
    senza `.` / `..`. Lista vuota se la dir non esiste.
    """
    if not vsi_path.startswith("/vsimem/"):
        raise ValueError(
            f"vsi_listdir: vsi_path deve iniziare con /vsimem/, dato: {vsi_path!r}"
        )
    lib = _resolve_libgdal()
    arr = lib.VSIReadDir(vsi_path.encode("utf-8"))
    if not arr:
        return []
    try:
        out: List[str] = []
        i = 0
        while arr[i]:
            name = arr[i].decode("utf-8")
            if name not in (".", ".."):
                out.append(name)
            i += 1
        return out
    finally:
        lib.CSLDestroy(arr)


def vsi_read_bytes(vsi_path: str) -> bytes:
    """Legge i bytes di un file `/vsimem/...` interamente in RAM.

    Pensato per file piccoli/medi (Shapefile sidecar tipicamente < 50MB
    totali). Per file grandi usare API chunked separate.

    Raises:
        VsiAdapterError: se VSIFOpenL ritorna NULL (file inesistente o
            permission denied).
    """
    if not vsi_path.startswith("/vsimem/"):
        raise ValueError(
            f"vsi_read_bytes: vsi_path deve iniziare con /vsimem/, dato: {vsi_path!r}"
        )
    lib = _resolve_libgdal()
    fp = lib.VSIFOpenL(vsi_path.encode("utf-8"), b"rb")
    if not fp:
        raise VsiAdapterError(f"VSIFOpenL({vsi_path}, 'rb') ritornato NULL")
    try:
        # Seek to end per ottenere la size, poi rewind + read.
        lib.VSIFSeekL(fp, 0, 2)  # SEEK_END
        size = lib.VSIFTellL(fp)
        lib.VSIFSeekL(fp, 0, 0)  # SEEK_SET
        if size == 0:
            return b""
        buf = (ctypes.c_ubyte * size)()
        n = lib.VSIFReadL(ctypes.cast(buf, ctypes.c_void_p), 1, size, fp)
        if n != size:
            raise VsiAdapterError(
                f"VSIFReadL({vsi_path}): letti {n}/{size} bytes"
            )
        return bytes(buf)
    finally:
        lib.VSIFCloseL(fp)


@contextmanager
def vsi_temp_dir(prefix: str = "plenora_vsi_") -> Iterator[str]:
    """Crea una `/vsimem/{prefix}{uuid}/` dir + cleanup ricorsivo all'exit.

    Pensato per consumer multi-file in RAM (es. Shapefile sidecar:
    pyogrio crea `.shp/.shx/.dbf/.prj/.cpg` nella stessa dir e il
    caller li zippa via `vsi_read_bytes` + `zipfile.writestr`).

    Yields:
        Il path della dir VSI creata, senza trailing slash.
    """
    name = f"{prefix}{uuid.uuid4().hex}"
    vsi_path = f"/vsimem/{name}"
    vsi_mkdir(vsi_path)
    try:
        yield vsi_path
    finally:
        try:
            vsi_rmdir_recursive(vsi_path)
        except Exception as exc:
            logger.warning(f"vsi_temp_dir cleanup {vsi_path} failed: {exc}")


__all__ = [
    "VsiAdapterError",
    "vsi_listdir",
    "vsi_mkdir",
    "vsi_read_bytes",
    "vsi_register_bytes",
    "vsi_rmdir_recursive",
    "vsi_temp_dir",
]
