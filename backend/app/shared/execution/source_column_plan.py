"""Proiezione delle sorgenti condivisa fra caricamento e inferenza."""


def source_column_plan(names: list[str], columns: list[dict]) -> list[tuple[str, str]]:
    """Seleziona i nomi visibili; piano vuoto significa passthrough dell'ingresso."""
    selected = []
    seen = set()
    for column in columns or []:
        name = column.get("name", "")
        if column.get("visible", True) and name in names and name not in seen:
            selected.append((name, column.get("alias") or name))
            seen.add(name)
    return selected
