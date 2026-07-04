"""
Path policy minimale: validazione fail-closed di un path utente

contro una whitelist di trusted roots.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Union


def assert_path_within(
    candidate: Union[str, Path],
    trusted_roots: Iterable[Union[str, Path]],
    *,
    must_exist: bool = False,
) -> Path:
    """Valida che `candidate` sia dentro almeno un trusted root.

    Steps:
    1. Reject UNC paths (`\\\\server\\share`, `//server/share`) prima
       di qualsiasi `resolve`: su Windows resolve puo' tentare
       il lookup SMB introducendo latenze di rete e side-channel
       temporali.
    2. `Path.resolve` su candidate e su ogni trusted root.
    3. Verifica che il candidate resolved sia dentro almeno un root
       resolved (via `relative_to`).
    4. Se `must_exist=True`, verifica che il file esista.

    Solleva `ValueError` con messaggio fail-closed standard. I
    caller con test caratterizzanti che asseriscono un wording
    specifico catturano e re-raise con il loro messaggio.

    Ritorna il `Path` resolved (utile al caller per evitare un
    secondo `resolve`).

    Args:
        candidate: path da validare (stringa o Path)
        trusted_roots: iterable di directory considerate sicure.
            Vuoto → ValueError (caller bug, non si valida contro
            zero root).
        must_exist: se True, verifica che `candidate` esista come
            file/directory dopo resolve.

    Raises:
        ValueError: candidate UNC, fuori da tutti i root, oppure
            (se must_exist) inesistente.
    """
    if isinstance(candidate, str):
        if candidate.startswith(("\\\\", "//")):
            raise ValueError(f"path UNC non consentito: {candidate}")
        candidate_str = candidate
    else:
        candidate_str = str(candidate)
        if candidate_str.startswith(("\\\\", "//")):
            raise ValueError(f"path UNC non consentito: {candidate_str}")

    roots = [Path(r).resolve() for r in trusted_roots]
    if not roots:
        raise ValueError(
            "assert_path_within: trusted_roots vuoto (caller bug)"
        )

    try:
        resolved = Path(candidate).resolve()
    except Exception as e:
        raise ValueError(f"path non valido: {e}") from e

    for root in roots:
        try:
            resolved.relative_to(root)
            break
        except ValueError:
            continue
    else:
        raise ValueError(
            f"path '{candidate_str}' fuori dai trusted roots"
        )

    if must_exist and not resolved.exists():
        raise ValueError(f"path '{candidate_str}' non esiste")

    return resolved


# Regex whitelist per `bundle_task_id` IMDF. I task_id generati da Celery
# sono UUID v4; qualche task legacy puo' avere formato leggermente diverso
# quindi accettiamo alfanumerico + dash/underscore. Primo char alfanumerico
# no `-`/`_` iniziale). Esclude `.`, `/`, `\`, spazi, null bytes e caratteri
# di controllo — sufficiente per bloccare path traversal
