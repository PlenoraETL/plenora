-- =============================================================================
-- Migration: Aggiunta indici mancanti per performance query
-- Data: 2026-04-12
-- Applicare con: psql -U <user> -d <db> -f 2026-04-12_add_missing_indexes.sql
-- Tutti gli indici usano IF NOT EXISTS per essere idempotenti.
-- =============================================================================

-- TIER 1: Alto impatto — query più frequenti
-- -----------------------------------------------------------------------------

-- Users: filtro per group_id in list_users
CREATE INDEX IF NOT EXISTS idx_users_group ON plenora.users(group_id);

-- Projects: ORDER BY updated_at DESC (8+ query)
CREATE INDEX IF NOT EXISTS idx_projects_updated_at ON plenora.projects(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_projects_group_updated_at ON plenora.projects(group_id, updated_at DESC);

-- File sources: ORDER BY created_at DESC
CREATE INDEX IF NOT EXISTS idx_file_sources_created_at ON plenora.file_sources(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_file_sources_group_created_at ON plenora.file_sources(group_id, created_at DESC);

-- Execution logs: filter group + sort date
CREATE INDEX IF NOT EXISTS idx_execution_logs_group_started ON plenora.execution_logs(group_id, started_at DESC);

-- Runs (intermediate): query per canvas + timestamp
CREATE INDEX IF NOT EXISTS idx_runs_canvas_timestamp ON plenora.runs(canvas_id, timestamp DESC);

-- TIER 2: Medio impatto
-- -----------------------------------------------------------------------------

-- Datasources: ORDER BY name
CREATE INDEX IF NOT EXISTS idx_datasources_name ON plenora.datasources(name);
CREATE INDEX IF NOT EXISTS idx_datasources_group_name ON plenora.datasources(group_id, name);

-- API connections: ORDER BY created_at DESC
CREATE INDEX IF NOT EXISTS idx_api_connections_created_at ON plenora.api_connections(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_api_connections_group_created_at ON plenora.api_connections(group_id, created_at DESC);

-- Schedules: foreign key su project_id (ON DELETE SET NULL)
CREATE INDEX IF NOT EXISTS idx_schedules_project_id ON plenora.schedules(project_id);

-- ArcGIS connections: ORDER BY updated_at DESC
CREATE INDEX IF NOT EXISTS idx_arcgis_connections_updated_at ON plenora.arcgis_connections(updated_at DESC);

-- Connections: ORDER BY created_at DESC
CREATE INDEX IF NOT EXISTS idx_connections_created_at ON plenora.connections(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_connections_group_created_at ON plenora.connections(group_id, created_at DESC);

-- Execution logs: schedule_id (per filtro schedule specifico)
CREATE INDEX IF NOT EXISTS idx_execution_logs_schedule_id ON plenora.execution_logs(schedule_id);

-- TIER 3: Basso impatto (nice-to-have)
-- -----------------------------------------------------------------------------

-- Eseguibili: filtro per created_by
CREATE INDEX IF NOT EXISTS idx_eseguibili_created_by ON plenora.eseguibili(created_by);

-- Comparison keys: lookup per node_id
CREATE INDEX IF NOT EXISTS idx_comparison_keys_node_id ON plenora.comparison_keys(node_id);

-- =============================================================================
-- Verifica: elenca gli indici creati
-- =============================================================================
-- SELECT schemaname, tablename, indexname FROM pg_indexes
-- WHERE schemaname = 'plenora' AND indexname LIKE 'idx_%'
-- ORDER BY tablename, indexname;
