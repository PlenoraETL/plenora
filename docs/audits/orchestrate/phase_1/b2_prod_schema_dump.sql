--
-- PostgreSQL database dump
--

\restrict knvtzObn6DeOOTEiaJHDSSHKVeYQOApouqqCOBNI1ZAGsg2QLXEZxUxJX4tSVSW

-- Dumped from database version 17.9 (Ubuntu 17.9-1.pgdg24.04+1)
-- Dumped by pg_dump version 17.9

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: plenora; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA "plenora";


--
-- Name: block_log_modifications(); Type: FUNCTION; Schema: plenora; Owner: -
--

CREATE FUNCTION "plenora"."block_log_modifications"() RETURNS "trigger"
    LANGUAGE "plpgsql"
    AS $$
DECLARE
    retention_flag TEXT;
BEGIN
    -- UPDATE: mai ammesso. Le righe di log/audit sono immutabili: per
    -- correggere un log si fa una nuova INSERT con un marker di
    -- superseding, non si muta la riga originale.
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION
            'Tabella %.% e'' append-only: UPDATE vietato (policy ISO 27001 A.12.4.1)',
            TG_TABLE_SCHEMA, TG_TABLE_NAME
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    -- DELETE: ammesso SOLO con bypass esplicito di retention cleanup.
    -- current_setting(key, missing_ok=true) ritorna NULL se la GUC non
    -- e' stata settata nella tx corrente (niente exception da catch).
    IF TG_OP = 'DELETE' THEN
        retention_flag := current_setting('plenora.retention_cleanup', true);
        IF retention_flag = 'true' THEN
            RETURN OLD;  -- consentito: retention job autorizzato
        END IF;
        RAISE EXCEPTION
            'Tabella %.% e'' append-only: DELETE richiede SET LOCAL plenora.retention_cleanup = ''true'' (retention job autorizzato)',
            TG_TABLE_SCHEMA, TG_TABLE_NAME
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    RETURN OLD;  -- ramo TG_OP teoricamente non raggiungibile (solo UPDATE/DELETE)
END;
$$;


SET default_table_access_method = "heap";

--
-- Name: api_connection_shares; Type: TABLE; Schema: plenora; Owner: -
--

