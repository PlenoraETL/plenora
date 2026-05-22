-- Migration: temp file source TTL cleanup
--
-- Bug originale (plenora-bug-orchestration I2 P2):
--   `eseguibili/service.py:360` crea un FileSource con name="__exec_temp_..."
--   quando un eseguibile viene lanciato con parametri custom. Nessun
--   cleanup automatico: questi temp file restavano nel DB indefinitamente,
--   accumulandosi e gonfiando la tabella `file_sources` + lo storage
--   MinIO (se OBJECT_STORAGE policy attivo).
--
-- Post-fix: due colonne aggiuntive permettono di marcare le righe temp
-- e schedularne il cleanup via un Celery beat job.
--
--   - `is_temp` BOOLEAN: flag che marca la riga come temporanea
--   - `expires_at` TIMESTAMPTZ: scadenza dopo cui la riga puo' essere
--     cancellata da un sweep periodico
--
-- Il cleanup task (`workers/tasks/cleanup_temp_files_task.py`) gira ogni
-- ora via PlenoraBeatScheduler e fa `DELETE FROM file_sources WHERE
-- is_temp = true AND expires_at < now()`. Per `STORAGE_POLICY=
-- object_storage` il cleanup chiama prima `delete_file_source` per
-- garantire la cancellazione del blob MinIO; per BLOB-in-DB il DELETE
-- e' sufficiente.

ALTER TABLE file_sources
    ADD COLUMN IF NOT EXISTS is_temp BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE file_sources
    ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;

-- Index parziale: solo le righe is_temp=true sono interrogate dal
-- cleanup sweep. Le righe is_temp=false (la maggior parte) non
-- consumano spazio nell'index. Pattern equivalente a quello gia'
-- usato per `idx_file_sources_storage_key`.
CREATE INDEX IF NOT EXISTS idx_file_sources_temp_expires
    ON file_sources(expires_at) WHERE is_temp = true;
