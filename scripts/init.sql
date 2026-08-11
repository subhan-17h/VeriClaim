-- Provision the read-only role the agent queries as. Runs once, at first boot, as the
-- database owner (see docker-compose.yml).
--
-- The engineering invariant this implements: SQL safety is deterministic and never
-- model-mediated. sqlglot AST validation rejects unsafe SQL before it is sent; this role
-- is what happens if that layer is ever wrong. Privileges are therefore the mechanism --
-- not `default_transaction_read_only`, which would refuse writes with a transaction-mode
-- error and thereby mask a genuinely mis-granted role from scripts/smoke.py.

\getenv readonly_password READONLY_PASSWORD

CREATE ROLE vericlaim_readonly LOGIN PASSWORD :'readonly_password';

-- Both schemas exist before anything ingests into them, because the default privileges
-- below are declared per schema and a schema created later would not inherit them.
--   ops    -- the transactional claims corpus (C-8.1)
--   sheets -- spreadsheets normalised with A1 lineage columns (C-6.2)
CREATE SCHEMA IF NOT EXISTS ops AUTHORIZATION vericlaim;
CREATE SCHEMA IF NOT EXISTS sheets AUTHORIZATION vericlaim;

GRANT CONNECT ON DATABASE vericlaim TO vericlaim_readonly;
GRANT USAGE ON SCHEMA ops, sheets TO vericlaim_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA ops, sheets TO vericlaim_readonly;

-- The clause that actually matters. The statement above is a no-op here -- at first boot
-- neither schema holds a table -- and every table the agent reads is created afterwards
-- by the corpus generator. Without this, the role would authenticate successfully and
-- then find nothing it is allowed to read.
--
-- Scoped to tables created by `vericlaim`, which is the only role that creates any.
ALTER DEFAULT PRIVILEGES FOR ROLE vericlaim IN SCHEMA ops
    GRANT SELECT ON TABLES TO vericlaim_readonly;
ALTER DEFAULT PRIVILEGES FOR ROLE vericlaim IN SCHEMA sheets
    GRANT SELECT ON TABLES TO vericlaim_readonly;

-- Postgres 15+ revokes this by default; restated so the guarantee is a property of this
-- file rather than of the image tag.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

-- A backstop for the per-session timeout sql/db.py sets from Settings. A runaway scan
-- started by any path that forgot to set one still dies on its own.
ALTER ROLE vericlaim_readonly SET statement_timeout = '10s';