CREATE TABLE "plenora"."api_connection_shares" (
    "api_connection_id" "text" NOT NULL,
    "group_id" "text" NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


--
-- Name: api_connections; Type: TABLE; Schema: plenora; Owner: -
--

CREATE TABLE "plenora"."api_connections" (
    "id" "text" NOT NULL,
    "group_id" "text" NOT NULL,
    "config" "jsonb" NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "created_by" "text"
);


--
-- Name: arcgis_connection_shares; Type: TABLE; Schema: plenora; Owner: -
--

CREATE TABLE "plenora"."arcgis_connection_shares" (
    "arcgis_connection_id" "text" NOT NULL,
    "group_id" "text" NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


--
-- Name: arcgis_connections; Type: TABLE; Schema: plenora; Owner: -
--

CREATE TABLE "plenora"."arcgis_connections" (
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
);


--
-- Name: audit_actions; Type: TABLE; Schema: plenora; Owner: -
--

CREATE TABLE "plenora"."audit_actions" (
    "id" bigint NOT NULL,
    "timestamp" timestamp with time zone DEFAULT "now"() NOT NULL,
    "user_id" "text",
    "action" "text" NOT NULL,
    "target" "text",
    "status" integer NOT NULL,
    "ip_address" "text",
    "error_message" "text",
    "metadata" "jsonb"
);


--
-- Name: audit_actions_id_seq; Type: SEQUENCE; Schema: plenora; Owner: -
--

CREATE SEQUENCE "plenora"."audit_actions_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: audit_actions_id_seq; Type: SEQUENCE OWNED BY; Schema: plenora; Owner: -
--

ALTER SEQUENCE "plenora"."audit_actions_id_seq" OWNED BY "plenora"."audit_actions"."id";


--
-- Name: backup_runs; Type: TABLE; Schema: plenora; Owner: -
--

CREATE TABLE "plenora"."backup_runs" (
    "id" bigint NOT NULL,
    "started_at" timestamp with time zone NOT NULL,
    "finished_at" timestamp with time zone,
    "exit_code" integer,
    "result" "text" NOT NULL,
    "bucket_key" "text",
    "size_bytes" bigint,
    "log_tail" "text",
    "error_message" "text",
    CONSTRAINT "backup_runs_result_check" CHECK (("result" = ANY (ARRAY['running'::"text", 'success'::"text", 'failed'::"text"])))
);


--
-- Name: backup_runs_id_seq; Type: SEQUENCE; Schema: plenora; Owner: -
--

CREATE SEQUENCE "plenora"."backup_runs_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: backup_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: plenora; Owner: -
--

ALTER SEQUENCE "plenora"."backup_runs_id_seq" OWNED BY "plenora"."backup_runs"."id";


--
-- Name: beat_heartbeat; Type: TABLE; Schema: plenora; Owner: -
--

CREATE TABLE "plenora"."beat_heartbeat" (
    "id" "text" NOT NULL,
    "ts" timestamp with time zone DEFAULT "now"() NOT NULL
);


--
-- Name: comparison_keys; Type: TABLE; Schema: plenora; Owner: -
--

CREATE TABLE "plenora"."comparison_keys" (
    "canvas_id" "text" NOT NULL,
    "node_id" "text" NOT NULL,
    "group_id" "text" NOT NULL,
    "key_columns" "jsonb" NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


--
-- Name: connection_shares; Type: TABLE; Schema: plenora; Owner: -
--

CREATE TABLE "plenora"."connection_shares" (
    "connection_id" "text" NOT NULL,
    "group_id" "text" NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


--
-- Name: connections; Type: TABLE; Schema: plenora; Owner: -
--

CREATE TABLE "plenora"."connections" (
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
);


--
-- Name: datasource_shares; Type: TABLE; Schema: plenora; Owner: -
--

CREATE TABLE "plenora"."datasource_shares" (
    "datasource_id" "text" NOT NULL,
    "group_id" "text" NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


--
-- Name: datasources; Type: TABLE; Schema: plenora; Owner: -
--

CREATE TABLE "plenora"."datasources" (
    "id" "text" NOT NULL,
    "group_id" "text" NOT NULL,
    "name" "text" NOT NULL,
    "ds_type" "text" NOT NULL,
    "config" "jsonb" NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "created_by" "text"
);


--
-- Name: eseguibile_shares; Type: TABLE; Schema: plenora; Owner: -
--

CREATE TABLE "plenora"."eseguibile_shares" (
    "eseguibile_id" "text" NOT NULL,
    "group_id" "text" NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


--
-- Name: eseguibili; Type: TABLE; Schema: plenora; Owner: -
--

CREATE TABLE "plenora"."eseguibili" (
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
);


--
-- Name: execution_logs; Type: TABLE; Schema: plenora; Owner: -
--

CREATE TABLE "plenora"."execution_logs" (
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
);


--
-- Name: file_source_shares; Type: TABLE; Schema: plenora; Owner: -
--

CREATE TABLE "plenora"."file_source_shares" (
    "file_source_id" "text" NOT NULL,
    "group_id" "text" NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


--
-- Name: file_sources; Type: TABLE; Schema: plenora; Owner: -
--

CREATE TABLE "plenora"."file_sources" (
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
);


--
-- Name: groups; Type: TABLE; Schema: plenora; Owner: -
--

CREATE TABLE "plenora"."groups" (
    "id" "text" NOT NULL,
    "name" "text" NOT NULL,
    "description" "text",
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


--
-- Name: log_runs; Type: TABLE; Schema: plenora; Owner: -
--

CREATE TABLE "plenora"."log_runs" (
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
);


--
-- Name: logs; Type: TABLE; Schema: plenora; Owner: -
--

CREATE TABLE "plenora"."logs" (
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
);


--
-- Name: logs_id_seq; Type: SEQUENCE; Schema: plenora; Owner: -
--

CREATE SEQUENCE "plenora"."logs_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: logs_id_seq; Type: SEQUENCE OWNED BY; Schema: plenora; Owner: -
--

ALTER SEQUENCE "plenora"."logs_id_seq" OWNED BY "plenora"."logs"."id";


--
-- Name: node_meta; Type: TABLE; Schema: plenora; Owner: -
--

CREATE TABLE "plenora"."node_meta" (
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
);


--
-- Name: node_rows; Type: TABLE; Schema: plenora; Owner: -
--

CREATE TABLE "plenora"."node_rows" (
    "id" bigint NOT NULL,
    "run_id" "text" NOT NULL,
    "node_id" "text" NOT NULL,
    "row_idx" integer NOT NULL,
    "row_hash" "text" NOT NULL,
    "row_data" "jsonb" NOT NULL
);


--
-- Name: node_rows_id_seq; Type: SEQUENCE; Schema: plenora; Owner: -
--

CREATE SEQUENCE "plenora"."node_rows_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: node_rows_id_seq; Type: SEQUENCE OWNED BY; Schema: plenora; Owner: -
--

ALTER SEQUENCE "plenora"."node_rows_id_seq" OWNED BY "plenora"."node_rows"."id";


--
-- Name: projects; Type: TABLE; Schema: plenora; Owner: -
--

CREATE TABLE "plenora"."projects" (
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
);


--
-- Name: results; Type: TABLE; Schema: plenora; Owner: -
--

CREATE TABLE "plenora"."results" (
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
);


--
-- Name: runs; Type: TABLE; Schema: plenora; Owner: -
--

CREATE TABLE "plenora"."runs" (
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
);


--
-- Name: schedule_catchup_history; Type: TABLE; Schema: plenora; Owner: -
--

CREATE TABLE "plenora"."schedule_catchup_history" (
    "id" bigint NOT NULL,
    "schedule_id" "text" NOT NULL,
    "emitted_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "scheduled_for" timestamp with time zone NOT NULL,
    "leader_id" "text" NOT NULL
);


--
-- Name: schedule_catchup_history_id_seq; Type: SEQUENCE; Schema: plenora; Owner: -
--

CREATE SEQUENCE "plenora"."schedule_catchup_history_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: schedule_catchup_history_id_seq; Type: SEQUENCE OWNED BY; Schema: plenora; Owner: -
--

ALTER SEQUENCE "plenora"."schedule_catchup_history_id_seq" OWNED BY "plenora"."schedule_catchup_history"."id";


--
-- Name: schedule_logs; Type: TABLE; Schema: plenora; Owner: -
--

CREATE TABLE "plenora"."schedule_logs" (
    "log_id" "text" NOT NULL,
    "schedule_id" "text" NOT NULL,
    "timestamp" timestamp with time zone NOT NULL,
    "level" "text" NOT NULL,
    "event" "text" NOT NULL,
    "triggered_by" "text" NOT NULL,
    "steps" "jsonb" DEFAULT '[]'::"jsonb" NOT NULL,
    "total_duration_ms" integer DEFAULT 0 NOT NULL,
    "error_message" "text",
    "schedule_info" "jsonb",
    "pipeline_info" "jsonb",
    "execution_info" "jsonb"
);


--
-- Name: scheduler_locks; Type: TABLE; Schema: plenora; Owner: -
--

CREATE TABLE "plenora"."scheduler_locks" (
    "key" "text" NOT NULL,
    "value" "text" NOT NULL,
    "expires_at" timestamp with time zone
);


--
-- Name: schedules; Type: TABLE; Schema: plenora; Owner: -
--

CREATE TABLE "plenora"."schedules" (
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
);


--
-- Name: schema_migrations; Type: TABLE; Schema: plenora; Owner: -
--

CREATE TABLE "plenora"."schema_migrations" (
    "filename" "text" NOT NULL,
    "applied_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


--
-- Name: ticket_attachments; Type: TABLE; Schema: plenora; Owner: -
--

CREATE TABLE "plenora"."ticket_attachments" (
    "id" "text" NOT NULL,
    "ticket_id" "text" NOT NULL,
    "file_source_id" "text" NOT NULL,
    "uploaded_by" "text" NOT NULL,
    "uploaded_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


--
-- Name: ticket_comments; Type: TABLE; Schema: plenora; Owner: -
--

CREATE TABLE "plenora"."ticket_comments" (
    "id" "text" NOT NULL,
    "ticket_id" "text" NOT NULL,
    "author_id" "text" NOT NULL,
    "body" "text" NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


--
-- Name: tickets; Type: TABLE; Schema: plenora; Owner: -
--

CREATE TABLE "plenora"."tickets" (
    "id" "text" NOT NULL,
    "kind" "text" NOT NULL,
    "title" "text" NOT NULL,
    "description" "text" DEFAULT ''::"text" NOT NULL,
    "severity" "text" NOT NULL,
    "status" "text" NOT NULL,
    "category" "text" DEFAULT 'other'::"text" NOT NULL,
    "created_by" "text" NOT NULL,
    "assignee" "text",
    "group_id" "text" NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "resolved_at" timestamp with time zone,
    "deleted_at" timestamp with time zone,
    CONSTRAINT "tickets_kind_check" CHECK (("kind" = ANY (ARRAY['tech_debt'::"text", 'user_support'::"text"]))),
    CONSTRAINT "tickets_severity_check" CHECK (("severity" = ANY (ARRAY['low'::"text", 'medium'::"text", 'high'::"text", 'critical'::"text"]))),
    CONSTRAINT "tickets_status_check" CHECK (("status" = ANY (ARRAY['open'::"text", 'in_progress'::"text", 'waiting_user'::"text", 'resolved'::"text", 'closed'::"text", 'reopened'::"text"])))
);


--
-- Name: user_mfa; Type: TABLE; Schema: plenora; Owner: -
--

CREATE TABLE "plenora"."user_mfa" (
    "user_id" "text" NOT NULL,
    "secret_encrypted" "text" NOT NULL,
    "enabled" boolean DEFAULT false NOT NULL,
    "enrolled_at" timestamp with time zone,
    "last_verified_at" timestamp with time zone,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


--
-- Name: users; Type: TABLE; Schema: plenora; Owner: -
--

CREATE TABLE "plenora"."users" (
    "id" "text" NOT NULL,
    "username" "text" NOT NULL,
    "password_hash" "text" NOT NULL,
    "email" "text",
    "group_id" "text" NOT NULL,
    "role" "text" DEFAULT 'user'::"text" NOT NULL,
    "is_active" boolean DEFAULT true NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


--
-- Name: verify_runs; Type: TABLE; Schema: plenora; Owner: -
--

CREATE TABLE "plenora"."verify_runs" (
    "id" bigint NOT NULL,
    "started_at" timestamp with time zone NOT NULL,
    "finished_at" timestamp with time zone,
    "exit_code" integer,
    "result" "text" NOT NULL,
    "log_tail" "text",
    "error_message" "text",
    CONSTRAINT "verify_runs_result_check" CHECK (("result" = ANY (ARRAY['running'::"text", 'success'::"text", 'failed'::"text"])))
);


--
-- Name: verify_runs_id_seq; Type: SEQUENCE; Schema: plenora; Owner: -
--

CREATE SEQUENCE "plenora"."verify_runs_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: verify_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: plenora; Owner: -
--

ALTER SEQUENCE "plenora"."verify_runs_id_seq" OWNED BY "plenora"."verify_runs"."id";


--
-- Name: audit_actions id; Type: DEFAULT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."audit_actions" ALTER COLUMN "id" SET DEFAULT "nextval"('"plenora"."audit_actions_id_seq"'::"regclass");


--
-- Name: backup_runs id; Type: DEFAULT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."backup_runs" ALTER COLUMN "id" SET DEFAULT "nextval"('"plenora"."backup_runs_id_seq"'::"regclass");


--
-- Name: logs id; Type: DEFAULT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."logs" ALTER COLUMN "id" SET DEFAULT "nextval"('"plenora"."logs_id_seq"'::"regclass");


--
-- Name: node_rows id; Type: DEFAULT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."node_rows" ALTER COLUMN "id" SET DEFAULT "nextval"('"plenora"."node_rows_id_seq"'::"regclass");


--
-- Name: schedule_catchup_history id; Type: DEFAULT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."schedule_catchup_history" ALTER COLUMN "id" SET DEFAULT "nextval"('"plenora"."schedule_catchup_history_id_seq"'::"regclass");


--
-- Name: verify_runs id; Type: DEFAULT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."verify_runs" ALTER COLUMN "id" SET DEFAULT "nextval"('"plenora"."verify_runs_id_seq"'::"regclass");


--
-- Name: api_connection_shares api_connection_shares_pkey; Type: CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."api_connection_shares"
    ADD CONSTRAINT "api_connection_shares_pkey" PRIMARY KEY ("api_connection_id", "group_id");


--
-- Name: api_connections api_connections_pkey; Type: CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."api_connections"
    ADD CONSTRAINT "api_connections_pkey" PRIMARY KEY ("id");


--
-- Name: arcgis_connection_shares arcgis_connection_shares_pkey; Type: CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."arcgis_connection_shares"
    ADD CONSTRAINT "arcgis_connection_shares_pkey" PRIMARY KEY ("arcgis_connection_id", "group_id");


--
-- Name: arcgis_connections arcgis_connections_pkey; Type: CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."arcgis_connections"
    ADD CONSTRAINT "arcgis_connections_pkey" PRIMARY KEY ("id");


--
-- Name: audit_actions audit_actions_pkey; Type: CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."audit_actions"
    ADD CONSTRAINT "audit_actions_pkey" PRIMARY KEY ("id");


--
-- Name: backup_runs backup_runs_pkey; Type: CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."backup_runs"
    ADD CONSTRAINT "backup_runs_pkey" PRIMARY KEY ("id");


--
-- Name: beat_heartbeat beat_heartbeat_pkey; Type: CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."beat_heartbeat"
    ADD CONSTRAINT "beat_heartbeat_pkey" PRIMARY KEY ("id");


--
-- Name: comparison_keys comparison_keys_pkey; Type: CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."comparison_keys"
    ADD CONSTRAINT "comparison_keys_pkey" PRIMARY KEY ("canvas_id", "node_id");


--
-- Name: connection_shares connection_shares_pkey; Type: CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."connection_shares"
    ADD CONSTRAINT "connection_shares_pkey" PRIMARY KEY ("connection_id", "group_id");


--
-- Name: connections connections_pkey; Type: CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."connections"
    ADD CONSTRAINT "connections_pkey" PRIMARY KEY ("id");


--
-- Name: datasource_shares datasource_shares_pkey; Type: CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."datasource_shares"
    ADD CONSTRAINT "datasource_shares_pkey" PRIMARY KEY ("datasource_id", "group_id");


--
-- Name: datasources datasources_pkey; Type: CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."datasources"
    ADD CONSTRAINT "datasources_pkey" PRIMARY KEY ("id");


--
-- Name: eseguibile_shares eseguibile_shares_pkey; Type: CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."eseguibile_shares"
    ADD CONSTRAINT "eseguibile_shares_pkey" PRIMARY KEY ("eseguibile_id", "group_id");


--
-- Name: eseguibili eseguibili_pkey; Type: CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."eseguibili"
    ADD CONSTRAINT "eseguibili_pkey" PRIMARY KEY ("id");


--
-- Name: execution_logs execution_logs_pkey; Type: CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."execution_logs"
    ADD CONSTRAINT "execution_logs_pkey" PRIMARY KEY ("id");


--
-- Name: file_source_shares file_source_shares_pkey; Type: CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."file_source_shares"
    ADD CONSTRAINT "file_source_shares_pkey" PRIMARY KEY ("file_source_id", "group_id");


--
-- Name: file_sources file_sources_pkey; Type: CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."file_sources"
    ADD CONSTRAINT "file_sources_pkey" PRIMARY KEY ("id");


--
-- Name: groups groups_pkey; Type: CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."groups"
    ADD CONSTRAINT "groups_pkey" PRIMARY KEY ("id");


--
-- Name: log_runs log_runs_pkey; Type: CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."log_runs"
    ADD CONSTRAINT "log_runs_pkey" PRIMARY KEY ("run_id");


--
-- Name: logs logs_pkey; Type: CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."logs"
    ADD CONSTRAINT "logs_pkey" PRIMARY KEY ("id");


--
-- Name: node_meta node_meta_pkey; Type: CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."node_meta"
    ADD CONSTRAINT "node_meta_pkey" PRIMARY KEY ("run_id", "node_id");


--
-- Name: node_rows node_rows_pkey; Type: CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."node_rows"
    ADD CONSTRAINT "node_rows_pkey" PRIMARY KEY ("id");


--
-- Name: projects projects_pkey; Type: CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."projects"
    ADD CONSTRAINT "projects_pkey" PRIMARY KEY ("id");


--
-- Name: results results_pkey; Type: CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."results"
    ADD CONSTRAINT "results_pkey" PRIMARY KEY ("id");


--
-- Name: runs runs_pkey; Type: CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."runs"
    ADD CONSTRAINT "runs_pkey" PRIMARY KEY ("run_id");


--
-- Name: schedule_catchup_history schedule_catchup_history_pkey; Type: CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."schedule_catchup_history"
    ADD CONSTRAINT "schedule_catchup_history_pkey" PRIMARY KEY ("id");


--
-- Name: schedule_logs schedule_logs_pkey; Type: CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."schedule_logs"
    ADD CONSTRAINT "schedule_logs_pkey" PRIMARY KEY ("log_id");


--
-- Name: scheduler_locks scheduler_locks_pkey; Type: CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."scheduler_locks"
    ADD CONSTRAINT "scheduler_locks_pkey" PRIMARY KEY ("key");


--
-- Name: schedules schedules_pkey; Type: CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."schedules"
    ADD CONSTRAINT "schedules_pkey" PRIMARY KEY ("id");


--
-- Name: schema_migrations schema_migrations_pkey; Type: CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."schema_migrations"
    ADD CONSTRAINT "schema_migrations_pkey" PRIMARY KEY ("filename");


--
-- Name: ticket_attachments ticket_attachments_pkey; Type: CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."ticket_attachments"
    ADD CONSTRAINT "ticket_attachments_pkey" PRIMARY KEY ("id");


--
-- Name: ticket_comments ticket_comments_pkey; Type: CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."ticket_comments"
    ADD CONSTRAINT "ticket_comments_pkey" PRIMARY KEY ("id");


--
-- Name: tickets tickets_pkey; Type: CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."tickets"
    ADD CONSTRAINT "tickets_pkey" PRIMARY KEY ("id");


--
-- Name: user_mfa user_mfa_pkey; Type: CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."user_mfa"
    ADD CONSTRAINT "user_mfa_pkey" PRIMARY KEY ("user_id");


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."users"
    ADD CONSTRAINT "users_pkey" PRIMARY KEY ("id");


--
-- Name: users users_username_key; Type: CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."users"
    ADD CONSTRAINT "users_username_key" UNIQUE ("username");


--
-- Name: verify_runs verify_runs_pkey; Type: CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."verify_runs"
    ADD CONSTRAINT "verify_runs_pkey" PRIMARY KEY ("id");


--
-- Name: idx_api_connections_created_at; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_api_connections_created_at" ON "plenora"."api_connections" USING "btree" ("created_at" DESC);


--
-- Name: idx_api_connections_group; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_api_connections_group" ON "plenora"."api_connections" USING "btree" ("group_id");


--
-- Name: idx_api_connections_group_created_at; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_api_connections_group_created_at" ON "plenora"."api_connections" USING "btree" ("group_id", "created_at" DESC);


--
-- Name: idx_arcgis_connections_group; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_arcgis_connections_group" ON "plenora"."arcgis_connections" USING "btree" ("group_id");


--
-- Name: idx_arcgis_connections_updated_at; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_arcgis_connections_updated_at" ON "plenora"."arcgis_connections" USING "btree" ("updated_at" DESC);


--
-- Name: idx_audit_actions_action; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_audit_actions_action" ON "plenora"."audit_actions" USING "btree" ("action");


--
-- Name: idx_audit_actions_ts; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_audit_actions_ts" ON "plenora"."audit_actions" USING "btree" ("timestamp" DESC);


--
-- Name: idx_audit_actions_user_ts; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_audit_actions_user_ts" ON "plenora"."audit_actions" USING "btree" ("user_id", "timestamp" DESC);


--
-- Name: idx_backup_runs_started_at; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_backup_runs_started_at" ON "plenora"."backup_runs" USING "btree" ("started_at" DESC);


--
-- Name: idx_comparison_keys_node_id; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_comparison_keys_node_id" ON "plenora"."comparison_keys" USING "btree" ("node_id");


--
-- Name: idx_connections_created_at; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_connections_created_at" ON "plenora"."connections" USING "btree" ("created_at" DESC);


--
-- Name: idx_connections_group; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_connections_group" ON "plenora"."connections" USING "btree" ("group_id");


--
-- Name: idx_connections_group_created_at; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_connections_group_created_at" ON "plenora"."connections" USING "btree" ("group_id", "created_at" DESC);


--
-- Name: idx_datasources_group; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_datasources_group" ON "plenora"."datasources" USING "btree" ("group_id");


--
-- Name: idx_datasources_group_name; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_datasources_group_name" ON "plenora"."datasources" USING "btree" ("group_id", "name");


--
-- Name: idx_datasources_name; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_datasources_name" ON "plenora"."datasources" USING "btree" ("name");


--
-- Name: idx_eseguibili_created_by; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_eseguibili_created_by" ON "plenora"."eseguibili" USING "btree" ("created_by");


--
-- Name: idx_eseguibili_group; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_eseguibili_group" ON "plenora"."eseguibili" USING "btree" ("group_id");


--
-- Name: idx_exec_logs_group; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_exec_logs_group" ON "plenora"."execution_logs" USING "btree" ("group_id");


--
-- Name: idx_exec_logs_project; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_exec_logs_project" ON "plenora"."execution_logs" USING "btree" ("project_id");


--
-- Name: idx_execution_logs_group_started; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_execution_logs_group_started" ON "plenora"."execution_logs" USING "btree" ("group_id", "started_at" DESC);


--
-- Name: idx_execution_logs_schedule_id; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_execution_logs_schedule_id" ON "plenora"."execution_logs" USING "btree" ("schedule_id");


--
-- Name: idx_file_sources_created_at; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_file_sources_created_at" ON "plenora"."file_sources" USING "btree" ("created_at" DESC);


--
-- Name: idx_file_sources_group; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_file_sources_group" ON "plenora"."file_sources" USING "btree" ("group_id");


--
-- Name: idx_file_sources_group_created_at; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_file_sources_group_created_at" ON "plenora"."file_sources" USING "btree" ("group_id", "created_at" DESC);


--
-- Name: idx_file_sources_storage_key_notnull; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_file_sources_storage_key_notnull" ON "plenora"."file_sources" USING "btree" ("storage_key") WHERE ("storage_key" IS NOT NULL);


--
-- Name: idx_log_group_id; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_log_group_id" ON "plenora"."logs" USING "btree" ("group_id");


--
-- Name: idx_log_level; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_log_level" ON "plenora"."logs" USING "btree" ("level");


--
-- Name: idx_log_module; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_log_module" ON "plenora"."logs" USING "btree" ("module");


--
-- Name: idx_log_project_id; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_log_project_id" ON "plenora"."logs" USING "btree" ("project_id");


--
-- Name: idx_log_runs_project; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_log_runs_project" ON "plenora"."log_runs" USING "btree" ("project_id");


--
-- Name: idx_log_runs_user_id; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_log_runs_user_id" ON "plenora"."log_runs" USING "btree" ("user_id") WHERE ("user_id" IS NOT NULL);


--
-- Name: idx_log_ts; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_log_ts" ON "plenora"."logs" USING "btree" ("ts");


--
-- Name: idx_node_rows_hash; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_node_rows_hash" ON "plenora"."node_rows" USING "btree" ("run_id", "node_id", "row_hash");


--
-- Name: idx_node_rows_run_node; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_node_rows_run_node" ON "plenora"."node_rows" USING "btree" ("run_id", "node_id");


--
-- Name: idx_projects_group; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_projects_group" ON "plenora"."projects" USING "btree" ("group_id");


--
-- Name: idx_projects_group_updated_at; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_projects_group_updated_at" ON "plenora"."projects" USING "btree" ("group_id", "updated_at" DESC);


--
-- Name: idx_projects_type; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_projects_type" ON "plenora"."projects" USING "btree" ("project_type");


--
-- Name: idx_projects_updated_at; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_projects_updated_at" ON "plenora"."projects" USING "btree" ("updated_at" DESC);


--
-- Name: idx_results_group; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_results_group" ON "plenora"."results" USING "btree" ("group_id");


--
-- Name: idx_results_project; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_results_project" ON "plenora"."results" USING "btree" ("project_id");


--
-- Name: idx_results_storage_key_notnull; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_results_storage_key_notnull" ON "plenora"."results" USING "btree" ("storage_key") WHERE ("storage_key" IS NOT NULL);


--
-- Name: idx_runs_canvas; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_runs_canvas" ON "plenora"."runs" USING "btree" ("canvas_id");


--
-- Name: idx_runs_canvas_timestamp; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_runs_canvas_timestamp" ON "plenora"."runs" USING "btree" ("canvas_id", "timestamp" DESC);


--
-- Name: idx_runs_group; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_runs_group" ON "plenora"."runs" USING "btree" ("group_id");


--
-- Name: idx_runs_user_id; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_runs_user_id" ON "plenora"."runs" USING "btree" ("user_id") WHERE ("user_id" IS NOT NULL);


--
-- Name: idx_schedule_catchup_schedule_emitted; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_schedule_catchup_schedule_emitted" ON "plenora"."schedule_catchup_history" USING "btree" ("schedule_id", "emitted_at" DESC);


--
-- Name: idx_schedule_logs_schedule_ts; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_schedule_logs_schedule_ts" ON "plenora"."schedule_logs" USING "btree" ("schedule_id", "timestamp" DESC);


--
-- Name: idx_schedule_logs_timestamp; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_schedule_logs_timestamp" ON "plenora"."schedule_logs" USING "btree" ("timestamp");


--
-- Name: idx_schedules_group; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_schedules_group" ON "plenora"."schedules" USING "btree" ("group_id");


--
-- Name: idx_schedules_project_id; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_schedules_project_id" ON "plenora"."schedules" USING "btree" ("project_id");


--
-- Name: idx_ticket_attachments_ticket_id; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_ticket_attachments_ticket_id" ON "plenora"."ticket_attachments" USING "btree" ("ticket_id");


--
-- Name: idx_ticket_comments_ticket_id; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_ticket_comments_ticket_id" ON "plenora"."ticket_comments" USING "btree" ("ticket_id", "created_at");


--
-- Name: idx_tickets_active_only; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_tickets_active_only" ON "plenora"."tickets" USING "btree" ("updated_at" DESC) WHERE ("deleted_at" IS NULL);


--
-- Name: idx_tickets_assignee_status; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_tickets_assignee_status" ON "plenora"."tickets" USING "btree" ("assignee", "status") WHERE ("assignee" IS NOT NULL);


--
-- Name: idx_tickets_created_by; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_tickets_created_by" ON "plenora"."tickets" USING "btree" ("created_by");


--
-- Name: idx_tickets_group_status; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_tickets_group_status" ON "plenora"."tickets" USING "btree" ("group_id", "status");


--
-- Name: idx_tickets_kind_status; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_tickets_kind_status" ON "plenora"."tickets" USING "btree" ("kind", "status");


--
-- Name: idx_tickets_updated_at; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_tickets_updated_at" ON "plenora"."tickets" USING "btree" ("updated_at" DESC);


--
-- Name: idx_users_group; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_users_group" ON "plenora"."users" USING "btree" ("group_id");


--
-- Name: idx_verify_runs_started_at; Type: INDEX; Schema: plenora; Owner: -
--

CREATE INDEX "idx_verify_runs_started_at" ON "plenora"."verify_runs" USING "btree" ("started_at" DESC);


--
-- Name: audit_actions trg_audit_actions_block_delete; Type: TRIGGER; Schema: plenora; Owner: -
--

CREATE TRIGGER "trg_audit_actions_block_delete" BEFORE DELETE ON "plenora"."audit_actions" FOR EACH ROW EXECUTE FUNCTION "plenora"."block_log_modifications"();


--
-- Name: audit_actions trg_audit_actions_block_update; Type: TRIGGER; Schema: plenora; Owner: -
--

CREATE TRIGGER "trg_audit_actions_block_update" BEFORE UPDATE ON "plenora"."audit_actions" FOR EACH ROW EXECUTE FUNCTION "plenora"."block_log_modifications"();


--
-- Name: logs trg_logs_block_delete; Type: TRIGGER; Schema: plenora; Owner: -
--

CREATE TRIGGER "trg_logs_block_delete" BEFORE DELETE ON "plenora"."logs" FOR EACH ROW EXECUTE FUNCTION "plenora"."block_log_modifications"();


--
-- Name: logs trg_logs_block_update; Type: TRIGGER; Schema: plenora; Owner: -
--

CREATE TRIGGER "trg_logs_block_update" BEFORE UPDATE ON "plenora"."logs" FOR EACH ROW EXECUTE FUNCTION "plenora"."block_log_modifications"();


--
-- Name: api_connection_shares api_connection_shares_api_connection_id_fkey; Type: FK CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."api_connection_shares"
    ADD CONSTRAINT "api_connection_shares_api_connection_id_fkey" FOREIGN KEY ("api_connection_id") REFERENCES "plenora"."api_connections"("id") ON DELETE CASCADE;


--
-- Name: api_connections api_connections_created_by_fkey; Type: FK CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."api_connections"
    ADD CONSTRAINT "api_connections_created_by_fkey" FOREIGN KEY ("created_by") REFERENCES "plenora"."users"("id");


--
-- Name: api_connections api_connections_group_id_fkey; Type: FK CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."api_connections"
    ADD CONSTRAINT "api_connections_group_id_fkey" FOREIGN KEY ("group_id") REFERENCES "plenora"."groups"("id");


--
-- Name: arcgis_connection_shares arcgis_connection_shares_arcgis_connection_id_fkey; Type: FK CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."arcgis_connection_shares"
    ADD CONSTRAINT "arcgis_connection_shares_arcgis_connection_id_fkey" FOREIGN KEY ("arcgis_connection_id") REFERENCES "plenora"."arcgis_connections"("id") ON DELETE CASCADE;


--
-- Name: arcgis_connections arcgis_connections_created_by_fkey; Type: FK CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."arcgis_connections"
    ADD CONSTRAINT "arcgis_connections_created_by_fkey" FOREIGN KEY ("created_by") REFERENCES "plenora"."users"("id");


--
-- Name: arcgis_connections arcgis_connections_group_id_fkey; Type: FK CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."arcgis_connections"
    ADD CONSTRAINT "arcgis_connections_group_id_fkey" FOREIGN KEY ("group_id") REFERENCES "plenora"."groups"("id");


--
-- Name: comparison_keys comparison_keys_group_id_fkey; Type: FK CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."comparison_keys"
    ADD CONSTRAINT "comparison_keys_group_id_fkey" FOREIGN KEY ("group_id") REFERENCES "plenora"."groups"("id");


--
-- Name: connection_shares connection_shares_connection_id_fkey; Type: FK CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."connection_shares"
    ADD CONSTRAINT "connection_shares_connection_id_fkey" FOREIGN KEY ("connection_id") REFERENCES "plenora"."connections"("id") ON DELETE CASCADE;


--
-- Name: connections connections_created_by_fkey; Type: FK CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."connections"
    ADD CONSTRAINT "connections_created_by_fkey" FOREIGN KEY ("created_by") REFERENCES "plenora"."users"("id");


--
-- Name: connections connections_group_id_fkey; Type: FK CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."connections"
    ADD CONSTRAINT "connections_group_id_fkey" FOREIGN KEY ("group_id") REFERENCES "plenora"."groups"("id");


--
-- Name: datasource_shares datasource_shares_datasource_id_fkey; Type: FK CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."datasource_shares"
    ADD CONSTRAINT "datasource_shares_datasource_id_fkey" FOREIGN KEY ("datasource_id") REFERENCES "plenora"."datasources"("id") ON DELETE CASCADE;


--
-- Name: datasources datasources_created_by_fkey; Type: FK CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."datasources"
    ADD CONSTRAINT "datasources_created_by_fkey" FOREIGN KEY ("created_by") REFERENCES "plenora"."users"("id");


--
-- Name: datasources datasources_group_id_fkey; Type: FK CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."datasources"
    ADD CONSTRAINT "datasources_group_id_fkey" FOREIGN KEY ("group_id") REFERENCES "plenora"."groups"("id");


--
-- Name: eseguibile_shares eseguibile_shares_eseguibile_id_fkey; Type: FK CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."eseguibile_shares"
    ADD CONSTRAINT "eseguibile_shares_eseguibile_id_fkey" FOREIGN KEY ("eseguibile_id") REFERENCES "plenora"."eseguibili"("id") ON DELETE CASCADE;


--
-- Name: eseguibili eseguibili_created_by_fkey; Type: FK CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."eseguibili"
    ADD CONSTRAINT "eseguibili_created_by_fkey" FOREIGN KEY ("created_by") REFERENCES "plenora"."users"("id");


--
-- Name: eseguibili eseguibili_group_id_fkey; Type: FK CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."eseguibili"
    ADD CONSTRAINT "eseguibili_group_id_fkey" FOREIGN KEY ("group_id") REFERENCES "plenora"."groups"("id");


--
-- Name: file_source_shares file_source_shares_file_source_id_fkey; Type: FK CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."file_source_shares"
    ADD CONSTRAINT "file_source_shares_file_source_id_fkey" FOREIGN KEY ("file_source_id") REFERENCES "plenora"."file_sources"("id") ON DELETE CASCADE;


--
-- Name: file_sources file_sources_created_by_fkey; Type: FK CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."file_sources"
    ADD CONSTRAINT "file_sources_created_by_fkey" FOREIGN KEY ("created_by") REFERENCES "plenora"."users"("id");


--
-- Name: file_sources file_sources_group_id_fkey; Type: FK CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."file_sources"
    ADD CONSTRAINT "file_sources_group_id_fkey" FOREIGN KEY ("group_id") REFERENCES "plenora"."groups"("id");


--
-- Name: node_meta node_meta_run_id_fkey; Type: FK CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."node_meta"
    ADD CONSTRAINT "node_meta_run_id_fkey" FOREIGN KEY ("run_id") REFERENCES "plenora"."runs"("run_id") ON DELETE CASCADE;


--
-- Name: node_rows node_rows_run_id_fkey; Type: FK CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."node_rows"
    ADD CONSTRAINT "node_rows_run_id_fkey" FOREIGN KEY ("run_id") REFERENCES "plenora"."runs"("run_id") ON DELETE CASCADE;


--
-- Name: projects projects_created_by_fkey; Type: FK CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."projects"
    ADD CONSTRAINT "projects_created_by_fkey" FOREIGN KEY ("created_by") REFERENCES "plenora"."users"("id");


--
-- Name: projects projects_group_id_fkey; Type: FK CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."projects"
    ADD CONSTRAINT "projects_group_id_fkey" FOREIGN KEY ("group_id") REFERENCES "plenora"."groups"("id");


--
-- Name: schedules schedules_created_by_fkey; Type: FK CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."schedules"
    ADD CONSTRAINT "schedules_created_by_fkey" FOREIGN KEY ("created_by") REFERENCES "plenora"."users"("id");


--
-- Name: schedules schedules_group_id_fkey; Type: FK CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."schedules"
    ADD CONSTRAINT "schedules_group_id_fkey" FOREIGN KEY ("group_id") REFERENCES "plenora"."groups"("id");


--
-- Name: schedules schedules_project_id_fkey; Type: FK CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."schedules"
    ADD CONSTRAINT "schedules_project_id_fkey" FOREIGN KEY ("project_id") REFERENCES "plenora"."projects"("id") ON DELETE SET NULL;


--
-- Name: ticket_attachments ticket_attachments_file_source_id_fkey; Type: FK CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."ticket_attachments"
    ADD CONSTRAINT "ticket_attachments_file_source_id_fkey" FOREIGN KEY ("file_source_id") REFERENCES "plenora"."file_sources"("id") ON DELETE CASCADE;


--
-- Name: ticket_attachments ticket_attachments_ticket_id_fkey; Type: FK CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."ticket_attachments"
    ADD CONSTRAINT "ticket_attachments_ticket_id_fkey" FOREIGN KEY ("ticket_id") REFERENCES "plenora"."tickets"("id") ON DELETE CASCADE;


--
-- Name: ticket_attachments ticket_attachments_uploaded_by_fkey; Type: FK CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."ticket_attachments"
    ADD CONSTRAINT "ticket_attachments_uploaded_by_fkey" FOREIGN KEY ("uploaded_by") REFERENCES "plenora"."users"("id");


--
-- Name: ticket_comments ticket_comments_author_id_fkey; Type: FK CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."ticket_comments"
    ADD CONSTRAINT "ticket_comments_author_id_fkey" FOREIGN KEY ("author_id") REFERENCES "plenora"."users"("id");


--
-- Name: ticket_comments ticket_comments_ticket_id_fkey; Type: FK CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."ticket_comments"
    ADD CONSTRAINT "ticket_comments_ticket_id_fkey" FOREIGN KEY ("ticket_id") REFERENCES "plenora"."tickets"("id") ON DELETE CASCADE;


--
-- Name: tickets tickets_assignee_fkey; Type: FK CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."tickets"
    ADD CONSTRAINT "tickets_assignee_fkey" FOREIGN KEY ("assignee") REFERENCES "plenora"."users"("id");


--
-- Name: tickets tickets_created_by_fkey; Type: FK CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."tickets"
    ADD CONSTRAINT "tickets_created_by_fkey" FOREIGN KEY ("created_by") REFERENCES "plenora"."users"("id");


--
-- Name: tickets tickets_group_id_fkey; Type: FK CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."tickets"
    ADD CONSTRAINT "tickets_group_id_fkey" FOREIGN KEY ("group_id") REFERENCES "plenora"."groups"("id");


--
-- Name: user_mfa user_mfa_user_id_fkey; Type: FK CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."user_mfa"
    ADD CONSTRAINT "user_mfa_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "plenora"."users"("id") ON DELETE CASCADE;


--
-- Name: users users_group_id_fkey; Type: FK CONSTRAINT; Schema: plenora; Owner: -
--

ALTER TABLE ONLY "plenora"."users"
    ADD CONSTRAINT "users_group_id_fkey" FOREIGN KEY ("group_id") REFERENCES "plenora"."groups"("id");


--
-- PostgreSQL database dump complete
--

\unrestrict knvtzObn6DeOOTEiaJHDSSHKVeYQOApouqqCOBNI1ZAGsg2QLXEZxUxJX4tSVSW

