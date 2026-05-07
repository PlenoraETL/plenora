"""
Interfaccia astratta per la scrittura dati su destinazioni.

Architettura
============
Definisce il contratto (ABC) che tutte le implementazioni di destinazione
devono rispettare. Questo permette di avere diverse destinazioni con la
stessa interfaccia:

- DatabaseWriter (ora): scrive su database relazionali via SQLAlchemy
- FeatureServiceWriter (futuro): scrive su ArcGIS FeatureService via REST

Il pattern è Strategy: l'Executor sceglie l'implementazione corretta
in base al tipo di nodo CARICA nel canvas.

Flusso standard di un writer:
1. validate() → verifica pre-condizioni (connessione, permessi, schema)
2. get_existing_columns() → recupera schema tabella esistente (se c'è)
3. check_conflicts() → pre-check conflitti PK/UNIQUE (solo APPEND)
4. write() → scrittura effettiva con batch e transazioni
5. create_table() → creazione tabella (solo modo CREATE/REPLACE)

Ogni metodo può lanciare eccezioni specifiche che vengono catturate
dall'Executor e convertite in WriteResult con lo status appropriato.
"""

from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

from app.core.carica.models import (
    ColumnMapping,
    ConflictReport,
    WriteConfig,
    WriteResult,
    BatchProgress,
)


class DestinationWriter(ABC):
    """Interfaccia astratta per scrivere dati su una destinazione.

    Tutte le implementazioni ricevono la configurazione nel costruttore
    e implementano i metodi del ciclo di vita.

    Attributes:
        config: configurazione completa del nodo CARICA
        progress_callback: funzione opzionale per aggiornare il progresso
            (usata dal task Celery per aggiornare Redis)
    """

    def __init__(
        self,
        config: WriteConfig,
        progress_callback: Optional[Callable[[BatchProgress], None]] = None,
    ):
        self.config = config
        self.progress_callback = progress_callback

    def report_progress(self, progress: BatchProgress) -> None:
        """Notifica il progresso se c'è un callback registrato."""
        if self.progress_callback:
            self.progress_callback(progress)

    # METODI ASTRATTI — da implementare in ogni destinazione

    @abstractmethod
    def validate(self) -> Tuple[bool, str]:
        """Valida le pre-condizioni prima della scrittura.

        Verifica:
        - Connessione al database raggiungibile
        - Permessi sufficienti (CREATE TABLE, INSERT, UPDATE)
        - Schema/tabella validi
        - Colonne match esistenti (per UPDATE/UPSERT)

        Returns:
            Tupla (is_valid, error_message).
            Se is_valid è True, error_message è stringa vuota.
        """
        ...

    @abstractmethod
    def get_existing_columns(self) -> List[Dict[str, str]]:
        """Recupera lo schema della tabella destinazione se esiste.

        Returns:
            Lista di {"name": "col_name", "type": "col_type"} per ogni colonna.
            Lista vuota se la tabella non esiste.
        """
        ...

    @abstractmethod
    def table_exists(self) -> bool:
        """Verifica se la tabella destinazione esiste già.

        Returns:
            True se la tabella esiste, False altrimenti.
        """
        ...

    @abstractmethod
    def check_conflicts(
        self,
        df: pd.DataFrame,
        match_columns: List[str],
    ) -> ConflictReport:
        """Pre-check conflitti per la modalità APPEND.

        Passaggio 1: cerca duplicati interni nel DataFrame sorgente.
        Passaggio 2: cerca conflitti con record già presenti nel DB.

        Args:
            df: DataFrame con i dati da inserire.
            match_columns: colonne su cui verificare i conflitti.

        Returns:
            ConflictReport con dettagli dei conflitti trovati.
        """
        ...

    @abstractmethod
    def write(self, df: pd.DataFrame) -> WriteResult:
        """Esegue la scrittura effettiva dei dati.

        La modalità (CREATE, APPEND, UPDATE, UPSERT, TRUNCATE_INSERT)
        è definita in self.config.write_mode.

        La scrittura avviene in batch (self.config.batch_size) con
        strategia transazionale (self.config.transaction_mode).

        Ad ogni batch completato, chiama self.report_progress() per
        aggiornare il frontend in tempo reale.

        Args:
            df: DataFrame con i dati da scrivere.

        Returns:
            WriteResult con esito dell'operazione.
        """
        ...

    @abstractmethod
    def create_table(self, columns: List[ColumnMapping]) -> bool:
        """Crea la tabella destinazione con il DDL appropriato.

        Genera il CREATE TABLE con:
        - Tipi colonna convertiti nel dialetto SQL del database
        - Colonna geometria con tipo e SRID corretti
        - Indice spaziale (se create_spatial_index=True)

        Args:
            columns: lista mappature con tipi destinazione.

        Returns:
            True se la tabella è stata creata con successo.

        Raises:
            Exception se la creazione fallisce (permessi, sintassi, ecc.)
        """
        ...

    @abstractmethod
    def drop_table(self) -> bool:
        """Elimina la tabella destinazione se esiste.

        Usato internamente per REPLACE e TRUNCATE_INSERT.

        Returns:
            True se la tabella è stata eliminata (o non esisteva).
        """
        ...

    @abstractmethod
    def truncate_table(self) -> bool:
        """Svuota la tabella destinazione senza eliminarla.

        Usato per TRUNCATE_INSERT. Mantiene la struttura e gli indici.

        Returns:
            True se la tabella è stata svuotata con successo.
        """
        ...

    @abstractmethod
    def get_schemas(self) -> List[str]:
        """Recupera la lista degli schema disponibili nel database.

        Astrazione unificata:
        - PostgreSQL: schema (public, custom, ...)
        - Oracle: schema utente
        - MySQL/MariaDB: database
        - SQL Server: schema nel database (dbo, custom, ...)

        Returns:
            Lista di nomi schema.
        """
        ...

    @abstractmethod
    def get_tables(self, schema_name: str) -> List[str]:
        """Recupera la lista delle tabelle in uno schema.

        Args:
            schema_name: nome dello schema.

        Returns:
            Lista di nomi tabella.
        """
        ...
