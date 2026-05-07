"""
mime_validator.py — Validazione MIME degli upload (content sniffing).

Goal: rifiutare file il cui contenuto non corrisponde all'estensione
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Set


class MimeMismatchError(ValueError):
    """Raised quando contenuto file non coincide con estensione/allowlist."""


# MIME consentiti per estensione
# Per ciascuna estensione: insieme di MIME che `filetype` puo' tornare
# legittimamente. None significa "formato testuale, nessun magic byte
# atteso" (validato via fallback `_is_textual`).
#
# Note sui formati "wrapper":
#   - xlsx e' un container OOXML zippato → filetype puo' tornare
#     'application/zip' o il MIME OOXML specifico a seconda della
#     versione/struttura. Accettiamo entrambi.
#   - xls (vecchio Office) e' OLE2 compound → 'application/x-cfb'
#   - gpkg e' un container SQLite → 'application/x-sqlite3'
#   - shp/gdb arrivano sempre zippati → 'application/zip'

_EXPECTED_MIMES: dict[str, Optional[Set[str]]] = {
    # Immagini
    "png":  {"image/png"},
    "jpg":  {"image/jpeg"},
    "jpeg": {"image/jpeg"},
    "gif":  {"image/gif"},
    "webp": {"image/webp"},

    # Office
    "xlsx": {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/zip",  # filetype torna spesso questo per xlsx
    },
    "xls":  {"application/vnd.ms-excel", "application/x-cfb"},

    # Archivi / geospaziali binari
    "zip":  {"application/zip"},
    "gpkg": {"application/x-sqlite3", "application/vnd.sqlite3"},
    "shp":  {"application/zip"},
    "gdb":  {"application/zip"},
    "dxf":  None,  # DXF e' testo (ASCII format) — filetype non riconosce

    # Testuali (no magic, validati via _is_textual)
    "csv":     None,
    "json":    None,
    "geojson": None,
    "log":     None,
    "txt":     None,
}


# MIME bloccati ESPLICITAMENTE, sempre, qualunque estensione il file dichiari.
# `filetype` puo' tornare uno di questi se il file e' un eseguibile.
# Difesa contro: evil.exe rinominato in .png passa l'allowlist estensione
# ma `filetype` lo riconosce come binario eseguibile → rifiuta.
_ALWAYS_BLOCKED_MIMES = {
    "application/x-msdownload",     # Windows PE/EXE/DLL
    "application/x-executable",     # Linux ELF
    "application/x-mach-binary",    # macOS Mach-O
    "application/vnd.microsoft.portable-executable",  # alias PE
}


# Magic bytes per signature non sempre coperte da filetype (shebang,
# script). Check prima di ogni cosa.
_BLOCKED_SIGNATURES = (
    b"\x7fELF",            # Linux ELF
    b"MZ",                 # Windows PE/EXE/DLL
    b"\xca\xfe\xba\xbe",   # Mach-O fat binary
    b"\xcf\xfa\xed\xfe",   # Mach-O 64-bit
    b"\xce\xfa\xed\xfe",   # Mach-O 32-bit
    b"#!",                 # shebang (bash, python, perl, ...)
)


def _is_textual(content: bytes, sample_size: int = 4096) -> bool:
    """Anti-binary heuristic: i primi N byte parsano UTF-8/latin-1
    senza null bytes E con basso ratio di control chars.

    SCOPE: questo check rileva "binary mascherato da testo" (es. .exe
    rinominato .csv) — NON valida lo schema/struttura del formato. Un
    `.csv` con colonne mancanti, un `.json` malformato o un `.geojson`
    senza geometry vengono accettati qui (saranno rifiutati piu' avanti
    dal parser specifico).

    Il check ratio si applica SEMPRE (non solo nel path latin-1):
    bytes 0x01-0x08 sono UTF-8 validi ma non sono testo legittimo —
    un binary mascherato puo' decodificare UTF-8 senza errori."""
    sample = content[:sample_size]
    if b"\x00" in sample:
        return False

    # Tentativo decode (UTF-8 → latin-1 fallback). Se entrambi falliscono → binary.
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        try:
            sample.decode("latin-1")
        except UnicodeDecodeError:
            return False

    # Ratio control chars: indipendente dall'encoding, applichiamo
    # sempre. Sono "stampabili": tab (0x09), newline (0x0A), CR (0x0D),
    # tutto da 0x20 (space) in su.
    control = sum(
        1 for b in sample
        if b < 0x20 and b not in (0x09, 0x0A, 0x0D)
    )
    return control / max(1, len(sample)) < 0.05


