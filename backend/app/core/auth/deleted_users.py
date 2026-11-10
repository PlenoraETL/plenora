"""Chi legge la tabella ``users``: tabella dichiarativa (10/11/2026).

Regola dell'utente eliminato «a lapide» (``app.shared.contracts.auth``):
eliminare un utente con storico lascia la riga con ``deleted_at``
valorizzato. Ogni SQL del backend che legge o scrive ``users`` e' qui, con il
suo tipo:

- ``operativa``: autenticazione, gestione, liste, selettori, conteggi. Il
  testo contiene ``NOT_DELETED_SQL``: una lapide non esiste;
- ``storica_nome``: mostra chi ha fatto qualcosa nello storico. Il nome passa
  da ``user_display_name_sql`` («utente eliminato (<username>)»);
- ``storica_riga``: legge la riga anche se e' una lapide, per una regola
  storica (es. ruolo di chi ha creato una risorsa). Mai per autenticare;
- ``tutte_le_righe``: vincoli che coprono tutte le righe (unicita' degli
  username, indice, cancellazione del gruppo), con il motivo;
- ``scrittura``: inserimento di un utente nuovo.

Il test ``tests2/auth/test_deleted_user_contract.py`` enumera gli SQL reali
(analisi del sorgente) e li confronta con :data:`USERS_SQL_SITES`: un punto
nuovo che legge ``users`` senza essere dichiarato, o dichiarato senza
rispettare il suo tipo, fa fallire il test.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SiteKind = Literal["operativa", "storica_nome", "storica_riga", "tutte_le_righe", "scrittura"]


@dataclass(frozen=True)
class UsersSqlSite:
    module: str  # percorso dal backend, es. "app/core/auth/db.py"
    name: str    # funzione (o costante di modulo) che contiene l'SQL
    kind: SiteKind
    reason: str


USERS_SQL_SITES: tuple[UsersSqlSite, ...] = (
    # Persistenza degli utenti (auth).
    UsersSqlSite("app/core/auth/db.py", "get_user", "operativa", "ogni autenticazione (token, worker, /me) e ogni permesso"),
    UsersSqlSite("app/core/auth/db.py", "get_user_by_username", "operativa", "login e seed"),
    UsersSqlSite("app/core/auth/db.py", "get_user_including_deleted", "storica_riga", "ruolo di chi ha creato una risorsa (ownership)"),
    UsersSqlSite("app/core/auth/db.py", "list_users", "operativa", "gestione utenti dell'admin"),
    UsersSqlSite("app/core/auth/db.py", "list_all_users", "operativa", "gestione utenti del superadmin"),
    UsersSqlSite("app/core/auth/db.py", "find_users_by_normalized_username", "tutte_le_righe",
                 "unicita' degli username: l'indice unico del DB copre anche le lapidi"),
    UsersSqlSite("app/core/auth/db.py", "get_username_index_status", "tutte_le_righe",
                 "collisioni che impediscono l'indice unico (copre tutte le righe)"),
    UsersSqlSite("app/core/auth/db.py", "create_user", "scrittura", "nuovo utente"),
    UsersSqlSite("app/core/auth/db.py", "update_user", "operativa", "modifica di un utente esistente"),
    UsersSqlSite("app/core/auth/db.py", "update_user_password", "operativa", "reset, cambio e rehash della password"),
    UsersSqlSite("app/core/auth/db.py", "delete_user", "operativa", "cancellazione fisica di un utente senza storico"),
    UsersSqlSite("app/core/auth/db.py", "TOMBSTONE_USER_SQL", "operativa", "eliminazione a lapide (solo un utente non gia' eliminato)"),
    UsersSqlSite("app/core/auth/db.py", "get_group_summary", "operativa", "conteggio degli utenti del gruppo"),
    # Cancellazione del gruppo: con il gruppo se ne vanno anche le lapidi.
    UsersSqlSite("app/db/group_cleanup.py", "_DELETE_GROUP_ROWS_SQL", "tutte_le_righe",
                 "eliminazione del gruppo: le righe del gruppo, lapidi comprese"),
    # Ticket.
    UsersSqlSite("app/core/tickets/db.py", "_ASSIGNABLE_USER_SQL", "operativa", "assegnatario di un ticket"),
    UsersSqlSite("app/core/tickets/db.py", "_LIST_COMMENTS_SQL", "storica_nome", "autore di un commento"),
    UsersSqlSite("app/core/tickets/db.py", "_LIST_ATTACHMENTS_SQL", "storica_nome", "chi ha caricato un allegato"),
    # Progetti.
    UsersSqlSite("app/core/projects/db.py", "list_canvas_versions", "storica_nome", "chi ha salvato una versione del canvas"),
    # Esito dei test delle connessioni.
    UsersSqlSite("app/shared/connection_tests/results.py", "_LOAD_SQL", "storica_nome", "chi ha testato la connessione"),
)

# Chi puo' leggere la riga di una lapide da Python (lettura storica).
HISTORICAL_ROW_READERS: frozenset[str] = frozenset({"app/core/auth/ownership.py"})
