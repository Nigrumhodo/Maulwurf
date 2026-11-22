-- Runtime LOGIN roles for the production stack (F6-17).
--
-- The migration owner (POSTGRES_USER) stays on the `migrate` service only.
-- api, ingest, and the worker/scheduler authenticate as these LOGIN roles and
-- still SET LOCAL ROLE to the NOLOGIN groups created by alembic 0005
-- (maulwurf_app, maulwurf_ingest, maulwurf_jobs). Membership is the second
-- barrier: a process that skips SET ROLE is not the table owner and does not
-- have BYPASSRLS, so FORCE ROW LEVEL SECURITY still applies.
--
-- Idempotent. Re-running rotates the passwords and resets attributes that must
-- stay off (SUPERUSER, BYPASSRLS, CREATEROLE, CREATEDB).
--
-- Apply after `alembic upgrade head`, from the operator shell. Passwords are read
-- from the environment inside psql via `\getenv` (PostgreSQL 15+; this project
-- pins PG16) so they never appear in `ps` output or shell history:
--
--   set -a && source .env.prod && set +a
--   psql "postgresql://${POSTGRES_USER}@127.0.0.1/${POSTGRES_DB}" \
--     -v ON_ERROR_STOP=1 \
--     -f infra/deploy/init-runtime-roles.sql
--
-- On self-hosted PostgreSQL with `log_statement = 'all'` or `'ddl'`, run with
-- `PGOPTIONS="-c log_statement=none"` so CREATE/ALTER ROLE PASSWORD statements
-- do not reach server logs.
--
-- The password in each DATABASE_URL_* value must match the corresponding
-- DATABASE_PASSWORD_* variable. Use a single-line hex password
-- (`openssl rand -hex 24`). Do not put $$ in it.

\set ON_ERROR_STOP on

\getenv app_password DATABASE_PASSWORD_APP
\getenv ingest_password DATABASE_PASSWORD_INGEST
\getenv jobs_password DATABASE_PASSWORD_JOBS

\if :{?app_password}
\else
DO $mw$ BEGIN RAISE EXCEPTION 'init-runtime-roles.sql: DATABASE_PASSWORD_APP must be set in the environment'; END $mw$;
\endif

\if :{?ingest_password}
\else
DO $mw$ BEGIN RAISE EXCEPTION 'init-runtime-roles.sql: DATABASE_PASSWORD_INGEST must be set in the environment'; END $mw$;
\endif

\if :{?jobs_password}
\else
DO $mw$ BEGIN RAISE EXCEPTION 'init-runtime-roles.sql: DATABASE_PASSWORD_JOBS must be set in the environment'; END $mw$;
\endif

-- psql substitutes :'var' only outside dollar quotes, so the length check stays here.
-- RAISE EXCEPTION aborts with non-zero exit code 3 under ON_ERROR_STOP (psql \quit always exits 0).
SELECT (length(:'app_password') < 16) AS app_short \gset
SELECT (length(:'ingest_password') < 16) AS ingest_short \gset
SELECT (length(:'jobs_password') < 16) AS jobs_short \gset
\if :app_short
DO $mw$ BEGIN RAISE EXCEPTION 'init-runtime-roles.sql: DATABASE_PASSWORD_APP must be at least 16 characters'; END $mw$;
\endif
\if :ingest_short
DO $mw$ BEGIN RAISE EXCEPTION 'init-runtime-roles.sql: DATABASE_PASSWORD_INGEST must be at least 16 characters'; END $mw$;
\endif
\if :jobs_short
DO $mw$ BEGIN RAISE EXCEPTION 'init-runtime-roles.sql: DATABASE_PASSWORD_JOBS must be at least 16 characters'; END $mw$;
\endif

DO $mw$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'maulwurf_app')
     OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'maulwurf_ingest')
     OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'maulwurf_jobs') THEN
    RAISE EXCEPTION
      'runtime group roles are missing; run alembic upgrade head before this script';
  END IF;
END
$mw$;

SELECT format(
  'CREATE ROLE maulwurf_app_login LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT PASSWORD %L',
  :'app_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'maulwurf_app_login')
\gexec

SELECT format(
  'CREATE ROLE maulwurf_ingest_login LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT PASSWORD %L',
  :'ingest_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'maulwurf_ingest_login')
\gexec

SELECT format(
  'CREATE ROLE maulwurf_jobs_login LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT PASSWORD %L',
  :'jobs_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'maulwurf_jobs_login')
\gexec

ALTER ROLE maulwurf_app_login WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT PASSWORD :'app_password';
ALTER ROLE maulwurf_ingest_login WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT PASSWORD :'ingest_password';
ALTER ROLE maulwurf_jobs_login WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT PASSWORD :'jobs_password';

GRANT maulwurf_app TO maulwurf_app_login;
GRANT maulwurf_ingest TO maulwurf_ingest_login;
GRANT maulwurf_jobs TO maulwurf_jobs_login;

DO $mw$
BEGIN
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO maulwurf_app_login', current_database());
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO maulwurf_ingest_login', current_database());
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO maulwurf_jobs_login', current_database());
END
$mw$;

GRANT USAGE ON SCHEMA public TO maulwurf_app_login;
GRANT USAGE ON SCHEMA public TO maulwurf_ingest_login;
GRANT USAGE ON SCHEMA public TO maulwurf_jobs_login;
