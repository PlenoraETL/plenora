"""
Policy Engine per lo Scheduler PLENORA-ETL.

Keeps this module's responsibility explicit and isolated from adjacent code.
"""

from typing import Optional, Tuple, Literal
from app.utils.logger import get_logger

logger = get_logger(__name__)

from app.core.scheduler.models import (
    ScheduleConfig,
    ScheduleErrorPolicy,
    ScheduleConflictPolicy,
)


class PolicyEngine:
    """
    Motore decisionale per retry e conflitti.
    
    Stateless: riceve la config e lo stato corrente,
    restituisce la decisione senza effetti collaterali.
    """
    
    # RETRY
    
    @staticmethod
    def should_retry(
        error_policy: ScheduleErrorPolicy,
        current_attempt: int
    ) -> Tuple[bool, int]:
        """
        Determina se fare retry e dopo quanto tempo.
        
        Args:
            error_policy: Configurazione retry dallo schedule
            current_attempt: Tentativo corrente (0-based: 0 = primo run)
        
        Returns:
            (should_retry: bool, wait_seconds: int)
            - (True, 60) → retry tra 60 secondi
            - (False, 0) → niente retry, errore definitivo
        """
        if current_attempt >= error_policy.max_retries:
            logger.info(f"Retry esauriti ({current_attempt}/{error_policy.max_retries})")
            return False, 0
        
        # Prendi il backoff per questo tentativo
        backoff_list = error_policy.retry_backoff_seconds
        if current_attempt < len(backoff_list):
            wait = backoff_list[current_attempt]
        else:
            # Se ci sono più retry che backoff, usa l'ultimo valore
            wait = backoff_list[-1] if backoff_list else 60
        
        logger.info(f"Retry programmato: tentativo {current_attempt + 1}/{error_policy.max_retries}, attesa {wait}s")
        return True, wait
    
    # CONFLICT RESOLUTION
    
    @staticmethod
    def resolve_conflict(
        conflict_policy: ScheduleConflictPolicy
    ) -> Literal["skip", "force", "abort"]:
        """
        Determina come gestire i conflitti CARICA.
        
        In esecuzione automatica la policy è già decisa dall'utente
        nel wizard. Questo metodo la restituisce per l'execution_manager.
        
        Returns:
            "skip" → inserisci solo righe OK
            "force" → sovrascrivi conflitti
            "abort" → interrompi
        """
        return conflict_policy.append_conflicts
    
    @staticmethod
    def should_log_skipped(conflict_policy: ScheduleConflictPolicy) -> bool:
        """Determina se loggare le righe skipped."""
        return conflict_policy.log_skipped_rows
    
    # VALUTAZIONE RISULTATO
    
    @staticmethod
    def evaluate_result(
        success: bool,
        rows_written: int = 0,
        rows_skipped: int = 0,
        error_message: Optional[str] = None
    ) -> Literal["completed", "partial", "failed"]:
        """
        Valuta il risultato di un'esecuzione e determina lo stato finale.
        
        Args:
            success: Pipeline completata senza errori fatali?
            rows_written: Righe scritte con successo
            rows_skipped: Righe saltate per conflitti
            error_message: Eventuale messaggio di errore
        
        Returns:
            "completed" → tutto OK
            "partial" → completato ma con righe skipped
            "failed" → errore fatale
        """
        if not success:
            return "failed"
        
        if rows_skipped > 0:
            logger.info(f"Esecuzione parziale: {rows_written} scritte, {rows_skipped} saltate")
            return "partial"
        
        return "completed"
    
    # DECISIONE COMPLETA
    
    @staticmethod
    def decide_after_failure(
        config: ScheduleConfig,
        current_attempt: int,
        error_message: str
    ) -> dict:
        """
        Decisione completa dopo un fallimento.
        
        Returns:
            {
                "action": "retry" | "give_up",
                "wait_seconds": int,
                "attempt": int,
                "event": "RETRY_SCHEDULED" | "RETRY_EXHAUSTED",
                "log_level": "WARNING" | "ERROR"
            }
        """
        should, wait = PolicyEngine.should_retry(
            config.on_error, current_attempt
        )
        
        if should:
            return {
                "action": "retry",
                "wait_seconds": wait,
                "attempt": current_attempt + 1,
                "event": "RETRY_SCHEDULED",
                "log_level": "WARNING"
            }
        else:
            return {
                "action": "give_up",
                "wait_seconds": 0,
                "attempt": current_attempt,
                "event": "RETRY_EXHAUSTED",
                "log_level": "ERROR"
            }
    
    @staticmethod
    def decide_after_success(
        rows_written: int = 0,
        rows_skipped: int = 0
    ) -> dict:
        """
        Decisione completa dopo un successo.
        
        Returns:
            {
                "status": "completed" | "partial",
                "event": "PIPELINE_COMPLETED" | "PIPELINE_PARTIAL",
                "log_level": "INFO" | "WARNING"
            }
        """
        status = PolicyEngine.evaluate_result(True, rows_written, rows_skipped)
        
        if status == "partial":
            return {
                "status": "partial",
                "event": "PIPELINE_PARTIAL",
                "log_level": "WARNING"
            }
        else:
            return {
                "status": "completed",
                "event": "PIPELINE_COMPLETED",
                "log_level": "INFO"
            }
