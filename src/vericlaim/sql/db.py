"""Pooled, time-bounded Postgres connections.

Every query the agent runs is borrowed from here, which makes this the place three
guarantees are made concrete:

* **A query cannot run forever.** ``statement_timeout`` is set on each session from
  :class:`~vericlaim.config.Settings`. ``init.sql`` sets a role-level default too; this
  is the configurable one, and it also covers the admin role, which has no such default.
* **A read-only session cannot write**, independently of what the role is granted. Two
  mechanisms, either one sufficient: privileges (proved by ``scripts/smoke.py``) and the
  session flag set here. The generated-SQL path should have to get past both.
* **A database that is down says so.** Failure to establish the pool raises
  :class:`DatabaseUnavailableError` naming the command that fixes it, rather than a bare
  pool timeout minutes into a run.

Connections are pooled rather than opened per query: a single question fans out over
several sources concurrently, and the reference implementation this replaces opened a
fresh connection -- and re-read its configuration from disk -- on every call.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
import psycopg_pool

from vericlaim.config import Settings, get_settings

DEFAULT_APPLICATION_NAME = "vericlaim"


class DatabaseUnavailableError(RuntimeError):
    """Raised when the pool cannot be established at all.

    Distinct from :class:`psycopg_pool.PoolTimeout`, which a working pool raises when
    every connection is busy. One means "Postgres is not there", the other means "we are
    saturated", and they call for different responses.
    """


class Database:
    """A lazily-opened connection pool for one role, with its session settings.

    Instances are injected rather than reached for globally, so a test can point one at
    a different database or a shorter timeout without touching process state. The one
    process-wide pair lives behind :func:`default_database`.
    """

    def __init__(
        self,
        dsn: str,
        *,
        statement_timeout_ms: int,
        read_only_session: bool = False,
        min_size: int = 1,
        max_size: int = 4,
        acquire_timeout_s: float = 10.0,
        connect_timeout_s: float = 5.0,
        application_name: str = DEFAULT_APPLICATION_NAME,
    ) -> None:
        self._dsn = dsn
        self.statement_timeout_ms = int(statement_timeout_ms)
        self.read_only_session = bool(read_only_session)
        self._min_size = min_size
        self._max_size = max_size
        self._acquire_timeout_s = acquire_timeout_s
        self._connect_timeout_s = connect_timeout_s
        self._application_name = application_name
        self._pool: psycopg_pool.ConnectionPool | None = None
        self._lock = threading.Lock()

    # -- session -----------------------------------------------------------

    def session_options(self) -> tuple[str, ...]:
        """Return the statements applied to every connection this pool opens.

        Returned rather than executed inline so the policy can be asserted without a
        database, and so a change to it is visible in a diff as a change to a list.
        """
        options = [
            f"SET statement_timeout = {self.statement_timeout_ms}",
            f"SET application_name = '{_quote(self._application_name)}'",
        ]
        if self.read_only_session:
            options.append("SET default_transaction_read_only = on")
        return tuple(options)

    def _configure(self, conn: psycopg.Connection) -> None:
        """Apply the session settings to a newly opened connection.

        Committed deliberately: ``SET`` is transactional, and the pool rolls back each
        connection when it is returned, which would otherwise undo all of this after the
        first use.
        """
        for option in self.session_options():
            conn.execute(option)
        conn.commit()

    # -- lifecycle ---------------------------------------------------------

    @property
    def is_open(self) -> bool:
        return self._pool is not None and not self._pool.closed

    def _ensure_pool(self) -> psycopg_pool.ConnectionPool:
        with self._lock:
            if self._pool is not None and not self._pool.closed:
                return self._pool

            pool = psycopg_pool.ConnectionPool(
                self._dsn,
                min_size=self._min_size,
                max_size=self._max_size,
                timeout=self._acquire_timeout_s,
                configure=self._configure,
                # Hand out only connections that answer. Without this, a restarted
                # Postgres leaves the pool holding dead sockets and the next several
                # queries fail for a reason that has nothing to do with them.
                check=psycopg_pool.ConnectionPool.check_connection,
                kwargs={"connect_timeout": max(1, round(self._connect_timeout_s))},
                open=False,
                name=f"vericlaim-{'ro' if self.read_only_session else 'rw'}",
            )
            try:
                # wait=True so an absent database fails here, once, with a clear
                # message -- rather than lazily, later, inside whichever query
                # happened to be first.
                pool.open(wait=True, timeout=self._connect_timeout_s)
            except psycopg_pool.PoolTimeout as exc:
                pool.close()
                raise DatabaseUnavailableError(
                    f"Could not connect to Postgres within {self._connect_timeout_s:g}s "
                    f"({_endpoint(self._dsn)}). Start it with `docker compose up -d`, "
                    "then try again."
                ) from exc
            self._pool = pool
            return pool

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection]:
        """Borrow a configured connection, returning it to the pool on exit.

        The transaction is committed on a clean exit and rolled back on an exception, so
        a failed query never leaves an aborted transaction for the next borrower.
        """
        pool = self._ensure_pool()
        with pool.connection() as conn:
            yield conn

    def close(self) -> None:
        """Close the pool. Idempotent, so shutdown paths need not track state."""
        with self._lock:
            if self._pool is not None:
                self._pool.close()
                self._pool = None


def _quote(value: str) -> str:
    """Escape a value for a single-quoted SQL literal."""
    return value.replace("'", "''")


def _endpoint(dsn: str) -> str:
    """Return ``host:port/dbname`` from a libpq connection string, for error messages.

    Parsed rather than interpolated from settings so the message describes the pool that
    actually failed, and so the password never appears in it.
    """
    fields = dict(
        part.split("=", 1) for part in dsn.split() if "=" in part
    )
    return f"{fields.get('host', '?')}:{fields.get('port', '?')}/{fields.get('dbname', '?')}"


# The one process-wide pool per role. Module state here is the point of pooling rather
# than an accident of it, but it is reachable only through these two functions, and
# close_databases() makes it resettable -- which is exactly what the caches this project
# replaces were missing.
_DATABASES: dict[bool, Database] = {}
_DATABASES_LOCK = threading.Lock()


def default_database(
    *, readonly: bool = True, settings: Settings | None = None
) -> Database:
    """Return the process-wide pool for a role, building it on first use.

    Read-only by default: the agent's path to the database is the read-only one, and a
    caller that needs to write has to say so.
    """
    resolved = settings or get_settings()
    with _DATABASES_LOCK:
        database = _DATABASES.get(readonly)
        if database is None:
            database = Database(
                resolved.dsn(readonly=readonly),
                statement_timeout_ms=resolved.sql_statement_timeout_ms,
                read_only_session=readonly,
                min_size=resolved.pg_pool_min_size,
                max_size=resolved.pg_pool_max_size,
                acquire_timeout_s=resolved.pg_pool_acquire_timeout_s,
                connect_timeout_s=resolved.pg_connect_timeout_s,
            )
            _DATABASES[readonly] = database
        return database


def close_databases() -> None:
    """Close every process-wide pool. Safe to call when none were built."""
    with _DATABASES_LOCK:
        for database in _DATABASES.values():
            database.close()
        _DATABASES.clear()
