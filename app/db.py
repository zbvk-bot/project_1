from __future__ import annotations

import contextlib
from datetime import datetime
from typing import Any, Iterable

import psycopg2
import psycopg2.extras
from psycopg2 import OperationalError, ProgrammingError
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from psycopg2 import sql

from .config import AppConfig
from .errors import DatabaseError

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(64) UNIQUE NOT NULL,
    password_hash VARCHAR(128) NOT NULL,
    salt VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS access_logs (
    id BIGSERIAL PRIMARY KEY,
    remote_ip VARCHAR(45) NOT NULL,
    ident VARCHAR(255) NOT NULL DEFAULT '-',
    auth_user VARCHAR(255) NOT NULL DEFAULT '-',
    request_time TIMESTAMPTZ NOT NULL,
    request_line TEXT NOT NULL,
    method VARCHAR(16),
    url_path TEXT,
    query_string TEXT,
    protocol VARCHAR(32),
    status_code INTEGER,
    response_bytes BIGINT,
    referer TEXT,
    user_agent TEXT,
    source_file VARCHAR(1024),
    line_hash VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (line_hash)
);

CREATE INDEX IF NOT EXISTS idx_access_logs_remote_ip ON access_logs (remote_ip);
CREATE INDEX IF NOT EXISTS idx_access_logs_request_time ON access_logs (request_time);
CREATE INDEX IF NOT EXISTS idx_access_logs_url_path ON access_logs (url_path);
"""


def ensure_database(cfg: AppConfig) -> None:
    try:
        conn = psycopg2.connect(cfg.maintenance_dsn)
    except OperationalError as exc:
        raise DatabaseError(
            f"Не удалось подключиться к PostgreSQL "
            f"({cfg.db_host}:{cfg.db_port}/{cfg.db_maintenance_name}): {exc}"
        ) from exc

    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (cfg.db_name,))
            if cur.fetchone():
                return
            cur.execute(
                sql.SQL("CREATE DATABASE {} ENCODING 'UTF8'").format(sql.Identifier(cfg.db_name))
            )
    except ProgrammingError as exc:
        raise DatabaseError(
            f"Не удалось создать базу «{cfg.db_name}». "
            f"Проверьте права пользователя «{cfg.db_user}»: {exc}"
        ) from exc
    finally:
        conn.close()


def init_schema(cfg: AppConfig) -> None:
    with connect(cfg) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)


def bootstrap(cfg: AppConfig) -> None:
    ensure_database(cfg)
    init_schema(cfg)


@contextlib.contextmanager
def connect(cfg: AppConfig):
    try:
        conn = psycopg2.connect(cfg.dsn)
    except OperationalError as exc:
        raise DatabaseError(
            f"Не удалось подключиться к PostgreSQL ({cfg.db_host}:{cfg.db_port}/{cfg.db_name}). "
            f"Запустите службу PostgreSQL и проверьте config.ini: {exc}"
        ) from exc
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def fetch_one(conn, query: str, params: tuple = ()) -> dict | None:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, params)
        row = cur.fetchone()
        return dict(row) if row else None


def fetch_all(conn, query: str, params: tuple = ()) -> list[dict]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, params)
        return [dict(r) for r in cur.fetchall()]


def execute(conn, query: str, params: tuple = ()) -> int:
    with conn.cursor() as cur:
        cur.execute(query, params)
        return cur.rowcount


def insert_access_logs(conn, rows: Iterable[dict]) -> int:
    sql_text = """
        INSERT INTO access_logs (
            remote_ip, ident, auth_user, request_time, request_line,
            method, url_path, query_string, protocol, status_code,
            response_bytes, referer, user_agent, source_file, line_hash
        ) VALUES (
            %(remote_ip)s, %(ident)s, %(auth_user)s, %(request_time)s,
            %(request_line)s, %(method)s, %(url_path)s, %(query_string)s,
            %(protocol)s, %(status_code)s, %(response_bytes)s,
            %(referer)s, %(user_agent)s, %(source_file)s, %(line_hash)s
        ) ON CONFLICT (line_hash) DO NOTHING
    """
    inserted = 0
    with conn.cursor() as cur:
        for row in rows:
            cur.execute(sql_text, row)
            inserted += cur.rowcount
    return inserted


def query_logs(
    conn,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    remote_ip: str | None = None,
    keyword: str | None = None,
    url_path: str | None = None,
    group_by: str | None = None,
    limit: int = 500,
) -> list[dict]:
    conditions = ["1=1"]
    params: list[Any] = []

    if date_from:
        conditions.append("request_time >= %s")
        params.append(date_from)
    if date_to:
        conditions.append("request_time <= %s")
        params.append(date_to)
    if remote_ip:
        conditions.append("remote_ip = %s")
        params.append(remote_ip)
    if keyword:
        conditions.append(
            "(url_path ILIKE %s OR request_line ILIKE %s OR referer ILIKE %s OR user_agent ILIKE %s)"
        )
        like = f"%{keyword}%"
        params.extend([like, like, like, like])
    if url_path:
        conditions.append("url_path = %s")
        params.append(url_path)

    where = " AND ".join(conditions)
    params.append(limit)

    if group_by == "ip":
        sql_text = f"""
            SELECT remote_ip, COUNT(*)::int AS hits,
                   MIN(request_time) AS first_seen,
                   MAX(request_time) AS last_seen
            FROM access_logs WHERE {where}
            GROUP BY remote_ip ORDER BY hits DESC LIMIT %s
        """
    elif group_by == "date":
        sql_text = f"""
            SELECT DATE(request_time) AS log_date, COUNT(*)::int AS hits
            FROM access_logs WHERE {where}
            GROUP BY DATE(request_time) ORDER BY log_date DESC LIMIT %s
        """
    else:
        sql_text = f"""
            SELECT request_time, remote_ip, method, url_path, status_code, response_bytes
            FROM access_logs WHERE {where}
            ORDER BY request_time DESC LIMIT %s
        """

    return fetch_all(conn, sql_text, tuple(params))


def list_distinct_urls(
    conn,
    keyword: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = 200,
) -> list[dict]:
    conditions = ["url_path IS NOT NULL"]
    params: list[Any] = []
    if date_from:
        conditions.append("request_time >= %s")
        params.append(date_from)
    if date_to:
        conditions.append("request_time <= %s")
        params.append(date_to)
    if keyword:
        conditions.append("url_path ILIKE %s")
        params.append(f"%{keyword}%")
    where = " AND ".join(conditions)
    params.append(limit)
    sql_text = f"""
        SELECT url_path, COUNT(*)::int AS hits
        FROM access_logs WHERE {where}
        GROUP BY url_path ORDER BY hits DESC LIMIT %s
    """
    return fetch_all(conn, sql_text, tuple(params))
