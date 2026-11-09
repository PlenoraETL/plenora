"""Chi referenzia un utente: lo storico non si perde e non si riscrive (09/11/2026).

Caso reale: l'esecutore «E0071» ha lanciato un Eseguibile; DELETE
/auth/users/{id} falliva con 500 perche' ``eseguibile_runs.user_id`` lo
referenzia senza ON DELETE. Regola: prima di cancellare si contano le righe
che referenziano l'utente con una chiave esterna SENZA ON DELETE (tabella
dichiarativa ``USER_REFERENCES``); se ce ne sono, 409 ``user_has_history``
con i conteggi per tipo e l'invito a disattivarlo. Una ForeignKeyViolation
residua (riferimento nuovo non ancora in tabella, riga scritta tra conteggio e
cancellazione) diventa comunque 409, mai 500.

Le chiavi esterne verso ``users`` con ON DELETE (CASCADE o SET NULL) le
gestisce il DB e non bloccano: sono dichiarate in ``USER_REFERENCES_BY_DB``.
Il test ``tests2/auth/test_user_references_contract.py`` enumera le chiavi
esterne reali di migrazioni e schema e le confronta con le due tabelle: una
chiave nuova non dichiarata fa fallire il test.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypedDict


@dataclass(frozen=True)
class UserReference:
    table: str
    column: str
    one: str   # «1 esecuzione registrata»
    many: str  # «3 esecuzioni registrate»

    @property
    def key(self) -> str:
        return f"{self.table}.{self.column}"

    def label(self, count: int) -> str:
        return f"{count} {self.one if count == 1 else self.many}"


class UserReferenceCount(TypedDict):
    """Un tipo di riferimento presente: tabella, colonna, quante righe, testo."""

    table: str
    column: str
    count: int
    label: str


# Chiavi esterne verso users SENZA ON DELETE: bloccano la cancellazione.
USER_REFERENCES: tuple[UserReference, ...] = (
    UserReference("eseguibile_runs", "user_id", "esecuzione registrata", "esecuzioni registrate"),
    UserReference("projects", "created_by", "progetto creato", "progetti creati"),
    UserReference("eseguibili", "created_by", "Eseguibile creato", "Eseguibili creati"),
    UserReference("eseguibile_versions", "created_by", "versione di Eseguibile pubblicata", "versioni di Eseguibili pubblicate"),
    UserReference("schedules", "created_by", "pianificazione creata", "pianificazioni create"),
    UserReference("connections", "created_by", "connessione creata", "connessioni create"),
    UserReference("api_connections", "created_by", "API esterna creata", "API esterne create"),
    UserReference("arcgis_connections", "created_by", "connessione ArcGIS creata", "connessioni ArcGIS create"),
    UserReference("datasources", "created_by", "Data Source creato", "Data Source creati"),
    UserReference("file_sources", "created_by", "file caricato", "file caricati"),
    UserReference("tickets", "created_by", "ticket aperto", "ticket aperti"),
    UserReference("tickets", "assignee", "ticket assegnato", "ticket assegnati"),
    UserReference("ticket_comments", "author_id", "commento a un ticket", "commenti ai ticket"),
    UserReference("ticket_attachments", "uploaded_by", "allegato a un ticket", "allegati ai ticket"),
)

# Chiavi esterne verso users CON ON DELETE: le gestisce il DB.
USER_REFERENCES_BY_DB: Mapping[tuple[str, str], str] = {
    ("user_mfa", "user_id"): "CASCADE",             # segreti MFA dell'utente
    ("user_mfa_used_codes", "user_id"): "CASCADE",  # codici MFA gia' usati
    ("connection_sets", "created_by"): "SET NULL",
}


def count_sql() -> str:
    """Una query: per ogni riferimento, quante righe referenziano l'utente.
    Nomi di tabella e colonna dalla tabella dichiarativa (costanti)."""
    return " UNION ALL ".join(
        f"SELECT '{ref.key}' AS ref, COUNT(*) AS n FROM {ref.table} WHERE {ref.column} = %s"
        for ref in USER_REFERENCES
    )


def count_user_references(
    user_id: str,
    fetchall: Callable[[str, tuple], Sequence[Mapping[str, Any]]],
) -> list[UserReferenceCount]:
    """I riferimenti presenti (conteggio > 0), nell'ordine della tabella."""
    rows = fetchall(count_sql(), tuple(user_id for _ in USER_REFERENCES))
    counts = {str(row["ref"]): int(row["n"] or 0) for row in rows}
    return [
        UserReferenceCount(table=ref.table, column=ref.column, count=counts[ref.key], label=ref.label(counts[ref.key]))
        for ref in USER_REFERENCES
        if counts.get(ref.key, 0) > 0
    ]


def _join(parts: Sequence[str]) -> str:
    return parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} e {parts[-1]}"


def history_message(username: str, references: Sequence[Mapping[str, Any]]) -> str:
    """«L'utente «E0071» ha 1 esecuzione registrata: per non perdere lo
    storico non si può eliminare. Disattivalo.»"""
    if not references:
        return (
            f"L'utente «{username}» è collegato a dati registrati: per non perdere lo storico "
            "non si può eliminare. Disattivalo."
        )
    what = _join([str(ref["label"]) for ref in references])
    return f"L'utente «{username}» ha {what}: per non perdere lo storico non si può eliminare. Disattivalo."


class UserHasHistoryError(Exception):
    """La cancellazione ha violato una chiave esterna verso users."""
