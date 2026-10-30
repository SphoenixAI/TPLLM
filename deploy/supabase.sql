-- Run once using a database administrator. No public API access is granted.
CREATE SCHEMA IF NOT EXISTS tpllm_hosting;
REVOKE ALL ON SCHEMA tpllm_hosting FROM PUBLIC, anon, authenticated;
CREATE TABLE IF NOT EXISTS tpllm_hosting.workspaces (
    id text PRIMARY KEY,
    payload bytea NOT NULL,
    revision bigint NOT NULL DEFAULT 1,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (octet_length(payload) <= 67108864)
);
ALTER TABLE tpllm_hosting.workspaces ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON tpllm_hosting.workspaces FROM PUBLIC, anon, authenticated;

-- A dedicated login gets only the data operations the adapter requires.
-- Set LOGIN and its generated password separately; never put secrets in a migration.
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tpllm_runtime') THEN
        CREATE ROLE tpllm_runtime NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
    END IF;
END $$;
GRANT USAGE ON SCHEMA tpllm_hosting TO tpllm_runtime;
GRANT SELECT, INSERT, UPDATE ON tpllm_hosting.workspaces TO tpllm_runtime;
-- Add the policy on first setup; preserve any existing policy unchanged.
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'tpllm_hosting' AND tablename = 'workspaces'
          AND policyname = 'tpllm_runtime_access'
    ) THEN
        CREATE POLICY tpllm_runtime_access ON tpllm_hosting.workspaces
            FOR ALL TO tpllm_runtime USING (true) WITH CHECK (true);
    END IF;
END $$;