def detect_mime(content: bytes) -> Optional[str]:
    """Sniffing MIME da magic bytes. Ritorna None se non riconosciuto
    (formato testuale o sconosciuto).

    Se `filetype` manca dal venv, solleva `MimeMismatchError` esplicito
    invece di fail-open silente (pre-fix il fallback aveva permesso
    deploy senza la lib di rendere ininfluente la validazione MIME).
    """
    try:
        import filetype
    except ImportError as e:
        raise MimeMismatchError(
            "filetype library non installata: validazione MIME "
            "impossibile. Installa `filetype` in requirements.txt e "
            "riavvia il processo."
        ) from e
    kind = filetype.guess(content[:8192])  # primi 8KB sono sufficienti
    return kind.mime if kind else None


def validate_no_executable(
    *,
    content: bytes,
    filename: str,
) -> None:
    """Modalita' "block-only": rifiuta esclusivamente contenuti che
    sembrano eseguibili (magic bytes / MIME). Accetta qualsiasi altro
    formato senza richiedere allowlist estensioni.

    Use case: endpoint dove l'utente carica qualsiasi file legittimo
    (data sources / file manager generici / preview), e l'unica difesa
    necessaria e' bloccare malware mascherato. Un'allowlist stretta
    bloccherebbe falsi positivi (.docx, .pdf, .png, .dxf, ecc.).

    Raises:
        MimeMismatchError: file vuoto, magic bytes eseguibile, MIME
            riconosciuto come eseguibile.
    """
    if not content:
        raise MimeMismatchError("File vuoto")

    head = content[:16]
    for sig in _BLOCKED_SIGNATURES:
        if head.startswith(sig):
            raise MimeMismatchError(
                f"File '{filename}': contenuto non consentito "
                f"(rilevata firma eseguibile)"
            )

    detected_mime = detect_mime(content)
    if detected_mime in _ALWAYS_BLOCKED_MIMES:
        raise MimeMismatchError(
            f"File '{filename}': MIME '{detected_mime}' bloccato "
            f"(eseguibile)"
        )


def validate_upload(
    *,
    content: bytes,
    filename: str,
    allowed_extensions: Set[str],
) -> str:
    """Valida che `content` corrisponda all'estensione di `filename` e
    sia in `allowed_extensions`. Ritorna l'estensione normalizzata
    (lowercase, no punto) per il caller.

    Raises:
        MimeMismatchError: se uno dei seguenti:
          - estensione assente o non in allowlist
          - magic bytes corrispondono a eseguibile noto
          - filetype riconosce il content ma il MIME non e' compatibile
            con l'estensione
          - estensione e' "binaria" nota (es. xlsx) ma filetype non
            riconosce o ritorna None
          - estensione e' "testuale" ma il content non e' testo
    """
    if not content:
        raise MimeMismatchError("File vuoto")

    ext = Path(filename).suffix.lstrip(".").lower()
    if not ext:
        raise MimeMismatchError(f"File '{filename}': estensione mancante")
    if ext not in allowed_extensions:
        raise MimeMismatchError(
            f"File '{filename}': estensione '{ext}' non consentita "
            f"(allowed: {sorted(allowed_extensions)})"
        )

    # 1) Blocco eseguibili PRIMA di tutto, qualunque sia l'estensione.
    head = content[:16]
    for sig in _BLOCKED_SIGNATURES:
        if head.startswith(sig):
            raise MimeMismatchError(
                f"File '{filename}': contenuto non consentito "
                f"(rilevata firma eseguibile)"
            )

    detected_mime = detect_mime(content)
    if detected_mime in _ALWAYS_BLOCKED_MIMES:
        raise MimeMismatchError(
            f"File '{filename}': MIME '{detected_mime}' bloccato "
            f"(eseguibile)"
        )

    # 2) L'estensione richiede MIME testuale → fallback heuristic.
    expected = _EXPECTED_MIMES.get(ext)
    if expected is None:
        # Formato testuale (csv/json/geojson/log/txt/dxf): se filetype
        # riconosce comunque qualcosa di binario, c'e' mismatch.
        if detected_mime is not None:
            raise MimeMismatchError(
                f"File '{filename}': estensione '{ext}' attesa testuale "
                f"ma contenuto e' '{detected_mime}'"
            )
        if not _is_textual(content):
            raise MimeMismatchError(
                f"File '{filename}': estensione '{ext}' testuale ma "
                f"contenuto e' binario / non UTF-8"
            )
        return ext

    # 3) Estensione binaria: filetype DEVE riconoscere e MIME DEVE
    #    matchare la lista attesa.
    if detected_mime is None:
        raise MimeMismatchError(
            f"File '{filename}': estensione '{ext}' richiede contenuto "
            f"binario riconosciuto, ma magic bytes non corrispondono "
            f"ad alcun formato noto"
        )
    if detected_mime not in expected:
        raise MimeMismatchError(
            f"File '{filename}': estensione '{ext}' attesa per MIME "
            f"{sorted(expected)} ma contenuto e' '{detected_mime}'"
        )

    return ext
