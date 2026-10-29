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
-- The server connects using a dedicated login with this schema/table access
-- and BYPASSRLS (or the table owner). Keep its password in Vercel secrets.
