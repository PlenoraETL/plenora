"""
Oracle DBMS_REDEFINITION replace strategy for DatabaseWriter.

The public DatabaseWriter method remains on ReplaceStrategiesMixin; this
module owns the Oracle-specific implementation so the generic replace
strategy file does not carry the DBMS_REDEFINITION flow.
"""

from __future__ import annotations

import logging
import math
import time
from typing import List

import pandas as pd
from sqlalchemy import text

from app.config import settings
from app.core.carica.models import ColumnMapping, WriteResult, WriteStatus
from app.core.carica.writer_batch_loop import run_batched_inserts
from app.utils.logger import get_logger, get_request_id, get_user_id

logger = get_logger("app.core.carica.writer")
_telemetry_logger = logging.getLogger("app.core.carica.writer")


def execute_oracle_redefinition(
    writer,
    df: pd.DataFrame,
    columns: List[ColumnMapping],
    result: WriteResult,
) -> WriteResult:
    """Oracle TRUNCATE_INSERT via DBMS_REDEFINITION."""
    src = writer.config.table_name.upper()
    schema = writer.config.schema_name.upper()
    interim = f"{src}__RDF"
    src_qualified = f'"{schema}"."{src}"'
    interim_qualified = f'"{schema}"."{interim}"'

    _telemetry_logger.info(
        f"Stage+swap dispatch: dialect={writer.db_type} path=oracle_redef "
        f"table={schema}.{src} rows={len(df)}",
        extra={
            "event": "stage_swap.dispatched",
            "dialect": writer.db_type,
            "path": "oracle_redef",
            "schema": schema,
            "table": src,
            "rows": len(df),
            "threshold": int(settings.CARICA_STAGE_SWAP_THRESHOLD or 0),
            "request_id": get_request_id(),
            "user_id": get_user_id(),
        },
    )

    insert_sql, _ = writer.ddl.generate_insert(schema, interim, columns)

    active_cols = [c for c in columns if not c.skip]
    batch_size = writer.config.batch_size
    total_rows = len(df)
    total_batches = math.ceil(total_rows / batch_size) if total_rows else 0
    result.batches_total = total_batches
    start_time = time.time()

    logger.info(
        f"[ORACLE REDEF] avvio per {src}: {total_rows} righe, "
        f"interim={interim}"
    )

    try:
        with writer.engine.connect() as conn:
            conn.execute(text(writer.ddl.generate_drop_if_exists(schema, interim)))
            conn.commit()
    except Exception as cleanup_err:
        logger.warning(
            "[ORACLE REDEF] cleanup difensivo interim orfana fallito, ignoro: %s: %s",
            type(cleanup_err).__name__,
            cleanup_err,
        )

    options_flag = None
    for opt in ("CONS_USE_PK", "CONS_USE_ROWID"):
        try:
            with writer.engine.connect() as conn:
                conn.execute(text(f"""
BEGIN
    DBMS_REDEFINITION.CAN_REDEF_TABLE(
        uname => '{schema}',
        tname => '{src}',
        options_flag => DBMS_REDEFINITION.{opt}
    );
END;"""))
                conn.commit()
            options_flag = opt
            break
        except Exception as e:
            logger.debug(
                f"[ORACLE REDEF] CAN_REDEF_TABLE({opt}) failed: {e}"
            )
            continue

    if options_flag is None:
        logger.warning(
            f"[ORACLE REDEF] tabella {src} non ammissibile per "
            f"DBMS_REDEFINITION (no PK, ROWID inutilizzabile, o "
            f"privilegio EXECUTE ON DBMS_REDEFINITION mancante). "
            f"Fallback a atomic DELETE+INSERT (stessa transazione, "
            f"no swap, OK per schema-preservation)."
        )
        return writer._truncate_insert_atomic(df, columns, result)

    try:
        create_sql = (
            f"CREATE TABLE {interim_qualified} AS "
            f"SELECT * FROM {src_qualified} WHERE 1=0"
        )
        with writer.engine.connect() as conn:
            conn.execute(text(create_sql))
            conn.commit()
    except Exception as e:
        logger.warning(
            f"[ORACLE REDEF] CREATE interim fallito: {e}. "
            f"Fallback a atomic."
        )
        return writer._truncate_insert_atomic(df, columns, result)

    try:
        with writer.engine.connect() as conn:
            conn.execute(text(f"""
BEGIN
    DBMS_REDEFINITION.START_REDEF_TABLE(
        uname => '{schema}',
        orig_table => '{src}',
        int_table => '{interim}',
        col_mapping => NULL,
        options_flag => DBMS_REDEFINITION.{options_flag}
    );
END;"""))
            conn.commit()
    except Exception as e:
        logger.warning(
            f"[ORACLE REDEF] START_REDEF_TABLE fallito: {e}. "
            f"Fallback a atomic."
        )
        try:
            with writer.engine.connect() as conn:
                conn.execute(text(writer.ddl.generate_drop_if_exists(schema, interim)))
                conn.commit()
        except Exception as cleanup_err:
            logger.warning(
                "[ORACLE REDEF] cleanup interim post-START_REDEF fail fallito, ignoro: %s: %s",
                type(cleanup_err).__name__,
                cleanup_err,
            )
        return writer._truncate_insert_atomic(df, columns, result)

    try:
        with writer.engine.connect() as conn:
            writer._oracle_setup(conn)
            conn.execute(text(f"DELETE FROM {interim_qualified}"))

            def _process_batch(batch_df, batch_num, _conn=conn):
                rows = writer._execute_batch(
                    _conn,
                    batch_df,
                    active_cols,
                    insert_sql,
                    "insert",
                    target_table=interim,
                )
                result.rows_written += rows
                result.rows_inserted += rows
                result.batches_completed = batch_num + 1

            run_batched_inserts(
                df=df,
                result=result,
                batch_size=batch_size,
                total_batches=total_batches,
                start_time=start_time,
                process_batch=_process_batch,
                report_progress=writer.report_progress,
            )

            conn.commit()

        with writer.engine.connect() as conn:
            conn.execute(text(f"""
DECLARE
    num_errors PLS_INTEGER;
BEGIN
    DBMS_REDEFINITION.COPY_TABLE_DEPENDENTS(
        uname => '{schema}',
        orig_table => '{src}',
        int_table => '{interim}',
        copy_indexes => DBMS_REDEFINITION.CONS_ORIG_PARAMS,
        copy_triggers => TRUE,
        copy_constraints => TRUE,
        copy_privileges => TRUE,
        ignore_errors => FALSE,
        num_errors => num_errors
    );
END;"""))
            conn.commit()

        with writer.engine.connect() as conn:
            conn.execute(text(f"""
BEGIN
    DBMS_REDEFINITION.FINISH_REDEF_TABLE(
        uname => '{schema}',
        orig_table => '{src}',
        int_table => '{interim}'
    );
END;"""))
            conn.commit()

        try:
            with writer.engine.connect() as conn:
                conn.execute(text(writer.ddl.generate_drop_if_exists(schema, interim)))
                conn.commit()
        except Exception as e:
            logger.warning(
                f"[ORACLE REDEF] DROP interim post-swap fallito "
                f"(orfano con dati vecchi, cleanup al prossimo run): {e}"
            )

        result.status = WriteStatus.SUCCESS
        logger.info(
            f"[ORACLE REDEF] completato: {result.rows_written}/"
            f"{total_rows} righe in {time.time() - start_time:.1f}s "
            f"(atomic via DBMS_REDEFINITION, schema preservato)"
        )
        return result

    except Exception as e:
        logger.error(
            f"[ORACLE REDEF] fallimento post-START: {e}", exc_info=True
        )
        try:
            with writer.engine.connect() as conn:
                conn.execute(text(f"""
BEGIN
    DBMS_REDEFINITION.ABORT_REDEF_TABLE(
        uname => '{schema}',
        orig_table => '{src}',
        int_table => '{interim}'
    );
END;"""))
                conn.commit()
        except Exception as ab_err:
            logger.warning(
                f"[ORACLE REDEF] ABORT_REDEF_TABLE fallito: {ab_err}"
            )
        try:
            with writer.engine.connect() as conn:
                conn.execute(text(writer.ddl.generate_drop_if_exists(schema, interim)))
                conn.commit()
        except Exception as cleanup_err:
            logger.warning(
                "[ORACLE REDEF] drop interim post-ABORT fallito, ignoro: %s: %s",
                type(cleanup_err).__name__,
                cleanup_err,
            )

        result.rows_written = 0
        result.rows_inserted = 0
        result.rows_updated = 0
        result.status = WriteStatus.WRITE_ERROR
        result.error_message = f"DBMS_REDEFINITION fallimento: {e}"
        result.error_details = repr(e)
        return result
