# Vercel and Supabase hosting

The hosted entrypoint is `app.py`. It runs the existing FastAPI application on
Vercel and stores the workspace in Supabase PostgreSQL. This is a private,
single-operator deployment. It does not introduce tenant isolation or shared
public access. Protect **all deployments, including production**, with Vercel
Authentication before adding data or publishing the application.

## Setup

1. Apply `deploy/supabase.sql` to the selected Supabase database.
2. Configure these Vercel server environment variables. Never commit their values:
   - `TPLLM_DATABASE_URL`: an SSL PostgreSQL direct connection or Supabase
     **session pooler on port 5432**. Transaction pooling on port 6543 is rejected
     because the adapter needs one database session throughout an invocation.
   - `TPLLM_WORKSPACE_ID`: a stable identifier, for example `tpllm-production`.
     Preview deployments automatically add their commit identity to this name,
     keeping preview edits separate from production data.
   - `CRON_SECRET`: a generated secret used only by the scheduled collector.
3. Link Vercel to `SphoenixAI/TPLLM`, use the FastAPI framework and the checked-in
   install command, and enable protection for production and preview deployments.
4. Deploy an exact tested commit. Verify `/health`, then open the dashboard and
   use the isolated demo. Test an edit across separate invocations before claiming
   durable persistence.

The database role must have schema usage, SELECT, INSERT and UPDATE on the
workspace table, and be the table owner or have BYPASSRLS. The schema is outside
the public Data API; public, anonymous and authenticated API roles have no access.
Use a dedicated login and a server-side secret. Do not use a browser publishable
key as a database credential.

## Durability and execution

Each request opens a dedicated PostgreSQL connection and takes a session advisory
lock for its workspace. It restores the latest compressed snapshot into a fresh
temporary directory. The existing SQLite journal and JSON artifact workflows run
there. SQLite's backup API captures committed WAL records; WAL/SHM sidecars are
not archived. A final snapshot commits before a successful response is sent.

Browser-record writes also checkpoint synchronously. In particular, an attempted
external action is durably saved **before** contacting its provider. A failed
checkpoint prevents the send. If execution stops after a provider accepts a
message but before its final receipt is saved, the persisted attempt still blocks
blind retry. These boundaries retain the existing reconciliation requirements.

Concurrent invocations wait up to 15 seconds for the workspace lock, then receive
a retryable 503 if it remains occupied.
Closing or losing the database session releases the lock. An invocation never
reconnects and writes a stale snapshot after losing its original session.
Missing storage or failed persistence returns 503; there is no ephemeral fallback.

The adapter preserves the current storage format in a private PostgreSQL `bytea`
record. It does not normalize the domain into PostgreSQL tables. Snapshots are
limited to 64 MiB uncompressed and 10,000 files. This approach is appropriate for
the initial single-operator deployment; larger workspaces and concurrent teams
need a dedicated storage implementation. Demo archives count toward this limit.

## Collection and access

The hosted process does not start the local background thread. Vercel Cron calls
`/api/cron/collect` every five minutes with `CRON_SECRET`; the handler restores the
same live workspace, runs the existing collection/coordination cycle, and persists
the result. The selected Vercel plan must support this schedule. Cron and a green
deployment do not configure provider accounts or prove successful collection.

Provider credentials, source mappings, enabled collection and standing authority
remain distinct. No provider credentials or model choice are copied from local
development automatically. Existing JWT roles continue to guard live writes;
Vercel Authentication supplies the outer boundary for the legacy read routes and
the isolated demo. Do not disable production protection without implementing
application-level authentication and reviewing the open-read surface.

Local startup is unchanged. The `hosted` Python extra adds PostgreSQL support;
ordinary CLI/local installations continue to use only SQLite.

To create a live dashboard administrator, use the local CLI with the same database
and workspace environment variables, after installing `.[dashboard,hosted]`:

```bash
python -m tpllm dashboard-key --hosted --owner your-name --role admin
```

It durably saves the key in the hosted workspace and prints the raw key once in
your terminal. Enter it in the protected dashboard's Authenticate panel. This
bootstrap operation has no HTTP endpoint. Do not share the key in chat or Git.

References: [Vercel FastAPI](https://vercel.com/docs/frameworks/backend/fastapi),
[Supabase connection modes](https://supabase.com/docs/guides/database/connecting-to-postgres),
[Vercel Authentication](https://vercel.com/docs/deployment-protection/methods-to-protect-deployments/vercel-authentication).
