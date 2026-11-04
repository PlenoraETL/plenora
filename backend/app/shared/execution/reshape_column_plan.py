"""Nomi delle colonne derivate condivisi con l'esecuzione reshape."""


from collections.abc import Collection


def melt_output_name(name: str, columns: Collection[str]) -> str:
    """Evita collisioni secondo il limite storico del runtime."""
    if name not in columns:
        return name
    for index in range(1, 100):
        candidate = f"{name}_{index}"
        if candidate not in columns:
            return candidate
    return name
