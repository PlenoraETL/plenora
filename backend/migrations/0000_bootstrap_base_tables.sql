-- =============================================================================
-- 0000_bootstrap_base_tables.sql
--
-- Phase 1 / B-2 bootstrap PR #16 — crea le tabelle base che il codice prod
-- assume preesistenti (riferimento `pg_connection.init_db()` commento:
-- "le tabelle sono già create dallo script create_pg_schema.py" — script
-- non presente nel repo).
--
-- Questo file viene applicato da `app/db/migrations_runner.py` come prima
-- migration ordinata lessicograficamente (`0000_*` precede `2026-*`).
-- Tutte le operazioni sono idempotenti:
--   - CREATE TABLE / SEQUENCE IF NOT EXISTS (nativo PG)
--   - ALTER TABLE ADD CONSTRAINT in DO block che cattura
--     `duplicate_object`/`duplicate_table`/`undefined_table`/
--     `undefined_column`/`invalid_schema_name`
--
-- Su prod re-run (tabelle e constraint già esistenti) il file e' no-op.
-- Su DB ephemeral (CI B-2 / dev fresco) il file ricrea l'intero schema base
-- atteso dalle 15 migration successive (`2026-*.sql`).
--
-- Derivato da dump schema-only di prod (oracle ground-truth, non SQLite
-- legacy). Estratto via script locale, non committato.
--
-- Scope:
--   - 27 tabelle base (escluse tabelle create da migration successive,
--     escluso `schema_migrations` creato inline da migrations_runner.py)
--   - 3 sequence base (logs_id_seq, node_rows_id_seq,
--     schedule_catchup_history_id_seq)
--   - 27 PRIMARY KEY + 1 UNIQUE (users_username_key) + 27 FOREIGN KEY
--     bootstrap->bootstrap (zero FK verso tabelle non ancora create)
--   - 3 sequence ownership
--
-- Esclusioni esplicite:
--   - nessun INSERT/COPY/DROP/TRUNCATE/DELETE/UPDATE
--   - nessun OWNER/GRANT/REVOKE/COMMENT
--   - nessun CREATE INDEX non-PK (delegato alle migration `2026-*` che
--     usano gia' `CREATE INDEX IF NOT EXISTS`)
-- =============================================================================

-- ----------------------------------------------------------------------------
-- Phase A: SEQUENCES (3 bootstrap)
-- ----------------------------------------------------------------------------

CREATE SEQUENCE IF NOT EXISTS "plenora"."logs_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

CREATE SEQUENCE IF NOT EXISTS "plenora"."node_rows_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

CREATE SEQUENCE IF NOT EXISTS "plenora"."schedule_catchup_history_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- ----------------------------------------------------------------------------
-- Phase B: TABLES (27 bootstrap, no table-level constraint, only columns + DEFAULT)
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS "plenora"."groups" (
    "id" "text" NOT NULL,
    "name" "text" NOT NULL,
    "description" "text",
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL
);;

CREATE TABLE IF NOT EXISTS "plenora"."users" (
    "id" "text" NOT NULL,
    "username" "text" NOT NULL,
    "password_hash" "text" NOT NULL,
    "email" "text",
    "group_id" "text" NOT NULL,
    "role" "text" DEFAULT 'user'::"text" NOT NULL,
    "is_active" boolean DEFAULT true NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL
);;

CREATE TABLE IF NOT EXISTS "plenora"."projects" (
    "id" "text" NOT NULL,
    "group_id" "text" NOT NULL,
    "name" "text" NOT NULL,
    "project_type" "text" NOT NULL,
    "canvas" "jsonb" DEFAULT '{"edges": [], "nodes": []}'::"jsonb" NOT NULL,
    "connection_id" "text",
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "created_by" "text",
    "retention_n" integer
);;

CREATE TABLE IF NOT EXISTS "plenora"."connections" (
    "id" "text" NOT NULL,
    "group_id" "text" NOT NULL,
    "name" "text" NOT NULL,
    "db_type" "text" NOT NULL,
    "host" "text",
    "port" integer,
    "database" "text",
    "db_schema" "text",
    "username" "text",
    "password" "text",
    "extra" "jsonb",
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "created_by" "text"
);;

CREATE TABLE IF NOT EXISTS "plenora"."api_connections" (
    "id" "text" NOT NULL,
    "group_id" "text" NOT NULL,
    "config" "jsonb" NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "created_by" "text"
);;

CREATE TABLE IF NOT EXISTS "plenora"."arcgis_connections" (
    "id" "text" NOT NULL,
    "group_id" "text" NOT NULL,
    "name" "text" NOT NULL,
    "description" "text" DEFAULT ''::"text",
    "server_url" "text" NOT NULL,
    "server_type" "text" DEFAULT 'arcgis_server'::"text" NOT NULL,
    "auth_type" "text" DEFAULT 'token'::"text" NOT NULL,
    "username" "text" DEFAULT ''::"text",
    "password" "text" DEFAULT ''::"text",
    "client_id" "text" DEFAULT ''::"text",
    "client_secret" "text" DEFAULT ''::"text",
    "token_url" "text" DEFAULT ''::"text",
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "created_by" "text"
);;

CREATE TABLE IF NOT EXISTS "plenora"."datasources" (
    "id" "text" NOT NULL,
    "group_id" "text" NOT NULL,
    "name" "text" NOT NULL,
    "ds_type" "text" NOT NULL,
    "config" "jsonb" NOT NULL,
    "scan_state" "jsonb",
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "created_by" "text"
);;

CREATE TABLE IF NOT EXISTS "plenora"."file_sources" (
    "id" "text" NOT NULL,
    "group_id" "text" NOT NULL,
    "name" "text" NOT NULL,
    "original_filename" "text" NOT NULL,
    "format" "text" NOT NULL,
    "data" "bytea",
    "size_bytes" integer,
    "metadata" "jsonb",
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "created_by" "text",
    "storage_key" "text"
);;

CREATE TABLE IF NOT EXISTS "plenora"."eseguibili" (
    "id" "text" NOT NULL,
    "name" "text" NOT NULL,
    "description" "text",
    "canvas" "jsonb" NOT NULL,
    "expected_columns" "jsonb",
    "default_output_format" "text" DEFAULT 'xlsx'::"text" NOT NULL,
    "default_output_name" "text",
    "icon" "text",
    "source_canvas_id" "text",
    "group_id" "text" NOT NULL,
    "created_by" "text" NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL
);;

CREATE TABLE IF NOT EXISTS "plenora"."comparison_keys" (
    "canvas_id" "text" NOT NULL,
    "node_id" "text" NOT NULL,
    "group_id" "text" NOT NULL,
    "key_columns" "jsonb" NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL
);;

CREATE TABLE IF NOT EXISTS "plenora"."connection_shares" (
    "connection_id" "text" NOT NULL,
    "group_id" "text" NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL
);;

CREATE TABLE IF NOT EXISTS "plenora"."api_connection_shares" (
    "api_connection_id" "text" NOT NULL,
    "group_id" "text" NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL
);;

CREATE TABLE IF NOT EXISTS "plenora"."arcgis_connection_shares" (
    "arcgis_connection_id" "text" NOT NULL,
    "group_id" "text" NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL
);;

CREATE TABLE IF NOT EXISTS "plenora"."datasource_shares" (
    "datasource_id" "text" NOT NULL,
    "group_id" "text" NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL
);;

CREATE TABLE IF NOT EXISTS "plenora"."file_source_shares" (
    "file_source_id" "text" NOT NULL,
    "group_id" "text" NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL
);;

CREATE TABLE IF NOT EXISTS "plenora"."eseguibile_shares" (
    "eseguibile_id" "text" NOT NULL,
    "group_id" "text" NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL
);;

CREATE TABLE IF NOT EXISTS "plenora"."execution_logs" (
    "id" "text" NOT NULL,
    "group_id" "text" NOT NULL,
    "project_id" "text",
    "schedule_id" "text",
    "status" "text" NOT NULL,
    "started_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "finished_at" timestamp with time zone,
    "duration_s" real,
    "rows_count" integer,
    "message" "text",
    "error" "text",
    "triggered_by" "text"
);;

CREATE TABLE IF NOT EXISTS "plenora"."runs" (
    "run_id" "text" NOT NULL,
    "canvas_id" "text" NOT NULL,
    "group_id" "text" NOT NULL,
    "timestamp" timestamp with time zone DEFAULT "now"() NOT NULL,
    "status" "text" DEFAULT 'running'::"text" NOT NULL,
    "pinned" boolean DEFAULT false NOT NULL,
    "total_rows_in" integer,
    "total_rows_out" integer,
    "total_rows_dropped" integer,
    "total_nodes" integer,
    "nodes_ok" integer DEFAULT 0,
    "nodes_error" integer DEFAULT 0,
    "nodes_skipped" integer DEFAULT 0,
    "total_exec_time" real,
    "input_hash" "text",
    "output_hash" "text",
    "user_id" character varying(64)
);;

CREATE TABLE IF NOT EXISTS "plenora"."results" (
    "id" "text" NOT NULL,
    "project_id" "text" NOT NULL,
    "group_id" "text" NOT NULL,
    "filename" "text" NOT NULL,
    "format" "text" NOT NULL,
    "data" "bytea",
    "size_bytes" integer,
    "rows_count" integer,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "created_by" "text",
    "storage_key" "text"
);;

CREATE TABLE IF NOT EXISTS "plenora"."log_runs" (
    "run_id" "text" NOT NULL,
    "project_id" "text" NOT NULL,
    "group_id" "text" DEFAULT ''::"text",
    "module" "text" DEFAULT ''::"text",
    "triggered_by" "text" DEFAULT 'manual'::"text",
    "ts_start" timestamp with time zone DEFAULT "now"() NOT NULL,
    "ts_end" timestamp with time zone,
    "status" "text" DEFAULT 'running'::"text",
    "rows_in" integer DEFAULT 0,
    "rows_out" integer DEFAULT 0,
    "duration_ms" integer DEFAULT 0,
    "error_node_id" "text",
    "error_module" "text",
    "error_message" "text",
    "steps" "jsonb" DEFAULT '[]'::"jsonb",
    "debug_payload" "jsonb",
    "user_id" character varying(64)
);;

CREATE TABLE IF NOT EXISTS "plenora"."logs" (
    "id" integer NOT NULL,
    "ts" timestamp with time zone DEFAULT "now"() NOT NULL,
    "level" "text" NOT NULL,
    "module" "text" NOT NULL,
    "op" "text" DEFAULT ''::"text",
    "msg" "text" NOT NULL,
    "project_id" "text" DEFAULT ''::"text",
    "group_id" "text" DEFAULT ''::"text",
    "run_id" "text" DEFAULT ''::"text",
    "node_id" "text" DEFAULT ''::"text",
    "details" "jsonb",
    "error" "text" DEFAULT ''::"text"
);;

CREATE TABLE IF NOT EXISTS "plenora"."schedules" (
    "id" "text" NOT NULL,
    "group_id" "text" NOT NULL,
    "project_id" "text",
    "name" "text" NOT NULL,
    "cron_expression" "text" NOT NULL,
    "is_enabled" boolean DEFAULT true NOT NULL,
    "config" "jsonb",
    "last_run" timestamp with time zone,
    "last_status" "text",
    "next_run" timestamp with time zone,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "created_by" "text"
);;

CREATE TABLE IF NOT EXISTS "plenora"."schedule_catchup_history" (
    "id" bigint NOT NULL,
    "schedule_id" "text" NOT NULL,
    "emitted_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "scheduled_for" timestamp with time zone NOT NULL,
    "leader_id" "text" NOT NULL
);;

CREATE TABLE IF NOT EXISTS "plenora"."scheduler_locks" (
    "key" "text" NOT NULL,
    "value" "text" NOT NULL,
    "expires_at" timestamp with time zone
);;

CREATE TABLE IF NOT EXISTS "plenora"."beat_heartbeat" (
    "id" "text" NOT NULL,
    "ts" timestamp with time zone DEFAULT "now"() NOT NULL
);;

CREATE TABLE IF NOT EXISTS "plenora"."node_meta" (
    "run_id" "text" NOT NULL,
    "node_id" "text" NOT NULL,
    "node_type" "text",
    "row_count" integer,
    "col_schema" "jsonb",
    "exec_time" real,
    "status" "text",
    "error_msg" "text",
    "node_hash" "text",
    "hash_algo" "text",
    "persistence_mode" "text",
    "persisted_rows_count" integer,
    "preview_truncated" boolean,
    "artifact_uri" "text",
    "artifact_format" "text",
    "artifact_status" "text",
    "artifact_error" "text"
);;

CREATE TABLE IF NOT EXISTS "plenora"."node_rows" (
    "id" bigint NOT NULL,
    "run_id" "text" NOT NULL,
    "node_id" "text" NOT NULL,
    "row_idx" integer NOT NULL,
    "row_hash" "text" NOT NULL,
    "row_data" "jsonb" NOT NULL
);;


-- ----------------------------------------------------------------------------
-- Phase C: PRIMARY KEYS (27 in idempotent DO blocks)
-- ----------------------------------------------------------------------------

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."groups"
        ADD CONSTRAINT "groups_pkey" PRIMARY KEY ("id");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN duplicate_table THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."users"
        ADD CONSTRAINT "users_pkey" PRIMARY KEY ("id");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN duplicate_table THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."projects"
        ADD CONSTRAINT "projects_pkey" PRIMARY KEY ("id");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN duplicate_table THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."connections"
        ADD CONSTRAINT "connections_pkey" PRIMARY KEY ("id");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN duplicate_table THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."api_connections"
        ADD CONSTRAINT "api_connections_pkey" PRIMARY KEY ("id");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN duplicate_table THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."arcgis_connections"
        ADD CONSTRAINT "arcgis_connections_pkey" PRIMARY KEY ("id");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN duplicate_table THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."datasources"
        ADD CONSTRAINT "datasources_pkey" PRIMARY KEY ("id");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN duplicate_table THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."file_sources"
        ADD CONSTRAINT "file_sources_pkey" PRIMARY KEY ("id");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN duplicate_table THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."eseguibili"
        ADD CONSTRAINT "eseguibili_pkey" PRIMARY KEY ("id");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN duplicate_table THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."comparison_keys"
        ADD CONSTRAINT "comparison_keys_pkey" PRIMARY KEY ("canvas_id", "node_id");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN duplicate_table THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."connection_shares"
        ADD CONSTRAINT "connection_shares_pkey" PRIMARY KEY ("connection_id", "group_id");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN duplicate_table THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."api_connection_shares"
        ADD CONSTRAINT "api_connection_shares_pkey" PRIMARY KEY ("api_connection_id", "group_id");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN duplicate_table THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."arcgis_connection_shares"
        ADD CONSTRAINT "arcgis_connection_shares_pkey" PRIMARY KEY ("arcgis_connection_id", "group_id");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN duplicate_table THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."datasource_shares"
        ADD CONSTRAINT "datasource_shares_pkey" PRIMARY KEY ("datasource_id", "group_id");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN duplicate_table THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."file_source_shares"
        ADD CONSTRAINT "file_source_shares_pkey" PRIMARY KEY ("file_source_id", "group_id");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN duplicate_table THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."eseguibile_shares"
        ADD CONSTRAINT "eseguibile_shares_pkey" PRIMARY KEY ("eseguibile_id", "group_id");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN duplicate_table THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."execution_logs"
        ADD CONSTRAINT "execution_logs_pkey" PRIMARY KEY ("id");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN duplicate_table THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."runs"
        ADD CONSTRAINT "runs_pkey" PRIMARY KEY ("run_id");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN duplicate_table THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."results"
        ADD CONSTRAINT "results_pkey" PRIMARY KEY ("id");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN duplicate_table THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."log_runs"
        ADD CONSTRAINT "log_runs_pkey" PRIMARY KEY ("run_id");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN duplicate_table THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."logs"
        ADD CONSTRAINT "logs_pkey" PRIMARY KEY ("id");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN duplicate_table THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."schedules"
        ADD CONSTRAINT "schedules_pkey" PRIMARY KEY ("id");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN duplicate_table THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."schedule_catchup_history"
        ADD CONSTRAINT "schedule_catchup_history_pkey" PRIMARY KEY ("id");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN duplicate_table THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."scheduler_locks"
        ADD CONSTRAINT "scheduler_locks_pkey" PRIMARY KEY ("key");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN duplicate_table THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."beat_heartbeat"
        ADD CONSTRAINT "beat_heartbeat_pkey" PRIMARY KEY ("id");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN duplicate_table THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."node_meta"
        ADD CONSTRAINT "node_meta_pkey" PRIMARY KEY ("run_id", "node_id");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN duplicate_table THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."node_rows"
        ADD CONSTRAINT "node_rows_pkey" PRIMARY KEY ("id");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN duplicate_table THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
END $$;


-- ----------------------------------------------------------------------------
-- Phase D: UNIQUE CONSTRAINTS (1 in idempotent DO block)
-- ----------------------------------------------------------------------------

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."users"
        ADD CONSTRAINT "users_username_key" UNIQUE ("username");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN duplicate_table THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
END $$;


-- ----------------------------------------------------------------------------
-- Phase E: FOREIGN KEYS (27 bootstrap->bootstrap, idempotent DO blocks)
-- ----------------------------------------------------------------------------

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."users"
        ADD CONSTRAINT "users_group_id_fkey" FOREIGN KEY ("group_id") REFERENCES "plenora"."groups"("id");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
    WHEN undefined_column THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."projects"
        ADD CONSTRAINT "projects_created_by_fkey" FOREIGN KEY ("created_by") REFERENCES "plenora"."users"("id");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
    WHEN undefined_column THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."projects"
        ADD CONSTRAINT "projects_group_id_fkey" FOREIGN KEY ("group_id") REFERENCES "plenora"."groups"("id");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
    WHEN undefined_column THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."connections"
        ADD CONSTRAINT "connections_created_by_fkey" FOREIGN KEY ("created_by") REFERENCES "plenora"."users"("id");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
    WHEN undefined_column THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."connections"
        ADD CONSTRAINT "connections_group_id_fkey" FOREIGN KEY ("group_id") REFERENCES "plenora"."groups"("id");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
    WHEN undefined_column THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."api_connections"
        ADD CONSTRAINT "api_connections_created_by_fkey" FOREIGN KEY ("created_by") REFERENCES "plenora"."users"("id");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
    WHEN undefined_column THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."api_connections"
        ADD CONSTRAINT "api_connections_group_id_fkey" FOREIGN KEY ("group_id") REFERENCES "plenora"."groups"("id");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
    WHEN undefined_column THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."arcgis_connections"
        ADD CONSTRAINT "arcgis_connections_created_by_fkey" FOREIGN KEY ("created_by") REFERENCES "plenora"."users"("id");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
    WHEN undefined_column THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."arcgis_connections"
        ADD CONSTRAINT "arcgis_connections_group_id_fkey" FOREIGN KEY ("group_id") REFERENCES "plenora"."groups"("id");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
    WHEN undefined_column THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."datasources"
        ADD CONSTRAINT "datasources_created_by_fkey" FOREIGN KEY ("created_by") REFERENCES "plenora"."users"("id");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
    WHEN undefined_column THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."datasources"
        ADD CONSTRAINT "datasources_group_id_fkey" FOREIGN KEY ("group_id") REFERENCES "plenora"."groups"("id");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
    WHEN undefined_column THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."file_sources"
        ADD CONSTRAINT "file_sources_created_by_fkey" FOREIGN KEY ("created_by") REFERENCES "plenora"."users"("id");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
    WHEN undefined_column THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."file_sources"
        ADD CONSTRAINT "file_sources_group_id_fkey" FOREIGN KEY ("group_id") REFERENCES "plenora"."groups"("id");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
    WHEN undefined_column THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."eseguibili"
        ADD CONSTRAINT "eseguibili_created_by_fkey" FOREIGN KEY ("created_by") REFERENCES "plenora"."users"("id");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
    WHEN undefined_column THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."eseguibili"
        ADD CONSTRAINT "eseguibili_group_id_fkey" FOREIGN KEY ("group_id") REFERENCES "plenora"."groups"("id");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
    WHEN undefined_column THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."comparison_keys"
        ADD CONSTRAINT "comparison_keys_group_id_fkey" FOREIGN KEY ("group_id") REFERENCES "plenora"."groups"("id");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
    WHEN undefined_column THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."connection_shares"
        ADD CONSTRAINT "connection_shares_connection_id_fkey" FOREIGN KEY ("connection_id") REFERENCES "plenora"."connections"("id") ON DELETE CASCADE;
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
    WHEN undefined_column THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."api_connection_shares"
        ADD CONSTRAINT "api_connection_shares_api_connection_id_fkey" FOREIGN KEY ("api_connection_id") REFERENCES "plenora"."api_connections"("id") ON DELETE CASCADE;
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
    WHEN undefined_column THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."arcgis_connection_shares"
        ADD CONSTRAINT "arcgis_connection_shares_arcgis_connection_id_fkey" FOREIGN KEY ("arcgis_connection_id") REFERENCES "plenora"."arcgis_connections"("id") ON DELETE CASCADE;
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
    WHEN undefined_column THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."datasource_shares"
        ADD CONSTRAINT "datasource_shares_datasource_id_fkey" FOREIGN KEY ("datasource_id") REFERENCES "plenora"."datasources"("id") ON DELETE CASCADE;
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
    WHEN undefined_column THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."file_source_shares"
        ADD CONSTRAINT "file_source_shares_file_source_id_fkey" FOREIGN KEY ("file_source_id") REFERENCES "plenora"."file_sources"("id") ON DELETE CASCADE;
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
    WHEN undefined_column THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."eseguibile_shares"
        ADD CONSTRAINT "eseguibile_shares_eseguibile_id_fkey" FOREIGN KEY ("eseguibile_id") REFERENCES "plenora"."eseguibili"("id") ON DELETE CASCADE;
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
    WHEN undefined_column THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."schedules"
        ADD CONSTRAINT "schedules_created_by_fkey" FOREIGN KEY ("created_by") REFERENCES "plenora"."users"("id");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
    WHEN undefined_column THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."schedules"
        ADD CONSTRAINT "schedules_group_id_fkey" FOREIGN KEY ("group_id") REFERENCES "plenora"."groups"("id");
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
    WHEN undefined_column THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."schedules"
        ADD CONSTRAINT "schedules_project_id_fkey" FOREIGN KEY ("project_id") REFERENCES "plenora"."projects"("id") ON DELETE SET NULL;
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
    WHEN undefined_column THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."node_meta"
        ADD CONSTRAINT "node_meta_run_id_fkey" FOREIGN KEY ("run_id") REFERENCES "plenora"."runs"("run_id") ON DELETE CASCADE;
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
    WHEN undefined_column THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."node_rows"
        ADD CONSTRAINT "node_rows_run_id_fkey" FOREIGN KEY ("run_id") REFERENCES "plenora"."runs"("run_id") ON DELETE CASCADE;
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN invalid_schema_name THEN NULL;
    WHEN undefined_table THEN NULL;
    WHEN undefined_column THEN NULL;
END $$;


-- ----------------------------------------------------------------------------
-- Phase F: SEQUENCE OWNERSHIP (3 idempotent DO blocks)
-- ----------------------------------------------------------------------------

DO $$ BEGIN
    ALTER SEQUENCE "plenora"."logs_id_seq" OWNED BY "plenora"."logs"."id";
EXCEPTION
    WHEN undefined_table THEN NULL;
    WHEN undefined_column THEN NULL;
    WHEN undefined_object THEN NULL;
    WHEN invalid_schema_name THEN NULL;
END $$;

DO $$ BEGIN
    ALTER SEQUENCE "plenora"."node_rows_id_seq" OWNED BY "plenora"."node_rows"."id";
EXCEPTION
    WHEN undefined_table THEN NULL;
    WHEN undefined_column THEN NULL;
    WHEN undefined_object THEN NULL;
    WHEN invalid_schema_name THEN NULL;
END $$;

DO $$ BEGIN
    ALTER SEQUENCE "plenora"."schedule_catchup_history_id_seq" OWNED BY "plenora"."schedule_catchup_history"."id";
EXCEPTION
    WHEN undefined_table THEN NULL;
    WHEN undefined_column THEN NULL;
    WHEN undefined_object THEN NULL;
    WHEN invalid_schema_name THEN NULL;
END $$;


-- ----------------------------------------------------------------------------
-- Phase G: COLUMN DEFAULTS / sequence linkage (3 idempotent DO blocks)
--          ALTER COLUMN id SET DEFAULT nextval(...) per le 3 sequence base.
--          Senza queste, INSERT senza id esplicito fallirebbe NOT NULL
--          violation. Riproduce esattamente quanto presente nel dump prod
--          (linee 797/804/811 del dump source).
-- ----------------------------------------------------------------------------

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."logs"
        ALTER COLUMN "id" SET DEFAULT nextval('"plenora"."logs_id_seq"'::regclass);
EXCEPTION
    WHEN undefined_table THEN NULL;
    WHEN undefined_column THEN NULL;
    WHEN undefined_object THEN NULL;
    WHEN invalid_schema_name THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."node_rows"
        ALTER COLUMN "id" SET DEFAULT nextval('"plenora"."node_rows_id_seq"'::regclass);
EXCEPTION
    WHEN undefined_table THEN NULL;
    WHEN undefined_column THEN NULL;
    WHEN undefined_object THEN NULL;
    WHEN invalid_schema_name THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE ONLY "plenora"."schedule_catchup_history"
        ALTER COLUMN "id" SET DEFAULT nextval('"plenora"."schedule_catchup_history_id_seq"'::regclass);
EXCEPTION
    WHEN undefined_table THEN NULL;
    WHEN undefined_column THEN NULL;
    WHEN undefined_object THEN NULL;
    WHEN invalid_schema_name THEN NULL;
END $$;

