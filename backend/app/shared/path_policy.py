"""Path policy minimale: validazione fail-closed di un path utente
contro una whitelist di trusted roots.

Sostituisce il pattern duplicato in 6 call site:
- `app/core/artifacts/resolver.py:_materialize_from_output_path`
- `app/services/file_service.py:240` (zip member validation)
- `app/core/imdf/router.py:_safe_bundle_dir`
- `app/core/features/routes.py:_resolve_input_path`
- `app/core/datasources/local.py:absolute_path`
- `app/core/files/router.py:_assert_path_within`

Tutti facevano `Path.resolve() + relative_to(...)` con UNC reject
preventivo; questo modulo centralizza la logica in **una sola
funzione pura**, niente classi, niente framework di astrazione.

Non esporta helper accessori (es. get_trusted_data_roots): se un
caller vuole costruire una lista di root da settings, lo fa nel
suo namespace. Centralizziamo il check, non la composizione.

`validate_imdf_task_id` (D3 commit 1) sposta qui la regex +
path-traversal guard che viveva triplicata in router/validator/
worker IMDF. Stesso principio "centralizziamo il check":
la funzione e' pura, caller traducono `ValueError` nel proprio
contratto di errore (HTTPException, report issue, Optional return).
"""

from __future__ import annotations

import re
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
       di qualsiasi `resolve()`: su Windows resolve() puo' tentare
       il lookup SMB introducendo latenze di rete e side-channel
       temporali.
    2. `Path.resolve()` su candidate e su ogni trusted root.
    3. Verifica che il candidate resolved sia dentro almeno un root
       resolved (via `relative_to`).
    4. Se `must_exist=True`, verifica che il file esista.

    Solleva `ValueError` con messaggio fail-closed standard. I
    caller con test caratterizzanti che asseriscono un wording
    specifico catturano e re-raise con il loro messaggio.

    Ritorna il `Path` resolved (utile al caller per evitare un
    secondo `resolve()`).

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
# (no `-`/`_` iniziale). Esclude `.`, `/`, `\`, spazi, null bytes e caratteri
# di controllo — sufficiente per bloccare path traversal.
_IMDF_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def validate_imdf_task_id(bundle_task_id: str, root: Path) -> Path:
    """Valida formato `bundle_task_id` IMDF + path traversal contro `root`.

    Due livelli di difesa:
      1. Regex whitelist `_IMDF_TASK_ID_RE` sul formato (no slash, no dots,
         no null bytes, max 128 char, primo char alfanumerico).
      2. `assert_path_within(root / bundle_task_id, [root])` come safety
         net — se nonostante la regex l'input sfugge, solleva `ValueError`.

    Ritorna il `Path` resolved del bundle dir (utile al caller per evitare
    un secondo `resolve()`). NON verifica che la dir esista — quello e'
    responsabilita' del caller (check `BUNDLE_MISSING` o simile).

    Args:
        bundle_task_id: stringa da validare.
        root: directory root `IMDF_BUNDLES_DIR` (Path, preferibilmente
            gia' resolved dal caller).

    Raises:
        ValueError: id vuoto, formato non conforme alla regex, oppure
            path resolved fuori da `root`.
    """
    if not bundle_task_id or not _IMDF_TASK_ID_RE.match(bundle_task_id):
        raise ValueError(
            f"bundle_task_id non valido (ammessi solo [A-Za-z0-9_-], "
            "primo char alfanumerico, max 128 char)"
        )
    return assert_path_within(root / bundle_task_id, [root])
